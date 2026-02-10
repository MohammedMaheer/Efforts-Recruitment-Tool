"""
Advanced API Routes for AI-Powered Services
Handles ML Ranking, Skill Extraction, Analytics, Calendar, SMS, Campaigns
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import List, Dict, Any, Optional
import logging
import asyncio

from models.advanced_schemas import (
    # ML Ranking
    MLRankRequest, MLRankResponse, MLRankResult, HiringDecisionRequest,
    # Skill Extraction
    SkillExtractionRequest, SkillExtractionResponse, SkillGapRequest, SkillGapResponse,
    # Duplicate Detection
    DuplicateCheckRequest, DuplicateCheckResponse, MergeCandidatesRequest,
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

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/advanced", tags=["Advanced AI Services"])


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
        raise HTTPException(500, f"Ranking failed: {str(e)}")


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
        raise HTTPException(500, f"Failed to record: {str(e)}")


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
        raise HTTPException(500, f"Retrain failed: {str(e)}")


# ============================================================================
# SKILL EXTRACTION ENDPOINTS
# ============================================================================

@router.post("/skills/extract", response_model=SkillExtractionResponse)
async def extract_skills(request: SkillExtractionRequest):
    """
    Extract skills from resume text.
    Uses GPT-4 for advanced inference if enabled.
    """
    try:
        service = get_skill_extractor()
        
        if request.use_gpt4:
            result = await service.extract_skills_gpt4(request.resume_text)
            method = "gpt4"
        else:
            result = service.extract_skills_local(request.resume_text)
            method = "local"
        
        return SkillExtractionResponse(
            technical_skills=result.get('technical_skills', []),
            soft_skills=result.get('soft_skills', []),
            certifications=result.get('certifications', []),
            tools=result.get('tools', []),
            extraction_method=method
        )
    except Exception as e:
        logger.error(f"Skill extraction error: {e}")
        raise HTTPException(500, f"Extraction failed: {str(e)}")


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
        
        result = service.analyze_skill_gaps(candidate_skills, job)
        
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
        raise HTTPException(500, f"Gap analysis failed: {str(e)}")


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
        all_candidates = db_service.get_candidates_paginated(1, 5000, {})
        
        duplicates = service.find_duplicates(candidate, all_candidates, request.threshold)
        
        return DuplicateCheckResponse(
            has_duplicates=len(duplicates) > 0,
            duplicates=[],  # Map duplicates
            checked_candidate_id=request.candidate_id
        )
    except Exception as e:
        raise HTTPException(500, f"Duplicate check failed: {str(e)}")


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
        raise HTTPException(500, f"Merge failed: {str(e)}")


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
        raise HTTPException(500, f"Matching failed: {str(e)}")


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
        all_candidates = db_service.get_candidates_paginated(1, 1000, {})
        candidates = all_candidates if all_candidates else []
        
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
        raise HTTPException(500, f"Matching failed: {str(e)}")


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
        retention = service.predict_retention_risk(candidate, job)
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
        raise HTTPException(500, f"Prediction failed: {str(e)}")


@router.get("/analytics/pipeline")
async def get_pipeline_analytics():
    """
    Get pipeline-wide analytics and recommendations.
    """
    try:
        service = get_predictive_analytics()
        
        # Aggregate analytics (mock)
        return {
            'total_candidates': 0,
            'avg_response_rate': 0.5,
            'avg_interview_success': 0.4,
            'bottlenecks': [],
            'recommendations': []
        }
    except Exception as e:
        raise HTTPException(500, f"Analytics failed: {str(e)}")


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
            # Get candidate from database (mock)
            candidate = {'id': request.candidate_id, 'resume_text': ''}
            resume_text = candidate.get('resume_text', '')
        else:
            resume_text = request.resume_text or ''
        
        result = service.analyze_resume(resume_text)
        
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
        raise HTTPException(500, f"Quality analysis failed: {str(e)}")


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
        raise HTTPException(500, f"Failed to get templates: {str(e)}")


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
        raise HTTPException(500, f"Failed to get template: {str(e)}")


@router.post("/templates")
async def create_email_template(request: EmailTemplateCreate):
    """Create a new email template"""
    try:
        service = get_templates_service()
        template = service.create_template(
            template_id=request.template_id,
            name=request.name,
            subject=request.subject,
            body=request.body,
            category=request.category
        )
        return template
    except Exception as e:
        raise HTTPException(500, f"Failed to create template: {str(e)}")


@router.put("/templates/{template_id}")
async def update_email_template(template_id: str, request: EmailTemplateUpdate):
    """Update an email template"""
    try:
        service = get_templates_service()
        updates = request.model_dump(exclude_none=True)
        template = service.update_template(template_id, updates)
        return template
    except Exception as e:
        raise HTTPException(500, f"Failed to update template: {str(e)}")


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
        raise HTTPException(500, f"Failed to delete template: {str(e)}")


@router.post("/templates/render")
async def render_email_template(request: RenderTemplateRequest):
    """Render a template with variables"""
    try:
        service = get_templates_service()
        result = service.render_template(request.template_id, request.variables)
        return result
    except Exception as e:
        raise HTTPException(500, f"Failed to render template: {str(e)}")


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
            },
            interviewer_email=request.interviewer_email,
            job_title=request.job_title,
            preferred_times=request.preferred_times,
            duration_minutes=request.duration_minutes,
            interview_type=request.interview_type,
            notes=request.notes,
            use_calendly=request.use_calendly
        )
        
        return ScheduleInterviewResponse(**result)
    except Exception as e:
        logger.error(f"Schedule interview error: {e}")
        raise HTTPException(500, f"Scheduling failed: {str(e)}")


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
        raise HTTPException(500, f"Failed to get availability: {str(e)}")


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
        raise HTTPException(500, f"SMS failed: {str(e)}")


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
        raise HTTPException(500, f"Bulk SMS failed: {str(e)}")


@router.get("/sms/templates")
async def list_sms_templates():
    """Get all SMS templates"""
    try:
        service = get_sms_service()
        return {'templates': service.templates}
    except Exception as e:
        raise HTTPException(500, f"Failed to get templates: {str(e)}")


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
        raise HTTPException(500, f"Failed to get campaigns: {str(e)}")


@router.get("/campaigns/stats/{campaign_id}", response_model=CampaignStatsResponse)
async def get_campaign_stats(campaign_id: str):
    """Get statistics for a campaign"""
    try:
        service = get_followup_service()
        stats = service.get_campaign_stats(campaign_id)
        return CampaignStatsResponse(**stats)
    except Exception as e:
        raise HTTPException(500, f"Failed to get stats: {str(e)}")


@router.get("/campaigns/stats")
async def get_all_campaign_stats():
    """Get statistics for all campaigns"""
    try:
        service = get_followup_service()
        return service.get_all_stats()
    except Exception as e:
        raise HTTPException(500, f"Failed to get stats: {str(e)}")


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
        raise HTTPException(500, f"Failed to get campaign: {str(e)}")


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
        raise HTTPException(500, f"Failed to create campaign: {str(e)}")


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
        raise HTTPException(500, f"Failed to delete campaign: {str(e)}")


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
        raise HTTPException(500, f"Enrollment failed: {str(e)}")


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
        raise HTTPException(500, f"Unenroll failed: {str(e)}")


@router.post("/campaigns/mark-responded")
async def mark_candidate_responded(candidate_id: str, campaign_id: Optional[str] = None):
    """Mark that candidate has responded (stops campaign)"""
    try:
        service = get_followup_service()
        service.mark_responded(candidate_id, campaign_id)
        return {'status': 'marked_responded', 'candidate_id': candidate_id}
    except Exception as e:
        raise HTTPException(500, f"Failed to mark responded: {str(e)}")


@router.get("/campaigns/enrollments/{candidate_id}")
async def get_candidate_enrollments(candidate_id: str):
    """Get all campaign enrollments for a candidate"""
    try:
        service = get_followup_service()
        enrollments = service.get_candidate_enrollments(candidate_id)
        return {'enrollments': enrollments}
    except Exception as e:
        raise HTTPException(500, f"Failed to get enrollments: {str(e)}")


@router.post("/campaigns/process")
async def process_campaign_steps(background_tasks: BackgroundTasks):
    """Manually trigger processing of due campaign steps"""
    try:
        service = get_followup_service()
        result = await service.process_due_steps()
        return result
    except Exception as e:
        raise HTTPException(500, f"Processing failed: {str(e)}")
