"""Route module: admin. Auto-extracted from main_legacy.py."""
import os
import json
import asyncio
import logging
import time
from core.lifespan import backup_db_to_gcs, LOCAL_DB_PATH
from services.db_repair import audit_database, repair_database, quick_health_check
from services.resume_parser import is_spaced_text, text_quality_score, collapse_spaced_chars
from services.token_storage import get_token_storage
from services.microsoft_graph import MicrosoftGraphService
import re
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends, UploadFile, File, Body, Query
from fastapi.responses import Response, JSONResponse, StreamingResponse, RedirectResponse

from core.config import get_settings
from core.dependencies import require_auth, optional_auth, require_admin

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(tags=["admin"])


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


@router.post("/api/admin/reset-database")
async def reset_database(auth=Depends(require_admin)):
    """Nuclear reset: wipe all candidates, resumes, caches, logs. Keeps users."""
    def _reset():
        with _db().get_connection() as conn:
            cursor = conn.cursor()
            tables_to_wipe = [
                'ai_score_cache',
                'resumes',
                'email_processing_log',
                'search_history',
                'candidates',
                'sync_metadata',
            ]
            for table in tables_to_wipe:
                try:
                    cursor.execute(f"DELETE FROM {table}")
                    logger.info(f"🗑️ Wiped table: {table}")
                except Exception as e:
                    logger.warning(f"Could not wipe {table}: {e}")
            conn.commit()
            cursor.execute("SELECT COUNT(*) FROM candidates")
            remaining = cursor.fetchone()[0]
            return remaining
    remaining = await asyncio.to_thread(_reset)
    # Clear in-memory caches
    _cache().clear()
    if hasattr(_ai(), 'embedding_cache'):
        _ai().embedding_cache.clear()
    return {
        "status": "success",
        "message": "Database reset complete — all candidates, resumes, caches, and logs wiped",
        "remaining_candidates": remaining
    }



@router.post("/api/admin/backup-db")
async def manual_backup_db(auth=Depends(require_admin)):
    """Manually trigger a database backup to GCS"""
    try:
        success = await asyncio.to_thread(backup_db_to_gcs)
        if success:
            size_mb = os.path.getsize(LOCAL_DB_PATH) / (1024 * 1024) if os.path.exists(LOCAL_DB_PATH) else 0
            return {"status": "success", "message": f"Database backed up to GCS ({size_mb:.1f} MB)"}
        else:
            return {"status": "warning", "message": "Backup skipped — GCS not available or no database file"}
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        raise HTTPException(status_code=500, detail="Backup failed. Check server logs for details.")




@router.post("/api/admin/cleanup-gibberish")
async def cleanup_gibberish_profiles(current_user: dict = Depends(require_admin)):
    """
    Comprehensive database repair: fix gibberish, garbled names, encoding issues,
    HTML in data, bad phones, duplicate emails, empty profiles, and more.
    Uses the new db_repair service for thorough detection and fixing.
    """
    try:
        _cache().clear()

        def _cleanup_gibberish_db():
            conn = _db().get_connection_raw()
            try:
                _results = repair_database(conn)
            finally:
                _db().return_connection(conn)
            # Flush connection pool so subsequent reads see the repaired data
            try:
                with _db().connection_lock:
                    while _db()._connection_pool:
                        old = _db()._connection_pool.pop()
                        try:
                            old.close()
                        except Exception as e:
                            logger.warning(f"Failed to close DB connection: {e}")
            except Exception as e:
                logger.debug(f"Non-critical: pool cleanup during DB repair: {e}")
            return _results
        results = await asyncio.to_thread(_cleanup_gibberish_db)
        
        logger.warning(
            f"DB Repair: {results['total_fixed']} fixed, "
            f"{results['summary']['deleted']} deleted, "
            f"{results['summary']['encoding_fixed']} encoding, "
            f"{results['summary']['names_recovered']} names recovered, "
            f"{results['summary']['duplicates_merged']} duplicates merged"
        )
        
        return {
            "status": "success",
            "deleted_count": results['summary']['deleted'],
            "reprocessed_count": results['summary']['names_recovered'],
            "encoding_fixed_count": results['summary']['encoding_fixed'],
            "skills_extracted_count": results['summary'].get('skills_extracted', 0),
            "rescored_count": results['summary'].get('rescored', 0),
            "total_fixed": results['total_fixed'],
            "details": results['summary'],
            "deleted": results['deleted'][:20],
            "encoding_fixed": results['encoding_fixed'][:20],
            "names_recovered": results['names_recovered'][:20],
            "skills_extracted": results.get('skills_extracted', [])[:20],
            "rescored": results.get('rescored', [])[:20],
            "duplicates_merged": results['duplicates_merged'][:10],
            "needs_rescore": len(results['needs_rescore']),
        }
    except Exception as e:
        logger.error(f"DB Repair failed: {e}")
        raise HTTPException(500, "Database repair failed. Check server logs for details.")




@router.get("/api/admin/database-audit")
async def database_audit_report(current_user: dict = Depends(require_admin)):
    """
    Full database health audit — returns detailed issue report without modifying data.
    Shows gibberish profiles, encoding issues, empty fields, score distribution, etc.
    """
    try:
        def _audit_db():
            conn = _db().get_connection_raw()
            try:
                return audit_database(conn)
            finally:
                _db().return_connection(conn)
        report = await asyncio.to_thread(_audit_db)
        return {"status": "success", **report}
    except Exception as e:
        logger.error(f"Audit failed: {e}")
        raise HTTPException(500, "Database audit failed. Check server logs for details.")




@router.post("/api/admin/database-repair-full")
async def full_database_repair(current_user: dict = Depends(require_admin)):
    """
    Full repair pipeline: cleanup -> fix encoding -> recover names -> deduplicate -> re-score.
    This is the nuclear option — fixes everything in one pass.
    """
    try:
        _cache().clear()
        
        # Phase 1: Run comprehensive repair (delete gibberish, fix encoding, etc.)
        def _phase1_repair():
            conn = _db().get_connection_raw()
            try:
                return repair_database(conn)
            finally:
                _db().return_connection(conn)
        repair_results = await asyncio.to_thread(_phase1_repair)
        
        # Phase 2: Re-score all candidates that need it (0, NULL, or 50 default)
        rescore_count = 0
        rescore_errors = 0
        try:
            def _fetch_rescore_candidates():
                with _db().get_connection() as conn2:
                    cursor = conn2.cursor()
                    cursor.execute("""
                        SELECT id, email, name, skills, summary, education, work_history, 
                               resume_text, experience
                        FROM candidates 
                        WHERE is_active = 1 AND (match_score = 0 OR match_score IS NULL OR match_score <= 35)
                    """)
                    return cursor.fetchall()
            rows = await asyncio.to_thread(_fetch_rescore_candidates)
            
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
                        new_score = 40
                        new_category = 'General'
                    else:
                        try:
                            # _ai() returns best available: Ollama -> Gemini -> local
                            _rescore_ai = _ai()
                            analysis_result = await asyncio.wait_for(
                                _rescore_ai.analyze_candidate(combined_text),
                                timeout=_deps().AI_ANALYSIS_TIMEOUT
                            )
                            new_score = analysis_result.get('quality_score') or analysis_result.get('match_score')
                            try:
                                new_score = int(float(new_score)) if new_score else 0
                            except (ValueError, TypeError):
                                new_score = 0
                            if new_score <= 0:
                                new_score = min(90, max(15, 25 + min(30, len(skills) * 3) + min(25, exp_years * 3)))
                            new_category = analysis_result.get('job_category', 'General')
                        except Exception as ae:
                            logger.warning(f"Re-score error for {name}: {type(ae).__name__}: {str(ae)[:200]}")
                            new_score = min(90, max(15, 25 + min(30, len(skills) * 3) + min(25, exp_years * 3)))
                            new_category = 'General'
                    
                    def _update_rescore(cid, new_score, new_category):
                        with _db().get_connection() as uc:
                            ucur = uc.cursor()
                            ucur.execute("""
                                UPDATE candidates SET match_score = ?, job_category = ?, last_updated = ? WHERE id = ?
                            """, (new_score, new_category, datetime.now().isoformat(), cid))
                            uc.commit()
                    await asyncio.to_thread(_update_rescore, cid, new_score, new_category)
                    rescore_count += 1
                except Exception as rescore_err:
                    logger.warning(f"Re-score error for {name}: {type(rescore_err).__name__}: {str(rescore_err)[:200]}")
                    rescore_errors += 1
        except Exception as se:
            logger.warning(f"Re-score phase error: {se}")
        
        # Phase 3: Post-repair audit
        def _phase3_audit():
            conn3 = _db().get_connection_raw()
            try:
                return quick_health_check(conn3)
            finally:
                _db().return_connection(conn3)
        post_audit = await asyncio.to_thread(_phase3_audit)
        
        # Phase 4: Backup repaired DB to GCS
        try:
            await asyncio.to_thread(backup_db_to_gcs)
            logger.info("DB Repair: backed up repaired database to GCS")
        except Exception as bke:
            logger.warning(f"DB Repair: GCS backup failed: {bke}")
        
        return {
            "status": "success",
            "repair": repair_results['summary'],
            "total_fixed": repair_results['total_fixed'],
            "rescored": rescore_count,
            "rescore_errors": rescore_errors,
            "post_repair_health": post_audit,
            "message": (
                f"Repaired {repair_results['total_fixed']} profiles, "
                f"rescored {rescore_count}, "
                f"{post_audit['issue_count']} issues remaining"
            ),
        }
    except Exception as e:
        logger.error(f"Full repair failed: {e}")
        raise HTTPException(500, "Full database repair failed. Check server logs for details.")




@router.post("/api/admin/relookup-from-email")
async def relookup_garbled_from_email(current_user: dict = Depends(require_admin)):
    """
    For candidates with garbled/empty data, search the original emails by sender address
    via Microsoft Graph and re-extract candidate info from the email body + attachments.
    This recovers data that was lost during initial parsing.
    """
    try:
        _cache().clear()
        
        # Find candidates that need re-lookup: bad names, no skills, empty summary
        def _fetch_garbled_candidates():
            conn = _db().get_connection_raw()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, email, name, skills, summary, resume_text, match_score
                    FROM candidates
                    WHERE (is_active = 1 OR is_active IS NULL)
                    AND (
                        name = '' OR name = 'Unknown' OR name IS NULL
                        OR name LIKE '%Ã%' OR name LIKE '%â€%'
                        OR (skills IS NULL OR skills = '[]' OR skills = '')
                        OR (summary IS NULL OR summary = '')
                        OR match_score = 0 OR match_score IS NULL
                    )
                """)
                rows = cursor.fetchall()
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, r)) for r in rows]
            finally:
                _db().return_connection(conn)
        candidates_to_fix = await asyncio.to_thread(_fetch_garbled_candidates)
        
        if not candidates_to_fix:
            return {"status": "success", "message": "No candidates need re-lookup", "fixed": 0}
        
        logger.info(f"Re-lookup: {len(candidates_to_fix)} candidates need email re-lookup")
        
        # Get OAuth token for email access
        primary_email = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL') or _settings.email_address or ''
        token_storage = get_token_storage()
        token_data = token_storage.get_token(primary_email)
        
        if not token_data:
            return {
                "status": "partial",
                "message": "No OAuth2 token — cannot search emails. Authenticate first.",
                "candidates_needing_fix": len(candidates_to_fix)
            }
        
        # Initialize Graph service
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=primary_email)
        graph_service.access_token = token_data['access_token']
        expires_str = (token_data.get('expires_at') or '').replace('Z', '+00:00')
        graph_service.token_expiry = datetime.fromisoformat(expires_str) if expires_str else None
        
        fixed_count = 0
        errors = 0
        fixed_details = []
        
        for cand in candidates_to_fix[:50]:  # Limit to 50 per call to avoid timeouts
            try:
                sender_email = cand['email']
                if not sender_email or '@' not in sender_email:
                    continue
                
                # Search for emails from this sender
                filter_q = f"from/emailAddress/address eq '{sender_email}'"
                result = await graph_service.get_messages(
                    folder='inbox', filter_query=filter_q, top=5
                )
                
                if result['status'] != 'success' or not result.get('messages'):
                    continue
                
                # Process the most recent email from this sender
                for msg in result['messages'][:3]:
                    sender = msg.get('from', {}).get('emailAddress', {})
                    sender_name = sender.get('name', '')
                    subject = msg.get('subject', '')
                    body = msg.get('body', {}).get('content', '')
                    
                    # Get attachments
                    attachments = []
                    if msg.get('hasAttachments'):
                        try:
                            attach_result = await graph_service.get_message_with_attachments(msg['id'])
                            if attach_result['status'] == 'success':
                                attachments = attach_result['attachments']
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to get attachments: {e}")
                    
                    received_dt = msg.get('receivedDateTime')
                    received_date = datetime.now()
                    if received_dt:
                        try:
                            received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse receivedDateTime: {e}")
                    
                    email_data = {
                        'subject': subject,
                        'sender_email': sender_email,
                        'sender_name': sender_name,
                        'body': body,
                        'attachments': attachments,
                        'received_date': received_date
                    }
                    
                    # Re-extract candidate from email
                    new_candidate = await _scraper().extract_candidate_from_email(email_data)
                    if not new_candidate or not new_candidate.get('email'):
                        continue
                    
                    # Save resume file if extracted from attachment
                    resume_file = new_candidate.pop('resume_file_data', None)
                    resume_filename = new_candidate.pop('resume_filename', None)
                    if resume_file and resume_filename:
                        try:
                            ct = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                            await asyncio.to_thread(_db().save_resume, cand['id'], resume_filename, resume_file, ct)
                        except Exception as e:
                            logger.warning(f"Failed to save resume for {cand.get('id', 'unknown')}: {e}")
                    
                    # Merge improved data into existing record
                    updates = {}
                    old_name = cand.get('name') or ''
                    new_name = new_candidate.get('name') or ''
                    if new_name and len(new_name) > len(old_name) and new_name.lower() not in ('unknown', 'n/a'):
                        updates['name'] = new_name
                    
                    new_skills = new_candidate.get('skills') or []
                    old_skills = json.loads(cand.get('skills') or '[]') if isinstance(cand.get('skills'), str) else (cand.get('skills') or [])
                    if len(new_skills) > len(old_skills):
                        updates['skills'] = json.dumps(new_skills)
                    
                    new_summary = new_candidate.get('summary') or ''
                    old_summary = cand.get('summary') or ''
                    if len(new_summary) > len(old_summary):
                        updates['summary'] = new_summary
                    
                    new_resume = new_candidate.get('resume_text') or ''
                    old_resume = cand.get('resume_text') or ''
                    if len(new_resume) > len(old_resume):
                        updates['resume_text'] = new_resume
                    
                    if new_candidate.get('phone') and not cand.get('phone'):
                        updates['phone'] = new_candidate['phone']
                    if new_candidate.get('location') and not cand.get('location'):
                        updates['location'] = new_candidate['location']
                    if new_candidate.get('experience') and not cand.get('experience'):
                        updates['experience'] = new_candidate['experience']
                    
                    if updates:
                        updates['last_updated'] = datetime.now().isoformat()
                        _RELOOKUP_ALLOWED_COLS = {'name', 'email', 'skills', 'summary', 'resume_text', 'phone', 'location', 'experience', 'last_updated'}
                        def _update_relookup_candidate(updates_dict, cand_id):
                            safe_updates = {k: v for k, v in updates_dict.items() if k in _RELOOKUP_ALLOWED_COLS}
                            if not safe_updates:
                                return
                            with _db().get_connection() as uc:
                                ucur = uc.cursor()
                                set_parts = [f"{k} = ?" for k in safe_updates]
                                vals = list(safe_updates.values()) + [cand_id]
                                ucur.execute(
                                    f"UPDATE candidates SET {', '.join(set_parts)} WHERE id = ?",
                                    vals
                                )
                                uc.commit()
                        await asyncio.to_thread(_update_relookup_candidate, updates, cand['id'])
                        fixed_count += 1
                        fixed_details.append({
                            'id': cand['id'],
                            'email': sender_email,
                            'old_name': old_name[:30],
                            'new_name': updates.get('name', old_name)[:30],
                            'fields_updated': list(updates.keys()),
                        })
                        break  # Found good data, move to next candidate
                
            except Exception as e:
                errors += 1
                logger.warning(f"Re-lookup error for {cand.get('email')}: {str(e)[:60]}")
        
        return {
            "status": "success",
            "candidates_checked": len(candidates_to_fix),
            "fixed": fixed_count,
            "errors": errors,
            "fixed_details": fixed_details[:20],
            "message": f"Re-looked up {len(candidates_to_fix)} candidates, fixed {fixed_count}",
        }
    except Exception as e:
        logger.error(f"Re-lookup failed: {e}")
        raise HTTPException(500, "Re-lookup failed. Check server logs for details.")




@router.patch("/api/admin/candidates/{candidate_id}")
async def admin_update_candidate(candidate_id: str, request: Request, current_user: dict = Depends(require_admin)):
    """Admin endpoint to directly update candidate fields (name, email, summary, etc.)."""
    updates = await request.json()
    allowed_fields = {'name', 'email', 'summary', 'resume_text', 'status', 'match_score', 'skills'}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}
    if not filtered:
        raise HTTPException(400, f"No valid fields. Allowed: {allowed_fields}")
    conn = None
    try:
        def _admin_update_db(filtered_fields, candidate_id_val):
            conn = _db().get_connection_raw()
            try:
                cursor = conn.cursor()
                set_parts = [f"{k} = ?" for k in filtered_fields]
                set_parts.append("last_updated = ?")
                vals = list(filtered_fields.values()) + [datetime.now().isoformat(), candidate_id_val]
                cursor.execute(f"UPDATE candidates SET {', '.join(set_parts)} WHERE id = ?", vals)
                if cursor.rowcount == 0:
                    return None
                conn.commit()
                return True
            finally:
                _db().return_connection(conn)
        result = await asyncio.to_thread(_admin_update_db, filtered, candidate_id)
        if result is None:
            raise HTTPException(404, "Candidate not found")
        return {"status": "success", "updated_fields": list(filtered.keys()), "candidate_id": candidate_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin update candidate error: {e}")
        raise HTTPException(500, "Update failed. Check server logs for details.")




@router.post("/api/admin/fix-corrupted-resume-text")
async def fix_corrupted_resume_text(current_user: dict = Depends(require_admin)):
    """
    Scan ALL candidates for corrupted resume_text (spaced characters, gibberish).
    For each corrupted entry that has a stored resume file, re-extract the text
    using the improved parser. For entries without a resume file, attempt in-place
    text repair (collapse spaced chars).
    """
    try:
        _cache().clear()
        results = {
            "scanned": 0,
            "corrupted_found": 0,
            "re_extracted": 0,
            "text_repaired": 0,
            "no_resume_file": 0,
            "errors": 0,
            "details": []
        }

        # Fetch all active candidates (with or without resume_text — we check names too)
        def _fetch_all():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, email, resume_text
                    FROM candidates
                    WHERE is_active = 1
                """)
                return cursor.fetchall()

        rows = await asyncio.to_thread(_fetch_all)
        results["scanned"] = len(rows)

        for row in rows:
            cid, name, email, resume_text = row
            try:
                # Check name for spaced-char corruption
                name_corrupted = bool(name and is_spaced_text(name, threshold=0.15, min_length=5))

                # Skip if no resume_text and name is fine
                if (not resume_text or len((resume_text or '').strip()) < 20) and not name_corrupted:
                    continue

                # Check if text is corrupted (spaced chars or very low quality)
                quality = text_quality_score(resume_text) if resume_text else 0.0
                is_spaced = is_spaced_text(resume_text) if resume_text else False

                if not is_spaced and quality >= 0.3 and not name_corrupted:
                    continue  # Text and name look fine

                results["corrupted_found"] += 1
                detail = {"id": cid, "name": name, "quality_before": round(quality, 3)}

                # Try to get the stored resume file and re-extract
                resume_data = await asyncio.to_thread(_db().get_resume, cid)

                if resume_data and resume_data.get("file_data"):
                    try:
                        new_text = await _resume_parser().extract_text(
                            resume_data["file_data"],
                            resume_data.get("filename", "resume.pdf")
                        )
                        new_quality = text_quality_score(new_text)
                        detail["quality_after"] = round(new_quality, 3)
                        detail["method"] = "re-extract"

                        if new_quality > quality and len(new_text.strip()) > 20:
                            # Update the candidate's resume_text
                            def _update_text(c_id, txt):
                                with _db().get_connection() as conn:
                                    cursor = conn.cursor()
                                    cursor.execute(
                                        "UPDATE candidates SET resume_text = ?, last_updated = ? WHERE id = ?",
                                        (txt[:10000], datetime.now().isoformat(), c_id)
                                    )
                                    conn.commit()
                            await asyncio.to_thread(_update_text, cid, new_text)
                            results["re_extracted"] += 1
                            detail["status"] = "re-extracted"
                        else:
                            detail["status"] = "re-extract-no-improvement"
                    except Exception as ex:
                        detail["status"] = f"re-extract-error: {str(ex)[:80]}"
                        results["errors"] += 1
                else:
                    # No resume file — try in-place text repair
                    results["no_resume_file"] += 1
                    if is_spaced:
                        repaired = collapse_spaced_chars(resume_text)
                        repaired_quality = text_quality_score(repaired)
                        if repaired_quality >= quality:  # accept equal or better
                            def _update_repaired(c_id, txt, fixed_name):
                                with _db().get_connection() as conn:
                                    cursor = conn.cursor()
                                    if fixed_name:
                                        cursor.execute(
                                            "UPDATE candidates SET resume_text = ?, name = ?, last_updated = ? WHERE id = ?",
                                            (txt[:10000], fixed_name, datetime.now().isoformat(), c_id)
                                        )
                                    else:
                                        cursor.execute(
                                            "UPDATE candidates SET resume_text = ?, last_updated = ? WHERE id = ?",
                                            (txt[:10000], datetime.now().isoformat(), c_id)
                                        )
                                    conn.commit()
                            # Also fix the name if it's spaced
                            fixed_name = None
                            if name and is_spaced_text(name, threshold=0.15, min_length=5):
                                fixed_name = collapse_spaced_chars(name).strip()
                                detail["name_fixed"] = fixed_name
                            await asyncio.to_thread(_update_repaired, cid, repaired, fixed_name)
                            results["text_repaired"] += 1
                            detail["method"] = "collapse-spaced"
                            detail["quality_after"] = round(repaired_quality, 3)
                            detail["status"] = "text-repaired"
                        else:
                            detail["status"] = "repair-no-improvement"
                    else:
                        detail["status"] = "no-resume-file"

                # Fix name even if resume text wasn't repaired
                if name and is_spaced_text(name, threshold=0.15, min_length=5) and detail.get("status") != "text-repaired":
                    fixed_name = collapse_spaced_chars(name).strip()
                    if fixed_name and fixed_name != name:
                        def _fix_name(c_id, new_name):
                            with _db().get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute(
                                    "UPDATE candidates SET name = ?, last_updated = ? WHERE id = ?",
                                    (new_name, datetime.now().isoformat(), c_id)
                                )
                                conn.commit()
                        await asyncio.to_thread(_fix_name, cid, fixed_name)
                        detail["name_fixed"] = fixed_name

                results["details"].append(detail)

            except Exception as e:
                results["errors"] += 1
                logger.warning(f"Fix resume text error for {cid}: {e}")

        # Only include first 50 details to avoid huge response
        results["details"] = results["details"][:50]

        return {
            "status": "success",
            "message": (
                f"Scanned {results['scanned']} candidates, "
                f"found {results['corrupted_found']} corrupted, "
                f"re-extracted {results['re_extracted']}, "
                f"text-repaired {results['text_repaired']}, "
                f"{results['errors']} errors"
            ),
            **results
        }
    except Exception as e:
        logger.error(f"Fix corrupted resume text error: {e}")
        raise HTTPException(500, "Fix corrupted resume text failed. Check server logs for details.")



