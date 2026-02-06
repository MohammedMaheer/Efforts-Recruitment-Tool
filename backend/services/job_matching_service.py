"""
Two-Way Job Matching Service
Calculates both candidate-to-job and job-to-candidate fit scores
Provides detailed match breakdown and recommendations
"""
import logging
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class JobMatchingEngine:
    """
    Advanced bidirectional matching:
    1. Candidate Fit Score: How well does candidate match job requirements?
    2. Job Fit Score: How well does job match candidate's preferences?
    
    Produces actionable insights for both recruiters and candidates.
    """
    
    # Skill importance weights
    SKILL_WEIGHTS = {
        'required': 1.0,
        'preferred': 0.5,
        'nice_to_have': 0.25,
    }
    
    # Experience scoring thresholds
    EXPERIENCE_SCORING = {
        'exact_match': 1.0,     # Exactly what's required
        'over_qualified': 0.8,  # 2+ years above requirement
        'under_qualified': 0.3, # Below requirement
        'close_match': 0.9,     # Within 1 year
    }
    
    def __init__(self):
        # Cache for computed embeddings
        self.embedding_cache = {}
    
    def calculate_candidate_fit(self, candidate: Dict, job: Dict) -> Dict:
        """
        Calculate how well a candidate fits a job
        Returns detailed breakdown with overall score
        """
        scores = {}
        weights = {}
        
        # 1. Skills Match (40% weight)
        skills_result = self._match_skills(
            candidate.get('skills', []),
            job.get('required_skills', []),
            job.get('preferred_skills', []),
            job.get('nice_to_have_skills', [])
        )
        scores['skills'] = skills_result['score']
        weights['skills'] = 40
        
        # 2. Experience Match (25% weight)
        exp_result = self._match_experience(
            candidate.get('experience', 0),
            job.get('min_experience', 0),
            job.get('max_experience', 15)
        )
        scores['experience'] = exp_result['score']
        weights['experience'] = 25
        
        # 3. Education Match (15% weight)
        edu_result = self._match_education(
            candidate.get('education', []),
            job.get('required_education', ''),
            job.get('preferred_fields', [])
        )
        scores['education'] = edu_result['score']
        weights['education'] = 15
        
        # 4. Location Match (10% weight)
        loc_result = self._match_location(
            candidate.get('location', ''),
            job.get('location', ''),
            job.get('remote_friendly', True)
        )
        scores['location'] = loc_result['score']
        weights['location'] = 10
        
        # 5. Culture/Soft Skills Fit (10% weight)
        culture_result = self._match_culture(
            candidate.get('summary', ''),
            candidate.get('workHistory', []),
            job.get('culture_keywords', []),
            job.get('company_size', '')
        )
        scores['culture'] = culture_result['score']
        weights['culture'] = 10
        
        # Calculate weighted overall score
        total_score = sum(scores[k] * weights[k] / 100 for k in scores)
        
        # Determine fit level
        if total_score >= 85:
            fit_level = 'excellent'
            recommendation = 'Highly recommended - schedule interview immediately'
        elif total_score >= 70:
            fit_level = 'good'
            recommendation = 'Strong candidate - prioritize for review'
        elif total_score >= 55:
            fit_level = 'moderate'
            recommendation = 'Potential fit - review with hiring manager'
        elif total_score >= 40:
            fit_level = 'weak'
            recommendation = 'Significant gaps - consider only if pool is limited'
        else:
            fit_level = 'poor'
            recommendation = 'Not recommended - major misalignment'
        
        return {
            'overall_score': round(total_score, 1),
            'fit_level': fit_level,
            'recommendation': recommendation,
            'breakdown': {
                'skills': {
                    'score': round(scores['skills'], 1),
                    'weight': weights['skills'],
                    'details': skills_result
                },
                'experience': {
                    'score': round(scores['experience'], 1),
                    'weight': weights['experience'],
                    'details': exp_result
                },
                'education': {
                    'score': round(scores['education'], 1),
                    'weight': weights['education'],
                    'details': edu_result
                },
                'location': {
                    'score': round(scores['location'], 1),
                    'weight': weights['location'],
                    'details': loc_result
                },
                'culture': {
                    'score': round(scores['culture'], 1),
                    'weight': weights['culture'],
                    'details': culture_result
                }
            },
            'strengths': self._identify_strengths(scores, skills_result, exp_result),
            'gaps': self._identify_gaps(skills_result, exp_result, edu_result),
        }
    
    def calculate_job_fit(self, job: Dict, candidate: Dict) -> Dict:
        """
        Calculate how well a job fits candidate's preferences
        Considers salary, growth, location, tech stack preferences
        """
        scores = {}
        
        # 1. Salary Fit (if candidate has expectations)
        candidate_salary = candidate.get('salary_expectation', {})
        job_salary = job.get('salary_range', {})
        
        if candidate_salary and job_salary:
            salary_score = self._match_salary(candidate_salary, job_salary)
        else:
            salary_score = {'score': 75, 'note': 'Salary information not available'}
        scores['salary'] = salary_score
        
        # 2. Tech Stack Preference
        candidate_preferred_tech = candidate.get('preferred_technologies', [])
        job_tech = job.get('tech_stack', []) or job.get('required_skills', [])
        
        tech_score = self._match_tech_preference(candidate_preferred_tech, job_tech)
        scores['tech_stack'] = tech_score
        
        # 3. Career Growth Opportunity
        career_score = self._assess_career_fit(
            candidate.get('experience', 0),
            candidate.get('current_title', ''),
            job.get('title', ''),
            job.get('seniority_level', '')
        )
        scores['career_growth'] = career_score
        
        # 4. Work Style Match
        work_style_score = self._match_work_style(
            candidate.get('work_preferences', {}),
            job
        )
        scores['work_style'] = work_style_score
        
        # 5. Company Fit
        company_score = self._match_company_preference(
            candidate.get('company_preferences', {}),
            job.get('company', {})
        )
        scores['company'] = company_score
        
        # Calculate overall job fit
        overall = sum(s.get('score', 50) for s in scores.values()) / len(scores)
        
        if overall >= 80:
            fit_message = "This role is an excellent match for your career goals!"
        elif overall >= 65:
            fit_message = "This role aligns well with your preferences."
        elif overall >= 50:
            fit_message = "This role has some alignment with your preferences."
        else:
            fit_message = "This role may not be ideal for your preferences."
        
        return {
            'overall_score': round(overall, 1),
            'message': fit_message,
            'breakdown': scores,
            'highlights': self._extract_job_highlights(job, scores),
            'concerns': self._extract_job_concerns(scores),
        }
    
    def _match_skills(self, candidate_skills: List[str], required: List[str], 
                      preferred: List[str], nice_to_have: List[str]) -> Dict:
        """Match candidate skills against job requirements"""
        candidate_set = set(s.lower() for s in candidate_skills)
        required_set = set(s.lower() for s in required)
        preferred_set = set(s.lower() for s in preferred)
        nice_set = set(s.lower() for s in nice_to_have)
        
        # Required skills (most important)
        required_match = candidate_set & required_set
        required_missing = required_set - candidate_set
        
        # Preferred skills
        preferred_match = candidate_set & preferred_set
        
        # Nice to have
        nice_match = candidate_set & nice_set
        
        # Calculate score
        if required_set:
            required_score = len(required_match) / len(required_set) * 100
        else:
            required_score = 100  # No requirements = full score
        
        # Bonus for preferred (up to 15 points)
        if preferred_set:
            preferred_bonus = (len(preferred_match) / len(preferred_set)) * 15
        else:
            preferred_bonus = 0
        
        # Bonus for nice-to-have (up to 5 points)
        if nice_set:
            nice_bonus = (len(nice_match) / len(nice_set)) * 5
        else:
            nice_bonus = 0
        
        # Final score (capped at 100)
        final_score = min(100, required_score * 0.8 + preferred_bonus + nice_bonus)
        
        return {
            'score': final_score,
            'required_matched': list(required_match),
            'required_missing': list(required_missing),
            'preferred_matched': list(preferred_match),
            'extra_skills': list(candidate_set - required_set - preferred_set - nice_set),
        }
    
    def _match_experience(self, candidate_exp: int, min_exp: int, max_exp: int) -> Dict:
        """Match candidate experience against job requirements"""
        if min_exp == 0 and max_exp >= 15:
            return {'score': 100, 'status': 'any_experience', 'note': 'Open to all levels'}
        
        if candidate_exp >= min_exp and candidate_exp <= max_exp:
            return {'score': 100, 'status': 'exact_match', 'note': 'Perfect experience match'}
        
        if candidate_exp > max_exp:
            over = candidate_exp - max_exp
            if over <= 2:
                return {'score': 90, 'status': 'slightly_over', 'note': 'Slightly overqualified'}
            elif over <= 5:
                return {'score': 75, 'status': 'overqualified', 'note': 'May be overqualified'}
            else:
                return {'score': 50, 'status': 'very_overqualified', 'note': 'Significantly overqualified'}
        
        if candidate_exp < min_exp:
            under = min_exp - candidate_exp
            if under <= 1:
                return {'score': 80, 'status': 'close', 'note': 'Close to minimum requirement'}
            elif under <= 2:
                return {'score': 60, 'status': 'slightly_under', 'note': 'Below minimum but trainable'}
            else:
                return {'score': 30, 'status': 'underqualified', 'note': 'Significantly below requirement'}
        
        return {'score': 50, 'status': 'unknown', 'note': 'Unable to assess'}
    
    def _match_education(self, candidate_edu: List, required: str, preferred_fields: List[str]) -> Dict:
        """Match candidate education against requirements"""
        if not required:
            return {'score': 100, 'status': 'no_requirement', 'note': 'No education requirement'}
        
        required_lower = required.lower()
        
        # Education level hierarchy
        levels = {
            'high school': 1,
            'associate': 2,
            'bachelor': 3,
            'master': 4,
            'phd': 5,
            'doctorate': 5
        }
        
        # Determine required level
        required_level = 0
        for level, rank in levels.items():
            if level in required_lower:
                required_level = rank
                break
        
        # Find candidate's highest education
        candidate_level = 0
        candidate_field = ''
        
        if isinstance(candidate_edu, list):
            for edu in candidate_edu:
                if isinstance(edu, dict):
                    degree = edu.get('degree', '').lower()
                    field = edu.get('field', '').lower()
                elif isinstance(edu, str):
                    degree = edu.lower()
                    field = ''
                else:
                    continue
                
                for level, rank in levels.items():
                    if level in degree and rank > candidate_level:
                        candidate_level = rank
                        candidate_field = field
        
        # Calculate score
        if candidate_level >= required_level:
            base_score = 80
        elif candidate_level == required_level - 1:
            base_score = 60
        else:
            base_score = 30
        
        # Field bonus
        field_bonus = 0
        if preferred_fields:
            for pref in preferred_fields:
                if pref.lower() in candidate_field:
                    field_bonus = 20
                    break
        
        return {
            'score': min(100, base_score + field_bonus),
            'candidate_level': candidate_level,
            'required_level': required_level,
            'field_match': field_bonus > 0,
            'note': f"{'Meets' if candidate_level >= required_level else 'Below'} education requirement"
        }
    
    def _match_location(self, candidate_loc: str, job_loc: str, remote_friendly: bool) -> Dict:
        """Match candidate location with job location"""
        if remote_friendly:
            return {'score': 100, 'status': 'remote', 'note': 'Remote work available'}
        
        if not job_loc:
            return {'score': 100, 'status': 'any_location', 'note': 'No location requirement'}
        
        if not candidate_loc:
            return {'score': 50, 'status': 'unknown', 'note': 'Candidate location unknown'}
        
        candidate_lower = candidate_loc.lower()
        job_lower = job_loc.lower()
        
        # Direct match
        if job_lower in candidate_lower or candidate_lower in job_lower:
            return {'score': 100, 'status': 'match', 'note': 'Location matches'}
        
        # Same country/region check (simplified)
        candidate_parts = set(candidate_lower.replace(',', ' ').split())
        job_parts = set(job_lower.replace(',', ' ').split())
        
        if candidate_parts & job_parts:
            return {'score': 75, 'status': 'same_region', 'note': 'Same general area'}
        
        return {'score': 30, 'status': 'different', 'note': 'Different location - relocation required'}
    
    def _match_culture(self, summary: str, work_history: List, 
                       culture_keywords: List[str], company_size: str) -> Dict:
        """Assess culture/soft skills fit"""
        score = 50  # Base score
        matches = []
        
        text = (summary or '').lower()
        
        # Add work history descriptions
        for job in (work_history or []):
            if isinstance(job, dict):
                text += ' ' + (job.get('description', '') or '').lower()
        
        # Check culture keywords
        for keyword in culture_keywords:
            if keyword.lower() in text:
                score += 10
                matches.append(keyword)
        
        # Cap at 100
        score = min(100, score)
        
        return {
            'score': score,
            'matches': matches,
            'note': f'Found {len(matches)} culture fit indicators'
        }
    
    def _match_salary(self, candidate_salary: Dict, job_salary: Dict) -> Dict:
        """Match salary expectations"""
        cand_min = candidate_salary.get('min', 0)
        cand_max = candidate_salary.get('max', float('inf'))
        
        job_min = job_salary.get('min', 0)
        job_max = job_salary.get('max', float('inf'))
        
        # Check overlap
        if cand_min <= job_max and cand_max >= job_min:
            # There's overlap
            if cand_min <= job_min and cand_max <= job_max:
                return {'score': 100, 'note': 'Salary expectations align perfectly'}
            elif cand_min > job_max * 0.9:
                return {'score': 70, 'note': 'Salary expectations slightly above budget'}
            else:
                return {'score': 90, 'note': 'Salary within range'}
        else:
            if cand_min > job_max:
                gap_pct = (cand_min - job_max) / job_max * 100
                if gap_pct <= 10:
                    return {'score': 60, 'note': 'Slightly above budget'}
                elif gap_pct <= 25:
                    return {'score': 40, 'note': 'Significantly above budget'}
                else:
                    return {'score': 20, 'note': 'Way above budget'}
            else:
                return {'score': 80, 'note': 'Below expectations - candidate may be undervaluing'}
    
    def _match_tech_preference(self, preferred: List[str], job_tech: List[str]) -> Dict:
        """Match candidate's preferred technologies with job's stack"""
        if not preferred:
            return {'score': 75, 'note': 'No technology preferences specified'}
        
        pref_set = set(s.lower() for s in preferred)
        job_set = set(s.lower() for s in job_tech)
        
        overlap = pref_set & job_set
        
        if not job_set:
            return {'score': 75, 'note': 'Job tech stack not specified'}
        
        match_ratio = len(overlap) / len(pref_set)
        score = int(50 + match_ratio * 50)
        
        return {
            'score': score,
            'matching_tech': list(overlap),
            'note': f'{len(overlap)} of {len(pref_set)} preferred technologies available'
        }
    
    def _assess_career_fit(self, experience: int, current_title: str, 
                          job_title: str, seniority: str) -> Dict:
        """Assess career growth opportunity"""
        # Seniority levels
        seniority_levels = {
            'intern': 0, 'junior': 1, 'mid': 2, 'senior': 3,
            'lead': 4, 'principal': 5, 'director': 6, 'vp': 7
        }
        
        current_level = 2  # Default to mid-level
        for level, rank in seniority_levels.items():
            if level in (current_title or '').lower():
                current_level = rank
                break
        
        job_level = 2
        for level, rank in seniority_levels.items():
            if level in (job_title or '').lower() or level in (seniority or '').lower():
                job_level = rank
                break
        
        level_diff = job_level - current_level
        
        if level_diff >= 2:
            return {'score': 60, 'note': 'Significant step up - ambitious move'}
        elif level_diff == 1:
            return {'score': 95, 'note': 'Great career growth opportunity'}
        elif level_diff == 0:
            return {'score': 75, 'note': 'Lateral move - similar level'}
        elif level_diff == -1:
            return {'score': 50, 'note': 'Step down - consider carefully'}
        else:
            return {'score': 30, 'note': 'Significant step down'}
    
    def _match_work_style(self, preferences: Dict, job: Dict) -> Dict:
        """Match work style preferences"""
        score = 75  # Default
        notes = []
        
        # Remote preference
        if preferences.get('remote_preferred'):
            if job.get('remote_friendly'):
                score += 15
                notes.append('Remote work available')
            else:
                score -= 20
                notes.append('On-site required')
        
        # Flexible hours
        if preferences.get('flexible_hours'):
            if job.get('flexible_hours'):
                score += 10
                notes.append('Flexible hours')
        
        return {'score': min(100, score), 'notes': notes}
    
    def _match_company_preference(self, preferences: Dict, company: Dict) -> Dict:
        """Match company preferences"""
        score = 70
        
        # Company size preference
        pref_size = preferences.get('preferred_size', '')
        company_size = company.get('size', '')
        
        if pref_size and company_size:
            if pref_size.lower() == company_size.lower():
                score += 20
        
        # Industry preference
        pref_industries = preferences.get('preferred_industries', [])
        company_industry = company.get('industry', '')
        
        if pref_industries and company_industry:
            if company_industry.lower() in [i.lower() for i in pref_industries]:
                score += 10
        
        return {'score': min(100, score), 'note': 'Company preference assessment'}
    
    def _identify_strengths(self, scores: Dict, skills_result: Dict, exp_result: Dict) -> List[str]:
        """Identify candidate's strengths for this role"""
        strengths = []
        
        if scores.get('skills', 0) >= 80:
            matched = skills_result.get('required_matched', [])
            if matched:
                strengths.append(f"Strong skill match: {', '.join(matched[:3])}")
        
        if scores.get('experience', 0) >= 90:
            strengths.append("Experience level is ideal for this role")
        
        if scores.get('education', 0) >= 80:
            strengths.append("Education background aligns well")
        
        extra = skills_result.get('extra_skills', [])
        if len(extra) >= 3:
            strengths.append(f"Brings additional valuable skills: {', '.join(extra[:3])}")
        
        return strengths
    
    def _identify_gaps(self, skills_result: Dict, exp_result: Dict, edu_result: Dict) -> List[str]:
        """Identify gaps that need addressing"""
        gaps = []
        
        missing = skills_result.get('required_missing', [])
        if missing:
            gaps.append(f"Missing required skills: {', '.join(missing)}")
        
        exp_status = exp_result.get('status', '')
        if 'under' in exp_status:
            gaps.append(exp_result.get('note', 'Below experience requirement'))
        
        if edu_result.get('score', 100) < 60:
            gaps.append(edu_result.get('note', 'Education gap'))
        
        return gaps
    
    def _extract_job_highlights(self, job: Dict, scores: Dict) -> List[str]:
        """Extract positive highlights about the job for candidate"""
        highlights = []
        
        if job.get('remote_friendly'):
            highlights.append("🏠 Remote work available")
        
        if job.get('flexible_hours'):
            highlights.append("⏰ Flexible working hours")
        
        if scores.get('career_growth', {}).get('score', 0) >= 80:
            highlights.append("📈 Great career growth opportunity")
        
        if job.get('benefits'):
            highlights.append(f"🎁 Benefits: {', '.join(job['benefits'][:3])}")
        
        return highlights
    
    def _extract_job_concerns(self, scores: Dict) -> List[str]:
        """Extract potential concerns about job fit"""
        concerns = []
        
        salary = scores.get('salary', {})
        if salary.get('score', 75) < 50:
            concerns.append(f"💰 {salary.get('note', 'Salary mismatch')}")
        
        career = scores.get('career_growth', {})
        if career.get('score', 75) < 50:
            concerns.append(f"📉 {career.get('note', 'May not align with career goals')}")
        
        return concerns


# Singleton
_matching_engine = None

def get_matching_engine() -> JobMatchingEngine:
    global _matching_engine
    if _matching_engine is None:
        _matching_engine = JobMatchingEngine()
    return _matching_engine
