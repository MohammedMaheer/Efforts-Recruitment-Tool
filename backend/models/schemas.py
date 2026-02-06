"""
Enhanced Pydantic Models with Comprehensive Validation
Following best practices for API data contracts
"""
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum
import re


# ============================================================================
# Enums for Type Safety
# ============================================================================

class CandidateStatus(str, Enum):
    """Candidate status values"""
    NEW = "New"
    REVIEWED = "Reviewed"
    SHORTLISTED = "Shortlisted"
    INTERVIEWING = "Interviewing"
    OFFERED = "Offered"
    HIRED = "Hired"
    REJECTED = "Rejected"
    WITHDRAWN = "Withdrawn"


class MatchTier(str, Enum):
    """Match score tiers"""
    STRONG = "Strong"
    PARTIAL = "Partial"
    WEAK = "Weak"


class JobCategory(str, Enum):
    """Standard job categories"""
    SOFTWARE_ENGINEER = "Software Engineer"
    DEVOPS_ENGINEER = "DevOps Engineer"
    DATA_SCIENTIST = "Data Scientist"
    PRODUCT_MANAGER = "Product Manager"
    MARKETING = "Marketing"
    SALES = "Sales"
    HR = "HR"
    FINANCE = "Finance"
    DESIGN = "Design"
    CUSTOMER_SUPPORT = "Customer Support"
    GENERAL = "General"


# ============================================================================
# Nested Models
# ============================================================================

class SkillDetail(BaseModel):
    """Detailed skill information"""
    name: str = Field(..., min_length=1, max_length=100)
    proficiency: Optional[str] = Field(default=None, description="Skill proficiency level")
    years: Optional[int] = Field(default=None, ge=0, le=50, description="Years of experience")
    
    @field_validator('name')
    @classmethod
    def normalize_skill_name(cls, v: str) -> str:
        return v.strip().lower()


class Education(BaseModel):
    """Education entry"""
    degree: str = Field(..., min_length=1, max_length=200)
    institution: str = Field(..., min_length=1, max_length=200)
    year: str = Field(default="", max_length=20)
    field: Optional[str] = Field(default=None, max_length=200)
    gpa: Optional[float] = Field(default=None, ge=0, le=4.0)


class WorkExperience(BaseModel):
    """Work experience entry"""
    title: str = Field(..., min_length=1, max_length=200)
    company: str = Field(..., min_length=1, max_length=200)
    duration: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_current: bool = False


class AIEvaluation(BaseModel):
    """AI-generated candidate evaluation"""
    strengths: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    recommendation: str = Field(default="")
    confidence_score: float = Field(default=0.0, ge=0, le=1.0)


# ============================================================================
# Request Models
# ============================================================================

class CandidateCreate(BaseModel):
    """Request model for creating a candidate"""
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=30)
    location: Optional[str] = Field(default=None, max_length=200)
    skills: List[str] = Field(default_factory=list)
    experience: int = Field(default=0, ge=0, le=70)
    education: str = Field(default="")
    summary: Optional[str] = Field(default=None, max_length=10000)
    linkedin: Optional[str] = Field(default=None, max_length=500)
    
    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v:
            # Remove all non-digit characters for validation
            digits = re.sub(r'\D', '', v)
            if len(digits) < 7 or len(digits) > 15:
                raise ValueError('Invalid phone number')
        return v
    
    @field_validator('linkedin')
    @classmethod
    def validate_linkedin(cls, v: Optional[str]) -> Optional[str]:
        if v and not ('linkedin.com' in v.lower() or v.startswith('linkedin.com')):
            if not v.startswith('http'):
                v = f'https://linkedin.com/in/{v}'
        return v


class CandidateUpdate(BaseModel):
    """Request model for updating a candidate"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=30)
    location: Optional[str] = Field(default=None, max_length=200)
    skills: Optional[List[str]] = None
    experience: Optional[int] = Field(default=None, ge=0, le=70)
    status: Optional[CandidateStatus] = None
    summary: Optional[str] = Field(default=None, max_length=10000)


class EmailConnectRequest(BaseModel):
    """Request to connect email account"""
    provider: str = Field(..., description="Email provider (gmail, outlook, custom)")
    email: EmailStr
    password: Optional[str] = Field(default=None, min_length=1)
    access_token: Optional[str] = None
    custom_imap_server: Optional[str] = None
    
    @model_validator(mode='after')
    def validate_credentials(self):
        if not self.password and not self.access_token:
            raise ValueError('Either password or access_token must be provided')
        return self


class EmailSyncRequest(BaseModel):
    """Request to sync emails"""
    provider: str
    email: EmailStr
    password: Optional[str] = None
    access_token: Optional[str] = None
    folder: str = Field(default='INBOX', max_length=100)
    limit: int = Field(default=50, ge=1, le=1000)


class OAuth2CallbackRequest(BaseModel):
    """OAuth2 callback data"""
    code: str = Field(..., min_length=1)
    state: Optional[str] = None
    redirect_uri: str = Field(..., min_length=1)


class JobDescriptionCreate(BaseModel):
    """Request to create job description"""
    title: str = Field(..., min_length=1, max_length=200)
    company: Optional[str] = Field(default=None, max_length=200)
    required_skills: List[str] = Field(default_factory=list)
    preferred_skills: List[str] = Field(default_factory=list)
    experience_level: str = Field(default="", max_length=50)
    location: Optional[str] = Field(default=None, max_length=200)
    education: Optional[str] = Field(default=None, max_length=200)
    responsibilities: List[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=50000)


# ============================================================================
# Response Models
# ============================================================================

class CandidateResponse(BaseModel):
    """Response model for candidate data"""
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    location: Optional[str] = None
    experience: int = 0
    skills: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    education: str = ""
    work_history: List[WorkExperience] = Field(default_factory=list)
    linkedin: Optional[str] = None
    status: str = "New"
    match_score: float = Field(default=50.0, alias="matchScore")
    job_category: str = "General"
    applied_date: Optional[str] = Field(default=None, alias="appliedDate")
    last_updated: Optional[str] = None
    has_resume: bool = Field(default=False, alias="hasResume")
    evaluation: Optional[AIEvaluation] = None
    
    class Config:
        populate_by_name = True


class CandidateListResponse(BaseModel):
    """Response for paginated candidate list"""
    page: int
    limit: int
    total: int
    candidates: List[CandidateResponse]
    from_cache: bool = False


class StatsResponse(BaseModel):
    """Dashboard statistics response"""
    total: int = Field(description="Total candidates")
    strong: int = Field(description="Strong matches (70%+)")
    partial: int = Field(description="Partial matches (40-69%)")
    weak: int = Field(description="Weak matches (<40%)")
    avg_score: float = Field(alias="avgScore", description="Average match score")
    recent_count: int = Field(alias="recentCount", description="Recent candidates (24h)")
    by_category: Dict[str, int] = Field(default_factory=dict)
    
    class Config:
        populate_by_name = True


class UploadResponse(BaseModel):
    """Response for file upload"""
    success: bool
    message: str
    candidate: Optional[CandidateResponse] = None
    filename: Optional[str] = None
    errors: List[str] = Field(default_factory=list)


class BatchUploadResponse(BaseModel):
    """Response for batch file upload"""
    total_files: int
    successful: int
    failed: int
    results: List[UploadResponse]


class HealthResponse(BaseModel):
    """Health check response"""
    status: str = "healthy"
    timestamp: str
    version: str
    scraper_running: bool = False
    system: Dict[str, Any] = Field(default_factory=dict)
    cache: Dict[str, int] = Field(default_factory=dict)


class JobDescriptionResponse(BaseModel):
    """Response model for job description"""
    id: str
    title: str
    company: Optional[str] = None
    required_skills: List[str] = Field(default_factory=list)
    preferred_skills: List[str] = Field(default_factory=list)
    experience_level: str = ""
    location: Optional[str] = None
    education: Optional[str] = None
    responsibilities: List[str] = Field(default_factory=list)
    description: str = ""
    candidate_count: int = 0
    created_at: Optional[str] = None


class MatchResult(BaseModel):
    """Candidate-job match result"""
    candidate_id: str
    job_description_id: str
    match_score: float = Field(ge=0, le=100)
    status: str
    matched_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    evaluation: Optional[AIEvaluation] = None


# ============================================================================
# Error Response Model
# ============================================================================

class ErrorResponse(BaseModel):
    """Standard error response"""
    error: bool = True
    error_code: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
