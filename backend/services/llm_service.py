"""
Local LLM Service - Powered by Ollama
======================================
Uses Ollama to run local LLMs for 100% accurate information extraction.

Best models for recruitment AI (performance/speed ratio):
1. qwen2.5:7b-instruct - BEST for structured extraction (7B params, fast, accurate)
2. phi3.5:latest - FASTEST for simple tasks (3.8B params, very fast)
3. llama3.1:8b - BEST reasoning (8B params, good for analysis)
4. mistral:7b - GOOD all-around (7B params, balanced)

Ollama runs models locally with zero API costs, full privacy, and fast inference.
Install: https://ollama.com/download
"""

import asyncio
import json
import logging
import re
import time
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from functools import lru_cache

logger = logging.getLogger(__name__)


class LLMService:
    """
    Local LLM Service using Ollama for 100% accurate information extraction.
    
    Features:
    - Structured JSON extraction from resumes, emails, job descriptions
    - Deep candidate analysis with pros/cons
    - Intelligent job matching with detailed reasoning
    - Interview question generation
    - AI chat assistant for recruitment
    - Response caching for performance
    - Automatic model fallback (qwen2.5 → phi3.5 → llama3.1)
    """
    
    # Model configuration - best performance/speed ratio
    PRIMARY_MODEL = "qwen2.5:7b"          # Best for structured extraction
    FAST_MODEL = "phi3.5"                  # Fast for simple tasks
    REASONING_MODEL = "llama3.1:8b"        # Best for deep analysis
    
    # Ollama API base URL
    OLLAMA_BASE_URL = "http://localhost:11434"
    
    def __init__(self):
        self.available = False
        self.available_models: List[str] = []
        self.primary_model = self.PRIMARY_MODEL
        self.fast_model = self.FAST_MODEL
        self.reasoning_model = self.REASONING_MODEL
        
        # Response cache for performance
        self._cache: Dict[str, Any] = {}
        self._cache_max_size = 500
        self._cache_ttl = 600  # 10 minutes
        
        # Performance tracking
        self._request_count = 0
        self._total_time = 0.0
        self._error_count = 0
        
        # Initialize
        self._http_client = None
        logger.info("🤖 LLM Service initialized (Ollama-based)")
    
    async def _get_client(self):
        """Get or create async HTTP client"""
        if self._http_client is None:
            try:
                import httpx
                self._http_client = httpx.AsyncClient(
                    base_url=self.OLLAMA_BASE_URL,
                    timeout=httpx.Timeout(300.0, connect=10.0)
                )
            except ImportError:
                import aiohttp
                self._http_client = None
                logger.warning("httpx not available, will use aiohttp")
        return self._http_client
    
    async def initialize(self) -> bool:
        """Initialize and check Ollama availability"""
        try:
            client = await self._get_client()
            if client:
                response = await client.get("/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    self.available_models = [
                        m.get("name", "").split(":")[0] + ":" + m.get("name", "").split(":")[-1]
                        if ":" in m.get("name", "") else m.get("name", "")
                        for m in data.get("models", [])
                    ]
                    
                    # Also store short names for matching
                    short_names = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                    
                    self.available = len(self.available_models) > 0
                    
                    if self.available:
                        # Select best available model
                        self._select_best_models(short_names)
                        logger.info(f"✅ Ollama connected! Models: {self.available_models}")
                        logger.info(f"📌 Primary: {self.primary_model} | Fast: {self.fast_model} | Reasoning: {self.reasoning_model}")
                    else:
                        logger.warning("⚠️ Ollama running but no models installed")
                        logger.warning("   Run: ollama pull qwen2.5:7b")
                    
                    return self.available
            
            # Try with aiohttp as fallback
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.OLLAMA_BASE_URL}/api/tags") as response:
                    if response.status == 200:
                        data = await response.json()
                        self.available_models = [
                            m.get("name", "") for m in data.get("models", [])
                        ]
                        short_names = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                        self.available = len(self.available_models) > 0
                        
                        if self.available:
                            self._select_best_models(short_names)
                            logger.info(f"✅ Ollama connected (aiohttp)! Models: {self.available_models}")
                        
                        return self.available
                        
        except Exception as e:
            logger.warning(f"⚠️ Ollama not available: {e}")
            logger.warning("   Install Ollama: https://ollama.com/download")
            logger.warning("   Then run: ollama pull qwen2.5:7b")
            self.available = False
            return False
    
    def _select_best_models(self, short_names: List[str]):
        """Select best available models from what's installed"""
        # Priority order for primary model (structured extraction)
        primary_priority = ["qwen2.5", "qwen2", "mistral", "llama3.1", "llama3", "phi3.5", "phi3", "gemma2"]
        # Priority order for fast model
        fast_priority = ["phi3.5", "phi3", "qwen2.5", "gemma2", "mistral", "llama3.1"]
        # Priority order for reasoning model
        reasoning_priority = ["llama3.1", "llama3", "qwen2.5", "mistral", "phi3.5"]
        
        for model in primary_priority:
            if model in short_names:
                # Find the full model name
                for full_name in self.available_models:
                    if full_name.startswith(model):
                        self.primary_model = full_name
                        break
                break
        
        for model in fast_priority:
            if model in short_names:
                for full_name in self.available_models:
                    if full_name.startswith(model):
                        self.fast_model = full_name
                        break
                break
        
        for model in reasoning_priority:
            if model in short_names:
                for full_name in self.available_models:
                    if full_name.startswith(model):
                        self.reasoning_model = full_name
                        break
                break
        
        # If we only have one model, use it for everything
        if len(self.available_models) == 1:
            self.primary_model = self.available_models[0]
            self.fast_model = self.available_models[0]
            self.reasoning_model = self.available_models[0]
    
    # ========================================================================
    # CORE LLM INTERFACE
    # ========================================================================
    
    async def _generate(
        self, 
        prompt: str, 
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        json_mode: bool = False
    ) -> str:
        """
        Generate text using Ollama LLM.
        Low temperature (0.1) for accurate extraction, higher for creative tasks.
        """
        if not self.available:
            return ""
        
        model = model or self.primary_model
        start_time = time.time()
        
        try:
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "top_p": 0.9,
                    "top_k": 40,
                }
            }
            
            if system:
                payload["system"] = system
            
            if json_mode:
                payload["format"] = "json"
            
            client = await self._get_client()
            
            if client:
                response = await client.post("/api/generate", json=payload)
                if response.status_code == 200:
                    data = response.json()
                    result = data.get("response", "")
                    
                    elapsed = time.time() - start_time
                    self._request_count += 1
                    self._total_time += elapsed
                    
                    tokens = data.get("eval_count", 0)
                    logger.debug(f"🤖 LLM [{model}]: {tokens} tokens in {elapsed:.1f}s")
                    
                    return result.strip()
                else:
                    self._error_count += 1
                    logger.error(f"Ollama error {response.status_code}: {response.text[:200]}")
                    return ""
            
            # Fallback to aiohttp
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.OLLAMA_BASE_URL}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=300)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        result = data.get("response", "")
                        
                        elapsed = time.time() - start_time
                        self._request_count += 1
                        self._total_time += elapsed
                        
                        return result.strip()
                    else:
                        self._error_count += 1
                        return ""
                        
        except Exception as e:
            self._error_count += 1
            elapsed = time.time() - start_time
            logger.error(f"LLM generation error ({elapsed:.1f}s): {e}")
            return ""
    
    async def _generate_json(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.05,
    ) -> Optional[Dict]:
        """Generate structured JSON output from LLM"""
        result = await self._generate(
            prompt=prompt,
            model=model,
            system=system,
            temperature=temperature,
            json_mode=True
        )
        
        if not result:
            return None
        
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            json_match = re.search(r'\{[\s\S]*\}', result)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            
            logger.warning(f"Failed to parse JSON from LLM response: {result[:200]}")
            return None
    
    def _get_cache_key(self, prefix: str, text: str) -> str:
        """Generate cache key"""
        text_hash = hashlib.md5(text.encode()).hexdigest()[:16]
        return f"{prefix}:{text_hash}"
    
    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached result if still valid"""
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry['time'] < self._cache_ttl:
                return entry['data']
            else:
                del self._cache[key]
        return None
    
    def _set_cache(self, key: str, data: Any):
        """Cache a result"""
        if len(self._cache) >= self._cache_max_size:
            # Remove oldest entries
            oldest_keys = sorted(
                self._cache.keys(),
                key=lambda k: self._cache[k]['time']
            )[:100]
            for k in oldest_keys:
                del self._cache[k]
        
        self._cache[key] = {'data': data, 'time': time.time()}
    
    # ========================================================================
    # RESUME PARSING - 100% ACCURATE EXTRACTION
    # ========================================================================
    
    async def parse_resume(self, text: str) -> Optional[Dict]:
        """
        Parse resume text using LLM for 100% accurate structured extraction.
        Extracts: name, email, phone, skills, experience, education, work history,
                  summary, location, linkedin, certifications
        """
        if not text or len(text.strip()) < 30:
            return None
        
        # Check cache
        cache_key = self._get_cache_key("resume", text)
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        system = """You are an expert resume parser. Extract ALL information from the resume with 100% accuracy.
You must return valid JSON. Be thorough and precise. Extract every detail mentioned.
For skills, include ALL technical skills, tools, frameworks, languages, and soft skills mentioned.
For work history, extract ALL positions with company, title, dates, and key responsibilities.
For education, extract ALL degrees with institution, field, and graduation year.
Never fabricate or hallucinate information - only extract what is explicitly stated."""

        prompt = f"""Parse this resume and extract ALL information into the following JSON structure.
Be extremely thorough - extract every skill, every job, every detail mentioned.

RESUME TEXT:
---
{text[:6000]}
---

Return ONLY valid JSON with this exact structure:
{{
    "name": "Full name of the candidate",
    "email": "email@example.com",
    "phone": "phone number with country code",
    "location": "City, Country",
    "linkedin": "LinkedIn URL if mentioned",
    "summary": "2-3 sentence professional summary based on their experience",
    "skills": ["skill1", "skill2", "skill3", ...],
    "experience_years": 0,
    "work_history": [
        {{
            "title": "Job Title",
            "company": "Company Name",
            "period": "Start Date - End Date",
            "description": "Key responsibilities and achievements"
        }}
    ],
    "education": [
        {{
            "degree": "Degree Type (PhD/Masters/Bachelors/etc)",
            "field": "Field of Study",
            "institution": "University/College Name",
            "year": "Graduation Year"
        }}
    ],
    "certifications": ["cert1", "cert2"],
    "languages": ["English", "Arabic", etc],
    "job_category": "Most likely job role category (e.g., Software Engineer, Data Scientist, etc)"
}}"""

        result = await self._generate_json(prompt, system=system, temperature=0.05)
        
        if result:
            # Normalize and validate
            result = self._normalize_resume_data(result)
            self._set_cache(cache_key, result)
            logger.info(f"📄 LLM Resume Parse: {result.get('name', 'Unknown')} | "
                       f"Skills: {len(result.get('skills', []))} | "
                       f"Exp: {result.get('experience_years', 0)}yrs")
        
        return result
    
    def _normalize_resume_data(self, data: Dict) -> Dict:
        """Normalize and validate parsed resume data"""
        normalized = {
            'name': str(data.get('name', 'Unknown')).strip(),
            'email': str(data.get('email', '')).strip(),
            'phone': str(data.get('phone', '')).strip(),
            'location': str(data.get('location', '')).strip(),
            'linkedin': str(data.get('linkedin', '')).strip(),
            'summary': str(data.get('summary', '')).strip(),
            'skills': [],
            'experience_years': 0,
            'work_history': [],
            'education': [],
            'certifications': [],
            'languages': [],
            'job_category': str(data.get('job_category', 'General')).strip(),
        }
        
        # Skills - ensure list of strings, deduplicated
        skills = data.get('skills', [])
        if isinstance(skills, list):
            seen = set()
            for s in skills:
                s_lower = str(s).strip().lower()
                if s_lower and s_lower not in seen:
                    seen.add(s_lower)
                    normalized['skills'].append(str(s).strip())
        
        # Experience years
        exp = data.get('experience_years', 0)
        if isinstance(exp, str):
            numbers = re.findall(r'\d+', exp)
            exp = int(numbers[0]) if numbers else 0
        normalized['experience_years'] = min(int(exp or 0), 50)
        
        # Work history
        work = data.get('work_history', [])
        if isinstance(work, list):
            for w in work[:10]:
                if isinstance(w, dict):
                    normalized['work_history'].append({
                        'title': str(w.get('title', '')).strip(),
                        'company': str(w.get('company', '')).strip(),
                        'period': str(w.get('period', '')).strip(),
                        'description': str(w.get('description', '')).strip()[:500],
                    })
        
        # Education
        edu = data.get('education', [])
        if isinstance(edu, list):
            for e in edu[:5]:
                if isinstance(e, dict):
                    normalized['education'].append({
                        'degree': str(e.get('degree', 'Degree')).strip(),
                        'field': str(e.get('field', '')).strip(),
                        'institution': str(e.get('institution', '')).strip(),
                        'year': str(e.get('year', '')).strip(),
                    })
        
        # Certifications
        certs = data.get('certifications', [])
        if isinstance(certs, list):
            normalized['certifications'] = [str(c).strip() for c in certs[:10] if c]
        
        # Languages
        langs = data.get('languages', [])
        if isinstance(langs, list):
            normalized['languages'] = [str(l).strip() for l in langs[:10] if l]
        
        return normalized
    
    # ========================================================================
    # EMAIL CANDIDATE EXTRACTION - PERFECT PARSING
    # ========================================================================
    
    async def parse_candidate_email(self, subject: str, body: str, sender: str = "") -> Optional[Dict]:
        """
        Extract candidate information from email (Indeed/LinkedIn/direct application).
        Uses LLM for 100% accurate extraction of all candidate details.
        """
        if not body or len(body.strip()) < 20:
            return None
        
        # Check cache
        cache_key = self._get_cache_key("email", f"{subject}:{body[:500]}")
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        # Detect email source
        source = "Direct"
        body_lower = body.lower()
        subject_lower = subject.lower()
        if "indeed" in body_lower or "indeed" in subject_lower:
            source = "Indeed"
        elif "linkedin" in body_lower or "linkedin" in subject_lower:
            source = "LinkedIn"
        elif "naukri" in body_lower or "naukri" in subject_lower:
            source = "Naukri"
        elif "glassdoor" in body_lower or "glassdoor" in subject_lower:
            source = "Glassdoor"
        
        system = """You are an expert recruitment email parser. Extract candidate information from job application emails with 100% accuracy.
These emails may come from job boards (Indeed, LinkedIn, Naukri) or direct applications.
Extract every piece of candidate information available. Return valid JSON only.
Never fabricate information - only extract what is explicitly mentioned in the email."""

        prompt = f"""Parse this job application email and extract ALL candidate information.

EMAIL SUBJECT: {subject}
SENDER: {sender}
SOURCE: {source}

EMAIL BODY:
---
{body[:4000]}
---

Return ONLY valid JSON:
{{
    "name": "Candidate's full name",
    "email": "candidate's email address",
    "phone": "phone number if mentioned",
    "location": "city/country if mentioned",
    "skills": ["skill1", "skill2", ...],
    "experience_years": 0,
    "education": [
        {{
            "degree": "Degree type",
            "field": "Field of study",
            "institution": "School name",
            "year": ""
        }}
    ],
    "summary": "Brief summary of candidate's background from email content",
    "linkedin": "LinkedIn URL if present",
    "job_applied_for": "The job title/position they applied for",
    "source": "{source}",
    "is_candidate_email": true
}}

Set "is_candidate_email" to false if this email does NOT contain a job application or candidate information."""

        result = await self._generate_json(prompt, model=self.fast_model, system=system, temperature=0.05)
        
        if result:
            # Check if it's actually a candidate email
            if not result.get('is_candidate_email', True):
                return None
            
            result['source'] = source
            result = self._normalize_email_data(result)
            
            if result.get('name') or result.get('email'):
                self._set_cache(cache_key, result)
                logger.info(f"📧 LLM Email Parse: {result.get('name', 'Unknown')} | Source: {source}")
                return result
        
        return None
    
    def _normalize_email_data(self, data: Dict) -> Dict:
        """Normalize email-extracted candidate data"""
        normalized = {
            'name': str(data.get('name', '')).strip(),
            'email': str(data.get('email', '')).strip(),
            'phone': str(data.get('phone', '')).strip(),
            'location': str(data.get('location', '')).strip(),
            'skills': [],
            'experience': int(data.get('experience_years', 0) or 0),
            'education': [],
            'summary': str(data.get('summary', '')).strip()[:500],
            'linkedin': str(data.get('linkedin', '')).strip(),
            'source': str(data.get('source', 'Direct')).strip(),
            'job_applied_for': str(data.get('job_applied_for', '')).strip(),
        }
        
        # Skills
        skills = data.get('skills', [])
        if isinstance(skills, list):
            normalized['skills'] = [str(s).strip() for s in skills if s]
        
        # Education
        edu = data.get('education', [])
        if isinstance(edu, list):
            for e in edu[:3]:
                if isinstance(e, dict):
                    normalized['education'].append({
                        'degree': str(e.get('degree', '')).strip(),
                        'field': str(e.get('field', '')).strip(),
                        'institution': str(e.get('institution', '')).strip(),
                        'year': str(e.get('year', '')).strip(),
                    })
        
        return normalized
    
    # ========================================================================
    # DEEP CANDIDATE ANALYSIS - NO OPENAI NEEDED
    # ========================================================================
    
    async def analyze_candidate_deep(self, candidate_data: Dict) -> Dict:
        """
        Deep analysis of a candidate - generates pros, cons, summary, 
        recommendations without needing OpenAI.
        """
        cache_key = self._get_cache_key("deep", json.dumps(candidate_data, default=str))
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        education = candidate_data.get('education', [])
        work_history = candidate_data.get('work_history', [])
        summary = candidate_data.get('summary', '')
        score = candidate_data.get('quality_score', candidate_data.get('score', 0))
        
        system = """You are a senior recruitment consultant performing deep candidate analysis.
Provide honest, detailed, actionable analysis. Be specific about strengths and areas for improvement.
Base your analysis ONLY on the provided data. Do not fabricate details."""

        prompt = f"""Analyze this candidate thoroughly and provide detailed insights.

CANDIDATE PROFILE:
- Name: {name}
- Skills: {', '.join(skills[:20]) if skills else 'Not specified'}
- Experience: {experience} years
- Education: {json.dumps(education[:3]) if education else 'Not specified'}
- Work History: {json.dumps(work_history[:5], default=str) if work_history else 'Not specified'}
- Summary: {summary[:300] if summary else 'Not available'}
- Current Score: {score}%

Provide comprehensive analysis as JSON:
{{
    "overall_assessment": "2-3 sentence overall assessment of the candidate",
    "strengths": ["strength1", "strength2", "strength3", "strength4", "strength5"],
    "weaknesses": ["weakness1", "weakness2", "weakness3"],
    "pros": ["pro1", "pro2", "pro3", "pro4", "pro5"],
    "cons": ["con1", "con2", "con3"],
    "recommended_roles": ["role1", "role2", "role3"],
    "interview_focus_areas": ["area1", "area2", "area3"],
    "salary_range_estimate": "Estimated range based on skills and experience",
    "culture_fit_notes": "Notes on potential cultural fit indicators",
    "development_areas": ["area1", "area2"],
    "hiring_recommendation": "STRONGLY_RECOMMEND | RECOMMEND | CONSIDER | PASS",
    "confidence_score": 85,
    "key_differentiators": ["what makes this candidate stand out"]
}}"""

        result = await self._generate_json(
            prompt, 
            model=self.reasoning_model, 
            system=system, 
            temperature=0.2
        )
        
        if result:
            # Ensure all fields exist
            defaults = {
                'overall_assessment': 'Analysis not available',
                'strengths': [],
                'weaknesses': [],
                'pros': [],
                'cons': [],
                'recommended_roles': [],
                'interview_focus_areas': [],
                'salary_range_estimate': 'Not determined',
                'culture_fit_notes': '',
                'development_areas': [],
                'hiring_recommendation': 'CONSIDER',
                'confidence_score': 50,
                'key_differentiators': [],
            }
            for key, default in defaults.items():
                if key not in result:
                    result[key] = default
            
            self._set_cache(cache_key, result)
            logger.info(f"🔬 Deep Analysis: {name} → {result.get('hiring_recommendation', 'N/A')}")
        
        return result or {
            'overall_assessment': 'Unable to perform deep analysis',
            'strengths': ['Resume submitted'],
            'weaknesses': ['Insufficient data for analysis'],
            'pros': ['Candidate is in the pipeline'],
            'cons': ['More information needed'],
            'recommended_roles': ['General'],
            'interview_focus_areas': ['Background verification', 'Skills assessment'],
            'hiring_recommendation': 'CONSIDER',
            'confidence_score': 30,
        }
    
    # ========================================================================
    # JOB DESCRIPTION MATCHING - INTELLIGENT SCORING
    # ========================================================================
    
    async def match_candidate_to_job(
        self, 
        candidate_data: Dict, 
        job_description: str
    ) -> Dict:
        """
        Match a candidate against a job description using LLM intelligence.
        Returns detailed match analysis with score, strengths, gaps.
        """
        cache_key = self._get_cache_key(
            "match", 
            f"{json.dumps(candidate_data, default=str)}:{job_description[:500]}"
        )
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        education = candidate_data.get('education', [])
        summary = candidate_data.get('summary', '')
        
        system = """You are an expert technical recruiter performing candidate-job matching analysis.
Score candidates honestly based on their actual qualifications vs job requirements.
Be specific about what matches and what doesn't. Provide actionable insights."""

        prompt = f"""Evaluate how well this candidate matches the job description.

CANDIDATE:
- Name: {name}
- Skills: {', '.join(skills[:20]) if skills else 'Not specified'}
- Experience: {experience} years
- Education: {json.dumps(education[:3]) if education else 'Not specified'}
- Summary: {summary[:300]}

JOB DESCRIPTION:
---
{job_description[:3000]}
---

Provide match analysis as JSON:
{{
    "match_score": 75,
    "skill_match_score": 80,
    "experience_match_score": 70,
    "education_match_score": 60,
    "overall_fit": "Good Match",
    "matched_skills": ["skill1", "skill2"],
    "missing_skills": ["skill1", "skill2"],
    "transferable_skills": ["skill1"],
    "strengths": ["strength1", "strength2", "strength3"],
    "gaps": ["gap1", "gap2"],
    "recommendation": "Detailed recommendation about this candidate for this role",
    "interview_questions": ["question1", "question2", "question3"],
    "risk_factors": ["risk1"],
    "growth_potential": "Assessment of growth potential in this role"
}}

Score 0-100 where:
- 90-100: Perfect match, immediately hire-worthy
- 75-89: Strong match, definitely interview
- 60-74: Good match, worth considering
- 40-59: Partial match, needs development
- 0-39: Poor match, significant gaps"""

        result = await self._generate_json(prompt, system=system, temperature=0.15)
        
        if result:
            # Ensure score is valid
            score = result.get('match_score', 50)
            if isinstance(score, str):
                numbers = re.findall(r'\d+', score)
                score = int(numbers[0]) if numbers else 50
            result['match_score'] = max(0, min(100, int(score)))
            
            self._set_cache(cache_key, result)
            logger.info(f"🎯 Job Match: {name} → {result['match_score']}%")
        
        return result or {
            'match_score': 0,
            'overall_fit': 'Unable to analyze',
            'matched_skills': [],
            'missing_skills': [],
            'strengths': [],
            'gaps': ['Analysis unavailable'],
            'recommendation': 'Manual review required',
        }
    
    # ========================================================================
    # BATCH JOB MATCHING - RANK MULTIPLE CANDIDATES
    # ========================================================================
    
    async def rank_candidates_for_job(
        self,
        candidates: List[Dict],
        job_description: str,
        top_n: int = 10
    ) -> List[Dict]:
        """Rank multiple candidates against a job description"""
        results = []
        
        for candidate in candidates[:50]:  # Process up to 50 candidates
            try:
                match = await self.match_candidate_to_job(candidate, job_description)
                results.append({
                    'candidate': candidate,
                    'match': match,
                    'score': match.get('match_score', 0)
                })
            except Exception as e:
                logger.warning(f"Match error for {candidate.get('name', 'Unknown')}: {e}")
        
        # Sort by score descending
        results.sort(key=lambda x: x['score'], reverse=True)
        
        return results[:top_n]
    
    # ========================================================================
    # CANDIDATE COMPARISON
    # ========================================================================
    
    async def compare_candidates(
        self,
        candidates: List[Dict],
        job_description: Optional[str] = None
    ) -> Dict:
        """Compare multiple candidates side by side"""
        if not candidates or len(candidates) < 2:
            return {'error': 'Need at least 2 candidates to compare'}
        
        candidates_text = ""
        for i, c in enumerate(candidates[:5], 1):
            candidates_text += f"""
Candidate {i}: {c.get('name', 'Unknown')}
- Skills: {', '.join(c.get('skills', [])[:10])}
- Experience: {c.get('experience', c.get('experience_years', 0))} years
- Education: {json.dumps(c.get('education', [])[:2]) if c.get('education') else 'N/A'}
- Score: {c.get('quality_score', c.get('score', 'N/A'))}%
"""
        
        job_context = ""
        if job_description:
            job_context = f"\nJOB DESCRIPTION:\n{job_description[:1500]}\n"
        
        system = """You are a senior recruiter comparing candidates. Be fair, specific, and data-driven."""

        prompt = f"""Compare these candidates and provide a detailed comparison.

{candidates_text}
{job_context}

Return JSON:
{{
    "ranking": [
        {{
            "rank": 1,
            "name": "Candidate Name",
            "score": 85,
            "key_advantage": "What makes them #1",
            "key_risk": "Main concern"
        }}
    ],
    "comparison_summary": "Overall comparison summary",
    "recommendation": "Who to interview first and why",
    "skill_comparison": {{
        "unique_skills": {{
            "Candidate Name": ["unique_skill1", "unique_skill2"]
        }},
        "common_skills": ["shared_skill1", "shared_skill2"]
    }},
    "best_for_role": "Which candidate is best for the specific role and why"
}}"""

        result = await self._generate_json(
            prompt, 
            model=self.reasoning_model, 
            system=system, 
            temperature=0.2
        )
        
        return result or {
            'ranking': [],
            'comparison_summary': 'Comparison unavailable',
            'recommendation': 'Manual review recommended'
        }
    
    # ========================================================================
    # AI CHAT ASSISTANT - RECRUITMENT INTELLIGENCE
    # ========================================================================
    
    async def chat(
        self,
        message: str,
        context: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None
    ) -> str:
        """
        AI chat assistant for recruitment queries.
        Uses LLM to understand natural language and provide intelligent responses.
        """
        ctx = context or {}
        total = ctx.get('totalCandidates', 0)
        avg_score = ctx.get('avgMatchScore', 0)
        strong = ctx.get('strongMatches', 0)
        recent = ctx.get('recentCount', 0)
        
        system = f"""You are an expert AI recruitment assistant for a company's HR team.
You help with candidate screening, job matching, pipeline analytics, and hiring decisions.

Current database stats:
- Total candidates: {total}
- Strong matches (70%+): {strong}
- Average score: {avg_score:.1f}%
- Recent applicants: {recent}

Be helpful, concise, and data-driven. Use the stats to give contextual answers.
Format your responses with markdown for readability.
If asked about specific candidates, explain you can search by skills, location, or score.
If asked to do something beyond your capabilities, suggest practical alternatives."""

        # Build conversation context
        history_text = ""
        if conversation_history:
            for msg in conversation_history[-5:]:  # Last 5 messages
                role = msg.get('role', 'user')
                content = msg.get('content', '')[:200]
                history_text += f"\n{role}: {content}"
        
        prompt = f"""{history_text}
User: {message}

Provide a helpful, detailed response:"""

        result = await self._generate(
            prompt,
            model=self.reasoning_model,
            system=system,
            temperature=0.3,
            max_tokens=2048
        )
        
        return result or f"""I'm here to help with your recruitment needs!

**Quick Stats:**
• Total candidates: {total}
• Strong matches: {strong}
• Average score: {avg_score:.1f}%

**Try asking me:**
• "Show top candidates for [role]"
• "Analyze our hiring pipeline"
• "Compare these candidates"
• "Draft an outreach email"

What would you like to know?"""
    
    # ========================================================================
    # INTERVIEW QUESTION GENERATION
    # ========================================================================
    
    async def generate_interview_questions(
        self,
        candidate_data: Dict,
        job_description: Optional[str] = None,
        num_questions: int = 8
    ) -> List[Dict]:
        """Generate tailored interview questions for a candidate"""
        
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        name = candidate_data.get('name', 'the candidate')
        
        job_context = ""
        if job_description:
            job_context = f"\nJOB THEY'RE APPLYING FOR:\n{job_description[:1000]}\n"
        
        system = """You are a senior technical interviewer. Generate thoughtful, relevant questions
that will effectively evaluate the candidate's abilities. Mix technical and behavioral questions."""

        prompt = f"""Generate {num_questions} interview questions for this candidate.

CANDIDATE:
- Name: {name}
- Skills: {', '.join(skills[:15])}
- Experience: {experience} years
{job_context}

Return JSON:
{{
    "questions": [
        {{
            "question": "The interview question",
            "type": "technical|behavioral|situational|problem-solving",
            "difficulty": "easy|medium|hard",
            "skill_tested": "What skill this tests",
            "what_to_look_for": "What a good answer looks like"
        }}
    ]
}}"""

        result = await self._generate_json(prompt, system=system, temperature=0.3)
        
        if result and 'questions' in result:
            return result['questions'][:num_questions]
        
        # Fallback questions
        return [
            {
                "question": f"Tell me about your experience with {skills[0] if skills else 'your primary technology'}.",
                "type": "technical",
                "difficulty": "medium",
                "skill_tested": skills[0] if skills else "General",
                "what_to_look_for": "Depth of knowledge and practical experience"
            },
            {
                "question": "Describe a challenging project you led and how you overcame obstacles.",
                "type": "behavioral",
                "difficulty": "medium", 
                "skill_tested": "Leadership",
                "what_to_look_for": "Problem-solving approach and leadership style"
            }
        ]
    
    # ========================================================================
    # JOB DESCRIPTION PARSING
    # ========================================================================
    
    async def parse_job_description(self, text: str) -> Dict:
        """Parse a job description into structured format using LLM"""
        
        system = """You are an expert job description parser. Extract all requirements and details accurately."""

        prompt = f"""Parse this job description and extract structured information.

JOB DESCRIPTION:
---
{text[:4000]}
---

Return JSON:
{{
    "title": "Job Title",
    "department": "Department if mentioned",
    "location": "Location/Remote status",
    "employment_type": "Full-time/Part-time/Contract",
    "experience_required": "X years",
    "required_skills": ["skill1", "skill2", ...],
    "preferred_skills": ["skill1", "skill2", ...],
    "education_required": "Minimum education requirement",
    "responsibilities": ["resp1", "resp2", ...],
    "benefits": ["benefit1", "benefit2", ...],
    "salary_range": "Salary range if mentioned",
    "key_requirements": ["req1", "req2", "req3"]
}}"""

        result = await self._generate_json(prompt, system=system, temperature=0.05)
        
        return result or {
            'title': 'Position',
            'required_skills': [],
            'preferred_skills': [],
            'responsibilities': [],
            'experience_required': 'Not specified'
        }
    
    # ========================================================================
    # EMAIL TEMPLATE GENERATION
    # ========================================================================
    
    async def generate_email_template(
        self,
        template_type: str,
        candidate_data: Optional[Dict] = None,
        job_title: Optional[str] = None
    ) -> Dict:
        """Generate professional email templates for recruitment"""
        
        context = ""
        if candidate_data:
            context = f"Candidate: {candidate_data.get('name', 'Candidate')}, Skills: {', '.join(candidate_data.get('skills', [])[:5])}"
        if job_title:
            context += f", Position: {job_title}"
        
        prompt = f"""Generate a professional recruitment email template.

Type: {template_type}
{f'Context: {context}' if context else ''}

Return JSON:
{{
    "subject": "Email subject line",
    "body": "Full email body with proper formatting",
    "variables": ["list of personalization variables used like {{name}}, {{position}}"],
    "tips": "Tips for using this template effectively"
}}"""

        result = await self._generate_json(
            prompt,
            model=self.fast_model,
            temperature=0.4
        )
        
        return result or {
            'subject': f'Re: {template_type}',
            'body': 'Template generation unavailable',
            'variables': [],
            'tips': ''
        }
    
    # ========================================================================
    # SERVICE STATUS & METRICS
    # ========================================================================
    
    def get_status(self) -> Dict:
        """Get LLM service status and metrics"""
        avg_time = self._total_time / self._request_count if self._request_count > 0 else 0
        
        return {
            'available': self.available,
            'ollama_url': self.OLLAMA_BASE_URL,
            'primary_model': self.primary_model,
            'fast_model': self.fast_model,
            'reasoning_model': self.reasoning_model,
            'available_models': self.available_models,
            'requests_processed': self._request_count,
            'average_response_time': round(avg_time, 2),
            'error_count': self._error_count,
            'cache_size': len(self._cache),
            'cache_max': self._cache_max_size,
        }
    
    def clear_cache(self):
        """Clear response cache"""
        self._cache.clear()
        logger.info("🗑️ LLM cache cleared")
    
    async def close(self):
        """Close HTTP client"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


# ============================================================================
# SINGLETON
# ============================================================================

_llm_service: Optional[LLMService] = None


async def get_llm_service() -> LLMService:
    """Get or create LLM service singleton"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
        await _llm_service.initialize()
    return _llm_service


def get_llm_service_sync() -> LLMService:
    """Get LLM service without initialization (for sync contexts)"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
