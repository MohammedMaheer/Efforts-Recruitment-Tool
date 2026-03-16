"""Route module: ai. Auto-extracted from main_legacy.py."""
import os
import json
import asyncio
import logging
import time
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends, UploadFile, File, Body, Query, Form
from fastapi.responses import Response, JSONResponse, StreamingResponse, RedirectResponse

from core.config import get_settings
from core.dependencies import require_auth, optional_auth, require_admin
from models.schemas import AnalyzeMatchRequest, InterviewQuestionsRequest, SummarizeResumeRequest

logger = logging.getLogger(__name__)
_settings = get_settings()

# Module-level state (replaces app.state and module globals from main_legacy.py)
_chat_rate_limits: dict = {}
_ai_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai_worker")
_analysis_in_progress: dict = {}  # candidate_id -> asyncio.Event
_MAX_CONCURRENT_ANALYSES = 100

router = APIRouter(tags=["ai"])


# ---- Service accessors (lazy imports to avoid circular deps) ----

def _db():
    from api.deps import get_db
    return get_db()

def _ai():
    from api.deps import get_ai
    return get_ai()

def _get_local_ai():
    from api.deps import get_local_ai
    return get_local_ai()

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


# ---- Helper functions ported from main_legacy.py ----

async def _run_candidate_analysis(candidate_id: str, refresh: bool = False):
    """Internal: actually run the LLM analysis for a candidate."""
    try:
        def _get_candidate_for_llm_analysis():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
                row = cursor.fetchone()
            if not row:
                return None
            return _db()._row_to_candidate(row)
        candidate = await asyncio.to_thread(_get_candidate_for_llm_analysis)

        if not candidate:
            raise HTTPException(404, "Candidate not found")

        resume_text = candidate.get('resume_text', '') or ''
        if not resume_text:
            try:
                resume_data = await asyncio.to_thread(_db().get_resume, candidate_id)
                if resume_data and resume_data.get('file_data'):
                    parsed = await _resume_parser().parse_resume(resume_data['file_data'], resume_data['filename'])
                    resume_text = parsed.get('raw_text', '') if parsed else ''
            except Exception as e:
                logger.debug(f"Non-critical: failed to get/parse resume for {candidate_id}: {e}")

        candidate_for_analysis = {
            'name': candidate.get('name', 'Unknown'),
            'email': candidate.get('email', ''),
            'location': candidate.get('location', ''),
            'skills': candidate.get('skills', []),
            'experience': candidate.get('experience', 0),
            'education': candidate.get('education', []),
            'work_history': candidate.get('workHistory', []),
            'summary': candidate.get('summary', ''),
            'matchScore': candidate.get('matchScore', 0),
            'job_category': candidate.get('jobCategory', candidate.get('job_category', 'General')),
            'job_subcategory': candidate.get('jobSubcategory', candidate.get('job_subcategory', '')),
        }

        if resume_text:
            candidate_for_analysis['resume_text'] = resume_text[:4000]

        analysis = None
        # TIER 0 — Gemini (primary)
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                analysis = await asyncio.wait_for(
                    gemini_svc.analyze_candidate_deep(candidate_for_analysis),
                    timeout=45.0
                )
                if analysis:
                    analysis['source'] = 'gemini'
        except asyncio.TimeoutError:
            logger.warning(f"Gemini deep analysis timeout for {candidate_id}")
        except Exception as gemini_err:
            logger.warning(f"Gemini deep analysis error: {gemini_err}")

        if not analysis:
            skills = candidate_for_analysis.get('skills', [])
            exp = candidate_for_analysis.get('experience', 0)
            name = candidate_for_analysis.get('name', 'Unknown')
            match_score = candidate_for_analysis.get('matchScore', candidate_for_analysis.get('match_score', 50))
            location = candidate_for_analysis.get('location', '')

            if match_score >= 90:
                fb_rating, fb_rec, fb_conf = 'A+', 'STRONGLY_RECOMMEND', 95
            elif match_score >= 80:
                fb_rating, fb_rec, fb_conf = 'A', 'STRONGLY_RECOMMEND', 88
            elif match_score >= 70:
                fb_rating, fb_rec, fb_conf = 'A-', 'RECOMMEND', 80
            elif match_score >= 60:
                fb_rating, fb_rec, fb_conf = 'B+', 'RECOMMEND', 72
            elif match_score >= 50:
                fb_rating, fb_rec, fb_conf = 'B', 'CONSIDER', 65
            elif match_score >= 40:
                fb_rating, fb_rec, fb_conf = 'B-', 'CONSIDER', 55
            elif match_score >= 30:
                fb_rating, fb_rec, fb_conf = 'C+', 'REVIEW', 45
            else:
                fb_rating, fb_rec, fb_conf = 'C', 'REVIEW', 35

            fb_pros = []
            if exp > 0:
                fb_pros.append(f'Brings {exp} years of domain experience')
            if len(skills) > 5:
                fb_pros.append(f'Well-rounded skill set with {len(skills)} competencies')
            elif len(skills) > 0:
                fb_pros.append(f'Focused expertise in {", ".join(skills[:3])}')
            if match_score >= 70:
                fb_pros.append('Strong overall match score for the target role')
            fb_pros.append('Profile is complete and in active pipeline')

            fb_cons = []
            if len(skills) < 5:
                fb_cons.append('Limited skills breadth -- expanding technical portfolio recommended')
            if exp < 3:
                fb_cons.append('Early career stage -- may need mentorship and onboarding support')
            if not location or location in ('Not Specified', 'Unknown', ''):
                fb_cons.append('Location not specified -- remote/relocation flexibility should be verified')
            if match_score < 60:
                fb_cons.append('Below-average match score -- verify alignment with role requirements')
            if not fb_cons:
                fb_cons.append('Profile appears strong overall -- detailed AI review recommended for deeper insights')

            analysis = {
                'executive_summary': f'{name} is a professional with {exp} years of experience specializing in {", ".join(skills[:5]) if skills else "their field"}. With a match score of {match_score}%, they {"show strong alignment" if match_score >= 70 else "show moderate alignment" if match_score >= 50 else "may need further evaluation"} for the target role.',
                'technical_assessment': f'The candidate lists {len(skills)} technical skills including {", ".join(skills[:8]) if skills else "unspecified technologies"}.',
                'experience_assessment': f'With {exp} years of professional experience, {name} {"demonstrates significant industry tenure" if exp > 5 else "is building their career foundation"}.',
                'education_assessment': 'Educational credentials are listed in their profile. Verification of qualifications is recommended during the screening process.',
                'pros': fb_pros,
                'cons': fb_cons,
                'career_trajectory': f'Based on {exp} years of experience, the candidate appears to be at a {"senior" if exp > 7 else "mid" if exp > 3 else "junior"}-level career stage.',
                'ideal_roles': [candidate_for_analysis.get('job_category', 'General')],
                'interview_focus_areas': ['Technical depth verification', 'Cultural alignment', 'Career motivation'],
                'hiring_recommendation': fb_rec,
                'hiring_recommendation_rationale': f'Based on a {match_score}% match score with {exp} years of experience and {len(skills)} listed skills.',
                'confidence_score': fb_conf,
                'overall_rating': fb_rating,
                'source': 'fallback',
            }

        await asyncio.to_thread(_db().save_ai_analysis, candidate_id, analysis)
        analysis['from_cache'] = False

        candidate_email_from_ai = analysis.get('candidate_email', '')
        if candidate_email_from_ai and '@' in candidate_email_from_ai:
            current_email = (candidate.get('email', '') or '').lower()
            portal_prefixes = ('cv@', 'jobs@', 'careers@', 'recruitment@', 'noreply@', 'apply@', 'resume@', 'info@', 'admin@', 'hr@')
            if current_email and any(current_email.startswith(p) for p in portal_prefixes):
                try:
                    def _update_email():
                        with _db().get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute("UPDATE candidates SET email = ? WHERE id = ?", (candidate_email_from_ai, candidate_id))
                            conn.commit()
                    await asyncio.to_thread(_update_email)
                    logger.info(f"Updated candidate email: {current_email} -> {candidate_email_from_ai}")
                except Exception as email_err:
                    logger.warning(f"Failed to update candidate email: {email_err}")

        if analysis.get('source') == 'fallback':
            analysis['isFallback'] = True

        return analysis

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI analysis error for {candidate_id}: {e}")
        raise HTTPException(500, "Error generating AI analysis")


def _quick_fallback_analysis(candidate: dict, job_description: dict) -> dict:
    """Quick rule-based fallback when AI is unavailable."""
    candidate_skills = set(s.lower() for s in candidate.get('skills', []))
    required_skills = set(s.lower() for s in job_description.get('required_skills', []))

    matched = candidate_skills & required_skills
    skill_score = (len(matched) / max(len(required_skills), 1)) * 100 if required_skills else 50

    exp = candidate.get('experience', 0)
    if isinstance(exp, str):
        exp = int(''.join(filter(str.isdigit, str(exp))) or '0')
    exp_score = min(100, exp * 15)

    score = int(skill_score * 0.7 + exp_score * 0.3)
    score = max(20, min(95, score))

    missing = list(required_skills - candidate_skills)[:3]

    return {
        "score": score,
        "strengths": [
            f"Matched {len(matched)} of {len(required_skills)} required skills" if required_skills else "Skills review needed",
            f"{exp} years of experience" if exp else "Experience to be verified"
        ],
        "gaps": [f"Missing: {', '.join(missing)}"] if missing else ["No major gaps identified"],
        "recommendation": "Recommended" if score >= 60 else "Consider with reservations",
        "matched_skills": len(matched),
        "total_required": len(required_skills),
        "semantic_used": False
    }


def _determine_primary_engine() -> str:
    """Determine which AI engine is currently primary."""
    gemini_svc = _gemini()
    if gemini_svc and gemini_svc.available:
        return "gemini"
    return "local_ai"


def _determine_model_description() -> str:
    """Dynamic model description based on what's available."""
    parts = []
    gemini_svc = _gemini()
    if gemini_svc and gemini_svc.available:
        parts.append(f"Gemini ({gemini_svc.model_name})")
    parts.extend(["Sentence-Transformers", "SpaCy NER", "Keyword"])
    return "Multi-Tier AI: " + " -> ".join(parts)


def _determine_ai_message() -> str:
    """Generate AI status message based on current configuration."""
    primary = _determine_primary_engine()
    gemini_svc = _gemini()
    if primary == "gemini":
        return f"AI Stack: Gemini {gemini_svc.model_name} (primary) + Local Embeddings + NER"
    return "AI Stack: Sentence-Transformers + SpaCy NER + Keyword (FREE, no LLM)"


def _determine_cost_info() -> str:
    """Generate cost information string."""
    primary = _determine_primary_engine()
    if primary == "gemini":
        return "~$0.01-0.05/day (Gemini 2.5 Flash is very low cost)"
    return "$0 (all local, no API costs)"


def _format_search_results(raw_results: list, candidates: list) -> list:
    """Normalize search results into {candidate, relevance_score, match_reasons} format.
    Deduplicates by candidate ID -- keeps the first (highest-ranked) occurrence."""
    formatted = []
    seen_ids = set()
    for item in raw_results:
        if isinstance(item, dict):
            cand_id = None
            if 'candidate' in item and isinstance(item['candidate'], dict):
                cand_id = str(item['candidate'].get('id', ''))
            elif 'id' in item:
                cand_id = str(item['id'])
            elif 'candidate_id' in item:
                cand_id = str(item['candidate_id'])

            if cand_id and cand_id in seen_ids:
                continue

            if 'candidate' in item and 'relevance_score' in item:
                formatted.append(item)
                if cand_id: seen_ids.add(cand_id)
            elif 'candidate' in item and 'score' in item:
                match_data = item.get('match', {})
                formatted.append({
                    "candidate": item['candidate'],
                    "relevance_score": int(item['score']),
                    "match_reasons": match_data.get('strengths', match_data.get('matched_skills', ["AI matched"])),
                    "matched_skills": match_data.get('matched_skills', []),
                    "missing_skills": match_data.get('missing_skills', []),
                    "recommendation": match_data.get('recommendation', ''),
                })
                if cand_id: seen_ids.add(cand_id)
            elif 'id' in item or 'name' in item:
                score = item.get('score', item.get('match_score', item.get('matchScore', 50)))
                formatted.append({
                    "candidate": item,
                    "relevance_score": score,
                    "match_reasons": item.get('match_reasons', item.get('key_strengths', ["AI matched"]))
                })
                if cand_id: seen_ids.add(cand_id)
            elif 'candidate_id' in item:
                cand = next((c for c in candidates if str(c.get('id')) == str(item['candidate_id'])), None)
                if cand:
                    formatted.append({
                        "candidate": cand,
                        "relevance_score": int(item.get('score', item.get('job_fit_score', 50))),
                        "match_reasons": item.get('match_reasons', item.get('key_strengths', ["AI matched"]))
                    })
                    if cand_id: seen_ids.add(cand_id)
    return formatted


@router.get("/api/ai/candidate/{candidate_id}/analysis")
async def get_candidate_deep_analysis(candidate_id: str, current_user: dict = Depends(require_auth)):
    """
    Get deep AI analysis of a candidate including:
    - Detailed pros and cons
    - Career trajectory analysis
    - Hiring recommendation
    - Interview focus areas
    """
    try:
        # Get candidate from database
        def _get_candidate_for_analysis():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
                row = cursor.fetchone()
            if not row:
                return None
            return _db()._row_to_candidate(row)
        candidate = await asyncio.to_thread(_get_candidate_for_analysis)
        
        if not candidate:
            raise HTTPException(404, "Candidate not found")
        
        # Check cache first
        cache_key = f"deep_analysis_{candidate_id}"
        if cache_key in _cache():
            cached = _cache()[cache_key]
            cached['from_cache'] = True
            return cached

        # TIER 0 — Gemini
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                analysis = await asyncio.wait_for(
                    gemini_svc.analyze_candidate_deep(candidate),
                    timeout=30.0
                )
                if analysis and analysis.get('overall_rating'):
                    result = {
                        "candidate_id": candidate_id,
                        "candidate_name": candidate['name'],
                        **analysis,
                        "ai_powered": True,
                        "source": "gemini"
                    }
                    _cache()[cache_key] = result
                    return result
        except Exception as gemini_err:
            logger.warning(f"Gemini deep analysis failed: {gemini_err}")

        # TIER 1: Basic fallback — No AI
        return {
            "candidate_id": candidate_id,
            "candidate_name": candidate['name'],
            "overall_score": candidate.get('matchScore', 50),
            "pros": [
                f"Has {candidate.get('experience', 0)} years of experience",
                f"Skills include: {', '.join(candidate.get('skills', [])[:5]) or 'Not specified'}",
                "Resume available in database"
            ],
            "cons": [
                "AI analysis unavailable - configure Gemini API key"
            ],
            "hiring_recommendation": {
                "verdict": "Review Needed",
                "confidence": 50,
                "ideal_roles": [],
                "interview_focus_areas": ["Technical skills", "Experience verification"]
            },
            "ai_powered": False,
            "source": "rule_based"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        raise HTTPException(500, "Error analyzing candidate")




@router.post("/api/ai/match-job-file")
async def match_candidates_to_job_file(
    file: UploadFile = File(None),
    job_description: str = Form(None),
    top_n: int = Form(10),
    current_user: dict = Depends(require_auth),
):
    """
    Match candidates from database against a job description supplied as a file (PDF/DOCX/TXT) or text.
    At least one of 'file' or 'job_description' is required.
    Parses the file to extract JD text, then runs AI matching against all DB candidates.
    """
    try:
        jd_text = ""

        # 1. Extract text from uploaded file
        if file and file.filename:
            filename = file.filename
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            if ext not in ('pdf', 'docx', 'doc', 'txt'):
                raise HTTPException(400, "Only PDF, DOCX and TXT files are supported for job descriptions.")
            content = await file.read()
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(400, "File too large. Max 10MB.")

            if ext == 'txt':
                jd_text = content.decode('utf-8', errors='ignore')
            else:
                # Use resume_parser to extract text from PDF/DOCX
                parsed = await _resume_parser().parse_resume(content, filename)
                jd_text = parsed.get('raw_text', '') or parsed.get('summary', '')

        # 2. Use text input as fallback or supplement
        if job_description:
            if jd_text:
                jd_text = jd_text + "\n\n" + job_description
            else:
                jd_text = job_description

        if not jd_text or len(jd_text.strip()) < 30:
            raise HTTPException(400, "Could not extract sufficient text from the job description. Please provide a file or paste the JD text.")

        # Search the ENTIRE database for comprehensive matching
        candidates_list = await asyncio.to_thread(
            _db().get_all_candidates_for_matching, {}
        )

        if not candidates_list:
            return {
                "status": "no_candidates",
                "message": "No candidates in database to match",
                "rankings": [],
                "job_analysis": {},
                "jd_text_length": len(jd_text)
            }

        total_searched = len(candidates_list)

        # TIER 0: Try Gemini (primary AI — always available in production)
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                ranked = await asyncio.wait_for(
                    gemini_svc.rank_candidates_for_job(candidates_list, jd_text, top_n),
                    timeout=_deps().AI_ANALYSIS_TIMEOUT
                )
                if ranked:
                    formatted_rankings = []
                    for i, r in enumerate(ranked):
                        c = r.get('candidate', {})
                        m = r.get('match', {})
                        formatted_rankings.append({
                            'rank': i + 1,
                            'candidate_id': c.get('id', ''),
                            'candidate_name': c.get('name', 'Unknown'),
                            'job_fit_score': round(m.get('match_score', r.get('score', 50)), 1),
                            'overall_fit': m.get('overall_fit', 'Review Needed'),
                            'matched_skills': m.get('matched_skills', []),
                            'missing_skills': m.get('missing_skills', []),
                            'strengths': m.get('strengths', []),
                            'gaps': m.get('gaps', []),
                            'recommendation': m.get('recommendation', 'Review candidate profile'),
                            'match_reasons': m.get('strengths', [])[:3] or [f"Skills: {', '.join(c.get('skills', [])[:5])}"],
                            'interview_questions': m.get('interview_questions', []),
                            'candidate_data': {
                                'id': c.get('id', ''),
                                'name': c.get('name', 'Unknown'),
                                'email': c.get('email', ''),
                                'phone': c.get('phone', ''),
                                'location': c.get('location', ''),
                                'experience': c.get('experience', 0),
                                'matchScore': round(m.get('match_score', r.get('score', 50)), 1),
                                'status': c.get('status', 'New'),
                                'skills': c.get('skills', []),
                                'summary': c.get('summary', ''),
                                'jobCategory': c.get('jobCategory', c.get('job_category', 'General')),
                                'jobSubcategory': c.get('jobSubcategory', c.get('job_subcategory', '')),
                                'education': c.get('education', []),
                                'workHistory': c.get('workHistory', c.get('work_history', [])),
                                'hasResume': bool(c.get('resume_text') or c.get('hasResume')),
                                'appliedDate': c.get('appliedDate', c.get('applied_date', '')),
                                'isShortlisted': c.get('status', '') == 'Shortlisted',
                            },
                        })
                    return {
                        "status": "success",
                        "rankings": formatted_rankings,
                        "ai_powered": True,
                        "source": "gemini",
                        "total_candidates_searched": total_searched,
                        "jd_text_length": len(jd_text)
                    }
        except (asyncio.TimeoutError, Exception) as gemini_err:
            logger.warning(f"Gemini job file matching failed: {gemini_err}")

        # TIER 1: Enhanced keyword matching fallback
        jd_lower = jd_text.lower()
        for c in candidates_list:
            skill_matches = sum(1 for s in c.get('skills', []) if s.lower() in jd_lower)
            exp = c.get('experience', 0) or 0
            base_score = c.get('matchScore', 50) or 50
            c['job_fit_score'] = min(30 + skill_matches * 12 + min(exp, 10) * 2 + base_score * 0.2, 98)

        candidates_list.sort(key=lambda x: x.get('job_fit_score', 0), reverse=True)

        return {
            "status": "basic_match",
            "message": "Keyword-based matching (AI models unavailable)",
            "rankings": [
                {
                    "rank": i + 1,
                    "candidate_id": c['id'],
                    "candidate_name": c.get('name', 'Unknown'),
                    "job_fit_score": round(c.get('job_fit_score', 50), 1),
                    "match_reasons": [f"Skills: {', '.join(c.get('skills', [])[:5])}"],
                    "recommendation": "Strong Fit" if c.get('job_fit_score', 0) >= 70 else "Potential Fit" if c.get('job_fit_score', 0) >= 50 else "Review Needed"
                }
                for i, c in enumerate(candidates_list[:top_n])
            ],
            "job_analysis": {},
            "ai_powered": False,
            "source": "keyword_fallback",
            "total_candidates_searched": total_searched,
            "jd_text_length": len(jd_text)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job file matching error: {e}")
        raise HTTPException(500, "Error matching candidates to job description")




@router.post("/api/ai/match-job")
async def match_candidates_to_job_description(
    job_description: str = Body(..., embed=True),
    top_n: int = Body(10, embed=True),
    min_experience: Optional[int] = Body(None, embed=True),
    current_user: dict = Depends(require_auth)
):
    """
    Match candidates from database against a job description.
    Returns ranked list with AI scores specific to this JD.
    
    Body params:
    - job_description: The full job description text
    - top_n: Number of top candidates to return (default 10)
    - min_experience: Minimum years of experience filter (optional)
    """
    try:
        if len(job_description) > 50000:
            raise HTTPException(400, "Job description too long (max 50,000 characters)")
        if not job_description or len(job_description.strip()) < 50:
            raise HTTPException(400, "Job description must be at least 50 characters")
        
        # Search the ENTIRE database for comprehensive matching
        filters = {}
        if min_experience:
            filters['min_experience'] = min_experience
        
        candidates = await asyncio.to_thread(
            _db().get_all_candidates_for_matching, filters
        )
        
        if not candidates:
            return {
                "status": "no_candidates",
                "message": "No candidates in database to match",
                "rankings": [],
                "job_analysis": {}
            }

        # TIER 0: Try Gemini (primary AI — always available in production)
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                ranked = await asyncio.wait_for(
                    gemini_svc.rank_candidates_for_job(candidates, job_description, top_n),
                    timeout=_deps().AI_ANALYSIS_TIMEOUT
                )
                if ranked:
                    formatted_rankings = []
                    for i, r in enumerate(ranked):
                        c = r.get('candidate', {})
                        m = r.get('match', {})
                        formatted_rankings.append({
                            'rank': i + 1,
                            'candidate_id': c.get('id', ''),
                            'candidate_name': c.get('name', 'Unknown'),
                            'job_fit_score': round(m.get('match_score', r.get('score', 50)), 1),
                            'overall_fit': m.get('overall_fit', 'Review Needed'),
                            'matched_skills': m.get('matched_skills', []),
                            'missing_skills': m.get('missing_skills', []),
                            'strengths': m.get('strengths', []),
                            'gaps': m.get('gaps', []),
                            'recommendation': m.get('recommendation', 'Review candidate profile'),
                            'match_reasons': m.get('strengths', [])[:3] or [f"Skills: {', '.join(c.get('skills', [])[:5])}"],
                            'interview_questions': m.get('interview_questions', []),
                            'candidate_data': {
                                'id': c.get('id', ''),
                                'name': c.get('name', 'Unknown'),
                                'email': c.get('email', ''),
                                'phone': c.get('phone', ''),
                                'location': c.get('location', ''),
                                'experience': c.get('experience', 0),
                                'matchScore': round(m.get('match_score', r.get('score', 50)), 1),
                                'status': c.get('status', 'New'),
                                'skills': c.get('skills', []),
                                'summary': c.get('summary', ''),
                                'jobCategory': c.get('jobCategory', c.get('job_category', 'General')),
                                'jobSubcategory': c.get('jobSubcategory', c.get('job_subcategory', '')),
                                'education': c.get('education', []),
                                'workHistory': c.get('workHistory', c.get('work_history', [])),
                                'hasResume': bool(c.get('resume_text') or c.get('hasResume')),
                                'appliedDate': c.get('appliedDate', c.get('applied_date', '')),
                                'isShortlisted': c.get('status', '') == 'Shortlisted',
                            },
                        })
                    return {
                        "status": "success",
                        "rankings": formatted_rankings,
                        "ai_powered": True,
                        "source": "gemini",
                        "total_candidates_searched": len(candidates),
                    }
        except (asyncio.TimeoutError, Exception) as gemini_err:
            logger.warning(f"Gemini job matching failed: {gemini_err}")

        # TIER 1: Basic keyword matching fallback
        jd_lower = job_description.lower()
        for c in candidates:
            skill_matches = sum(1 for s in c.get('skills', []) if s.lower() in jd_lower)
            c['job_fit_score'] = min(40 + skill_matches * 10, 95)
        
        candidates.sort(key=lambda x: x.get('job_fit_score', 0), reverse=True)
        
        return {
            "status": "basic_match",
            "message": "Basic keyword matching (AI unavailable)",
            "rankings": [
                {
                    "rank": i + 1,
                    "candidate_id": c['id'],
                    "candidate_name": c['name'],
                    "job_fit_score": c.get('job_fit_score', 50),
                    "match_reasons": [f"Skills: {', '.join(c.get('skills', [])[:5])}"],
                    "recommendation": "Review Needed"
                }
                for i, c in enumerate(candidates[:top_n])
            ],
            "job_analysis": {},
            "ai_powered": False,
            "source": "keyword_fallback"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job matching error: {e}")
        raise HTTPException(500, "Error matching candidates")




@router.post("/api/ai/compare-candidates")
async def compare_candidates(
    candidate_ids: List[str] = Body(..., embed=True),
    job_description: Optional[str] = Body(None, embed=True),
    current_user: dict = Depends(require_auth),
):
    """
    Generate AI comparison of multiple candidates.
    Useful for final hiring decisions.
    """
    try:
        if len(candidate_ids) < 2:
            raise HTTPException(400, "Need at least 2 candidates to compare")
        if len(candidate_ids) > 5:
            raise HTTPException(400, "Can compare up to 5 candidates at a time")
        
        # Get candidates from database
        def _get_candidates_for_comparison():
            candidates = []
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                for cid in candidate_ids:
                    cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (cid,))
                    row = cursor.fetchone()
                    if row:
                        candidates.append(_db()._row_to_candidate(row))
            return candidates
        candidates = await asyncio.to_thread(_get_candidates_for_comparison)
        
        if len(candidates) < 2:
            raise HTTPException(404, "Could not find enough candidates to compare")
        
        # TIER 0 — Gemini
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                jd_part = f" against this job: {job_description[:3000]}" if job_description else ""
                cand_summaries = [
                    f"{c.get('name','?')}: {c.get('experience',0)}yr exp, skills: {', '.join(c.get('skills',[])[:5])}"
                    for c in candidates
                ]
                query = f"Compare and rank these candidates{jd_part}. Candidates: {'; '.join(cand_summaries)}"
                gemini_result = await asyncio.wait_for(
                    gemini_svc.chat(query, None, candidates_data=candidates),
                    timeout=30.0
                )
                if gemini_result:
                    response_text = gemini_result.get('response', '') if isinstance(gemini_result, dict) else str(gemini_result)
                    if response_text:
                        return {
                            "comparison_summary": response_text,
                            "candidates": [c.get('name', 'Unknown') for c in candidates],
                            "ai_powered": True,
                            "source": "gemini"
                        }
        except Exception as gemini_err:
            logger.warning(f"Gemini comparison failed: {gemini_err}")

        # TIER 1: Rule-based fallback
        candidates.sort(key=lambda x: x.get('matchScore', 0), reverse=True)
        return {
            "comparison_matrix": [
                {
                    "name": c['name'],
                    "overall_rank": i + 1,
                    "score": c.get('matchScore', 50),
                    "key_strengths": c.get('skills', [])[:3],
                    "key_weaknesses": ["AI analysis unavailable"],
                    "best_for": c.get('jobCategory', 'General'),
                    "risk_level": "unknown"
                }
                for i, c in enumerate(candidates)
            ],
            "head_to_head": {
                "winner": candidates[0]['name'],
                "reasoning": "Highest match score",
                "runner_up": candidates[1]['name'] if len(candidates) > 1 else None
            },
            "recommendation": "Configure Gemini API key for detailed comparison",
            "ai_powered": False,
            "source": "rule_based"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        raise HTTPException(500, "Error comparing candidates")




@router.post("/api/ai/chat")
async def ai_chat(
    message: str = Body(..., embed=True),
    include_candidates: bool = Body(True, embed=True),
    conversation_history: list = Body([], embed=True),
    num_candidates: int = Body(15, embed=True),
    current_user: dict = Depends(require_auth)
):
    """
    Enhanced AI chat with full database search capability.
    2-STAGE APPROACH: Pre-filter candidates by query relevance -> Send subset to AI
    2-TIER FALLBACK: Gemini -> Rule-based
    """
    # ── Input validation ──
    if not message or not message.strip():
        raise HTTPException(400, "Message cannot be empty")
    message = message.strip()
    if len(message) > 2000:
        raise HTTPException(400, "Message too long (max 2000 characters)")
    num_candidates = max(1, min(num_candidates, 50))  # Clamp 1-50
    conversation_history = (conversation_history or [])[-10:]  # Keep last 10 turns max

    # ── Simple rate limiting (10 requests per minute per user) ──
    import time as _time
    user_id = current_user.get("sub", current_user.get("id", "anon"))
    now = _time.time()
    user_hits = _chat_rate_limits.get(user_id, [])
    user_hits = [t for t in user_hits if now - t < 60]  # Keep last 60s
    if len(user_hits) >= 10:
        raise HTTPException(429, "Too many requests. Please wait a moment before sending another message.")
    user_hits.append(now)
    _chat_rate_limits[user_id] = user_hits
    # Periodic cleanup: remove stale users (every ~100 requests)
    if len(_chat_rate_limits) > 100:
        stale = [uid for uid, hits in _chat_rate_limits.items() if not any(now - t < 60 for t in hits)]
        for uid in stale:
            _chat_rate_limits.pop(uid, None)

    try:
        candidates_data = None
        context = None
        
        if include_candidates:
            stats = await asyncio.to_thread(_db().get_statistics)
            # Fetch ALL candidates so the Gemini pre-filter can search the ENTIRE database.
            # The pre-filter scores every candidate by query relevance (Python-side, no AI cost)
            # and only sends the top 150 most relevant to the Gemini prompt.
            # Token cost stays the same (~$0.001/request) regardless of DB size.
            candidates = await asyncio.to_thread(
                _db().get_candidates_for_ai, {}, 5000
            )
            candidates_data = candidates
            context = {
                'totalCandidates': stats.get('total_candidates', 0),
                'avgMatchScore': stats.get('avg_score', 0),
                'strongMatches': stats.get('strong_matches', 0),
                'recentCount': stats.get('recent_count', 0),
                'categories': stats.get('categories', {}),
            }
        
        # TIER 0: Try Gemini (cost-effective, always available in production)
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                gemini_result = await asyncio.wait_for(
                    gemini_svc.chat(message, context, conversation_history=conversation_history, candidates_data=candidates_data, return_candidates=True, num_candidates=num_candidates),
                    timeout=_deps().AI_TIMEOUT
                )
                if gemini_result:
                    # gemini_result is a dict with 'response' and 'candidates_lookup'
                    if isinstance(gemini_result, dict):
                        cands = gemini_result.get('candidates_lookup', [])
                        # Save to search history (fire-and-forget)
                        try:
                            import uuid as _uuid_mod
                            top3 = [{"name": c.get("name",""), "score": c.get("matchScore", 0), "id": c.get("id","")} for c in cands[:3]]
                            await asyncio.to_thread(
                                _db().save_search,
                                str(_uuid_mod.uuid4())[:12], message,
                                (cands[0].get("jobCategory","") if cands else ""),
                                len(cands), top3, current_user.get("sub","")
                            )
                        except Exception as e:
                            logger.debug(f"Non-critical: save_search failed: {e}")
                        return {
                            "response": gemini_result.get('response', ''),
                            "ai_powered": True,
                            "context_included": include_candidates,
                            "source": "gemini",
                            "candidates_lookup": cands
                        }
                    else:
                        # Fallback for string response
                        return {
                            "response": gemini_result,
                            "ai_powered": True,
                            "context_included": include_candidates,
                            "source": "gemini"
                        }
            else:
                logger.warning(f"Gemini unavailable: svc={gemini_svc is not None}, available={getattr(gemini_svc, 'available', 'N/A')}")
        except asyncio.TimeoutError:
            logger.warning(f"Gemini chat timeout (>{_deps().AI_TIMEOUT}s)")
        except Exception as gemini_err:
            import traceback as _tb
            logger.warning(f"Gemini chat error: {gemini_err}\n{_tb.format_exc()}")
        
        # TIER 1: Rule-based fallback
        return {
            "response": f"I understand you're asking about: '{message}'. Currently no AI services are available. "
                        f"Please configure GEMINI_API_KEY for intelligent responses.",
            "ai_powered": False,
            "context_included": include_candidates,
            "source": "rule_based"
        }
        
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        raise HTTPException(status_code=500, detail="AI chat service temporarily unavailable")




@router.post("/api/candidates/{candidate_id}/rescore")
async def rescore_single_candidate(candidate_id: str, current_user: dict = Depends(require_admin)):
    """
    Re-run Gemini AI scoring for a single candidate.
    Updates matchScore, jobCategory, skills, experience in the database.
    Called when user clicks 'Refresh' on candidate detail page.
    """
    try:
        def _get_candidate_for_rescore():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, name, email, skills, summary, education, work_history, "
                    "experience, resume_text, match_score, job_category "
                    "FROM candidates WHERE id = ? AND is_active = 1",
                    (candidate_id,),
                )
                return cursor.fetchone()

        row = await asyncio.to_thread(_get_candidate_for_rescore)
        if not row:
            raise HTTPException(404, "Candidate not found")

        cid, name, email, skills_json, summary, education, work_history, experience, resume_text, old_score, old_category = row

        # Build analysis text — prefer resume_text from PDF
        text_parts = []
        if resume_text and len(str(resume_text).strip()) > 50:
            text_parts.append(str(resume_text)[:3000])
        else:
            if name and name != "Unknown":
                text_parts.append(f"Name: {name}")
            if summary:
                text_parts.append(f"Summary: {summary}")
            try:
                skills = json.loads(skills_json) if isinstance(skills_json, str) else (skills_json or [])
                if skills and isinstance(skills, list) and skills != ["R"]:
                    text_parts.append(f"Skills: {', '.join(skills)}")
            except Exception:
                skills = []
            if education:
                text_parts.append(f"Education: {education}")
            if work_history:
                try:
                    wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                    for job in (wh if isinstance(wh, list) else []):
                        if isinstance(job, dict):
                            text_parts.append(f"Work: {job.get('title', '')} at {job.get('company', '')} ({job.get('duration', '')})")
                except Exception:
                    pass

        analysis_text = "\n".join(text_parts)

        if len(analysis_text.strip()) < 20:
            return {
                "status": "skipped",
                "message": "Not enough data to re-score",
                "matchScore": old_score,
                "jobCategory": old_category,
            }

        # Use Gemini (preferred) or fallback AI service
        rescore_ai = _ai()
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                rescore_ai = gemini_svc
        except Exception:
            pass

        new_score = old_score
        new_category = old_category
        new_skills = None
        new_experience = None

        _job_ctx = old_category or None
        try:
            ai_result = await asyncio.wait_for(
                rescore_ai.analyze_candidate(analysis_text, job_context=_job_ctx),
                timeout=_deps().AI_ANALYSIS_TIMEOUT,
            )
            if ai_result:
                raw_score = ai_result.get("quality_score") or ai_result.get("match_score")
                try:
                    parsed_score = int(float(raw_score)) if raw_score else 0
                except (TypeError, ValueError):
                    parsed_score = 0
                if parsed_score > 0:
                    # Never downgrade an existing valid score — take the higher value
                    new_score = max(parsed_score, old_score or 0)
                else:
                    # AI returned 0 — calculate fallback from AI-extracted data
                    ai_skills_fb = ai_result.get("skills", [])
                    ai_exp_fb = 0
                    try:
                        ai_exp_fb = int(float(ai_result.get("experience", 0) or 0))
                    except (TypeError, ValueError):
                        pass
                    has_edu = bool(ai_result.get("education"))
                    has_certs = bool(ai_result.get("certifications"))
                    has_summary = bool(ai_result.get("summary", "").strip())
                    fb = 25 + min(30, len(ai_skills_fb) * 3) + min(25, ai_exp_fb * 3) + (10 if has_edu else 0) + (5 if has_certs else 0) + (3 if has_summary else 0)
                    new_score = min(90, max(15, fb))
                new_category = ai_result.get("job_category", old_category) or old_category
                ai_skills = ai_result.get("skills", [])
                if isinstance(ai_skills, list) and len(ai_skills) > 0:
                    new_skills = ai_skills
                ai_exp = ai_result.get("experience")
                if ai_exp is not None:
                    try:
                        new_experience = int(float(ai_exp))
                    except (TypeError, ValueError):
                        pass
        except asyncio.TimeoutError:
            logger.warning(f"Rescore timeout for {name}")
        except Exception as e:
            logger.warning(f"Rescore AI error for {name}: {e}")

        # Update database
        def _update_rescore(sid, scat, sskills, sexp, scid):
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                parts = ["match_score = ?", "job_category = ?"]
                vals = [sid, scat]
                if sskills is not None:
                    parts.append("skills = ?")
                    vals.append(json.dumps(sskills))
                if sexp is not None:
                    parts.append("experience = ?")
                    vals.append(sexp)
                vals.append(scid)
                cursor.execute(f"UPDATE candidates SET {', '.join(parts)} WHERE id = ?", vals)
                conn.commit()

        await asyncio.to_thread(_update_rescore, new_score, new_category, new_skills, new_experience, candidate_id)

        # Clear cached AI analysis so it regenerates with new data
        def _clear_ai_analysis(cid):
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE candidates SET ai_analysis = NULL WHERE id = ?", (cid,))
                conn.commit()
        try:
            await asyncio.to_thread(_clear_ai_analysis, candidate_id)
        except Exception:
            pass  # OK if no cached analysis

        logger.info(f"✅ Rescored {name}: {old_score}->{new_score}%, {old_category}->{new_category}")

        return {
            "status": "success",
            "candidate_id": candidate_id,
            "old_score": old_score,
            "new_score": new_score,
            "old_category": old_category,
            "new_category": new_category,
            "matchScore": new_score,
            "jobCategory": new_category,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Rescore error for {candidate_id}: {e}")
        raise HTTPException(500, "Error re-scoring candidate")




@router.get("/api/candidates/{candidate_id}/ai-analysis")
async def get_candidate_ai_analysis(candidate_id: str, refresh: bool = False, current_user: dict = Depends(require_auth)):
    """
    Get or generate detailed AI analysis for a candidate.
    Returns comprehensive paragraph-style assessment with pros, cons,
    executive summary, career trajectory, and hiring recommendation.
    Results are persisted in DB for fast retrieval.
    """
    try:
        # Check if we already have a stored analysis (unless refresh requested)
        if not refresh:
            stored = await asyncio.to_thread(_db().get_ai_analysis, candidate_id)
            if stored and stored.get('executive_summary'):
                stored['from_cache'] = True
                return stored
        
        # Request deduplication: if another request is already generating analysis
        # for this candidate, wait for it to complete instead of running a second LLM call
        if candidate_id in _analysis_in_progress:
            logger.info(f"⏳ Waiting for in-progress analysis for {candidate_id}")
            try:
                await asyncio.wait_for(_analysis_in_progress[candidate_id].wait(), timeout=65)
                stored = await asyncio.to_thread(_db().get_ai_analysis, candidate_id)
                if stored and stored.get('executive_summary'):
                    stored['from_cache'] = True
                    return stored
            except asyncio.TimeoutError:
                pass  # Fall through to generate
        
        # Mark this candidate as in-progress
        # Evict stale entries if dict is too large (prevents memory leak from crashed analyses)
        if len(_analysis_in_progress) >= _MAX_CONCURRENT_ANALYSES:
            # Clear oldest entries (likely stale from crashed requests)
            _analysis_in_progress.clear()
            logger.warning("⚠️ Cleared _analysis_in_progress dict (exceeded max concurrent)")
        event = asyncio.Event()
        _analysis_in_progress[candidate_id] = event
        
        try:
            return await _run_candidate_analysis(candidate_id, refresh)
        finally:
            event.set()
            _analysis_in_progress.pop(candidate_id, None)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI analysis error for {candidate_id}: {e}")
        raise HTTPException(500, "Error generating AI analysis")




@router.post("/api/ai/analyze-match")
async def analyze_match(request: AnalyzeMatchRequest, current_user: dict = Depends(require_auth)):
    """
    Use AI to analyze candidate-job match - OPTIMIZED
    Runs AI in separate thread pool to avoid blocking
    """
    try:
        candidate_id = request.candidate.get('id', 'temp')
        job_id = request.job_description.get('id', 'general')
        
        # Check cache first (non-blocking)
        cached = await asyncio.to_thread(_db().get_cached_ai_score, candidate_id, job_id)
        if cached:
            cached['from_cache'] = True
            return cached
        
        result = None
        # TIER 0 — Gemini (primary)
        try:
            from services.gemini_service import get_gemini_service
            _ai_gemini = get_gemini_service()
            if _ai_gemini and _ai_gemini.available:
                jd_text = json.dumps(request.job_description)
                result = await asyncio.wait_for(
                    _ai_gemini.match_candidate_to_job(request.candidate, jd_text),
                    timeout=15.0
                )
                if result:
                    result['source'] = 'gemini'
                    logger.info("✅ Gemini match analysis completed")
        except asyncio.TimeoutError:
            logger.warning("⏱️ Gemini analyze-match timeout")
        except Exception as gemini_err:
            logger.warning(f"⚠️ Gemini analyze-match error: {gemini_err}")

        # TIER 1 — Local AI (fallback)
        if not result:
            try:
                local_svc = _get_local_ai()
                if local_svc:
                    result = await asyncio.to_thread(
                        local_svc.analyze_candidate_match,
                        request.candidate,
                        request.job_description or {}
                    )
                    result['source'] = 'local_ai'
                    logger.info("✅ Local AI analysis completed")
                else:
                    result = _quick_fallback_analysis(request.candidate, request.job_description)
                    result['source'] = 'fallback_error'

            except asyncio.TimeoutError:
                logger.warning(f"⏱️ Local AI timeout (>{_deps().AI_ANALYSIS_TIMEOUT}s)")
                result = _quick_fallback_analysis(request.candidate, request.job_description)
                result['source'] = 'fallback_timeout'

            except Exception as local_error:
                logger.warning(f"⚠️ Local AI error: {local_error}")
                result = _quick_fallback_analysis(request.candidate, request.job_description)
                result['source'] = 'fallback_error'
        
        # Cache result in background (non-blocking)
        result['from_cache'] = False
        asyncio.create_task(
            asyncio.to_thread(_db().cache_ai_score, candidate_id, job_id, result)
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Analyze match error: {e}")
        # Return fallback instead of error
        return _quick_fallback_analysis(request.candidate, request.job_description)




@router.post("/api/ai/interview-questions")
async def generate_interview_questions(request: InterviewQuestionsRequest, current_user: dict = Depends(require_auth)):
    """
    Generate AI-powered interview questions
    3-TIER FALLBACK: Gemini -> Local AI -> Rule-based
    """
    try:
        # TIER 0: Try Gemini first (primary)
        try:
            from services.gemini_service import get_gemini_service
            _ai_gemini = get_gemini_service()
            if _ai_gemini and _ai_gemini.available:
                jd_str = json.dumps(request.job_description)
                questions = await asyncio.wait_for(
                    _ai_gemini.generate_interview_questions(
                        request.candidate,
                        jd_str,
                        request.num_questions
                    ),
                    timeout=15.0
                )
                if questions:
                    return {"questions": questions, "source": "gemini"}
        except Exception as gemini_err:
            logger.warning(f"⚠️ Gemini interview questions failed: {gemini_err}")

        # TIER 1: Try Local AI (FREE)
        try:
            local_svc = _get_local_ai()
            if local_svc:
                raw_questions = await asyncio.to_thread(
                    local_svc.generate_interview_questions,
                    request.candidate,
                    request.job_description or {},
                    request.num_questions or 10
                )
                # Normalize List[str] -> List[Dict] to match TIER 0 shape
                questions = []
                for q in (raw_questions or []):
                    if isinstance(q, dict):
                        questions.append(q)
                    else:
                        questions.append({"question": str(q), "type": "general", "difficulty": "medium", "skill_tested": "", "what_to_look_for": ""})
                if questions:
                    return {"questions": questions, "source": "local_ai"}
        except Exception as local_error:
            logger.warning(f"⚠️ Local AI interview questions failed: {local_error}")

        # TIER 2: Rule-based fallback
        candidate_skills = request.candidate.get('skills', [])
        job_title = request.job_description.get('title', 'the position')
        default_questions = [
            f"Tell me about your experience that's most relevant to {job_title}.",
            f"How do you stay current with developments in your field?",
            f"Describe a challenging project you worked on and how you overcame obstacles.",
            f"What interests you most about this role?",
            f"Where do you see yourself in the next 3-5 years?"
        ]
        if candidate_skills:
            skill_q = f"Can you describe your experience with {', '.join(candidate_skills[:3])}?"
            default_questions[1] = skill_q

        return {
            "questions": default_questions[:request.num_questions],
            "source": "rule_based",
            "note": "Configure Gemini for AI-generated interview questions"
        }
    except Exception as e:
        raise HTTPException(500, "Error generating questions")



@router.post("/api/ai/summarize-resume")
async def summarize_resume(request: SummarizeResumeRequest, current_user: dict = Depends(require_auth)):
    """
    Generate AI summary of resume
    3-TIER FALLBACK: Gemini -> Local AI -> Rule-based
    """
    try:
        # TIER 0 — Gemini
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                result = await asyncio.wait_for(
                    gemini_svc.analyze_candidate(request.resume_text),
                    timeout=15.0
                )
                if result and isinstance(result, dict) and result.get('summary'):
                    return {
                        "summary": result['summary'],
                        "key_skills": result.get('skills', []),
                        "experience_years": result.get('experience', 0),
                        "source": "gemini"
                    }
        except Exception:
            pass  # Fall through to TIER 1

        # TIER 1: Try Local AI (FREE)
        try:
            local_svc = _get_local_ai()
            if local_svc:
                summary = await asyncio.to_thread(local_svc.summarize_resume, request.resume_text)
                if summary:
                    return {"summary": summary, "source": "local_ai"}
        except Exception as local_error:
            logger.warning(f"⚠️ Local AI summarize failed: {local_error}")

        # TIER 2: Rule-based fallback
        text = request.resume_text[:500]
        return {
            "summary": f"Resume summary (basic extraction): {text}...",
            "source": "rule_based",
            "note": "Configure Gemini for AI-powered summaries"
        }
    except Exception as e:
        raise HTTPException(500, "Error summarizing resume")



@router.post("/api/ai/batch-analyze")
async def batch_analyze_new_candidates(job_id: str = "general", batch_size: int = 50, current_user: dict = Depends(require_admin)):
    """
    Batch analyze ONLY NEW candidates with CONCURRENT processing
    PRIMARY: Local AI (FREE, handles 100+ concurrent requests)
    FALLBACK: Rule-based (keyword matching)
    Optimized for high-load scenarios with 10,000+ candidates
    """
    try:
        # Get only candidates without AI scores
        new_candidates = await asyncio.to_thread(_db().get_candidates_needing_ai_analysis, job_id)
        
        if not new_candidates:
            return {
                "message": "All candidates already analyzed",
                "new_count": 0,
                "analyzed_count": 0
            }
        
        # Process in batches with concurrent execution
        analyzed_count = 0
        failed_count = 0
        fallback_used = 0
        gemini_count = 0
        _batch_sem = asyncio.Semaphore(5)

        # Process batch_size candidates at a time (default 50 for high throughput)
        batch = new_candidates[:batch_size]

        async def analyze_one(candidate):
            nonlocal analyzed_count, failed_count, fallback_used, gemini_count
            try:
                _job_ctx = candidate.get('job_applied_for') or candidate.get('job_category') or None
                analysis_text = candidate.get('resume_text', '') or candidate.get('summary', '')
                result = None
                engine = 'local_ai'

                # TIER 0 — Gemini
                if analysis_text:
                    try:
                        from services.gemini_service import get_gemini_service
                        _ai_gemini = get_gemini_service()
                        if _ai_gemini and _ai_gemini.available:
                            async with _batch_sem:
                                gemini_result = await asyncio.wait_for(
                                    _ai_gemini.analyze_candidate(analysis_text, job_context=_job_ctx),
                                    timeout=_deps().AI_ANALYSIS_TIMEOUT
                                )
                            if gemini_result and gemini_result.get('quality_score', 0) > 0:
                                result = gemini_result
                                engine = 'gemini'
                                gemini_count += 1
                    except Exception as e:
                        logger.debug(f"Gemini batch analyze skipped for {analysis_text[:50]}: {e}")

                if result is None:
                    # Run CPU-bound Local AI analysis in thread pool to avoid blocking event loop
                    local_svc = _get_local_ai()
                    if local_svc:
                        result = await asyncio.to_thread(
                            local_svc.analyze_candidate_match,
                            candidate,
                            {"id": job_id, "title": _job_ctx or "General Position", "description": ""}
                        )

                if result:
                    result['ai_engine'] = engine
                await asyncio.to_thread(_db().cache_ai_score, candidate['id'], job_id, result)
                analyzed_count += 1
            except Exception as local_error:
                logger.error(f"AI analysis failed for {candidate['id']}: {local_error}")
                failed_count += 1

        # Execute all analyses concurrently (Local AI can handle 100+ parallel)
        await asyncio.gather(*[analyze_one(c) for c in batch], return_exceptions=True)

        return {
            "message": "Batch analysis complete",
            "total_candidates": len(new_candidates),
            "processed_batch": len(batch),
            "analyzed_count": analyzed_count,
            "failed_count": failed_count,
            "fallback_used": fallback_used,
            "ai_engine": "gemini" if gemini_count > 0 else "local_ai",
            "concurrent_processing": True
        }
    except Exception as e:
        raise HTTPException(500, "Error")



@router.get("/api/ai/status")
async def ai_status(current_user: dict = Depends(require_auth)):
    """
    Check AI service status and configuration
    """
    # Get local AI cache stats
    ai_cache = {}
    try:
        local_svc = _get_local_ai()
        ai_cache = local_svc.get_cache_stats() if local_svc else {}
    except Exception as e:
        logger.debug(f"Non-critical: AI cache stats failed: {e}")

    return {
        "available": True,
        "ai_tier_order": _settings.ai_tier_order,
        "environment": "production" if _settings.is_production else "development",
        "primary_engine": _determine_primary_engine(),
        "fallback_engine": "keyword",
        "gemini": {
            "available": _gemini().available if _gemini() else False,
            "model": _gemini().model_name if _gemini() else None,
            "requests_processed": _gemini()._request_count if _gemini() else 0,
            "avg_response_time": round(_gemini()._total_time / max(_gemini()._request_count, 1), 2) if _gemini() else 0,
            "error_count": _gemini()._error_count if _gemini() else 0,
            "cache_size": len(_gemini()._cache) if _gemini() else 0,
        },
        "sentence_model": ai_cache.get('model_loaded', False),
        "ner_model": ai_cache.get('ner_loaded', False),
        "device": ai_cache.get('device', 'cpu'),
        "cache": {
            "embedding": ai_cache.get('embedding_cache_size', 0),
            "ner": ai_cache.get('ner_cache_size', 0),
            "analysis": ai_cache.get('analysis_cache_size', 0),
            "gemini": len(_gemini()._cache) if _gemini() else 0,
        },
        "model": _determine_model_description(),
        "fallback_model": None,
        "message": _determine_ai_message(),
        "caching_enabled": True,
        "concurrent_processing": True,
        "max_concurrent": "100+ requests",
        "cost": _determine_cost_info(),
        "gemini_available": _gemini().available if _gemini() else False,
        "setup_instructions": {
            "gemini": "Set GEMINI_API_KEY env var. Get key from https://aistudio.google.com/apikey",
        }
    }




@router.post("/api/ai/smart-search")
async def ai_smart_search(
    query: str = Body(..., embed=True),
    top_n: int = Body(20, embed=True),
    current_user: dict = Depends(require_admin)
):
    """
    Option C: Two-stage LLM-powered smart search for 5000+ candidates.

    Stage 1: Constraint parsing → semantic similarity filtering (200-500 candidates)
    Stage 2: Gemini ranks the filtered pool with constraint-aware hints

    Takes natural language query and returns best-matching candidates using semantic
    understanding + Gemini AI + constraint awareness.
    """
    # Validate inputs
    if not query or not query.strip():
        raise HTTPException(400, "Query cannot be empty")
    query = query.strip()[:2000]
    top_n = max(1, min(100, top_n))

    try:
        # Stage 1A: Parse constraints from query
        from services.constraint_parser import get_constraint_parser
        constraint_parser = get_constraint_parser()
        constraints = await asyncio.to_thread(constraint_parser.parse_query, query)
        logger.info(f"Parsed constraints: {constraints.dict()}")

        # Get all candidates (enriched)
        candidates = await asyncio.to_thread(
            _db().get_candidates_for_ai, {}, 5000
        )
        if not candidates:
            return {
                "results": [],
                "total": 0,
                "query": query,
                "constraints": constraints.dict(),
                "message": "No candidates in database"
            }

        # Stage 1B+C: Apply two-stage filtering + semantic search
        try:
            from services.semantic_search_service import get_semantic_search_service
            semantic_search = get_semantic_search_service()

            filtered = await asyncio.wait_for(
                semantic_search.filter_stage_1(
                    candidates,
                    query,
                    constraints,
                    target_pool_size=300
                ),
                timeout=_deps().AI_ANALYSIS_TIMEOUT
            )
            logger.info(f"Filtered to {len(filtered)} candidates after constraints + semantic search")

            # Stage 2: Try constraint-aware Gemini ranking
            try:
                from services.gemini_service import get_gemini_service
                gemini_svc = get_gemini_service()
                if gemini_svc and gemini_svc.available and filtered:
                    ranked = await asyncio.wait_for(
                        gemini_svc.rank_candidates_with_constraints(
                            filtered,
                            constraints,
                            query,
                            top_n
                        ),
                        timeout=_deps().AI_ANALYSIS_TIMEOUT
                    )
                    if ranked:
                        formatted = _format_search_results(ranked, candidates)
                        return {
                            "results": formatted,
                            "total_candidates": len(candidates),
                            "after_constraints": len(filtered),
                            "for_gemini": len(filtered),
                            "query": query,
                            "constraints": constraints.dict(),
                            "source": "two-stage-semantic+gemini",
                            "message": f"Found {len(formatted)} matches using two-stage search"
                        }
            except asyncio.TimeoutError:
                logger.warning(f"Constraint-aware Gemini ranking timed out after {_deps().AI_ANALYSIS_TIMEOUT}s")
            except Exception as gemini_err:
                logger.warning(f"Constraint-aware Gemini ranking failed: {gemini_err}")

            # Fallback: Return filtered results ranked by semantic score
            filtered.sort(key=lambda x: x.get('semantic_score', 0), reverse=True)
            formatted = _format_search_results(filtered[:top_n], candidates)
            return {
                "results": formatted,
                "total_candidates": len(candidates),
                "after_constraints": len(filtered),
                "query": query,
                "constraints": constraints.dict(),
                "source": "semantic-only",
                "message": f"Found {len(formatted)} matches using semantic filtering (Gemini unavailable)"
            }

        except asyncio.TimeoutError:
            logger.warning(f"Two-stage search timed out after {_deps().AI_ANALYSIS_TIMEOUT}s")
        except Exception as semantic_err:
            logger.warning(f"Two-stage search failed: {semantic_err}")

        # Fallback to legacy Gemini ranking (no semantic filtering)
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                ranked = await asyncio.wait_for(
                    gemini_svc.rank_candidates_for_job(candidates, query, top_n),
                    timeout=_deps().AI_ANALYSIS_TIMEOUT
                )
                if ranked:
                    formatted = _format_search_results(ranked, candidates)
                    return {
                        "results": formatted,
                        "total_searched": len(candidates),
                        "query": query,
                        "source": "gemini-legacy",
                        "message": f"Found {len(formatted)} matches using Gemini (semantic filtering unavailable)"
                    }
        except Exception as gemini_err:
            logger.warning(f"Legacy Gemini fallback failed: {gemini_err}")

        # Final fallback: matching engine (semantic / TF-IDF)
        try:
            results = await asyncio.wait_for(
                _matching_engine().match_candidates(query, candidates, top_n),
                timeout=_deps().AI_ANALYSIS_TIMEOUT
            )
            formatted = _format_search_results(results, candidates)
            return {
                "results": formatted,
                "total_searched": len(candidates),
                "query": query,
                "source": "semantic",
                "message": f"Found {len(formatted)} matches using semantic search"
            }
        except asyncio.TimeoutError:
            logger.warning("Semantic search timed out after 20s")
        except Exception as sem_err:
            logger.warning(f"Semantic search failed: {sem_err}")

        # Tokenized keyword fallback (individual tokens, not full-string match)
        _STOP_WORDS = {'with', 'and', 'the', 'for', 'years', 'year', 'who', 'has', 'have', 'are', 'that', 'from', 'this', 'those', 'any', 'all'}
        q_lower = query.lower()
        tokens = [t for t in re.split(r'\W+', q_lower) if len(t) > 2 and t not in _STOP_WORDS]
        scored = []
        for c in candidates:
            score = 0
            match_reasons = []
            skills = c.get('skills', [])
            skills_lower = [s.lower() for s in skills]
            matched_skill_names: list = []
            for t in tokens:
                for idx_s, s_low in enumerate(skills_lower):
                    if (t in s_low or s_low in t) and skills[idx_s] not in matched_skill_names:
                        matched_skill_names.append(skills[idx_s])
                        break
            if matched_skill_names:
                score += min(len(matched_skill_names) * 15, 45)
                for sn in matched_skill_names[:3]:
                    match_reasons.append(f"Skill: {sn}")
            summary_lower = str(c.get('summary', '')).lower()
            summary_hits = sum(1 for t in tokens if t in summary_lower)
            if summary_hits:
                score += min(summary_hits * 5, 15)
                match_reasons.append("Summary match")
            cat_lower = str(c.get('jobCategory', '')).lower()
            if any(t in cat_lower for t in tokens):
                score += 10
                match_reasons.append(f"Category: {c.get('jobCategory', '')}")
            # Location matching
            c_location = str(c.get('location', '')).lower()
            if c_location:
                for q_word in tokens:
                    if q_word in c_location:
                        score += 15
                        match_reasons.append(f"Location: {c.get('location', '')}")
                        break
            # Work history company search (enriched data)
            work_hist = c.get('work_history', [])
            if isinstance(work_hist, list):
                for job in work_hist[:3]:
                    company = str(job.get('company', '') if isinstance(job, dict) else '').lower()
                    if any(t in company for t in tokens if len(t) > 3):
                        score += 20
                        match_reasons.append(f"Worked at: {job.get('company', '')}")
                        break
            # Language matching (enriched data)
            langs = c.get('languages', [])
            if isinstance(langs, list):
                langs_lower = {(l.lower() if isinstance(l, str) else '') for l in langs}
                if any(t in langs_lower for t in tokens):
                    score += 15
                    match_reasons.append("Language match")
            scored.append({
                "candidate": c,
                "relevance_score": min(score, 100),
                "match_reasons": match_reasons or ["Keyword match"]
            })

        scored.sort(key=lambda x: x['relevance_score'], reverse=True)
        return {
            "results": scored[:top_n],
            "total_searched": len(candidates),
            "query": query,
            "source": "keyword",
            "message": "Used basic keyword matching"
        }
    except Exception as e:
        logger.error(f"Smart search error: {e}")
        raise HTTPException(500, "Internal server error")


