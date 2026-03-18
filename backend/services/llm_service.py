"""
Ollama LLM Service
==================
Local LLM engine using Ollama's OpenAI-compatible API.
Replaces Gemini as the primary AI service when running locally,
eliminating cloud API costs entirely.

Tier priority (local hybrid mode):
  1. Ollama (primary — free, private, fast on GPU)
  2. Sentence-transformers / keyword fallback (local_ai_service)
"""

import json
import logging
import re
import time
import asyncio
import hashlib
import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Any, Union

import httpx

logger = logging.getLogger(__name__)


def _repair_json(text: str) -> Optional[Dict]:
    """Lightweight JSON repair for LLM output. Returns a dict or None."""
    if not text:
        return None
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    text = re.sub(r'```$', '', text).strip()

    def _ensure_dict(val):
        if isinstance(val, dict):
            return val
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    return item
        return None

    # Direct parse
    try:
        return _ensure_dict(json.loads(text))
    except json.JSONDecodeError:
        pass

    # Extract balanced {...}
    start = text.find('{')
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return _ensure_dict(json.loads(candidate))
                    except json.JSONDecodeError:
                        fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
                        try:
                            return _ensure_dict(json.loads(fixed))
                        except json.JSONDecodeError:
                            pass
                    break

    # Patch truncated JSON
    open_b = text.count('{') - text.count('}')
    open_k = text.count('[') - text.count(']')
    if open_b > 0 or open_k > 0:
        patched = text + (']' * max(open_k, 0)) + ('}' * max(open_b, 0))
        patched = re.sub(r',\s*([}\]])', r'\1', patched)
        try:
            return _ensure_dict(json.loads(patched))
        except json.JSONDecodeError:
            pass

    logger.debug(f"_repair_json: all strategies failed ({len(text)} chars)")
    return None


class OllamaService:
    """Local LLM service using Ollama's OpenAI-compatible API."""

    def __init__(self, host: str = "http://localhost:11434", model: str = "qwen2.5:14b", timeout: float = 120.0):
        self.host = host.rstrip('/')
        self.model_name = model
        self.timeout = timeout
        self.available = False
        self.available_models: List[str] = []
        self.primary_model = model

        # Performance tracking
        self._request_count = 0
        self._total_time = 0.0
        self._error_count = 0

        # Thread-safe response cache
        self._cache: OrderedDict = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_max_size = 500
        self._cache_ttl = 14400  # 4 hours

        # Thread-safe search cache
        self._search_cache: OrderedDict = OrderedDict()
        self._search_cache_lock = threading.Lock()
        self._search_cache_ttl = 900  # 15 minutes

        # Probe Ollama on init
        self._probe()

    def _probe(self):
        """Check if Ollama is running and the model is available."""
        try:
            resp = httpx.get(f"{self.host}/api/tags", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                self.available_models = [m['name'] for m in data.get('models', [])]
                # Check if requested model (or a variant) is pulled
                if any(self.model_name in m for m in self.available_models):
                    self.available = True
                    logger.info(f"✅ Ollama connected: {self.model_name} (models: {', '.join(self.available_models[:5])})")
                else:
                    logger.warning(
                        f"⚠️ Ollama running but model '{self.model_name}' not found. "
                        f"Available: {', '.join(self.available_models[:5])}. "
                        f"Run: ollama pull {self.model_name}"
                    )
                    # Still mark available if ANY model exists — we'll use the first one
                    if self.available_models:
                        self.primary_model = self.available_models[0]
                        self.available = True
                        logger.info(f"  → Using fallback model: {self.primary_model}")
            else:
                logger.warning(f"⚠️ Ollama returned status {resp.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Ollama not reachable at {self.host}: {e}")

    # ------------------------------------------------------------------
    # Cache helpers (identical pattern to GeminiService)
    # ------------------------------------------------------------------

    def _cache_key(self, prefix: str, text: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()
        return f"ollama:{prefix}:{h}"

    def _get_cached(self, key: str) -> Optional[Any]:
        with self._cache_lock:
            if key in self._cache:
                entry = self._cache[key]
                if time.time() - entry['time'] < self._cache_ttl:
                    self._cache.move_to_end(key)
                    return entry['data']
                del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any):
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = {'data': data, 'time': time.time()}
            else:
                if len(self._cache) >= self._cache_max_size:
                    for _ in range(self._cache_max_size // 5):
                        if self._cache:
                            self._cache.popitem(last=False)
                self._cache[key] = {'data': data, 'time': time.time()}

    def _get_search_cached(self, query: str, num_candidates: int, total: int) -> Optional[Any]:
        key = f"search:{hashlib.sha256(f'{query.lower().strip()}:{num_candidates}'.encode()).hexdigest()}"
        with self._search_cache_lock:
            if key in self._search_cache:
                entry = self._search_cache[key]
                if time.time() - entry['time'] < self._search_cache_ttl:
                    self._search_cache.move_to_end(key)
                    return entry['data']
                del self._search_cache[key]
        return None

    def _set_search_cache(self, query: str, num_candidates: int, total: int, data: Any):
        key = f"search:{hashlib.sha256(f'{query.lower().strip()}:{num_candidates}'.encode()).hexdigest()}"
        result_count = num_candidates if isinstance(num_candidates, int) else 0
        ttl = self._search_cache_ttl if result_count >= 5 else max(300, self._search_cache_ttl // 3)
        with self._search_cache_lock:
            if len(self._search_cache) >= 50:
                for _ in range(20):
                    if self._search_cache:
                        self._search_cache.popitem(last=False)
            self._search_cache[key] = {'data': data, 'time': time.time(), 'ttl': ttl}

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    def _generate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 2048, system: str = None) -> str:
        """Synchronous text generation via Ollama's OpenAI-compatible API."""
        if not self.available:
            return ""
        start = time.time()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = httpx.post(
                f"{self.host}/v1/chat/completions",
                json={
                    "model": self.primary_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": False,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            result = (data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
            elapsed = time.time() - start
            self._request_count += 1
            self._total_time += elapsed

            # Log usage
            usage = data.get("usage", {})
            logger.info(
                f"🧠 Ollama [{self.primary_model}]: {len(result)} chars in {elapsed:.1f}s | "
                f"in={usage.get('prompt_tokens', 0)} out={usage.get('completion_tokens', 0)}"
            )
            return result
        except httpx.TimeoutException:
            self._error_count += 1
            logger.error(f"Ollama timeout after {self.timeout}s")
            return ""
        except Exception as e:
            self._error_count += 1
            logger.error(f"Ollama generation error: {e}")
            return ""

    async def _agenerate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 2048, system: str = None) -> str:
        """Async generation — runs sync call in thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._generate, prompt, temperature, max_tokens, system)

    def _generate_json(self, prompt: str, temperature: float = 0.05, max_tokens: int = 2048, system: str = None) -> Optional[Dict]:
        result = self._generate(prompt, temperature=temperature, max_tokens=max_tokens, system=system)
        if not result:
            return None
        parsed = _repair_json(result)
        if parsed is None:
            # Retry once with stricter instruction
            logger.warning("JSON repair failed, retrying with stricter prompt")
            retry_result = self._generate(
                "Your previous response was not valid JSON. Return ONLY the raw JSON object with no markdown, no commentary:\n\n" + prompt,
                temperature=0.0, max_tokens=max_tokens, system=system
            )
            if retry_result:
                parsed = _repair_json(retry_result)
        return parsed

    async def _agenerate_json(self, prompt: str, temperature: float = 0.0, max_tokens: int = 2048, system: str = None) -> Optional[Dict]:
        result = await self._agenerate(prompt, temperature=temperature, max_tokens=max_tokens, system=system)
        if not result:
            return None
        parsed = _repair_json(result)
        if parsed is None:
            # Retry once with stricter instruction
            logger.warning("JSON repair failed, retrying with stricter prompt")
            retry_result = await self._agenerate(
                "Your previous response was not valid JSON. Return ONLY the raw JSON object with no markdown, no commentary:\n\n" + prompt,
                temperature=0.0, max_tokens=max_tokens, system=system
            )
            if retry_result:
                parsed = _repair_json(retry_result)
        return parsed

    # ==================================================================
    # RESUME PARSING
    # ==================================================================

    async def parse_resume(self, text: str) -> Optional[Dict]:
        """Parse resume text into structured data."""
        cache_key = self._cache_key("resume", text)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        prompt = f"""Extract structured data from the resume below. Return ONLY valid JSON — no commentary, no markdown.

════ RESUME ════
{text[:12000]}
════ END ════

EXTRACTION RULES:
• name: Full name exactly as written (First Last).
• email: Exact email address. Leave empty if none found.
• phone: Include country code if present. Leave empty if none.
• location: "City, Country" or "City" format.
• nationality: ONLY if explicitly stated. Leave empty if not stated.
• linkedin: Full URL or handle. Leave empty if none.
• summary: 3-4 sentence professional summary based on the resume content.
• skills: Extract ALL technical skills, tools, frameworks, programming languages, platforms, methodologies, and domain expertise. Be exhaustive.
• experience_years: Sum all work roles from dates. If explicitly stated, use that value. Round to 1 decimal.
• work_history: ALL job positions, newest first. Duration = calculated time in role. Description = 1-2 sentences.
• education: Real academic degrees only (B.Tech, MBA, Ph.D, Diploma, B.Sc). NOT certifications.
• certifications: Professional certifications only (AWS Certified, PMP, CFA, CISSP, etc.). NOT degrees.
• languages: As stated. Leave empty array if not mentioned.
• notice_period: Exact text as found. Leave empty if not stated.
• current_salary: As written in resume. Leave empty if not stated.
• expected_salary: As written. Leave empty if not stated.
• source_portal: Job portal if mentioned. Leave empty if unclear.
• job_applied_for: Position if stated. Leave empty — do NOT guess.

Return EXACTLY this JSON structure:
{{
    "name": "",
    "email": "",
    "phone": "",
    "location": "",
    "nationality": "",
    "linkedin": "",
    "summary": "",
    "skills": [],
    "experience_years": 0,
    "work_history": [{{"title": "", "company": "", "period": "", "duration": "", "description": ""}}],
    "education": [{{"degree": "", "field": "", "institution": "", "year": ""}}],
    "certifications": [],
    "languages": [],
    "notice_period": "",
    "current_salary": "",
    "expected_salary": "",
    "source_portal": "",
    "job_applied_for": ""
}}

CRITICAL: NEVER fabricate data. Use empty string or empty array when data is absent."""

        result = await self._agenerate_json(
            prompt, temperature=0.0, max_tokens=4096,
            system="You are an expert resume parser. Extract structured candidate data from resumes into JSON. Be exhaustive with skills extraction. Never fabricate data."
        )
        if result:
            self._set_cache(cache_key, result)
        return result

    # ==================================================================
    # CANDIDATE EMAIL PARSING
    # ==================================================================

    async def parse_candidate_email(self, subject: str, body: str, sender: str = "", resume_text: str = "") -> Optional[Dict]:
        """Parse a candidate application email into structured data."""
        cache_key = self._cache_key("email", f"{subject}:{body[:500]}")
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        source = "Email"
        body_section = body[:6000] if body else "(empty)"
        resume_section = f"\nATTACHED RESUME:\n{resume_text[:6000]}" if resume_text else ""

        prompt = f"""You are a recruitment email parser. Extract candidate data as JSON. Return ONLY valid JSON.

SUBJECT: {subject}
SENDER: {sender}
SOURCE: {source}

EMAIL BODY:
{body_section}
{resume_section}

EXTRACTION RULES:
• is_candidate_email: false if NOT a job application. true otherwise.
• name: Full name of the applicant.
• email: Candidate's PERSONAL email only. NEVER use portal-generated addresses.
• phone: With country code if present.
• location: "City, Country" from email or resume.
• nationality: Only if explicitly stated.
• skills: ALL technical skills mentioned.
• experience_years: From stated value or sum of work dates. 0 if unknown.
• summary: 2-3 sentence professional summary.
• job_applied_for: Role mentioned in subject or email body.
• notice_period: As stated. Leave empty if not mentioned.
• current_salary: Keep original format. Leave empty if not stated.
• expected_salary: Keep original format. Leave empty if not stated.
• work_history: Actual job positions only (NOT education). Newest first.
• education: Real degrees only. NOT certifications.
• certifications: Professional certs only. NOT degrees.
• quality_score: Score 0-100.

Return JSON:
{{
    "name": "",
    "email": "",
    "phone": "",
    "location": "",
    "nationality": "",
    "linkedin": "",
    "summary": "",
    "skills": [],
    "experience_years": 0,
    "job_applied_for": "",
    "source": "{source}",
    "notice_period": "",
    "current_salary": "",
    "expected_salary": "",
    "work_history": [{{"title": "", "company": "", "period": "", "description": ""}}],
    "education": [{{"degree": "", "field": "", "institution": "", "year": ""}}],
    "certifications": [],
    "languages": [],
    "quality_score": 0,
    "is_candidate_email": true
}}"""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=4096)
        if result:
            self._set_cache(cache_key, result)
        return result

    # ==================================================================
    # CANDIDATE ANALYSIS
    # ==================================================================

    async def analyze_candidate(self, text: str, job_context: str = None) -> Dict:
        """Analyze resume text and return structured assessment."""
        cache_key = self._cache_key("analyze", text)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        job_instruction = f"\nEvaluate fit for: {job_context[:500]}" if job_context else ""

        system_msg = "You are a senior recruitment analyst. You extract structured data from resumes and assess candidate quality with precision. Always return valid JSON only — no markdown, no commentary."

        prompt = f"""Analyze the following resume. Extract structured data and assess quality.
{job_instruction}

════ RESUME ════
{text[:6000]}
════ END ════

Return this exact JSON structure:
{{
    "name": "Full Name",
    "phone": "Phone with country code (e.g. +971-50-1234567). Empty string if not found.",
    "email": "PERSONAL email only — exclude corporate/company emails",
    "location": "City, Country",
    "skills": ["List ALL technical skills, tools, frameworks, languages, platforms, methodologies mentioned"],
    "experience": 5,
    "education": ["Highest degree — e.g. B.Tech Computer Science, MBA Finance"],
    "job_category": "One of: Software Engineer, DevOps Engineer, Data Scientist, Cybersecurity, QA / Testing, IT & Systems, Product Manager, Design, Project Management, Business Analyst, Consulting, Marketing, Content & Communications, Sales, Finance, HR, Executive, Legal, Healthcare, Education, Customer Service, Operations, General",
    "job_subcategory": "Specific role title (e.g. React Frontend Developer, AWS Solutions Architect)",
    "quality_score": 65,
    "summary": "2-3 sentence professional summary highlighting key strengths",
    "certifications": ["Professional certifications only — NOT degrees"],
    "languages": ["Spoken languages"],
    "linkedin": "LinkedIn URL or empty string",
    "work_history": [{{"title": "Job Title", "company": "Company Name", "period": "Jan 2020 - Present", "duration": "4 years", "description": "1-2 sentence role summary"}}]
}}

SCORING RUBRIC (quality_score is an integer 10-100):
  85-100: Expert — 15+ skills, 8+ years, advanced degree, top certifications, FAANG/tier-1 companies
  70-84:  Strong — 10+ skills, 5+ years experience, relevant degree, some certifications
  55-69:  Solid — 5-9 skills, 2-5 years, degree present, coherent work history
  40-54:  Developing — 3-4 skills, 1-2 years, or junior/entry-level with potential
  25-39:  Weak — Minimal skills (<3), <1 year, no degree, sparse information
  10-24:  Very Weak — Nearly empty, unreadable, or irrelevant content

RULES:
• Count skills exhaustively — include programming languages, frameworks, cloud platforms, tools, methodologies.
• Calculate experience_years by summing all work role durations. Use explicit statement if present.
• Never fabricate data. Use empty string or empty array when information is absent.
• quality_score MUST reflect the rubric above. Never default to 25, 50, or 65."""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=3072, system=system_msg)
        if result:
            # Coerce quality_score to int
            qs = result.get('quality_score', 0)
            if isinstance(qs, str):
                try:
                    qs = int(qs)
                except (TypeError, ValueError):
                    qs = 0
            result['quality_score'] = max(0, min(100, qs))

            # Data-driven score floor when model returns 0
            if result['quality_score'] == 0:
                _s = result.get('skills', [])
                _e = result.get('experience', 0) or 0
                try:
                    _e = int(float(_e))
                except (TypeError, ValueError):
                    _e = 0
                _has_edu = bool(result.get('education'))
                _has_certs = bool(result.get('certifications'))
                _has_summary = bool(str(result.get('summary', '')).strip())
                _data_floor = 10 + min(25, len(_s) * 3) + min(20, _e * 3) + (
                    8 if _has_edu else 0) + (5 if _has_certs else 0) + (3 if _has_summary else 0)
                if len(_s) > 0 or _e > 0:
                    result['quality_score'] = min(75, max(15, _data_floor))

            # Post-processing: clean phone (strip CID artifacts, validate format)
            phone = str(result.get('phone', '')).strip()
            if phone:
                # Remove common LLM artifacts
                phone = re.sub(r'\bCID[:\-]?\s*\S+', '', phone).strip()
                # Keep only if it looks like a phone number
                if not re.search(r'\d{5,}', re.sub(r'[\s\-\+\(\)]', '', phone)):
                    result['phone'] = ''
                else:
                    result['phone'] = phone

            # Ensure experience is numeric
            exp = result.get('experience', 0)
            if isinstance(exp, str):
                try:
                    result['experience'] = int(float(exp))
                except (TypeError, ValueError):
                    result['experience'] = 0

            self._set_cache(cache_key, result)
        return result or {}

    async def analyze_candidate_deep(self, candidate_data: Dict) -> Optional[Dict]:
        """Deep AI analysis with pros/cons and hiring recommendation."""
        cache_key = self._cache_key("deep", json.dumps(candidate_data, default=str))
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        education = candidate_data.get('education', [])
        work_history = candidate_data.get('work_history', candidate_data.get('workHistory', []))
        summary = candidate_data.get('summary', '')
        resume_text = candidate_data.get('resume_text', '')

        if not experience or experience == 0:
            _m = re.search(r'(\d{1,2})\+?\s*years?\s*(of\s*)?(?:IT\s*)?experience', f"{summary} {resume_text}", re.IGNORECASE)
            if _m:
                experience = int(_m.group(1))

        work_text = ""
        if work_history:
            for w in work_history[:6]:
                if isinstance(w, dict):
                    title = w.get('title', w.get('position', ''))
                    company = w.get('company', w.get('organization', ''))
                    period = w.get('period', w.get('duration', w.get('years', '')))
                    desc = w.get('description', w.get('responsibilities', ''))
                    work_text += f"\n  - {title} at {company} ({period})"
                    if desc:
                        work_text += f" — {str(desc)[:150]}"

        edu_text = ""
        if education:
            for e in education[:4]:
                if isinstance(e, dict):
                    degree = e.get('degree', e.get('title', ''))
                    field = e.get('field', '')
                    inst = e.get('institution', e.get('school', ''))
                    year = e.get('year', e.get('graduation_year', ''))
                    edu_text += f"\n  - {degree}{' in ' + field if field else ''} — {inst} ({year})"
                elif isinstance(e, str):
                    edu_text += f"\n  - {e}"

        resume_section = f"\n\nRESUME TEXT (raw):\n{resume_text[:3000]}" if resume_text else ""

        prompt = f"""You are a world-class senior recruiter with 20+ years experience. Analyze this candidate thoroughly and provide a detailed, data-driven assessment. Return ONLY valid JSON.

CANDIDATE: {name}
Experience: {experience} years
Skills: {', '.join(skills[:25]) if skills else 'Not listed'}
Education:{edu_text or ' N/A'}
Work History:{work_text or ' N/A'}
Summary: {summary[:500] if summary else 'N/A'}
{resume_section}

IMPORTANT EXTRACTION RULES:
- For education: Extract REAL academic degrees only. Do NOT extract skills or certifications as degrees.
- For work history: Extract actual job positions with title, company, and duration.
- For email: Extract personal email only (gmail, yahoo, hotmail, outlook). Do NOT use portal emails.

Return JSON with ALL these fields:
{{
    "executive_summary": "3-4 sentence thorough assessment",
    "technical_assessment": "2-3 sentences on technical capabilities",
    "experience_assessment": "2-3 sentences on career progression",
    "education_assessment": "1-2 sentences on educational background",
    "career_trajectory": "2-3 sentences on career growth pattern",
    "pros": ["Specific strength 1", "Specific strength 2", "Specific strength 3", "Specific strength 4"],
    "cons": ["Specific concern 1", "Specific concern 2"],
    "ideal_roles": ["Best-fit role 1", "Best-fit role 2", "Best-fit role 3"],
    "interview_focus_areas": ["Area 1", "Area 2", "Area 3"],
    "hiring_recommendation": "STRONGLY_RECOMMEND or RECOMMEND or CONSIDER or PASS",
    "hiring_recommendation_rationale": "2-3 sentences explaining the recommendation",
    "confidence_score": 80,
    "overall_rating": "A+ or A or A- or B+ or B or B- or C+ or C or D",
    "candidate_email": "personal email if found, otherwise empty string"
}}

SCORING GUIDELINES:
- A+/A (STRONGLY_RECOMMEND): 8+ years, deep expertise, leadership, certifications, strong progression
- A-/B+ (RECOMMEND): 5-8 years, solid skills, good education, clear growth
- B/B- (CONSIDER): 2-5 years, relevant skills but gaps, developing career
- C+/C (CONSIDER/PASS): Entry-level, limited skills, unclear trajectory
- D (PASS): Misaligned background, significant gaps

Be specific — reference actual skills, companies, and experience from the profile."""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)

        if result:
            result.setdefault('overall_assessment', result.get('executive_summary', ''))
            result.setdefault('strengths', result.get('pros', [])[:5])
            result.setdefault('weaknesses', result.get('cons', [])[:3])
            result.setdefault('recommended_roles', result.get('ideal_roles', []))
            result['source'] = 'ollama'
            self._set_cache(cache_key, result)
            logger.info(f"🔍 [Ollama] Deep Analysis: {name} → {result.get('hiring_recommendation', 'N/A')}")

        return result or None

    # ==================================================================
    # CANDIDATE-JOB MATCHING
    # ==================================================================

    async def match_candidate_to_job(self, candidate_data: Dict, job_description: str) -> Dict:
        """Match a single candidate against a job description."""
        _jd_hash = hashlib.sha256((job_description or '').encode()).hexdigest()[:16]
        cache_key = self._cache_key("match", f"{json.dumps(candidate_data, default=str)}:{_jd_hash}")
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        name = candidate_data.get('name', 'Unknown')
        skills_str = ', '.join((candidate_data.get('skills') or [])[:20])
        exp = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        location = candidate_data.get('location', '')
        notice = candidate_data.get('notice_period', '')
        salary = candidate_data.get('current_salary', '') or candidate_data.get('expected_salary', '')
        wh = candidate_data.get('workHistory') or candidate_data.get('work_history') or []
        wh_lines = []
        for w in wh[:3]:
            if isinstance(w, dict):
                wh_lines.append(f"  - {w.get('title', '')} at {w.get('company', '')} ({w.get('duration', '')}) : {w.get('description', '')[:120]}")
        work_text = '\n'.join(wh_lines) if wh_lines else '  Not provided'
        edu = candidate_data.get('education') or []
        edu_text = '; '.join([e.get('degree', '') + ' ' + e.get('field', '') if isinstance(e, dict) else str(e) for e in edu[:2]]) or 'Not provided'
        certs = ', '.join(candidate_data.get('certifications') or []) or 'None'
        summary = candidate_data.get('summary', '')

        prompt = f"""Evaluate candidate-job fit. Return ONLY valid JSON.

CANDIDATE: {name}
Skills: {skills_str}
Experience: {exp} years | Location: {location}
Notice Period: {notice} | Salary: {salary}
Work History:
{work_text}
Education: {edu_text}
Certifications: {certs}
Summary: {summary[:400]}

JOB:
{job_description[:3000]}

Return JSON:
{{
    "match_score": 75,
    "skill_match_score": 80,
    "experience_match_score": 70,
    "overall_fit": "Good Match",
    "matched_skills": ["skill1"],
    "missing_skills": ["skill1"],
    "strengths": ["str1", "str2"],
    "gaps": ["gap1"],
    "recommendation": "2-3 sentence fit summary",
    "interview_questions": ["q1", "q2"],
    "risk_factors": ["risk1"]
}}"""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)
        if result:
            score = result.get('match_score', 50)
            if isinstance(score, str):
                try:
                    score = int(score)
                except ValueError:
                    score = 50
            result['match_score'] = max(0, min(100, score))
            result['source'] = 'ollama'
            self._set_cache(cache_key, result)
        return result or {'match_score': 50, 'matched_skills': [], 'missing_skills': [], 'strengths': [], 'gaps': [], 'recommendation': 'Analysis unavailable'}

    # ==================================================================
    # BATCH RANKING
    # ==================================================================

    async def rank_candidates_for_job(self, candidates: List[Dict], job_description: str, top_n: int = 10) -> List[Dict]:
        """Score and rank candidates against a job description."""
        if not candidates:
            return []

        # Process in batches of 5
        all_results = []
        for i in range(0, len(candidates), 5):
            batch = candidates[i:i + 5]
            batch_results = await self._batch_match(batch, job_description)
            all_results.extend(batch_results)

        all_results.sort(key=lambda x: x.get('match_score', 0), reverse=True)
        return all_results[:top_n]

    async def _batch_match(self, batch: List[Dict], job_description: str) -> List[Dict]:
        """Score a batch of candidates against a job description."""
        n = len(batch)
        candidates_text = ""
        for i, c in enumerate(batch, 1):
            skills = ', '.join((c.get('skills') or [])[:15])
            exp = c.get('experience', c.get('experience_years', 0))
            location = c.get('location', '')
            wh = c.get('workHistory') or c.get('work_history') or []
            wh_str = '; '.join([f"{w.get('title', '')} at {w.get('company', '')}" for w in wh[:2] if isinstance(w, dict)]) if wh else 'N/A'
            candidates_text += f"\n[{i}] {c.get('name', 'Unknown')} | {exp}yr | {location} | Skills: {skills} | History: {wh_str}"

        prompt = f"""Score each candidate against the job description. Return ONLY valid JSON with a "candidates" array of EXACTLY {n} objects — one per candidate, in the SAME order.

SCORING RULES:
R1. Work History job titles are the #1 signal
R2. ALL hard constraints must be satisfied — a single violation = 0-35 score
R3. Skills listed but never demonstrated in work history = weaker signal
R7. Job hopper penalty: >4 jobs in 3 years with <6 month tenure = reduce by 15-20 points

{candidates_text}

JOB DESCRIPTION:
{job_description[:3000]}

Each object must have: match_score (0-100 integer), matched_skills (array), missing_skills (array), strengths (array), gaps (array), recommendation (string).

Score guidelines: 85-100 Excellent, 70-84 Strong, 55-69 Moderate, 40-54 Weak, <40 Poor.

Return: {{"candidates": [...]}}"""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=4096)
        if result and 'candidates' in result:
            scored = result['candidates']
            for i, s in enumerate(scored):
                if i < len(batch):
                    s['name'] = batch[i].get('name', 'Unknown')
                    s['id'] = batch[i].get('id')
                    s['source'] = 'ollama'
                    score = s.get('match_score', 50)
                    if isinstance(score, str):
                        try:
                            score = int(score)
                        except ValueError:
                            score = 50
                    s['match_score'] = max(0, min(100, score))
            return scored[:len(batch)]

        # Fallback: return basic entries
        return [{'name': c.get('name', 'Unknown'), 'id': c.get('id'), 'match_score': 50,
                 'matched_skills': [], 'missing_skills': [], 'strengths': [], 'gaps': [],
                 'recommendation': 'Scoring unavailable', 'source': 'fallback'} for c in batch]

    async def rank_candidates_with_constraints(
        self, candidates: List[Dict], constraints: Any, job_description: str, top_n: int = 10
    ) -> List[Dict]:
        """Rank candidates with parsed constraint hints."""
        # Delegate to rank_candidates_for_job — Ollama handles constraints via the JD text
        return await self.rank_candidates_for_job(candidates, job_description, top_n)

    # ==================================================================
    # CANDIDATE COMPARISON
    # ==================================================================

    async def compare_candidates(self, candidates: List[Dict], job_description: Optional[str] = None) -> Dict:
        """Compare multiple candidates side by side."""
        if not candidates or len(candidates) < 2:
            return {'error': 'Need at least 2 candidates'}

        candidates_text = ""
        for i, c in enumerate(candidates[:5], 1):
            candidates_text += f"\nCandidate {i}: {c.get('name', 'Unknown')}\n- Skills: {', '.join(c.get('skills', [])[:10])}\n- Experience: {c.get('experience', 0)} years\n- Score: {c.get('quality_score', c.get('score', 'N/A'))}%\n"

        job_ctx = f"\nJOB:\n{job_description[:1500]}" if job_description else ""

        prompt = f"""Compare these candidates. Return ONLY valid JSON.
{candidates_text}{job_ctx}

Return JSON:
{{
    "ranking": [{{"rank": 1, "name": "Name", "score": 85, "key_advantage": "Why #1", "key_risk": "Main concern"}}],
    "comparison_summary": "Overview",
    "recommendation": "Who to interview first and why",
    "best_for_role": "Best candidate and why"
}}"""

        result = await self._agenerate_json(prompt, temperature=0.0, max_tokens=2048)
        return result or {'ranking': [], 'comparison_summary': 'Comparison unavailable'}

    # ==================================================================
    # AI CHAT
    # ==================================================================

    async def chat(
        self,
        message: str,
        context: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None,
        candidates_data: Optional[List[Dict]] = None,
        return_candidates: bool = False,
        num_candidates: int = 15,
    ) -> Union[str, Dict]:
        """AI chat assistant — delegates to Ollama for intelligent responses.
        
        This method handles all query types: search, analytics, advice, greetings.
        The prompt is built to match the GeminiService chat() interface exactly.
        """
        ctx = context or {}
        total = ctx.get('totalCandidates', 0)
        avg_score = ctx.get('avgMatchScore', 0)
        strong = ctx.get('strongMatches', 0)

        # Build conversation history context
        history_text = ""
        if conversation_history:
            for msg in conversation_history[-6:]:
                role = msg.get('role', 'user')
                content = msg.get('content', '')[:300]
                history_text += f"\n{role}: {content}"

        # Build candidates context if available
        candidates_context = ""
        if candidates_data:
            for i, c in enumerate(candidates_data[:num_candidates], 1):
                name = c.get('name', 'Unknown')
                skills = ', '.join((c.get('skills') or [])[:10])
                exp = c.get('experience', c.get('experience_years', 0))
                loc = c.get('location', '')
                score = c.get('quality_score', c.get('score', 0))
                candidates_context += f"\n[{i}] {name} | {score}% | {exp}yr | {loc} | {skills}"

        prompt = f"""You are the AI Recruiter for Efforts Solutions. Answer the user's query helpfully using the data provided.

═══ DATABASE SNAPSHOT ═══
Total: {total} candidates | Strong (70%+): {strong} | Avg Score: {avg_score:.0f}%

═══ CANDIDATE POOL ═══
{candidates_context if candidates_context else '(No candidates pre-filtered for this query)'}

═══ CONVERSATION HISTORY ═══{history_text if history_text else ' (none)'}

═══ USER QUERY ═══
{message}

═══ RULES ═══
- Only use data shown. Never invent candidates.
- Quality over quantity — 3 perfect matches beat 10 wrong ones.
- If the pool is thin, say so honestly.
- For search queries: format each candidate with name, score, experience, location, key skills, and why they match.
- For advice queries: give practical recruitment advice.
- For analytics: summarize database statistics.

Respond naturally and helpfully."""

        result = await self._agenerate(prompt, temperature=0.2, max_tokens=4096)

        if return_candidates and candidates_data:
            candidates_lookup = []
            for i, c in enumerate(candidates_data[:num_candidates], 1):
                candidates_lookup.append({
                    'index': i,
                    'id': c.get('id'),
                    'name': c.get('name', 'Unknown'),
                    'score': c.get('quality_score', c.get('score', 0)),
                    'skills': c.get('skills', []),
                    'experience': c.get('experience', c.get('experience_years', 0)),
                    'location': c.get('location', ''),
                    'email': c.get('email', ''),
                    'phone': c.get('phone', ''),
                })
            return {
                'response': result,
                'candidates_lookup': candidates_lookup,
            }

        return result or "I'm unable to process this request right now. Please try again."

    # ==================================================================
    # INTERVIEW QUESTIONS
    # ==================================================================

    async def generate_interview_questions(
        self, candidate_data: Dict, job_description: Optional[str] = None, num_questions: int = 8
    ) -> List[Dict]:
        """Generate tailored interview questions."""
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        name = candidate_data.get('name', 'the candidate')
        job_ctx = f"\nJOB:\n{job_description[:1000]}" if job_description else ""

        prompt = f"""Generate {num_questions} interview questions for {name}.
Skills: {', '.join(skills[:15])}, Experience: {experience} years{job_ctx}

Return JSON:
{{"questions": [{{"question": "...", "type": "technical|behavioral|situational", "difficulty": "easy|medium|hard", "skill_tested": "...", "what_to_look_for": "..."}}]}}"""

        result = await self._agenerate_json(prompt, temperature=0.3, max_tokens=2048)
        if result and 'questions' in result:
            return result['questions'][:num_questions]
        return [{"question": f"Tell me about your experience with {skills[0] if skills else 'your field'}.",
                 "type": "technical", "difficulty": "medium",
                 "skill_tested": skills[0] if skills else "General",
                 "what_to_look_for": "Depth of knowledge"}]

    # ==================================================================
    # JOB DESCRIPTION PARSING
    # ==================================================================

    async def parse_job_description(self, text: str) -> Dict:
        """Parse a job description into structured format."""
        prompt = f"""Parse this job description. Return ONLY valid JSON.

JOB DESCRIPTION:
{text[:4000]}

Return JSON:
{{
    "title": "Job Title",
    "department": "Department",
    "location": "Location",
    "employment_type": "Full-time/Part-time/Contract",
    "experience_required": "X years",
    "required_skills": ["skill1", "skill2"],
    "preferred_skills": ["skill1"],
    "education_required": "Minimum education",
    "responsibilities": ["resp1", "resp2"],
    "benefits": ["benefit1"],
    "salary_range": "Range if mentioned",
    "key_requirements": ["req1", "req2"]
}}"""

        result = await self._agenerate_json(prompt, temperature=0.05, max_tokens=2048)
        return result or {'title': 'Position', 'required_skills': [], 'responsibilities': []}

    # ==================================================================
    # EMAIL TEMPLATE GENERATION
    # ==================================================================

    async def generate_email_template(self, template_type: str, candidate_data: Optional[Dict] = None, job_title: Optional[str] = None) -> Dict:
        """Generate professional email templates."""
        context = ""
        if candidate_data:
            context = f"Candidate: {candidate_data.get('name', 'Candidate')}, Skills: {', '.join(candidate_data.get('skills', [])[:5])}"
        if job_title:
            context += f", Position: {job_title}"

        prompt = f"""Generate a professional recruitment email template.
Type: {template_type}
{f'Context: {context}' if context else ''}

Return JSON:
{{"subject": "Subject line", "body": "Full email body", "variables": ["{{name}}", "{{position}}"], "tips": "Usage tips"}}"""

        result = await self._agenerate_json(prompt, temperature=0.4, max_tokens=2048)
        return result or {'subject': f'Re: {template_type}', 'body': 'Template unavailable', 'variables': [], 'tips': ''}

    # ==================================================================
    # STATUS & METRICS
    # ==================================================================

    def get_status(self) -> Dict:
        """Get Ollama service status."""
        avg_time = self._total_time / self._request_count if self._request_count > 0 else 0
        return {
            'available': self.available,
            'model': self.primary_model,
            'host': self.host,
            'requests_processed': self._request_count,
            'average_response_time': round(avg_time, 2),
            'error_count': self._error_count,
            'cache_size': len(self._cache),
            'available_models': self.available_models[:10],
        }

    def clear_cache(self):
        """Clear response cache."""
        with self._cache_lock:
            self._cache.clear()
        with self._search_cache_lock:
            self._search_cache.clear()
        logger.info("🗑️ Ollama cache cleared")


# ============================================================================
# SINGLETON
# ============================================================================

_ollama_service: Optional[OllamaService] = None
_ollama_lock = threading.Lock()


def get_llm_service() -> Optional[OllamaService]:
    """Get or create Ollama service singleton. Thread-safe."""
    global _ollama_service
    if _ollama_service is None:
        with _ollama_lock:
            if _ollama_service is None:
                from core.config import get_settings
                _settings = get_settings()
                if _settings.use_ollama:
                    _ollama_service = OllamaService(
                        host=_settings.ollama_host,
                        model=_settings.ollama_model,
                        timeout=_settings.ollama_timeout,
                    )
                    if _ollama_service.available:
                        logger.info(f"✅ Ollama service initialized: {_ollama_service.primary_model}")
                    else:
                        logger.warning("⚠️ Ollama service initialized but not available — will use fallback AI")
                else:
                    logger.info("💡 Ollama disabled (USE_OLLAMA=false)")
                    return None
    return _ollama_service
