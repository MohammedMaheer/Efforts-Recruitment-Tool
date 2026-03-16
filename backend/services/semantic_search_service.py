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
        final = ranked[:target_pool_size]

        # Step 1D: Add match explanations to each candidate
        for candidate in final:
            candidate['match_reasons'] = self._build_match_reasons(candidate, constraints)

        return final

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

            # ── NEGATIVE CONSTRAINTS (EXCLUSION FILTERS) ──────────────────────────

            # Exclude candidates with negative skills
            if constraints.negative_skills:
                candidate_skills = [s.lower() for s in candidate.get('skills', []) or []]
                if any(neg_skill.lower() in candidate_skills for neg_skill in constraints.negative_skills):
                    continue  # Exclude this candidate

            # Exclude freelancers if requested
            if constraints.must_not_be_freelancer:
                job_title = (candidate.get('jobSubcategory') or candidate.get('job_category') or '').lower()
                summary = (candidate.get('summary') or '').lower()
                # Check for freelance keywords
                if any(keyword in job_title or keyword in summary for keyword in ['freelance', 'contractor', 'contract', 'part-time']):
                    continue  # Exclude this candidate

            # Exclude specific industries if requested
            if constraints.excluded_industries:
                candidate_industry = (candidate.get('current_industry') or candidate.get('job_category') or '').lower()
                if any(ind.lower() in candidate_industry for ind in constraints.excluded_industries):
                    continue  # Exclude this candidate

            filtered.append(candidate)

        return filtered

    async def _score_by_semantic_similarity(
        self,
        candidates: List[Dict[str, Any]],
        query: str,
        batch_size: int = 32,
    ) -> List[Dict[str, Any]]:
        """
        Compute semantic similarity scores using embeddings with caching (Phase 1.1).

        Enriches candidate profiles (skills + summary + history) and compares
        to query using sentence-transformers cosine similarity.
        Caches embeddings to avoid recomputation on repeated searches.

        Args:
            candidates: Filtered candidate list
            query: Natural language query
            batch_size: Batch size for embedding computation

        Returns:
            Candidates with added 'semantic_score' field
        """
        if not candidates:
            return []

        # Get embedding cache (Phase 1.1)
        from api.deps import get_embedding_cache
        embedding_cache = get_embedding_cache()

        # Compute query embedding once (cache query too)
        try:
            query_embedding = await embedding_cache.get(query)
            if query_embedding is None:
                query_embedding = await asyncio.to_thread(
                    self.local_ai_service.get_embedding, query
                )
                await embedding_cache.set(query, query_embedding)
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

        # Check cache for embeddings and identify missing ones (Phase 1.1)
        candidate_embeddings = []
        missing_profiles = []
        missing_indices = []

        try:
            cached_embs, missing = await embedding_cache.get_batch(candidate_profiles)

            # Build embeddings list with cache hits, placeholders for misses
            for i, profile in enumerate(candidate_profiles):
                if profile in cached_embs:
                    candidate_embeddings.append(cached_embs[profile])
                else:
                    candidate_embeddings.append(None)
                    missing_profiles.append(profile)
                    missing_indices.append(i)

            # Compute missing embeddings only
            if missing_profiles:
                logger.debug(f"Embedding cache: {len(cached_embs)}/{len(candidate_profiles)} hits, computing {len(missing_profiles)} missing")
                computed_embeddings = []
                for i in range(0, len(missing_profiles), batch_size):
                    batch = missing_profiles[i : i + batch_size]
                    batch_embeddings = await asyncio.to_thread(
                        self._batch_embed, batch
                    )
                    computed_embeddings.extend(batch_embeddings)

                # Store computed embeddings in cache and fill results
                computed_dict = {}
                for profile, embedding in zip(missing_profiles, computed_embeddings):
                    if embedding is not None:
                        computed_dict[profile] = embedding
                await embedding_cache.set_batch(computed_dict)

                # Fill in computed embeddings
                for idx, embedding in zip(missing_indices, computed_embeddings):
                    candidate_embeddings[idx] = embedding
            else:
                logger.debug(f"Embedding cache: 100% hit rate ({len(candidate_profiles)}/{len(candidate_profiles)})")

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

    def _build_match_reasons(self, candidate: Dict[str, Any], constraints: ParsedConstraints) -> List[str]:
        """Build list of reasons why candidate matched the query constraints."""
        match_reasons = []

        # Track matched required skills
        if constraints.required_skills:
            candidate_skills = [s.lower() for s in candidate.get('skills', []) or []]
            matched_skills = []
            for req_skill in constraints.required_skills:
                if req_skill.lower() in candidate_skills:
                    matched_skills.append(req_skill)
            if matched_skills:
                match_reasons.append(f"Skills: {', '.join(matched_skills[:3])}")

        # Track experience match
        if constraints.min_experience is not None:
            exp = candidate.get('experience', 0) or 0
            if exp >= constraints.min_experience:
                match_reasons.append(f"Experience: {exp} years")

        # Track location match
        if constraints.locations and candidate.get('location_match'):
            location = candidate.get('location', '')
            if location:
                match_reasons.append(f"Location: {location}")

        # Track remote preference match
        if constraints.remote_type == 'required' and candidate.get('can_work_remote'):
            match_reasons.append("Remote: Yes")

        # Add semantic relevance score
        score = candidate.get('semantic_score', 0)
        if score > 0:
            match_reasons.append(f"Relevance: {int(score * 100)}%")

        return match_reasons

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
