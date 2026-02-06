"""
Resume Quality & Red Flag Detection Service
Analyzes resumes for potential issues and red flags
Provides detailed quality assessment with actionable insights
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json

logger = logging.getLogger(__name__)


class ResumeQualityAnalyzer:
    """
    Comprehensive resume quality analysis:
    1. Red Flag Detection (gaps, job hopping, inconsistencies)
    2. Quality Scoring (formatting, content depth, achievements)
    3. Credibility Assessment (verifiable claims, realistic experience)
    4. ATS Optimization Score
    """
    
    # Red flag categories and severity
    RED_FLAGS = {
        'employment_gap': {'weight': 8, 'category': 'experience'},
        'job_hopping': {'weight': 7, 'category': 'experience'},
        'title_inflation': {'weight': 6, 'category': 'credibility'},
        'vague_descriptions': {'weight': 5, 'category': 'content'},
        'no_quantified_achievements': {'weight': 5, 'category': 'content'},
        'missing_dates': {'weight': 6, 'category': 'credibility'},
        'inconsistent_timeline': {'weight': 8, 'category': 'credibility'},
        'buzzword_heavy': {'weight': 4, 'category': 'content'},
        'no_progression': {'weight': 5, 'category': 'experience'},
        'education_mismatch': {'weight': 4, 'category': 'credibility'},
        'contact_info_issues': {'weight': 3, 'category': 'formatting'},
        'poor_formatting': {'weight': 3, 'category': 'formatting'},
        'typos_errors': {'weight': 4, 'category': 'content'},
    }
    
    # Buzzword and filler lists
    BUZZWORDS = [
        'synergy', 'leverage', 'paradigm', 'disrupt', 'innovative',
        'passionate', 'rockstar', 'ninja', 'guru', 'wizard',
        'dynamic', 'proactive', 'results-driven', 'team player',
        'think outside the box', 'go-getter', 'self-starter',
        'detail-oriented', 'fast-paced', 'hit the ground running'
    ]
    
    # Action verbs that indicate real achievements
    STRONG_VERBS = [
        'achieved', 'increased', 'decreased', 'reduced', 'saved',
        'generated', 'delivered', 'launched', 'built', 'created',
        'designed', 'implemented', 'developed', 'led', 'managed',
        'negotiated', 'streamlined', 'optimized', 'automated',
        'transformed', 'pioneered', 'established', 'executed'
    ]
    
    # Title progression expectations
    TITLE_PROGRESSION = {
        'intern': 0,
        'junior': 1,
        'associate': 2,
        'analyst': 2,
        'specialist': 3,
        'engineer': 3,
        'senior': 4,
        'lead': 5,
        'principal': 6,
        'manager': 5,
        'director': 7,
        'vp': 8,
        'vice president': 8,
        'head': 7,
        'chief': 9,
        'cto': 9,
        'ceo': 10,
    }
    
    def __init__(self):
        self.analysis_cache = {}
    
    def analyze_resume(self, candidate: Dict, resume_text: str = None) -> Dict:
        """
        Perform comprehensive resume quality analysis
        
        Args:
            candidate: Candidate profile with parsed data
            resume_text: Raw resume text (optional, for deeper analysis)
        
        Returns:
            Detailed quality report with scores and recommendations
        """
        resume_text = resume_text or candidate.get('summary', '')
        
        red_flags = []
        quality_scores = {}
        
        # 1. Employment Gap Analysis
        gap_result = self._analyze_employment_gaps(candidate.get('workHistory', []))
        if gap_result['flags']:
            red_flags.extend(gap_result['flags'])
        quality_scores['continuity'] = gap_result['score']
        
        # 2. Job Hopping Analysis
        hopping_result = self._analyze_job_hopping(candidate.get('workHistory', []))
        if hopping_result['flags']:
            red_flags.extend(hopping_result['flags'])
        quality_scores['stability'] = hopping_result['score']
        
        # 3. Career Progression Analysis
        progression_result = self._analyze_progression(candidate.get('workHistory', []))
        if progression_result['flags']:
            red_flags.extend(progression_result['flags'])
        quality_scores['progression'] = progression_result['score']
        
        # 4. Content Quality Analysis
        content_result = self._analyze_content_quality(
            resume_text,
            candidate.get('summary', ''),
            candidate.get('workHistory', [])
        )
        if content_result['flags']:
            red_flags.extend(content_result['flags'])
        quality_scores['content'] = content_result['score']
        
        # 5. Credibility Analysis
        credibility_result = self._analyze_credibility(candidate)
        if credibility_result['flags']:
            red_flags.extend(credibility_result['flags'])
        quality_scores['credibility'] = credibility_result['score']
        
        # 6. Completeness Analysis
        completeness_result = self._analyze_completeness(candidate)
        quality_scores['completeness'] = completeness_result['score']
        
        # 7. ATS Compatibility
        ats_result = self._analyze_ats_compatibility(resume_text, candidate)
        quality_scores['ats_score'] = ats_result['score']
        
        # Calculate overall score
        weights = {
            'continuity': 15,
            'stability': 15,
            'progression': 15,
            'content': 20,
            'credibility': 20,
            'completeness': 10,
            'ats_score': 5
        }
        
        overall_score = sum(
            quality_scores[k] * weights[k] / 100 
            for k in quality_scores
        )
        
        # Severity classification
        critical_flags = [f for f in red_flags if f.get('severity') == 'critical']
        warning_flags = [f for f in red_flags if f.get('severity') == 'warning']
        info_flags = [f for f in red_flags if f.get('severity') == 'info']
        
        # Risk level
        if critical_flags:
            risk_level = 'high'
        elif len(warning_flags) >= 3:
            risk_level = 'medium'
        elif warning_flags:
            risk_level = 'low'
        else:
            risk_level = 'none'
        
        return {
            'overall_score': round(overall_score, 1),
            'risk_level': risk_level,
            'quality_breakdown': {k: round(v, 1) for k, v in quality_scores.items()},
            'red_flags': {
                'critical': critical_flags,
                'warning': warning_flags,
                'info': info_flags,
                'total_count': len(red_flags)
            },
            'strengths': self._identify_strengths(quality_scores, content_result),
            'improvements': self._suggest_improvements(red_flags, quality_scores),
            'verification_needed': self._get_verification_items(red_flags),
            'interview_questions': self._generate_probe_questions(red_flags),
        }
    
    def _analyze_employment_gaps(self, work_history: List) -> Dict:
        """Detect employment gaps"""
        flags = []
        gaps = []
        
        if not work_history or len(work_history) < 2:
            return {'score': 100, 'flags': [], 'gaps': []}
        
        # Sort by date (most recent first)
        jobs = []
        for job in work_history:
            if isinstance(job, dict):
                jobs.append(job)
        
        if len(jobs) < 2:
            return {'score': 100, 'flags': [], 'gaps': []}
        
        # Simplified gap detection (without actual date parsing)
        # In production, you'd parse actual dates
        
        # Check if any job has "present" or current
        has_current = any(
            'present' in str(job.get('end_date', job.get('endDate', ''))).lower()
            for job in jobs
        )
        
        if not has_current and len(jobs) >= 1:
            # Potential current gap
            flags.append({
                'type': 'employment_gap',
                'severity': 'warning',
                'message': 'Currently unemployed or missing current position',
                'details': 'No current employment indicated',
                'recommendation': 'Ask about current employment status'
            })
        
        # Calculate score (deduct for each gap)
        gap_penalty = len(flags) * 15
        score = max(30, 100 - gap_penalty)
        
        return {
            'score': score,
            'flags': flags,
            'gaps': gaps
        }
    
    def _analyze_job_hopping(self, work_history: List) -> Dict:
        """Detect job hopping patterns"""
        flags = []
        
        if not work_history:
            return {'score': 100, 'flags': []}
        
        jobs = [j for j in work_history if isinstance(j, dict)]
        job_count = len(jobs)
        
        # Calculate average tenure (estimate)
        # In production, calculate from actual dates
        
        if job_count >= 5:
            flags.append({
                'type': 'job_hopping',
                'severity': 'warning',
                'message': f'Frequent job changes ({job_count} positions)',
                'details': 'May indicate difficulty with commitment or fit',
                'recommendation': 'Explore reasons for each transition'
            })
        elif job_count >= 4:
            flags.append({
                'type': 'job_hopping',
                'severity': 'info',
                'message': f'Multiple job changes ({job_count} positions)',
                'details': 'Pattern worth exploring',
                'recommendation': 'Understand career motivations'
            })
        
        # Score based on job count vs experience
        if job_count <= 2:
            score = 100
        elif job_count == 3:
            score = 90
        elif job_count == 4:
            score = 75
        elif job_count == 5:
            score = 60
        else:
            score = max(30, 100 - (job_count * 10))
        
        return {'score': score, 'flags': flags}
    
    def _analyze_progression(self, work_history: List) -> Dict:
        """Analyze career progression"""
        flags = []
        
        if not work_history or len(work_history) < 2:
            return {'score': 80, 'flags': []}  # Can't assess
        
        jobs = [j for j in work_history if isinstance(j, dict)]
        
        if len(jobs) < 2:
            return {'score': 80, 'flags': []}
        
        # Extract titles and their levels
        titles = []
        for job in jobs:
            title = (job.get('title', job.get('position', '')) or '').lower()
            level = 0
            for keyword, lvl in self.TITLE_PROGRESSION.items():
                if keyword in title:
                    level = max(level, lvl)
            titles.append({'title': title, 'level': level})
        
        # Check for progression (should generally go up over time)
        if len(titles) >= 2:
            # Reverse to chronological order
            titles_chrono = list(reversed(titles))
            
            # Check if latest is lower than previous highest
            max_level = max(t['level'] for t in titles_chrono[:-1]) if len(titles_chrono) > 1 else 0
            current_level = titles_chrono[-1]['level']
            
            if current_level < max_level - 1:
                flags.append({
                    'type': 'no_progression',
                    'severity': 'warning',
                    'message': 'Potential step back in career',
                    'details': 'Current role appears lower than previous positions',
                    'recommendation': 'Explore reasons for career change'
                })
            elif current_level < max_level:
                flags.append({
                    'type': 'no_progression',
                    'severity': 'info',
                    'message': 'Lateral or slight step back detected',
                    'details': 'May be intentional career pivot',
                    'recommendation': 'Discuss career goals and motivations'
                })
        
        # Score
        if not flags:
            score = 100
        elif any(f['severity'] == 'warning' for f in flags):
            score = 65
        else:
            score = 80
        
        return {'score': score, 'flags': flags}
    
    def _analyze_content_quality(self, resume_text: str, summary: str, work_history: List) -> Dict:
        """Analyze content quality"""
        flags = []
        text = (resume_text + ' ' + summary).lower()
        
        # Check for quantified achievements
        number_pattern = r'\d+[%$kKmMx]|\$\d+|\d+\s*(percent|customers|users|team|projects)'
        achievements = re.findall(number_pattern, text)
        
        if len(achievements) < 2:
            flags.append({
                'type': 'no_quantified_achievements',
                'severity': 'warning',
                'message': 'Lacks quantified achievements',
                'details': 'Strong resumes include measurable results',
                'recommendation': 'Ask for specific metrics and outcomes'
            })
        
        # Check for buzzwords
        buzzword_count = sum(1 for bw in self.BUZZWORDS if bw in text)
        if buzzword_count >= 5:
            flags.append({
                'type': 'buzzword_heavy',
                'severity': 'info',
                'message': f'Contains {buzzword_count} common buzzwords',
                'details': 'May be padding without substance',
                'recommendation': 'Probe for specific examples'
            })
        
        # Check for strong action verbs
        strong_verb_count = sum(1 for v in self.STRONG_VERBS if v in text)
        
        # Check for vague descriptions
        vague_patterns = ['responsible for', 'duties included', 'worked on', 'helped with']
        vague_count = sum(1 for p in vague_patterns if p in text)
        
        if vague_count >= 3:
            flags.append({
                'type': 'vague_descriptions',
                'severity': 'warning',
                'message': 'Uses vague, responsibility-focused language',
                'details': 'Lacks impact-focused descriptions',
                'recommendation': 'Ask about specific contributions and results'
            })
        
        # Calculate score
        score = 70  # Base
        score += min(15, len(achievements) * 5)  # Up to 15 for achievements
        score += min(10, strong_verb_count * 2)  # Up to 10 for strong verbs
        score -= min(15, buzzword_count * 3)  # Deduct for buzzwords
        score -= min(15, vague_count * 5)  # Deduct for vague language
        
        return {
            'score': max(30, min(100, score)),
            'flags': flags,
            'metrics_found': len(achievements),
            'strong_verbs': strong_verb_count
        }
    
    def _analyze_credibility(self, candidate: Dict) -> Dict:
        """Analyze credibility and consistency"""
        flags = []
        
        # Check for missing dates
        work_history = candidate.get('workHistory', []) or []
        jobs_without_dates = 0
        
        for job in work_history:
            if isinstance(job, dict):
                has_dates = bool(
                    job.get('start_date') or 
                    job.get('startDate') or
                    job.get('end_date') or
                    job.get('endDate')
                )
                if not has_dates:
                    jobs_without_dates += 1
        
        if jobs_without_dates >= 2:
            flags.append({
                'type': 'missing_dates',
                'severity': 'warning',
                'message': f'{jobs_without_dates} positions missing dates',
                'details': 'May be hiding gaps or inconsistencies',
                'recommendation': 'Request complete employment history'
            })
        
        # Check education
        education = candidate.get('education', [])
        experience = candidate.get('experience', 0)
        
        # Title vs experience mismatch
        work_titles = []
        for job in work_history:
            if isinstance(job, dict):
                title = (job.get('title', '') or '').lower()
                work_titles.append(title)
        
        # Check for suspicious title progression
        senior_titles = ['director', 'vp', 'chief', 'head', 'principal', 'senior']
        has_senior = any(
            any(st in title for st in senior_titles) 
            for title in work_titles[:2]  # Recent titles
        )
        
        if has_senior and experience < 5:
            flags.append({
                'type': 'title_inflation',
                'severity': 'warning',
                'message': 'Senior title with limited experience',
                'details': f'Senior role claimed with {experience} years experience',
                'recommendation': 'Verify role scope and responsibilities'
            })
        
        # Calculate score
        score = 100
        for flag in flags:
            if flag['severity'] == 'critical':
                score -= 25
            elif flag['severity'] == 'warning':
                score -= 15
            else:
                score -= 5
        
        return {'score': max(30, score), 'flags': flags}
    
    def _analyze_completeness(self, candidate: Dict) -> Dict:
        """Analyze profile completeness"""
        required_fields = {
            'name': 15,
            'email': 15,
            'phone': 10,
            'skills': 15,
            'experience': 10,
            'education': 10,
            'summary': 10,
            'workHistory': 15,
        }
        
        score = 0
        missing = []
        
        for field, weight in required_fields.items():
            value = candidate.get(field)
            if value:
                if isinstance(value, list) and len(value) > 0:
                    score += weight
                elif isinstance(value, (str, int, float)) and value:
                    score += weight
                else:
                    missing.append(field)
            else:
                missing.append(field)
        
        return {
            'score': score,
            'missing_fields': missing,
            'message': f'{score}% complete' if missing else 'Profile complete'
        }
    
    def _analyze_ats_compatibility(self, resume_text: str, candidate: Dict) -> Dict:
        """Analyze ATS (Applicant Tracking System) compatibility"""
        score = 100
        issues = []
        
        # Check for standard sections
        text_lower = resume_text.lower()
        
        sections = ['experience', 'education', 'skills', 'summary', 'objective']
        found_sections = sum(1 for s in sections if s in text_lower)
        
        if found_sections < 3:
            score -= 15
            issues.append('Missing standard resume sections')
        
        # Check for contact info
        if not candidate.get('email'):
            score -= 10
            issues.append('Missing email address')
        
        if not candidate.get('phone'):
            score -= 5
            issues.append('Missing phone number')
        
        # Check for proper skill formatting
        skills = candidate.get('skills', [])
        if len(skills) < 3:
            score -= 10
            issues.append('Few skills listed - ATS may not parse well')
        
        return {
            'score': max(40, score),
            'issues': issues,
            'recommendation': 'Ensure resume has clear sections and standard formatting'
        }
    
    def _identify_strengths(self, quality_scores: Dict, content_result: Dict) -> List[str]:
        """Identify resume strengths"""
        strengths = []
        
        if quality_scores.get('stability', 0) >= 90:
            strengths.append("Shows strong job stability")
        
        if quality_scores.get('progression', 0) >= 90:
            strengths.append("Clear career progression")
        
        if content_result.get('metrics_found', 0) >= 5:
            strengths.append("Well-quantified achievements")
        
        if content_result.get('strong_verbs', 0) >= 5:
            strengths.append("Uses impactful action language")
        
        if quality_scores.get('completeness', 0) >= 90:
            strengths.append("Comprehensive profile information")
        
        return strengths
    
    def _suggest_improvements(self, red_flags: List, quality_scores: Dict) -> List[str]:
        """Suggest improvements based on analysis"""
        suggestions = []
        
        for flag in red_flags:
            if flag.get('recommendation'):
                suggestions.append(flag['recommendation'])
        
        if quality_scores.get('content', 100) < 70:
            suggestions.append("Request more specific examples and metrics")
        
        if quality_scores.get('completeness', 100) < 80:
            suggestions.append("Ask candidate to provide missing information")
        
        return list(set(suggestions))[:5]  # Top 5 unique suggestions
    
    def _get_verification_items(self, red_flags: List) -> List[str]:
        """Get items that need verification"""
        items = []
        
        for flag in red_flags:
            if flag.get('severity') in ['critical', 'warning']:
                if 'employment' in flag.get('type', ''):
                    items.append("Verify employment dates with references")
                elif 'title' in flag.get('type', ''):
                    items.append("Confirm job titles with previous employers")
                elif 'education' in flag.get('type', ''):
                    items.append("Verify educational credentials")
        
        if not items:
            items.append("Standard background check recommended")
        
        return list(set(items))
    
    def _generate_probe_questions(self, red_flags: List) -> List[str]:
        """Generate interview questions to address red flags"""
        questions = []
        
        flag_types = set(f.get('type') for f in red_flags)
        
        if 'employment_gap' in flag_types:
            questions.append("Can you walk me through the transition between [Job A] and [Job B]?")
            questions.append("What were you focused on during the gap period?")
        
        if 'job_hopping' in flag_types:
            questions.append("What prompted your move from [Company]?")
            questions.append("What are you looking for in your next long-term role?")
        
        if 'no_quantified_achievements' in flag_types:
            questions.append("Can you share specific metrics or outcomes from your work?")
            questions.append("What measurable impact did you have in your last role?")
        
        if 'vague_descriptions' in flag_types:
            questions.append("Tell me about a specific project you led and its results.")
            questions.append("What was your individual contribution to [achievement]?")
        
        if 'title_inflation' in flag_types:
            questions.append("What was the scope and team size in your [title] role?")
            questions.append("Who did you report to and who reported to you?")
        
        if not questions:
            questions.append("Tell me about your most significant achievement.")
            questions.append("Why are you interested in this opportunity?")
        
        return questions


# Singleton
_analyzer = None

def get_quality_analyzer() -> ResumeQualityAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = ResumeQualityAnalyzer()
    return _analyzer
