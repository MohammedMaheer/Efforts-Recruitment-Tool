"""
Google Gemini AI Service
========================
Primary AI engine for DEPLOYMENT (Cloud Run / GCP).
Uses Gemini 2.0 Flash for fast, cost-effective inference (~$0.10/1M input tokens).

Tier priority in production:
  1. Gemini (primary — fast, cheap, high quality)
  2. OpenAI  (fallback — if Gemini quota/error)
  3. Keyword (emergency — zero API cost)

For local development, Ollama remains primary (zero cost, full privacy).
"""

import json
import logging
import re
import time
import asyncio
import hashlib
from typing import Dict, List, Optional, Any, Union

logger = logging.getLogger(__name__)

# Try to import google-generativeai
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None  # type: ignore
    GEMINI_AVAILABLE = False
    logger.info("google-generativeai not installed — Gemini service disabled")


def _repair_json(text: str) -> Optional[Dict]:
    """Lightweight JSON repair for Gemini output."""
    if not text:
        return None
    # Strip markdown fences
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    text = re.sub(r'```$', '', text).strip()

    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract largest {...} blob
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        candidate = m.group()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Fix trailing commas
        fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Patch truncated JSON
    open_b = text.count('{') - text.count('}')
    open_k = text.count('[') - text.count(']')
    if open_b > 0 or open_k > 0:
        patched = text + (']' * max(open_k, 0)) + ('}' * max(open_b, 0))
        patched = re.sub(r',\s*([}\]])', r'\1', patched)
        try:
            return json.loads(patched)
        except json.JSONDecodeError:
            pass

    return None


class GeminiService:
    """
    Google Gemini AI Service — mirrors the interface of LLMService / OpenAIService
    so it can be used as a drop-in replacement in the AI tier chain.

    Capabilities:
    - Resume parsing (structured JSON extraction)
    - Email candidate extraction
    - Deep candidate analysis (pros/cons/recommendation)
    - Candidate–job matching with detailed scoring
    - Batch candidate ranking
    - Candidate comparison
    - AI chat assistant
    - Interview question generation
    - Job description parsing
    - Email template generation
    """

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-2.0-flash"):
        import os
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model_name = model_name
        self.available = False
        self._model = None
        self._chat_model = None

        # Performance tracking
        self._request_count = 0
        self._total_time = 0.0
        self._error_count = 0

        # Response cache
        self._cache: Dict[str, Any] = {}
        self._cache_max_size = 2000
        self._cache_ttl = 3600  # 1 hour

        if not GEMINI_AVAILABLE:
            logger.warning("⚠️ google-generativeai package not installed. Run: pip install google-generativeai")
            return

        if not self.api_key:
            logger.warning("⚠️ GEMINI_API_KEY not set — Gemini service disabled")
            return

        try:
            genai.configure(api_key=self.api_key)
            self._model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=2048,  # INTELLIGENCE OPTIMIZED: need room for detailed analysis
                ),
            )
            self._chat_model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=3000,  # INTELLIGENCE OPTIMIZED: rich candidate lists need space
                ),
            )
            self.available = True
            logger.info(f"✅ Gemini AI initialized: {self.model_name}")
        except Exception as e:
            logger.error(f"❌ Gemini initialization failed: {e}")
            self.available = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_key(self, prefix: str, text: str) -> str:
        h = hashlib.md5(text.encode()).hexdigest()[:16]
        return f"gemini:{prefix}:{h}"

    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry['time'] < self._cache_ttl:
                return entry['data']
            del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any):
        if len(self._cache) >= self._cache_max_size:
            oldest = sorted(self._cache, key=lambda k: self._cache[k]['time'])[:100]
            for k in oldest:
                del self._cache[k]
        self._cache[key] = {'data': data, 'time': time.time()}

    def _generate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 1024) -> str:
        """Synchronous text generation via Gemini."""
        if not self.available or not self._model:
            return ""
        start = time.time()
        try:
            cfg = genai.types.GenerationConfig(temperature=temperature, max_output_tokens=max_tokens)
            response = self._model.generate_content(prompt, generation_config=cfg)
            result = response.text.strip()
            elapsed = time.time() - start
            self._request_count += 1
            self._total_time += elapsed
            logger.debug(f"🌟 Gemini [{self.model_name}]: {len(result)} chars in {elapsed:.1f}s")
            return result
        except Exception as e:
            self._error_count += 1
            logger.error(f"Gemini generation error: {e}")
            return ""

    async def _agenerate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 1024) -> str:
        """Async wrapper — Gemini SDK is sync, so we run in executor."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._generate, prompt, temperature, max_tokens)

    def _generate_json(self, prompt: str, temperature: float = 0.05, max_tokens: int = 1024) -> Optional[Dict]:
        """Generate structured JSON from Gemini."""
        result = self._generate(prompt, temperature=temperature, max_tokens=max_tokens)
        if not result:
            return None
        return _repair_json(result)

    async def _agenerate_json(self, prompt: str, temperature: float = 0.05, max_tokens: int = 1024) -> Optional[Dict]:
        """Async JSON generation."""
        result = await self._agenerate(prompt, temperature=temperature, max_tokens=max_tokens)
        if not result:
            return None
        return _repair_json(result)

    # ==================================================================
    # RESUME PARSING
    # ==================================================================

    async def parse_resume(self, text: str) -> Optional[Dict]:
        """Parse resume text using Gemini for structured extraction."""
        if not text or len(text.strip()) < 30:
            return None

        cache_key = self._cache_key("resume", text)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        from services.job_taxonomy import classify_job_title

        # COST OPTIMIZED: Removed taxonomy, reduced resume text to 3K
        prompt = f"""Expert resume parser. Extract ALL info accurately. Return ONLY valid JSON.

RESUME:
{text[:3000]}

Return JSON:
{{
    "name": "Full name",
    "email": "email",
    "phone": "phone",
    "location": "City, Country",
    "linkedin": "LinkedIn URL or empty",
    "summary": "2-3 sentence professional summary",
    "skills": ["all skills, tools, languages, frameworks"],
    "experience_years": 0,
    "work_history": [{{"title": "Title", "company": "Company", "period": "Dates", "description": "1-2 sentences"}}],
    "education": [{{"degree": "Degree", "field": "Field", "institution": "School", "year": "Year"}}],
    "certifications": ["cert names"],
    "languages": ["languages"]
}}

Only extract explicitly stated data. Never fabricate."""

        result = await self._agenerate_json(prompt, temperature=0.05)

        if result:
            # Validate category
            if not result.get('job_subcategory') or result.get('job_category') == 'General':
                titles = [w.get('title', '') for w in result.get('work_history', []) if isinstance(w, dict)]
                if titles:
                    cat, sub = classify_job_title(titles[0])
                    result['job_category'] = cat
                    result['job_subcategory'] = sub

            self._set_cache(cache_key, result)
            logger.info(f"📄 [Gemini] Resume parsed: {result.get('name', 'Unknown')} | "
                        f"Skills: {len(result.get('skills', []))} | "
                        f"Exp: {result.get('experience_years', 0)}yrs")

        return result

    # ==================================================================
    # EMAIL CANDIDATE EXTRACTION
    # ==================================================================

    async def parse_candidate_email(self, subject: str, body: str, sender: str = "") -> Optional[Dict]:
        """Parse candidate email using Gemini."""
        if not body or len(body.strip()) < 20:
            return None

        cache_key = self._cache_key("email", f"{subject}:{body[:500]}")
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        source = "Direct"
        body_lower = body.lower()
        if "indeed" in body_lower:
            source = "Indeed"
        elif "linkedin" in body_lower:
            source = "LinkedIn"
        elif "naukri" in body_lower:
            source = "Naukri"

        from services.job_taxonomy import classify_job_title

        # COST OPTIMIZED: Removed taxonomy, reduced body to 2.5K
        prompt = f"""Parse this job application email. Extract candidate info. Return ONLY valid JSON.

SUBJECT: {subject}
SENDER: {sender}
SOURCE: {source}

BODY:
{body[:2500]}

Return JSON:
{{
    "name": "Candidate name",
    "email": "email",
    "phone": "phone or empty",
    "location": "location or empty",
    "skills": ["skills"],
    "experience_years": 0,
    "summary": "Brief summary",
    "linkedin": "LinkedIn URL or empty",
    "job_applied_for": "Position applied for",
    "source": "{source}",
    "is_candidate_email": true
}}

Set is_candidate_email to false if NOT a job application."""

        result = await self._agenerate_json(prompt, temperature=0.05)

        if result:
            if not result.get('is_candidate_email', True):
                return None
            result['source'] = source
            # Validate category
            if not result.get('job_subcategory'):
                title = result.get('job_applied_for', '')
                if title:
                    cat, sub = classify_job_title(title)
                    result['job_category'] = cat
                    result['job_subcategory'] = sub
            if result.get('name') or result.get('email'):
                self._set_cache(cache_key, result)
                logger.info(f"📧 [Gemini] Email parsed: {result.get('name', 'Unknown')} | Source: {source}")
                return result

        return None

    # ==================================================================
    # CANDIDATE TEXT ANALYSIS (for background processing)
    # ==================================================================

    async def analyze_candidate(self, text: str) -> Dict:
        """Analyze raw candidate/resume text and extract structured data.
        Used by the background processor to enrich unprocessed candidates."""
        if not text or len(text.strip()) < 20:
            return {}

        cache_key = self._cache_key("analyze", text[:500])
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        prompt = f"""You are a recruitment AI. Analyze this candidate profile/resume text and extract structured information.
Return ONLY valid JSON.

TEXT:
{text[:3000]}

Return JSON:
{{
    "skills": ["skill1", "skill2", "skill3"],
    "experience": 5,
    "education": ["degree or certification"],
    "job_category": "Software Development",
    "match_score": 65,
    "quality_score": 70,
    "summary": "Brief 1-2 sentence professional summary",
    "location": "City, Country if mentioned",
    "certifications": ["cert1"]
}}"""

        result = await self._agenerate_json(prompt, temperature=0.1)

        if result:
            # Normalize fields
            score = result.get('match_score', result.get('quality_score', 50))
            if score is None:
                score = 50
            if isinstance(score, str):
                nums = re.findall(r'\d+', score)
                score = int(nums[0]) if nums else 50
            try:
                result['match_score'] = max(0, min(100, int(float(score))))
            except (TypeError, ValueError):
                result['match_score'] = 50
            result['quality_score'] = result['match_score']
            result.setdefault('job_category', 'General')
            result.setdefault('skills', [])
            result.setdefault('experience', 0)
            result.setdefault('summary', '')
            self._set_cache(cache_key, result)
            logger.info(f"📊 [Gemini] Analyzed candidate: score={result['match_score']}, category={result['job_category']}")
            return result

        return {}

    # ==================================================================
    # DEEP CANDIDATE ANALYSIS
    # ==================================================================

    async def analyze_candidate_deep(self, candidate_data: Dict) -> Dict:
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

        work_text = ""
        if work_history:
            for w in work_history[:4]:
                if isinstance(w, dict):
                    work_text += f"\n  - {w.get('title', '')} at {w.get('company', '')} ({w.get('period', '')})"

        prompt = f"""You are a senior recruiter. Provide specific, data-driven candidate assessment. Return ONLY valid JSON.

Name: {name}
Experience: {experience} years
Skills: {', '.join(skills[:20]) if skills else 'Not listed'}
Education: {json.dumps(education[:3], default=str) if education else 'N/A'}
Work History:{work_text or ' N/A'}
Summary: {summary[:300] if summary else 'N/A'}

Return JSON:
{{
    "executive_summary": "2-3 sentence assessment",
    "pros": ["pro 1", "pro 2", "pro 3"],
    "cons": ["con 1", "con 2"],
    "ideal_roles": ["Role 1", "Role 2"],
    "interview_focus_areas": ["Topic 1", "Topic 2"],
    "hiring_recommendation": "STRONGLY_RECOMMEND|RECOMMEND|CONSIDER|PASS",
    "confidence_score": 85,
    "overall_rating": "A|B+|B|C+|C|D",
    "strengths": ["strength 1", "strength 2"],
    "weaknesses": ["weakness 1"]
}}"""

        result = await self._agenerate_json(prompt, temperature=0.15)

        if result:
            result.setdefault('overall_assessment', result.get('executive_summary', ''))
            result.setdefault('strengths', result.get('pros', [])[:5])
            result.setdefault('weaknesses', result.get('cons', [])[:3])
            result.setdefault('recommended_roles', result.get('ideal_roles', []))
            self._set_cache(cache_key, result)
            logger.info(f"🔍 [Gemini] Deep Analysis: {name} → {result.get('hiring_recommendation', 'N/A')}")

        return result or {
            'executive_summary': f'{name} has {experience} years of experience.',
            'pros': ['Application submitted'],
            'cons': ['AI analysis unavailable'],
            'hiring_recommendation': 'CONSIDER',
            'confidence_score': 30,
            'overall_rating': 'C',
            'overall_assessment': f'{name} profile requires manual review.',
            'strengths': ['Resume submitted'],
            'weaknesses': ['Analysis unavailable'],
            'recommended_roles': ['General'],
        }

    # ==================================================================
    # CANDIDATE-JOB MATCHING
    # ==================================================================

    async def match_candidate_to_job(self, candidate_data: Dict, job_description: str) -> Dict:
        """Match a single candidate against a job description."""
        cache_key = self._cache_key("match", f"{json.dumps(candidate_data, default=str)}:{job_description[:500]}")
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))

        prompt = f"""Evaluate candidate-job fit. Return ONLY valid JSON.

CANDIDATE: {name}
Skills: {', '.join(skills[:20]) if skills else 'Not specified'}
Experience: {experience} years
Summary: {candidate_data.get('summary', '')[:400]}

JOB:
{job_description[:2000]}

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

        result = await self._agenerate_json(prompt, temperature=0.15)

        if result:
            score = result.get('match_score', 50)
            if isinstance(score, str):
                nums = re.findall(r'\d+', score)
                score = int(nums[0]) if nums else 50
            result['match_score'] = max(0, min(100, int(score)))
            self._set_cache(cache_key, result)
            logger.info(f"🎯 [Gemini] Match: {name} → {result['match_score']}%")

        return result or {
            'match_score': 0,
            'matched_skills': [],
            'missing_skills': [],
            'strengths': [],
            'gaps': ['Analysis unavailable'],
            'recommendation': 'Manual review required',
        }

    # ==================================================================
    # BATCH CANDIDATE RANKING
    # ==================================================================

    async def rank_candidates_for_job(
        self,
        candidates: List[Dict],
        job_description: str,
        top_n: int = 10
    ) -> List[Dict]:
        """
        Rank candidates against a JD using pre-filter + batch Gemini scoring.
        Same 2-stage approach as LLMService for consistency.
        """
        start = time.time()

        # Stage 1: Fast keyword pre-filter (matches chat() approach)
        jd_lower = job_description.lower()
        jd_tokens = set(re.sub(r'[^\w\s#+.]', ' ', jd_lower).split())
        # Remove stop words (same list as chat())
        stop_words = {'find', 'me', 'the', 'a', 'an', 'is', 'are', 'in', 'for', 'and', 'or', 'with',
                      'who', 'show', 'list', 'get', 'all', 'best', 'top', 'candidates', 'candidate',
                      'can', 'you', 'i', 'we', 'our', 'have', 'has', 'do', 'does', 'what', 'how',
                      'need', 'want', 'looking', 'search', 'tell', 'about', 'give', 'please',
                      'any', 'some', 'good', 'from', 'to', 'of', 'that', 'this', 'it', 'be',
                      'position', 'role', 'job', 'hiring'}
        jd_keywords = {t for t in jd_tokens if len(t) >= 2 and t not in stop_words}

        # Location aliases for flexible matching
        location_aliases = {
            'uae': ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'united arab emirates'],
            'usa': ['united states', 'new york', 'california', 'texas', 'florida'],
            'uk': ['united kingdom', 'london', 'manchester', 'england'],
            'india': ['mumbai', 'delhi', 'bangalore', 'bengaluru', 'hyderabad', 'chennai', 'pune', 'kolkata', 'noida', 'india'],
            'gcc': ['saudi arabia', 'kuwait', 'bahrain', 'oman', 'qatar', 'dubai', 'abu dhabi', 'riyadh'],
            'ksa': ['saudi arabia', 'riyadh', 'jeddah', 'dammam'],
        }
        expanded_keywords = set(jd_keywords)
        for alias, expansions in location_aliases.items():
            if alias in jd_keywords:
                expanded_keywords.update(expansions)

        # Skill synonyms for flexible matching
        skill_synonyms = {
            'ml': {'machine', 'learning'}, 'ai': {'artificial', 'intelligence'},
            'rpa': {'robotic', 'process', 'automation', 'uipath', 'blueprism'},
            'react': {'reactjs'}, 'reactjs': {'react'}, 'node': {'nodejs'}, 'nodejs': {'node'},
            'js': {'javascript'}, 'javascript': {'js'}, 'ts': {'typescript'}, 'typescript': {'ts'},
            'devops': {'cicd', 'docker', 'kubernetes', 'jenkins', 'terraform'},
            'fullstack': {'full stack', 'frontend', 'backend'},
            'sql': {'mysql', 'postgresql', 'oracle', 'database'},
            'qa': {'testing', 'quality assurance', 'selenium'},
            'automate': {'automation', 'rpa'}, 'automation': {'automate', 'rpa'},
            'cyber': {'cybersecurity', 'security'}, 'security': {'cybersecurity', 'infosec'},
            'sap': {'erp'}, 'erp': {'sap'},
            'scrum': {'agile'}, 'agile': {'scrum'},
        }

        pre_scored = []
        for idx, c in enumerate(candidates):
            skills = [s.lower().strip() for s in c.get('skills', [])]
            category = str(c.get('jobCategory', c.get('job_category', ''))).lower()
            subcategory = str(c.get('jobSubcategory', c.get('job_subcategory', ''))).lower()
            location = str(c.get('location', '')).lower()
            summary = str(c.get('summary', '')).lower()

            # Word-boundary matching (not substring) for skills with synonym support
            skill_hits = 0
            for s in skills:
                s_words = set(re.sub(r'[^\w\s]', ' ', s).split())
                if s_words & expanded_keywords:
                    skill_hits += 1
                    continue
                # Also match whole skill name as keyword (e.g. 'rpa' == 'rpa')
                matched = False
                for kw in expanded_keywords:
                    if kw == s or (len(kw) >= 3 and (kw in s.split() or s in kw.split())):
                        skill_hits += 1
                        matched = True
                        break
                # Check synonyms if no direct match
                if not matched:
                    for kw in expanded_keywords:
                        syns = skill_synonyms.get(kw, set())
                        if syns and (syns & s_words or s in syns):
                            skill_hits += 1
                            break

            # Category/subcategory match
            cat_hits = sum(1 for kw in expanded_keywords if kw in category.split() or kw in subcategory.split())

            # Location match
            loc_hits = sum(1 for kw in expanded_keywords if kw in location)

            # Summary word match
            summary_words = set(summary.split())
            summary_hits = len(summary_words & expanded_keywords)

            exp = c.get('experience', 0) or 0
            pre_score = skill_hits * 12 + cat_hits * 10 + loc_hits * 8 + min(summary_hits, 5) * 3 + min(exp, 15) * 0.5
            pre_scored.append((pre_score, idx, c))

        pre_scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        # Take more candidates for better Gemini recall
        deep_count = min(max(top_n * 3, 30), len(pre_scored))
        # Prefer candidates with actual relevance
        relevant = [(s, i, c) for s, i, c in pre_scored if s > 0]
        shortlisted = [c for _, _, c in (relevant[:deep_count] if relevant else pre_scored[:deep_count])]

        logger.info(f"🎯 [Gemini] Pre-filtered {len(candidates)} → {len(shortlisted)} candidates")

        # Stage 2: Batch Gemini scoring (5 per prompt)
        BATCH_SIZE = 5
        results = []

        # Check cache first
        uncached = []
        for c in shortlisted:
            ck = self._cache_key("fast_match", f"{c.get('name','')}:{c.get('email','')}:{job_description[:200]}")
            cached = self._get_cached(ck)
            if cached:
                results.append({'candidate': c, 'match': cached, 'score': cached.get('match_score', 0)})
            else:
                uncached.append(c)

        # Batch process uncached — PARALLEL with asyncio.gather for speed
        batches = [uncached[i:i + BATCH_SIZE] for i in range(0, len(uncached), BATCH_SIZE)]

        async def _process_batch(batch):
            batch_results_list = []
            try:
                batch_results = await self._batch_match(batch, job_description)
                for j, c in enumerate(batch):
                    if j < len(batch_results):
                        match_data = batch_results[j]
                        ck = self._cache_key("fast_match", f"{c.get('name','')}:{c.get('email','')}:{job_description[:200]}")
                        self._set_cache(ck, match_data)
                        batch_results_list.append({'candidate': c, 'match': match_data, 'score': match_data.get('match_score', 0)})
                    else:
                        ps = next((p for p, _, cc in pre_scored if cc is c), 0)
                        batch_results_list.append({
                            'candidate': c,
                            'match': {'match_score': min(int(ps * 2), 100), 'strengths': ['Pre-filter matched'], 'gaps': ['Deep analysis pending']},
                            'score': min(int(ps * 2), 100),
                        })
            except Exception as e:
                logger.warning(f"[Gemini] Batch match error: {e}")
                for c in batch:
                    match = await self.match_candidate_to_job(c, job_description)
                    batch_results_list.append({'candidate': c, 'match': match, 'score': match.get('match_score', 0)})
            return batch_results_list

        # Run up to 4 batches concurrently (rate-limit friendly)
        CONCURRENT_LIMIT = 4
        for chunk_start in range(0, len(batches), CONCURRENT_LIMIT):
            chunk = batches[chunk_start:chunk_start + CONCURRENT_LIMIT]
            batch_outputs = await asyncio.gather(*[_process_batch(b) for b in chunk])
            for batch_output in batch_outputs:
                results.extend(batch_output)

        results.sort(key=lambda x: x['score'], reverse=True)
        elapsed = time.time() - start
        logger.info(f"⚡ [Gemini] Ranking done: {len(results)} results in {elapsed:.1f}s")
        return results[:top_n]

    async def _batch_match(self, batch: List[Dict], job_description: str) -> List[Dict]:
        """Score multiple candidates in a single Gemini call."""
        candidates_text = ""
        for i, c in enumerate(batch, 1):
            skills_str = ', '.join(c.get('skills', [])[:12]) or 'Not specified'
            candidates_text += f"\nCANDIDATE {i}: {c.get('name', 'Unknown')}\n  Skills: {skills_str}\n  Experience: {c.get('experience', 0)} years\n  Summary: {c.get('summary', '')[:200]}\n"

        n = len(batch)
        prompt = f"""Score each candidate against the job. Return ONLY valid JSON with a "candidates" array of exactly {n} objects.

{candidates_text}

JOB:
{job_description[:1500]}

Return: {{"candidates": [{{"match_score": 75, "matched_skills": ["Python"], "missing_skills": ["Go"], "strengths": ["Strong backend"], "gaps": ["No cloud"], "recommendation": "Good fit"}}]}}"""

        result = await self._agenerate_json(prompt, temperature=0.1)
        if not result:
            raise ValueError("Empty Gemini batch response")

        batch_results = result.get('candidates', [])
        if isinstance(result, list):
            batch_results = result

        normalized = []
        for item in batch_results:
            if not isinstance(item, dict):
                continue
            score = item.get('match_score', 50)
            if isinstance(score, str):
                nums = re.findall(r'\d+', score)
                score = int(nums[0]) if nums else 50
            item['match_score'] = max(0, min(100, int(score)))
            normalized.append(item)

        return normalized

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

        result = await self._agenerate_json(prompt, temperature=0.2)
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
    ) -> Union[str, Dict]:
        """AI chat assistant with intelligent 2-stage database search.
        
        Stage 1: Pre-filter candidates using keyword extraction from user query
        Stage 2: Send relevant subset to Gemini for intelligent analysis
        
        This allows searching the ENTIRE database cost-effectively.
        
        If return_candidates=True, returns a dict with 'response' and 'candidates_lookup'
        mapping [N] indices to candidate data from the database.
        """
        ctx = context or {}
        total = ctx.get('totalCandidates', 0)
        avg_score = ctx.get('avgMatchScore', 0)
        strong = ctx.get('strongMatches', 0)
        categories = ctx.get('categories', {})
        
        # Track selected candidates for returning alongside response
        _selected_candidates = []

        # --- STAGE 1: Intelligent Pre-filtering ---
        # Extract keywords from user query for candidate pre-filtering
        query_lower = message.lower()
        
        # Build category summary for general questions
        cat_summary = ""
        if categories:
            cat_lines = [f"  \u2022 {cat}: {info.get('count', info) if isinstance(info, dict) else info} candidates" for cat, info in sorted(categories.items(), key=lambda x: x[1].get('count', 0) if isinstance(x[1], dict) else x[1], reverse=True)[:15]]
            cat_summary = "\nCategories breakdown:\n" + "\n".join(cat_lines)

        candidates_context = ""
        relevant_count = 0
        total_scanned = 0
        
        if candidates_data:
            total_scanned = len(candidates_data)
            
            # Smart pre-filter: score each candidate against query keywords
            scored_candidates = []
            query_tokens = set(re.sub(r'[^\w\s]', ' ', query_lower).split())
            # Remove common stop words
            stop_words = {'find', 'me', 'the', 'a', 'an', 'is', 'are', 'in', 'for', 'and', 'or', 'with', 
                         'who', 'show', 'list', 'get', 'all', 'best', 'top', 'candidates', 'candidate',
                         'can', 'you', 'i', 'we', 'our', 'have', 'has', 'do', 'does', 'what', 'how',
                         'need', 'want', 'looking', 'search', 'tell', 'about', 'give', 'please',
                         'any', 'some', 'good', 'from', 'to', 'of', 'that', 'this', 'it', 'be',
                         'position', 'role', 'job', 'hiring'}
            # Keep meaningful keywords
            keywords = query_tokens - stop_words
            
            # Location aliases for flexible matching
            location_aliases = {
                'uae': ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'fujairah', 'ras al khaimah', 'umm al quwain', 'united arab emirates'],
                'usa': ['united states', 'new york', 'california', 'texas', 'florida', 'chicago', 'los angeles', 'san francisco'],
                'uk': ['united kingdom', 'london', 'manchester', 'birmingham', 'england', 'scotland'],
                'india': ['mumbai', 'delhi', 'bangalore', 'bengaluru', 'hyderabad', 'chennai', 'pune', 'kolkata'],
                'gcc': ['saudi arabia', 'kuwait', 'bahrain', 'oman', 'qatar', 'dubai', 'abu dhabi', 'riyadh', 'doha'],
                'ksa': ['saudi arabia', 'riyadh', 'jeddah', 'dammam', 'mecca', 'medina'],
            }
            
            # Expand location keywords
            expanded_keywords = set(keywords)
            for alias, expansions in location_aliases.items():
                if alias in keywords:
                    expanded_keywords.update(expansions)
            
            # Common skill synonyms for flexible matching
            skill_synonyms = {
                'ml': {'machine', 'learning', 'machine learning'},
                'ai': {'artificial', 'intelligence', 'artificial intelligence'},
                'nlp': {'natural', 'language', 'processing', 'natural language processing'},
                'rpa': {'robotic', 'process', 'automation', 'uipath', 'blueprism', 'automation anywhere'},
                'react': {'reactjs', 'react.js'},
                'reactjs': {'react', 'react.js'},
                'node': {'nodejs', 'node.js'},
                'nodejs': {'node', 'node.js'},
                'js': {'javascript'},
                'javascript': {'js'},
                'ts': {'typescript'},
                'typescript': {'ts'},
                'python': {'django', 'flask', 'fastapi'},
                'devops': {'cicd', 'ci/cd', 'docker', 'kubernetes', 'jenkins', 'terraform'},
                'cloud': {'aws', 'azure', 'gcp', 'google cloud'},
                'aws': {'amazon web services', 'cloud'},
                'azure': {'microsoft azure', 'cloud'},
                'gcp': {'google cloud', 'cloud'},
                'fullstack': {'full stack', 'full-stack', 'frontend', 'backend'},
                'frontend': {'front-end', 'front end', 'react', 'angular', 'vue'},
                'backend': {'back-end', 'back end', 'api', 'server'},
                'sql': {'mysql', 'postgresql', 'oracle', 'database'},
                'database': {'sql', 'nosql', 'mongodb', 'postgresql', 'mysql'},
                'data': {'analytics', 'analysis', 'science'},
                'qa': {'testing', 'quality assurance', 'test automation', 'selenium'},
                'testing': {'qa', 'quality assurance', 'test'},
                'automate': {'automation', 'rpa', 'scripting'},
                'automation': {'automate', 'rpa', 'scripting'},
                'cyber': {'cybersecurity', 'security', 'infosec'},
                'security': {'cybersecurity', 'infosec', 'soc', 'siem'},
                'sap': {'erp', 'sap hana', 'sap s/4hana'},
                'erp': {'sap', 'oracle erp', 'dynamics'},
                'hr': {'human resources', 'recruitment', 'talent acquisition'},
                'pm': {'project management', 'project manager'},
                'scrum': {'agile', 'sprint', 'kanban'},
                'agile': {'scrum', 'sprint', 'kanban'},
            }
                    
            for idx, c in enumerate(candidates_data):
                relevance = 0
                name = str(c.get('name', '')).lower()
                skills = [s.lower() for s in c.get('skills', [])]
                skills_str = ' '.join(skills)
                category = str(c.get('jobCategory', c.get('job_category', ''))).lower()
                subcategory = str(c.get('jobSubcategory', c.get('job_subcategory', ''))).lower()
                location = str(c.get('location', '')).lower()
                summary = str(c.get('summary', '')).lower()
                experience = c.get('experience', 0) or 0
                score = c.get('matchScore', c.get('match_score', 0)) or 0
                
                # Score based on keyword matches (word-boundary, not substring)
                for kw in expanded_keywords:
                    if len(kw) < 2:
                        continue
                    # Also check skill synonyms
                    kw_synonyms = skill_synonyms.get(kw, set())
                    # Skills: check each skill individually with word matching + synonyms
                    for s in skills:
                        s_words = set(re.sub(r'[^\w\s]', ' ', s).split())
                        if kw in s_words or kw == s:
                            relevance += 20
                            break
                        # Check synonyms (e.g. 'ml' matches 'machine learning')
                        if kw_synonyms and (kw_synonyms & s_words or s in kw_synonyms):
                            relevance += 18
                            break
                    # Category/subcategory: word match
                    if kw in category.split() or kw in subcategory.split():
                        relevance += 15
                    if kw in location:
                        relevance += 15
                    if kw in name.split():
                        relevance += 25  # Direct name search
                    # Summary: word match (not substring)
                    if kw in set(summary.split()):
                        relevance += 5
                
                # Boost by match score
                relevance += score * 0.1
                
                # Recency boost — newly synced candidates get prioritized
                created_at = c.get('created_at', '')
                if created_at:
                    try:
                        from datetime import datetime, timedelta
                        # Parse created_at (format: YYYY-MM-DD HH:MM:SS or ISO)
                        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00')) if 'T' in created_at else datetime.strptime(created_at[:19], '%Y-%m-%d %H:%M:%S')
                        now = datetime.utcnow()
                        age_hours = (now - created_dt.replace(tzinfo=None)).total_seconds() / 3600
                        if age_hours <= 24:
                            relevance += 15  # Strong boost for last 24h
                        elif age_hours <= 72:
                            relevance += 10  # Medium boost for last 3 days
                        elif age_hours <= 168:
                            relevance += 5   # Small boost for last week
                        
                        # Recency-specific queries get extra boost
                        if any(w in query_lower for w in ['new', 'recent', 'latest', 'today', 'week', 'fresh']):
                            if age_hours <= 168:
                                relevance += 20
                    except (ValueError, TypeError):
                        pass
                
                # Experience-based queries
                if any(w in query_lower for w in ['senior', 'experienced', 'lead', 'principal', 'manager']):
                    if experience >= 7:
                        relevance += 10
                elif any(w in query_lower for w in ['junior', 'entry', 'fresher', 'graduate', 'intern']):
                    if experience <= 3:
                        relevance += 10
                
                scored_candidates.append((relevance, idx, c))
            
            # Sort by relevance and take top candidates (idx as tiebreaker to avoid dict comparison)
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            
            # Determine how many to include based on relevance distribution
            # If query is very specific, take fewer but more relevant candidates
            # If query is general, take more candidates
            has_specific_keywords = len(keywords) >= 2
            
            if has_specific_keywords:
                # Take candidates with relevance > 0, up to 150
                relevant = [(score, idx, c) for score, idx, c in scored_candidates if score > 0]
                selected = relevant[:150] if relevant else scored_candidates[:80]
            else:
                # General query — take top 150 (ensures new candidates aren't buried)
                selected = scored_candidates[:150]
            
            relevant_count = len(selected)
            
            # Store selected candidates indexed by [N] position for frontend matching
            _selected_candidates = [c for (_score, _idx, c) in selected[:150]]
            
            # Build context with rich candidate info — include ALL available data
            candidates_context = f"\n\nCANDIDATE DATABASE ({relevant_count} most relevant of {total_scanned} scanned):\n"
            for i, (rel_score, _idx, c) in enumerate(selected[:150]):
                skills_str = ', '.join(c.get('skills', [])[:25])
                work = c.get('workHistory', c.get('work_history', []))
                work_str = '; '.join([f"{w.get('title', '')} at {w.get('company', '')} ({w.get('duration', '')})" for w in work[:4]]) if work else 'N/A'
                edu = c.get('education', [])
                edu_str = '; '.join([f"{e.get('degree', '')} - {e.get('institution', '')}" for e in edu[:3]]) if edu else 'N/A'
                certs = c.get('certifications', [])
                certs_str = ', '.join(certs[:5]) if certs else ''
                langs = c.get('languages', [])
                langs_str = ', '.join(langs[:5]) if langs else ''
                summary_text = str(c.get('summary', ''))[:500]
                email_str = c.get('email', 'N/A')
                phone_str = c.get('phone', 'N/A')
                linkedin_str = c.get('linkedin', '')
                status_str = c.get('status', 'New')
                
                candidates_context += (
                    f"[{i+1}] {c.get('name', 'Unknown')} | Score: {c.get('matchScore', 0)}% | "
                    f"Status: {status_str} | "
                    f"{c.get('jobCategory', c.get('job_category', 'General'))} | "
                    f"Exp: {c.get('experience', 0)}yrs | {c.get('location', 'N/A')}\n"
                    f"   Email: {email_str} | Phone: {phone_str}"
                )
                if linkedin_str:
                    candidates_context += f" | LinkedIn: {linkedin_str}"
                candidates_context += f"\n   Skills: {skills_str}\n"
                candidates_context += f"   Work History: {work_str}\n"
                candidates_context += f"   Education: {edu_str}\n"
                if certs_str:
                    candidates_context += f"   Certifications: {certs_str}\n"
                if langs_str:
                    candidates_context += f"   Languages: {langs_str}\n"
                if summary_text:
                    candidates_context += f"   Summary: {summary_text}\n"

        history_text = ""
        if conversation_history:
            for msg in conversation_history[-10:]:
                history_text += f"\n{msg.get('role', 'user')}: {msg.get('content', '')[:500]}"

        # Build category breakdown for context
        cat_list = ', '.join([f"{k}: {v}" for k, v in list(categories.items())[:15]]) if categories else 'No category data'

        prompt = f"""You are the AI Recruitment Intelligence Agent for Efforts Solutions — the most advanced AI-powered recruitment platform. You are a world-class talent acquisition specialist with COMPLETE, UNRESTRICTED access to the entire candidate database.

YOUR CAPABILITIES:
• You can search, filter, rank, compare, and analyze ANY candidate in the database
• You understand technical roles, non-technical roles, management, and executive positions 
• You know UAE/GCC/Middle East recruitment dynamics (visa, sponsorship, Emiratization)
• You can handle: candidate search, job matching, skill gap analysis, team building, salary insights, hiring strategy, pipeline analytics, diversity analysis, and more

DATABASE SNAPSHOT:
• Total active candidates: {total}
• Strong matches (70%+ score): {strong}
• Average candidate score: {avg_score:.1f}%
• Categories: {cat_list}

SEARCH RESULTS: Scanned {total_scanned} candidates, showing {relevant_count} most relevant below.
{candidates_context}

CONVERSATION HISTORY:{history_text}

USER QUERY: {message}

RESPONSE RULES:
1. ALWAYS use REAL candidate data from the database above — names, scores, skills, locations, work history, education. NEVER fabricate or hallucinate candidates.
2. When listing candidates, use this format for EACH candidate:
   **#N. Candidate Name** | Score: X% | Category | Experience: Xyrs | Location
   - Skills: list key skills
   - Work History: relevant roles
   - Education: degrees
   - Match Reasoning: why they fit the query
   - Contact: email, phone if available
3. For search/find queries: thoroughly check ALL {relevant_count} candidates listed above, rank by relevance to the query, show ALL matches (not just top 3-5)
4. Location matching: UAE includes Dubai, Abu Dhabi, Sharjah, Ajman, RAK, etc. Match flexibly (city, country, region).
5. Skill matching: consider synonyms (e.g., "RPA" = "Robotic Process Automation" = "UiPath" = "Automation Anywhere" = "Blue Prism")
6. For "how many" / statistics queries: count accurately from the data provided
7. For comparison queries: side-by-side analysis with strengths and weaknesses
8. ALWAYS provide actionable next steps: "Shortlist this candidate", "Schedule interview", "Review full profile"
9. If results are limited, suggest adjusting criteria (e.g., "Try expanding location to all UAE" or "Consider candidates with 5+ years instead of 10+")
10. Use rich markdown: **bold** names/scores, bullet points, horizontal rules between candidates
11. Be comprehensive — do not truncate results. If 15 candidates match, show all 15.
12. For any query you don't understand, ask a clarifying question rather than giving a generic answer.
13. If asked about shortlisted candidates, filter by status=Shortlisted.
14. Include the candidate's current status (New, Strong, Shortlisted, etc.) in results."""

        result = await self._agenerate(prompt, temperature=0.3, max_tokens=3500)

        text_response = result or f"I'm here to help! We have **{total} candidates** in the database. What would you like to know?"
        
        if return_candidates:
            # Return dict with response text + the candidates lookup (1-indexed)
            # Each candidate is a lightweight dict with id, name, etc.
            candidates_lookup = []
            for i, c in enumerate(_selected_candidates):
                candidates_lookup.append({
                    'index': i + 1,  # 1-indexed to match [N] in prompt
                    'id': c.get('id', ''),
                    'name': c.get('name', ''),
                    'matchScore': c.get('matchScore', c.get('match_score', 0)),
                    'location': c.get('location', ''),
                    'jobCategory': c.get('jobCategory', c.get('job_category', '')),
                    'experience': c.get('experience', 0),
                    'skills': c.get('skills', [])[:10],
                    'email': c.get('email', ''),
                    'phone': c.get('phone', ''),
                    'status': c.get('status', 'New'),
                })
            return {
                'response': text_response,
                'candidates_lookup': candidates_lookup
            }
        
        return text_response

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

        result = await self._agenerate_json(prompt, temperature=0.3)
        if result and 'questions' in result:
            return result['questions'][:num_questions]
        return [{"question": f"Tell me about your experience with {skills[0] if skills else 'your field'}.", "type": "technical", "difficulty": "medium", "skill_tested": skills[0] if skills else "General", "what_to_look_for": "Depth of knowledge"}]

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

        result = await self._agenerate_json(prompt, temperature=0.05)
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

        result = await self._agenerate_json(prompt, temperature=0.4)
        return result or {'subject': f'Re: {template_type}', 'body': 'Template unavailable', 'variables': [], 'tips': ''}

    # ==================================================================
    # STATUS & METRICS
    # ==================================================================

    def get_status(self) -> Dict:
        """Get Gemini service status."""
        avg_time = self._total_time / self._request_count if self._request_count > 0 else 0
        return {
            'available': self.available,
            'model': self.model_name,
            'api_key_set': bool(self.api_key),
            'requests_processed': self._request_count,
            'average_response_time': round(avg_time, 2),
            'error_count': self._error_count,
            'cache_size': len(self._cache),
        }

    def clear_cache(self):
        """Clear response cache."""
        self._cache.clear()
        logger.info("🗑️ Gemini cache cleared")


# ============================================================================
# SINGLETON
# ============================================================================

_gemini_service: Optional[GeminiService] = None


def get_gemini_service() -> Optional[GeminiService]:
    """Get or create Gemini service singleton."""
    global _gemini_service
    if _gemini_service is None:
        import os
        api_key = os.getenv("GEMINI_API_KEY", "")
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        if api_key:
            _gemini_service = GeminiService(api_key=api_key, model_name=model)
        else:
            logger.info("💡 GEMINI_API_KEY not set — Gemini service not initialized")
            return None
    return _gemini_service
