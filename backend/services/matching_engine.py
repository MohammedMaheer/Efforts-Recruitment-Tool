from typing import List, Dict, Any
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

class MatchingEngine:
    """AI-powered candidate matching engine"""
    
    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words='english',
            ngram_range=(1, 2)
        )
    
    async def match_candidates(
        self,
        job_description_id: str,
        candidate_ids: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Match candidates against job description
        Returns ranked list with match scores
        """
        # In production, fetch from database and run AI matching
        # Currently returns empty list - connect to your database
        return []
    
    async def evaluate_candidate(
        self,
        candidate_id: str,
        job_description_id: str
    ) -> Dict[str, Any]:
        """
        Detailed evaluation of candidate vs job description
        NOTE: Requires database integration to fetch actual candidate and JD data
        """
        # TODO: Fetch candidate and job description from database
        # TODO: Run AI evaluation model
        # Currently returns placeholder structure - connect to your database
        evaluation = {
            "match_score": 0,
            "strengths": [],
            "gaps": [],
            "recommendation": "Database integration required",
            "confidence_score": 0
        }
        
        return evaluation
    
    def calculate_skill_match(
        self,
        candidate_skills: List[str],
        required_skills: List[str],
        preferred_skills: List[str] = None
    ) -> Dict[str, Any]:
        """
        Calculate skill match percentage
        """
        candidate_skills_lower = [s.lower() for s in candidate_skills]
        required_skills_lower = [s.lower() for s in required_skills]
        
        # Calculate required skills match
        matched_required = [
            skill for skill in required_skills_lower
            if skill in candidate_skills_lower
        ]
        
        required_match_pct = len(matched_required) / len(required_skills) if required_skills else 0
        
        # Calculate preferred skills match
        preferred_match_pct = 0
        matched_preferred = []
        if preferred_skills:
            preferred_skills_lower = [s.lower() for s in preferred_skills]
            matched_preferred = [
                skill for skill in preferred_skills_lower
                if skill in candidate_skills_lower
            ]
            preferred_match_pct = len(matched_preferred) / len(preferred_skills)
        
        # Overall score (70% required, 30% preferred)
        overall_score = (required_match_pct * 0.7) + (preferred_match_pct * 0.3)
        
        return {
            "overall_score": round(overall_score * 100, 2),
            "required_match": round(required_match_pct * 100, 2),
            "preferred_match": round(preferred_match_pct * 100, 2),
            "matched_skills": matched_required + matched_preferred,
            "missing_required": [
                s for s in required_skills_lower
                if s not in candidate_skills_lower
            ],
            "missing_preferred": [
                s for s in (preferred_skills_lower if preferred_skills else [])
                if s not in candidate_skills_lower
            ]
        }
    
    def calculate_semantic_similarity(
        self,
        text1: str,
        text2: str
    ) -> float:
        """
        Calculate semantic similarity between two texts
        Uses TF-IDF and cosine similarity
        """
        try:
            vectors = self.vectorizer.fit_transform([text1, text2])
            similarity = cosine_similarity(vectors[0:1], vectors[1:2])[0][0]
            return float(similarity)
        except:
            return 0.0
    
    def generate_recommendation(
        self,
        match_score: float,
        strengths: List[str],
        gaps: List[str]
    ) -> str:
        """
        Generate AI recommendation based on match analysis
        """
        if match_score >= 85:
            return "Strong candidate - highly recommend for interview. Excellent skill match and qualifications."
        elif match_score >= 70:
            return "Good candidate - recommend interview. Skills align well with some minor gaps that can be addressed."
        elif match_score >= 50:
            return "Moderate candidate - consider for interview if other options limited. Has potential but significant gaps exist."
        else:
            return "Weak match - not recommended for this role. Skills and experience don't align with requirements."
    
    def categorize_candidate(self, match_score: float) -> str:
        """
        Categorize candidate based on match score
        """
        if match_score >= 80:
            return "Strong"
        elif match_score >= 60:
            return "Partial"
        else:
            return "Reject"
