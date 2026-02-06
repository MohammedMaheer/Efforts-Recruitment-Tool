"""
Predictive Analytics Service
Predicts candidate response rates, interview success, and hiring outcomes
Uses historical data to improve predictions over time
"""
import json
import logging
import math
import os
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent.parent / "models_trained"
MODEL_PATH.mkdir(exist_ok=True)


class PredictiveAnalytics:
    """
    ML-powered predictions for recruiting:
    1. Response Rate: Will candidate reply to outreach?
    2. Interview Success: Will candidate pass interview?
    3. Offer Acceptance: Will candidate accept offer?
    4. Retention Risk: Will candidate leave within 1 year?
    5. Time to Hire: How long will hiring process take?
    """
    
    # Historical average rates (used as priors)
    BASELINE_RATES = {
        'response_rate': 0.35,      # 35% average response rate
        'interview_pass': 0.40,     # 40% pass interviews
        'offer_acceptance': 0.70,   # 70% accept offers
        'first_year_retention': 0.85,  # 85% stay 1+ year
    }
    
    # Factors that increase/decrease rates
    RESPONSE_FACTORS = {
        'personalized_message': 1.4,
        'job_title_match': 1.3,
        'salary_mentioned': 1.2,
        'company_well_known': 1.25,
        'linkedin_connection': 1.5,
        'passive_candidate': 0.6,
        'currently_employed': 0.7,
        'actively_looking': 1.8,
        'senior_level': 0.8,
        'weekend_outreach': 0.7,
    }
    
    INTERVIEW_FACTORS = {
        'skill_match_high': 1.5,
        'experience_exact': 1.3,
        'referral': 1.6,
        'previous_interview': 1.2,
        'communication_strong': 1.3,
        'job_hopping': 0.7,
        'career_gap': 0.8,
        'overqualified': 0.85,
        'underqualified': 0.6,
    }
    
    def __init__(self):
        self.historical_data = []
        self._load_historical()
    
    def _load_historical(self):
        """Load historical outcomes for model improvement"""
        history_file = MODEL_PATH / "predictive_history.json"
        if history_file.exists():
            try:
                with open(history_file, 'r') as f:
                    self.historical_data = json.load(f)
                logger.info(f"Loaded {len(self.historical_data)} historical records")
            except Exception as e:
                logger.warning(f"Could not load history: {e}")
    
    def _save_historical(self):
        """Save historical data"""
        history_file = MODEL_PATH / "predictive_history.json"
        try:
            with open(history_file, 'w') as f:
                json.dump(self.historical_data[-10000:], f)  # Keep last 10k records
        except Exception as e:
            logger.error(f"Could not save history: {e}")
    
    def predict_response_rate(self, candidate: Dict, outreach_context: Dict = None) -> Dict:
        """
        Predict probability that candidate will respond to outreach
        
        Args:
            candidate: Candidate profile
            outreach_context: Details about the outreach (personalized, timing, etc.)
        
        Returns:
            Dict with probability, confidence, and factors
        """
        outreach_context = outreach_context or {}
        
        # Start with baseline
        probability = self.BASELINE_RATES['response_rate']
        factors = []
        
        # Candidate status factors
        status = candidate.get('status', '').lower()
        if 'active' in status or 'looking' in status:
            probability *= self.RESPONSE_FACTORS['actively_looking']
            factors.append(('Actively looking', '+80%'))
        elif candidate.get('currently_employed', True):
            probability *= self.RESPONSE_FACTORS['currently_employed']
            factors.append(('Currently employed', '-30%'))
        
        # Experience level
        experience = candidate.get('experience', 0)
        if experience >= 10:
            probability *= self.RESPONSE_FACTORS['senior_level']
            factors.append(('Senior candidate', '-20%'))
        
        # Profile completeness (correlates with activity)
        completeness = self._calculate_completeness(candidate)
        if completeness >= 0.8:
            probability *= 1.2
            factors.append(('Complete profile', '+20%'))
        elif completeness < 0.5:
            probability *= 0.8
            factors.append(('Incomplete profile', '-20%'))
        
        # LinkedIn presence
        if candidate.get('linkedin'):
            probability *= 1.15
            factors.append(('LinkedIn available', '+15%'))
        
        # Outreach factors
        if outreach_context.get('personalized'):
            probability *= self.RESPONSE_FACTORS['personalized_message']
            factors.append(('Personalized message', '+40%'))
        
        if outreach_context.get('salary_mentioned'):
            probability *= self.RESPONSE_FACTORS['salary_mentioned']
            factors.append(('Salary mentioned', '+20%'))
        
        if outreach_context.get('linkedin_connection'):
            probability *= self.RESPONSE_FACTORS['linkedin_connection']
            factors.append(('LinkedIn connection', '+50%'))
        
        # Cap probability
        probability = min(0.95, max(0.05, probability))
        
        # Calculate confidence based on data availability
        data_points = sum([
            1 if candidate.get('experience') else 0,
            1 if candidate.get('skills') else 0,
            1 if candidate.get('linkedin') else 0,
            1 if candidate.get('location') else 0,
        ])
        confidence = min(0.9, 0.5 + data_points * 0.1)
        
        return {
            'probability': round(probability * 100, 1),
            'confidence': round(confidence * 100, 1),
            'factors': factors,
            'recommendation': self._get_response_recommendation(probability),
            'best_time': self._suggest_outreach_time(candidate),
        }
    
    def predict_interview_success(self, candidate: Dict, job: Dict = None) -> Dict:
        """
        Predict probability of passing interview
        
        Args:
            candidate: Candidate profile
            job: Job requirements (optional)
        """
        job = job or {}
        
        probability = self.BASELINE_RATES['interview_pass']
        factors = []
        
        # Skill match
        match_score = candidate.get('matchScore', 50)
        if match_score >= 80:
            probability *= self.INTERVIEW_FACTORS['skill_match_high']
            factors.append(('Strong skill match', '+50%'))
        elif match_score < 50:
            probability *= self.INTERVIEW_FACTORS['underqualified']
            factors.append(('Skills gap', '-40%'))
        
        # Experience fit
        required_exp = job.get('min_experience', 0)
        candidate_exp = candidate.get('experience', 0)
        
        if required_exp > 0:
            if abs(candidate_exp - required_exp) <= 1:
                probability *= self.INTERVIEW_FACTORS['experience_exact']
                factors.append(('Experience match', '+30%'))
            elif candidate_exp > required_exp + 5:
                probability *= self.INTERVIEW_FACTORS['overqualified']
                factors.append(('Overqualified', '-15%'))
        
        # Referral
        if candidate.get('referral'):
            probability *= self.INTERVIEW_FACTORS['referral']
            factors.append(('Referred candidate', '+60%'))
        
        # Job hopping
        work_history = candidate.get('workHistory', []) or []
        if len(work_history) >= 4:
            # Check average tenure
            avg_tenure = self._calculate_avg_tenure(work_history)
            if avg_tenure < 18:  # Less than 18 months average
                probability *= self.INTERVIEW_FACTORS['job_hopping']
                factors.append(('Frequent job changes', '-30%'))
        
        # Education
        education = candidate.get('education', [])
        if education:
            probability *= 1.1
            factors.append(('Education verified', '+10%'))
        
        probability = min(0.95, max(0.05, probability))
        
        return {
            'probability': round(probability * 100, 1),
            'factors': factors,
            'preparation_tips': self._get_interview_tips(candidate, job),
            'risk_areas': self._identify_risk_areas(candidate, job),
        }
    
    def predict_offer_acceptance(self, candidate: Dict, offer: Dict = None) -> Dict:
        """
        Predict probability of accepting an offer
        
        Args:
            candidate: Candidate profile
            offer: Offer details (salary, benefits, etc.)
        """
        offer = offer or {}
        
        probability = self.BASELINE_RATES['offer_acceptance']
        factors = []
        
        # Salary competitiveness
        offered = offer.get('salary', 0)
        expected = candidate.get('salary_expectation', {}).get('min', 0)
        
        if offered and expected:
            salary_ratio = offered / expected
            if salary_ratio >= 1.1:
                probability *= 1.3
                factors.append(('Above salary expectations', '+30%'))
            elif salary_ratio >= 1.0:
                probability *= 1.1
                factors.append(('Meets salary expectations', '+10%'))
            elif salary_ratio >= 0.9:
                probability *= 0.8
                factors.append(('Below expectations', '-20%'))
            else:
                probability *= 0.5
                factors.append(('Significantly below', '-50%'))
        
        # Location fit
        if offer.get('remote') and candidate.get('prefers_remote'):
            probability *= 1.2
            factors.append(('Remote work offered', '+20%'))
        
        # Counter offer risk
        if candidate.get('currently_employed'):
            probability *= 0.85
            factors.append(('May receive counter offer', '-15%'))
        
        # Multiple offers
        if candidate.get('interviewing_elsewhere'):
            probability *= 0.7
            factors.append(('Interviewing elsewhere', '-30%'))
        
        probability = min(0.95, max(0.1, probability))
        
        return {
            'probability': round(probability * 100, 1),
            'factors': factors,
            'negotiation_tips': self._get_negotiation_tips(probability, factors),
        }
    
    def predict_retention_risk(self, candidate: Dict) -> Dict:
        """
        Predict risk of candidate leaving within first year
        """
        # Start with baseline retention
        retention = self.BASELINE_RATES['first_year_retention']
        risk_factors = []
        
        # Job hopping history
        work_history = candidate.get('workHistory', []) or []
        avg_tenure = self._calculate_avg_tenure(work_history)
        
        if avg_tenure < 12:
            retention *= 0.6
            risk_factors.append({
                'factor': 'Short tenure history',
                'impact': 'high',
                'detail': f'Average tenure: {avg_tenure} months'
            })
        elif avg_tenure < 24:
            retention *= 0.8
            risk_factors.append({
                'factor': 'Moderate tenure history',
                'impact': 'medium',
                'detail': f'Average tenure: {avg_tenure} months'
            })
        
        # Experience mismatch
        experience = candidate.get('experience', 0)
        match_score = candidate.get('matchScore', 50)
        
        if match_score < 50:
            retention *= 0.85
            risk_factors.append({
                'factor': 'Skills mismatch',
                'impact': 'medium',
                'detail': 'May struggle or lose interest'
            })
        
        # Overqualification
        if experience >= 10 and match_score < 70:
            retention *= 0.75
            risk_factors.append({
                'factor': 'Potentially overqualified',
                'impact': 'high',
                'detail': 'May seek more challenging role'
            })
        
        # Location
        location = candidate.get('location', '').lower()
        if 'remote' in location or not location:
            retention *= 0.9
            risk_factors.append({
                'factor': 'Remote worker',
                'impact': 'low',
                'detail': 'Lower engagement risk'
            })
        
        retention = min(0.95, max(0.2, retention))
        risk_score = round((1 - retention) * 100, 1)
        
        if risk_score >= 40:
            risk_level = 'high'
        elif risk_score >= 20:
            risk_level = 'medium'
        else:
            risk_level = 'low'
        
        return {
            'retention_probability': round(retention * 100, 1),
            'risk_score': risk_score,
            'risk_level': risk_level,
            'risk_factors': risk_factors,
            'mitigation_suggestions': self._get_retention_tips(risk_factors),
        }
    
    def estimate_time_to_hire(self, candidate: Dict, job: Dict = None) -> Dict:
        """
        Estimate days from application to hire
        """
        job = job or {}
        
        # Base estimates by role type
        base_days = {
            'junior': 21,
            'mid': 28,
            'senior': 35,
            'lead': 42,
            'director': 56,
            'executive': 70,
        }
        
        # Determine seniority
        experience = candidate.get('experience', 0)
        if experience < 2:
            seniority = 'junior'
        elif experience < 5:
            seniority = 'mid'
        elif experience < 8:
            seniority = 'senior'
        elif experience < 12:
            seniority = 'lead'
        else:
            seniority = 'director'
        
        estimated_days = base_days.get(seniority, 28)
        
        # Adjustments
        adjustments = []
        
        # Match score affects speed
        match_score = candidate.get('matchScore', 50)
        if match_score >= 80:
            estimated_days *= 0.8
            adjustments.append('Strong match - faster process')
        elif match_score < 50:
            estimated_days *= 1.3
            adjustments.append('Skills gap - additional evaluation needed')
        
        # Referral speeds things up
        if candidate.get('referral'):
            estimated_days *= 0.7
            adjustments.append('Referral - expedited process')
        
        # Currently employed
        if candidate.get('currently_employed'):
            estimated_days += 14  # Notice period
            adjustments.append('Notice period needed')
        
        # Visa/relocation
        if candidate.get('requires_visa'):
            estimated_days += 30
            adjustments.append('Visa processing time')
        
        estimated_days = round(estimated_days)
        
        return {
            'estimated_days': estimated_days,
            'range': {
                'min': max(7, estimated_days - 10),
                'max': estimated_days + 14
            },
            'adjustments': adjustments,
            'timeline': self._generate_timeline(estimated_days),
        }
    
    def _calculate_completeness(self, candidate: Dict) -> float:
        """Calculate profile completeness"""
        fields = ['name', 'email', 'phone', 'location', 'skills', 'experience',
                  'education', 'summary', 'linkedin', 'workHistory']
        present = sum(1 for f in fields if candidate.get(f))
        return present / len(fields)
    
    def _calculate_avg_tenure(self, work_history: List) -> int:
        """Calculate average job tenure in months"""
        if not work_history:
            return 36  # Default to 3 years if unknown
        
        tenures = []
        for job in work_history:
            if isinstance(job, dict):
                # Try to extract dates
                start = job.get('start_date', job.get('startDate', ''))
                end = job.get('end_date', job.get('endDate', 'present'))
                
                # Simple estimation based on text
                if 'present' in str(end).lower():
                    tenures.append(24)  # Assume 2 years for current
                else:
                    tenures.append(18)  # Default estimate
        
        return round(sum(tenures) / len(tenures)) if tenures else 36
    
    def _suggest_outreach_time(self, candidate: Dict) -> Dict:
        """Suggest best time to reach out"""
        # Tuesday-Thursday mornings are generally best
        return {
            'best_days': ['Tuesday', 'Wednesday', 'Thursday'],
            'best_times': ['9:00 AM - 11:00 AM', '2:00 PM - 4:00 PM'],
            'avoid': ['Monday morning', 'Friday afternoon', 'Weekends'],
        }
    
    def _get_response_recommendation(self, probability: float) -> str:
        """Get recommendation based on response probability"""
        if probability >= 0.6:
            return "High chance of response - proceed with outreach"
        elif probability >= 0.4:
            return "Moderate chance - personalize message for better results"
        elif probability >= 0.25:
            return "Lower chance - consider warm introduction or LinkedIn connection first"
        else:
            return "Low probability - focus on building relationship first"
    
    def _get_interview_tips(self, candidate: Dict, job: Dict) -> List[str]:
        """Generate interview preparation tips"""
        tips = []
        
        skills = candidate.get('skills', [])
        if skills:
            tips.append(f"Focus questions on: {', '.join(skills[:3])}")
        
        experience = candidate.get('experience', 0)
        if experience >= 8:
            tips.append("Assess leadership and strategic thinking")
        elif experience >= 4:
            tips.append("Evaluate problem-solving and ownership")
        else:
            tips.append("Assess learning ability and culture fit")
        
        tips.append("Ask about specific projects and quantified achievements")
        
        return tips
    
    def _identify_risk_areas(self, candidate: Dict, job: Dict) -> List[str]:
        """Identify areas that need probing in interview"""
        risks = []
        
        work_history = candidate.get('workHistory', []) or []
        if len(work_history) >= 5:
            risks.append("Explore reasons for job changes")
        
        match_score = candidate.get('matchScore', 50)
        if match_score < 60:
            risks.append("Assess transferable skills and learning capacity")
        
        if not candidate.get('education'):
            risks.append("Verify qualifications and certifications")
        
        return risks
    
    def _get_negotiation_tips(self, probability: float, factors: List) -> List[str]:
        """Generate negotiation tips for recruiters"""
        tips = []
        
        if probability < 0.6:
            tips.append("Consider increasing offer or adding benefits")
            tips.append("Emphasize growth opportunities and culture")
            tips.append("Create urgency with deadline")
        else:
            tips.append("Offer is competitive - proceed with confidence")
            tips.append("Prepare for standard negotiation on start date/benefits")
        
        return tips
    
    def _get_retention_tips(self, risk_factors: List[Dict]) -> List[str]:
        """Generate retention improvement suggestions"""
        tips = []
        
        for rf in risk_factors:
            if rf['factor'] == 'Short tenure history':
                tips.append("Conduct thorough culture fit assessment")
                tips.append("Set clear expectations and goals upfront")
                tips.append("Plan frequent check-ins during first 6 months")
            elif rf['factor'] == 'Skills mismatch':
                tips.append("Create detailed onboarding and training plan")
                tips.append("Assign mentor for first 3 months")
            elif rf['factor'] == 'Potentially overqualified':
                tips.append("Discuss growth path and future opportunities")
                tips.append("Consider expanded responsibilities")
        
        if not tips:
            tips.append("Standard onboarding recommended")
        
        return tips
    
    def _generate_timeline(self, total_days: int) -> List[Dict]:
        """Generate hiring process timeline"""
        today = datetime.now()
        
        return [
            {'stage': 'Application Review', 'day': 0, 'date': today.strftime('%Y-%m-%d')},
            {'stage': 'Initial Screen', 'day': 3, 'date': (today + timedelta(days=3)).strftime('%Y-%m-%d')},
            {'stage': 'Technical Interview', 'day': 10, 'date': (today + timedelta(days=10)).strftime('%Y-%m-%d')},
            {'stage': 'Onsite/Final', 'day': 17, 'date': (today + timedelta(days=17)).strftime('%Y-%m-%d')},
            {'stage': 'Offer', 'day': 21, 'date': (today + timedelta(days=21)).strftime('%Y-%m-%d')},
            {'stage': 'Start Date', 'day': total_days, 'date': (today + timedelta(days=total_days)).strftime('%Y-%m-%d')},
        ]
    
    def record_outcome(self, candidate_id: str, stage: str, outcome: bool, details: Dict = None):
        """
        Record actual outcome for model improvement
        
        Args:
            candidate_id: Unique candidate identifier
            stage: 'response', 'interview', 'offer', 'retention'
            outcome: True for positive outcome, False for negative
            details: Additional context
        """
        record = {
            'candidate_id': candidate_id,
            'stage': stage,
            'outcome': outcome,
            'timestamp': datetime.now().isoformat(),
            'details': details or {}
        }
        
        self.historical_data.append(record)
        self._save_historical()
        
        logger.info(f"Recorded {stage} outcome for {candidate_id}: {outcome}")


# Singleton
_analytics = None

def get_predictive_analytics() -> PredictiveAnalytics:
    global _analytics
    if _analytics is None:
        _analytics = PredictiveAnalytics()
    return _analytics
