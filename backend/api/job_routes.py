"""Route module: job. Auto-extracted from main_legacy.py."""
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

router = APIRouter(tags=["job"])


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


@router.post("/api/jd/generate")
async def generate_job_description(
    title: str = Body(..., embed=True),
    department: str = Body("", embed=True),
    experience_level: str = Body("Mid Level", embed=True),
    employment_type: str = Body("Full-time", embed=True),
    location: str = Body("", embed=True),
    description: str = Body("", embed=True),
    skills: list = Body([], embed=True),
    current_user: dict = Depends(require_auth)
):
    """Generate a job description using AI"""
    try:
        prompt = f"""Generate a professional, detailed job description for the following role:

Title: {title}
Department: {department}
Experience Level: {experience_level}
Employment Type: {employment_type}
Location: {location}
Brief Description: {description}
Required Skills: {', '.join(skills) if skills else 'Not specified'}

Generate a comprehensive job description with these sections:
1. Company Overview (2-3 sentences about a modern innovative company)
2. Position Summary (3-4 sentences about the role)
3. Key Responsibilities (6-8 bullet points)
4. Required Qualifications (5-7 bullet points)
5. Preferred Qualifications (3-5 bullet points)
6. What We Offer (4-6 bullet points about benefits/culture)

Format as clean text with section headers. Be specific and compelling."""

        result_text = None
        source = "fallback"

        # Try Gemini first
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                result = await asyncio.wait_for(
                    gemini_svc.chat(prompt, None),
                    timeout=_deps().AI_ANALYSIS_TIMEOUT
                )
                if result:
                    result_text = result
                    source = "gemini"
        except Exception as e:
            logger.warning(f"Gemini JD generation failed: {e}")

        # Rule-based fallback
        if not result_text:
            skills_text = ', '.join(skills) if skills else 'relevant technical skills'
            result_text = f"""# {title}

**Department:** {department or 'Technology'} | **Location:** {location or 'Flexible'} | **Experience Level:** {experience_level} | **Employment Type:** {employment_type}

## Company Overview

We are a pioneering company dedicated to pushing boundaries in our industry. We are at the forefront of developing innovative solutions that solve real-world challenges and create significant value for our clients.

## Position Summary

We are seeking a talented and driven {title} to join our team{' in ' + location if location else ''}. This {employment_type.lower()} role is crucial for {description or 'driving our technical initiatives forward'}, leveraging your expertise in {skills_text}.

## Key Responsibilities

- Lead and contribute to the design, development, and deployment of solutions
- Collaborate with cross-functional teams to define and implement requirements
- Write clean, maintainable, and well-tested code following best practices
- Participate in code reviews and contribute to technical documentation
- Mentor junior team members and share knowledge across the organization
- Stay current with industry trends and emerging technologies

## Required Qualifications

- {experience_level} experience in a similar role
- Strong proficiency in {skills_text}
- Excellent problem-solving and analytical skills
- Strong communication and collaboration abilities
- Bachelor's degree in a relevant field or equivalent experience

## Preferred Qualifications

- Experience with cloud platforms (AWS, GCP, Azure)
- Experience in an agile development environment
- Contributions to open-source projects
- Relevant industry certifications

## What We Offer

- Competitive salary and comprehensive benefits package
- Flexible working arrangements
- Professional development opportunities
- Collaborative and innovative work environment
- Health and wellness programs
"""
            source = "template"

        return {
            "status": "success",
            "job_description": result_text,
            "title": title,
            "source": source
        }
    except Exception as e:
        logger.error(f"JD generation error: {e}")
        raise HTTPException(status_code=500, detail="Error occurred. Check server logs.")



@router.post("/api/matching/match-candidates")
async def match_candidates(
    job_description_id: str,
    candidate_ids: Optional[List[str]] = None,
    current_user: dict = Depends(require_auth),
):
    """
    Match candidates against a job description using LLM + semantic + TF-IDF matching.
    Resolves IDs to data, then calls the multi-tier matching engine.
    """
    try:
        # Resolve job description from database
        def _resolve_match_data():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT description, title, required_skills FROM job_descriptions WHERE id = ?", (job_description_id,))
                jd_row = cursor.fetchone()
                if not jd_row:
                    return None, []
                
                job_text = f"{jd_row[1] or ''}\n{jd_row[0] or ''}\nRequired Skills: {jd_row[2] or ''}"
                
                if candidate_ids:
                    placeholders = ','.join(['?' for _ in candidate_ids])
                    cursor.execute(f"SELECT * FROM candidates WHERE id IN ({placeholders}) AND is_active = 1", candidate_ids)
                else:
                    cursor.execute("SELECT * FROM candidates WHERE is_active = 1 ORDER BY match_score DESC LIMIT 100")
                
                rows = cursor.fetchall()
            candidates = [_db()._row_to_candidate(row) for row in rows]
            return job_text, candidates
        job_text, candidates = await asyncio.to_thread(_resolve_match_data)
        if job_text is None:
            raise HTTPException(404, f"Job description not found: {job_description_id}")
        
        if not candidates:
            return []
        
        results = await _matching_engine().match_candidates(job_text, candidates)
        return results
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Error matching candidates")



@router.post("/api/matching/evaluate-candidate")
async def evaluate_candidate(candidate_id: str, job_description_id: str, current_user: dict = Depends(require_auth)):
    """
    Detailed AI evaluation of a single candidate using LLM.
    Resolves IDs to data, then calls the multi-tier matching engine.
    """
    try:
        # Resolve job description
        def _resolve_eval_data():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT description, title, required_skills FROM job_descriptions WHERE id = ?", (job_description_id,))
                jd_row = cursor.fetchone()
                if not jd_row:
                    return None, None
                
                job_text = f"{jd_row[1] or ''}\n{jd_row[0] or ''}\nRequired Skills: {jd_row[2] or ''}"
                
                cursor.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
                cand_row = cursor.fetchone()
            if not cand_row:
                return job_text, None
            return job_text, _db()._row_to_candidate(cand_row)
        job_text, candidate_data = await asyncio.to_thread(_resolve_eval_data)
        if job_text is None:
            raise HTTPException(404, f"Job description not found: {job_description_id}")
        if candidate_data is None:
            raise HTTPException(404, f"Candidate not found: {candidate_id}")
        
        evaluation = await _matching_engine().evaluate_candidate(candidate_data, job_text)
        return evaluation
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Error evaluating candidate")



@router.get("/api/taxonomy")
async def get_job_taxonomy():
    """Get the full hierarchical job taxonomy (categories -> subcategories)"""
    from services.job_taxonomy import get_all_categories_with_subcategories, ALL_CATEGORIES
    return {
        "categories": ALL_CATEGORIES,
        "taxonomy": get_all_categories_with_subcategories(),
    }



@router.get("/api/taxonomy/{category}/subcategories")
async def get_subcategories(category: str):
    """Get subcategories for a specific category"""
    from services.job_taxonomy import get_subcategories as _get_subs
    subs = _get_subs(category)
    if not subs:
        raise HTTPException(404, f"Category '{category}' not found")
    return {"category": category, "subcategories": subs}



@router.post("/api/taxonomy/classify")
async def classify_title(title: str = Body(..., embed=True), current_user: dict = Depends(require_auth)):
    """Classify a free-text job title into category + subcategory"""
    from services.job_taxonomy import classify_job_title
    cat, sub = classify_job_title(title)
    return {"title": title, "category": cat, "subcategory": sub}


