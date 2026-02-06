from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime

class Skill(BaseModel):
    name: str
    proficiency: Optional[str] = None
    years: Optional[int] = None

class Education(BaseModel):
    degree: str
    institution: str
    year: str
    field: Optional[str] = None

class WorkExperience(BaseModel):
    title: str
    company: str
    duration: str
    description: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class Candidate(BaseModel):
    id: str
    name: str
    email: EmailStr
    phone: Optional[str] = None
    location: Optional[str] = None
    experience: int
    skills: List[str]
    summary: Optional[str] = None
    education: List[Education]
    work_history: List[WorkExperience]
    resume_url: Optional[str] = None
    applied_date: datetime
    match_score: Optional[float] = None
    status: Optional[str] = None

class JobDescription(BaseModel):
    id: str
    title: str
    company: Optional[str] = None
    required_skills: List[str]
    preferred_skills: List[str]
    experience_level: str
    location: Optional[str] = None
    education: Optional[str] = None
    responsibilities: List[str]
    description: str
    created_at: datetime

class MatchResult(BaseModel):
    candidate_id: str
    job_description_id: str
    match_score: float
    status: str
    matched_skills: List[str]
    missing_skills: List[str]
    evaluation: Optional[dict] = None

class Evaluation(BaseModel):
    strengths: List[str]
    gaps: List[str]
    recommendation: str
    confidence_score: float
