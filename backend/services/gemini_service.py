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

# ── Shared constants for pre-filter scoring (used by both chat() and rank_candidates_for_job()) ──

STOP_WORDS = frozenset({
    'find', 'me', 'the', 'a', 'an', 'is', 'are', 'in', 'for', 'and', 'or', 'with',
    'who', 'show', 'list', 'get', 'all', 'best', 'top', 'candidates', 'candidate',
    'can', 'you', 'i', 'we', 'our', 'have', 'has', 'do', 'does', 'what', 'how',
    'need', 'want', 'looking', 'search', 'tell', 'about', 'give', 'please',
    'any', 'some', 'good', 'from', 'to', 'of', 'that', 'this', 'it', 'be',
    'position', 'role', 'job', 'hiring', 'work', 'working', 'prefer', 'preferred',
    'should', 'must', 'minimum', 'experience', 'years', 'year', 'office',
})

LOCATION_ALIASES = {
    # Country / region aliases
    'uae': ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'fujairah', 'ras al khaimah', 'umm al quwain', 'united arab emirates'],
    'usa': ['united states', 'new york', 'california', 'texas', 'florida', 'chicago', 'los angeles', 'san francisco', 'seattle', 'boston', 'austin'],
    'uk': ['united kingdom', 'london', 'manchester', 'birmingham', 'england', 'scotland'],
    'india': ['mumbai', 'delhi', 'bangalore', 'bengaluru', 'hyderabad', 'chennai', 'pune', 'kolkata', 'noida', 'gurgaon', 'gurugram', 'jaipur', 'ahmedabad', 'kochi', 'cochin', 'coimbatore', 'thiruvananthapuram', 'trivandrum'],
    'gcc': ['saudi arabia', 'kuwait', 'bahrain', 'oman', 'qatar', 'dubai', 'abu dhabi', 'riyadh', 'doha', 'muscat'],
    'ksa': ['saudi arabia', 'riyadh', 'jeddah', 'dammam', 'mecca', 'medina'],
    # Indian city <-> state bidirectional aliases
    'chennai': ['tamil nadu', 'tamilnadu', 'tn', 'madras'],
    'tamil nadu': ['chennai', 'coimbatore', 'madurai', 'trichy', 'salem', 'tamilnadu'],
    'tamilnadu': ['chennai', 'tamil nadu', 'coimbatore', 'madurai'],
    'bangalore': ['bengaluru', 'karnataka'],
    'bengaluru': ['bangalore', 'karnataka'],
    'karnataka': ['bangalore', 'bengaluru', 'mysore', 'mysuru', 'mangalore'],
    'hyderabad': ['telangana', 'andhra pradesh', 'secunderabad'],
    'telangana': ['hyderabad', 'secunderabad', 'warangal'],
    'mumbai': ['maharashtra', 'bombay', 'navi mumbai', 'thane'],
    'maharashtra': ['mumbai', 'pune', 'nagpur', 'nashik', 'thane'],
    'delhi': ['new delhi', 'ncr', 'noida', 'gurgaon', 'gurugram', 'faridabad', 'ghaziabad'],
    'ncr': ['delhi', 'new delhi', 'noida', 'gurgaon', 'gurugram', 'faridabad', 'ghaziabad'],
    'noida': ['delhi', 'ncr', 'uttar pradesh'],
    'gurgaon': ['gurugram', 'delhi', 'ncr', 'haryana'],
    'gurugram': ['gurgaon', 'delhi', 'ncr', 'haryana'],
    'pune': ['maharashtra'],
    'kolkata': ['west bengal', 'calcutta'],
    'kochi': ['cochin', 'kerala', 'ernakulam'],
    'cochin': ['kochi', 'kerala'],
    'kerala': ['kochi', 'cochin', 'thiruvananthapuram', 'trivandrum', 'kozhikode', 'calicut'],
    'dubai': ['uae', 'united arab emirates'],
    'abu dhabi': ['uae', 'united arab emirates'],
    'sharjah': ['uae', 'united arab emirates'],
    'riyadh': ['ksa', 'saudi arabia'],
    'jeddah': ['ksa', 'saudi arabia'],
    # East Asia / SEA
    'singapore': ['sg'],
    'malaysia': ['kuala lumpur', 'kl'],
}

# Comprehensive skill synonyms for flexible matching
SKILL_SYNONYMS = {
    # AI / ML
    'ml': {'machine learning', 'machine', 'learning'},
    'machine learning': {'ml', 'deep learning', 'neural networks'},
    'ai': {'artificial intelligence', 'machine learning', 'ml', 'deep learning'},
    'artificial intelligence': {'ai', 'ml'},
    'nlp': {'natural language processing', 'text mining', 'text analytics'},
    'natural language processing': {'nlp'},
    'deep learning': {'dl', 'neural networks', 'tensorflow', 'pytorch'},
    'computer vision': {'cv', 'image processing', 'opencv'},
    # RPA / Automation
    'rpa': {'robotic process automation', 'uipath', 'blueprism', 'blue prism', 'automation anywhere', 'power automate'},
    'robotic process automation': {'rpa'},
    'uipath': {'rpa', 'robotic process automation'},
    'blueprism': {'rpa', 'blue prism'},
    'blue prism': {'rpa', 'blueprism'},
    'automation anywhere': {'rpa'},
    'power automate': {'microsoft power automate', 'power platform', 'rpa'},
    'microsoft power automate': {'power automate', 'power platform'},
    'power platform': {'power automate', 'power apps', 'power bi'},
    # JavaScript ecosystem
    'react': {'reactjs', 'react.js', 'react js'},
    'reactjs': {'react', 'react.js'},
    'react.js': {'react', 'reactjs'},
    'angular': {'angularjs', 'angular.js', 'angular js'},
    'angularjs': {'angular', 'angular.js'},
    'vue': {'vuejs', 'vue.js', 'vue js'},
    'vuejs': {'vue', 'vue.js'},
    'next': {'nextjs', 'next.js'},
    'nextjs': {'next', 'next.js'},
    'next.js': {'next', 'nextjs'},
    'node': {'nodejs', 'node.js', 'node js'},
    'nodejs': {'node', 'node.js'},
    'node.js': {'node', 'nodejs'},
    'express': {'expressjs', 'express.js'},
    'js': {'javascript'},
    'javascript': {'js', 'ecmascript', 'es6'},
    'ts': {'typescript'},
    'typescript': {'ts'},
    'jquery': {'jquery'},
    # Python ecosystem
    'python': {'py', 'python3'},
    'django': {'django rest framework', 'drf'},
    'flask': {'flask'},
    'fastapi': {'fast api'},
    # Java / JVM
    'java': {'j2ee', 'jee', 'jdk'},
    'spring': {'spring boot', 'springboot', 'spring framework'},
    'spring boot': {'spring', 'springboot'},
    'springboot': {'spring', 'spring boot'},
    'kotlin': {'kt'},
    'scala': {'scala'},
    # .NET / C#
    'c#': {'csharp', 'c sharp', '.net', 'dotnet'},
    'csharp': {'c#', '.net'},
    '.net': {'dotnet', 'c#', 'csharp', 'asp.net'},
    'dotnet': {'.net', 'c#'},
    'asp.net': {'.net', 'dotnet', 'c#'},
    # C / C++
    'c++': {'cpp', 'c plus plus', 'cplusplus'},
    'cpp': {'c++', 'c plus plus'},
    # DevOps / Cloud
    'devops': {'ci/cd', 'cicd', 'docker', 'kubernetes', 'jenkins', 'terraform', 'ansible'},
    'cicd': {'ci/cd', 'continuous integration', 'continuous deployment', 'devops'},
    'ci/cd': {'cicd', 'continuous integration', 'devops'},
    'docker': {'container', 'containerization'},
    'kubernetes': {'k8s', 'container orchestration'},
    'k8s': {'kubernetes'},
    'terraform': {'tf', 'infrastructure as code', 'iac'},
    'ansible': {'configuration management'},
    'aws': {'amazon web services', 'cloud', 'ec2', 's3', 'lambda'},
    'amazon web services': {'aws'},
    'azure': {'microsoft azure', 'cloud'},
    'microsoft azure': {'azure'},
    'gcp': {'google cloud', 'google cloud platform', 'cloud'},
    'google cloud': {'gcp', 'google cloud platform'},
    'cloud': {'aws', 'azure', 'gcp'},
    # Databases
    'sql': {'mysql', 'postgresql', 'oracle', 'database', 'rdbms', 'mssql', 'sql server'},
    'mysql': {'sql', 'database', 'rdbms'},
    'postgresql': {'postgres', 'sql', 'database'},
    'postgres': {'postgresql'},
    'mongodb': {'mongo', 'nosql'},
    'mongo': {'mongodb', 'nosql'},
    'nosql': {'mongodb', 'cassandra', 'redis', 'dynamodb'},
    'oracle': {'oracle db', 'plsql', 'pl/sql'},
    'sql server': {'mssql', 'microsoft sql server', 'tsql'},
    'mssql': {'sql server', 'microsoft sql server'},
    'redis': {'cache', 'in-memory database'},
    'database': {'sql', 'nosql', 'rdbms', 'dbms'},
    # Full-stack
    'fullstack': {'full stack', 'full-stack', 'frontend', 'backend'},
    'full stack': {'fullstack', 'full-stack'},
    'frontend': {'front-end', 'front end', 'ui', 'client-side'},
    'backend': {'back-end', 'back end', 'server-side', 'api'},
    # Data
    'data science': {'data scientist', 'analytics', 'machine learning'},
    'data engineering': {'data engineer', 'etl', 'data pipeline'},
    'data analytics': {'data analysis', 'analytics', 'business intelligence', 'bi'},
    'power bi': {'powerbi', 'business intelligence', 'bi'},
    'powerbi': {'power bi'},
    'tableau': {'data visualization', 'business intelligence'},
    'etl': {'data pipeline', 'data engineering'},
    # QA / Testing
    'qa': {'testing', 'quality assurance', 'test automation', 'selenium', 'qc'},
    'testing': {'qa', 'quality assurance', 'test'},
    'selenium': {'test automation', 'qa', 'web testing'},
    'automation testing': {'test automation', 'qa', 'selenium', 'cypress'},
    'test automation': {'automation testing', 'qa', 'selenium'},
    'manual testing': {'qa', 'quality assurance'},
    # Security
    'cyber': {'cybersecurity', 'security', 'infosec'},
    'cybersecurity': {'cyber security', 'security', 'infosec', 'soc', 'siem'},
    'security': {'cybersecurity', 'infosec'},
    'infosec': {'information security', 'cybersecurity'},
    # ERP / Business
    'sap': {'sap hana', 'sap s/4hana', 'erp'},
    'erp': {'sap', 'oracle erp', 'dynamics 365'},
    'salesforce': {'sfdc', 'crm'},
    'crm': {'salesforce', 'hubspot', 'dynamics crm'},
    # Project Management
    'scrum': {'agile', 'sprint', 'kanban', 'scrum master'},
    'agile': {'scrum', 'sprint', 'kanban'},
    'pmp': {'project management', 'project manager'},
    'pm': {'project management', 'project manager'},
    'project management': {'pm', 'pmp', 'agile', 'scrum'},
    # HR / Recruitment
    'hr': {'human resources', 'recruitment', 'talent acquisition', 'hrbp', 'people operations'},
    'recruitment': {'talent acquisition', 'hiring', 'hr', 'staffing', 'sourcing'},
    'talent acquisition': {'recruitment', 'hiring', 'sourcing'},
    'hrbp': {'hr business partner', 'human resources', 'hr'},
    # Design
    'ui/ux': {'ux', 'ui', 'user experience', 'user interface', 'ux design', 'ui design'},
    'ux': {'user experience', 'ui/ux', 'ux design', 'ux research'},
    'ui': {'user interface', 'ui/ux', 'ui design'},
    'figma': {'ui design', 'ux design', 'prototyping', 'sketch'},
    # Mobile
    'ios': {'swift', 'objective-c', 'xcode', 'apple'},
    'android': {'kotlin', 'java', 'mobile'},
    'react native': {'mobile', 'cross-platform', 'react'},
    'flutter': {'dart', 'mobile', 'cross-platform'},
    # API / Integration
    'rest': {'rest api', 'restful', 'api'},
    'rest api': {'restful', 'rest', 'api'},
    'restful': {'rest api', 'rest'},
    'graphql': {'graph ql', 'api'},
    'api': {'rest', 'restful', 'graphql', 'web services'},
    'microservices': {'micro services', 'distributed systems'},
    'micro services': {'microservices'},
    # Networking / Infra
    'networking': {'network', 'ccna', 'ccnp', 'cisco'},
    'cisco': {'ccna', 'ccnp', 'networking'},
    'linux': {'unix', 'ubuntu', 'centos', 'rhel'},
    'unix': {'linux'},
    # Automation general
    'automate': {'automation', 'scripting'},
    'automation': {'automate', 'scripting', 'rpa'},
    # ── Finance / Accounting ──
    'finance': {'financial analysis', 'financial modeling', 'accounting', 'treasury', 'budgeting'},
    'accounting': {'accounts', 'bookkeeping', 'financial reporting', 'audit', 'cpa', 'acca'},
    'cpa': {'certified public accountant', 'accounting'},
    'acca': {'chartered accountant', 'accounting'},
    'cfa': {'chartered financial analyst', 'finance', 'investment'},
    'audit': {'auditing', 'internal audit', 'external audit', 'accounting'},
    'treasury': {'cash management', 'finance'},
    'tax': {'taxation', 'tax planning', 'vat', 'corporate tax'},
    # ── Sales / Business Development ──
    'sales': {'business development', 'account management', 'revenue', 'selling'},
    'business development': {'bd', 'sales', 'partnerships'},
    'account management': {'key accounts', 'client management', 'sales'},
    'b2b': {'business to business', 'enterprise sales'},
    'b2c': {'business to consumer', 'retail sales', 'consumer'},
    # ── Marketing ──
    'marketing': {'digital marketing', 'brand management', 'advertising', 'seo', 'sem'},
    'digital marketing': {'seo', 'sem', 'social media marketing', 'ppc', 'google ads'},
    'seo': {'search engine optimization', 'digital marketing'},
    'sem': {'search engine marketing', 'ppc', 'google ads'},
    'content marketing': {'content writing', 'copywriting', 'blog'},
    'social media': {'social media marketing', 'smm', 'community management'},
    # ── Operations / Supply Chain ──
    'operations': {'operations management', 'process improvement', 'lean', 'six sigma'},
    'supply chain': {'scm', 'logistics', 'procurement', 'warehousing'},
    'logistics': {'supply chain', 'freight', 'shipping', 'transportation'},
    'procurement': {'purchasing', 'sourcing', 'vendor management'},
    'lean': {'lean manufacturing', 'six sigma', 'kaizen', 'continuous improvement'},
    'six sigma': {'lean', 'process improvement', 'quality'},
    # ── Healthcare ──
    'healthcare': {'medical', 'hospital', 'clinical', 'health'},
    'nursing': {'nurse', 'registered nurse', 'rn', 'healthcare'},
    'pharmacy': {'pharmacist', 'pharmaceutical', 'pharma'},
    # ── Legal ──
    'legal': {'law', 'lawyer', 'attorney', 'compliance', 'contracts'},
    'compliance': {'regulatory', 'governance', 'risk', 'legal'},
    # ── Consulting ──
    'consulting': {'consultant', 'advisory', 'management consulting', 'strategy'},
    # ── Education ──
    'education': {'teaching', 'training', 'e-learning', 'curriculum'},
    'teaching': {'teacher', 'instructor', 'professor', 'education'},
    # ── Insurance ──
    'insurance': {'underwriting', 'claims', 'risk management', 'actuarial'},
    # ── Real Estate ──
    'real estate': {'property', 'realty', 'property management', 'leasing'},
}

# Pre-compiled regex for location detection
LOCATION_PATTERN = re.compile(
    r'(?:based\s+in|work\s*(?:ing)?\s*(?:from|in|at)|located?\s+(?:in|at)|'
    r'(?:prefer(?:red|ably)?|must\s+be)\s+(?:in|from|to\s+work\s+(?:from|in))|'
    r'office\s+in|(?:from|in|at|near)\s+)'
    r'\s+([A-Za-z][A-Za-z\s,]{1,40}?)(?:\s*[.\-]|\s+(?:office|with|who|minimum|'
    r'min|experience|exp|having|and|must|should|can|preferr?|at\s+least|\d)|$)',
    re.IGNORECASE
)

# Pre-compiled regex for experience detection
EXPERIENCE_PATTERN = re.compile(
    r'(?:minimum|min|at\s+least|above)?\s*(\d+)\+?\s*(?:years?|yrs?|y)\s*(?:of\s+)?(?:experience|exp)?',
    re.IGNORECASE
)

# Experience RANGE pattern: "0 to 2 years", "1-3 years", "between 2 and 5 years"
EXPERIENCE_RANGE_PATTERN = re.compile(
    r'(?:(\d+)\s*(?:to|-|–)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|between\s*(\d+)\s*(?:and|&)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|(?:no\s+more\s+than|not\s+more\s+than|max(?:imum)?|under|below|less\s+than|at\s+most)\s*(\d+)\s*(?:years?|yrs?|y)'
    r'|(?:do\s+not|don\'t|doesn\'t|should\s+not)\s+(?:include|have|exceed).*?(?:more\s+than|above|over)\s*(\d+)\s*(?:years?|yrs?|y))',
    re.IGNORECASE
)

# Count extraction from message: "show me 15 candidates", "top 20", "list 25"
COUNT_PATTERN = re.compile(
    r'(?:show|give|list|find|get|display|return|fetch|bring|send|provide)\s+(?:me\s+)?(?:the\s+)?(?:top\s+)?(\d+)'
    r'|(?:top|best|first)\s+(\d+)'
    r'|(\d+)\s+(?:candidates|results|people|profiles|matches|applicants|resumes|cvs)',
    re.IGNORECASE
)


def _expand_location_terms(raw_terms: list, stop_words: frozenset = STOP_WORDS) -> set:
    """Expand location terms using aliases (bidirectional). Returns set of all matching terms."""
    expanded: set = set()
    for term in raw_terms:
        expanded.add(term)
        for word in term.split():
            if len(word) > 2:
                expanded.add(word)
        if term in LOCATION_ALIASES:
            for alias in LOCATION_ALIASES[term]:
                expanded.add(alias)
        for alias_key, alias_vals in LOCATION_ALIASES.items():
            if term in alias_vals or term == alias_key:
                expanded.add(alias_key)
                for v in alias_vals:
                    expanded.add(v)
    return expanded


def _extract_location_from_text(text: str) -> list:
    """Extract required location terms from a query or JD using regex."""
    match = LOCATION_PATTERN.search(text)
    terms = []
    if match:
        raw_loc = match.group(1).strip().strip(',').strip()
        for part in raw_loc.split(','):
            part = part.strip().lower()
            if part and part not in STOP_WORDS and len(part) > 1:
                terms.append(part)
    return terms


def _safe_int_experience(val) -> int:
    """Safely convert experience to int, handling strings and None."""
    try:
        return int(float(val or 0))
    except (ValueError, TypeError):
        return 0

logger = logging.getLogger(__name__)

# Try to import google-generativeai
try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None  # type: ignore
    genai_types = None  # type: ignore
    GEMINI_AVAILABLE = False
    logger.info("google-genai not installed — Gemini service disabled")


def _repair_json(text: str) -> Optional[Dict]:
    """Lightweight JSON repair for Gemini output. Always returns a dict or None."""
    if not text:
        return None
    # Strip markdown fences
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    text = re.sub(r'```$', '', text).strip()

    def _ensure_dict(val):
        """Ensure the result is a dict, not a list or other type."""
        if isinstance(val, dict):
            return val
        if isinstance(val, list):
            # Return first dict in list, or None
            for item in val:
                if isinstance(item, dict):
                    return item
            return None
        return None

    # Direct parse
    try:
        parsed = json.loads(text)
        result = _ensure_dict(parsed)
        if result is not None:
            return result
    except json.JSONDecodeError:
        pass

    # Extract largest {...} blob
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        candidate = m.group()
        try:
            parsed = json.loads(candidate)
            result = _ensure_dict(parsed)
            if result is not None:
                return result
        except json.JSONDecodeError:
            pass
        # Fix trailing commas
        fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
        try:
            parsed = json.loads(fixed)
            result = _ensure_dict(parsed)
            if result is not None:
                return result
        except json.JSONDecodeError:
            pass

    # Patch truncated JSON
    open_b = text.count('{') - text.count('}')
    open_k = text.count('[') - text.count(']')
    if open_b > 0 or open_k > 0:
        patched = text + (']' * max(open_k, 0)) + ('}' * max(open_b, 0))
        patched = re.sub(r',\s*([}\]])', r'\1', patched)
        try:
            parsed = json.loads(patched)
            result = _ensure_dict(parsed)
            if result is not None:
                return result
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
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model_name = model_name
        self.available = False
        self._client = None

        # Performance tracking
        self._request_count = 0
        self._total_time = 0.0
        self._error_count = 0

        # Response cache
        self._cache: Dict[str, Any] = {}
        self._cache_max_size = 500
        self._cache_ttl = 3600  # 1 hour

        # Daily budget tracking — prevents runaway costs
        self._daily_call_count = 0
        self._daily_call_date = ''  # YYYY-MM-DD
        self._daily_call_limit = int(os.environ.get('GEMINI_DAILY_LIMIT', '2000'))  # Max API calls/day

        if not GEMINI_AVAILABLE:
            logger.warning("⚠️ google-genai package not installed. Run: pip install google-genai")
            return

        if not self.api_key:
            logger.warning("⚠️ GEMINI_API_KEY not set — Gemini service disabled")
            return

        try:
            self._client = genai.Client(api_key=self.api_key)
            self.available = True
            logger.info(f"✅ Gemini AI initialized: {self.model_name}")
        except Exception as e:
            logger.error(f"❌ Gemini initialization failed: {e}")
            self.available = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_key(self, prefix: str, text: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()
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

    def _generate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 1024, thinking_budget: int = 0) -> str:
        """Synchronous text generation via Gemini.
        
        thinking_budget: Controls Gemini 2.5 Flash's internal reasoning tokens.
          0 = disabled (cheap — use for JSON extraction / structured tasks)
          >0 = enabled with token cap (use for open-ended chat / analysis)
        Thinking tokens cost $3.50/1M vs $0.60/1M for output — disable when not needed.
        """
        if not self.available or not self._client:
            return ""
        # ── Daily budget check ──
        import datetime as _dt
        today = _dt.date.today().isoformat()
        if self._daily_call_date != today:
            self._daily_call_date = today
            self._daily_call_count = 0
        if self._daily_call_count >= self._daily_call_limit:
            logger.warning(f"⚠️ Gemini daily limit reached ({self._daily_call_limit} calls). Skipping API call.")
            return ""
        self._daily_call_count += 1
        start = time.time()
        try:
            # Build config — disable thinking for extraction tasks to save ~70% cost
            gen_config = genai_types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
                thinking_config=genai_types.ThinkingConfig(
                    thinking_budget=thinking_budget,
                ),
            )
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=gen_config,
            )
            result = response.text.strip()
            elapsed = time.time() - start
            self._request_count += 1
            self._total_time += elapsed
            # Log token usage and estimated cost for monitoring
            usage = getattr(response, 'usage_metadata', None)
            input_tokens = getattr(usage, 'prompt_token_count', 0) or 0
            output_tokens = getattr(usage, 'candidates_token_count', 0) or 0
            thinking_used = getattr(usage, 'thoughts_token_count', 0) or 0
            # Cost estimate: Gemini 2.5 Flash — $0.15/1M input, $0.60/1M output, $3.50/1M thinking
            est_cost = (input_tokens * 0.15 + output_tokens * 0.60 + thinking_used * 3.50) / 1_000_000
            logger.info(
                f"💰 Gemini [{self.model_name}]: {len(result)} chars in {elapsed:.1f}s | "
                f"in={input_tokens} out={output_tokens} think={thinking_used} | "
                f"~${est_cost:.4f}"
            )
            return result
        except Exception as e:
            self._error_count += 1
            logger.error(f"Gemini generation error: {e}")
            return ""

    async def _agenerate(self, prompt: str, temperature: float = 0.1, max_tokens: int = 1024, thinking_budget: int = 0) -> str:
        """Async wrapper — Gemini SDK is sync, so we run in executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._generate, prompt, temperature, max_tokens, thinking_budget)

    def _generate_json(self, prompt: str, temperature: float = 0.05, max_tokens: int = 1024) -> Optional[Dict]:
        """Generate structured JSON from Gemini (thinking disabled for cost savings)."""
        result = self._generate(prompt, temperature=temperature, max_tokens=max_tokens, thinking_budget=0)
        if not result:
            return None
        return _repair_json(result)

    async def _agenerate_json(self, prompt: str, temperature: float = 0.05, max_tokens: int = 1024) -> Optional[Dict]:
        """Async JSON generation (thinking disabled for cost savings)."""
        result = await self._agenerate(prompt, temperature=temperature, max_tokens=max_tokens, thinking_budget=0)
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

        try:
            result = await self._agenerate_json(prompt, temperature=0.05)
        except Exception as gen_err:
            logger.warning(f"Gemini JSON generation error: {gen_err}")
            return None

        if result and isinstance(result, dict):
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
                logger.info(f"[Gemini] Email parsed: {result.get('name', 'Unknown')} | Source: {source}")
                return result

        return None

    # ==================================================================
    # CANDIDATE TEXT ANALYSIS (for background processing)
    # ==================================================================

    async def analyze_candidate(self, text: str, job_context: str = None) -> Dict:
        """Analyze raw candidate/resume text and extract structured data.
        Used by the background processor to enrich unprocessed candidates.
        If job_context is provided, scores reflect fit for that role."""
        if not text or len(text.strip()) < 20:
            return {}

        cache_key = self._cache_key("analyze", text[:500] + (job_context or '')[:200])
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        job_instruction = ""
        if job_context:
            job_instruction = f"""
TARGET ROLE CONTEXT:
{job_context[:1000]}

Score the candidate's FIT for this specific role. A 90%+ means excellent match for this exact position."""
        else:
            job_instruction = """
No specific target role provided. Score based on OVERALL professional quality:
- 85-100: Exceptional — 10+ yrs experience, strong skills, leadership, certifications
- 70-84: Strong — 5+ yrs, good skills match, relevant education
- 55-69: Moderate — 2-5 yrs, some relevant skills but gaps
- 40-54: Developing — Entry-level, limited skills, minimal experience
- Below 40: Weak — Unclear background or very junior

Be precise and differentiated. Do NOT default to 50 or 65. Assess the actual resume quality carefully.
If the resume shows 10+ skills, clear experience, and education, score 75+.
If sparse/generic, score 35-50."""

        prompt = f"""You are an expert recruitment AI analyzing a candidate's resume/profile.
Extract ALL structured information and provide an honest quality assessment.
{job_instruction}

RESUME TEXT:
{text[:4000]}

Return ONLY valid JSON with these exact fields:
{{
    "name": "Full Name from resume (if visible)",
    "phone": "Phone number with country code if found",
    "email": "Email address if found",
    "location": "City, State/Country — extract from address, contact info, or any location mention",
    "skills": ["Extract ALL technical and professional skills mentioned — be thorough, list 10+ if present"],
    "experience": 5,
    "education": ["Highest degree — e.g. B.Tech in Computer Science, MBA, etc."],
    "job_category": "MUST be exactly one of: Software Engineering, Data & Analytics, IT & Systems, Engineering, HR & Admin, Finance & Accounting, Sales, Operations, Consulting, Healthcare, Design & Creative, QA & Testing, Marketing, Customer Service, Insurance & Safety, Retail & Hospitality, Business Analyst, Education, Legal, General",
    "job_subcategory": "Specific role title — e.g. Full Stack Developer, MEP Engineer, Data Scientist",
    "quality_score": 72,
    "summary": "2-3 sentence professional summary highlighting key strengths and experience level",
    "certifications": ["Any certifications mentioned"],
    "languages": ["Languages spoken if mentioned"],
    "linkedin": "LinkedIn URL if found",
    "work_history": ["Recent company/role if mentioned"]
}}

IMPORTANT for quality_score:
- Base it on: depth of experience, breadth of skills, education quality, certifications, career progression
- A resume with 10+ skills, 5+ years exp, and a degree should score 70-80
- A resume with 3-4 skills and 1-2 years should score 45-55
- Score MUST reflect actual resume content — never default to 50 or 65"""

        result = await self._agenerate_json(prompt, temperature=0.1)

        if result:
            # Normalize score — prefer quality_score, then match_score
            score = result.get('quality_score') or result.get('match_score')
            if score is None:
                # Calculate a reasonable fallback from extracted data
                skills_count = len(result.get('skills', []))
                exp = result.get('experience', 0) or 0
                has_edu = bool(result.get('education'))
                has_certs = bool(result.get('certifications'))
                score = 25  # base
                score += min(30, skills_count * 3)  # up to 30 from skills
                score += min(25, exp * 3)  # up to 25 from experience  
                score += 10 if has_edu else 0
                score += 5 if has_certs else 0
                score = min(95, max(15, score))
                logger.info(f"📊 Calculated fallback score: {score} (skills={skills_count}, exp={exp})")
            if isinstance(score, str):
                nums = re.findall(r'\d+', score)
                score = int(nums[0]) if nums else 40
            try:
                result['match_score'] = max(10, min(100, int(float(score))))
            except (TypeError, ValueError):
                result['match_score'] = 40
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

        # Stage 1: Fast keyword pre-filter using shared constants
        jd_lower = job_description.lower()
        jd_tokens = set(re.sub(r'[^\w\s#+.]', ' ', jd_lower).split())
        jd_keywords = {t for t in jd_tokens if len(t) >= 2 and t not in STOP_WORDS}

        expanded_keywords = set(jd_keywords)
        for alias, expansions in LOCATION_ALIASES.items():
            if alias in jd_keywords:
                expanded_keywords.update(expansions)

        # Detect explicit location requirement from JD
        raw_loc_terms = _extract_location_from_text(job_description)
        jd_location_terms = _expand_location_terms(raw_loc_terms)
        jd_has_location = len(jd_location_terms) > 0

        # Detect experience requirement from JD
        _exp_match_jd = EXPERIENCE_PATTERN.search(job_description)
        jd_min_experience = int(_exp_match_jd.group(1)) if _exp_match_jd else 0

        pre_scored = []
        for idx, c in enumerate(candidates):
            skills = [s.lower().strip() for s in c.get('skills', [])]
            category = str(c.get('jobCategory', c.get('job_category', ''))).lower()
            subcategory = str(c.get('jobSubcategory', c.get('job_subcategory', ''))).lower()
            location = str(c.get('location', '')).lower()
            summary = str(c.get('summary', '')).lower()

            # Word-boundary matching for skills with synonym support
            skill_hits = 0
            for s in skills:
                s_words = set(re.sub(r'[^\w\s]', ' ', s).split())
                if s_words & expanded_keywords:
                    skill_hits += 1
                    continue
                matched = False
                for kw in expanded_keywords:
                    if jd_has_location and kw in jd_location_terms:
                        continue
                    if kw == s or (len(kw) >= 3 and (kw in s.split() or s in kw.split())):
                        skill_hits += 1
                        matched = True
                        break
                if not matched:
                    for kw in expanded_keywords:
                        if jd_has_location and kw in jd_location_terms:
                            continue
                        syns = SKILL_SYNONYMS.get(kw, set())
                        if syns and (syns & s_words or s in syns):
                            skill_hits += 1
                            break

            cat_hits = sum(1 for kw in expanded_keywords if kw in category.split() or kw in subcategory.split())

            # Location scoring — strong when location requirement detected
            loc_score_add = 0
            if jd_has_location:
                loc_words = set(re.sub(r'[^\w\s]', ' ', location).split())
                loc_matched = any(lt in location or lt in loc_words for lt in jd_location_terms)
                if loc_matched:
                    loc_score_add = 50  # Strong boost
                else:
                    loc_score_add = -30  # Penalty
            else:
                loc_hits = sum(1 for kw in expanded_keywords if kw in location)
                loc_score_add = loc_hits * 8

            summary_words = set(summary.split())
            summary_hits = len(summary_words & expanded_keywords)

            exp = _safe_int_experience(c.get('experience', 0))
            
            # Experience scoring — meaningful weight
            exp_score = min(exp, 20) * 1.5  # Up to 30 points from experience
            if jd_min_experience > 0:
                if exp >= jd_min_experience:
                    exp_score += 15  # Bonus for meeting minimum
                else:
                    exp_score -= 15  # Penalty for below minimum

            pre_score = skill_hits * 15 + cat_hits * 10 + loc_score_add + min(summary_hits, 5) * 3 + exp_score
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

    # ── Query type classification ──
    @staticmethod
    def _classify_query(message: str) -> str:
        """Classify user query to route to the best prompt strategy.
        Returns: 'search', 'analytics', 'advice', 'comparison', 'greeting', 'followup'
        """
        msg = message.lower().strip()

        # Greetings / pleasantries
        if msg in ('hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening', 'thanks', 'thank you', 'ok', 'okay'):
            return 'greeting'

        # Follow-up / conversational (short messages referencing prior context)
        if len(msg.split()) <= 4 and any(w in msg for w in ['yes', 'no', 'more', 'next', 'sure', 'go ahead', 'continue', 'elaborate', 'explain']):
            return 'followup'

        # Analytics / statistics queries
        analytics_signals = [
            'how many', 'count', 'total', 'statistics', 'stats', 'average', 'breakdown',
            'distribution', 'percentage', 'ratio', 'trend', 'report', 'summary of',
            'overview', 'dashboard', 'analyze the database', 'analyze our'
        ]
        if any(sig in msg for sig in analytics_signals):
            return 'analytics'

        # Comparison queries
        if any(w in msg for w in ['compare', 'comparison', 'versus', 'vs', 'better between', 'side by side', 'which one']):
            return 'comparison'

        # Recruitment advice / general knowledge
        advice_signals = [
            'how to', 'how do i', 'how should', 'what is the best way', 'what are the best',
            'tips for', 'advice on', 'best practices', 'strategy for', 'suggest a',
            'recommend a', 'help me write', 'draft a', 'template for', 'guide for',
            'explain', 'what does', 'what is', 'define', 'difference between',
            'improve my', 'optimize my', 'when should i', 'why should',
            'interview questions', 'how to evaluate', 'red flags',
            'salary range', 'market rate', 'compensation for',
            'what questions', 'should i ask', 'how to interview',
            'how to assess', 'screening tips', 'evaluation criteria',
            'what to look for', 'hiring tips', 'recruitment tips',
        ]
        if any(sig in msg for sig in advice_signals) and not any(w in msg for w in ['find', 'show', 'list', 'get', 'candidates']):
            return 'advice'

        # Default: candidate search
        return 'search'

    async def chat(
        self,
        message: str,
        context: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None,
        candidates_data: Optional[List[Dict]] = None,
        return_candidates: bool = False,
        num_candidates: int = 15,
    ) -> Union[str, Dict]:
        """AI chat assistant with intelligent 2-stage database search.
        
        Stage 0: Classify query type → route to best prompt strategy
        Stage 1: Pre-filter candidates using keyword extraction from user query
        Stage 2: Send relevant subset to Gemini for intelligent analysis
        
        Query types:
        - 'search': Candidate search (full pre-filter + candidate context)
        - 'analytics': Database statistics & insights
        - 'advice': Recruitment best practices & general knowledge
        - 'comparison': Candidate comparison
        - 'greeting': Friendly greeting
        - 'followup': Conversational follow-up
        
        This allows searching the ENTIRE database cost-effectively.
        
        If return_candidates=True, returns a dict with 'response' and 'candidates_lookup'
        mapping [N] indices to candidate data from the database.
        """
        ctx = context or {}
        total = ctx.get('totalCandidates', 0)
        avg_score = ctx.get('avgMatchScore', 0)
        strong = ctx.get('strongMatches', 0)
        categories = ctx.get('categories', {})
        
        # ── STAGE 0: Query Classification ──
        query_type = self._classify_query(message)
        logger.info(f"🧠 Query classified as: {query_type} | Message: {message[:80]}")
        
        # Track selected candidates for returning alongside response
        _selected_candidates = []

        # --- STAGE 1: Intelligent Pre-filtering ---
        # Extract keywords from user query for candidate pre-filtering
        query_lower = message.lower()
        
        # Build category summary for general/analytics questions
        cat_summary = ""
        if categories:
            cat_lines = [f"  \u2022 {cat}: {info.get('count', info) if isinstance(info, dict) else info} candidates" for cat, info in sorted(categories.items(), key=lambda x: x[1].get('count', 0) if isinstance(x[1], dict) else x[1], reverse=True)[:15]]
            cat_summary = "\nCategories breakdown:\n" + "\n".join(cat_lines)

        candidates_context = ""
        relevant_count = 0
        total_scanned = 0
        
        # Initialize variables used later in prompt building (set before the candidates_data block)
        has_location_requirement = False
        required_location_terms: list = []
        expanded_location_terms: list = []
        required_min_experience = 0
        required_max_experience = 999  # No upper limit by default
        
        # ── Server-side count extraction from message (override frontend default) ──
        _count_match = COUNT_PATTERN.search(message)
        if _count_match:
            extracted_count = int(next(g for g in _count_match.groups() if g))
            if 1 <= extracted_count <= 50:
                num_candidates = extracted_count
                logger.info(f"Extracted requested count from message: {num_candidates}")
        
        if candidates_data:
            total_scanned = len(candidates_data)
            
            # Smart pre-filter: score each candidate against query keywords
            scored_candidates = []
            query_tokens = set(re.sub(r'[^\w\s]', ' ', query_lower).split())
            keywords = query_tokens - STOP_WORDS
            
            # Detect explicit location requirement using shared helpers
            required_location_terms = _extract_location_from_text(message)
            expanded_location_terms = _expand_location_terms(required_location_terms)
            has_location_requirement = len(expanded_location_terms) > 0
            
            # Detect experience requirement (both min and range/max)
            _range_match = EXPERIENCE_RANGE_PATTERN.search(message)
            if _range_match:
                groups = _range_match.groups()
                if groups[0] and groups[1]:       # "0 to 2 years"
                    required_min_experience = int(groups[0])
                    required_max_experience = int(groups[1])
                elif groups[2] and groups[3]:     # "between 2 and 5 years"
                    required_min_experience = int(groups[2])
                    required_max_experience = int(groups[3])
                elif groups[4]:                   # "max 2 years", "under 3 years"
                    required_max_experience = int(groups[4])
                elif groups[5]:                   # "do not include more than 2 years"
                    required_max_experience = int(groups[5])
                logger.info(f"Experience range detected: {required_min_experience}-{required_max_experience} years")
            else:
                _exp_match = EXPERIENCE_PATTERN.search(message)
                required_min_experience = int(_exp_match.group(1)) if _exp_match else 0
            
            # Expand keywords with location aliases
            expanded_keywords = set(keywords)
            for alias, expansions in LOCATION_ALIASES.items():
                if alias in keywords:
                    expanded_keywords.update(expansions)
                    
            # ── Build multi-word phrases from query for phrase matching ──
            # e.g. "data science" should match as a phrase, not just "data" + "science"
            query_clean = re.sub(r'[^\w\s]', ' ', query_lower)
            query_words_ordered = [w for w in query_clean.split() if w not in STOP_WORDS and len(w) >= 2]
            query_phrases = set()
            for pi in range(len(query_words_ordered) - 1):
                phrase = f"{query_words_ordered[pi]} {query_words_ordered[pi+1]}"
                # Only keep phrases that are meaningful (both words appear consecutively in original)
                if phrase in query_lower:
                    query_phrases.add(phrase)
            
            for idx, c in enumerate(candidates_data):
                relevance = 0
                name = str(c.get('name', '')).lower()
                skills = [s.lower() for s in c.get('skills', [])]
                skills_str = ' '.join(skills)
                category = str(c.get('jobCategory', c.get('job_category', ''))).lower()
                subcategory = str(c.get('jobSubcategory', c.get('job_subcategory', ''))).lower()
                # Also match against normalized category words for broader matching
                cat_words = set(re.sub(r'[^\w\s]', ' ', category).split()) | set(re.sub(r'[^\w\s]', ' ', subcategory).split())
                location = str(c.get('location', '')).lower()
                summary = str(c.get('summary', '')).lower()
                experience = _safe_int_experience(c.get('experience', 0))
                score = c.get('matchScore', c.get('match_score', 0)) or 0
                
                # ── Build searchable text from work history ──
                work_history = c.get('workHistory', c.get('work_history', []))
                wh_titles = []
                wh_companies = []
                wh_full_text = ''
                if isinstance(work_history, list):
                    for w in work_history:
                        if isinstance(w, dict):
                            t = str(w.get('title', '')).lower()
                            comp = str(w.get('company', '')).lower()
                            if t: wh_titles.append(t)
                            if comp: wh_companies.append(comp)
                    wh_full_text = ' '.join(wh_titles + wh_companies)
                
                # ── Build searchable text from education ──
                education = c.get('education', [])
                edu_text = ''
                if isinstance(education, list):
                    for e in education:
                        if isinstance(e, dict):
                            edu_text += f" {str(e.get('degree', '')).lower()} {str(e.get('institution', '')).lower()} {str(e.get('field', '')).lower()}"
                edu_text = edu_text.strip()
                
                # ── Build searchable text from certifications ──
                certs = c.get('certifications', [])
                certs_text = ' '.join([str(x).lower() for x in certs[:10]]) if isinstance(certs, list) else ''
                
                # ── Location-aware scoring ──
                location_matched = False
                if has_location_requirement:
                    loc_words = set(re.sub(r'[^\w\s]', ' ', location).split())
                    for lt in expanded_location_terms:
                        if lt in location or lt in loc_words:
                            location_matched = True
                            break
                    if location_matched:
                        relevance += 60  # Strong boost for matching the required location
                    else:
                        relevance -= 40  # Penalty for being in wrong location
                
                # ── Multi-word phrase matching (bonus on top of individual keyword scores) ──
                for phrase in query_phrases:
                    if phrase in skills_str:
                        relevance += 15  # Strong: phrase found in skills (e.g. "data science")
                    if phrase in wh_full_text:
                        relevance += 14  # Strong: phrase in work history
                    if phrase in summary:
                        relevance += 10
                    if phrase in category or phrase in subcategory:
                        relevance += 14  # Category phrase match is very strong
                    if phrase in edu_text:
                        relevance += 8
                    if phrase in certs_text:
                        relevance += 10
                
                # ── Job title matching — high value signal for role-specific queries ──
                # Check if any work history title closely matches the query role
                role_title_bonus = 0
                for title in wh_titles:
                    title_words = set(title.split())
                    title_kw_overlap = len(title_words & expanded_keywords)
                    if title_kw_overlap >= 2:
                        role_title_bonus = max(role_title_bonus, 25)
                    elif title_kw_overlap == 1 and any(kw in title for kw in expanded_keywords if len(kw) >= 4):
                        role_title_bonus = max(role_title_bonus, 15)
                relevance += role_title_bonus
                
                # Score based on keyword matches (word-boundary, not substring)
                for kw in expanded_keywords:
                    if len(kw) < 2:
                        continue
                    # Skip location keywords from normal scoring — handled above
                    if has_location_requirement and kw in expanded_location_terms:
                        continue
                    # Also check skill synonyms
                    kw_synonyms = SKILL_SYNONYMS.get(kw, set())
                    # Skills: check each skill individually with word matching + synonyms
                    matched_skill = False
                    for s in skills:
                        s_words = set(re.sub(r'[^\w\s]', ' ', s).split())
                        if kw in s_words or kw == s:
                            relevance += 20
                            matched_skill = True
                            break
                        # Check synonyms (e.g. 'ml' matches 'machine learning')
                        if kw_synonyms and (kw_synonyms & s_words or s in kw_synonyms):
                            relevance += 18
                            matched_skill = True
                            break
                    # Category/subcategory: word match (using expanded cat_words)
                    if kw in cat_words:
                        relevance += 15
                    # Also check category synonym match (e.g. query "finance" matches category "accounting")
                    if kw_synonyms and (kw_synonyms & cat_words):
                        relevance += 12
                    # General location keyword match (only when no explicit requirement)
                    if not has_location_requirement and kw in location:
                        relevance += 15
                    if kw in name.split():
                        relevance += 25  # Direct name search
                    
                    # ── Work history scoring (job titles + company names) ──
                    wh_words = set(wh_full_text.split())
                    if kw in wh_words:
                        relevance += 15  # Strong signal — keyword in actual job history
                    elif kw_synonyms:
                        if kw_synonyms & wh_words:
                            relevance += 12
                    # Also check if keyword appears as substring in titles (e.g. "sales" in "sales manager")
                    if not (kw in wh_words):
                        for title in wh_titles:
                            if kw in title:
                                relevance += 10
                                break
                    
                    # ── Education scoring ──
                    if edu_text and kw in set(edu_text.split()):
                        relevance += 8
                    
                    # ── Certification scoring ──
                    if certs_text and kw in set(certs_text.split()):
                        relevance += 10
                    
                    # Summary: word match — increased weight (summary is rich text)
                    if kw in set(summary.split()):
                        relevance += 12
                    
                    # ── If keyword matched nowhere at all, slight penalty to de-rank irrelevant candidates ──
                    if not matched_skill and kw not in cat_words and kw not in wh_words and kw not in set(summary.split()):
                        relevance -= 2  # Small penalty per unmatched keyword
                
                # Boost by match score
                relevance += score * 0.15
                
                # ── Experience requirement check — meaningful weight ──
                if required_max_experience < 999:
                    # Has an upper cap (e.g. "0-2 years", "max 3 years")
                    if experience > required_max_experience:
                        relevance -= 80  # HARD penalty for exceeding max
                    elif experience >= required_min_experience and experience <= required_max_experience:
                        relevance += 30  # Perfect fit within range
                    elif experience < required_min_experience:
                        relevance -= 20  # Below minimum
                elif required_min_experience > 0:
                    if experience >= required_min_experience:
                        relevance += 20  # Meets minimum experience
                        if experience >= required_min_experience * 1.5:
                            relevance += 10
                    else:
                        relevance -= 25  # Below minimum experience
                else:
                    # General experience boost even when no explicit requirement
                    relevance += min(experience, 15) * 1.0
                
                # Recency boost — only for explicitly recency-related queries
                created_at = c.get('created_at', '')
                if created_at and any(w in query_lower for w in ['new', 'recent', 'latest', 'today', 'week', 'fresh']):
                    try:
                        from datetime import datetime, timedelta
                        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00')) if 'T' in created_at else datetime.strptime(created_at[:19], '%Y-%m-%d %H:%M:%S')
                        now = datetime.utcnow()
                        age_hours = (now - created_dt.replace(tzinfo=None)).total_seconds() / 3600
                        if age_hours <= 24:
                            relevance += 20
                        elif age_hours <= 72:
                            relevance += 15
                        elif age_hours <= 168:
                            relevance += 10
                    except (ValueError, TypeError):
                        pass
                
                # Experience-based queries — richer seniority detection
                if any(w in query_lower for w in ['senior', 'experienced', 'lead', 'principal', 'director', 'head', 'vp', 'chief']):
                    if experience >= 10:
                        relevance += 20
                    elif experience >= 7:
                        relevance += 15
                    elif experience >= 5:
                        relevance += 5
                    else:
                        relevance -= 10  # Penalty for juniors on senior queries
                elif any(w in query_lower for w in ['mid', 'intermediate', 'mid-level', 'moderate']):
                    if 3 <= experience <= 7:
                        relevance += 15
                elif any(w in query_lower for w in ['junior', 'entry', 'fresher', 'graduate', 'intern', 'trainee', 'beginner']):
                    if experience <= 2:
                        relevance += 15
                    elif experience <= 3:
                        relevance += 5
                    else:
                        relevance -= 10  # Penalty for seniors on junior queries
                
                scored_candidates.append((relevance, idx, c))
            
            # Sort by relevance and take top candidates (idx as tiebreaker to avoid dict comparison)
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            
            # Dynamic pool size — optimized for Gemini 2.5 Flash throughput
            # Compact profiles: 15 candidates keeps prompt well within 120s timeout
            has_specific_keywords = len(keywords) >= 2
            has_many_keywords = len(keywords) >= 4
            
            if has_many_keywords:
                MAX_CANDIDATES_TO_GEMINI = 12  # Complex query — tight focus
            elif has_specific_keywords:
                MAX_CANDIDATES_TO_GEMINI = 15  # Moderate query
            else:
                MAX_CANDIDATES_TO_GEMINI = 18  # Broad/simple query
            
            # Ensure we request at least enough for the user's num_candidates
            MAX_CANDIDATES_TO_GEMINI = max(MAX_CANDIDATES_TO_GEMINI, min(num_candidates + 3, 20))
            
            if has_specific_keywords:
                relevant = [(score, idx, c) for score, idx, c in scored_candidates if score > 0]
                selected = relevant[:MAX_CANDIDATES_TO_GEMINI] if relevant else scored_candidates[:50]
            else:
                selected = scored_candidates[:MAX_CANDIDATES_TO_GEMINI]
            
            relevant_count = len(selected)
            
            # ── Diagnostic logging ──
            top5_scores = [(c.get('name', '?'), s) for s, _, c in selected[:5]]
            logger.info(
                f"Pre-filter: {total_scanned} candidates → {relevant_count} selected "
                f"(MAX={MAX_CANDIDATES_TO_GEMINI}, keywords={list(keywords)[:8]}, "
                f"location={'yes' if has_location_requirement else 'no'}, "
                f"exp={required_min_experience}-{required_max_experience}y, "
                f"phrases={list(query_phrases)[:4]}) "
                f"Top5: {top5_scores}"
            )
            
            # Store selected candidates for frontend matching
            _selected_candidates = [c for (_score, _idx, c) in selected[:MAX_CANDIDATES_TO_GEMINI]]
            
            # Build context — lean format for fast Gemini processing
            # Focus on essential info: name, skills, work titles, location, experience
            candidates_context = f"\n\nCANDIDATES ({relevant_count} pre-filtered from {total_scanned}):\n"
            for i, (rel_score, _idx, c) in enumerate(selected[:MAX_CANDIDATES_TO_GEMINI]):
                skills_raw = c.get('skills', [])
                skills_str = ', '.join(skills_raw[:15]) if isinstance(skills_raw, list) else str(skills_raw or '')
                work = c.get('workHistory', c.get('work_history', []))
                if isinstance(work, list):
                    work_entries = []
                    for w in work[:3]:
                        if isinstance(w, dict):
                            entry = f"{w.get('title', 'N/A')} @ {w.get('company', 'N/A')}"
                            dur = w.get('duration', '')
                            if dur:
                                entry += f" ({dur})"
                            work_entries.append(entry)
                    work_str = '; '.join(work_entries) or 'N/A'
                else:
                    work_str = str(work)[:150] if work else 'N/A'
                edu = c.get('education', [])
                if isinstance(edu, list) and edu:
                    e = edu[0] if isinstance(edu[0], dict) else {}
                    edu_str = ' - '.join(p for p in [e.get('degree', ''), e.get('field', ''), e.get('institution', '')] if p) or 'N/A'
                else:
                    edu_str = str(edu)[:100] if edu else 'N/A'
                
                candidates_context += (
                    f"[{i+1}] {c.get('name', 'Unknown')} | {c.get('matchScore', 0)}% | "
                    f"{c.get('jobCategory', c.get('job_category', 'General'))} | "
                    f"Exp: {c.get('experience', 0)}yrs | {c.get('location', 'N/A')} | "
                    f"Status: {c.get('status', 'New')}\n"
                    f"   Skills: {skills_str}\n"
                    f"   Work: {work_str}\n"
                    f"   Edu: {edu_str}\n"
                    f"   Contact: {c.get('email', 'N/A')} | {c.get('phone', 'N/A')}\n"
                )

        # Build conversation context
        history_text = ""
        if conversation_history:
            # Keep last 8 messages, prioritize recent full context
            for msg in conversation_history[-8:]:
                role = msg.get('role', 'user')
                content = msg.get('content', '')[:400]
                history_text += f"\n{role}: {content}"

        cat_list = ', '.join([f"{k}: {v}" for k, v in list(categories.items())[:12]]) if categories else 'N/A'

        # Build dynamic constraint sections
        constraints = []
        if has_location_requirement:
            loc_str = ', '.join(required_location_terms)
            constraints.append(f"LOCATION FILTER (MANDATORY): Candidates in/near {loc_str} MUST be ranked first. Only include non-local candidates if fewer than {num_candidates} match locally. Flag non-local candidates clearly.")
        if required_max_experience < 999:
            constraints.append(f"EXPERIENCE RANGE FILTER (STRICT): Only {required_min_experience}-{required_max_experience} years. EXCLUDE any candidate with more than {required_max_experience} years of experience — this is a hard requirement, not a preference.")
        elif required_min_experience > 0:
            constraints.append(f"EXPERIENCE FILTER: Minimum {required_min_experience}+ years. Flag candidates below this threshold.")
        
        constraints_text = "\n".join(f"• {c}" for c in constraints) if constraints else "No special filters."

        # ══════════════════════════════════════════════════════════════
        # STAGE 2: Build prompt based on query type
        # ══════════════════════════════════════════════════════════════

        if query_type == 'greeting':
            # ── Fast greeting — no Gemini call needed ──
            greetings_map = {
                'hi': 'Hello! 👋', 'hello': 'Hello! 👋', 'hey': 'Hey there! 👋',
                'good morning': 'Good morning! ☀️', 'good afternoon': 'Good afternoon! 🌤️',
                'good evening': 'Good evening! 🌙',
                'thanks': 'You\'re welcome! 😊', 'thank you': 'You\'re welcome! 😊',
                'ok': 'Great!', 'okay': 'Great!',
            }
            greeting = greetings_map.get(message.lower().strip(), 'Hello! 👋')
            text_response = f"""{greeting} I'm your AI Recruitment Assistant for Efforts Solutions.

**Here's what I can do:**

🔍 **Find Candidates** — "Find React developers in Dubai with 3+ years experience"
📊 **Analytics** — "How many candidates do we have by category?"
💡 **Recruitment Advice** — "How to evaluate a senior backend engineer?"
📋 **Compare Candidates** — "Compare John and Sarah for the PM role"
📝 **Interview Help** — "What questions should I ask a data scientist?"

**Quick Stats:** {total} candidates in database | {strong} strong matches (70%+)
{cat_summary}

What would you like to explore?"""

        elif query_type == 'advice':
            # ── Recruitment expertise prompt — no candidate data needed ──
            prompt = f"""You are a world-class Senior Recruitment Strategist and HR Expert for Efforts Solutions, a recruitment agency. You have 20+ years of experience across tech, finance, healthcare, engineering, and executive hiring.

You have deep expertise in:
- Talent acquisition strategy & sourcing methodologies
- Interview design, behavioral & competency-based questioning
- Compensation benchmarking & offer negotiation
- Employer branding & candidate experience
- Diversity, equity & inclusion in hiring
- Applicant tracking systems & recruitment technology
- Labor market trends across GCC, India, US, UK, Europe
- Industry-specific hiring (IT, finance, engineering, healthcare, sales, operations)
- Remote/hybrid workforce management
- Onboarding best practices

DATABASE CONTEXT: You have access to a database of {total} candidates across these categories: {cat_list}

CONVERSATION HISTORY:{history_text}

USER QUESTION:
{message}

─── RESPONSE GUIDELINES ───
1. Provide actionable, expert-level advice grounded in real recruitment best practices
2. Use specific examples, frameworks, or methodologies where relevant
3. Reference industry standards (e.g., SHRM, LinkedIn Talent Insights, Glassdoor data)
4. Structure your response with clear headers, bullet points, and numbered steps
5. If the question relates to roles in the database, reference the candidate pool size
6. Include pro tips, common pitfalls to avoid, and red/green flags
7. Be concise but thorough — aim for comprehensive yet scannable responses
8. Use bold text for key terms and headers for structure
9. When discussing salaries/compensation, acknowledge regional variations (GCC vs India vs US/UK)
10. End with a specific actionable recommendation or next step

Write in a professional, confident, and helpful tone. Format with markdown."""

            result = await self._agenerate(prompt, temperature=0.3, max_tokens=4000, thinking_budget=4096)
            text_response = result or "I'd be happy to help with recruitment advice. Could you provide more details about your question?"

        elif query_type == 'analytics':
            # ── Analytics prompt — uses database stats, minimal candidate data ──
            # Build richer stats context
            stats_detail = f"""DATABASE ANALYTICS:
- Total Candidates: {total}
- Strong Matches (70%+): {strong}
- Average Match Score: {avg_score:.1f}%
{cat_summary}
"""
            # Add top candidates by score if available
            if _selected_candidates:
                top_by_score = sorted(_selected_candidates[:200], key=lambda c: c.get('matchScore', 0), reverse=True)[:10]
                stats_detail += "\nTop 10 Candidates by Score:\n"
                for i, c in enumerate(top_by_score, 1):
                    stats_detail += f"  {i}. {c.get('name', 'N/A')} — {c.get('matchScore', 0)}% | {c.get('jobCategory', 'General')} | {c.get('experience', 0)} yrs | {c.get('location', 'N/A')}\n"
                
                # Location distribution
                loc_counts: Dict[str, int] = {}
                for c in _selected_candidates[:500]:
                    loc = str(c.get('location', 'Unknown')).strip()
                    if loc:
                        loc_counts[loc] = loc_counts.get(loc, 0) + 1
                top_locations = sorted(loc_counts.items(), key=lambda x: x[1], reverse=True)[:10]
                stats_detail += "\nTop Locations:\n" + "\n".join(f"  • {loc}: {cnt}" for loc, cnt in top_locations)
                
                # Experience distribution
                exp_buckets = {'0-2 yrs': 0, '3-5 yrs': 0, '6-10 yrs': 0, '10+ yrs': 0}
                for c in _selected_candidates[:500]:
                    exp = _safe_int_experience(c.get('experience', 0))
                    if exp <= 2: exp_buckets['0-2 yrs'] += 1
                    elif exp <= 5: exp_buckets['3-5 yrs'] += 1
                    elif exp <= 10: exp_buckets['6-10 yrs'] += 1
                    else: exp_buckets['10+ yrs'] += 1
                stats_detail += "\n\nExperience Distribution:\n" + "\n".join(f"  • {k}: {v}" for k, v in exp_buckets.items())

            prompt = f"""You are an expert Recruitment Analytics Advisor for Efforts Solutions. Analyze the data and provide clear, insightful answers with specific numbers.

{stats_detail}

CONVERSATION HISTORY:{history_text}

USER QUESTION:
{message}

─── RESPONSE GUIDELINES ───
1. Lead with the specific numbers and data the user asked about
2. Present data in clear tables or bullet lists with bold labels
3. Provide context and insights — don't just state numbers, explain what they mean
4. Highlight trends, strengths, and gaps in the talent pool
5. Compare against industry benchmarks where possible
6. Suggest actionable steps based on the data (e.g., "You have a gap in senior DevOps — consider posting on specialized job boards")
7. Use percentages and ratios for clearer understanding
8. If the user asks about something not in the data, say so clearly and suggest alternatives
9. Format with markdown headers, bold labels, and organized structure
10. Keep it data-driven and precise — recruiters need facts, not fluff"""

            result = await self._agenerate(prompt, temperature=0.15, max_tokens=4000, thinking_budget=4096)
            text_response = result or f"We have **{total} candidates** in the database. Could you clarify what analytics you'd like to see?"

        elif query_type == 'followup':
            # ── Conversational follow-up — leverage conversation history ──
            prompt = f"""You are an AI Recruitment Assistant for Efforts Solutions. The user is continuing a conversation.

DATABASE: {total} candidates | Categories: {cat_list}

CONVERSATION HISTORY:{history_text}

USER FOLLOW-UP:
{message}

{f"CANDIDATE POOL (if needed for follow-up):{candidates_context}" if candidates_context else ""}

Respond naturally to the follow-up. If they're asking for more candidates, different criteria, or clarification, provide it. If they're acknowledging or confirming, respond appropriately.
Keep the same format and quality as the previous response. Use markdown formatting."""

            result = await self._agenerate(prompt, temperature=0.2, max_tokens=6000, thinking_budget=4096)
            text_response = result or "Could you provide more details about what you'd like me to do next?"

        else:
            # ══════════════════════════════════════════════════════════
            # CANDIDATE SEARCH — Optimized prompt for speed + quality
            # ══════════════════════════════════════════════════════════

            prompt = f"""You are an expert AI Recruitment Specialist for Efforts Solutions with deep knowledge of the global talent market. Analyze the candidate pool thoroughly and rank the best matches.

DATABASE: {total} candidates total | {strong} strong matches (70%+) | Categories: {cat_list}

FILTERS: {constraints_text}

{candidates_context}

HISTORY:{history_text}

QUERY: {message}

ANALYSIS FRAMEWORK:
1. Parse query deeply: extract role, seniority level, must-have skills, nice-to-have skills, location preference, experience range, industry/domain, exclusions, and implicit requirements
2. Evaluate candidates on a weighted multi-dimensional scoring matrix:
   - Skills Match (30%): exact matches, close equivalents, transferable skills
   - Experience Fit (25%): years, seniority level, industry relevance, career trajectory
   - Location (20%): exact match, same country/region, willingness to relocate (infer from work history)
   - Industry/Domain Alignment (15%): same sector, adjacent sector, transferable domain knowledge
   - Education & Certifications (10%): degree relevance, prestigious institutions, professional certifications
3. Recognize skill synonyms and ecosystems:
   - Frontend: React=ReactJS, Vue=VueJS, Angular, Next.js≈React+SSR, TypeScript≈JavaScript advanced
   - Backend: Node=NodeJS, Python≈Django/Flask/FastAPI, Java≈Spring Boot, C#=.NET, Go=Golang
   - Cloud: AWS=Amazon Web Services, GCP=Google Cloud, Azure=Microsoft Cloud, K8s=Kubernetes
   - Data: ML=Machine Learning=AI, Data Science≈Analytics, SQL≈PostgreSQL/MySQL/SQLite
   - Methodologies: Agile=Scrum, CI/CD=DevOps pipelines, TDD=Test-Driven Development
4. Apply hard filters ("ONLY", "must", "exclude", "no more than") as deal-breakers — auto-disqualify violators
5. NEVER fabricate candidates. Use ONLY candidates from the pool above.
6. If few match, say so honestly and suggest specific ways to broaden criteria.

Return exactly {num_candidates} candidates (or fewer if not enough qualify):

**#N. Full Name** | Score: X% | Category | Exp: X yrs | Location
- **Key Skills:** relevant skills (bold top matches, note skill gaps)
- **Work History:** recent roles, companies, notable achievements or projects
- **Match Analysis:** 3-4 sentences explaining why they're a strong fit. Reference specific skills from their profile that match the query. Be honest about any gaps or risks. Note transferable experience from adjacent domains.
- **Hiring Risk Assessment:** Low/Medium/High — briefly explain (e.g., "Low — exact skill match, right seniority, same city" or "Medium — strong skills but may be overqualified at 12 years for a mid-level role")
- **Fit Rating:** ⭐⭐⭐⭐⭐ Excellent / ⭐⭐⭐⭐ Strong / ⭐⭐⭐ Good / ⭐⭐ Partial
- **Contact:** email, phone

End with:
**📊 Search Intelligence** — How you interpreted the query, what filters were applied, how many candidates were evaluated ({relevant_count} pre-filtered from {total}), how many qualified, and overall pool strength for this role
**💡 Recruitment Recommendations** — Actionable next steps: interview order priority, additional screening questions to ask top candidates, criteria to relax if pool is thin, alternative job titles to search for"""

            result = await self._agenerate(prompt, temperature=0.15, max_tokens=6000, thinking_budget=8192)
            text_response = result or f"I'm here to help! We have **{total} candidates** in the database. What would you like to know?"
        
        if return_candidates:
            # ── Parse which candidates Gemini ACTUALLY mentioned in its response ──
            # Gemini re-ranks and picks its own top-N from the pool, so we must
            # build candidates_lookup from the NAMES in the response text, not
            # from the original pool order. This prevents card/text mismatch.
            import re as _re
            mentioned_pattern = _re.compile(
                r'\*{0,2}#(\d+)\.\s*(.+?)(?:\s*\*{0,2}\s*\||\s*\*{0,2}\s*\n|\s*\*{2,})',
                _re.MULTILINE
            )
            mentioned_entries = mentioned_pattern.findall(text_response or '')
            
            candidates_lookup = []
            used_ids = set()
            
            if mentioned_entries:
                # Build lookup in the order Gemini listed them
                for rank_str, raw_name in mentioned_entries:
                    name_clean = raw_name.strip().strip('*').strip().lower()
                    # Find best match in pool by name
                    best_match = None
                    best_score = 0
                    for c in _selected_candidates:
                        if c.get('id', '') in used_ids:
                            continue
                        cname = (c.get('name', '') or '').lower().strip()
                        # Exact match
                        if cname == name_clean:
                            best_match = c
                            best_score = 100
                            break
                        # Starts-with or contains
                        cname_base = cname.split('–')[0].split('-')[0].strip()
                        nclean_base = name_clean.split('–')[0].split('-')[0].strip()
                        if cname_base == nclean_base or cname.startswith(nclean_base) or nclean_base.startswith(cname_base):
                            if best_score < 90:
                                best_match = c
                                best_score = 90
                        else:
                            # Word overlap matching
                            cn_words = set(cname_base.split())
                            nn_words = set(nclean_base.split())
                            overlap = len(cn_words & nn_words)
                            if overlap >= 2 or (overlap >= 1 and min(len(cn_words), len(nn_words)) <= 2):
                                score = overlap * 30
                                if score > best_score:
                                    best_match = c
                                    best_score = score
                    
                    if best_match:
                        used_ids.add(best_match.get('id', ''))
                        candidates_lookup.append({
                            'index': int(rank_str),
                            'id': best_match.get('id', ''),
                            'name': best_match.get('name', ''),
                            'matchScore': best_match.get('matchScore', best_match.get('match_score', 0)),
                            'location': best_match.get('location', ''),
                            'jobCategory': best_match.get('jobCategory', best_match.get('job_category', '')),
                            'experience': best_match.get('experience', 0),
                            'skills': best_match.get('skills', [])[:10],
                            'email': best_match.get('email', ''),
                            'phone': best_match.get('phone', ''),
                            'status': best_match.get('status', 'New'),
                            'hasResume': best_match.get('hasResume', False),
                        })
            
            # Fallback: if parsing found nothing, send top-N from pool
            if not candidates_lookup:
                for i, c in enumerate(_selected_candidates[:num_candidates]):
                    cid = c.get('id', '')
                    if cid in used_ids:
                        continue  # Skip duplicates in fallback too
                    used_ids.add(cid)
                    candidates_lookup.append({
                        'index': i + 1,
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
                        'hasResume': c.get('hasResume', False),
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
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        if api_key:
            _gemini_service = GeminiService(api_key=api_key, model_name=model)
        else:
            logger.info("💡 GEMINI_API_KEY not set — Gemini service not initialized")
            return None
    return _gemini_service
