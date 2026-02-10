"""
OpenAI Service for AI-powered candidate matching and analysis
Enhanced with deep candidate analysis, pros/cons, and job-specific scoring.
Serves as EMERGENCY FALLBACK when local LLM (Ollama) is unavailable.
"""
import os
import json
from typing import Dict, List, Optional, Any
from openai import OpenAI
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables")
        
        self.client = OpenAI(api_key=api_key)
        self.model = os.getenv('OPENAI_MODEL', 'gpt-4o')
        self.max_tokens = int(os.getenv('OPENAI_MAX_TOKENS', '2000'))
        self.temperature = float(os.getenv('OPENAI_TEMPERATURE', '0.3'))
    
    # ===========================================================================
    # FALLBACK: Resume Parsing
    # ===========================================================================
    
    def parse_resume(self, text: str) -> Optional[Dict]:
        """Parse resume text using OpenAI — emergency fallback for Ollama."""
        from services.job_taxonomy import get_taxonomy_prompt_text, classify_job_title
        taxonomy = get_taxonomy_prompt_text()
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": """You are an expert resume parser with 15+ years experience in talent acquisition.
Extract ALL information with maximum accuracy. Return ONLY valid JSON.
CRITICAL: Extract every skill, every job position, every degree. Never fabricate data.
For work history, include detailed descriptions with responsibilities and achievements.
For the summary, write 4-6 sentences covering career focus, expertise, achievements, and strengths."""},
                    {"role": "user", "content": f"""Parse this resume and extract ALL information.

RESUME:
{text[:8000]}

JOB TAXONOMY:
{taxonomy}

Return JSON:
{{
    "name": "Full name",
    "email": "email",
    "phone": "phone with country code",
    "location": "City, Country",
    "linkedin": "URL if mentioned",
    "summary": "Detailed 4-6 sentence professional summary covering career focus, key strengths, notable achievements, domain expertise, technical depth, and what they bring to a team.",
    "skills": ["ALL skills, tools, frameworks, languages, methodologies mentioned"],
    "experience_years": 0,
    "work_history": [{{"title": "Exact title", "company": "Company", "period": "MMM YYYY - MMM YYYY or Present", "description": "Detailed 2-4 sentence description of responsibilities, achievements, and technologies used"}}],
    "education": [{{"degree": "Type", "field": "Field", "institution": "Name", "year": "YYYY"}}],
    "certifications": ["certification names with issuing body"],
    "languages": ["languages spoken"],
    "job_category": "EXACT category from taxonomy",
    "job_subcategory": "EXACT subcategory from taxonomy"
}}"""}
                ],
                max_tokens=3000,
                temperature=0.05,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content)
            # Validate category
            if not result.get('job_subcategory'):
                titles = [w.get('title', '') for w in result.get('work_history', []) if isinstance(w, dict)]
                if titles:
                    cat, sub = classify_job_title(titles[0])
                    result['job_category'] = cat
                    result['job_subcategory'] = sub
            logger.info(f"📄 [OpenAI Fallback] Resume parsed: {result.get('name', 'Unknown')}")
            return result
        except Exception as e:
            logger.error(f"OpenAI resume parse error: {e}")
            return None
    
    # ===========================================================================
    # FALLBACK: Email Parsing
    # ===========================================================================
    
    def parse_candidate_email(self, subject: str, body: str, sender: str = "") -> Optional[Dict]:
        """Parse candidate email using OpenAI — emergency fallback."""
        from services.job_taxonomy import get_taxonomy_prompt_text, classify_job_title
        taxonomy = get_taxonomy_prompt_text()
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert recruitment email parser. Return valid JSON only."},
                    {"role": "user", "content": f"""Parse this job application email.

SUBJECT: {subject}
SENDER: {sender}
BODY:
{body[:3000]}

JOB TAXONOMY:
{taxonomy}

Return JSON:
{{
    "name": "Candidate name",
    "email": "email",
    "phone": "phone if mentioned",
    "location": "location if mentioned",
    "skills": ["skill1", "skill2"],
    "experience_years": 0,
    "education": [{{"degree": "", "field": "", "institution": "", "year": ""}}],
    "summary": "Brief summary from email",
    "linkedin": "URL if present",
    "job_applied_for": "Job title they applied for",
    "job_category": "EXACT category from taxonomy",
    "job_subcategory": "EXACT subcategory from taxonomy",
    "is_candidate_email": true
}}"""}
                ],
                max_tokens=1500,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content)
            if not result.get('is_candidate_email', True):
                return None
            # Validate category
            if not result.get('job_subcategory'):
                title = result.get('job_applied_for', '')
                if title:
                    cat, sub = classify_job_title(title)
                    result['job_category'] = cat
                    result['job_subcategory'] = sub
            logger.info(f"📧 [OpenAI Fallback] Email parsed: {result.get('name', 'Unknown')}")
            return result
        except Exception as e:
            logger.error(f"OpenAI email parse error: {e}")
            return None

    def analyze_candidate_match(
        self, 
        candidate_data: Dict, 
        job_description: Dict
    ) -> Dict:
        """
        Use OpenAI to analyze how well a candidate matches a job description
        """
        prompt = f"""Analyze this candidate against the job requirements:

CANDIDATE:
Name: {candidate_data.get('name', 'N/A')}
Experience: {candidate_data.get('experience', 'N/A')} years
Skills: {', '.join(candidate_data.get('skills', []))}
Education: {candidate_data.get('education', 'N/A')}
Summary: {candidate_data.get('summary', 'N/A')}

JOB REQUIREMENTS:
Title: {job_description.get('title', 'N/A')}
Required Skills: {', '.join(job_description.get('required_skills', []))}
Experience: {job_description.get('experience_required', 'N/A')}
Description: {job_description.get('description', 'N/A')}

Provide:
1. Match score (0-100)
2. Top 3 strengths
3. Top 3 gaps
4. Overall recommendation

Format as JSON with keys: score, strengths (array), gaps (array), recommendation (string)"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert HR recruiter analyzing candidate-job fit. Always respond in valid JSON format."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                response_format={"type": "json_object"}
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            return result
            
        except Exception as e:
            return {
                "error": str(e),
                "score": 0,
                "strengths": [],
                "gaps": [],
                "recommendation": "Error analyzing candidate"
            }
    
    def generate_interview_questions(
        self,
        candidate_data: Dict,
        job_description: Dict,
        num_questions: int = 5
    ) -> List[str]:
        """
        Generate tailored interview questions for a candidate
        """
        prompt = f"""Generate {num_questions} technical interview questions for this candidate based on their background and the job requirements.

CANDIDATE:
Skills: {', '.join(candidate_data.get('skills', []))}
Experience: {candidate_data.get('experience', 'N/A')} years

JOB:
Title: {job_description.get('title', 'N/A')}
Required Skills: {', '.join(job_description.get('required_skills', []))}

Return as JSON array of questions: {{"questions": ["question 1", "question 2", ...]}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert technical interviewer. Always respond in valid JSON format."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                response_format={"type": "json_object"}
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            return result.get('questions', [])
            
        except Exception as e:
            return [f"Error generating questions: {str(e)}"]
    
    def summarize_resume(self, resume_text: str) -> str:
        """
        Generate a concise summary of a candidate's resume
        """
        prompt = f"""Summarize this resume in 2-3 sentences, highlighting key qualifications:

{resume_text[:2000]}  # Limit to first 2000 chars

Provide a professional, concise summary."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert resume analyst."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0.5
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            return f"Error summarizing resume: {str(e)}"
    
    def analyze_candidate_deep(self, candidate_data: Dict) -> Dict:
        """
        Deep AI analysis of a candidate - generates comprehensive pros/cons,
        strengths, weaknesses, career trajectory analysis, and hiring recommendations.
        """
        prompt = f"""You are a senior talent acquisition specialist with 15+ years of experience.
Perform a comprehensive analysis of this candidate:

CANDIDATE PROFILE:
- Name: {candidate_data.get('name', 'N/A')}
- Email: {candidate_data.get('email', 'N/A')}
- Location: {candidate_data.get('location', 'N/A')}
- Experience: {candidate_data.get('experience', 0)} years
- Skills: {', '.join(candidate_data.get('skills', [])[:20])}
- Education: {json.dumps(candidate_data.get('education', []))}
- Summary: {candidate_data.get('summary', 'N/A')[:500]}
- Work History: {json.dumps(candidate_data.get('workHistory', candidate_data.get('work_history', []))[:3])}

Provide a thorough analysis in the following JSON format:
{{
    "overall_score": <0-100 integer>,
    "pros": [
        "<specific strength 1 with evidence>",
        "<specific strength 2 with evidence>",
        "<specific strength 3 with evidence>",
        "<specific strength 4 with evidence>",
        "<specific strength 5 with evidence>"
    ],
    "cons": [
        "<specific concern 1 with context>",
        "<specific concern 2 with context>",
        "<specific concern 3 with context>"
    ],
    "skill_assessment": {{
        "technical_depth": <1-10>,
        "soft_skills_indicator": <1-10>,
        "leadership_potential": <1-10>,
        "growth_trajectory": "<ascending/stable/declining>"
    }},
    "career_analysis": {{
        "career_progression": "<rapid/steady/slow/unclear>",
        "job_stability": "<stable/moderate/high_turnover>",
        "role_evolution": "<description of career path>"
    }},
    "hiring_recommendation": {{
        "verdict": "<Strong Hire/Hire/Maybe/No Hire>",
        "confidence": <0-100>,
        "ideal_roles": ["<role 1>", "<role 2>"],
        "red_flags": ["<flag if any>"],
        "interview_focus_areas": ["<area 1>", "<area 2>", "<area 3>"]
    }},
    "summary": "<2-3 sentence executive summary of this candidate>"
}}

Be specific, data-driven, and provide actionable insights."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert talent analyst. Provide detailed, actionable candidate assessments. Always respond in valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1500,
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            result['ai_analyzed'] = True
            result['analysis_model'] = self.model
            return result
            
        except Exception as e:
            logger.error(f"Deep candidate analysis error: {e}")
            return {
                "error": str(e),
                "overall_score": 50,
                "pros": ["Unable to analyze - API error"],
                "cons": ["Analysis unavailable"],
                "ai_analyzed": False
            }
    
    def match_candidates_to_job(
        self, 
        job_description: str, 
        candidates: List[Dict],
        top_n: int = 10
    ) -> Dict:
        """
        Match multiple candidates against a job description.
        Returns ranked list with AI scores specific to this JD.
        """
        # Prepare candidate summaries for context
        candidate_summaries = []
        for i, c in enumerate(candidates[:30]):  # Limit to 30 for token limits
            summary = f"""
Candidate {i+1} (ID: {c.get('id', 'N/A')}):
- Name: {c.get('name', 'N/A')}
- Experience: {c.get('experience', 0)} years
- Skills: {', '.join(c.get('skills', [])[:15])}
- Location: {c.get('location', 'N/A')}
- Summary: {c.get('summary', 'N/A')[:200]}
"""
            candidate_summaries.append(summary)
        
        prompt = f"""You are an expert technical recruiter. Analyze and rank candidates for this position.

JOB DESCRIPTION:
{job_description[:2000]}

CANDIDATES TO EVALUATE:
{''.join(candidate_summaries)}

Rank ALL candidates from best to worst fit for this specific job.
Return JSON in this format:
{{
    "job_analysis": {{
        "key_requirements": ["<req 1>", "<req 2>", "<req 3>"],
        "must_have_skills": ["<skill 1>", "<skill 2>"],
        "nice_to_have": ["<skill 1>", "<skill 2>"],
        "experience_level": "<junior/mid/senior/lead>",
        "role_type": "<description>"
    }},
    "rankings": [
        {{
            "rank": 1,
            "candidate_id": "<id>",
            "candidate_name": "<name>",
            "job_fit_score": <0-100>,
            "match_reasons": ["<reason 1>", "<reason 2>", "<reason 3>"],
            "gaps": ["<gap if any>"],
            "recommendation": "<Strong Match/Good Match/Partial Match/Weak Match>"
        }}
    ],
    "summary": {{
        "total_evaluated": <number>,
        "strong_matches": <number>,
        "recommendation": "<brief hiring recommendation>"
    }}
}}

Be thorough and rank ALL candidates provided."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert recruiter. Match candidates to jobs precisely. Always respond in valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2500,
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Ensure rankings is limited to top_n
            if 'rankings' in result:
                result['rankings'] = result['rankings'][:top_n]
            
            return result
            
        except Exception as e:
            logger.error(f"Job matching error: {e}")
            return {
                "error": str(e),
                "rankings": [],
                "job_analysis": {},
                "summary": {"total_evaluated": 0, "strong_matches": 0}
            }
    
    def generate_candidate_comparison(
        self,
        candidates: List[Dict],
        job_description: Optional[str] = None
    ) -> Dict:
        """
        Generate side-by-side comparison of candidates with pros/cons for each.
        """
        candidate_details = []
        for c in candidates[:5]:  # Compare up to 5 candidates
            detail = f"""
{c.get('name', 'Unknown')}:
- Experience: {c.get('experience', 0)} years
- Skills: {', '.join(c.get('skills', [])[:10])}
- Current/Last Role: {c.get('workHistory', [{}])[0].get('title', 'N/A') if c.get('workHistory') else 'N/A'}
- Education: {c.get('education', [{}])[0].get('degree', 'N/A') if c.get('education') else 'N/A'}
"""
            candidate_details.append(detail)
        
        jd_context = f"\n\nJOB CONTEXT:\n{job_description[:500]}" if job_description else ""
        
        prompt = f"""Compare these candidates for hiring decision:{jd_context}

CANDIDATES:
{''.join(candidate_details)}

Provide comparison in JSON format:
{{
    "comparison_matrix": [
        {{
            "name": "<candidate name>",
            "overall_rank": <1-5>,
            "score": <0-100>,
            "key_strengths": ["<str 1>", "<str 2>"],
            "key_weaknesses": ["<weak 1>"],
            "best_for": "<what role/situation they're best for>",
            "risk_level": "<low/medium/high>"
        }}
    ],
    "head_to_head": {{
        "winner": "<name of best candidate>",
        "reasoning": "<why they're the best choice>",
        "runner_up": "<second best>",
        "close_call": <true/false if decision was difficult>
    }},
    "recommendation": "<Final hiring recommendation with reasoning>"
}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert at comparing job candidates. Be objective and specific."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1500,
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            return {"error": str(e), "comparison_matrix": [], "recommendation": "Error generating comparison"}
    
    def chat_with_ai(self, message: str, context: Optional[str] = None, candidates_data: Optional[List[Dict]] = None) -> str:
        """
        Enhanced chat functionality with candidate context awareness
        """
        system_prompt = """You are an elite AI recruitment assistant for a UAE-based hiring platform. 
You have deep expertise in:
- Technical and non-technical hiring
- Candidate evaluation and scoring
- Job market trends in UAE/GCC
- Interview strategies and questions
- Salary benchmarking
- Skill gap analysis

Be specific, data-driven, and actionable in your responses.
When discussing candidates, reference specific data points.
Always provide concrete recommendations."""

        messages = [{"role": "system", "content": system_prompt}]
        
        if context:
            messages.append({"role": "system", "content": f"Database Context: {context}"})
        
        if candidates_data:
            candidate_context = f"Available Candidates ({len(candidates_data)} total):\n"
            for c in candidates_data[:10]:
                candidate_context += f"- {c.get('name')}: {c.get('experience', 0)}yrs exp, Skills: {', '.join(c.get('skills', [])[:5])}\n"
            messages.append({"role": "system", "content": candidate_context})
        
        messages.append({"role": "user", "content": message})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            return f"I apologize, but I encountered an error: {str(e)}"

# Singleton instance
_openai_service = None

def get_openai_service() -> Optional[OpenAIService]:
    """Get or create OpenAI service instance"""
    global _openai_service
    if _openai_service is None:
        try:
            _openai_service = OpenAIService()
        except ValueError as e:
            print(f"OpenAI service not available: {e}")
            return None
    return _openai_service
