"""
AI-Powered Candidate Matching Engine
=====================================
Uses LLM for intelligent semantic matching with TF-IDF fallback.

Matching tiers:
1. LLM (Ollama) - Deep semantic understanding, reasoning about fit
2. Sentence-Transformers - Embedding-based semantic similarity
3. TF-IDF + Cosine Similarity - Statistical keyword matching (fallback)
"""

import logging
from typing import List, Dict, Any, Optional
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

logger = logging.getLogger(__name__)


class MatchingEngine:
    """
    AI-powered candidate matching engine.
    
    Uses a tiered approach:
    1. LLM for deep semantic matching and reasoning (primary)
    2. Sentence-transformers for embedding-based similarity
    3. TF-IDF for statistical matching (fallback)
    """
    
    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words='english',
            ngram_range=(1, 2)
        )
        self._llm_service = None
        self._sentence_model = None
        self._initialized = False
    
    async def _ensure_initialized(self):
        """Lazy-initialize LLM and sentence-transformer services"""
        if self._initialized:
            return
        
        # Try to get LLM service
        try:
            from services.llm_service import get_llm_service
            self._llm_service = await get_llm_service()
            if self._llm_service.available:
                logger.info("✅ MatchingEngine: LLM-powered matching enabled")
        except Exception as e:
            logger.debug(f"LLM service not available for matching: {e}")
        
        # Try to get sentence model
        try:
            from services.local_ai_service import get_ai_service
            ai = get_ai_service()
            if ai.sentence_model:
                self._sentence_model = ai.sentence_model
                logger.info("✅ MatchingEngine: Semantic similarity enabled")
        except Exception as e:
            logger.debug(f"Sentence model not available: {e}")
        
        self._initialized = True
    
    async def match_candidates(
        self,
        job_description: str,
        candidates: List[Dict],
        top_n: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Match candidates against a job description.
        Returns ranked list with detailed match analysis.
        """
        await self._ensure_initialized()
        
        if not candidates:
            return []
        
        # Use LLM for intelligent matching if available
        if self._llm_service and self._llm_service.available:
            try:
                results = await self._llm_service.rank_candidates_for_job(
                    candidates=candidates,
                    job_description=job_description,
                    top_n=top_n
                )
                logger.info(f"🤖 LLM matched {len(results)} candidates")
                return results
            except Exception as e:
                logger.warning(f"LLM matching failed, using fallback: {e}")
        
        # Fallback: Use semantic + keyword matching
        results = []
        for candidate in candidates[:100]:
            try:
                score = await self._calculate_combined_score(candidate, job_description)
                results.append({
                    'candidate': candidate,
                    'match': {
                        'match_score': score,
                        'overall_fit': self._get_fit_label(score),
                        'matched_skills': [],
                        'missing_skills': [],
                        'strengths': [],
                        'gaps': [],
                    },
                    'score': score
                })
            except Exception as e:
                logger.warning(f"Matching error: {e}")
        
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_n]
    
    async def evaluate_candidate(
        self,
        candidate_data: Dict,
        job_description: str
    ) -> Dict[str, Any]:
        """
        Detailed evaluation of candidate vs job description.
        Uses LLM for deep analysis.
        """
        await self._ensure_initialized()
        
        # Use LLM for detailed evaluation
        if self._llm_service and self._llm_service.available:
            try:
                return await self._llm_service.match_candidate_to_job(
                    candidate_data=candidate_data,
                    job_description=job_description
                )
            except Exception as e:
                logger.warning(f"LLM evaluation failed: {e}")
        
        # Fallback evaluation
        candidate_skills = [s.lower() for s in candidate_data.get('skills', [])]
        score = await self._calculate_combined_score(candidate_data, job_description)
        
        return {
            "match_score": score,
            "overall_fit": self._get_fit_label(score),
            "strengths": [f"Has {len(candidate_skills)} relevant skills"] if candidate_skills else [],
            "gaps": ["Detailed analysis requires LLM service"],
            "recommendation": self.generate_recommendation(score, [], []),
            "confidence_score": 60
        }
    
    async def _calculate_combined_score(self, candidate: Dict, job_description: str) -> int:
        """Calculate combined score using semantic similarity + skill matching"""
        scores = []
        
        # Build candidate text
        candidate_text = ' '.join([
            str(candidate.get('summary', '')),
            ' '.join(candidate.get('skills', [])),
            str(candidate.get('experience', '')),
        ]).strip()
        
        if not candidate_text:
            return 0
        
        # 1. Semantic similarity using sentence-transformers
        if self._sentence_model:
            try:
                cand_emb = self._sentence_model.encode(candidate_text, show_progress_bar=False)
                job_emb = self._sentence_model.encode(job_description[:1000], show_progress_bar=False)
                
                from numpy import dot
                from numpy.linalg import norm
                similarity = float(dot(cand_emb, job_emb) / (norm(cand_emb) * norm(job_emb)))
                scores.append(('semantic', similarity * 100, 0.5))
            except Exception:
                pass
        
        # 2. TF-IDF similarity
        try:
            tfidf_score = self.calculate_semantic_similarity(candidate_text, job_description) * 100
            scores.append(('tfidf', tfidf_score, 0.2))
        except Exception:
            pass
        
        # 3. Skill keyword overlap
        candidate_skills = set(s.lower() for s in candidate.get('skills', []))
        job_lower = job_description.lower()
        matched = sum(1 for s in candidate_skills if s in job_lower)
        skill_score = (matched / max(len(candidate_skills), 1)) * 100
        scores.append(('skills', skill_score, 0.3))
        
        if not scores:
            return 0
        
        # Weighted average
        total_weight = sum(w for _, _, w in scores)
        weighted_score = sum(s * w for _, s, w in scores) / total_weight
        
        return max(0, min(100, int(weighted_score)))
    
    def calculate_skill_match(
        self,
        candidate_skills: List[str],
        required_skills: List[str],
        preferred_skills: List[str] = None
    ) -> Dict[str, Any]:
        """Calculate skill match percentage with detailed breakdown"""
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
        preferred_skills_lower = []
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
                s for s in preferred_skills_lower
                if s not in candidate_skills_lower
            ]
        }
    
    def calculate_semantic_similarity(
        self,
        text1: str,
        text2: str
    ) -> float:
        """Calculate semantic similarity between two texts using TF-IDF"""
        try:
            if not text1.strip() or not text2.strip():
                return 0.0
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
        """Generate recommendation based on match analysis"""
        if match_score >= 85:
            return "Strong candidate - highly recommend for interview. Excellent skill match and qualifications."
        elif match_score >= 70:
            return "Good candidate - recommend interview. Skills align well with some minor gaps."
        elif match_score >= 50:
            return "Moderate candidate - consider for interview. Has potential but some gaps exist."
        elif match_score >= 35:
            return "Below average match - not ideal for this role but may have transferable skills."
        else:
            return "Weak match - not recommended. Skills and experience don't align with requirements."
    
    def categorize_candidate(self, match_score: float) -> str:
        """Categorize candidate based on match score"""
        if match_score >= 80:
            return "Strong"
        elif match_score >= 60:
            return "Partial"
        elif match_score >= 40:
            return "Potential"
        else:
            return "Reject"
    
    def _get_fit_label(self, score: int) -> str:
        """Get fit label for a score"""
        if score >= 85:
            return "Excellent Match"
        elif score >= 70:
            return "Strong Match"
        elif score >= 55:
            return "Good Match"
        elif score >= 40:
            return "Partial Match"
        else:
            return "Low Match"
