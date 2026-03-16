"""
Two-stage semantic search with constraint filtering.

Stage 1: Applies hard constraints + semantic similarity scoring
Stage 2: Gemini AI ranks the filtered pool

Enables searching across 5000+ candidates efficiently.
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

from .constraint_parser import ParsedConstraints
from services.local_ai_service import get_local_ai_service

logger = logging.getLogger(__name__)


class SemanticSearchService:
    """Two-stage search combining constraint filtering + embeddings."""

    def __init__(self):
        self.local_ai_service = get_local_ai_service()

    async def filter_stage_1(
        self,
        candidates: List[Dict[str, Any]],
        query: str,
        constraints: ParsedConstraints,
        target_pool_size: int = 300,
    ) -> List[Dict[str, Any]]:
        """
        Stage 1: Filter candidates using constraints + semantic similarity.

        Args:
            candidates: Full candidate list (5000+)
            query: Original natural language query
            constraints: Parsed constraints from query
            target_pool_size: Target number of candidates to return (200-500)

        Returns:
            Filtered candidates ranked by semantic similarity, capped at target_pool_size
        """
        logger.info(f"Stage 1: Filtering {len(candidates)} candidates")

        # Step 1A: Apply hard constraint filters
        filtered = self.apply_constraint_filters(candidates, constraints)
        logger.info(f"After constraints: {len(filtered)} candidates")

        # Step 1B: Compute semantic scores on filtered pool
        scored = await self._score_by_semantic_similarity(filtered, query)
        logger.info(f"Computed embeddings for {len(scored)} candidates")

        # Step 1C: Sort by score and cap to target pool size
        ranked = sorted(scored, key=lambda x: x['semantic_score'], reverse=True)
        return ranked[:target_pool_size]

    def apply_constraint_filters(
        self,
        candidates: List[Dict[str, Any]],
        constraints: ParsedConstraints,
    ) -> List[Dict[str, Any]]:
        """
        Apply hard constraint filters to candidate list.

        Filters by: experience, salary, education, location, notice period, languages
        """
        filtered = []

        for candidate in candidates:
            # Check experience constraint
            if constraints.min_experience is not None:
                experience = candidate.get('experience', 0) or 0
                if experience < constraints.min_experience:
                    continue

            if constraints.max_experience is not None:
                experience = candidate.get('experience', 0) or 0
                if experience > constraints.max_experience:
                    continue

            # Check salary constraint
            if constraints.min_salary is not None:
                salary = candidate.get('current_salary') or candidate.get('expected_salary') or 0
                if salary and salary < constraints.min_salary:
                    continue

            if constraints.max_salary is not None:
                salary = candidate.get('current_salary') or candidate.get('expected_salary') or 0
                if salary and salary > constraints.max_salary:
                    continue

            # Check education constraint
            if constraints.education_level is not None:
                education = (candidate.get('education', {}) or {}).get('degree', '').lower()
                edu_level = constraints.education_level.lower()
                # Normalize education comparison
                if edu_level == 'phd' and 'phd' not in education:
                    continue
                elif edu_level == 'masters' and not any(m in education for m in ['master', 'm.tech', 'm.s']):
                    continue
                elif edu_level == 'bachelors' and not any(b in education for b in ['bachelor', 'b.tech', 'b.s']):
                    continue

            # Check location constraint (soft filter: bonus if match, penalty if not)
            if constraints.locations:
                location = (candidate.get('location', '') or '').lower()
                if any(loc.lower() in location for loc in constraints.locations):
                    candidate['location_match'] = True
                else:
                    candidate['location_match'] = False

            # Check remote type constraint
            if constraints.remote_type and constraints.remote_type != 'optional':
                # If strict remote requirement, check candidate preference
                # For now, soft filter (warning only, don't exclude)
                can_be_remote = candidate.get('can_work_remote', False)
                if constraints.remote_type == 'required' and not can_be_remote:
                    # Could exclude, but for MVP keep as soft filter
                    candidate['remote_mismatch'] = True

            # Check notice period constraint
            if constraints.notice_period_max:
                notice = (candidate.get('notice_period', '') or '').lower()
                if constraints.notice_period_max == 'immediate':
                    if 'immediate' not in notice:
                        continue
                # For other notice periods, could add stricter filtering if needed

            # Check languages constraint
            if constraints.languages:
                languages = [l.lower() for l in candidate.get('languages', []) or []]
                if not any(lang.lower() in ' '.join(languages) for lang in constraints.languages):
                    # Soft filter: warning but don't exclude
                    candidate['language_mismatch'] = True

            filtered.append(candidate)

        return filtered

    async def _score_by_semantic_similarity(
        self,
        candidates: List[Dict[str, Any]],
        query: str,
        batch_size: int = 32,
    ) -> List[Dict[str, Any]]:
        """
        Compute semantic similarity scores using embeddings.

        Enriches candidate profiles (skills + summary + history) and compares
        to query using sentence-transformers cosine similarity.

        Args:
            candidates: Filtered candidate list
            query: Natural language query
            batch_size: Batch size for embedding computation

        Returns:
            Candidates with added 'semantic_score' field
        """
        if not candidates:
            return []

        # Compute query embedding once
        try:
            query_embedding = await asyncio.to_thread(
                self.local_ai_service.get_embedding, query
            )
        except Exception as e:
            logger.error(f"Failed to embed query: {e}")
            logger.warning("Falling back to equal scores")
            for c in candidates:
                c['semantic_score'] = 0.5
            return candidates

        # Prepare candidate profiles
        candidate_profiles = [
            self._enrich_candidate_profile(c) for c in candidates
        ]

        # Compute embeddings in batches
        candidate_embeddings = []
        try:
            for i in range(0, len(candidate_profiles), batch_size):
                batch = candidate_profiles[i : i + batch_size]
                batch_embeddings = await asyncio.to_thread(
                    self._batch_embed, batch
                )
                candidate_embeddings.extend(batch_embeddings)
        except Exception as e:
            logger.error(f"Failed to embed candidates: {e}")
            logger.warning("Falling back to equal scores")
            for c in candidates:
                c['semantic_score'] = 0.5
            return candidates

        # Compute cosine similarity scores
        for i, candidate in enumerate(candidates):
            if i < len(candidate_embeddings):
                embedding = candidate_embeddings[i]
                if embedding is not None:
                    # Cosine similarity: (A · B) / (||A|| ||B||)
                    score = self._cosine_similarity(query_embedding, embedding)
                    candidate['semantic_score'] = float(max(0, min(1, score)))
                else:
                    candidate['semantic_score'] = 0.5
            else:
                candidate['semantic_score'] = 0.5

        return candidates

    def _batch_embed(self, texts: List[str]) -> List[Optional[np.ndarray]]:
        """Embed a batch of texts."""
        embeddings = []
        for text in texts:
            try:
                embedding = self.local_ai_service.get_embedding(text)
                embeddings.append(embedding)
            except Exception as e:
                logger.warning(f"Failed to embed text: {e}")
                embeddings.append(None)
        return embeddings

    def _enrich_candidate_profile(self, candidate: Dict[str, Any]) -> str:
        """
        Create enriched profile text for semantic comparison.

        Concatenates: name, skills, summary, job_category, work_history titles
        """
        parts = []

        # Name
        name = candidate.get('firstName') or candidate.get('name') or ''
        if name:
            parts.append(name)

        # Skills
        skills = candidate.get('skills', []) or []
        if skills:
            if isinstance(skills, list):
                parts.append(' '.join(skills[:20]))  # Cap at 20 skills
            else:
                parts.append(str(skills))

        # Summary
        summary = candidate.get('summary') or ''
        if summary:
            parts.append(summary[:500])  # Cap at 500 chars

        # Job category
        job_category = candidate.get('jobSubcategory') or candidate.get('job_category') or ''
        if job_category:
            parts.append(job_category)

        # Work history
        work_history = candidate.get('work_history', []) or []
        if work_history:
            job_titles = []
            for job in work_history[:3]:  # Cap at 3 most recent jobs
                title = job.get('title') or job.get('jobTitle') or ''
                company = job.get('company') or ''
                if title:
                    job_titles.append(title)
                if company:
                    job_titles.append(company)
            if job_titles:
                parts.append(' '.join(job_titles))

        # Education
        education = candidate.get('education', {}) or {}
        if education:
            degree = education.get('degree') or ''
            field = education.get('field') or ''
            if degree:
                parts.append(degree)
            if field:
                parts.append(field)

        # Certifications
        certifications = candidate.get('certifications', []) or []
        if certifications:
            cert_names = [c.get('name') or c.get('certification') or '' for c in certifications[:5]]
            parts.append(' '.join(filter(None, cert_names)))

        # Join all parts
        profile = ' '.join(filter(None, parts))
        return profile.lower()

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        if a is None or b is None:
            return 0.0

        a = np.array(a).flatten()
        b = np.array(b).flatten()

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))


# ── Singleton ─────────────────────────────────────────────────────────────────

_semantic_search_service = None

def get_semantic_search_service() -> SemanticSearchService:
    """Get or create semantic search service singleton."""
    global _semantic_search_service
    if _semantic_search_service is None:
        _semantic_search_service = SemanticSearchService()
    return _semantic_search_service
