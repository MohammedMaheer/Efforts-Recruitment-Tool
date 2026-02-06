"""
Duplicate Detection Service
ML-based candidate deduplication using fuzzy matching
Identifies same person with different emails, names, or profiles
"""
import logging
import re
from typing import Dict, List, Optional, Tuple, Set
from difflib import SequenceMatcher
import hashlib

logger = logging.getLogger(__name__)


class DuplicateDetector:
    """
    Intelligent duplicate detection using multiple signals:
    - Name similarity (fuzzy matching)
    - Phone number matching
    - Email domain + name patterns
    - Skills overlap
    - Work history overlap
    - LinkedIn profile matching
    """
    
    # Weights for different matching criteria
    WEIGHTS = {
        'phone_exact': 50,        # Same phone = very likely duplicate
        'linkedin_exact': 45,     # Same LinkedIn = definite duplicate
        'email_similar': 30,      # Similar email pattern
        'name_fuzzy': 25,         # Similar name
        'skills_overlap': 15,     # High skill overlap
        'work_history': 20,       # Same companies
        'location_match': 5,      # Same location
    }
    
    # Threshold for considering duplicates
    DUPLICATE_THRESHOLD = 70  # 70+ = likely duplicate
    POSSIBLE_THRESHOLD = 50   # 50-70 = possible duplicate
    
    def __init__(self):
        self.name_cache = {}  # Cache for name normalization
    
    def normalize_name(self, name: str) -> str:
        """Normalize name for comparison"""
        if not name:
            return ""
        
        if name in self.name_cache:
            return self.name_cache[name]
        
        # Lowercase and remove extra spaces
        normalized = name.lower().strip()
        
        # Remove titles
        titles = ['mr', 'mrs', 'ms', 'dr', 'prof', 'sir', 'eng', 'engr']
        for title in titles:
            normalized = re.sub(rf'\b{title}\.?\b', '', normalized)
        
        # Remove punctuation
        normalized = re.sub(r'[^\w\s]', '', normalized)
        
        # Collapse multiple spaces
        normalized = ' '.join(normalized.split())
        
        self.name_cache[name] = normalized
        return normalized
    
    def normalize_phone(self, phone: str) -> str:
        """Normalize phone number (keep only digits)"""
        if not phone:
            return ""
        
        # Extract digits only
        digits = re.sub(r'\D', '', phone)
        
        # Remove country codes for comparison (keep last 10 digits)
        if len(digits) > 10:
            digits = digits[-10:]
        
        return digits
    
    def normalize_email(self, email: str) -> Tuple[str, str]:
        """Split email into local part and domain"""
        if not email or '@' not in email:
            return ("", "")
        
        local, domain = email.lower().split('@', 1)
        
        # Remove common suffixes from local part (like +tag)
        local = local.split('+')[0]
        
        # Remove dots from Gmail (john.doe and johndoe are same)
        if 'gmail' in domain:
            local = local.replace('.', '')
        
        return (local, domain)
    
    def name_similarity(self, name1: str, name2: str) -> float:
        """Calculate similarity between two names (0-1)"""
        n1 = self.normalize_name(name1)
        n2 = self.normalize_name(name2)
        
        if not n1 or not n2:
            return 0.0
        
        # Exact match
        if n1 == n2:
            return 1.0
        
        # Check for name parts
        parts1 = set(n1.split())
        parts2 = set(n2.split())
        
        # If all parts of shorter name are in longer name
        if parts1.issubset(parts2) or parts2.issubset(parts1):
            return 0.9
        
        # Common parts ratio
        common = parts1 & parts2
        if common:
            total = parts1 | parts2
            ratio = len(common) / len(total)
            if ratio >= 0.5:
                return 0.7 + (ratio - 0.5) * 0.4
        
        # Fuzzy string matching
        return SequenceMatcher(None, n1, n2).ratio()
    
    def email_similarity(self, email1: str, email2: str) -> float:
        """Calculate similarity between two emails"""
        local1, domain1 = self.normalize_email(email1)
        local2, domain2 = self.normalize_email(email2)
        
        if not local1 or not local2:
            return 0.0
        
        # Exact match
        if local1 == local2 and domain1 == domain2:
            return 1.0
        
        # Same local part, different domain (job change)
        if local1 == local2:
            return 0.8
        
        # Similar local parts
        local_sim = SequenceMatcher(None, local1, local2).ratio()
        
        # Boost if same domain
        if domain1 == domain2:
            return min(1.0, local_sim + 0.2)
        
        return local_sim
    
    def skills_overlap(self, skills1: List[str], skills2: List[str]) -> float:
        """Calculate skill overlap ratio"""
        if not skills1 or not skills2:
            return 0.0
        
        set1 = set(s.lower() for s in skills1)
        set2 = set(s.lower() for s in skills2)
        
        intersection = set1 & set2
        union = set1 | set2
        
        if not union:
            return 0.0
        
        # Jaccard similarity
        return len(intersection) / len(union)
    
    def work_history_overlap(self, history1: List[Dict], history2: List[Dict]) -> float:
        """Check if candidates worked at same companies"""
        if not history1 or not history2:
            return 0.0
        
        def extract_companies(history):
            companies = set()
            for job in history:
                if isinstance(job, dict):
                    company = job.get('company', '').lower()
                    if company:
                        # Normalize company name
                        company = re.sub(r'\b(inc|llc|ltd|corp|company|co)\b', '', company)
                        company = company.strip()
                        if company:
                            companies.add(company)
            return companies
        
        companies1 = extract_companies(history1)
        companies2 = extract_companies(history2)
        
        if not companies1 or not companies2:
            return 0.0
        
        # Check for overlap
        overlap = companies1 & companies2
        if overlap:
            return min(1.0, len(overlap) * 0.5)
        
        # Check for fuzzy company matches
        for c1 in companies1:
            for c2 in companies2:
                sim = SequenceMatcher(None, c1, c2).ratio()
                if sim > 0.8:
                    return 0.5
        
        return 0.0
    
    def calculate_duplicate_score(self, candidate1: Dict, candidate2: Dict) -> Dict:
        """
        Calculate overall duplicate likelihood score
        Returns score (0-100) and breakdown of matching factors
        """
        score = 0
        breakdown = {}
        
        # Phone match (strongest signal)
        phone1 = self.normalize_phone(candidate1.get('phone', ''))
        phone2 = self.normalize_phone(candidate2.get('phone', ''))
        
        if phone1 and phone2 and len(phone1) >= 7:
            if phone1 == phone2:
                breakdown['phone_exact'] = self.WEIGHTS['phone_exact']
                score += self.WEIGHTS['phone_exact']
            elif phone1[-7:] == phone2[-7:]:  # Last 7 digits match
                breakdown['phone_partial'] = self.WEIGHTS['phone_exact'] * 0.7
                score += self.WEIGHTS['phone_exact'] * 0.7
        
        # LinkedIn match
        linkedin1 = (candidate1.get('linkedin') or '').lower()
        linkedin2 = (candidate2.get('linkedin') or '').lower()
        
        if linkedin1 and linkedin2:
            # Extract username from URL
            def extract_linkedin_user(url):
                match = re.search(r'linkedin\.com/in/([^/?\s]+)', url)
                return match.group(1) if match else url
            
            user1 = extract_linkedin_user(linkedin1)
            user2 = extract_linkedin_user(linkedin2)
            
            if user1 == user2:
                breakdown['linkedin_exact'] = self.WEIGHTS['linkedin_exact']
                score += self.WEIGHTS['linkedin_exact']
        
        # Email similarity
        email_sim = self.email_similarity(
            candidate1.get('email', ''),
            candidate2.get('email', '')
        )
        if email_sim > 0.5:
            email_score = email_sim * self.WEIGHTS['email_similar']
            breakdown['email_similar'] = round(email_score, 1)
            score += email_score
        
        # Name similarity
        name_sim = self.name_similarity(
            candidate1.get('name', ''),
            candidate2.get('name', '')
        )
        if name_sim > 0.6:
            name_score = name_sim * self.WEIGHTS['name_fuzzy']
            breakdown['name_fuzzy'] = round(name_score, 1)
            score += name_score
        
        # Skills overlap
        skills_sim = self.skills_overlap(
            candidate1.get('skills', []),
            candidate2.get('skills', [])
        )
        if skills_sim > 0.5:
            skills_score = skills_sim * self.WEIGHTS['skills_overlap']
            breakdown['skills_overlap'] = round(skills_score, 1)
            score += skills_score
        
        # Work history
        history_sim = self.work_history_overlap(
            candidate1.get('workHistory', []),
            candidate2.get('workHistory', [])
        )
        if history_sim > 0:
            history_score = history_sim * self.WEIGHTS['work_history']
            breakdown['work_history'] = round(history_score, 1)
            score += history_score
        
        # Location match
        loc1 = (candidate1.get('location') or '').lower()
        loc2 = (candidate2.get('location') or '').lower()
        
        if loc1 and loc2 and (loc1 in loc2 or loc2 in loc1):
            breakdown['location_match'] = self.WEIGHTS['location_match']
            score += self.WEIGHTS['location_match']
        
        # Determine duplicate status
        if score >= self.DUPLICATE_THRESHOLD:
            status = 'likely_duplicate'
        elif score >= self.POSSIBLE_THRESHOLD:
            status = 'possible_duplicate'
        else:
            status = 'not_duplicate'
        
        return {
            'score': round(min(100, score), 1),
            'status': status,
            'breakdown': breakdown,
            'candidate1_id': candidate1.get('id'),
            'candidate2_id': candidate2.get('id'),
        }
    
    def find_duplicates(self, candidates: List[Dict], new_candidate: Dict = None) -> List[Dict]:
        """
        Find potential duplicates for a candidate
        If new_candidate is provided, check against all existing
        Otherwise, find all duplicate pairs in the list
        """
        duplicates = []
        
        if new_candidate:
            # Check new candidate against existing
            for existing in candidates:
                if existing.get('id') == new_candidate.get('id'):
                    continue
                
                result = self.calculate_duplicate_score(new_candidate, existing)
                if result['status'] != 'not_duplicate':
                    duplicates.append(result)
        else:
            # Find all duplicate pairs (O(n²) - use for batch processing)
            seen_pairs = set()
            
            for i, c1 in enumerate(candidates):
                for c2 in candidates[i+1:]:
                    pair_key = tuple(sorted([c1.get('id', i), c2.get('id', '')]))
                    if pair_key in seen_pairs:
                        continue
                    
                    result = self.calculate_duplicate_score(c1, c2)
                    if result['status'] != 'not_duplicate':
                        duplicates.append(result)
                        seen_pairs.add(pair_key)
        
        # Sort by score (highest first)
        return sorted(duplicates, key=lambda x: x['score'], reverse=True)
    
    def merge_candidates(self, primary: Dict, secondary: Dict) -> Dict:
        """
        Merge two candidate profiles, preferring non-empty values
        Primary candidate's ID is kept
        """
        merged = primary.copy()
        
        # Fields to merge (prefer non-empty)
        merge_fields = ['phone', 'location', 'linkedin', 'summary']
        
        for field in merge_fields:
            if not merged.get(field) and secondary.get(field):
                merged[field] = secondary[field]
        
        # Merge skills (union)
        primary_skills = set(merged.get('skills', []))
        secondary_skills = set(secondary.get('skills', []))
        merged['skills'] = list(primary_skills | secondary_skills)
        
        # Merge work history (combine if different companies)
        primary_history = merged.get('workHistory', []) or []
        secondary_history = secondary.get('workHistory', []) or []
        
        # Get existing company names
        existing_companies = set()
        for job in primary_history:
            if isinstance(job, dict):
                existing_companies.add(job.get('company', '').lower())
        
        # Add jobs from secondary that aren't in primary
        for job in secondary_history:
            if isinstance(job, dict):
                company = job.get('company', '').lower()
                if company and company not in existing_companies:
                    primary_history.append(job)
                    existing_companies.add(company)
        
        merged['workHistory'] = primary_history
        
        # Merge education
        primary_edu = merged.get('education', []) or []
        secondary_edu = secondary.get('education', []) or []
        
        if isinstance(primary_edu, list) and isinstance(secondary_edu, list):
            # Simple dedup by degree
            existing_degrees = set()
            for edu in primary_edu:
                if isinstance(edu, dict):
                    existing_degrees.add(edu.get('degree', '').lower())
            
            for edu in secondary_edu:
                if isinstance(edu, dict):
                    degree = edu.get('degree', '').lower()
                    if degree and degree not in existing_degrees:
                        primary_edu.append(edu)
            
            merged['education'] = primary_edu
        
        # Take higher match score
        merged['matchScore'] = max(
            merged.get('matchScore', 0),
            secondary.get('matchScore', 0)
        )
        
        # Note the merge
        merged['merged_from'] = secondary.get('id')
        merged['merged_at'] = __import__('datetime').datetime.now().isoformat()
        
        return merged
    
    def generate_duplicate_hash(self, candidate: Dict) -> str:
        """
        Generate a hash for quick duplicate detection
        Uses normalized phone and name for grouping
        """
        phone = self.normalize_phone(candidate.get('phone', ''))
        name = self.normalize_name(candidate.get('name', ''))
        
        # Create hash from stable identifiers
        data = f"{phone[-7:] if phone else ''}{name}"
        return hashlib.md5(data.encode()).hexdigest()[:12]


# Singleton
_duplicate_detector = None

def get_duplicate_detector() -> DuplicateDetector:
    global _duplicate_detector
    if _duplicate_detector is None:
        _duplicate_detector = DuplicateDetector()
    return _duplicate_detector
