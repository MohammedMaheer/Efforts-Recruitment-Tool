"""
Role-Based Candidate Scoring Service
Scores candidates with weights adjusted for job seniority level

Phase 2.2: Role-based scoring with seniority awareness
- Junior roles: Prioritize learning potential (skills > experience)
- Mid roles: Balanced weighting (skills = experience > education)
- Senior roles: Prioritize proven experience (experience > skills)
- Lead roles: Strategic knowledge + industry experience weighted highest
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ScoringService:
    """Score candidates with seniority-aware weighting."""

    # Seniority-based weighting schemes
    SENIORITY_WEIGHTS = {
        'junior': {
            'skills': 0.50,
            'experience': 0.20,
            'education': 0.20,
            'certifications': 0.10,
        },
        'mid': {
            'skills': 0.40,
            'experience': 0.30,
            'education': 0.20,
            'certifications': 0.10,
        },
        'senior': {
            'skills': 0.30,
            'experience': 0.50,
            'education': 0.10,
            'certifications': 0.10,
        },
        'lead': {
            'skills': 0.25,
            'experience': 0.55,
            'education': 0.15,
            'certifications': 0.05,
        },
    }

    def __init__(self):
        pass

    def score_candidate_for_role(self, candidate: Dict, seniority: str = 'mid') -> float:
        """
        Compute role-based score for a candidate.

        Args:
            candidate: Candidate dict with 'skills', 'experience', 'education', 'certifications'
            seniority: Job seniority level (junior|mid|senior|lead)

        Returns:
            Score 0-100 adjusted for role requirements
        """
        # Validate and normalize seniority
        seniority = (seniority or 'mid').lower()
        if seniority not in self.SENIORITY_WEIGHTS:
            seniority = 'mid'

        # Get weighting scheme for this seniority
        weights = self.SENIORITY_WEIGHTS[seniority]

        # Calculate component scores (each 0-100)
        skill_score = self._score_skills(candidate)
        exp_score = self._score_experience(candidate)
        edu_score = self._score_education(candidate)
        cert_score = self._score_certifications(candidate)

        # Weighted sum
        final_score = (
            skill_score * weights['skills'] +
            exp_score * weights['experience'] +
            edu_score * weights['education'] +
            cert_score * weights['certifications']
        )

        # Clamp to 0-100
        return max(0, min(100, round(final_score, 1)))

    def _score_skills(self, candidate: Dict) -> float:
        """Score skills component (0-100)."""
        skills = candidate.get('skills', [])
        if not isinstance(skills, list):
            skills = []

        skill_count = len(skills)
        if skill_count >= 8:
            return 100.0
        elif skill_count >= 5:
            return 75.0
        elif skill_count >= 3:
            return 50.0
        elif skill_count >= 1:
            return 25.0
        else:
            return 0.0

    def _score_experience(self, candidate: Dict) -> float:
        """Score experience component (0-100)."""
        experience = candidate.get('experience', 0)
        if not isinstance(experience, (int, float)):
            try:
                experience = int(experience) if experience else 0
            except (ValueError, TypeError):
                experience = 0

        if experience >= 10:
            return 100.0
        elif experience >= 5:
            return 75.0
        elif experience >= 2:
            return 50.0
        elif experience >= 1:
            return 25.0
        else:
            return 0.0

    def _score_education(self, candidate: Dict) -> float:
        """Score education component (0-100)."""
        education = candidate.get('education', [])
        if not isinstance(education, list):
            education = []

        has_degree = any(
            'degree' in str(edu).lower() or
            'bachelor' in str(edu).lower() or
            'master' in str(edu).lower() or
            'phd' in str(edu).lower()
            for edu in education
        )

        has_certs = len(candidate.get('certifications', [])) > 0

        if has_degree and has_certs:
            return 100.0
        elif has_degree:
            return 75.0
        elif has_certs:
            return 50.0
        else:
            return 25.0

    def _score_certifications(self, candidate: Dict) -> float:
        """Score certifications component (0-100)."""
        certs = candidate.get('certifications', [])
        if not isinstance(certs, list):
            certs = []

        cert_count = len(certs)
        if cert_count >= 3:
            return 100.0
        elif cert_count >= 2:
            return 75.0
        elif cert_count >= 1:
            return 50.0
        else:
            return 0.0

    def score_candidates_for_role(self, candidates: List[Dict], seniority: str = 'mid') -> List[Dict]:
        """
        Score a list of candidates for a job with specific seniority level.

        Adds 'role_score' field to each candidate.

        Args:
            candidates: List of candidate dicts
            seniority: Job seniority level

        Returns:
            Candidates with added 'role_score' field, sorted descending by score
        """
        for candidate in candidates:
            candidate['role_score'] = self.score_candidate_for_role(candidate, seniority)

        # Sort by role_score descending
        return sorted(candidates, key=lambda c: c.get('role_score', 0), reverse=True)


# Singleton instance
_scoring_service = None


def get_scoring_service() -> ScoringService:
    """Get or create the scoring service singleton."""
    global _scoring_service
    if _scoring_service is None:
        _scoring_service = ScoringService()
    return _scoring_service
