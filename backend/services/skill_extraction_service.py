"""
Advanced Skill Extraction Service
Uses local pattern matching for complex skill inference from resume context
Identifies implicit skills, skill levels, and related technologies
"""
import logging
import re
from difflib import get_close_matches
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class AdvancedSkillExtractor:
    """
    Local skill extraction with:
    - Implicit skill inference (mentions React → knows JavaScript)
    - Skill level assessment (beginner/intermediate/expert)
    - Technology stack grouping
    - Soft skills detection
    """
    
    # Technology relationships for inference
    SKILL_RELATIONSHIPS = {
        # Frontend implies
        'react': ['javascript', 'html', 'css', 'jsx', 'npm', 'webpack'],
        'angular': ['typescript', 'javascript', 'html', 'css', 'rxjs'],
        'vue': ['javascript', 'html', 'css', 'vuex'],
        'next.js': ['react', 'javascript', 'node.js', 'typescript'],
        'nuxt': ['vue', 'javascript', 'node.js'],
        
        # Backend implies
        'django': ['python', 'sql', 'orm', 'rest api'],
        'flask': ['python', 'rest api'],
        'fastapi': ['python', 'async', 'rest api', 'pydantic'],
        'spring boot': ['java', 'maven', 'rest api', 'sql'],
        'express': ['node.js', 'javascript', 'rest api'],
        'rails': ['ruby', 'sql', 'mvc'],
        '.net': ['c#', 'sql', 'visual studio'],
        
        # Data implies
        'tensorflow': ['python', 'machine learning', 'deep learning', 'numpy'],
        'pytorch': ['python', 'machine learning', 'deep learning', 'numpy'],
        'pandas': ['python', 'data analysis', 'numpy'],
        'spark': ['big data', 'sql', 'python', 'scala'],
        'hadoop': ['big data', 'java', 'mapreduce'],
        
        # Cloud implies
        'aws': ['cloud computing', 'ec2', 's3', 'lambda'],
        'azure': ['cloud computing', 'microsoft'],
        'gcp': ['cloud computing', 'google cloud'],
        'kubernetes': ['docker', 'devops', 'containerization'],
        'docker': ['containerization', 'linux', 'devops'],
        'terraform': ['infrastructure as code', 'devops', 'cloud'],
        
        # Database implies
        'postgresql': ['sql', 'database', 'relational database'],
        'mysql': ['sql', 'database', 'relational database'],
        'mongodb': ['nosql', 'database', 'document database'],
        'redis': ['caching', 'nosql', 'in-memory database'],
        'elasticsearch': ['search', 'nosql', 'analytics'],
    }
    
    # Skill level indicators
    EXPERT_INDICATORS = [
        'architect', 'lead', 'senior', 'principal', 'expert', 'advanced',
        'years of experience', '5+ years', '7+ years', '10+ years',
        'designed', 'architected', 'mentored', 'led team'
    ]
    
    INTERMEDIATE_INDICATORS = [
        'proficient', 'experienced', '2-5 years', '3+ years', '2+ years',
        'developed', 'implemented', 'built', 'created'
    ]
    
    BEGINNER_INDICATORS = [
        'familiar', 'basic', 'learning', 'exposure', 'coursework',
        '1 year', 'junior', 'entry', 'intern'
    ]
    
    # Soft skills dictionary
    SOFT_SKILLS = {
        'communication': ['communicate', 'presentation', 'written', 'verbal', 'stakeholder'],
        'leadership': ['lead', 'manage', 'mentor', 'coach', 'team lead', 'supervisor'],
        'problem solving': ['problem-solving', 'analytical', 'troubleshoot', 'debug', 'resolve'],
        'teamwork': ['collaborate', 'team player', 'cross-functional', 'agile team'],
        'time management': ['deadline', 'prioritize', 'multitask', 'time-sensitive'],
        'adaptability': ['adapt', 'flexible', 'fast-paced', 'dynamic environment'],
        'creativity': ['creative', 'innovative', 'design thinking', 'ideation'],
        'attention to detail': ['detail-oriented', 'meticulous', 'thorough', 'quality'],
    }
    
    def __init__(self):
        pass

    def _fuzzy_match_skill(self, typo_skill: str, candidate_skills: List[str], threshold: float = 0.85) -> Optional[str]:
        """
        Find similar skill in candidate list - handles typos (Phase 1.2).

        Uses difflib.get_close_matches for fuzzy string matching.
        Example: "Phyton" -> "Python", "Sofware" -> "Software"

        Args:
            typo_skill: Skill name with potential typo
            candidate_skills: List of candidate skills to search in
            threshold: Minimum similarity ratio (0.0-1.0)

        Returns:
            Matching skill name if found, None otherwise
        """
        candidate_skills_lower = [s.lower() for s in candidate_skills]
        matches = get_close_matches(
            typo_skill.lower(),
            candidate_skills_lower,
            n=1,
            cutoff=threshold
        )
        if matches:
            # Find the original skill name (with original casing)
            match_lower = matches[0]
            for skill in candidate_skills:
                if skill.lower() == match_lower:
                    return skill
        return None

    async def extract_skills(self, resume_text: str) -> Dict:
        """
        Extract skills from resume using local pattern matching.
        Returns structured skill data with levels and categories.
        """
        return await self.extract_skills_local(resume_text)
    
    async def extract_skills_local(self, resume_text: str) -> Dict:
        """
        Local skill extraction using pattern matching.
        Uses pattern matching and skill relationships
        """
        text_lower = resume_text.lower()
        
        technical_skills = []
        soft_skills = []
        inferred_skills = []
        tools = []
        certifications = []
        
        # Extract explicit technical skills
        all_tech_skills = set()
        for skill in self.SKILL_RELATIONSHIPS.keys():
            if skill in text_lower:
                level = self._assess_skill_level(skill, resume_text)
                technical_skills.append({
                    'name': skill.title(),
                    'level': level,
                    'years': self._estimate_years(skill, resume_text),
                    'context': self._extract_context(skill, resume_text)
                })
                all_tech_skills.add(skill)
                
                # Add inferred skills
                for implied in self.SKILL_RELATIONSHIPS[skill]:
                    if implied not in all_tech_skills:
                        inferred_skills.append({
                            'name': implied.title(),
                            'inferred_from': f"{skill.title()} experience"
                        })
                        all_tech_skills.add(implied)
        
        # Extract soft skills
        for skill, indicators in self.SOFT_SKILLS.items():
            for indicator in indicators:
                if indicator in text_lower:
                    evidence = self._extract_context(indicator, resume_text)
                    soft_skills.append({
                        'name': skill.title(),
                        'evidence': evidence
                    })
                    break
        
        # Extract certifications
        cert_patterns = [
            r'(aws\s+\w+\s+architect)',
            r'(certified\s+\w+\s+\w+)',
            r'(pmp|scrum master|cissp|cka|ckad)',
            r'(google\s+cloud\s+\w+)',
            r'(azure\s+\w+)',
        ]
        for pattern in cert_patterns:
            matches = re.findall(pattern, text_lower)
            certifications.extend([m.title() for m in matches])
        
        # Extract tools
        common_tools = [
            'git', 'github', 'gitlab', 'bitbucket',
            'jira', 'confluence', 'trello', 'asana',
            'slack', 'teams', 'zoom',
            'figma', 'sketch', 'adobe xd',
            'jenkins', 'circleci', 'github actions',
            'postman', 'swagger', 'insomnia',
            'vscode', 'intellij', 'pycharm',
        ]
        for tool in common_tools:
            if tool in text_lower:
                tools.append(tool.title())
        
        return {
            'technical_skills': technical_skills,
            'soft_skills': soft_skills,
            'certifications': list(set(certifications)),
            'tools': list(set(tools)),
            'languages': self._extract_languages(resume_text),
            'inferred_skills': inferred_skills
        }
    
    def _assess_skill_level(self, skill: str, text: str) -> str:
        """Assess skill level based on context"""
        text_lower = text.lower()
        
        # Find sentences containing the skill
        sentences = text.split('.')
        skill_context = ' '.join(s for s in sentences if skill in s.lower())
        context_lower = skill_context.lower()
        
        # Check for expert indicators
        for indicator in self.EXPERT_INDICATORS:
            if indicator in context_lower:
                return 'expert'
        
        # Check for beginner indicators
        for indicator in self.BEGINNER_INDICATORS:
            if indicator in context_lower:
                return 'beginner'
        
        # Default to intermediate
        return 'intermediate'
    
    def _estimate_years(self, skill: str, text: str) -> Optional[int]:
        """Estimate years of experience with a skill"""
        text_lower = text.lower()
        
        # Look for patterns like "5 years of Python" or "Python (5 years)"
        patterns = [
            rf'(\d+)\+?\s*years?\s+(?:of\s+)?{skill}',
            rf'{skill}\s*\((\d+)\+?\s*years?\)',
            rf'{skill}.*?(\d+)\+?\s*years?',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                return int(match.group(1))
        
        return None
    
    def _extract_context(self, skill: str, text: str) -> str:
        """Extract a relevant sentence showing skill usage"""
        sentences = text.split('.')
        for sentence in sentences:
            if skill.lower() in sentence.lower() and len(sentence) > 20:
                return sentence.strip()[:200]
        return ""
    
    def _extract_languages(self, text: str) -> List[str]:
        """Extract spoken languages"""
        languages = []
        common_languages = [
            'english', 'spanish', 'french', 'german', 'mandarin', 'chinese',
            'japanese', 'korean', 'arabic', 'hindi', 'portuguese', 'russian',
            'italian', 'dutch', 'swedish', 'polish', 'turkish'
        ]
        
        text_lower = text.lower()
        for lang in common_languages:
            if lang in text_lower:
                languages.append(lang.title())
        
        return languages
    
    async def analyze_skill_gaps(self, candidate_skills: List[str], job_skills: List[str]) -> Dict:
        """
        Analyze gaps between candidate skills and job requirements.
        Uses fuzzy matching (Phase 1.2) for typo tolerance.
        """
        candidate_set = set(s.lower() for s in candidate_skills)
        job_set = set(s.lower() for s in job_skills)

        # Direct matches (exact)
        matching = candidate_set & job_set

        # Missing skills (with fuzzy fallback - Phase 1.2)
        missing = job_set - candidate_set
        fuzzy_matched = set()

        for missing_skill in list(missing):
            # Try fuzzy match (Phase 1.2)
            fuzzy_match = self._fuzzy_match_skill(missing_skill, list(candidate_set), threshold=0.85)
            if fuzzy_match:
                fuzzy_matched.add(missing_skill)
                matching.add(missing_skill)  # Count as match via fuzzy

        # Remove fuzzy matched from missing set
        missing = missing - fuzzy_matched

        # Extra skills (candidate has, job doesn't require)
        extra = candidate_set - job_set

        # Check for related skills that could substitute
        transferable = []
        for missing_skill in missing:
            for candidate_skill in candidate_set:
                # Check if candidate skill implies missing skill
                if candidate_skill in self.SKILL_RELATIONSHIPS:
                    if missing_skill in self.SKILL_RELATIONSHIPS[candidate_skill]:
                        transferable.append({
                            'missing': missing_skill,
                            'covered_by': candidate_skill
                        })

        # Calculate match percentage
        if job_set:
            covered = len(matching) + len(transferable)
            match_pct = round(covered / len(job_set) * 100, 1)
        else:
            match_pct = 100
        
        return {
            'match_percentage': match_pct,
            'matching_skills': list(matching),
            'missing_skills': list(missing),
            'extra_skills': list(extra),
            'transferable_skills': transferable,
            'recommendation': self._generate_recommendation(match_pct, missing)
        }
    
    def _generate_recommendation(self, match_pct: float, missing: Set[str]) -> str:
        """Generate hiring recommendation based on skill analysis"""
        if match_pct >= 90:
            return "Excellent match - highly recommended for interview"
        elif match_pct >= 75:
            return f"Strong match - missing {len(missing)} skills that may be trainable"
        elif match_pct >= 60:
            return f"Moderate match - consider if other qualifications compensate"
        elif match_pct >= 40:
            return "Weak match - significant skill gaps exist"
        else:
            return "Poor match - not recommended without extensive training"


# Singleton
_skill_extractor = None

def get_skill_extractor() -> AdvancedSkillExtractor:
    global _skill_extractor
    if _skill_extractor is None:
        _skill_extractor = AdvancedSkillExtractor()
    return _skill_extractor
