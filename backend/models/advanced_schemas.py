"""
Advanced Pydantic Models for AI-Powered Services
ML Ranking, Skill Extraction, Analytics, Calendar, SMS, Campaigns
"""
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


# ============================================================================
# Enums
# ============================================================================

class CampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StepType(str, Enum):
    EMAIL = "email"
    SMS = "sms"
    TASK = "task"
    WEBHOOK = "webhook"


class SkillLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class RedFlagSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ============================================================================
# ML Ranking Models
# ============================================================================

class MLRankRequest(BaseModel):
    """Request to rank candidates using ML model"""
    candidate_ids: List[str] = Field(..., description="List of candidate IDs to rank")
    job_id: Optional[str] = Field(default=None, description="Job ID for context")
    top_n: int = Field(default=10, ge=1, le=100, description="Number of top candidates to return")


class MLRankResult(BaseModel):
    """Single candidate ML ranking result"""
    candidate_id: str
    hire_probability: float = Field(ge=0, le=1)
    rank: int
    factors: Dict[str, float] = Field(default_factory=dict)


class MLRankResponse(BaseModel):
    """Response with ranked candidates"""
    rankings: List[MLRankResult]
    model_version: str
    total_candidates: int
    model_trained: bool


class HiringDecisionRequest(BaseModel):
    """Record a hiring decision for ML training"""
    candidate_id: str
    job_id: str
    was_hired: bool
    feedback: Optional[str] = None


# ============================================================================
# Skill Extraction Models
# ============================================================================

class SkillExtractionRequest(BaseModel):
    """Request to extract skills from resume text"""
    resume_text: str = Field(..., min_length=10, description="Resume text to analyze")
    use_gpt4: bool = Field(default=False, description="Use GPT-4 for advanced extraction")


class ExtractedSkill(BaseModel):
    """Extracted skill with metadata"""
    name: str
    level: SkillLevel
    category: str
    confidence: float = Field(ge=0, le=1)
    context: Optional[str] = None
    years_experience: Optional[int] = None
    is_inferred: bool = False


class SkillExtractionResponse(BaseModel):
    """Response with extracted skills"""
    technical_skills: List[ExtractedSkill]
    soft_skills: List[ExtractedSkill]
    certifications: List[str]
    tools: List[str]
    extraction_method: str  # "gpt4" or "local"


class SkillGapRequest(BaseModel):
    """Request to analyze skill gaps"""
    candidate_id: str
    job_id: str


class SkillGapResponse(BaseModel):
    """Skill gap analysis result"""
    candidate_id: str
    job_id: str
    matched_skills: List[str]
    missing_required: List[str]
    missing_preferred: List[str]
    recommendations: List[str]
    gap_score: float = Field(ge=0, le=100)


# ============================================================================
# Duplicate Detection Models
# ============================================================================

class DuplicateCheckRequest(BaseModel):
    """Request to check for duplicates"""
    candidate_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    name: Optional[str] = None
    threshold: float = Field(default=70, ge=0, le=100)


class DuplicateMatch(BaseModel):
    """A potential duplicate match"""
    candidate_id: str
    candidate_name: str
    candidate_email: str
    similarity_score: float
    match_reasons: List[str]


class DuplicateCheckResponse(BaseModel):
    """Response with duplicate candidates"""
    has_duplicates: bool
    duplicates: List[DuplicateMatch]
    checked_candidate_id: Optional[str]


class MergeCandidatesRequest(BaseModel):
    """Request to merge duplicate candidates"""
    primary_candidate_id: str
    duplicate_candidate_ids: List[str]


# ============================================================================
# Job Matching Models
# ============================================================================

class JobMatchRequest(BaseModel):
    """Request for job-candidate matching"""
    candidate_id: str
    job_ids: List[str] = Field(default_factory=list, description="Empty = match all jobs")


class JobFitScore(BaseModel):
    """Job fit score breakdown"""
    job_id: str
    job_title: str
    overall_score: float = Field(ge=0, le=100)
    skills_score: float = Field(ge=0, le=100)
    experience_score: float = Field(ge=0, le=100)
    education_score: float = Field(ge=0, le=100)
    location_score: float = Field(ge=0, le=100)
    matched_skills: List[str]
    missing_skills: List[str]


class JobMatchResponse(BaseModel):
    """Response with job matches"""
    candidate_id: str
    candidate_name: str
    matches: List[JobFitScore]
    best_match: Optional[JobFitScore]


class CandidateMatchRequest(BaseModel):
    """Request to find candidates for a job"""
    job_id: str
    min_score: float = Field(default=50, ge=0, le=100)
    limit: int = Field(default=20, ge=1, le=100)


# ============================================================================
# Predictive Analytics Models
# ============================================================================

class PredictionRequest(BaseModel):
    """Request predictions for a candidate"""
    candidate_id: str
    job_id: Optional[str] = None


class PredictionResponse(BaseModel):
    """Predictive analytics response"""
    candidate_id: str
    response_rate: float = Field(ge=0, le=1, description="Probability of response")
    interview_success: float = Field(ge=0, le=1, description="Probability of passing interview")
    offer_acceptance: float = Field(ge=0, le=1, description="Probability of accepting offer")
    retention_risk: str  # "low", "medium", "high"
    time_to_hire_days: int
    factors: Dict[str, Any]


class PipelineAnalyticsResponse(BaseModel):
    """Pipeline-wide analytics"""
    total_candidates: int
    avg_response_rate: float
    avg_interview_success: float
    bottlenecks: List[str]
    recommendations: List[str]


# ============================================================================
# Resume Quality Models
# ============================================================================

class ResumeQualityRequest(BaseModel):
    """Request resume quality analysis"""
    candidate_id: Optional[str] = None
    resume_text: Optional[str] = None


class RedFlag(BaseModel):
    """A detected red flag"""
    type: str
    severity: RedFlagSeverity
    description: str
    details: Optional[Dict[str, Any]] = None


class ResumeQualityResponse(BaseModel):
    """Resume quality analysis result"""
    candidate_id: Optional[str]
    overall_score: float = Field(ge=0, le=100)
    red_flags: List[RedFlag]
    strengths: List[str]
    ats_score: float = Field(ge=0, le=100)
    interview_questions: List[str]
    recommendations: List[str]


# ============================================================================
# Email Templates Models
# ============================================================================

class EmailTemplateCreate(BaseModel):
    """Create a new email template"""
    template_id: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1)
    category: str = Field(default="general")
    variables: List[str] = Field(default_factory=list)


class EmailTemplateUpdate(BaseModel):
    """Update an email template"""
    name: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None


class EmailTemplateResponse(BaseModel):
    """Email template response"""
    template_id: str
    name: str
    subject: str
    body: str
    category: str
    variables: List[str]
    is_default: bool
    created_at: Optional[str] = None


class RenderTemplateRequest(BaseModel):
    """Request to render a template"""
    template_id: str
    variables: Dict[str, str]


class RenderTemplateResponse(BaseModel):
    """Rendered template response"""
    subject: str
    body: str


# ============================================================================
# Calendar Integration Models
# ============================================================================

class ScheduleInterviewRequest(BaseModel):
    """Request to schedule an interview"""
    candidate_id: str
    candidate_email: EmailStr
    candidate_name: str
    job_title: str
    interviewer_email: EmailStr
    preferred_times: List[str] = Field(default_factory=list, description="ISO datetime strings")
    duration_minutes: int = Field(default=60, ge=15, le=480)
    interview_type: str = Field(default="video")  # video, phone, in-person
    notes: Optional[str] = None
    use_calendly: bool = Field(default=False)


class InterviewSlot(BaseModel):
    """Available interview slot"""
    start_time: str
    end_time: str
    is_available: bool


class ScheduleInterviewResponse(BaseModel):
    """Response after scheduling interview"""
    success: bool
    interview_id: Optional[str] = None
    calendar_event_id: Optional[str] = None
    calendly_link: Optional[str] = None
    scheduled_time: Optional[str] = None
    meeting_link: Optional[str] = None
    error: Optional[str] = None


class AvailabilityRequest(BaseModel):
    """Request available time slots"""
    interviewer_email: EmailStr
    date_range_start: str
    date_range_end: str
    duration_minutes: int = Field(default=60)


class AvailabilityResponse(BaseModel):
    """Available time slots response"""
    slots: List[InterviewSlot]
    timezone: str


# ============================================================================
# SMS Notification Models
# ============================================================================

class SendSMSRequest(BaseModel):
    """Request to send SMS"""
    to_phone: str = Field(..., min_length=10)
    message: Optional[str] = None
    template_id: Optional[str] = None
    variables: Dict[str, str] = Field(default_factory=dict)
    candidate_id: Optional[str] = None


class SendSMSResponse(BaseModel):
    """SMS send response"""
    success: bool
    message_sid: Optional[str] = None
    to_phone: str
    status: str
    error: Optional[str] = None


class BulkSMSRequest(BaseModel):
    """Request to send bulk SMS"""
    recipients: List[Dict[str, str]]  # [{"phone": "...", "name": "..."}]
    template_id: str
    variables: Dict[str, str] = Field(default_factory=dict)


class BulkSMSResponse(BaseModel):
    """Bulk SMS response"""
    total: int
    successful: int
    failed: int
    results: List[SendSMSResponse]


# ============================================================================
# Campaign / Follow-up Models
# ============================================================================

class CampaignStep(BaseModel):
    """A single step in a drip campaign"""
    delay_days: int = Field(default=0, ge=0)
    delay_hours: int = Field(default=0, ge=0)
    type: StepType
    template: Optional[str] = None
    message: Optional[str] = None
    subject: Optional[str] = None
    condition: Optional[str] = None
    task: Optional[str] = None


class CampaignCreate(BaseModel):
    """Create a new campaign"""
    campaign_id: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    trigger: str = Field(default="manual")
    steps: List[CampaignStep]
    stop_conditions: List[str] = Field(default_factory=list)


class CampaignResponse(BaseModel):
    """Campaign response"""
    campaign_id: str
    name: str
    description: Optional[str]
    trigger: str
    steps: List[Dict[str, Any]]
    stop_conditions: List[str]
    is_default: bool = False
    created_at: Optional[str] = None


class EnrollCandidateRequest(BaseModel):
    """Enroll candidate in campaign"""
    candidate_id: str
    candidate_email: EmailStr
    candidate_name: str
    candidate_phone: Optional[str] = None
    campaign_id: str
    variables: Dict[str, str] = Field(default_factory=dict)


class EnrollmentResponse(BaseModel):
    """Enrollment response"""
    status: str
    enrollment_id: Optional[str] = None
    campaign: Optional[str] = None
    next_step_at: Optional[str] = None
    error: Optional[str] = None


class CampaignStatsResponse(BaseModel):
    """Campaign statistics"""
    campaign_id: str
    total_enrolled: int
    active: int
    completed: int
    cancelled: int
    responded: int


class UnenrollRequest(BaseModel):
    """Unenroll candidate from campaign"""
    candidate_id: str
    campaign_id: Optional[str] = None
    reason: str = Field(default="manual")
