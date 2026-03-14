"""Route module: upload. Auto-extracted from main_legacy.py."""
import os
import json
import asyncio
import logging
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

router = APIRouter(tags=["upload"])


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


@router.post("/api/resumes/upload")
async def upload_resume(file: UploadFile = File(...), current_user: dict = Depends(require_auth)):
    """
    Upload a single resume file (PDF or DOCX).
    Parses the resume, runs AI analysis, and saves the candidate.
    """
    try:
        filename = file.filename or "unknown.pdf"
        # Sanitize filename: strip path components and restrict to safe characters
        filename = os.path.basename(filename)
        filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
        if len(filename) > 255:
            filename = filename[:255]
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in ('pdf', 'docx'):
            raise HTTPException(400, "Only PDF and DOCX files are supported.")

        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(400, "File too large. Max 10MB.")

        # Parse resume
        parsed = await _resume_parser().parse_resume(content, filename)
        if not parsed.get('email'):
            # Generate a placeholder email from filename
            import hashlib
            file_hash = hashlib.sha256(content[:1024]).hexdigest()[:8]
            clean_name = re.sub(r'[^a-zA-Z]', '', parsed.get('name', ''))[:20] or 'candidate'
            parsed['email'] = f"{clean_name.lower()}.{file_hash}@uploaded.local"

        candidate_id = f"upload_{hashlib.sha256(parsed['email'].encode()).hexdigest()[:16]}_{int(datetime.now().timestamp())}"

        # AI analysis
        resume_text = parsed.get('raw_text', '') or parsed.get('summary', '')
        ai_score = None
        job_category = 'General'
        job_subcategory = ''
        summary = parsed.get('summary', '')

        if resume_text.strip():
            try:
                ai_analysis = await asyncio.wait_for(
                    _ai().analyze_candidate(resume_text),
                    timeout=_deps().AI_ANALYSIS_TIMEOUT
                )
                if ai_analysis:
                    ai_score = ai_analysis.get('quality_score')
                    job_category = ai_analysis.get('job_category', 'General')
                    job_subcategory = ai_analysis.get('job_subcategory', '')
                    summary = ai_analysis.get('summary', summary)
                    # Merge AI skills with parsed skills instead of overwriting
                    if ai_analysis.get('skills'):
                        existing_skills = set(s.lower() for s in parsed.get('skills', []))
                        merged_skills = list(parsed.get('skills', []))
                        for skill in ai_analysis['skills']:
                            if skill.lower() not in existing_skills:
                                merged_skills.append(skill)
                                existing_skills.add(skill.lower())
                        parsed['skills'] = merged_skills
                    if ai_analysis.get('experience'):
                        parsed['experience'] = ai_analysis['experience']
                    # Prefer AI-extracted contact info if parser missed it
                    if not parsed.get('phone') and ai_analysis.get('phone'):
                        parsed['phone'] = ai_analysis['phone']
                    if not parsed.get('location') and ai_analysis.get('location'):
                        parsed['location'] = ai_analysis['location']
                    if not parsed.get('name', '').strip() or parsed.get('name') == 'Unknown':
                        if ai_analysis.get('name'):
                            parsed['name'] = ai_analysis['name']
                    if ai_analysis.get('certifications'):
                        parsed['certifications'] = ai_analysis['certifications']
                    if ai_analysis.get('languages'):
                        parsed['languages'] = ai_analysis['languages']
                    if ai_analysis.get('linkedin'):
                        parsed['linkedin'] = ai_analysis['linkedin']
            except Exception as ai_err:
                logger.warning(f"AI analysis failed for upload: {str(ai_err)[:100]}")

        # Calculate fallback score if AI didn't return one
        if ai_score is None:
            skills_count = len(parsed.get('skills', []))
            exp = parsed.get('experience', 0) or 0
            if isinstance(exp, str):
                nums = re.findall(r'\d+', str(exp))
                exp = int(nums[0]) if nums else 0
            has_edu = bool(parsed.get('education'))
            ai_score = 25 + min(30, skills_count * 3) + min(25, exp * 3) + (10 if has_edu else 0)
            ai_score = min(90, max(15, ai_score))
            logger.info(f"📊 Calculated fallback score for upload: {ai_score} (skills={skills_count}, exp={exp})")

        # Infer category from skills if still General
        if job_category == 'General' and parsed.get('skills'):
            email_data = {'subject': filename, 'body': resume_text[:500]}
            job_category = await _scraper().infer_job_category(email_data, parsed)

        # Determine status
        status = 'Strong' if ai_score >= 70 else ('Partial' if ai_score >= 40 else 'Reject')

        candidate = {
            'id': candidate_id,
            'email': parsed['email'],
            'name': parsed.get('name', 'Unknown'),
            'phone': parsed.get('phone', ''),
            'location': parsed.get('location', ''),
            'skills': parsed.get('skills', []),
            'experience': parsed.get('experience', 0),
            'education': json.dumps(parsed.get('education', [])) if isinstance(parsed.get('education'), list) else parsed.get('education', ''),
            'summary': summary,
            'workHistory': parsed.get('work_history', []),
            'linkedin': parsed.get('linkedin', ''),
            'status': status,
            'matchScore': round(ai_score, 1),
            'job_category': job_category,
            'job_subcategory': job_subcategory or parsed.get('job_subcategory', ''),
            'appliedDate': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'raw_email_subject': f"Resume Upload: {filename}",
            'certifications': parsed.get('certifications', []),
            'languages': parsed.get('languages', []),
            'resume_text': resume_text[:10000] if resume_text else '',
        }

        # Check if candidate with same email exists
        existing = await asyncio.to_thread(_db().get_candidate_by_email, parsed['email'])
        if existing:
            candidate['id'] = existing['id']
            await asyncio.to_thread(_db().update_candidate, candidate)
            logger.info(f"📝 Updated candidate from upload: {candidate['name']}")
        else:
            await asyncio.to_thread(_db().insert_candidate, candidate)
            logger.info(f"✨ New candidate from upload: {candidate['name']} ({candidate['email']}) - Score: {ai_score}")

        # Save detailed AI analysis if available
        if resume_text.strip():
            try:
                await asyncio.to_thread(_db().save_ai_analysis, candidate['id'], {
                    'score': round(ai_score, 1),
                    'job_category': job_category,
                    'summary': summary,
                    'skills': parsed.get('skills', []),
                    'experience': parsed.get('experience', 0),
                    'education': parsed.get('education', []),
                    'certifications': parsed.get('certifications', []),
                    'languages': parsed.get('languages', []),
                    'analyzed_at': datetime.now().isoformat(),
                })
            except Exception as e:
                logger.warning(f"Failed to save AI analysis: {e}")

        # Save resume file for future re-analysis
        try:
            content_type = 'application/pdf' if filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' if filename.lower().endswith('.docx') else 'application/octet-stream'
            await asyncio.to_thread(_db().save_resume, candidate['id'], filename, content, content_type)
        except Exception as e:
            logger.warning(f"Failed to save resume file: {e}")

        return {
            "status": "success",
            "candidate": {
                "id": candidate['id'],
                "name": candidate['name'],
                "email": candidate['email'],
                "matchScore": candidate['matchScore'],
                "jobCategory": job_category,
                "status": status,
                "skills": candidate['skills'],
            },
            "filename": filename,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume upload error: {str(e)}")
        raise HTTPException(500, "Error processing resume")




@router.post("/api/resumes/upload-multiple")
async def upload_multiple_resumes(files: List[UploadFile] = File(...), current_user: dict = Depends(require_auth)):
    """
    Upload multiple resume files at once.
    Returns results for each file.
    """
    if len(files) > 50:
        raise HTTPException(400, "Too many files. Maximum 50 per upload.")
    results = []
    for file in files:
        try:
            raw_filename = file.filename or "unknown.pdf"
            filename = os.path.basename(raw_filename)
            filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename) or "upload.pdf"
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            if ext not in ('pdf', 'docx'):
                results.append({"filename": filename, "status": "error", "message": "Unsupported format. Only PDF/DOCX."})
                continue

            content = await file.read()
            if len(content) > 10 * 1024 * 1024:
                results.append({"filename": filename, "status": "error", "message": "File too large (max 10MB)."})
                continue

            parsed = await _resume_parser().parse_resume(content, filename)
            if not parsed.get('email'):
                import hashlib
                file_hash = hashlib.sha256(content[:1024]).hexdigest()[:8]
                clean_name = re.sub(r'[^a-zA-Z]', '', parsed.get('name', ''))[:20] or 'candidate'
                parsed['email'] = f"{clean_name.lower()}.{file_hash}@uploaded.local"

            candidate_id = f"upload_{hashlib.sha256(parsed['email'].encode()).hexdigest()[:16]}_{int(datetime.now().timestamp())}"

            resume_text = parsed.get('raw_text', '') or parsed.get('summary', '')
            ai_score = None
            job_category = 'General'
            job_subcategory = ''
            summary = parsed.get('summary', '')

            if resume_text.strip():
                try:
                    ai_analysis = await asyncio.wait_for(
                        _ai().analyze_candidate(resume_text),
                        timeout=_deps().AI_ANALYSIS_TIMEOUT
                    )
                    if ai_analysis:
                        ai_score = ai_analysis.get('quality_score')
                        job_category = ai_analysis.get('job_category', 'General')
                        job_subcategory = ai_analysis.get('job_subcategory', '')
                        summary = ai_analysis.get('summary', summary)
                        if ai_analysis.get('skills'):
                            existing_skills = set(s.lower() for s in parsed.get('skills', []))
                            merged_skills = list(parsed.get('skills', []))
                            for skill in ai_analysis['skills']:
                                if skill.lower() not in existing_skills:
                                    merged_skills.append(skill)
                                    existing_skills.add(skill.lower())
                            parsed['skills'] = merged_skills
                        if ai_analysis.get('experience'):
                            parsed['experience'] = ai_analysis['experience']
                        # Prefer AI-extracted contact info if parser missed it
                        if not parsed.get('phone') and ai_analysis.get('phone'):
                            parsed['phone'] = ai_analysis['phone']
                        if not parsed.get('location') and ai_analysis.get('location'):
                            parsed['location'] = ai_analysis['location']
                        if not parsed.get('name', '').strip() or parsed.get('name') == 'Unknown':
                            if ai_analysis.get('name'):
                                parsed['name'] = ai_analysis['name']
                        if ai_analysis.get('certifications'):
                            parsed['certifications'] = ai_analysis['certifications']
                        if ai_analysis.get('languages'):
                            parsed['languages'] = ai_analysis['languages']
                        if ai_analysis.get('linkedin'):
                            parsed['linkedin'] = ai_analysis['linkedin']
                except Exception as e:
                    logger.warning(f"AI analysis failed for multi-upload {filename}: {e}")

            # Calculate fallback score if AI didn't return one
            if ai_score is None:
                skills_count = len(parsed.get('skills', []))
                exp = parsed.get('experience', 0) or 0
                if isinstance(exp, str):
                    nums = re.findall(r'\d+', str(exp))
                    exp = int(nums[0]) if nums else 0
                has_edu = bool(parsed.get('education'))
                ai_score = 25 + min(30, skills_count * 3) + min(25, exp * 3) + (10 if has_edu else 0)
                ai_score = min(90, max(15, ai_score))

            if job_category == 'General' and parsed.get('skills'):
                email_data = {'subject': filename, 'body': resume_text[:500]}
                job_category = await _scraper().infer_job_category(email_data, parsed)

            status = 'Strong' if ai_score >= 70 else ('Partial' if ai_score >= 40 else 'Reject')

            candidate = {
                'id': candidate_id,
                'email': parsed['email'],
                'name': parsed.get('name', 'Unknown'),
                'phone': parsed.get('phone', ''),
                'location': parsed.get('location', ''),
                'skills': parsed.get('skills', []),
                'experience': parsed.get('experience', 0),
                'education': json.dumps(parsed.get('education', [])) if isinstance(parsed.get('education'), list) else parsed.get('education', ''),
                'summary': summary,
                'workHistory': parsed.get('work_history', []),
                'linkedin': parsed.get('linkedin', ''),
                'status': status,
                'matchScore': round(ai_score, 1),
                'job_category': job_category,
                'job_subcategory': job_subcategory or parsed.get('job_subcategory', ''),
                'appliedDate': datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                'raw_email_subject': f"Resume Upload: {filename}",
                'certifications': parsed.get('certifications', []),
                'languages': parsed.get('languages', []),
                'resume_text': resume_text[:10000] if resume_text else '',
            }

            existing = await asyncio.to_thread(_db().get_candidate_by_email, parsed['email'])
            if existing:
                candidate['id'] = existing['id']
                await asyncio.to_thread(_db().update_candidate, candidate)
            else:
                await asyncio.to_thread(_db().insert_candidate, candidate)

            # Save AI analysis
            try:
                await asyncio.to_thread(_db().save_ai_analysis, candidate['id'], {
                    'score': round(ai_score, 1),
                    'job_category': job_category,
                    'summary': summary,
                    'skills': parsed.get('skills', []),
                    'experience': parsed.get('experience', 0),
                    'education': parsed.get('education', []),
                    'certifications': parsed.get('certifications', []),
                    'languages': parsed.get('languages', []),
                    'analyzed_at': datetime.now().isoformat(),
                })
            except Exception as e:
                logger.warning(f"Failed to save AI analysis for {candidate.get('id', 'unknown')}: {e}")

            # Save resume file
            try:
                content_type = 'application/pdf' if ext == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                await asyncio.to_thread(_db().save_resume, candidate['id'], filename, content, content_type)
            except Exception as e:
                logger.warning(f"Failed to save resume file {filename}: {e}")

            logger.info(f"✨ Processed upload: {candidate['name']} - Score: {ai_score}")

            results.append({
                "filename": filename,
                "status": "success",
                "candidate": {
                    "id": candidate['id'],
                    "name": candidate['name'],
                    "email": candidate['email'],
                    "matchScore": candidate['matchScore'],
                    "jobCategory": job_category,
                    "candidateStatus": status,
                },
            })

        except Exception as e:
            logger.error(f"Error processing {file.filename}: {str(e)}")
            results.append({"filename": file.filename or "unknown", "status": "error", "message": "Processing failed"})

    success_count = sum(1 for r in results if r['status'] == 'success')
    return {
        "status": "completed",
        "total": len(files),
        "success": success_count,
        "failed": len(files) - success_count,
        "results": results,
    }



