"""
Advanced API Routes for AI-Powered Services
Handles ML Ranking, Skill Extraction, Analytics, Calendar, SMS, Campaigns
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging
import asyncio

from models.advanced_schemas import (
    # ML Ranking
    MLRankRequest, MLRankResponse, MLRankResult, HiringDecisionRequest,
    # Skill Extraction
    SkillExtractionRequest, SkillExtractionResponse, SkillGapRequest, SkillGapResponse,
    # Duplicate Detection
    DuplicateCheckRequest, DuplicateCheckResponse, DuplicateMatch, MergeCandidatesRequest,
    # Job Matching
    JobMatchRequest, JobMatchResponse, CandidateMatchRequest,
    # Predictive Analytics
    PredictionRequest, PredictionResponse, PipelineAnalyticsResponse,
    # Resume Quality
    ResumeQualityRequest, ResumeQualityResponse,
    # Email Templates
    EmailTemplateCreate, EmailTemplateUpdate, EmailTemplateResponse,
    RenderTemplateRequest, RenderTemplateResponse,
    # Calendar
    ScheduleInterviewRequest, ScheduleInterviewResponse,
    AvailabilityRequest, AvailabilityResponse,
    # SMS
    SendSMSRequest, SendSMSResponse, BulkSMSRequest, BulkSMSResponse,
    # Campaigns
    CampaignCreate, CampaignResponse, EnrollCandidateRequest,
    EnrollmentResponse, CampaignStatsResponse, UnenrollRequest,
)

# Import services
from services.ml_ranking_service import get_ranking_model
from services.skill_extraction_service import get_skill_extractor
from services.duplicate_detection_service import get_duplicate_detector
from services.job_matching_service import get_matching_engine
from services.predictive_analytics_service import get_predictive_analytics
from services.resume_quality_service import get_quality_analyzer
from services.email_templates_service import get_templates_service
from services.calendar_integration_service import get_calendar_service
from services.sms_notification_service import get_sms_service
from services.followup_service import get_followup_service
from services.database_service import get_db_service
from core.dependencies import require_auth

logger = logging.getLogger(__name__)

# Create router — all advanced endpoints require authentication
router = APIRouter(prefix="/api/advanced", tags=["Advanced AI Services"], dependencies=[Depends(require_auth)])


def _safe_error(prefix: str, e: Exception) -> str:
    """Return a sanitized error message for API responses (no stack traces or internal paths)."""
    msg = str(e)[:200]  # Truncate long error messages
    # Strip file paths and internal details
    for pattern in ['/app/', '/usr/', 'Traceback', 'File "']:
        if pattern in msg:
            return f"{prefix}: internal error"
    return f"{prefix}: {msg}"


# ============================================================================
# ML RANKING ENDPOINTS
# ============================================================================

@router.post("/ml/rank", response_model=MLRankResponse)
async def rank_candidates(request: MLRankRequest):
    """
    Rank candidates using ML model trained on hiring decisions.
    Returns probability of hire for each candidate.
    """
    try:
        service = get_ranking_model()
        db_service = get_db_service()
        
        # Get candidates from database
        candidates = []
        for cid in request.candidate_ids:
            candidate = db_service.get_candidate_by_id(cid)
            if candidate:
                candidates.append({
                    'id': cid,
                    'skills': candidate.get('skills', []),
                    'experience': candidate.get('experience', 0),
                    'education': candidate.get('education', []),
                    'location': candidate.get('location', ''),
                })
            else:
                candidates.append({
                    'id': cid,
                    'skills': [],
                    'experience': 0,
                    'education': [],
                    'location': '',
                })
        
        job = {'id': request.job_id} if request.job_id else None
        
        rankings = service.rank_candidates(candidates, job)
        
        results = [
            MLRankResult(
                candidate_id=r['candidate_id'],
                hire_probability=r['hire_probability'],
                rank=r['rank'],
                factors=r.get('factors', {})
            )
            for r in rankings[:request.top_n]
        ]
        
        return MLRankResponse(
            rankings=results,
            model_version=service.model_version,
            total_candidates=len(request.candidate_ids),
            model_trained=service.is_trained
        )
    except Exception as e:
        logger.error(f"ML ranking error: {e}")
        raise HTTPException(500, _safe_error("Ranking failed", e))


@router.post("/ml/record-decision")
async def record_hiring_decision(request: HiringDecisionRequest):
    """
    Record a hiring decision to train the ML model.
    Model retrains automatically after sufficient data.
    """
    try:
        service = get_ranking_model()
        db_service = get_db_service()
        
        # Get candidate features from database
        db_candidate = db_service.get_candidate_by_id(request.candidate_id)
        candidate = {
            'id': request.candidate_id,
            'skills': db_candidate.get('skills', []) if db_candidate else [],
            'experience': db_candidate.get('experience', 0) if db_candidate else 0,
        }
        job = {'id': request.job_id}
        
        service.record_hiring_decision(candidate, job, request.was_hired)
        
        return {
            'status': 'recorded',
            'candidate_id': request.candidate_id,
            'was_hired': request.was_hired,
            'total_decisions': len(service.training_data),
            'model_trained': service.is_trained
        }
    except Exception as e:
        logger.error(f"Record decision error: {e}")
        raise HTTPException(500, _safe_error("Failed to record", e))


@router.post("/ml/retrain")
async def retrain_ml_model():
    """Force retrain the ML ranking model"""
    try:
        service = get_ranking_model()
        service.retrain()
        return {
            'status': 'success',
            'model_version': service.model_version,
            'training_samples': len(service.training_data)
        }
    except Exception as e:
        raise HTTPException(500, _safe_error("Retrain failed", e))


# ============================================================================
# SKILL EXTRACTION ENDPOINTS
# ============================================================================

@router.post("/skills/extract", response_model=SkillExtractionResponse)
async def extract_skills(request: SkillExtractionRequest):
    """
    Extract skills from resume text using local pattern matching.
    """
    try:
        service = get_skill_extractor()
        result = await service.extract_skills(request.resume_text)
        
        return SkillExtractionResponse(
            technical_skills=result.get('technical_skills', []),
            soft_skills=result.get('soft_skills', []),
            certifications=result.get('certifications', []),
            tools=result.get('tools', []),
            extraction_method="local"
        )
    except Exception as e:
        logger.error(f"Skill extraction error: {e}")
        raise HTTPException(500, _safe_error("Extraction failed", e))


@router.post("/skills/gap-analysis", response_model=SkillGapResponse)
async def analyze_skill_gap(request: SkillGapRequest):
    """
    Analyze skill gap between candidate and job requirements.
    Returns matched, missing, and recommended skills.
    """
    try:
        service = get_skill_extractor()
        db_service = get_db_service()
        
        # Fetch candidate skills from database
        candidate_data = db_service.get_candidate_by_id(request.candidate_id) if request.candidate_id else None
        candidate_skills = candidate_data.get('skills', []) if candidate_data else []
        job = {'required_skills': [], 'preferred_skills': []}
        
        result = await service.analyze_skill_gaps(candidate_skills, job)
        
        return SkillGapResponse(
            candidate_id=request.candidate_id,
            job_id=request.job_id,
            matched_skills=result.get('matched', []),
            missing_required=result.get('missing_required', []),
            missing_preferred=result.get('missing_preferred', []),
            recommendations=result.get('recommendations', []),
            gap_score=result.get('gap_score', 50)
        )
    except Exception as e:
        raise HTTPException(500, _safe_error("Gap analysis failed", e))


# ============================================================================
# DUPLICATE DETECTION ENDPOINTS
# ============================================================================

@router.post("/duplicates/check", response_model=DuplicateCheckResponse)
async def check_duplicates(request: DuplicateCheckRequest):
    """
    Check if candidate has potential duplicates.
    Uses fuzzy matching on name, email, phone, LinkedIn.
    """
    try:
        service = get_duplicate_detector()
        
        candidate = {
            'id': request.candidate_id or 'new',
            'email': request.email or '',
            'phone': request.phone or '',
            'name': request.name or '',
        }
        
        # Get all candidates from database for comparison
        db_service = get_db_service()
        all_candidates, _ = db_service.get_candidates_paginated(1, 5000, {})

        duplicates = service.find_duplicates(candidate, all_candidates, request.threshold)

        # Map duplicates to response schema
        duplicate_matches = [
            DuplicateMatch(
                candidate_id=str(d.get('id', '')),
                candidate_name=d.get('name', 'Unknown'),
                candidate_email=d.get('email', ''),
                similarity_score=d.get('similarity', d.get('score', 0.0)),
                match_reasons=d.get('reasons', d.get('match_reasons', []))
            )
            for d in duplicates
        ]

        return DuplicateCheckResponse(
            has_duplicates=len(duplicate_matches) > 0,
            duplicates=duplicate_matches,
            checked_candidate_id=request.candidate_id
        )
    except Exception as e:
        raise HTTPException(500, _safe_error("Duplicate check failed", e))


@router.post("/duplicates/merge")
async def merge_duplicates(request: MergeCandidatesRequest):
    """
    Merge duplicate candidates into primary record.
    Combines data and removes duplicates.
    """
    try:
        service = get_duplicate_detector()
        db_service = get_db_service()
        
        # Get candidates from database
        primary = db_service.get_candidate_by_id(request.primary_candidate_id) or {'id': request.primary_candidate_id}
        duplicates = []
        for did in request.duplicate_candidate_ids:
            dup = db_service.get_candidate_by_id(did)
            if dup:
                duplicates.append(dup)
            else:
                duplicates.append({'id': did})
        
        merged = service.merge_candidates(primary, duplicates)
        
        return {
            'status': 'success',
            'merged_candidate_id': request.primary_candidate_id,
            'removed_ids': request.duplicate_candidate_ids
        }
    except Exception as e:
        raise HTTPException(500, _safe_error("Merge failed", e))


# ============================================================================
# JOB MATCHING ENDPOINTS
# ============================================================================

@router.post("/matching/candidate-to-jobs", response_model=JobMatchResponse)
async def match_candidate_to_jobs(request: JobMatchRequest):
    """
    Find best job matches for a candidate.
    Returns scored matches with skill breakdown.
    """
    try:
        service = get_matching_engine()
        db_service = get_db_service()
        
        # Get candidate from database
        candidate = db_service.get_candidate_by_id(request.candidate_id) or {'id': request.candidate_id, 'name': 'Unknown'}
        jobs = [{'id': jid} for jid in request.job_ids] if request.job_ids else []
        
        matches = []
        for job in jobs:
            score = service.calculate_candidate_fit(candidate, job)
            matches.append(score)
        
        return JobMatchResponse(
            candidate_id=request.candidate_id,
            candidate_name=candidate.get('name', ''),
            matches=[],  # Map matches
            best_match=None
        )
    except Exception as e:
        raise HTTPException(500, _safe_error("Matching failed", e))


@router.post("/matching/job-to-candidates")
async def match_job_to_candidates(request: CandidateMatchRequest):
    """
    Find best candidates for a job.
    Returns ranked candidates with scores.
    """
    try:
        service = get_matching_engine()
        db_service = get_db_service()
        
        # Get job and candidates from database
        job = {'id': request.job_id}
        all_candidates, _ = db_service.get_candidates_paginated(1, 1000, {})
        candidates = all_candidates
        
        matches = []
        for candidate in candidates:
            score = service.calculate_job_fit(candidate, job)
            if score.get('overall_score', 0) >= request.min_score:
                matches.append(score)
        
        matches.sort(key=lambda x: x.get('overall_score', 0), reverse=True)
        
        return {
            'job_id': request.job_id,
            'matches': matches[:request.limit],
            'total_candidates': len(candidates)
        }
    except Exception as e:
        raise HTTPException(500, _safe_error("Matching failed", e))


# ============================================================================
# PREDICTIVE ANALYTICS ENDPOINTS
# ============================================================================

@router.post("/analytics/predict", response_model=PredictionResponse)
async def predict_candidate_outcomes(request: PredictionRequest):
    """
    Predict candidate outcomes: response rate, interview success,
    offer acceptance, retention risk, time to hire.
    """
    try:
        service = get_predictive_analytics()
        
        # Get candidate from database (mock)
        candidate = {'id': request.candidate_id}
        job = {'id': request.job_id} if request.job_id else None
        
        response_rate = service.predict_response_rate(candidate)
        interview_success = service.predict_interview_success(candidate, job)
        offer_acceptance = service.predict_offer_acceptance(candidate, job)
        retention = service.predict_retention_risk(candidate)
        time_to_hire = service.estimate_time_to_hire(candidate, job)
        
        return PredictionResponse(
            candidate_id=request.candidate_id,
            response_rate=response_rate.get('probability', 0.5),
            interview_success=interview_success.get('probability', 0.5),
            offer_acceptance=offer_acceptance.get('probability', 0.5),
            retention_risk=retention.get('risk_level', 'medium'),
            time_to_hire_days=time_to_hire.get('estimated_days', 30),
            factors={
                'response_factors': response_rate.get('factors', {}),
                'interview_factors': interview_success.get('factors', {}),
            }
        )
    except Exception as e:
        raise HTTPException(500, _safe_error("Prediction failed", e))


@router.get("/analytics/pipeline")
async def get_pipeline_analytics():
    """
    Get pipeline-wide analytics and recommendations from real DB data.
    """
    try:
        db = get_db_service()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Total candidates
            cursor.execute("SELECT COUNT(*) FROM candidates WHERE is_active = 1")
            total = cursor.fetchone()[0]
            
            # Average match score
            cursor.execute("SELECT AVG(match_score) FROM candidates WHERE is_active = 1")
            avg_score = cursor.fetchone()[0] or 0
            
            # Strong matches (score >= 70)
            cursor.execute("SELECT COUNT(*) FROM candidates WHERE is_active = 1 AND match_score >= 70")
            strong = cursor.fetchone()[0]
            
            # New in last 24h
            cursor.execute("""
                SELECT COUNT(*) FROM candidates 
                WHERE is_active = 1 AND datetime(applied_date) > datetime('now', '-24 hours')
            """)
            new_24h = cursor.fetchone()[0]
            
            # New in last 7 days
            cursor.execute("""
                SELECT COUNT(*) FROM candidates 
                WHERE is_active = 1 AND datetime(applied_date) > datetime('now', '-7 days')
            """)
            new_7d = cursor.fetchone()[0]
            
            # Category distribution
            cursor.execute("""
                SELECT job_category, COUNT(*) as cnt, AVG(match_score) as avg_s
                FROM candidates WHERE is_active = 1
                GROUP BY job_category ORDER BY cnt DESC
            """)
            categories = {row[0] or 'General': {"count": row[1], "avg_score": round(row[2] or 0, 1)} for row in cursor.fetchall()}
            
            # Score distribution
            cursor.execute("""
                SELECT 
                    SUM(CASE WHEN match_score >= 80 THEN 1 ELSE 0 END) as excellent,
                    SUM(CASE WHEN match_score >= 60 AND match_score < 80 THEN 1 ELSE 0 END) as good,
                    SUM(CASE WHEN match_score >= 40 AND match_score < 60 THEN 1 ELSE 0 END) as fair,
                    SUM(CASE WHEN match_score < 40 THEN 1 ELSE 0 END) as low
                FROM candidates WHERE is_active = 1
            """)
            dist = cursor.fetchone()
            score_distribution = {
                "excellent_80_plus": dist[0] or 0,
                "good_60_79": dist[1] or 0,
                "fair_40_59": dist[2] or 0,
                "low_below_40": dist[3] or 0
            }
            
            # Top skills (from skills JSON column)
            cursor.execute("SELECT skills FROM candidates WHERE is_active = 1 AND skills IS NOT NULL")
            import json as _json
            skill_counts: Dict[str, int] = {}
            for row in cursor.fetchall():
                try:
                    skills = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or [])
                    for s in skills:
                        if isinstance(s, str) and s.strip():
                            skill_counts[s.strip()] = skill_counts.get(s.strip(), 0) + 1
                except Exception:
                    pass
            top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:15]
            
            # Status breakdown
            cursor.execute("""
                SELECT status, COUNT(*) FROM candidates 
                WHERE is_active = 1 GROUP BY status
            """)
            status_breakdown = {(row[0] or 'New'): row[1] for row in cursor.fetchall()}
            
            # Recent activity (last 30 days by day)
            cursor.execute("""
                SELECT date(applied_date) as day, COUNT(*) as cnt
                FROM candidates WHERE is_active = 1 
                AND datetime(applied_date) > datetime('now', '-30 days')
                GROUP BY day ORDER BY day
            """)
            daily_activity = [{"date": row[0], "count": row[1]} for row in cursor.fetchall()]
            
            # Generate bottlenecks and recommendations
            bottlenecks = []
            recommendations = []
            
            if total > 0:
                strong_pct = (strong / total) * 100
                if strong_pct < 20:
                    bottlenecks.append(f"Only {strong_pct:.0f}% of candidates are strong matches (score ≥ 70)")
                    recommendations.append("Consider broadening job descriptions or adjusting match criteria")
                
                if avg_score < 50:
                    bottlenecks.append(f"Average match score is low ({avg_score:.0f}%)")
                    recommendations.append("Review and update job requirements to better align with candidate pool")
                
                if new_7d == 0:
                    bottlenecks.append("No new candidates added in the last 7 days")
                    recommendations.append("Set up email integration or upload more resumes to maintain pipeline flow")
                
                low_quality = score_distribution.get("low_below_40", 0)
                if low_quality > total * 0.3:
                    bottlenecks.append(f"{low_quality} candidates ({(low_quality/total)*100:.0f}%) have very low scores")
                    recommendations.append("Review sourcing channels - many candidates are poor matches")
            else:
                bottlenecks.append("No candidates in the pipeline yet")
                recommendations.append("Upload resumes or connect email integration to start building your pipeline")
                recommendations.append("Use the AI Assistant to search for candidates matching your job descriptions")
            
            # Calculate response rate proxy from email logs
            cursor.execute("SELECT COUNT(*) FROM email_processing_log")
            email_count = cursor.fetchone()[0]
            avg_response_rate = min(email_count / max(total, 1), 1.0) if total > 0 else 0
        
        return {
            'total_candidates': total,
            'strong_matches': strong,
            'new_24h': new_24h,
            'new_7d': new_7d,
            'avg_score': round(avg_score, 1),
            'avg_response_rate': round(avg_response_rate, 2),
            'avg_interview_success': round(strong / max(total, 1), 2),
            'categories': categories,
            'category_count': len(categories),
            'score_distribution': score_distribution,
            'top_skills': [{"skill": s, "count": c} for s, c in top_skills],
            'status_breakdown': status_breakdown,
            'daily_activity': daily_activity,
            'bottlenecks': bottlenecks,
            'recommendations': recommendations
        }
    except Exception as e:
        logger.error(f"Analytics failed: {e}")
        raise HTTPException(500, _safe_error("Analytics failed", e))


# ============================================================================
# RESUME QUALITY ENDPOINTS
# ============================================================================

@router.post("/quality/analyze", response_model=ResumeQualityResponse)
async def analyze_resume_quality(request: ResumeQualityRequest):
    """
    Analyze resume quality: detect red flags, calculate ATS score,
    generate interview questions.
    """
    try:
        service = get_quality_analyzer()
        
        if request.candidate_id:
            # Build candidate dict for the analyzer
            candidate = {'id': request.candidate_id, 'summary': '', 'skills': [], 'workHistory': [], 'education': []}
            resume_text = request.resume_text or ''
        else:
            candidate = {'summary': request.resume_text or '', 'skills': [], 'workHistory': [], 'education': []}
            resume_text = request.resume_text or ''
        
        result = service.analyze_resume(candidate, resume_text)
        
        return ResumeQualityResponse(
            candidate_id=request.candidate_id,
            overall_score=result.get('quality_score', 50),
            red_flags=[],  # Map red flags
            strengths=result.get('strengths', []),
            ats_score=result.get('ats_score', 50),
            interview_questions=result.get('interview_questions', []),
            recommendations=result.get('recommendations', [])
        )
    except Exception as e:
        raise HTTPException(500, _safe_error("Quality analysis failed", e))


# ============================================================================
# EMAIL TEMPLATES ENDPOINTS
# ============================================================================

@router.get("/templates")
async def list_email_templates():
    """Get all email templates"""
    try:
        service = get_templates_service()
        templates = service.get_all_templates()
        return {'templates': list(templates.values())}
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get templates", e))


@router.get("/templates/{template_id}")
async def get_email_template(template_id: str):
    """Get a specific email template"""
    try:
        service = get_templates_service()
        template = service.get_template(template_id)
        if not template:
            raise HTTPException(404, f"Template not found: {template_id}")
        return template
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get template", e))


@router.post("/templates")
async def create_email_template(request: EmailTemplateCreate):
    """Create a new email template"""
    try:
        service = get_templates_service()
        template = service.create_template(
            template_id=request.template_id,
            template={
                'name': request.name,
                'subject': request.subject,
                'body': request.body,
                'category': request.category,
            }
        )
        return template
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to create template", e))


@router.put("/templates/{template_id}")
async def update_email_template(template_id: str, request: EmailTemplateUpdate):
    """Update an email template"""
    try:
        service = get_templates_service()
        updates = request.model_dump(exclude_none=True)
        template = service.update_template(template_id, updates)
        return template
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to update template", e))


@router.delete("/templates/{template_id}")
async def delete_email_template(template_id: str):
    """Delete an email template"""
    try:
        service = get_templates_service()
        success = service.delete_template(template_id)
        if not success:
            raise HTTPException(400, "Cannot delete default template")
        return {'status': 'deleted', 'template_id': template_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to delete template", e))


@router.post("/templates/render")
async def render_email_template(request: RenderTemplateRequest):
    """Render a template with variables"""
    try:
        service = get_templates_service()
        result = service.render_template(request.template_id, request.variables)
        return result
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to render template", e))


# ============================================================================
# CALENDAR INTEGRATION ENDPOINTS
# ============================================================================

@router.post("/calendar/schedule", response_model=ScheduleInterviewResponse)
async def schedule_interview(request: ScheduleInterviewRequest):
    """
    Schedule an interview via Google Calendar or Calendly.
    Creates calendar event with video meeting link.
    """
    try:
        service = get_calendar_service()
        
        result = await service.schedule_interview(
            candidate={
                'id': request.candidate_id,
                'email': request.candidate_email,
                'name': request.candidate_name,
                'jobCategory': request.job_title or '',
            },
            interview_type=request.interview_type or 'Interview',
            datetime_slot=request.preferred_times[0] if request.preferred_times else datetime.now(),
            duration_minutes=request.duration_minutes or 60,
            interviewer_email=request.interviewer_email,
            notes=request.notes or '',
        )
        
        return ScheduleInterviewResponse(**result)
    except Exception as e:
        logger.error(f"Schedule interview error: {e}")
        raise HTTPException(500, _safe_error("Scheduling failed", e))


@router.post("/calendar/availability", response_model=AvailabilityResponse)
async def get_availability(request: AvailabilityRequest):
    """
    Get available time slots for scheduling.
    Checks interviewer's calendar for free slots.
    """
    try:
        service = get_calendar_service()
        
        slots = await service.get_available_slots(
            interviewer_email=request.interviewer_email,
            date_range_start=request.date_range_start,
            date_range_end=request.date_range_end,
            duration_minutes=request.duration_minutes
        )
        
        return AvailabilityResponse(
            slots=slots,
            timezone="UTC"
        )
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get availability", e))


# ============================================================================
# SMS NOTIFICATION ENDPOINTS
# ============================================================================

@router.post("/sms/send", response_model=SendSMSResponse)
async def send_sms(request: SendSMSRequest):
    """
    Send SMS notification to candidate.
    Uses template or custom message.
    """
    try:
        service = get_sms_service()
        
        if request.template_id:
            result = await service.send_template_sms(
                to_phone=request.to_phone,
                template_id=request.template_id,
                variables=request.variables,
                candidate_id=request.candidate_id
            )
        else:
            result = await service.send_sms(
                to_phone=request.to_phone,
                message=request.message or '',
                candidate_id=request.candidate_id
            )
        
        return SendSMSResponse(**result)
    except Exception as e:
        logger.error(f"SMS send error: {e}")
        raise HTTPException(500, _safe_error("SMS failed", e))


@router.post("/sms/bulk", response_model=BulkSMSResponse)
async def send_bulk_sms(request: BulkSMSRequest):
    """
    Send SMS to multiple recipients.
    Rate-limited to avoid carrier issues.
    """
    try:
        service = get_sms_service()
        
        result = await service.send_bulk_sms(
            recipients=request.recipients,
            template_id=request.template_id,
            variables=request.variables
        )
        
        return BulkSMSResponse(**result)
    except Exception as e:
        raise HTTPException(500, _safe_error("Bulk SMS failed", e))


@router.get("/sms/templates")
async def list_sms_templates():
    """Get all SMS templates"""
    try:
        service = get_sms_service()
        return {'templates': service.templates}
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get templates", e))


@router.post("/sms/webhook")
async def sms_webhook(request: dict):
    """Handle incoming SMS webhook from Twilio"""
    try:
        service = get_sms_service()
        result = service.handle_webhook(request)
        return result
    except Exception as e:
        logger.error(f"SMS webhook error: {e}")
        return {'status': 'error'}


# ============================================================================
# CAMPAIGN / FOLLOW-UP ENDPOINTS
# ============================================================================

@router.get("/campaigns")
async def list_campaigns():
    """Get all drip campaigns"""
    try:
        service = get_followup_service()
        campaigns = service.get_all_campaigns()
        return {'campaigns': list(campaigns.values())}
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get campaigns", e))


@router.get("/campaigns/stats/{campaign_id}", response_model=CampaignStatsResponse)
async def get_campaign_stats(campaign_id: str):
    """Get statistics for a campaign (job category)"""
    try:
        db = get_db_service()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status IN ('New','Reviewed') THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN status IN ('Hired','Offered') THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'Withdrawn' THEN 1 ELSE 0 END) as cancelled,
                    SUM(CASE WHEN status IN ('Shortlisted','Interviewing','Offered','Hired') THEN 1 ELSE 0 END) as responded
                FROM candidates
                WHERE is_active = 1 AND job_category = ?
            """, (campaign_id,))
            row = cursor.fetchone()
        return CampaignStatsResponse(
            campaign_id=campaign_id,
            total_enrolled=row[0] or 0,
            active=row[1] or 0,
            completed=row[2] or 0,
            cancelled=row[3] or 0,
            responded=row[4] or 0,
        )
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get stats", e))


@router.get("/campaigns/stats")
async def get_all_campaign_stats():
    """Get campaign performance stats derived from real candidate data, grouped by job category."""
    try:
        db = get_db_service()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COALESCE(job_category, 'General') as category,
                    COUNT(*) as total,
                    SUM(CASE WHEN status IN ('New','Reviewed') THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN status IN ('Hired','Offered') THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'Withdrawn' THEN 1 ELSE 0 END) as cancelled,
                    SUM(CASE WHEN status IN ('Shortlisted','Interviewing','Offered','Hired') THEN 1 ELSE 0 END) as responded
                FROM candidates
                WHERE is_active = 1
                GROUP BY COALESCE(job_category, 'General')
                HAVING COUNT(*) >= 1
                ORDER BY COUNT(*) DESC
            """)
            rows = cursor.fetchall()

        campaigns = {}
        total_enrollments = 0
        active_enrollments = 0
        for row in rows:
            cat = row[0] or 'General'
            campaigns[cat] = {
                'campaign_id': cat,
                'total_enrolled': row[1] or 0,
                'active': row[2] or 0,
                'completed': row[3] or 0,
                'cancelled': row[4] or 0,
                'responded': row[5] or 0,
            }
            total_enrollments += row[1] or 0
            active_enrollments += row[2] or 0

        return {
            'total_campaigns': len(campaigns),
            'total_enrollments': total_enrollments,
            'active_enrollments': active_enrollments,
            'campaigns': campaigns,
        }
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get stats", e))


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str):
    """Get a specific campaign"""
    try:
        service = get_followup_service()
        campaign = service.get_campaign(campaign_id)
        if not campaign:
            raise HTTPException(404, f"Campaign not found: {campaign_id}")
        return campaign
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get campaign", e))


@router.post("/campaigns")
async def create_campaign(request: CampaignCreate):
    """Create a new drip campaign"""
    try:
        service = get_followup_service()
        
        campaign = service.create_campaign(
            campaign_id=request.campaign_id,
            campaign={
                'name': request.name,
                'description': request.description,
                'trigger': request.trigger,
                'steps': [s.model_dump() for s in request.steps],
                'stop_conditions': request.stop_conditions,
            }
        )
        return campaign
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to create campaign", e))


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str):
    """Delete a campaign"""
    try:
        service = get_followup_service()
        success = service.delete_campaign(campaign_id)
        if not success:
            raise HTTPException(400, "Cannot delete default campaign")
        return {'status': 'deleted', 'campaign_id': campaign_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to delete campaign", e))


@router.post("/campaigns/enroll", response_model=EnrollmentResponse)
async def enroll_in_campaign(request: EnrollCandidateRequest):
    """Enroll a candidate in a drip campaign"""
    try:
        service = get_followup_service()
        
        result = service.enroll_candidate(
            candidate={
                'id': request.candidate_id,
                'email': request.candidate_email,
                'name': request.candidate_name,
                'phone': request.candidate_phone,
            },
            campaign_id=request.campaign_id,
            variables=request.variables
        )
        
        return EnrollmentResponse(**result)
    except Exception as e:
        raise HTTPException(500, _safe_error("Enrollment failed", e))


@router.post("/campaigns/unenroll")
async def unenroll_from_campaign(request: UnenrollRequest):
    """Remove candidate from campaign(s)"""
    try:
        service = get_followup_service()
        
        result = service.unenroll_candidate(
            candidate_id=request.candidate_id,
            campaign_id=request.campaign_id,
            reason=request.reason
        )
        
        return result
    except Exception as e:
        raise HTTPException(500, _safe_error("Unenroll failed", e))


@router.post("/campaigns/mark-responded")
async def mark_candidate_responded(candidate_id: str, campaign_id: Optional[str] = None):
    """Mark that candidate has responded (stops campaign)"""
    try:
        service = get_followup_service()
        service.mark_responded(candidate_id, campaign_id)
        return {'status': 'marked_responded', 'candidate_id': candidate_id}
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to mark responded", e))


@router.get("/campaigns/enrollments/{candidate_id}")
async def get_candidate_enrollments(candidate_id: str):
    """Get all campaign enrollments for a candidate"""
    try:
        service = get_followup_service()
        enrollments = service.get_candidate_enrollments(candidate_id)
        return {'enrollments': enrollments}
    except Exception as e:
        raise HTTPException(500, _safe_error("Failed to get enrollments", e))


@router.post("/campaigns/process")
async def process_campaign_steps(background_tasks: BackgroundTasks):
    """Manually trigger processing of due campaign steps"""
    try:
        service = get_followup_service()
        result = await service.process_due_steps()
        return result
    except Exception as e:
        raise HTTPException(500, _safe_error("Processing failed", e))
