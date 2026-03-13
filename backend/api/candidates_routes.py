"""Route module: candidates. Auto-extracted from main_legacy.py."""
import os
import json
import asyncio
import logging
import time
from core.lifespan import backup_db_to_gcs
from core.db_wrapper import IS_POSTGRES
import re
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends, UploadFile, File, Body, Query
from fastapi.responses import Response, JSONResponse, StreamingResponse, RedirectResponse

from core.config import get_settings
from core.dependencies import require_auth, optional_auth, require_admin
from models.schemas import LinkedInProfileImport

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(tags=["candidates"])


# ---- Service accessors (lazy imports to avoid circular deps) ----

def _db():
    from api.deps import get_db
    return get_db()

def _ai():
    from api.deps import get_ai
    return get_ai()

def _gemini():
    from api.deps import get_gemini
    return get_gemini()

def _scraper():
    from api.deps import get_scraper
    return get_scraper()

def _resume_parser():
    from api.deps import get_resume_parser
    return get_resume_parser()

def _email_parser():
    from api.deps import get_email_parser
    return get_email_parser()

def _auth_svc():
    from api.deps import get_auth
    return get_auth()

def _matching_engine():
    from api.deps import get_matching_engine
    return get_matching_engine()

def _cache():
    from api.deps import response_cache
    return response_cache

def _get_cache_lock():
    from api.deps import _cache_lock
    return _cache_lock

def _deps():
    """Get deps module for constants."""
    import api.deps as deps
    return deps


@router.delete("/api/candidates/{candidate_id}")
async def delete_candidate(candidate_id: str, current_user: dict = Depends(require_auth)):
    """Delete a single candidate by ID."""
    try:
        def _delete_candidate_db():
            with _db().get_connection() as conn:
                cursor = conn.execute("SELECT name, email FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                name, email = row[0], row[1]
                conn.execute("UPDATE candidates SET is_active = 0, last_updated = ? WHERE id = ?",
                             (datetime.now().isoformat(), candidate_id))
                conn.execute("DELETE FROM resumes WHERE candidate_id = ?", (candidate_id,))
                conn.commit()
                return (name, email)
        result = await asyncio.to_thread(_delete_candidate_db)
        if result is None:
            raise HTTPException(404, f"Candidate {candidate_id} not found")
        name, email = result
        _cache().clear()
        logger.info(f"🗑️ Deleted candidate: {name} ({email})")
        return {"status": "success", "message": f"Candidate {name} deleted", "candidate_id": candidate_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete candidate error: {e}")
        raise HTTPException(500, "Error deleting candidate. Check server logs for details.")




@router.post("/api/candidates/purge-indeed")
async def purge_indeed_candidates(current_user: dict = Depends(require_admin)):
    """
    Delete all candidates with Indeed relay emails (@indeedemail.com, conversation-* IDs).
    These are system-generated relay addresses, not real candidate emails.
    """
    try:
        _cache().clear()
        result = await asyncio.to_thread(_db().purge_indeed_candidates)
        logger.info(f"🗑️ Purged Indeed candidates: {result}")
        return {
            "status": "success",
            "message": f"Purged {result['total_deleted']} Indeed relay candidates",
            **result
        }
    except Exception as e:
        logger.error(f"Purge failed: {e}")
        raise HTTPException(500, "Purge failed. Check server logs for details.")




@router.post("/api/candidates/reset-and-reparse")
async def reset_and_reparse_all_emails(current_user: dict = Depends(require_admin)):
    """
    Clear all candidates and re-parse ALL emails from inbox.
    Parses email body, attached resumes, and uses Local AI for analysis.
    """
    try:
        # Clear response cache
        _cache().clear()
        
        # Step 1: Clear all candidates from database
        deleted_count = await asyncio.to_thread(_db().clear_all_candidates)
        logger.info(f"🗑️ Cleared {deleted_count} candidates from database")
        
        # Step 2: Clear processed message IDs to force reprocessing
        _scraper().processed_message_ids.clear()
        
        # Step 3: Trigger full email sync via OAuth2 (Microsoft Graph)
        primary_email = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL') or _settings.email_address or ''
        token_storage = get_token_storage()
        token_data = token_storage.get_token(primary_email)
        
        if not token_data:
            return {
                "status": "error",
                "message": "No OAuth2 token found. Please authenticate first.",
                "deleted_count": deleted_count
            }
        
        # Initialize Graph service
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id)
        graph_service.access_token = token_data['access_token']
        graph_service.token_expiry = datetime.fromisoformat(token_data['expires_at'])
        
        # Fetch emails in batches to avoid OOM (max 5000 per batch, paginated)
        logger.info("📧 Fetching emails from inbox for re-parsing (paginated)...")
        result = await graph_service.get_messages(folder='inbox', top=5000, fetch_all=True)
        
        if result['status'] != 'success':
            return {
                "status": "error",
                "message": f"Failed to fetch emails: {result.get('message')}",
                "deleted_count": deleted_count
            }
        
        messages = result['messages']
        logger.info(f"📧 Found {len(messages)} emails to re-parse")
        
        # Process all messages
        new_count = 0
        ai_analyzed_count = 0
        
        async def process_message(msg):
            nonlocal new_count, ai_analyzed_count
            try:
                sender = msg.get('from', {}).get('emailAddress', {})
                sender_email = sender.get('address', '')
                sender_name = sender.get('name', '')
                
                subject = msg.get('subject', '')
                body = msg.get('body', {}).get('content', '')
                
                # Get attachments (resumes)
                attachments = []
                if msg.get('hasAttachments'):
                    attach_result = await graph_service.get_message_with_attachments(msg['id'])
                    if attach_result['status'] == 'success':
                        attachments = attach_result['attachments']
                
                # Use actual email received date from Graph API
                received_dt = msg.get('receivedDateTime')
                if received_dt:
                    try:
                        received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                    except Exception:
                        received_date = datetime.now()
                else:
                    received_date = datetime.now()
                
                email_data = {
                    'subject': subject,
                    'sender_email': sender_email,
                    'sender_name': sender_name,
                    'body': body,
                    'attachments': attachments,
                    'received_date': received_date
                }
                
                # Extract candidate from email (parses body + resume attachments)
                candidate = await _scraper().extract_candidate_from_email(email_data)
                if not candidate or not candidate.get('email'):
                    return

                # Block system/noreply emails and bad candidate names
                _email_lower = candidate['email'].lower()
                _name_lower = (candidate.get('name', '') or '').lower().strip()
                _BLOCKED_EMAIL_PATS = ['noreply', 'no-reply', 'no_reply', 'donotreply', 'do-not-reply',
                    'mailer-daemon', 'postmaster', 'notifications@', 'notification@',
                    'messages-noreply', 'alert@', 'alerts@', 'system@', 'bounce@']
                _BLOCKED_NAMES = ['unknown', 'messages', 'notification', 'noreply', 'no reply',
                    'system', 'admin', 'administrator', 'postmaster', 'mailer-daemon',
                    'indeed', 'linkedin', 'glassdoor', 'monster', 'info', 'support',
                    'test', 'null', 'none', 'n/a', 'na', '']
                if any(pat in _email_lower for pat in _BLOCKED_EMAIL_PATS):
                    return
                if _name_lower in _BLOCKED_NAMES or len(_name_lower) < 2:
                    return
                
                # AI Analysis: Use Local AI first, Gemini as fallback
                # Prefer resume_text if available, otherwise use summary + skills
                resume_text = candidate.get('resume_text', '') or (candidate.get('summary', '') + ' ' + ' '.join(candidate.get('skills', [])))
                if resume_text.strip():
                    try:
                        # Try Local AI first (FREE)
                        ai_analysis = await asyncio.wait_for(
                            _ai().analyze_candidate(resume_text),
                            timeout=_deps().AI_ANALYSIS_TIMEOUT
                        )
                        if ai_analysis:
                            candidate['job_category'] = normalize_category_backend(ai_analysis.get('job_category', candidate.get('job_category', 'General')))
                            candidate['matchScore'] = ai_analysis.get('quality_score')
                            # Update skills and experience from AI if better
                            if ai_analysis.get('skills'):
                                ai_skills = ai_analysis['skills']
                                if isinstance(ai_skills, list) and len(ai_skills) > len(candidate.get('skills', [])):
                                    candidate['skills'] = ai_skills
                            if ai_analysis.get('experience') and ai_analysis['experience'] > candidate.get('experience', 0):
                                candidate['experience'] = ai_analysis['experience']
                            ai_analyzed_count += 1
                    except asyncio.TimeoutError:
                        # Local AI timeout - calculate from available data
                        logger.warning(f"⏱️ Local AI timeout for {sender_email} - using calculated fallback")
                        _skills = candidate.get('skills', [])
                        _exp = candidate.get('experience', 0) or 0
                        _has_edu = bool(candidate.get('education'))
                        _has_summary = bool(candidate.get('summary', '').strip())
                        candidate['matchScore'] = min(92, max(15, 30 + min(25, len(_skills) * 3) + min(25, (_exp if isinstance(_exp, int) else 0) * 3) + (5 if _has_edu else 0) + (3 if _has_summary else 0)))
                    except Exception as ai_err:
                        logger.warning(f"AI analysis error: {str(ai_err)[:50]} - using calculated fallback")
                        _skills = candidate.get('skills', [])
                        _exp = candidate.get('experience', 0) or 0
                        _has_edu = bool(candidate.get('education'))
                        _has_summary = bool(candidate.get('summary', '').strip())
                        candidate['matchScore'] = min(92, max(15, 30 + min(25, len(_skills) * 3) + min(25, (_exp if isinstance(_exp, int) else 0) * 3) + (5 if _has_edu else 0) + (3 if _has_summary else 0)))
                
                # Save resume file if present
                resume_file = candidate.pop('resume_file_data', None)
                resume_filename = candidate.pop('resume_filename', None)
                
                # Save candidate to database
                await asyncio.to_thread(_db().insert_candidate, candidate)
                
                # Save resume file separately
                if resume_file and resume_filename:
                    content_type = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                    await asyncio.to_thread(_db().save_resume, candidate['id'], resume_filename, resume_file, content_type)
                
                new_count += 1
                    
            except Exception as e:
                logger.warning(f"Error processing message: {str(e)[:100]}")
        
        # Process in batches
        BATCH_SIZE = 5
        for i in range(0, len(messages), BATCH_SIZE):
            batch = messages[i:i+BATCH_SIZE]
            await asyncio.gather(*[process_message(msg) for msg in batch], return_exceptions=True)
            
            if len(messages) > 50 and (i + BATCH_SIZE) % 50 == 0:
                logger.info(f"📊 Progress: {min(i+BATCH_SIZE, len(messages))}/{len(messages)} emails processed, {new_count} candidates...")
        
        logger.info(f"✅ Re-parse complete: {new_count} candidates from {len(messages)} emails, {ai_analyzed_count} AI-analyzed")
        
        return {
            "status": "success",
            "message": "All emails re-parsed successfully",
            "deleted_count": deleted_count,
            "emails_processed": len(messages),
            "candidates_created": new_count,
            "ai_analyzed": ai_analyzed_count
        }
        
    except Exception as e:
        logger.error(f"Reset and reparse error: {e}")
        raise HTTPException(500, "Reset and reparse failed. Check server logs for details.")



@router.get("/api/candidates")
async def get_candidates(
    page: int = 1,
    limit: int = 50,
    job_category: Optional[str] = None,
    min_score: Optional[int] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    fields: Optional[str] = None,
    current_user: dict = Depends(require_auth)
):
    """
    Get candidates with OPTIMIZED pagination and caching
    Efficiently handles 100,000+ candidates
    Use fields=light for fast list views (skips resume_text, ai_analysis)
    Use search= to filter by name, email, skills, or job category
    Use status= to filter by candidate status (e.g. Shortlisted, Reviewed)
    """
    # Validate pagination bounds
    page = max(1, page)
    limit = max(1, min(500, limit))
    if search and len(search) > 500:
        raise HTTPException(400, "Search term too long (max 500 characters)")
    is_light = fields == 'light'
    # Create cache key
    cache_key = f"candidates_p{page}_l{limit}_c{job_category}_s{min_score}_q{search}_st{status}_f{fields}"
    
    # Check cache first
    if cache_key in response_cache:
        logger.info("Cache hit for candidates")
        cached_result = response_cache[cache_key]
        cached_result["from_cache"] = True
        return cached_result
    
    try:
        filters = {}
        if job_category:
            filters['job_category'] = job_category
        if min_score:
            filters['min_score'] = min_score
        if search:
            filters['search'] = search
        if status:
            filters['status'] = status
        
        # Use lightweight query for list views, full query for detail views
        # Reads don't need the db_semaphore — SQLite handles concurrent reads natively
        if is_light:
            candidates, total_count = await asyncio.to_thread(
                _db().get_candidates_light,
                page,
                limit,
                filters
            )
        else:
            candidates, total_count = await asyncio.to_thread(
                _db().get_candidates_paginated,
                page,
                limit,
                filters
            )
        
        result = {
            "page": page,
            "limit": limit,
            "candidates": candidates,
            "total": total_count,
            "from_cache": False
        }
        
        # Cache result
        response_cache[cache_key] = result
        
        return result
    except Exception as e:
        logger.error(f"Error fetching candidates: {e}")
        raise HTTPException(500, "Error fetching candidates")



@router.get("/api/candidates/new")
async def get_new_candidates(since: str, limit: int = 500, current_user: dict = Depends(require_auth)):
    """
    Get only NEW candidates since specified date
    Incremental processing - avoids reprocessing 100,000s
    """
    try:
        new_candidates = await asyncio.to_thread(_db().get_new_candidates_since, since)
        total_new = len(new_candidates)
        # Limit response size to prevent middleware/memory issues
        capped = new_candidates[:limit]
        return {
            "new_count": total_new,
            "returned": len(capped),
            "candidates": capped
        }
    except Exception as e:
        logger.error(f"Error fetching new candidates since {since}: {e}")
        raise HTTPException(500, "Error fetching new candidates. Check server logs for details.")




@router.post("/api/candidates/reprocess-garbled")
async def reprocess_garbled_candidates(current_user: dict = Depends(require_admin)):
    """
    Comprehensive reprocessing of all poorly processed candidates:
    1. Cleanup gibberish/system profiles
    2. Fix encoding issues (mojibake)
    3. Re-score candidates with 0 or default scores
    4. Regenerate AI analysis for candidates with no or stale analysis
    Runs cleanup first, then re-scores, all in one call.
    """
    try:
        _cache().clear()
        results = {'cleaned': 0, 'rescored': 0, 'encoding_fixed': 0, 'errors': 0}
        
        # Step 1: Run gibberish cleanup
        try:
            # Create a mock user for internal call
            cleanup_result = await cleanup_gibberish_profiles(current_user)
            results['cleaned'] = cleanup_result.get('deleted_count', 0)
            results['encoding_fixed'] = cleanup_result.get('encoding_fixed_count', 0)
        except Exception as ce:
            logger.warning(f"Gibberish cleanup phase error: {ce}")
        
        # Step 2: Re-score all candidates with 0/null/default(50) scores
        try:
            def _fetch_garbled_rescore():
                with _db().get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, email, name, skills, summary, education, work_history, resume_text, experience
                        FROM candidates 
                        WHERE is_active = 1 AND (match_score = 0 OR match_score IS NULL OR match_score = 50)
                    """)
                    return cursor.fetchall()
            rows = await asyncio.to_thread(_fetch_garbled_rescore)
            
            if rows:
                for row in rows:
                    try:
                        cid, email, name, skills_json, summary, education, work_history, resume_text, experience = row
                        try:
                            skills = json.loads(skills_json) if skills_json else []
                        except (json.JSONDecodeError, TypeError):
                            skills = []
                        
                        text_parts = []
                        if summary: text_parts.append(summary)
                        if resume_text: text_parts.append(resume_text[:2000])
                        if skills: text_parts.append(' '.join(str(s) for s in skills))
                        if education:
                            try:
                                ed = json.loads(education) if isinstance(education, str) else education
                                for e in (ed if isinstance(ed, list) else []):
                                    if isinstance(e, dict):
                                        text_parts.append(f"{e.get('degree', '')} {e.get('institution', '')}")
                            except Exception as e:
                                logger.debug(f"Non-critical: failed to parse education JSON: {e}")
                        if work_history:
                            try:
                                wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                                for job in (wh if isinstance(wh, list) else []):
                                    if isinstance(job, dict):
                                        text_parts.append(f"{job.get('title', '')} at {job.get('company', '')}")
                            except Exception as e:
                                logger.debug(f"Non-critical: failed to parse work_history JSON: {e}")
                        
                        combined_text = ' '.join(text_parts)
                        try:
                            exp_years = int(float(experience)) if experience else 0
                        except (ValueError, TypeError):
                            exp_years = 0
                        
                        if len(combined_text.strip()) < 10:
                            new_score = 15
                            new_category = 'Insufficient Data'
                        else:
                            try:
                                # Use Gemini if available for better results
                                _rescore_ai = _ai()
                                try:
                                    _g = get_gemini_service()
                                    if _g and _g.available:
                                        _rescore_ai = _g
                                except Exception as e:
                                    logger.debug(f"Non-critical: Gemini service not available for rescore: {e}")
                                analysis_result = await asyncio.wait_for(
                                    _rescore_ai.analyze_candidate(combined_text),
                                    timeout=_deps().AI_ANALYSIS_TIMEOUT
                                )
                                new_score = analysis_result.get('quality_score') or analysis_result.get('match_score')
                                try:
                                    new_score = int(float(new_score)) if new_score else 0
                                except Exception as e:
                                    logger.debug(f"Non-critical: failed to parse rescore value: {e}")
                                    new_score = 0
                                if new_score <= 0:
                                    new_score = min(95, max(25, len(skills) * 5 + exp_years * 3 + 20))
                                new_category = analysis_result.get('job_category', 'General')
                            except Exception:
                                # Rule-based scoring
                                new_score = min(95, max(25, len(skills) * 5 + exp_years * 3 + 20))
                                new_category = 'General'
                        
                        def _update_garbled_rescore(cid_val, score_val, cat_val):
                            with _db().get_connection() as uc:
                                ucur = uc.cursor()
                                ucur.execute("""
                                    UPDATE candidates SET match_score = ?, job_category = ?, last_updated = ? WHERE id = ?
                                """, (score_val, cat_val, datetime.now().isoformat(), cid_val))
                                uc.commit()
                        await asyncio.to_thread(_update_garbled_rescore, cid, new_score, new_category)
                        
                        results['rescored'] += 1
                    except Exception as re_err:
                        results['errors'] += 1
                        logger.warning(f"Rescore error for {name}: {str(re_err)[:50]}")
        except Exception as se:
            logger.warning(f"Rescore phase error: {se}")
        
        return {
            "status": "success",
            "message": f"Reprocessed: {results['cleaned']} cleaned, {results['rescored']} rescored, {results['encoding_fixed']} encoding fixed",
            **results
        }
    except Exception as e:
        logger.error(f"Reprocess garbled error: {e}")
        raise HTTPException(500, "Error")




@router.post("/api/candidates/fix-summaries")
async def fix_garbage_summaries(current_user: dict = Depends(require_auth)):
    """
    Find all candidates with garbage summaries (raw email body text like 'Dear HR...')
    and regenerate proper summaries using Gemini AI or structured field generation.
    """
    try:
        from services.email_scraper import is_garbage_summary, generate_structured_summary, sanitize_summary
        
        def _fetch_all_for_summary_fix():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, summary, resume_text, skills, experience, location, education, job_category, job_subcategory FROM candidates WHERE is_active = 1")
                rows = cursor.fetchall()
                col_names = [desc[0] for desc in cursor.description]
                return [dict(zip(col_names, row)) for row in rows]
        
        candidates = await asyncio.to_thread(_fetch_all_for_summary_fix)
        
        garbage_count = 0
        fixed_ai = 0
        fixed_structured = 0
        cleared = 0
        
        # Initialize AI service
        ai_svc = None
        try:
            from services.gemini_service import GeminiService
            ai_svc = GeminiService()
            logger.info("🤖 Gemini AI available for summary regeneration")
        except Exception as e:
            logger.warning(f"Gemini not available, will use structured summaries: {e}")
        
        for candidate in candidates:
            summary = candidate.get('summary', '') or ''
            
            if not is_garbage_summary(summary):
                continue
            
            garbage_count += 1
            candidate_id = candidate['id']
            new_summary = ''
            
            # Parse skills back from JSON if stored as string
            skills = candidate.get('skills', '[]')
            if isinstance(skills, str):
                try:
                    skills = json.loads(skills)
                except (json.JSONDecodeError, TypeError):
                    skills = []
            
            # Try AI regeneration first if resume text is available
            resume_text = candidate.get('resume_text', '') or ''
            if ai_svc and resume_text and len(resume_text) > 50:
                try:
                    ai_result = await asyncio.wait_for(
                        ai_svc.analyze_candidate(resume_text),
                        timeout=30
                    )
                    if ai_result:
                        ai_summary = ai_result.get('summary', '')
                        if ai_summary and not is_garbage_summary(ai_summary):
                            new_summary = ai_summary
                            fixed_ai += 1
                            
                            # Also update skills/experience if AI provided better data
                            ai_skills = ai_result.get('skills', [])
                            if ai_skills and len(ai_skills) > len(skills):
                                def _update_skills(cid, sk):
                                    with _db().get_connection() as conn:
                                        conn.execute("UPDATE candidates SET skills = ? WHERE id = ?", 
                                                    [json.dumps(sk), cid])
                                        conn.commit()
                                await asyncio.to_thread(_update_skills, candidate_id, ai_skills)
                except Exception as e:
                    logger.warning(f"AI summary regen failed for {candidate.get('name')}: {e}")
            
            # Fallback to structured summary from fields
            if not new_summary:
                candidate_data = {
                    'name': candidate.get('name', ''),
                    'skills': skills,
                    'experience': candidate.get('experience', 0),
                    'location': candidate.get('location', ''),
                    'education': candidate.get('education', ''),
                    'job_category': candidate.get('job_category', ''),
                    'job_subcategory': candidate.get('job_subcategory', ''),
                }
                new_summary = generate_structured_summary(candidate_data)
                if new_summary:
                    fixed_structured += 1
                else:
                    cleared += 1
            
            # Update in DB
            def _update_summary(cid, s):
                with _db().get_connection() as conn:
                    conn.execute("UPDATE candidates SET summary = ? WHERE id = ?", [s, cid])
                    conn.commit()
            await asyncio.to_thread(_update_summary, candidate_id, new_summary)
        
        # Backup to GCS if we made changes
        if garbage_count > 0:
            try:
                from google.cloud import storage
                gcs_client = storage.Client()
                bucket = gcs_client.bucket(GCS_BUCKET_NAME)
                blob = bucket.blob('db/recruitment.db')
                blob.upload_from_filename('/app/data/recruitment.db')
                logger.info("☁️ Database backed up to GCS after summary fix")
            except Exception as e:
                logger.warning(f"GCS backup after summary fix failed: {e}")
        
        result = {
            "status": "success",
            "total_garbage_found": garbage_count,
            "fixed_with_ai": fixed_ai,
            "fixed_with_structured": fixed_structured,
            "cleared_empty": cleared,
            "message": f"Fixed {garbage_count} garbage summaries: {fixed_ai} via AI, {fixed_structured} via structured generation, {cleared} cleared"
        }
        logger.info(f"✅ Summary cleanup: {result['message']}")
        return result
        
    except Exception as e:
        logger.error(f"Fix summaries error: {e}")
        raise HTTPException(500, "Error")




@router.post("/api/candidates/reprocess-scores")
async def reprocess_candidate_scores(current_user: dict = Depends(require_auth)):
    """
    Reprocess all candidates with match_score = 0 to calculate proper AI scores.
    This fixes candidates that were imported before AI scoring was properly connected.
    """
    try:
        def _fetch_rescore_candidates():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                # Get candidates with 0 or very low scores
                cursor.execute("""
                    SELECT id, email, name, skills, summary, education, work_history, resume_text
                    FROM candidates 
                    WHERE match_score = 0 OR match_score IS NULL OR (match_score <= 35 AND (job_category = 'General' OR job_category IS NULL))
                    LIMIT 200
                """)
                return cursor.fetchall()
        rows = await asyncio.to_thread(_fetch_rescore_candidates)
        
        if not rows:
            return {"status": "success", "message": "No candidates need reprocessing", "processed": 0}
        
        # Prefer Gemini for reprocessing
        reprocess_ai = _ai()
        try:
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                reprocess_ai = gemini_svc
        except Exception as e:
            logger.debug(f"Non-critical: Gemini service not available for reprocessing: {e}")
        
        processed = 0
        errors = 0
        
        for row in rows:
            try:
                candidate_id, email, name, skills_json, summary, education, work_history, resume_text = row
                
                # Prefer resume_text from PDF parsing
                if resume_text and len(resume_text.strip()) > 50:
                    analysis_text = resume_text[:3000]
                else:
                    # Build text for AI analysis from available fields
                    try:
                        skills = json.loads(skills_json) if skills_json else []
                    except (json.JSONDecodeError, TypeError):
                        skills = []
                    text_parts = []
                    if summary:
                        text_parts.append(summary)
                    if skills and skills != ['R']:
                        text_parts.append(' '.join(str(s) for s in skills))
                    if education:
                        text_parts.append(education)
                    if work_history:
                        try:
                            wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                            for job in wh:
                                if isinstance(job, dict):
                                    text_parts.append(f"{job.get('title', '')} at {job.get('company', '')}")
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse work_history JSON: {e}")
                    analysis_text = ' '.join(text_parts)
                
                if len(analysis_text.strip()) < 10:
                    # Not enough text - assign low score
                    new_score = 15
                    new_category = 'Insufficient Data'
                else:
                    # Use AI to analyze (prefer Gemini)
                    try:
                        ai_result = await asyncio.wait_for(
                            reprocess_ai.analyze_candidate(analysis_text),
                            timeout=_deps().AI_ANALYSIS_TIMEOUT
                        )
                        new_score = ai_result.get('quality_score') or ai_result.get('match_score')
                        try:
                            new_score = int(float(new_score)) if new_score else 0
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse reprocess score: {e}")
                            new_score = 0
                        if new_score <= 0:
                            new_score = 15  # Minimal score — AI returned nothing useful
                        new_category = ai_result.get('job_category', 'General')
                    except Exception:
                        new_score = 15
                        new_category = 'General'
                
                # Update database
                def _update_rescore_db(new_score_val, new_category_val, cid_val):
                    with _db().get_connection() as update_conn:
                        update_cursor = update_conn.cursor()
                        update_cursor.execute("""
                            UPDATE candidates SET match_score = ?, job_category = ? WHERE id = ?
                        """, (new_score_val, new_category_val, cid_val))
                        update_conn.commit()
                await asyncio.to_thread(_update_rescore_db, new_score, new_category, candidate_id)
                
                processed += 1
                logger.info(f"✅ Reprocessed {name}: Score={new_score}%, Category={new_category}")
                
            except Exception as e:
                errors += 1
                logger.warning(f"Error reprocessing candidate {row[2]}: {str(e)[:50]}")
        
        return {
            "status": "success",
            "message": f"Reprocessed {processed} candidates",
            "processed": processed,
            "errors": errors
        }
        
    except Exception as e:
        logger.error(f"Reprocess error: {str(e)}")
        raise HTTPException(500, "Error reprocessing")




# ── Category constants (extracted from main_legacy.py) ──
# ── Canonical category list + normalization map (backend-side) ──
CANONICAL_CATEGORIES = {
    'Software Engineering', 'Data & Analytics', 'IT & Systems', 'Engineering',
    'HR & Admin', 'Finance & Accounting', 'Sales', 'Operations',
    'Project Management', 'Consulting', 'Healthcare', 'Design & Creative',
    'QA & Testing', 'Marketing', 'Customer Service', 'Insurance & Safety',
    'Retail & Hospitality', 'Education', 'Legal', 'Business Analyst', 'General',
}

BACKEND_CATEGORY_NORMALIZE: dict = {
    'software development': 'Software Engineering', 'software engineer': 'Software Engineering',
    'software engineering': 'Software Engineering', 'web developer': 'Software Engineering',
    'web development': 'Software Engineering', 'frontend developer': 'Software Engineering',
    'front-end developer': 'Software Engineering', 'backend developer': 'Software Engineering',
    'full-stack developer': 'Software Engineering', 'full stack developer': 'Software Engineering',
    'mobile developer': 'Software Engineering', 'mobile development': 'Software Engineering',
    'devops engineer': 'Software Engineering', 'devops': 'Software Engineering',
    'cloud engineer': 'Software Engineering', 'rpa developer': 'Software Engineering',
    'developer': 'Software Engineering', 'programmer': 'Software Engineering',
    'data analyst': 'Data & Analytics', 'data science': 'Data & Analytics',
    'data scientist': 'Data & Analytics', 'data analytics': 'Data & Analytics',
    'data engineering': 'Data & Analytics', 'data engineer': 'Data & Analytics',
    'machine learning': 'Data & Analytics', 'ml engineer': 'Data & Analytics',
    'ai engineer': 'Data & Analytics', 'business intelligence': 'Data & Analytics',
    'bioinformatics': 'Data & Analytics',
    'it': 'IT & Systems', 'information technology': 'IT & Systems',
    'it support': 'IT & Systems', 'it/automation': 'IT & Systems',
    'network engineering': 'IT & Systems', 'network engineer': 'IT & Systems',
    'system administrator': 'IT & Systems', 'systems engineer': 'IT & Systems',
    'cybersecurity': 'IT & Systems', 'information security': 'IT & Systems',
    'database administrator': 'IT & Systems', 'technical support': 'IT & Systems',
    'engineering': 'Engineering', 'mechanical engineering': 'Engineering',
    'electrical engineering': 'Engineering', 'civil engineering': 'Engineering',
    'chemical engineering': 'Engineering', 'petroleum engineer': 'Engineering',
    'structural engineering': 'Engineering', 'mep engineer': 'Engineering',
    'hvac engineer': 'Engineering', 'construction': 'Engineering',
    'construction management': 'Engineering', 'architecture': 'Engineering',
    'oil & gas': 'Engineering', 'telecommunications': 'Engineering',
    'hr': 'HR & Admin', 'human resources': 'HR & Admin',
    'hr/admin': 'HR & Admin', 'administration': 'HR & Admin',
    'administrative': 'HR & Admin', 'talent acquisition': 'HR & Admin',
    'recruitment': 'HR & Admin', 'office administrator': 'HR & Admin',
    'executive assistant': 'HR & Admin',
    'finance': 'Finance & Accounting', 'accounting': 'Finance & Accounting',
    'accounting/finance': 'Finance & Accounting', 'financial services': 'Finance & Accounting',
    'audit': 'Finance & Accounting', 'banking': 'Finance & Accounting',
    'taxation': 'Finance & Accounting', 'bookkeeping': 'Finance & Accounting',
    'sales': 'Sales', 'business development': 'Sales',
    'sales & marketing': 'Sales', 'account management': 'Sales',
    'real estate': 'Sales', 'technical sales': 'Sales',
    'operations': 'Operations', 'supply chain': 'Operations',
    'supply chain management': 'Operations', 'logistics': 'Operations',
    'warehouse/logistics': 'Operations', 'procurement': 'Operations',
    'inventory management': 'Operations',
    'project management': 'Project Management', 'project manager': 'Project Management',
    'product management': 'Project Management', 'product manager': 'Project Management',
    'scrum master': 'Project Management', 'program manager': 'Project Management',
    'consulting': 'Consulting', 'management consulting': 'Consulting',
    'healthcare': 'Healthcare', 'medical': 'Healthcare',
    'nursing': 'Healthcare', 'pharmacy': 'Healthcare',
    'design': 'Design & Creative', 'graphic design': 'Design & Creative',
    'ui/ux': 'Design & Creative', 'interior design': 'Design & Creative',
    'media & creative': 'Design & Creative',
    'quality assurance': 'QA & Testing', 'qa': 'QA & Testing',
    'quality control': 'QA & Testing', 'testing': 'QA & Testing',
    'marketing': 'Marketing', 'digital marketing': 'Marketing',
    'social media': 'Marketing', 'content writing': 'Marketing',
    'public relations': 'Marketing', 'communications': 'Marketing',
    'customer service': 'Customer Service', 'customer support': 'Customer Service',
    'call center': 'Customer Service', 'client relations': 'Customer Service',
    'insurance': 'Insurance & Safety', 'safety officer': 'Insurance & Safety',
    'safety': 'Insurance & Safety', 'security': 'Insurance & Safety',
    'retail': 'Retail & Hospitality', 'hospitality': 'Retail & Hospitality',
    'hotel management': 'Retail & Hospitality', 'food & beverage': 'Retail & Hospitality',
    'education': 'Education', 'teaching': 'Education', 'training': 'Education',
    'legal': 'Legal', 'lawyer': 'Legal',
    'business analyst': 'Business Analyst', 'business analysis': 'Business Analyst',
    'systems analyst': 'Business Analyst', 'crm administrator/business analyst': 'Business Analyst',
    'insufficient data': 'General', 'other': 'General', 'n/a': 'General', 'not specified': 'General',
}

# Contains-based fuzzy fallback patterns (same as frontend)
BACKEND_CATEGORY_CONTAINS: list = [
    ('software', 'Software Engineering'), ('developer', 'Software Engineering'),
    ('full stack', 'Software Engineering'), ('full-stack', 'Software Engineering'),
    ('devops', 'Software Engineering'),
    ('data scien', 'Data & Analytics'), ('data analy', 'Data & Analytics'),
    ('data engineer', 'Data & Analytics'), ('machine learning', 'Data & Analytics'),
    ('business intelligence', 'Data & Analytics'),
    ('network', 'IT & Systems'), ('system admin', 'IT & Systems'),
    ('cyber', 'IT & Systems'), ('information technology', 'IT & Systems'),
    ('mechanical', 'Engineering'), ('electrical', 'Engineering'),
    ('civil', 'Engineering'), ('petroleum', 'Engineering'),
    ('structural', 'Engineering'), ('mep', 'Engineering'),
    ('construction', 'Engineering'),
    ('human resource', 'HR & Admin'), ('talent acqui', 'HR & Admin'),
    ('recruit', 'HR & Admin'), ('administrat', 'HR & Admin'),
    ('accounting', 'Finance & Accounting'), ('finance', 'Finance & Accounting'),
    ('audit', 'Finance & Accounting'), ('banking', 'Finance & Accounting'),
    ('sales', 'Sales'), ('business develop', 'Sales'), ('account manag', 'Sales'),
    ('supply chain', 'Operations'), ('logistics', 'Operations'),
    ('warehouse', 'Operations'), ('procurement', 'Operations'),
    ('operations', 'Operations'),
    ('project manag', 'Project Management'), ('program manag', 'Project Management'),
    ('product manag', 'Project Management'), ('scrum', 'Project Management'),
    ('consult', 'Consulting'),
    ('healthcare', 'Healthcare'), ('medical', 'Healthcare'),
    ('nurs', 'Healthcare'), ('pharma', 'Healthcare'),
    ('design', 'Design & Creative'), ('graphic', 'Design & Creative'),
    ('creative', 'Design & Creative'), ('ui/ux', 'Design & Creative'),
    ('quality assur', 'QA & Testing'), ('quality control', 'QA & Testing'),
    ('testing', 'QA & Testing'),
    ('marketing', 'Marketing'), ('social media', 'Marketing'),
    ('content writ', 'Marketing'), ('public relation', 'Marketing'),
    ('customer serv', 'Customer Service'), ('customer supp', 'Customer Service'),
    ('call center', 'Customer Service'),
    ('insurance', 'Insurance & Safety'), ('safety', 'Insurance & Safety'),
    ('retail', 'Retail & Hospitality'), ('hospitality', 'Retail & Hospitality'),
    ('hotel', 'Retail & Hospitality'),
    ('education', 'Education'), ('teaching', 'Education'), ('training', 'Education'),
    ('legal', 'Legal'), ('lawyer', 'Legal'),
    ('business analy', 'Business Analyst'),
]


def normalize_category_backend(raw: str) -> str:
    """Normalize a category name to a canonical category. Zero AI cost."""
    if not raw:
        return 'General'
    key = raw.lower().strip()
    # Already canonical?
    if raw in CANONICAL_CATEGORIES:
        return raw
    # Exact match
    if key in BACKEND_CATEGORY_NORMALIZE:
        return BACKEND_CATEGORY_NORMALIZE[key]
    # Contains-based fuzzy
    for pattern, category in BACKEND_CATEGORY_CONTAINS:
        if pattern in key:
            return category
    return raw  # Return as-is if no match



@router.post("/api/candidates/normalize-categories")
async def normalize_all_categories(current_user: dict = Depends(require_auth)):
    """
    Normalize ALL candidate categories to canonical names.
    This is a fast, zero-AI-cost operation that uses pattern matching.
    Fixes messy/duplicate category names across the entire database.
    """
    try:
        def _fetch_all_categories():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, name, job_category FROM candidates WHERE is_active = 1"
                )
                return cursor.fetchall()

        rows = await asyncio.to_thread(_fetch_all_categories)
        if not rows:
            return {"status": "success", "message": "No candidates found", "updated": 0}

        updated = 0
        changes: dict = {}

        for cid, name, raw_cat in rows:
            if not raw_cat:
                continue
            new_cat = normalize_category_backend(raw_cat)
            if new_cat != raw_cat:
                def _do_update(c_id, c_cat):
                    with _db().get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE candidates SET job_category = ? WHERE id = ?",
                            (c_cat, c_id),
                        )
                        conn.commit()
                await asyncio.to_thread(_do_update, cid, new_cat)
                updated += 1
                key = f"{raw_cat} -> {new_cat}"
                changes[key] = changes.get(key, 0) + 1

        # Persist to GCS
        if updated > 0:
            try:
                await asyncio.to_thread(backup_db_to_gcs)
                logger.info(f"✅ GCS backup after category normalization ({updated} updates)")
            except Exception as e:
                logger.warning(f"Non-critical: GCS backup after normalize failed: {e}")

        return {
            "status": "success",
            "message": f"Normalized {updated} candidates' categories",
            "updated": updated,
            "total_checked": len(rows),
            "changes": changes,
        }

    except Exception as e:
        logger.error(f"Normalize categories error: {e}")
        raise HTTPException(500, "Error normalizing categories")




@router.post("/api/candidates/recategorize-general")
async def recategorize_general_candidates(current_user: dict = Depends(require_auth)):
    """
    Re-categorize all candidates with job_category='General' using skills-based classification.
    Uses the rule-based classify_job_title() from job_taxonomy for speed (no AI cost).
    Falls back to skills keyword matching when job title classification returns General.
    """
    try:
        from services.job_taxonomy import classify_job_title

        # Skills-to-category fallback mapping
        SKILL_CATEGORY_MAP = {
            "Software Engineer": ["python", "java", "javascript", "react", "angular", "vue", "node", "django",
                "flask", "spring", "c++", "c#", "go", "rust", "typescript", "php", "ruby", "swift",
                "kotlin", "flutter", "mobile", "android", "ios", "html", "css", "sass", "webpack",
                "rest", "graphql", "api", "microservices", "oop", ".net", "asp.net", "laravel",
                "rails", "next.js", "nuxt", "svelte", "redux", "sql", "nosql", "postgresql",
                "mysql", "mongodb", "redis", "elasticsearch", "software development", "full stack",
                "backend", "frontend", "web development"],
            "DevOps Engineer": ["docker", "kubernetes", "k8s", "terraform", "ansible", "jenkins",
                "ci/cd", "aws", "azure", "gcp", "linux", "devops", "helm", "prometheus", "grafana",
                "cloudformation", "puppet", "chef", "vagrant", "openshift", "argocd", "github actions"],
            "Data Scientist": ["machine learning", "deep learning", "tensorflow", "pytorch", "pandas",
                "numpy", "scikit", "data science", "nlp", "computer vision", "ml", "ai",
                "artificial intelligence", "data analysis", "tableau", "power bi", "r", "statistics",
                "regression", "classification", "neural network", "big data", "spark", "hadoop",
                "data engineering", "etl", "data pipeline", "airflow", "dbt"],
            "Cybersecurity": ["security", "penetration testing", "soc", "siem", "firewall", "ids",
                "ips", "cyber", "vulnerability", "infosec", "owasp", "nist", "iso 27001",
                "ethical hacking", "incident response", "malware", "encryption", "forensics"],
            "QA / Testing": ["selenium", "cypress", "testing", "qa", "jest", "junit", "testng",
                "automation testing", "manual testing", "quality assurance", "appium", "postman",
                "performance testing", "jmeter", "loadrunner", "regression testing", "sdet",
                "test plan", "test cases", "bug tracking"],
            "IT & Systems": ["system admin", "network", "cisco", "vmware", "active directory",
                "windows server", "it support", "helpdesk", "tcp/ip", "dns", "dhcp", "vpn",
                "firewall", "cloud architecture", "solutions architect", "enterprise", "sap",
                "erp", "crm", "dynamics", "dba", "database admin", "oracle", "sharepoint"],
            "Product Manager": ["product management", "roadmap", "user stories", "backlog",
                "product strategy", "mvp", "stakeholder", "product owner", "product lifecycle"],
            "Design": ["figma", "sketch", "adobe xd", "photoshop", "illustrator", "ui", "ux",
                "user experience", "user interface", "wireframe", "prototype", "design system",
                "indesign", "after effects", "graphic design", "web design", "responsive design"],
            "Project Management": ["project management", "pmp", "prince2", "agile", "scrum",
                "kanban", "jira", "confluence", "gantt", "risk management", "scrum master",
                "sprint planning", "waterfall", "program management", "delivery management"],
            "Business Analyst": ["business analysis", "requirements gathering", "brd", "frd",
                "use case", "process mapping", "gap analysis", "swimlane", "visio", "bpmn",
                "systems analysis"],
            "Marketing": ["marketing", "seo", "sem", "google ads", "facebook ads", "social media",
                "content marketing", "email marketing", "digital marketing", "analytics",
                "google analytics", "hubspot", "mailchimp", "brand", "campaign", "ppc", "crm marketing"],
            "Sales": ["sales", "salesforce", "crm", "business development", "lead generation",
                "pipeline", "account management", "b2b", "b2c", "cold calling", "negotiation"],
            "Finance": ["finance", "accounting", "budgeting", "forecasting", "financial modeling",
                "excel", "quickbooks", "tally", "gst", "taxation", "audit", "ifrs", "gaap",
                "accounts payable", "accounts receivable", "bookkeeping", "ebitda"],
            "HR": ["human resources", "recruitment", "talent acquisition", "onboarding",
                "performance management", "hris", "payroll", "employee engagement", "l&d",
                "compensation", "benefits", "workday", "bamboohr", "succession planning"],
            "Content & Communications": ["content writing", "copywriting", "blogging", "editing",
                "journalism", "public relations", "corporate communications", "technical writing",
                "documentation", "newsletter"],
            "Healthcare": ["healthcare", "medical", "clinical", "nursing", "pharmacy", "patient care",
                "ehr", "hipaa", "biomedical", "diagnosis", "treatment", "hospital"],
            "Education": ["teaching", "curriculum", "e-learning", "lms", "moodle", "education",
                "tutoring", "instructional design", "training", "course development"],
            "Engineering": ["mechanical", "electrical", "civil", "structural", "cad", "autocad",
                "solidworks", "matlab", "aerospace", "robotics", "plc", "scada", "manufacturing",
                "industrial engineering"],
            "Customer Support": ["customer service", "customer success", "call center", "zendesk",
                "freshdesk", "live chat", "ticketing", "client relations", "help desk support"],
            "Media & Creative": ["video editing", "premiere pro", "final cut", "animation",
                "3d modeling", "blender", "maya", "photography", "lightroom", "motion graphics"],
            "Operations": ["operations", "supply chain", "logistics", "procurement", "inventory",
                "warehouse", "vendor management", "lean", "six sigma", "kaizen"],
            "Consulting": ["consulting", "strategy", "business consulting", "management consulting",
                "advisory", "change management", "transformation"],
        }

        def _fetch_general_candidates():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, skills, summary, work_history, resume_text
                    FROM candidates 
                    WHERE job_category = 'General' OR job_category IS NULL OR job_category = ''
                """)
                return cursor.fetchall()

        rows = await asyncio.to_thread(_fetch_general_candidates)

        if not rows:
            return {"status": "success", "message": "No General category candidates found", "updated": 0}

        updated = 0
        still_general = 0
        category_counts = {}

        for row in rows:
            try:
                candidate_id, name, skills_json, summary, work_history, resume_text = row

                # Build comprehensive text for classification
                text_parts = []

                # 1. Try work history job titles first (best signal)
                if work_history:
                    try:
                        wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                        if isinstance(wh, list):
                            for job in wh:
                                if isinstance(job, dict):
                                    title = job.get('title', '') or job.get('position', '')
                                    if title:
                                        text_parts.append(title)
                    except Exception:
                        pass

                # 2. Summary
                if summary:
                    text_parts.append(summary[:200])

                # Try classify_job_title with work history titles
                new_category = "General"
                new_subcategory = "Other"
                combined_text = ' '.join(text_parts)

                if combined_text.strip():
                    cat, sub = classify_job_title(combined_text)
                    if cat != "General":
                        new_category = cat
                        new_subcategory = sub

                # 3. If still General, try skills-based matching
                if new_category == "General":
                    skills_list = []
                    if skills_json:
                        try:
                            skills_list = json.loads(skills_json) if isinstance(skills_json, str) else skills_json
                            if isinstance(skills_list, list):
                                skills_list = [s.lower().strip() for s in skills_list if isinstance(s, str)]
                        except Exception:
                            skills_list = []

                    # Also extract text from resume
                    all_text = ' '.join(skills_list)
                    if resume_text:
                        all_text += ' ' + resume_text[:500].lower()
                    if summary:
                        all_text += ' ' + summary.lower()

                    best_match = "General"
                    best_score = 0
                    for category, keywords in SKILL_CATEGORY_MAP.items():
                        score = sum(1 for kw in keywords if kw in all_text)
                        if score > best_score:
                            best_score = score
                            best_match = category
                    
                    if best_score >= 2:  # Need at least 2 skill matches
                        new_category = best_match
                        from services.job_taxonomy import _match_subcategory
                        new_subcategory = _match_subcategory(all_text, new_category)

                if new_category != "General":
                    def _update_category(cid, cat, sub):
                        with _db().get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE candidates SET job_category = ?, job_subcategory = ? WHERE id = ?
                            """, (cat, sub, cid))
                            conn.commit()
                    await asyncio.to_thread(_update_category, candidate_id, new_category, new_subcategory)
                    updated += 1
                    category_counts[new_category] = category_counts.get(new_category, 0) + 1
                    logger.info(f"✅ Re-categorized {name}: {new_category} / {new_subcategory}")
                else:
                    still_general += 1

            except Exception as e:
                logger.warning(f"Error re-categorizing {row[1]}: {str(e)[:80]}")

        # Persist changes to GCS
        if updated > 0:
            try:
                await asyncio.to_thread(backup_db_to_gcs)
                logger.info(f"✅ GCS backup after recategorization ({updated} updates)")
            except Exception as e:
                logger.warning(f"Non-critical: GCS backup after recategorize failed: {e}")

        return {
            "status": "success",
            "message": f"Re-categorized {updated} candidates, {still_general} remain General",
            "updated": updated,
            "still_general": still_general,
            "category_breakdown": category_counts
        }

    except Exception as e:
        logger.error(f"Recategorize error: {str(e)}")
        raise HTTPException(500, "Error re-categorizing")




@router.post("/api/candidates/reprocess-with-gemini")
async def reprocess_candidates_with_gemini(current_user: dict = Depends(require_auth)):
    """
    Bulk reprocess ALL poorly-scored candidates using Gemini AI.
    Targets candidates with: score <= 35, category='General', minimal skills like '["R"]'.
    Uses resume_text (from PDF parsing) for best results. Cost-optimized with batching and delays.
    """
    try:
        gemini_svc = get_gemini_service()
        if not gemini_svc or not gemini_svc.available:
            raise HTTPException(503, "Gemini service not available")
        
        def _fetch_gemini_rescore():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, email, skills, summary, education, work_history, 
                           experience, resume_text, match_score, job_category
                    FROM candidates 
                    WHERE is_active = 1
                    AND (
                        (match_score <= 35 AND match_score > 0)
                        OR (job_category = 'General' AND match_score <= 50)
                        OR (skills IS NULL OR skills = '' OR skills = '[]' OR skills = '["R"]' OR skills = '["r"]')
                    )
                    ORDER BY created_at DESC
                    LIMIT 500
                """)
                return cursor.fetchall()
        rows = await asyncio.to_thread(_fetch_gemini_rescore)
        
        if not rows:
            return {"status": "success", "message": "No candidates need Gemini reprocessing", "processed": 0, "total_found": 0}
        
        total_found = len(rows)
        processed = 0
        improved = 0
        errors = 0
        
        logger.info(f"🚀 Gemini bulk reprocess: Found {total_found} poorly-scored candidates")
        
        for row in rows:
            try:
                candidate_id = row[0]
                name = row[1] or 'Unknown'
                email = row[2] or ''
                skills_json = row[3] or '[]'
                summary = row[4] or ''
                education = row[5] or ''
                work_history = row[6] or ''
                experience = row[7] or 0
                resume_text = row[8] or ''
                old_score = row[9] or 0
                old_category = row[10] or 'General'
                
                # Build the best possible analysis text — prefer resume_text (from PDF)
                text_parts = []
                if resume_text and len(resume_text.strip()) > 50:
                    text_parts.append(resume_text[:3000])
                else:
                    # Fallback: reconstruct from fields
                    if name and name != 'Unknown':
                        text_parts.append(f"Name: {name}")
                    if summary:
                        text_parts.append(f"Summary: {summary}")
                    try:
                        skills = json.loads(skills_json) if isinstance(skills_json, str) else skills_json
                        if skills and isinstance(skills, list) and skills != ['R']:
                            text_parts.append(f"Skills: {', '.join(skills)}")
                    except Exception as e:
                        logger.debug(f"Non-critical: failed to parse skills JSON: {e}")
                    if education:
                        text_parts.append(f"Education: {education}")
                    if work_history:
                        try:
                            wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                            for job in (wh if isinstance(wh, list) else []):
                                if isinstance(job, dict):
                                    text_parts.append(f"Work: {job.get('title', '')} at {job.get('company', '')} ({job.get('duration', '')})")
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse work_history JSON: {e}")
                
                analysis_text = '\n'.join(text_parts)
                
                if len(analysis_text.strip()) < 20:
                    # Not enough data to analyze — set a low score honestly
                    def _set_insufficient_data(cid):
                        with _db().get_connection() as uc:
                            ucur = uc.cursor()
                            ucur.execute("""
                                UPDATE candidates SET match_score = 15, job_category = 'Insufficient Data',
                                last_updated = datetime('now') WHERE id = ?
                            """, (cid,))
                            uc.commit()
                    await asyncio.to_thread(_set_insufficient_data, candidate_id)
                    continue
                
                # Call Gemini for analysis
                result = await asyncio.wait_for(
                    gemini_svc.analyze_candidate(analysis_text),
                    timeout=60
                )
                
                if result:
                    new_score = result.get('quality_score') or result.get('match_score') or result.get('overall_score')
                    try:
                        new_score = int(float(new_score)) if new_score else 0
                        new_score = max(10, min(95, new_score))  # Sane bounds
                    except (ValueError, TypeError):
                        new_score = 0
                    if new_score <= 0:
                        # Calculate from AI-extracted data
                        _sk = result.get('skills', [])
                        _ex = result.get('experience', 0) or 0
                        try: _ex = int(float(_ex))
                        except (ValueError, TypeError): _ex = 0
                        new_score = max(15, min(90, 25 + len(_sk) * 3 + _ex * 3 + (10 if result.get('education') else 0)))
                    
                    new_category = result.get('job_category', result.get('category', '')) or 'General'
                    new_skills = result.get('skills', [])
                    new_experience = result.get('experience', 0)
                    new_summary = result.get('summary', '') or ''
                    
                    try:
                        new_experience = int(float(new_experience)) if new_experience else 0
                    except (ValueError, TypeError):
                        new_experience = 0
                    
                    skills_str = json.dumps(new_skills) if isinstance(new_skills, list) and new_skills else None
                    
                    def _update_gemini_result(ns, nc, ss, ne, nsm, jr, cid):
                        with _db().get_connection() as uc:
                            ucur = uc.cursor()
                            ucur.execute("""
                                UPDATE candidates 
                                SET match_score = ?,
                                    job_category = CASE WHEN ? != '' AND ? != 'General' THEN ? ELSE job_category END,
                                    skills = CASE WHEN ? IS NOT NULL AND length(?) > 4 THEN ? ELSE skills END,
                                    experience = CASE WHEN ? > 0 THEN MAX(COALESCE(experience, 0), ?) ELSE experience END,
                                    summary = CASE WHEN ? != '' AND length(?) > length(COALESCE(summary, '')) THEN ? ELSE summary END,
                                    ai_analysis = ?,
                                    last_updated = datetime('now')
                                WHERE id = ?
                            """, (ns,
                                  nc, nc, nc,
                                  ss, ss, ss,
                                  ne, ne,
                                  nsm, nsm, nsm,
                                  jr,
                                  cid))
                            uc.commit()
                    await asyncio.to_thread(_update_gemini_result, new_score,
                                           new_category, skills_str, new_experience,
                                           new_summary, json.dumps(result), candidate_id)
                    
                    if new_score > old_score:
                        improved += 1
                    processed += 1
                    
                    if processed % 25 == 0:
                        logger.info(f"📊 Gemini reprocess progress: {processed}/{total_found} done, {improved} improved")
                
                # Rate limit: 1.5 second delay between Gemini calls (cost optimization)
                await asyncio.sleep(1.5)
                
            except asyncio.TimeoutError:
                errors += 1
                logger.warning(f"⏳ Gemini timeout for {name}")
            except Exception as e:
                errors += 1
                logger.warning(f"⚠️ Gemini reprocess error for {name}: {str(e)[:100]}")
                await asyncio.sleep(1)
        
        # Backup after bulk reprocessing
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, backup_db_to_gcs)
            logger.info("💾 Database backed up after Gemini reprocessing")
        except Exception as backup_err:
            logger.warning(f"Backup after reprocess failed: {backup_err}")
        
        logger.info(f"✅ Gemini bulk reprocess complete: {processed} processed, {improved} improved, {errors} errors out of {total_found}")
        
        return {
            "status": "success",
            "message": f"Gemini reprocessed {processed} candidates, {improved} improved scores",
            "total_found": total_found,
            "processed": processed,
            "improved": improved,
            "errors": errors
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Gemini bulk reprocess error: {str(e)}")
        raise HTTPException(500, "Error")




@router.get("/api/candidates/{candidate_id}")
async def get_candidate(candidate_id: str, current_user: dict = Depends(require_auth)):
    """Get single candidate by ID"""
    try:
        def _get_candidate_by_id():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
                row = cursor.fetchone()
            if not row:
                return None
            return _db()._row_to_candidate(row)
        candidate = await asyncio.to_thread(_get_candidate_by_id)
        if candidate is None:
            raise HTTPException(404, "Candidate not found")
        return candidate
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Error")




@router.get("/api/candidates/{candidate_id}/resume")
async def download_resume(candidate_id: str, current_user: dict = Depends(require_auth)):
    """Download candidate's resume file"""
    from fastapi.responses import Response
    
    try:
        resume = await asyncio.to_thread(_db().get_resume, candidate_id)
        
        if not resume:
            raise HTTPException(404, "Resume not found for this candidate")
        
        return Response(
            content=resume['file_data'],
            media_type=resume['content_type'],
            headers={
                'Content-Disposition': f'attachment; filename="{os.path.basename(resume.get("filename") or "resume.pdf")}"'
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume download error: {str(e)}")
        raise HTTPException(500, "Error downloading resume")




@router.post("/api/candidates/{candidate_id}/resume")
async def upload_resume_for_candidate(candidate_id: str, file: UploadFile = File(...), current_user: dict = Depends(require_auth)):
    """Upload a resume file for an existing candidate. Also re-parses the resume and updates candidate data."""
    try:
        filename = file.filename or "resume.pdf"
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in ('pdf', 'docx', 'doc'):
            raise HTTPException(400, "Only PDF and DOCX files are supported.")
        
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(400, "File too large. Max 10MB.")
        if len(content) < 100:
            raise HTTPException(400, "File too small or empty.")
        
        # Verify candidate exists
        def _check_exists():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name FROM candidates WHERE id = ?", [candidate_id])
                return cursor.fetchone()
        
        existing = await asyncio.to_thread(_check_exists)
        if not existing:
            raise HTTPException(404, "Candidate not found")
        
        # Save the resume binary file
        content_type = 'application/pdf' if ext == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        await asyncio.to_thread(_db().save_resume, candidate_id, filename, content, content_type)
        
        # Parse the resume to extract text and structured data
        try:
            parsed = await _resume_parser().parse_resume(content, filename)
            resume_text = parsed.get('raw_text', '') or ''
            
            updates = {}
            if resume_text:
                updates['resume_text'] = resume_text[:10000]
            
            # Run AI analysis on the resume text
            if resume_text and len(resume_text) > 50:
                try:
                    ai_result = await asyncio.wait_for(
                        _ai().analyze_candidate(resume_text),
                        timeout=_deps().AI_ANALYSIS_TIMEOUT
                    )
                    if ai_result:
                        if ai_result.get('summary'):
                            from services.email_scraper import is_garbage_summary
                            if not is_garbage_summary(ai_result['summary']):
                                updates['summary'] = ai_result['summary']
                        if ai_result.get('skills'):
                            updates['skills'] = json.dumps(ai_result['skills'])
                        if ai_result.get('experience'):
                            updates['experience'] = ai_result['experience']
                        if ai_result.get('quality_score'):
                            updates['match_score'] = ai_result['quality_score']
                        if ai_result.get('job_category') and ai_result['job_category'] != 'General':
                            updates['job_category'] = ai_result['job_category']
                        if ai_result.get('job_subcategory'):
                            updates['job_subcategory'] = ai_result['job_subcategory']
                        if ai_result.get('education'):
                            updates['education'] = json.dumps(ai_result['education']) if isinstance(ai_result['education'], list) else ai_result['education']
                        if ai_result.get('location'):
                            updates['location'] = ai_result['location']
                        if ai_result.get('phone'):
                            updates['phone'] = ai_result['phone']
                        if ai_result.get('certifications'):
                            updates['certifications'] = json.dumps(ai_result['certifications'])
                        if ai_result.get('languages'):
                            updates['languages'] = json.dumps(ai_result['languages'])
                except Exception as ai_err:
                    logger.warning(f"AI analysis failed for uploaded resume: {ai_err}")
            
            # Update candidate with extracted data
            if updates:
                def _update_candidate():
                    with _db().get_connection() as conn:
                        set_clause = ', '.join(f"{k} = ?" for k in updates.keys())
                        values = list(updates.values()) + [candidate_id]
                        conn.execute(f"UPDATE candidates SET {set_clause} WHERE id = ?", values)
                        conn.commit()
                await asyncio.to_thread(_update_candidate)
                
        except Exception as parse_err:
            logger.warning(f"Resume parsing failed (file still saved): {parse_err}")
        
        # Backup to GCS
        try:
            await asyncio.to_thread(backup_db_to_gcs)
        except Exception:
            pass
        
        logger.info(f"✅ Resume uploaded for candidate {candidate_id}: {filename}")
        return {"status": "success", "message": f"Resume '{filename}' uploaded successfully", "candidate_id": candidate_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume upload for candidate error: {e}")
        raise HTTPException(500, "Error")




@router.post("/api/candidates/batch")
async def batch_import_candidates(
    candidates: List[dict],
    analyze: bool = True,
    current_user: dict = Depends(require_auth)
):
    """
    Import candidates in batch for high-volume scenarios (10,000+)
    - Uses batch database inserts for speed
    - Optional AI analysis with batch processing
    """
    try:
        start_time = time.time()
        logger.info(f"📊 Batch import started: {len(candidates)} candidates")
        
        # AI analyze in batches if requested
        if analyze and len(candidates) > 0:
            texts = [c.get('summary', '') or c.get('resume_text', '') for c in candidates]
            
            # Process in small batches to avoid memory issues
            BATCH_SIZE = 50
            for i in range(0, len(candidates), BATCH_SIZE):
                batch_texts = texts[i:i + BATCH_SIZE]
                batch_candidates = candidates[i:i + BATCH_SIZE]
                
                for idx, text in enumerate(batch_texts):
                    if text and len(text) > 20:
                        try:
                            analysis = await asyncio.wait_for(
                                _ai().analyze_candidate(text),
                                timeout=_deps().AI_ANALYSIS_TIMEOUT
                            )
                            if analysis:
                                # Email-parsed values take priority over LLM for contact info
                                batch_candidates[idx].update({
                                    'job_category': analysis.get('job_category', 'General'),
                                    'matchScore': analysis.get('quality_score'),
                                    'skills': analysis.get('skills', []),
                                    'experience': analysis.get('experience', 0),
                                    'education': analysis.get('education', []),
                                    'phone': batch_candidates[idx].get('phone') or analysis.get('phone', ''),
                                    'location': batch_candidates[idx].get('location') or analysis.get('location', ''),
                                    'linkedin': batch_candidates[idx].get('linkedin') or analysis.get('linkedin', ''),
                                })
                        except Exception as e:
                            logger.warning(f"AI batch analysis failed for item {i+idx}: {str(e)[:50]}")
                
                if (i + BATCH_SIZE) % 500 == 0:
                    logger.info(f"📊 AI Progress: {min(i + BATCH_SIZE, len(candidates))}/{len(candidates)}")
        
        # Bulk insert to database
        result = await asyncio.to_thread(
            _db().insert_candidates_batch, 
            candidates
        )
        
        elapsed = time.time() - start_time
        rate = len(candidates) / elapsed if elapsed > 0 else 0
        
        logger.info(f"✅ Batch complete: {result['inserted']} inserted, {result['updated']} updated in {elapsed:.2f}s ({rate:.0f}/sec)")
        
        return {
            "status": "completed",
            "total": len(candidates),
            "inserted": result['inserted'],
            "updated": result['updated'],
            "elapsed_seconds": round(elapsed, 2),
            "rate_per_second": round(rate, 1)
        }
        
    except Exception as e:
        logger.error(f"Batch import error: {e}")
        raise HTTPException(500, "Batch import failed")



@router.post("/api/candidates/linkedin")
async def import_linkedin_profile(profile: LinkedInProfileImport, current_user: dict = Depends(require_auth)):
    """
    Import a candidate from LinkedIn profile scraped by browser extension.
    Analyzes the profile and stores in database.
    """
    try:
        logger.info(f"📥 LinkedIn import: {profile.name}")
        
        # Check for existing candidate with same LinkedIn URL
        existing = await asyncio.to_thread(_db().get_candidate_by_linkedin, profile.linkedin)
        if existing:
            logger.info(f"Updating existing LinkedIn profile: {profile.name}")
            # Update existing record
            candidate_id = existing.get('id')
        else:
            candidate_id = None
        
        # Analyze the profile using AI
        analysis = None
        if profile.resume_text and len(profile.resume_text) > 50:
            try:
                analysis = await asyncio.wait_for(
                    _ai().analyze_candidate(profile.resume_text),
                    timeout=_deps().AI_ANALYSIS_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning(f"AI analysis timeout for {profile.name}")
            except Exception as e:
                logger.warning(f"AI analysis error for {profile.name}: {e}")
        
        # Build candidate record
        candidate_data = {
            "id": candidate_id or f"linkedin_{datetime.now().strftime('%Y%m%d%H%M%S')}_{profile.name.replace(' ', '_')}",
            "name": profile.name,
            "email": profile.email,
            "phone": profile.phone or "",
            "location": profile.location or "",
            "linkedin": profile.linkedin,
            "source": profile.source,
            "skills": analysis.get('skills', profile.skills) if analysis else (profile.skills or []),
            "experience": profile.experience or 0,
            "matchScore": analysis.get('quality_score') if analysis else 0,
            "status": "new",
            "appliedDate": profile.scraped_at or datetime.now().isoformat(),
            "summary": profile.headline or "",
            "jobCategory": analysis.get('job_category', profile.job_category) if analysis else profile.job_category,
            "resumeText": profile.resume_text or "",
            "profileImage": profile.profile_image or "",
            "education": profile.education or [],
            "workExperience": profile.work_experience or [],
            "certifications": profile.certifications or [],
            "languages": profile.languages or [],
            "aiAnalysis": analysis
        }
        
        # Store in database
        if existing:
            candidate_data['id'] = candidate_id
            await asyncio.to_thread(_db().update_candidate, candidate_data)
        else:
            await asyncio.to_thread(_db().insert_candidate, candidate_data)
        
        logger.info(f"✅ LinkedIn profile imported: {profile.name} (Score: {candidate_data['matchScore']})")
        
        return {
            "success": True,
            "id": candidate_data["id"],
            "name": profile.name,
            "matchScore": candidate_data["matchScore"],
            "skills": candidate_data["skills"],
            "jobCategory": candidate_data["jobCategory"]
        }
        
    except Exception as e:
        logger.error(f"LinkedIn import error: {e}")
        raise HTTPException(500, "Failed to import LinkedIn profile")



@router.get("/api/candidates/stream")
async def stream_all_candidates(batch_size: int = 100, current_user: dict = Depends(require_auth)):
    """
    Stream all candidates for large exports (10,000+)
    Returns JSON array streamed in batches
    """
    from fastapi.responses import StreamingResponse
    
    async def generate():
        yield "["
        first = True
        all_batches = await asyncio.to_thread(lambda: list(_db().get_candidates_stream(min(batch_size, 500))))
        for batch in all_batches:
            for candidate in batch:
                if not first:
                    yield ","
                yield json.dumps(candidate)
                first = False
        yield "]"
    
    return StreamingResponse(
        generate(),
        media_type="application/json",
        headers={"X-Stream-Type": "batch"}
    )



@router.post("/api/candidates/deduplicate")
async def deduplicate_candidates(current_user: dict = Depends(require_auth)):
    """Merge duplicate candidates (same email, different case)."""
    try:
        result = await asyncio.to_thread(_db().deduplicate_candidates)
        if result.get('merged', 0) > 0:
            _cache().clear()
        return result
    except Exception as e:
        logger.error(f"Dedup error: {str(e)}")
        raise HTTPException(500, "Error deduplicating")



