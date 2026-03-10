"""
Local LLM Service - Powered by Ollama (Enhanced) + Smart AI Tier Fallback
==========================================================================
Uses Ollama to run local LLMs for highly accurate information extraction
with GPU acceleration, retry logic, confidence scoring, and advanced
JSON repair.

Smart AI Tier System (auto-detected by environment):
  LOCAL DEV:  Ollama → Gemini → OpenAI → Keyword
  PRODUCTION: Gemini → OpenAI → Ollama → Keyword

When a request fails on the primary tier, it automatically falls through
to the next available engine — no manual intervention needed.

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
import os
import re
import time
import hashlib
import random
from typing import Dict, List, Optional, Any, Tuple
from functools import lru_cache

logger = logging.getLogger(__name__)


# ==========================================================================
# GPU ACCELERATION HELPERS
# ==========================================================================

def configure_gpu_for_ollama() -> Dict[str, Any]:
    """
    Detect GPU and return Ollama-compatible configuration options for
    maximum inference speed.  Returns a dict that callers can merge into
    the Ollama ``options`` payload.
    """
    gpu_info: Dict[str, Any] = {
        "gpu_available": False,
        "gpu_name": None,
        "gpu_memory_mb": 0,
        "num_gpu": 0,          # Ollama: layers offloaded to GPU
        "num_thread": None,    # Ollama: CPU threads
        "numa": False,         # NUMA node awareness
    }
    try:
        import torch
        if torch.cuda.is_available():
            gpu_info["gpu_available"] = True
            gpu_info["gpu_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            gpu_info["gpu_memory_mb"] = getattr(props, 'total_memory', getattr(props, 'total_mem', 0)) // (1024 * 1024)
            # Offload all layers to GPU
            gpu_info["num_gpu"] = 999
            logger.info(f"🚀 GPU acceleration enabled: {gpu_info['gpu_name']} "
                        f"({gpu_info['gpu_memory_mb']} MB)")
        else:
            # Optimise CPU: use all available cores
            cpu_count = os.cpu_count() or 4
            gpu_info["num_thread"] = max(cpu_count - 1, 1)
            logger.info(f"⚡ CPU inference with {gpu_info['num_thread']} threads")
    except ImportError:
        cpu_count = os.cpu_count() or 4
        gpu_info["num_thread"] = max(cpu_count - 1, 1)
    return gpu_info


# ==========================================================================
# ADVANCED JSON REPAIR
# ==========================================================================

def repair_json(text: str) -> Optional[Dict]:
    """
    Aggressively try to extract valid JSON from potentially malformed LLM
    output.  Handles:
    - Markdown code fences
    - Trailing commas
    - Single quotes
    - Unquoted keys
    - Truncated output (missing closing braces)
    """
    if not text:
        return None

    # Strip markdown fences
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = text.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Extract largest {...} blob
    brace_match = re.search(r'\{[\s\S]*\}', text)
    if brace_match:
        candidate = brace_match.group()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # 2b. Fix trailing commas before } or ]
        fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        # 2c. Replace single quotes with double quotes (careful around apostrophes)
        fixed2 = re.sub(r"(?<![a-zA-Z])'|'(?![a-zA-Z])", '"', fixed)
        try:
            return json.loads(fixed2)
        except json.JSONDecodeError:
            pass

    # 3. Handle truncated JSON — add missing closing braces/brackets
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')
    if open_braces > 0 or open_brackets > 0:
        patched = text + (']' * max(open_brackets, 0)) + ('}' * max(open_braces, 0))
        patched = re.sub(r',\s*([}\]])', r'\1', patched)
        try:
            return json.loads(patched)
        except json.JSONDecodeError:
            pass

    return None


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
    
    # Model configuration - configurable via environment variables
    PRIMARY_MODEL = os.getenv("OLLAMA_PRIMARY_MODEL", "qwen2.5:7b")
    FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "phi3.5")
    REASONING_MODEL = os.getenv("OLLAMA_REASONING_MODEL", "llama3.1:8b")
    
    # Ollama API base URL
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    
    def __init__(self):
        self.available = False
        self.available_models: List[str] = []
        self.primary_model = self.PRIMARY_MODEL
        self.fast_model = self.FAST_MODEL
        self.reasoning_model = self.REASONING_MODEL
        
        # GPU / inference configuration
        self._gpu_config = configure_gpu_for_ollama()
        
        # Response cache for performance
        self._cache: Dict[str, Any] = {}
        self._cache_max_size = 2000
        self._cache_ttl = 3600  # 1 hour (was 10 min)
        
        # Performance tracking
        self._request_count = 0
        self._total_time = 0.0
        self._error_count = 0
        
        # Retry configuration
        self._max_retries = int(os.getenv("LLM_MAX_RETRIES", "3"))
        self._base_retry_delay = 1.0  # seconds
        
        # Initialize
        self._http_client = None
        logger.info("🤖 LLM Service initialized (Ollama-based) | "
                     f"GPU: {self._gpu_config.get('gpu_name', 'CPU')} | "
                     f"Retries: {self._max_retries}")
    
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
        max_tokens: int = 2048,
        json_mode: bool = False
    ) -> str:
        """
        Generate text using Ollama LLM with automatic retry & exponential
        back-off.  GPU layers are offloaded when available.
        """
        if not self.available:
            return ""
        
        model = model or self.primary_model
        start_time = time.time()
        
        # Build Ollama options — merge GPU config
        options: Dict[str, Any] = {
            "temperature": temperature,
            "num_predict": max_tokens,
            "top_p": 0.9,
            "top_k": 40,
        }
        if self._gpu_config.get("num_gpu"):
            options["num_gpu"] = self._gpu_config["num_gpu"]
        if self._gpu_config.get("num_thread"):
            options["num_thread"] = self._gpu_config["num_thread"]
        if self._gpu_config.get("numa"):
            options["numa"] = True
        
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"
        
        last_error: Optional[Exception] = None
        
        for attempt in range(1, self._max_retries + 1):
            try:
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
                        tps = tokens / elapsed if elapsed > 0 else 0
                        logger.debug(f"🤖 LLM [{model}]: {tokens} tokens in {elapsed:.1f}s "
                                     f"({tps:.0f} tok/s) attempt={attempt}")
                        
                        return result.strip()
                    else:
                        last_error = Exception(f"HTTP {response.status_code}: {response.text[:200]}")
                        self._error_count += 1
                        logger.warning(f"Ollama error (attempt {attempt}/{self._max_retries}): "
                                       f"{response.status_code}")
                else:
                    # Fallback to aiohttp
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            f"{self.OLLAMA_BASE_URL}/api/generate",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=90)
                        ) as response:
                            if response.status == 200:
                                data = await response.json()
                                result = data.get("response", "")
                                
                                elapsed = time.time() - start_time
                                self._request_count += 1
                                self._total_time += elapsed
                                
                                return result.strip()
                            else:
                                last_error = Exception(f"HTTP {response.status}")
                                self._error_count += 1
                        
            except Exception as e:
                last_error = e
                self._error_count += 1
            
            # Exponential back-off with jitter before retry
            if attempt < self._max_retries:
                delay = self._base_retry_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.info(f"⏳ LLM retry {attempt}/{self._max_retries} in {delay:.1f}s …")
                await asyncio.sleep(delay)
        
        elapsed = time.time() - start_time
        logger.error(f"LLM generation failed after {self._max_retries} attempts "
                     f"({elapsed:.1f}s): {last_error}")
        return ""
    
    async def _generate_json(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.05,
    ) -> Optional[Dict]:
        """Generate structured JSON output from LLM with advanced repair.
        If Ollama is unavailable, falls through the AI tier chain (Gemini → OpenAI)."""
        
        # If Ollama is available, try it first
        if self.available:
            result = await self._generate(
                prompt=prompt,
                model=model,
                system=system,
                temperature=temperature,
                json_mode=True
            )
            
            if result:
                parsed = repair_json(result)
                if parsed is not None:
                    return parsed
                
                # Last resort: ask LLM to fix its own output
                logger.warning(f"JSON repair failed – raw LLM output (first 300 chars): {result[:300]}")
                fix_prompt = (
                    "The following text was supposed to be valid JSON but is malformed. "
                    "Return ONLY the corrected valid JSON, nothing else:\n\n" + result[:3000]
                )
                retry_result = await self._generate(
                    prompt=fix_prompt,
                    model=model or self.fast_model,
                    temperature=0.0,
                    json_mode=True,
                )
                if retry_result:
                    parsed = repair_json(retry_result)
                    if parsed is not None:
                        logger.info("✅ JSON self-repair succeeded")
                        return parsed
                
                logger.warning(f"Failed to parse JSON from LLM response after repair: {result[:200]}")
        
        # Ollama unavailable or failed — try tier fallback (Gemini → OpenAI)
        return await self._tier_generate_json(prompt, temperature=temperature, model=model, system=system)
    
    # ========================================================================
    # SMART AI TIER FALLBACK
    # ========================================================================
    
    async def _tier_generate_json(
        self,
        prompt: str,
        temperature: float = 0.05,
        model: Optional[str] = None,
        system: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Generate JSON using the smart AI tier chain.
        Tries engines in priority order based on config (auto-detected by environment).
        
        LOCAL DEV:  Ollama → Gemini → OpenAI → None
        PRODUCTION: Gemini → OpenAI → Ollama → None
        """
        from core.config import get_settings
        settings = get_settings()
        tier_order = settings.ai_tier_order
        
        for engine in tier_order:
            try:
                if engine == "ollama" and self.available:
                    # Use _generate() directly to avoid infinite recursion
                    # (_generate_json → _tier_generate_json → _generate_json)
                    raw = await self._generate(
                        prompt=prompt, model=model, system=system,
                        temperature=temperature, json_mode=True
                    )
                    if raw:
                        parsed = repair_json(raw)
                        if parsed is not None:
                            return parsed
                
                elif engine == "gemini":
                    from services.gemini_service import get_gemini_service
                    gemini_svc = get_gemini_service()
                    if gemini_svc and gemini_svc.available:
                        # Combine system + prompt for Gemini (it doesn't have separate system/user)
                        full_prompt = f"{system}\n\n{prompt}" if system else prompt
                        result = await gemini_svc._agenerate_json(full_prompt, temperature=temperature)
                        if result:
                            logger.info(f"🌟 Tier fallback: Gemini handled request")
                            return result
                
                elif engine == "openai":
                    from services.openai_service import get_openai_service
                    openai_svc = get_openai_service()
                    if openai_svc:
                        # OpenAI is synchronous — run in executor
                        import asyncio
                        loop = asyncio.get_event_loop()
                        # Build messages for OpenAI chat
                        messages = []
                        if system:
                            messages.append({"role": "system", "content": system})
                        messages.append({"role": "user", "content": prompt})
                        
                        def _openai_call():
                            try:
                                response = openai_svc.client.chat.completions.create(
                                    model=openai_svc.model,
                                    messages=messages,
                                    max_tokens=2000,
                                    temperature=temperature,
                                    response_format={"type": "json_object"}
                                )
                                import json
                                return json.loads(response.choices[0].message.content)
                            except Exception as ex:
                                logger.warning(f"OpenAI tier call failed: {ex}")
                                return None
                        
                        result = await loop.run_in_executor(None, _openai_call)
                        if result:
                            logger.info(f"💳 Tier fallback: OpenAI handled request")
                            return result
                
                elif engine == "keyword":
                    # Terminal — no LLM available, return None
                    break
                    
            except Exception as e:
                logger.warning(f"⚠️ AI tier '{engine}' failed: {e}")
                continue
        
        return None
    
    async def _tier_generate(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        system: Optional[str] = None,
    ) -> str:
        """
        Generate free-form text using the smart AI tier chain.
        """
        from core.config import get_settings
        settings = get_settings()
        tier_order = settings.ai_tier_order
        
        for engine in tier_order:
            try:
                if engine == "ollama" and self.available:
                    result = await self._generate(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
                    if result:
                        return result
                
                elif engine == "gemini":
                    from services.gemini_service import get_gemini_service
                    gemini_svc = get_gemini_service()
                    if gemini_svc and gemini_svc.available:
                        full_prompt = f"{system}\n\n{prompt}" if system else prompt
                        result = await gemini_svc._agenerate(full_prompt, temperature=temperature, max_tokens=max_tokens)
                        if result:
                            return result
                
                elif engine == "openai":
                    from services.openai_service import get_openai_service
                    openai_svc = get_openai_service()
                    if openai_svc:
                        import asyncio
                        loop = asyncio.get_event_loop()
                        messages = []
                        if system:
                            messages.append({"role": "system", "content": system})
                        messages.append({"role": "user", "content": prompt})
                        
                        def _openai_text():
                            try:
                                response = openai_svc.client.chat.completions.create(
                                    model=openai_svc.model,
                                    messages=messages,
                                    max_tokens=max_tokens,
                                    temperature=temperature,
                                )
                                return response.choices[0].message.content.strip()
                            except Exception:
                                return ""
                        
                        result = await loop.run_in_executor(None, _openai_text)
                        if result:
                            return result
                
                elif engine == "keyword":
                    break
                    
            except Exception as e:
                logger.warning(f"⚠️ AI tier '{engine}' text generation failed: {e}")
                continue
        
        return ""
    
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
        Parse resume text using LLM for highly-accurate structured extraction.
        
        Enhanced with:
        - Multi-pass extraction (LLM pass → regex validation pass)
        - Confidence scoring per field
        - Cross-field consistency checks
        - Hallucination filtering
        """
        if not text or len(text.strip()) < 30:
            return None
        
        # Check cache
        cache_key = self._get_cache_key("resume_v2", text)
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        from services.job_taxonomy import get_taxonomy_prompt_text, classify_job_title
        taxonomy_text = get_taxonomy_prompt_text()
        
        system = """You are an expert resume parser with 15+ years experience in talent acquisition. Your task is to extract ALL information from the resume with maximum accuracy.
CRITICAL RULES:
1. Return ONLY valid JSON — no comments, no markdown, no extra text.
2. Extract EVERY skill, EVERY job position, EVERY degree mentioned. Do not skip anything.
3. For skills: include ALL technical skills, programming languages, frameworks, tools, platforms, methodologies, and relevant soft skills actually mentioned.
4. For work history: extract EVERY position with the exact job title, company name, date range, and a detailed description of responsibilities and achievements (2-4 sentences per role).
5. For education: extract ALL degrees, diplomas, and certifications with institution name, field of study, and graduation year.
6. For the summary: write a detailed 4-6 sentence professional summary capturing career focus, domain expertise, key achievements, technical depth, and professional trajectory.
7. NEVER fabricate, guess, or hallucinate information — only extract what is explicitly stated in the resume.
8. If information is not found, use empty string or empty array — never make up data.
9. For experience_years: calculate from the earliest work start date to present, or use the number explicitly stated.
10. For location: extract the candidate's current location or the most recently mentioned location.
11. For confidence_score: rate 0-100 how confident you are about the overall extraction quality."""

        prompt = f"""Parse this resume and extract ALL information into the following JSON structure.
Be extremely thorough — extract every skill, every job, every educational qualification, every detail mentioned.

RESUME TEXT:
---
{text[:10000]}
---

JOB TAXONOMY (use these EXACT category and subcategory names):
{taxonomy_text}

Return ONLY valid JSON with this exact structure (replace ALL placeholder values with ACTUAL data from the resume):
{{
    "name": "<EXTRACT the candidate's actual full name from the resume>",
    "email": "<EXTRACT actual email address>",
    "phone": "<EXTRACT actual phone number>",
    "location": "<EXTRACT actual city/country>",
    "linkedin": "<EXTRACT actual LinkedIn URL or empty string>",
    "summary": "<WRITE a detailed 4-6 sentence professional summary based on the actual resume content>",
    "skills": ["<EXTRACT each actual skill mentioned in the resume>"],
    "experience_years": 0,
    "work_history": [
        {{
            "title": "<EXTRACT actual job title>",
            "company": "<EXTRACT actual company name>",
            "period": "<EXTRACT actual date range>",
            "description": "<EXTRACT actual job responsibilities and achievements>"
        }}
    ],
    "education": [
        {{
            "degree": "<EXTRACT actual degree type>",
            "field": "<EXTRACT actual field of study>",
            "institution": "<EXTRACT actual institution name>",
            "year": "<EXTRACT actual graduation year>"
        }}
    ],
    "certifications": ["<EXTRACT actual certification names>"],
    "languages": ["<EXTRACT actual languages mentioned>"],
    "job_category": "<Pick BEST matching category from taxonomy>",
    "job_subcategory": "<Pick BEST matching subcategory>",
    "confidence_score": 85
}}

CRITICAL RULES:
- Replace EVERY <EXTRACT ...> placeholder with REAL data from the resume above.
- NEVER use placeholder names like 'John Doe', 'Jane Smith', or 'Full name'. Extract the ACTUAL name.
- NEVER use placeholder emails like 'email@example.com'. Extract the ACTUAL email.
- NEVER invent or fabricate any data. Only extract what is explicitly written in the resume.
- If a field is not found in the resume, use an empty string or empty array.
- Set confidence_score (0-100) reflecting how confident you are in the overall extraction."""

        result = await self._generate_json(prompt, system=system, temperature=0.05)
        
        if result:
            # Normalize and validate
            result = self._normalize_resume_data(result)
            
            # ---- REGEX VALIDATION PASS ----
            # Cross-check critical fields using direct regex extraction from
            # the raw text to catch LLM hallucinations or omissions.
            result = self._regex_validation_pass(result, text)
            
            self._set_cache(cache_key, result)
            logger.info(f"📄 LLM Resume Parse: {result.get('name', 'Unknown')} | "
                       f"Skills: {len(result.get('skills', []))} | "
                       f"Exp: {result.get('experience_years', 0)}yrs | "
                       f"Confidence: {result.get('confidence_score', 0)}%")
        
        return result
    
    def _regex_validation_pass(self, data: Dict, raw_text: str) -> Dict:
        """
        Second-pass regex validation: verify/supplement LLM extraction with
        direct pattern matching on the raw text.  This catches cases where the
        LLM missed or hallucinated information.
        """
        # -- Email --
        if not data.get('email'):
            m = re.search(
                r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', raw_text
            )
            if m:
                candidate_email = m.group()
                # Reject obvious system/placeholder emails
                if 'example.com' not in candidate_email.lower():
                    data['email'] = candidate_email
        
        # -- Phone --
        if not data.get('phone'):
            phone_patterns = [
                r'\+971[\s.\-]?\d{1,2}[\s.\-]?\d{3}[\s.\-]?\d{4}',
                r'\+\d{1,3}[\s.\-]?\(?\d{2,4}\)?[\s.\-]?\d{3,4}[\s.\-]?\d{3,4}',
                r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}',
            ]
            for pat in phone_patterns:
                m = re.search(pat, raw_text)
                if m:
                    digits = re.sub(r'\D', '', m.group())
                    if len(digits) >= 7:
                        data['phone'] = m.group().strip()
                        break
        
        # -- LinkedIn --
        if not data.get('linkedin'):
            m = re.search(
                r'(?:https?://)?(?:www\.)?linkedin\.com/in/[a-zA-Z0-9\-]+',
                raw_text, re.IGNORECASE
            )
            if m:
                url = m.group()
                if not url.startswith('http'):
                    url = 'https://' + url
                data['linkedin'] = url
        
        # -- Experience years (cross-check) --
        if data.get('experience_years', 0) == 0:
            m = re.search(r'(\d{1,2})\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)',
                          raw_text, re.IGNORECASE)
            if m:
                data['experience_years'] = min(int(m.group(1)), 50)
        
        # -- Skills supplement: catch skills the LLM missed --
        existing_skills_lower = {s.lower() for s in data.get('skills', [])}
        text_lower = raw_text.lower()
        
        COMMON_SKILLS = [
            'python', 'java', 'javascript', 'typescript', 'react', 'angular', 'vue',
            'node.js', 'django', 'flask', 'fastapi', 'spring', 'docker', 'kubernetes',
            'aws', 'azure', 'gcp', 'terraform', 'sql', 'postgresql', 'mongodb',
            'redis', 'kafka', 'git', 'ci/cd', 'machine learning', 'deep learning',
            'tensorflow', 'pytorch', 'pandas', 'numpy', 'spark', 'hadoop',
            'figma', 'photoshop', 'excel', 'power bi', 'tableau',
            'agile', 'scrum', 'jira', 'confluence',
        ]
        for skill in COMMON_SKILLS:
            if skill in text_lower and skill not in existing_skills_lower:
                data.setdefault('skills', []).append(skill.title())
                existing_skills_lower.add(skill)
        
        # Deduplicate skills (case-insensitive)
        if data.get('skills'):
            seen: set = set()
            deduped: List[str] = []
            for s in data['skills']:
                key = s.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    deduped.append(s.strip())
            data['skills'] = deduped
        
        return data
    
    def _normalize_resume_data(self, data: Dict) -> Dict:
        """Normalize and validate parsed resume data"""
        from services.job_taxonomy import classify_job_title, get_category_for_subcategory
        
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
            'job_subcategory': str(data.get('job_subcategory', '')).strip(),
            'confidence_score': min(max(int(data.get('confidence_score', 0) or 0), 0), 100),
        }
        
        # Reject hallucinated/placeholder values — comprehensive list of common LLM fabrications
        PLACEHOLDER_NAMES = {
            'john doe', 'jane doe', 'jane smith', 'john smith', 'bob smith', 'bob jones',
            'alice johnson', 'alice smith', 'alice', 'ali', 'bob', 'test user', 'sample candidate',
            'full name', 'candidate name', 'unknown', 'n/a', 'none', 'extract',
            'first last', 'firstname lastname', 'name here', 'your name', 'the candidate',
            'alex johnson', 'michael smith', 'sarah connor', 'example name', 'candidate',
            'resume owner', 'applicant', 'job seeker', 'job applicant',
        }
        name_lower = normalized['name'].lower().strip()
        if (name_lower in PLACEHOLDER_NAMES or 
            normalized['name'].startswith('<') or
            normalized['name'].startswith('EXTRACT') or
            len(normalized['name']) < 2):
            normalized['name'] = ''
        
        PLACEHOLDER_EMAILS = {
            'email@example.com', 'candidate@email.com', 'name@email.com', 
            'john@example.com', 'user@example.com', 'alice@example.com',
            'test@test.com', 'sample@email.com', 'your@email.com',
            'firstname.lastname@email.com', 'name@domain.com',
        }
        if (normalized['email'].lower() in PLACEHOLDER_EMAILS or 
            normalized['email'].startswith('<') or
            normalized['email'].startswith('EXTRACT') or
            'example.com' in normalized['email'].lower()):
            normalized['email'] = ''
        
        PLACEHOLDER_LOCATIONS = {
            'city, country', 'new york, usa', 'new york', 'location', 'n/a', 'not specified',
            'san francisco, ca', 'san francisco, usa', 'city, state', 'somewhere', 'remote',
            'united states', 'usa', 'uk', 'any city', 'your city',
        }
        if (normalized['location'].lower() in PLACEHOLDER_LOCATIONS or 
            normalized['location'].startswith('<') or
            normalized['location'].startswith('EXTRACT')):
            normalized['location'] = ''
        
        PLACEHOLDER_PHONES = {'n/a', 'none', 'not specified', '123-456-7890', '(123) 456-7890',
                              '+1-234-567-8900', '000-000-0000', '555-555-5555'}
        if (normalized['phone'].startswith('<') or 
            normalized['phone'].startswith('EXTRACT') or
            normalized['phone'].lower() in PLACEHOLDER_PHONES):
            normalized['phone'] = ''
        
        if (normalized['linkedin'].startswith('<') or 
            normalized['linkedin'].startswith('EXTRACT') or
            'linkedin.com' not in normalized['linkedin']):
            normalized['linkedin'] = ''
        
        # Reject hallucinated summary that doesn't relate to actual resume content
        summary_lower = normalized['summary'].lower()
        PLACEHOLDER_SUMMARY_MARKERS = ['john doe', 'jane doe', 'alice johnson', 'bob smith',
                                        'a highly skilled professional', 'this candidate']
        for marker in PLACEHOLDER_SUMMARY_MARKERS:
            if marker in summary_lower:
                normalized['summary'] = ''
                break
        
        # Validate category/subcategory using taxonomy fallback
        if not normalized['job_subcategory'] or normalized['job_category'] == 'General':
            # Try to classify from most recent job title
            titles = []
            for w in data.get('work_history', []):
                if isinstance(w, dict) and w.get('title'):
                    titles.append(w['title'])
            if titles:
                cat, sub = classify_job_title(titles[0])
                if cat != 'General' or not normalized['job_category'] or normalized['job_category'] == 'General':
                    normalized['job_category'] = cat
                    normalized['job_subcategory'] = sub
        
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
        
        # Work history — filter out hallucinated/placeholder entries
        PLACEHOLDER_COMPANIES = {'tech innovations inc', 'tech innovations', 'acme corp', 'acme corporation',
                                  'example company', 'company name', 'abc corp', 'xyz inc', 'company inc',
                                  'previous company', 'current company', 'startup name', 'big tech company'}
        work = data.get('work_history', [])
        if isinstance(work, list):
            for w in work[:10]:
                if isinstance(w, dict):
                    title = str(w.get('title', '')).strip()
                    company = str(w.get('company', '')).strip()
                    # Skip entries with placeholder/hallucinated values
                    if (company.lower() in PLACEHOLDER_COMPANIES or
                        company.startswith('<') or company.startswith('EXTRACT') or
                        title.startswith('<') or title.startswith('EXTRACT')):
                        continue
                    if title and company:  # Only add entries with both title and company
                        normalized['work_history'].append({
                            'title': title,
                            'company': company,
                            'period': str(w.get('period', '')).strip(),
                            'description': str(w.get('description', '')).strip()[:500],
                        })
        
        # Education — filter out hallucinated/placeholder entries
        PLACEHOLDER_INSTITUTIONS = {'university name', 'university of technology', 'mit', 'harvard',
                                     'example university', 'school name', 'institution name',
                                     'state university', 'college name'}
        edu = data.get('education', [])
        if isinstance(edu, list):
            for e in edu[:5]:
                if isinstance(e, dict):
                    degree = str(e.get('degree', '')).strip()
                    institution = str(e.get('institution', '')).strip()
                    # Skip entries with placeholder values
                    if (institution.lower() in PLACEHOLDER_INSTITUTIONS or
                        institution.startswith('<') or institution.startswith('EXTRACT') or
                        degree.startswith('<') or degree.startswith('EXTRACT')):
                        continue
                    if degree:  # Only add if degree is specified
                        normalized['education'].append({
                            'degree': degree,
                            'field': str(e.get('field', '')).strip(),
                            'institution': institution,
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
        
        from services.job_taxonomy import get_taxonomy_prompt_text, classify_job_title
        taxonomy_text = get_taxonomy_prompt_text()
        
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

JOB TAXONOMY (use these EXACT category and subcategory names):
{taxonomy_text}

Return ONLY valid JSON (replace ALL placeholders with ACTUAL data from the email):
{{
    "name": "<EXTRACT the candidate's actual full name>",
    "email": "<EXTRACT actual email address>",
    "phone": "<EXTRACT actual phone number or empty string>",
    "location": "<EXTRACT actual location or empty string>",
    "skills": ["<EXTRACT each actual skill mentioned>"],
    "experience_years": 0,
    "education": [
        {{
            "degree": "<EXTRACT actual degree>",
            "field": "<EXTRACT actual field>",
            "institution": "<EXTRACT actual institution>",
            "year": ""
        }}
    ],
    "summary": "<WRITE a brief summary based on actual email content>",
    "linkedin": "<EXTRACT actual LinkedIn URL or empty string>",
    "job_applied_for": "<EXTRACT actual job title they applied for>",
    "job_category": "<Pick BEST matching category from taxonomy>",
    "job_subcategory": "<Pick BEST matching subcategory>",
    "source": "{source}",
    "is_candidate_email": true
}}

CRITICAL: Extract ONLY real data from the email. NEVER use placeholder names like 'John Doe'. If data is not found, use empty string.
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
        from services.job_taxonomy import classify_job_title
        
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
            'job_category': str(data.get('job_category', 'General')).strip(),
            'job_subcategory': str(data.get('job_subcategory', '')).strip(),
        }
        
        # Validate / fallback category from job title
        if not normalized['job_subcategory'] or normalized['job_category'] == 'General':
            title = normalized['job_applied_for']
            if title:
                cat, sub = classify_job_title(title)
                normalized['job_category'] = cat
                normalized['job_subcategory'] = sub
        
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
        Deep analysis of a candidate - generates detailed paragraph-style analysis
        with pros, cons, strengths, hiring recommendation, and actionable insights.
        Optimized prompt for faster inference (~15-25s vs original ~60-70s).
        """
        cache_key = self._get_cache_key("deep_v3", json.dumps(candidate_data, default=str))
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        education = candidate_data.get('education', [])
        work_history = candidate_data.get('work_history', candidate_data.get('workHistory', []))
        summary = candidate_data.get('summary', '')
        score = candidate_data.get('quality_score', candidate_data.get('matchScore', candidate_data.get('score', 0)))
        location = candidate_data.get('location', '')
        job_category = candidate_data.get('job_category', candidate_data.get('jobCategory', ''))
        
        system = """You are a senior recruiter. Provide specific, data-driven candidate assessments. Reference actual skills and experience. Be honest and constructive."""

        # Compact work history
        work_text = ""
        if work_history:
            for w in work_history[:4]:
                if isinstance(w, dict):
                    work_text += f"\n  - {w.get('title', '')} at {w.get('company', '')} ({w.get('duration', w.get('period', ''))})"
        
        prompt = f"""Assess this candidate. Return JSON only.

Name: {name} | Location: {location or 'N/A'} | Category: {job_category or 'N/A'}
Experience: {experience} years | Score: {score}%
Skills: {', '.join(skills[:20]) if skills else 'Not listed'}
Education: {json.dumps(education[:3], default=str) if education else 'N/A'}
Work History:{work_text if work_text else ' N/A'}
Summary: {summary[:400] if summary else 'N/A'}

Return JSON:
{{
    "executive_summary": "3-4 sentence assessment of overall profile and fit",
    "technical_assessment": "2-3 sentences on technical capabilities",
    "experience_assessment": "2-3 sentences on career progression",
    "education_assessment": "1-2 sentences on education relevance",
    "pros": ["specific pro 1", "specific pro 2", "specific pro 3", "specific pro 4"],
    "cons": ["specific con 1", "specific con 2"],
    "career_trajectory": "1-2 sentences on career direction",
    "ideal_roles": ["Role 1", "Role 2"],
    "interview_focus_areas": ["Topic 1", "Topic 2", "Topic 3"],
    "hiring_recommendation": "STRONGLY_RECOMMEND|RECOMMEND|CONSIDER|PASS",
    "hiring_recommendation_rationale": "2 sentence explanation",
    "confidence_score": 85,
    "overall_rating": "A|B+|B|C+|C|D",
    "key_differentiators": ["What makes them stand out"]
}}"""

        result = await self._generate_json(
            prompt, 
            model=self.fast_model, 
            system=system, 
            temperature=0.15
        )
        
        if result:
            # Ensure all fields exist with meaningful defaults
            defaults = {
                'executive_summary': f'{name} is a candidate with {experience} years of experience. Further analysis requires more detailed profile information.',
                'technical_assessment': 'Technical assessment requires more detailed skills information.',
                'experience_assessment': 'Experience assessment requires more detailed work history.',
                'education_assessment': 'Educational background information is limited.',
                'pros': [f'{name} has submitted their application and is in the pipeline'],
                'cons': ['More information is needed for a comprehensive assessment'],
                'career_trajectory': 'Career trajectory analysis requires more work history data.',
                'ideal_roles': ['General'],
                'interview_focus_areas': ['Background verification', 'Skills assessment', 'Cultural fit'],
                'salary_range_estimate': 'Insufficient data for salary estimation',
                'culture_fit_notes': 'Cultural fit assessment requires interview interaction.',
                'hiring_recommendation': 'CONSIDER',
                'hiring_recommendation_rationale': 'Insufficient data for a strong recommendation.',
                'confidence_score': 50,
                'overall_rating': 'C+',
                'key_differentiators': [],
                # Backward compatibility
                'overall_assessment': '',
                'strengths': [],
                'weaknesses': [],
                'recommended_roles': [],
                'development_areas': [],
            }
            for key, default in defaults.items():
                if key not in result or not result[key]:
                    result[key] = default
            
            # Map backward-compatible fields
            if not result.get('overall_assessment'):
                result['overall_assessment'] = result['executive_summary']
            if not result.get('strengths'):
                result['strengths'] = result['pros'][:5]
            if not result.get('weaknesses'):
                result['weaknesses'] = result['cons'][:3]
            if not result.get('recommended_roles'):
                result['recommended_roles'] = result['ideal_roles']
            
            self._set_cache(cache_key, result)
            logger.info(f"Deep Analysis: {name} -> {result.get('hiring_recommendation', 'N/A')} ({result.get('overall_rating', '?')})")
        
        return result or {
            'executive_summary': f'{name} is a candidate with {experience} years of experience. Detailed AI analysis could not be completed at this time.',
            'technical_assessment': f'Skills listed: {", ".join(skills[:10]) if skills else "none specified"}. A thorough technical evaluation is recommended during the interview process.',
            'experience_assessment': f'The candidate reports {experience} years of professional experience. Career progression details should be verified in an interview.',
            'education_assessment': 'Educational credentials should be verified.',
            'pros': [f'Has {experience} years of stated experience', f'Listed {len(skills)} skills in their profile', 'Application submitted and in pipeline'],
            'cons': ['AI deep analysis was unavailable — manual review recommended', 'Profile details need in-person verification'],
            'career_trajectory': 'Trajectory analysis unavailable.',
            'ideal_roles': ['General'],
            'interview_focus_areas': ['Technical skills verification', 'Experience validation', 'Cultural fit assessment'],
            'salary_range_estimate': 'Not determined',
            'culture_fit_notes': 'Requires interview assessment.',
            'hiring_recommendation': 'CONSIDER',
            'hiring_recommendation_rationale': 'AI analysis was not fully available. Manual review is recommended.',
            'confidence_score': 30,
            'overall_rating': 'C',
            'key_differentiators': [],
            'overall_assessment': f'{name} has {experience} years of experience. Manual review recommended.',
            'strengths': ['Resume submitted'],
            'weaknesses': ['Insufficient data for analysis'],
            'recommended_roles': ['General'],
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
        
        system = """You are an expert technical recruiter. Score candidates honestly based on actual qualifications vs job requirements. Be specific."""

        # Build richer candidate profile
        work_history = candidate_data.get('work_history', candidate_data.get('workHistory', []))
        work_text = ""
        if work_history:
            for w in work_history[:4]:
                if isinstance(w, dict):
                    work_text += f"\n  - {w.get('title', '')} at {w.get('company', '')} ({w.get('period', w.get('duration', ''))})"
        
        prompt = f"""Evaluate candidate-job fit. Return JSON only.

CANDIDATE: {name}
Skills: {', '.join(skills[:20]) if skills else 'Not specified'}
Experience: {experience} years
Education: {json.dumps(education[:2]) if education else 'N/A'}
Work History:{work_text if work_text else ' N/A'}
Summary: {summary[:400]}

JOB:
{job_description[:2000]}

Return JSON:
{{
    "match_score": 75,
    "skill_match_score": 80,
    "experience_match_score": 70,
    "overall_fit": "Good Match",
    "matched_skills": ["skill1", "skill2"],
    "missing_skills": ["skill1"],
    "strengths": ["strength1", "strength2", "strength3"],
    "gaps": ["gap1", "gap2"],
    "recommendation": "2-3 sentence fit summary",
    "interview_questions": ["question1", "question2"],
    "risk_factors": ["risk1"]
}}

Score: 90-100 perfect, 75-89 strong, 60-74 good, 40-59 partial, 0-39 poor"""

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
        """
        Rank candidates against a JD using a 2-stage approach for large databases:
        Stage 1: Fast keyword/skill pre-filter to narrow down candidates
        Stage 2: Batch LLM analysis — multiple candidates per prompt for speed
        
        Optimized for speed:
        - Aggressive pre-filtering reduces LLM calls
        - Batch scoring: 5 candidates per single LLM call (~5x faster)
        - Compact prompts for faster inference
        - Result caching prevents re-analysis
        """
        import time as _time
        start = _time.time()
        
        # ----- Stage 1: Fast keyword pre-filter -----
        jd_lower = job_description.lower()
        
        # Extract key terms from JD
        jd_keywords = set()
        for term in re.findall(r'[a-zA-Z#+.]+(?:\.[a-zA-Z]+)*', jd_lower):
            if len(term) >= 2:
                jd_keywords.add(term)
        
        # Score every candidate quickly by skill overlap + experience
        pre_scored = []
        for idx, c in enumerate(candidates):
            skills = [s.lower() for s in c.get('skills', [])]
            skill_hits = sum(1 for s in skills if any(kw in s or s in kw for kw in jd_keywords))
            summary_lower = str(c.get('summary', '')).lower()
            cat_lower = str(c.get('jobCategory', c.get('job_category', ''))).lower()
            summary_hits = sum(1 for kw in jd_keywords if kw in summary_lower) * 0.3
            cat_hit = 3 if any(kw in cat_lower for kw in jd_keywords if len(kw) > 3) else 0
            exp = c.get('experience', 0) or 0
            base_score = c.get('matchScore', c.get('match_score', 0)) or 0
            
            pre_score = skill_hits * 8 + summary_hits + cat_hit + min(exp, 15) * 1.2 + base_score * 0.15
            pre_scored.append((pre_score, idx, c))
        
        # Sort by pre-score and take only top candidates for deep analysis
        pre_scored.sort(key=lambda x: x[0], reverse=True)
        # Deep-analyze top_n + 5 candidates
        deep_analysis_count = min(top_n + 5, len(pre_scored))
        shortlisted = [c for _, _, c in pre_scored[:deep_analysis_count]]
        
        logger.info(f"🎯 JD Match: Pre-filtered {len(candidates)} → {len(shortlisted)} candidates for batch LLM analysis")
        
        # ----- Stage 2: Batch LLM analysis (multiple candidates per prompt) -----
        CANDIDATES_PER_PROMPT = 5  # Score 5 candidates in a single LLM call
        results = []
        
        # Check cache first and separate cached vs uncached
        uncached = []
        for candidate in shortlisted:
            cache_key = self._get_cache_key(
                "fast_match",
                f"{candidate.get('name','')}:{candidate.get('email','')}:{job_description[:200]}"
            )
            cached = self._get_cached(cache_key)
            if cached:
                results.append({
                    'candidate': candidate,
                    'match': cached,
                    'score': cached.get('match_score', 0)
                })
            else:
                uncached.append(candidate)
        
        if uncached:
            logger.info(f"⚡ {len(results)} cached, {len(uncached)} need LLM scoring in {(len(uncached) + CANDIDATES_PER_PROMPT - 1) // CANDIDATES_PER_PROMPT} batch call(s)")
        
        # Process uncached candidates in batches of CANDIDATES_PER_PROMPT
        for i in range(0, len(uncached), CANDIDATES_PER_PROMPT):
            batch = uncached[i:i + CANDIDATES_PER_PROMPT]
            try:
                batch_results = await self._batch_match_candidates(batch, job_description)
                for j, candidate in enumerate(batch):
                    if j < len(batch_results):
                        match_data = batch_results[j]
                        # Cache the result
                        cache_key = self._get_cache_key(
                            "fast_match",
                            f"{candidate.get('name','')}:{candidate.get('email','')}:{job_description[:200]}"
                        )
                        self._set_cache(cache_key, match_data)
                        results.append({
                            'candidate': candidate,
                            'match': match_data,
                            'score': match_data.get('match_score', 0)
                        })
                    else:
                        # If LLM returned fewer results than expected, use pre-filter score
                        pre_score = next((ps for c, ps in pre_scored if c is candidate), 0)
                        results.append({
                            'candidate': candidate,
                            'match': {
                                'match_score': min(int(pre_score * 2), 100),
                                'matched_skills': [],
                                'missing_skills': [],
                                'strengths': ['Pre-filter matched'],
                                'gaps': ['Deep analysis pending'],
                                'recommendation': 'Review candidate profile',
                            },
                            'score': min(int(pre_score * 2), 100)
                        })
            except Exception as e:
                logger.warning(f"Batch match error: {e}")
                # Fallback: score individually for this batch
                for candidate in batch:
                    try:
                        match = await self._fast_match_candidate(candidate, job_description)
                        results.append({
                            'candidate': candidate,
                            'match': match,
                            'score': match.get('match_score', 0)
                        })
                    except Exception as ex:
                        logger.warning(f"Individual match error for {candidate.get('name', 'Unknown')}: {ex}")
        
        # Sort by LLM score descending
        results.sort(key=lambda x: x['score'], reverse=True)
        
        elapsed = _time.time() - start
        logger.info(f"⚡ JD Match complete: {len(results)} results in {elapsed:.1f}s (batch mode, {len(uncached)} LLM-scored)")
        
        return results[:top_n]
    
    async def _batch_match_candidates(
        self,
        candidates_batch: List[Dict],
        job_description: str
    ) -> List[Dict]:
        """
        Score multiple candidates in a single LLM call for dramatic speed improvement.
        Returns a list of match dicts (one per candidate, in order).
        """
        # Build compact candidate summaries
        candidates_text = ""
        for i, c in enumerate(candidates_batch, 1):
            name = c.get('name', 'Unknown')
            skills = c.get('skills', [])
            experience = c.get('experience', c.get('experience_years', 0))
            summary = c.get('summary', '')[:200]
            skills_str = ', '.join(skills[:12]) if skills else 'Not specified'
            candidates_text += f"\nCANDIDATE {i}: {name}\n  Skills: {skills_str}\n  Experience: {experience} years\n  Summary: {summary}\n"
        
        n = len(candidates_batch)
        prompt = f"""Score each candidate below against the job description. Return valid JSON only.

{candidates_text}

JOB DESCRIPTION:
{job_description[:1500]}

Return a JSON object with a "candidates" array containing exactly {n} objects (one per candidate, in order).
Each object must have: "match_score" (0-100), "matched_skills" (list), "missing_skills" (list), "strengths" (list of 1-2), "gaps" (list of 1-2), "recommendation" (one sentence).

Example format:
{{"candidates": [{{"match_score": 75, "matched_skills": ["Python"], "missing_skills": ["Go"], "strengths": ["Strong backend"], "gaps": ["No cloud exp"], "recommendation": "Good fit"}}]}}"""
        
        result = await self._generate_json(
            prompt,
            model=self.primary_model,
            temperature=0.1
        )
        
        if not result:
            logger.warning("Batch match returned empty — falling back to individual scoring")
            raise ValueError("Empty batch LLM response")
        
        # Extract candidates array from response
        batch_results = result.get('candidates', [])
        if not isinstance(batch_results, list):
            # Maybe the result IS the array
            if isinstance(result, list):
                batch_results = result
            else:
                raise ValueError(f"Unexpected batch format: {type(result)}")
        
        # Normalize scores
        normalized = []
        for i, item in enumerate(batch_results):
            if not isinstance(item, dict):
                continue
            score = item.get('match_score', 50)
            if isinstance(score, str):
                numbers = re.findall(r'\d+', score)
                score = int(numbers[0]) if numbers else 50
            item['match_score'] = max(0, min(100, int(score)))
            normalized.append(item)
            
            # Log each score
            name = candidates_batch[i].get('name', 'Unknown') if i < len(candidates_batch) else 'Unknown'
            logger.info(f"🎯 Job Match: {name} → {item['match_score']}%")
        
        return normalized
    
    async def _fast_match_candidate(
        self,
        candidate_data: Dict,
        job_description: str
    ) -> Dict:
        """
        Fast match for batch ranking — uses compact prompt for speed.
        Caches results. ~3-5x faster than full match_candidate_to_job.
        """
        # Check cache first
        cache_key = self._get_cache_key(
            "fast_match",
            f"{candidate_data.get('name','')}:{candidate_data.get('email','')}:{job_description[:200]}"
        )
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        name = candidate_data.get('name', 'Unknown')
        skills = candidate_data.get('skills', [])
        experience = candidate_data.get('experience', candidate_data.get('experience_years', 0))
        summary = candidate_data.get('summary', '')[:300]
        
        # Compact prompt — much shorter for fast inference
        skills_str = ', '.join(skills[:15]) if skills else 'Not specified'
        json_template = '{"match_score": <0-100>, "matched_skills": ["skill1"], "missing_skills": ["skill1"], "strengths": ["str1"], "gaps": ["gap1"], "recommendation": "fit summary"}'
        prompt = f"""Rate this candidate for the job. Return valid JSON only.

CANDIDATE: {name}
Skills: {skills_str}
Experience: {experience} years
Summary: {summary}

JOB: {job_description[:1500]}

Return JSON with this exact structure: {json_template}"""
        
        result = await self._generate_json(
            prompt,
            model=self.primary_model,
            temperature=0.1
        )
        
        if result:
            score = result.get('match_score', 50)
            if isinstance(score, str):
                numbers = re.findall(r'\d+', score)
                score = int(numbers[0]) if numbers else 50
            result['match_score'] = max(0, min(100, int(score)))
            self._set_cache(cache_key, result)
            logger.info(f"🎯 Job Match: {name} → {result['match_score']}%")
        
        return result or {
            'match_score': 0,
            'matched_skills': [],
            'missing_skills': [],
            'strengths': [],
            'gaps': ['Analysis unavailable'],
            'recommendation': 'Manual review required',
        }
    
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
        conversation_history: Optional[List[Dict]] = None,
        candidates_data: Optional[List[Dict]] = None
    ) -> str:
        """
        AI chat assistant with intelligent 2-stage database search.
        Stage 1: Pre-filter candidates using keyword extraction from user query
        Stage 2: Send relevant subset to LLM for intelligent analysis
        """
        ctx = context or {}
        total = ctx.get('totalCandidates', 0)
        avg_score = ctx.get('avgMatchScore', 0)
        strong = ctx.get('strongMatches', 0)
        recent = ctx.get('recentCount', 0)
        categories = ctx.get('categories', {})
        
        # Build category summary
        cat_summary = ""
        if categories:
            cat_lines = [f"  • {cat}: {info.get('count', info) if isinstance(info, dict) else info} candidates" for cat, info in sorted(categories.items(), key=lambda x: x[1].get('count', 0) if isinstance(x[1], dict) else x[1], reverse=True)[:15]]
            cat_summary = "\nCategories breakdown:\n" + "\n".join(cat_lines)
        
        # --- STAGE 1: Smart Pre-filtering ---
        query_lower = message.lower()
        candidates_context = ""
        relevant_count = 0
        total_scanned = 0
        
        if candidates_data:
            total_scanned = len(candidates_data)
            scored_candidates = []
            query_tokens = set(re.sub(r'[^\w\s]', ' ', query_lower).split())
            stop_words = {'find', 'me', 'the', 'a', 'an', 'is', 'are', 'in', 'for', 'and', 'or', 'with',
                         'who', 'show', 'list', 'get', 'all', 'best', 'top', 'candidates', 'candidate',
                         'can', 'you', 'i', 'we', 'our', 'have', 'has', 'do', 'does', 'what', 'how',
                         'need', 'want', 'looking', 'search', 'tell', 'about', 'give', 'please',
                         'any', 'some', 'good', 'from', 'to', 'of', 'that', 'this', 'it', 'be'}
            keywords = query_tokens - stop_words
            
            location_aliases = {
                'uae': ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'fujairah', 'ras al khaimah', 'umm al quwain', 'united arab emirates'],
                'usa': ['united states', 'new york', 'california', 'texas', 'florida', 'chicago', 'los angeles', 'san francisco'],
                'uk': ['united kingdom', 'london', 'manchester', 'birmingham', 'england', 'scotland'],
                'india': ['mumbai', 'delhi', 'bangalore', 'bengaluru', 'hyderabad', 'chennai', 'pune', 'kolkata'],
                'gcc': ['saudi arabia', 'kuwait', 'bahrain', 'oman', 'qatar', 'dubai', 'abu dhabi', 'riyadh', 'doha'],
                'ksa': ['saudi arabia', 'riyadh', 'jeddah', 'dammam', 'mecca', 'medina'],
            }
            
            expanded_keywords = set(keywords)
            for alias, expansions in location_aliases.items():
                if alias in keywords:
                    expanded_keywords.update(expansions)
            
            for idx, c in enumerate(candidates_data):
                relevance = 0
                name = str(c.get('name', '')).lower()
                skills = [s.lower() for s in c.get('skills', [])]
                skills_str = ' '.join(skills)
                category = str(c.get('jobCategory', c.get('job_category', ''))).lower()
                subcategory = str(c.get('jobSubcategory', c.get('job_subcategory', ''))).lower()
                location = str(c.get('location', '')).lower()
                summary = str(c.get('summary', '')).lower()
                score = c.get('matchScore', c.get('match_score', 0)) or 0
                experience = c.get('experience', 0) or 0
                
                for kw in expanded_keywords:
                    if len(kw) < 2: continue
                    if kw in skills_str: relevance += 20
                    if kw in category or kw in subcategory: relevance += 15
                    if kw in location: relevance += 15
                    if kw in name: relevance += 25
                    if kw in summary: relevance += 5
                
                relevance += score * 0.1
                
                if any(w in query_lower for w in ['senior', 'experienced', 'lead', 'principal', 'manager']):
                    if experience >= 7: relevance += 10
                elif any(w in query_lower for w in ['junior', 'entry', 'fresher', 'graduate', 'intern']):
                    if experience <= 3: relevance += 10
                
                scored_candidates.append((relevance, idx, c))
            
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            has_specific_keywords = len(keywords) >= 2
            
            if has_specific_keywords:
                relevant = [(s, i, c) for s, i, c in scored_candidates if s > 0]
                selected = relevant[:100] if relevant else scored_candidates[:50]
            else:
                selected = scored_candidates[:80]
            
            relevant_count = len(selected)
            
            candidates_context = f"\n\nCANDIDATE DATABASE ({relevant_count} most relevant of {total_scanned} scanned):\n"
            for i, (rel_score, _idx, c) in enumerate(selected[:100]):
                skills_str = ', '.join(c.get('skills', [])[:15])
                work = c.get('workHistory', c.get('work_history', []))
                work_str = '; '.join([f"{w.get('title', '')} at {w.get('company', '')}" for w in work[:3]]) if work else 'N/A'
                edu = c.get('education', [])
                edu_str = '; '.join([f"{e.get('degree', '')} from {e.get('institution', '')}" for e in edu[:2]]) if edu else 'N/A'
                summary_text = str(c.get('summary', ''))[:200]
                
                candidates_context += (
                    f"[{i+1}] {c.get('name', 'Unknown')} | Score: {c.get('matchScore', 0)}% | "
                    f"{c.get('jobCategory', c.get('job_category', 'General'))} | "
                    f"Exp: {c.get('experience', 0)}yrs | {c.get('location', 'N/A')}\n"
                    f"   Skills: {skills_str}\n"
                    f"   Work: {work_str} | Edu: {edu_str}\n"
                    f"   {summary_text}\n"
                )
        
        system = f"""You are the AI Recruitment Intelligence Agent for Efforts Solutions, a premier HR technology company specializing in AI-powered recruitment across the Middle East and globally.
You are a world-class talent acquisition specialist with deep expertise in technical and non-technical recruiting, candidate evaluation, JD analysis, and hiring strategy.
You have FULL ACCESS to the candidate database and must provide specific, data-driven, actionable answers.

DATABASE OVERVIEW:
• Total candidates: {total} | Strong matches (70%+): {strong} | Average score: {avg_score:.1f}% | Recent 24h: {recent}
{cat_summary}

SEARCH CONTEXT: Scanned {total_scanned} candidates, showing {relevant_count} most relevant to query.
{candidates_context}

CRITICAL INSTRUCTIONS:
1. ALWAYS reference actual candidate data — use real names, scores, skills, locations, experience
2. When searching for candidates: analyze ALL listed candidates thoroughly, rank by fit
3. For job/JD queries: extract required skills, experience level, location — match against database
4. For location queries: match city, country, and region flexibly (UAE = Dubai/Abu Dhabi/Sharjah etc.)
5. Present results in clear structured format: numbered list with candidate details
6. Include match reasoning for each recommended candidate
7. If few matches found, suggest what criteria to adjust
8. Use markdown formatting: **bold** for names, bullet points for details
9. NEVER fabricate candidates or data — only reference what's in the database
10. For salary/visa/notice period questions: note these need direct candidate contact
11. When asked about categories/departments: reference the category breakdown provided
12. For comparison queries: create side-by-side analysis with pros/cons
13. Be proactive — if the query implies a search, DO the search and present results
14. Provide brief actionable next steps (e.g., "Schedule interview", "Request visa docs")"""

        # Build conversation context
        history_text = ""
        if conversation_history:
            for msg in conversation_history[-8:]:  # Last 8 messages for better context
                role = msg.get('role', 'user')
                content = msg.get('content', '')[:300]
                history_text += f"\n{role}: {content}"
        
        prompt = f"""{history_text}
User: {message}

Analyze the query carefully. If it's a candidate search, match against ALL candidate data (skills, experience, location, job category, work history).
If it's a JD or job requirement, extract key skills/requirements and rank matching candidates.
If it's about hiring strategy or analytics, provide data-driven insights from the database.
Provide a detailed, actionable response with specific candidate names and data:"""

        result = await self._tier_generate(
            prompt,
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
        
        # Get tier information
        try:
            from core.config import get_settings
            settings = get_settings()
            tier_order = settings.ai_tier_order
            tier_mode = settings.ai_tier_mode
        except Exception:
            tier_order = ["ollama", "keyword"]
            tier_mode = "auto"
        
        # Check Gemini availability
        gemini_available = False
        try:
            from services.gemini_service import get_gemini_service
            g = get_gemini_service()
            gemini_available = g.available if g else False
        except Exception:
            pass
        
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
            'gpu': {
                'enabled': self._gpu_config.get('gpu_available', False),
                'name': self._gpu_config.get('gpu_name'),
                'memory_mb': self._gpu_config.get('gpu_memory_mb', 0),
                'num_gpu_layers': self._gpu_config.get('num_gpu', 0),
                'cpu_threads': self._gpu_config.get('num_thread'),
            },
            'max_retries': self._max_retries,
            'ai_tier_mode': tier_mode,
            'ai_tier_order': tier_order,
            'gemini_available': gemini_available,
            'tier_fallback_enabled': True,
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
    """Get or create LLM service singleton, re-checking availability if not connected.
    In production (Cloud Run), skip Ollama re-check to avoid wasted HTTP calls."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
        # Skip Ollama init in production — it's never available on Cloud Run
        if not (os.getenv('K_SERVICE') or os.getenv('ENVIRONMENT', '').lower() == 'production'):
            await _llm_service.initialize()
    elif not _llm_service.available:
        # Re-check Ollama availability — but NOT in production
        if not (os.getenv('K_SERVICE') or os.getenv('ENVIRONMENT', '').lower() == 'production'):
            await _llm_service.initialize()
    return _llm_service


def get_llm_service_sync() -> LLMService:
    """Get LLM service without initialization (for sync contexts)"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
