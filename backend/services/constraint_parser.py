"""
Natural language constraint parser for AI search.

Extracts structured constraints (skills, experience, salary, location, etc.)
from natural language queries to enable two-stage candidate filtering.
"""
import re
import logging
from typing import List, Optional, Dict, Set
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Patterns ──────────────────────────────────────────────────────────────────

# Experience patterns: "5 years", "3-5 yrs", "senior with 8+ exp"
EXPERIENCE_PATTERN = re.compile(r'(\d+)\s*(?:\+|-\s*)?(?:\d+)?\s*(?:year|yr|yrs|years|exp|experience)', re.IGNORECASE)
EXPERIENCE_RANGE_PATTERN = re.compile(r'(\d+)\s*[-–—to]\s*(\d+)\s*(?:year|yr|yrs|yearsexp|experience)', re.IGNORECASE)

# Seniority patterns
SENIORITY_PATTERN = re.compile(
    r'\b(junior|mid|mid-level|midlevel|senior|lead|principal|staff|executive|c-level)\b',
    re.IGNORECASE
)

# Salary patterns: "$100k-150k", "100k-150k salary", "150k LPA"
SALARY_PATTERN = re.compile(r'(?:\$|₹)?(\d+(?:k|,000)?)\s*(?:[-–—to]\s*(?:\$|₹)?(\d+(?:k|,000)?))?\s*(?:lpa|salary|inr|usd)', re.IGNORECASE)
SALARY_RANGE_PATTERN = re.compile(r'(?:\$|₹)?(\d+)k?\s*[-–—to]\s*(?:\$|₹)?(\d+)k?', re.IGNORECASE)

# Remote patterns
REMOTE_PATTERN = re.compile(
    r'\b(remote|wfh|work from home|on-site|onsite|hybrid|in-office|office-based)\b',
    re.IGNORECASE
)

# Education patterns
EDUCATION_PATTERN = re.compile(
    r'\b(high school|hs|bachelors?|b\.?s|b\.?tech|b\.?a|masters?|m\.?s|m\.?tech|m\.?a|phd|ph\.?d|doctorate|degree)\b',
    re.IGNORECASE
)

# Notice period patterns: "immediate", "2 weeks", "1 month", "30 days"
NOTICE_PATTERN = re.compile(
    r'\b(immediate|2\s*weeks?|2w|1\s*month?|30\s*days?|notice|available)\b',
    re.IGNORECASE
)

# Location patterns: common countries/regions
LOCATION_PATTERN = re.compile(
    r'\b(india|usa|uk|uae|dubai|singapore|bangalore|mumbai|delhi|hyderabad|london|new york|san francisco|remote)\b',
    re.IGNORECASE
)

# Industry/company type patterns
INDUSTRY_PATTERN = re.compile(
    r'\b(startup|scale-?up|scaleup|fortune|enterprise|mid-?size|midsize|agency|product|service|saas|b2b|b2c)\b',
    re.IGNORECASE
)


# ── Skill Synonyms (reused from gemini_service.py) ──────────────────────────

SKILL_SYNONYMS = {
    'python': {'py', 'python3'},
    'javascript': {'js', 'ecmascript', 'es6'},
    'typescript': {'ts'},
    'java': {'j2ee', 'jee', 'jdk'},
    'react': {'reactjs', 'react.js', 'react js'},
    'node': {'nodejs', 'node.js', 'node js'},
    'angular': {'angularjs', 'angular.js', 'angular js'},
    'vue': {'vuejs', 'vue.js', 'vue js'},
    'django': {'django rest framework', 'drf'},
    'flask': {'flask'},
    'fastapi': {'fast api'},
    'spring': {'spring boot', 'springboot', 'spring framework'},
    'aws': {'amazon web services', 'cloud', 'ec2', 's3', 'lambda'},
    'azure': {'microsoft azure', 'cloud'},
    'gcp': {'google cloud', 'google cloud platform', 'cloud'},
    'docker': {'container', 'containerization'},
    'kubernetes': {'k8s', 'container orchestration'},
    'devops': {'ci/cd', 'cicd', 'docker', 'kubernetes', 'jenkins', 'terraform', 'ansible'},
    'sql': {'mysql', 'postgresql', 'oracle', 'database', 'rdbms', 'mssql', 'sql server'},
    'mongodb': {'mongo', 'nosql'},
    'ml': {'machine learning', 'machine', 'learning'},
    'ai': {'artificial intelligence', 'machine learning', 'ml', 'deep learning'},
    'data science': {'data scientist', 'analytics', 'machine learning'},
    'selenium': {'test automation', 'qa', 'web testing'},
    'golang': {'go', 'go lang'},
    'rust': {'rust'},
    'c++': {'cpp', 'c plus plus', 'cplusplus'},
    'c#': {'csharp', 'c sharp', '.net', 'dotnet'},
}

# ── Location Aliases (reused from gemini_service.py) ──────────────────────

LOCATION_ALIASES = {
    'bangalore': ['bengaluru', 'karnataka', 'blr'],
    'bengaluru': ['bangalore', 'karnataka'],
    'delhi': ['new delhi', 'ncr', 'noida', 'gurgaon', 'gurugram', 'faridabad'],
    'mumbai': ['maharashtra', 'bombay', 'navi mumbai'],
    'hyderabad': ['telangana', 'andhra pradesh'],
    'uae': ['dubai', 'abu dhabi', 'abudhabi', 'sharjah', 'ajman'],
    'dubai': ['uae', 'emirates', 'dxb'],
    'usa': ['united states', 'us', 'california', 'texas', 'new york'],
    'uk': ['united kingdom', 'london', 'england'],
    'singapore': ['sg', 'lion city'],
}


# ── Pydantic Models ──────────────────────────────────────────────────────────

class ParsedConstraints(BaseModel):
    """Structured constraints extracted from natural language query."""
    required_skills: List[str] = []
    nice_to_have_skills: List[str] = []
    min_experience: Optional[int] = None
    max_experience: Optional[int] = None
    seniority_level: Optional[str] = None  # junior, mid, senior, lead
    locations: List[str] = []
    remote_type: Optional[str] = None  # required, optional, not-allowed
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    education_level: Optional[str] = None  # high_school, bachelors, masters, phd
    industries: List[str] = []
    notice_period_max: Optional[str] = None  # immediate, 2weeks, 1month
    languages: List[str] = []

    def dict(self):
        """Return dict excluding None/empty values."""
        return {k: v for k, v in super().dict().items() if v and (not isinstance(v, list) or len(v) > 0)}


# ── Constraint Parser ─────────────────────────────────────────────────────────

class ConstraintParser:
    """Parse natural language queries into structured constraints."""

    def parse_query(self, query: str) -> ParsedConstraints:
        """
        Parse natural language query into structured constraints.

        Example:
        "Find me a Python developer in Bangalore with 3-5 years experience,
         remote or hybrid, masters degree, willing to relocate, salary 80-120k"
        """
        query_lower = query.lower()

        return ParsedConstraints(
            required_skills=self._extract_required_skills(query_lower),
            nice_to_have_skills=self._extract_nice_to_have_skills(query_lower),
            min_experience=self._extract_min_experience(query_lower),
            max_experience=self._extract_max_experience(query_lower),
            seniority_level=self._extract_seniority(query_lower),
            locations=self._extract_locations(query_lower),
            remote_type=self._extract_remote_type(query_lower),
            min_salary=self._extract_min_salary(query_lower),
            max_salary=self._extract_max_salary(query_lower),
            education_level=self._extract_education_level(query_lower),
            industries=self._extract_industries(query_lower),
            notice_period_max=self._extract_notice_period(query_lower),
            languages=self._extract_languages(query_lower),
        )

    def _extract_required_skills(self, query: str) -> List[str]:
        """Extract must-have skills from query."""
        skills = set()

        # Look for "must have", "required", "mandatory" keywords
        must_have_section = re.search(r'(?:must have|required|mandatory|essential)[:\s]*([^.]*?)(?:\.|\bnote\b|$)', query, re.IGNORECASE)
        if must_have_section:
            skills_text = must_have_section.group(1)
        else:
            # Default: first sentence or take all skills mentioned
            skills_text = query.split('.')[0]

        # Find skill mentions
        for skill_canonical, aliases in SKILL_SYNONYMS.items():
            pattern = r'\b(' + '|'.join([re.escape(skill_canonical)] + [re.escape(a) for a in aliases]) + r')\b'
            if re.search(pattern, skills_text, re.IGNORECASE):
                skills.add(skill_canonical)

        return list(skills)

    def _extract_nice_to_have_skills(self, query: str) -> List[str]:
        """Extract nice-to-have skills from query."""
        skills = set()

        # Look for "nice to have", "preferred", "bonus" keywords
        nice_section = re.search(r'(?:nice to have|preferred|bonus|additional|plus)[:\s]*([^.]*?)(?:\.|\b(?:must|required)\b|$)', query, re.IGNORECASE)
        if nice_section:
            skills_text = nice_section.group(1)

            for skill_canonical, aliases in SKILL_SYNONYMS.items():
                pattern = r'\b(' + '|'.join([re.escape(skill_canonical)] + [re.escape(a) for a in aliases]) + r')\b'
                if re.search(pattern, skills_text, re.IGNORECASE):
                    skills.add(skill_canonical)

        return list(skills)

    def _extract_min_experience(self, query: str) -> Optional[int]:
        """Extract minimum experience requirement."""
        # Look for patterns like "3-5 years", "5+ years", "at least 3 years"
        range_match = EXPERIENCE_RANGE_PATTERN.search(query)
        if range_match:
            return int(range_match.group(1))

        # Look for single experience number
        exp_matches = EXPERIENCE_PATTERN.findall(query)
        if exp_matches:
            # Take the first one as minimum
            return int(exp_matches[0])

        return None

    def _extract_max_experience(self, query: str) -> Optional[int]:
        """Extract maximum experience (for range like 3-5 years)."""
        range_match = EXPERIENCE_RANGE_PATTERN.search(query)
        if range_match:
            return int(range_match.group(2))
        return None

    def _extract_seniority(self, query: str) -> Optional[str]:
        """Extract seniority level (junior, mid, senior, lead)."""
        match = SENIORITY_PATTERN.search(query)
        if match:
            level = match.group(1).lower()
            # Normalize variations
            if level in ('mid', 'mid-level', 'midlevel'):
                return 'mid'
            elif level in ('senior'):
                return 'senior'
            elif level in ('junior'):
                return 'junior'
            elif level in ('lead', 'principal', 'staff'):
                return 'lead'
        return None

    def _extract_locations(self, query: str) -> List[str]:
        """Extract location requirements."""
        locations = set()

        for loc_canonical, aliases in LOCATION_ALIASES.items():
            pattern = r'\b(' + '|'.join([re.escape(loc_canonical)] + [re.escape(a) for a in aliases]) + r')\b'
            if re.search(pattern, query, re.IGNORECASE):
                locations.add(loc_canonical)

        return list(locations)

    def _extract_remote_type(self, query: str) -> Optional[str]:
        """Extract remote work preference (required, optional, not-allowed)."""
        # Check for explicit remote requirement
        if re.search(r'\b(?:must be remote|remote required|remote only|fully remote)\b', query, re.IGNORECASE):
            return 'required'
        elif re.search(r'\b(?:remote|wfh|work from home|can be remote)\b', query, re.IGNORECASE):
            return 'optional'
        elif re.search(r'\b(?:on-?site|onsite|office|in-office|no remote)\b', query, re.IGNORECASE):
            return 'not-allowed'

        return None

    def _extract_min_salary(self, query: str) -> Optional[float]:
        """Extract minimum salary expectation."""
        # Try range pattern first
        range_match = SALARY_RANGE_PATTERN.search(query)
        if range_match:
            min_val = range_match.group(1)
            return float(min_val.replace('k', '000').replace(',', ''))

        # Try detailed salary pattern
        sal_matches = SALARY_PATTERN.findall(query)
        if sal_matches:
            first_match = sal_matches[0]
            if first_match[0]:  # min value
                val = first_match[0].replace('k', '000').replace(',', '')
                return float(val)

        return None

    def _extract_max_salary(self, query: str) -> Optional[float]:
        """Extract maximum salary expectation."""
        # Try range pattern first
        range_match = SALARY_RANGE_PATTERN.search(query)
        if range_match:
            max_val = range_match.group(2)
            return float(max_val.replace('k', '000').replace(',', ''))

        # Try detailed salary pattern
        sal_matches = SALARY_PATTERN.findall(query)
        if sal_matches:
            first_match = sal_matches[0]
            if first_match[1]:  # max value
                val = first_match[1].replace('k', '000').replace(',', '')
                return float(val)

        return None

    def _extract_education_level(self, query: str) -> Optional[str]:
        """Extract education requirement."""
        match = EDUCATION_PATTERN.search(query)
        if match:
            edu = match.group(1).lower()
            if 'phd' in edu or 'doctorate' in edu:
                return 'phd'
            elif 'master' in edu or 'm.s' in edu or 'm.tech' in edu:
                return 'masters'
            elif 'bachelor' in edu or 'b.s' in edu or 'b.tech' in edu or 'b.a' in edu or 'degree' in edu:
                return 'bachelors'
            elif 'high school' in edu or 'hs' in edu:
                return 'high_school'
        return None

    def _extract_industries(self, query: str) -> List[str]:
        """Extract industry preference."""
        industries = set()
        match = INDUSTRY_PATTERN.findall(query)
        if match:
            industries.update(match)
        return list(industries)

    def _extract_notice_period(self, query: str) -> Optional[str]:
        """Extract notice period requirement."""
        match = NOTICE_PATTERN.search(query)
        if match:
            notice = match.group(1).lower()
            if 'immediate' in notice or 'available' in notice:
                return 'immediate'
            elif '2' in notice or 'week' in notice:
                return '2weeks'
            elif 'month' in notice or '30' in notice:
                return '1month'
        return None

    def _extract_languages(self, query: str) -> List[str]:
        """Extract language requirements."""
        languages = set()

        # Common language patterns
        lang_patterns = {
            'english': [r'\benglish\b'],
            'spanish': [r'\bspanish\b'],
            'french': [r'\bfrench\b'],
            'german': [r'\bgerman\b'],
            'mandarin': [r'\bmandarin\b', r'\bchinese\b'],
            'hindi': [r'\bhindi\b'],
            'tamil': [r'\btamil\b'],
            'telugu': [r'\btelugu\b'],
            'kannada': [r'\bkannada\b'],
            'marathi': [r'\bmarathi\b'],
        }

        for lang, patterns in lang_patterns.items():
            for pattern in patterns:
                if re.search(pattern, query, re.IGNORECASE):
                    languages.add(lang)
                    break

        return list(languages)


# ── Singleton ─────────────────────────────────────────────────────────────────

_constraint_parser = None

def get_constraint_parser() -> ConstraintParser:
    """Get or create constraint parser singleton."""
    global _constraint_parser
    if _constraint_parser is None:
        _constraint_parser = ConstraintParser()
    return _constraint_parser
