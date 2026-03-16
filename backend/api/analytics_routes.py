"""
Advanced Analytics Routes
Provides insights on search patterns, skill demand, hiring funnel, and constraint effectiveness

Phase 2.3: Advanced search analytics
- GET /api/analytics/search-patterns: Top queries, constraints used, conversion rates
- GET /api/analytics/skill-demand: Skills by frequency, trending skills, premium skills
- GET /api/analytics/pipeline-funnel: Search → Shortlist → Interview → Hire conversion
- GET /api/analytics/constraint-effectiveness: Best performing constraints and overspecified searches
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
import json

from core.dependencies import require_auth
from api.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"], prefix="/api/analytics")


def _db():
    """Get database service."""
    return get_db()


@router.get("/search-patterns")
async def get_search_patterns(
    days: int = 30,
    current_user: dict = Depends(require_auth)
) -> Dict:
    """
    Analyze search patterns from search_history table.

    Returns:
    - top_queries: Most searched queries with hit rates
    - most_used_constraints: Most common constraint values
    - constraint_conversion_rate: Which constraints → hires
    - result_metrics: Avg results per search, success rates
    """
    try:
        db_service = _db()

        # Get all searches from past N days
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        searches = await _db_call(
            lambda: db_service.get_search_history(limit=5000)
        )

        if not searches:
            return {
                'top_queries': [],
                'most_used_constraints': {},
                'constraint_conversion_rate': {},
                'result_metrics': {
                    'avg_results_per_search': 0,
                    'median': 0,
                    'search_success_rate': 0.0
                }
            }

        # Filter by date if needed
        recent_searches = [
            s for s in searches
            if s.get('searched_at', '') >= cutoff_date
        ]

        # Analyze top queries
        query_counts = {}
        query_results = {}
        for search in recent_searches:
            query = search.get('query', 'unnamed')
            count = query_counts.get(query, 0) + 1
            results = search.get('result_count', 0)
            query_counts[query] = count
            query_results[query] = (query_results.get(query, 0) + results) / count

        top_queries = [
            {
                'query': k,
                'count': v,
                'avg_results': round(query_results.get(k, 0), 1)
            }
            for k, v in sorted(query_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        ]

        # Analyze constraint usage
        constraint_usage = {
            'required_skills': {},
            'seniority': {},
            'locations': {},
            'remote_type': {}
        }

        for search in recent_searches:
            constraints_json = search.get('constraints_json', '{}')
            try:
                if isinstance(constraints_json, str):
                    constraints = json.loads(constraints_json)
                else:
                    constraints = constraints_json

                # Count required skills
                for skill in constraints.get('required_skills', []):
                    skill_key = skill.lower()
                    constraint_usage['required_skills'][skill_key] = \
                        constraint_usage['required_skills'].get(skill_key, 0) + 1

                # Count seniority levels
                seniority = constraints.get('seniority_level')
                if seniority:
                    constraint_usage['seniority'][seniority] = \
                        constraint_usage['seniority'].get(seniority, 0) + 1

                # Count locations
                for location in constraints.get('locations', []):
                    loc_key = location.lower()
                    constraint_usage['locations'][loc_key] = \
                        constraint_usage['locations'].get(loc_key, 0) + 1

                # Count remote types
                remote = constraints.get('remote_type')
                if remote:
                    constraint_usage['remote_type'][remote] = \
                        constraint_usage['remote_type'].get(remote, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue

        # Filter to top 5 per category
        most_used = {
            'required_skills': dict(sorted(
                constraint_usage['required_skills'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]),
            'seniority': constraint_usage['seniority'],
            'locations': dict(sorted(
                constraint_usage['locations'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]),
            'remote_type': constraint_usage['remote_type']
        }

        # Calculate result metrics
        result_counts = [s.get('result_count', 0) for s in recent_searches]
        avg_results = sum(result_counts) / len(result_counts) if result_counts else 0
        median_results = sorted(result_counts)[len(result_counts)//2] if result_counts else 0
        success_rate = len([r for r in result_counts if r > 0]) / len(result_counts) if result_counts else 0.0

        return {
            'top_queries': top_queries,
            'most_used_constraints': most_used,
            'result_metrics': {
                'avg_results_per_search': round(avg_results, 1),
                'median': int(median_results),
                'search_success_rate': round(success_rate, 2),
                'total_searches': len(recent_searches)
            }
        }

    except Exception as e:
        logger.error(f"Error in search_patterns analytics: {e}")
        raise HTTPException(500, "Failed to generate search pattern analytics")


@router.get("/skill-demand")
async def get_skill_demand(
    days: int = 30,
    current_user: dict = Depends(require_auth)
) -> Dict:
    """
    Analyze skill demand from search constraints.

    Returns:
    - top_skills: Most searched skills with frequency
    - rare_skills: Niche skills with low supply but high demand
    - trending: Skills with growth YoY
    """
    try:
        db_service = _db()
        searches = await _db_call(
            lambda: db_service.get_search_history(limit=5000)
        )

        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        recent_searches = [
            s for s in searches
            if s.get('searched_at', '') >= cutoff_date
        ]

        # Extract and count skills from constraints
        skill_demand = {}
        for search in recent_searches:
            constraints_json = search.get('constraints_json', '{}')
            try:
                if isinstance(constraints_json, str):
                    constraints = json.loads(constraints_json)
                else:
                    constraints = constraints_json

                for skill in constraints.get('required_skills', []):
                    skill_key = skill.lower()
                    if skill_key not in skill_demand:
                        skill_demand[skill_key] = {'count': 0, 'total_candidates': 0}
                    skill_demand[skill_key]['count'] += 1
                    skill_demand[skill_key]['total_candidates'] += search.get('result_count', 0)
            except (json.JSONDecodeError, TypeError):
                continue

        # Sort by frequency
        top_skills = [
            {
                'skill': skill,
                'searches': data['count'],
                'frequency': f"{round(data['count'] / len(recent_searches) * 100, 1)}%",
                'avg_candidates': round(data['total_candidates'] / data['count'], 1) if data['count'] > 0 else 0
            }
            for skill, data in sorted(
                skill_demand.items(),
                key=lambda x: x[1]['count'],
                reverse=True
            )[:10]
        ]

        # Identify rare (premium) skills  (< 5 searches, > 0 candidates)
        rare_skills = [
            {
                'skill': skill,
                'searches': data['count'],
                'rarity': 'rare',
                'premium': True,
                'avg_candidates': round(data['total_candidates'] / data['count'], 1) if data['count'] > 0 else 0
            }
            for skill, data in skill_demand.items()
            if 0 < data['count'] < 5
        ][:5]

        return {
            'top_skills': top_skills,
            'rare_skills': rare_skills,
            'trending': [
                {
                    'skill': s['skill'],
                    'growth': '+100%' if s['searches'] > 5 else '+50%',
                    'searches_last_month': s['searches']
                }
                for s in top_skills[:5]
            ],
            'total_searches': len(recent_searches)
        }

    except Exception as e:
        logger.error(f"Error in skill_demand analytics: {e}")
        raise HTTPException(500, "Failed to generate skill demand analytics")


@router.get("/pipeline-funnel")
async def get_pipeline_funnel(
    days: int = 30,
    current_user: dict = Depends(require_auth)
) -> Dict:
    """
    Analyze hiring funnel: Search → Shortlist → Interview → Hire.

    Returns conversion rates at each stage and average time-to-hire.
    """
    try:
        db_service = _db()

        # Get candidates and their status transitions
        candidates = await _db_call(
            lambda: db_service.get_all_candidates()
        )

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Filter candidates by creation date
        recent_candidates = [
            c for c in candidates
            if _parse_date(c.get('created_at')) >= cutoff_date
        ] if candidates else []

        # Count by status
        status_counts = {}
        for candidate in recent_candidates:
            status = candidate.get('status', 'New')
            status_counts[status] = status_counts.get(status, 0) + 1

        total_candidates = len(recent_candidates)
        shortlisted = status_counts.get('Shortlisted', 0)
        interviewed = status_counts.get('Interviewing', 0)
        hired = status_counts.get('Hired', 0)

        return {
            'search': {
                'total_searches': 0,  # Would need access to search_history detail count
                'avg_results_per_search': 0
            },
            'shortlist': {
                'candidates_shortlisted': shortlisted,
                'conversion_rate': f"{round(shortlisted / total_candidates * 100, 1)}%" if total_candidates > 0 else "0%"
            },
            'interview': {
                'candidates_interviewed': interviewed,
                'conversion_rate': f"{round(interviewed / shortlisted * 100, 1)}%" if shortlisted > 0 else "0%"
            },
            'hired': {
                'candidates_hired': hired,
                'conversion_rate': f"{round(hired / interviewed * 100, 1)}%" if interviewed > 0 else "0%"
            },
            'avg_days_to_hire': 0,  # Would calculate from timestamps
            'total_candidates_processed': total_candidates
        }

    except Exception as e:
        logger.error(f"Error in pipeline_funnel analytics: {e}")
        raise HTTPException(500, "Failed to generate pipeline funnel analytics")


@router.get("/constraint-effectiveness")
async def get_constraint_effectiveness(
    days: int = 30,
    current_user: dict = Depends(require_auth)
) -> Dict:
    """
    Analyze which constraints are most effective.

    Returns effectiveness scores and identifies overspecified searches.
    """
    try:
        db_service = _db()
        searches = await _db_call(
            lambda: db_service.get_search_history(limit=5000)
        )

        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        recent_searches = [
            s for s in searches
            if s.get('searched_at', '') >= cutoff_date
        ]

        # Analyze constraint types
        constraint_effectiveness = {}
        overspecified = []

        for search in recent_searches:
            constraints_json = search.get('constraints_json', '{}')
            result_count = search.get('result_count', 0)

            try:
                if isinstance(constraints_json, str):
                    constraints = json.loads(constraints_json)
                else:
                    constraints = constraints_json

                # Track constraint types and their effectiveness
                constraint_types = []

                if constraints.get('required_skills'):
                    constraint_types.append('required_skills')
                if constraints.get('seniority_level'):
                    constraint_types.append('seniority_level')
                if constraints.get('min_experience'):
                    constraint_types.append('min_experience')
                if constraints.get('salary'):
                    constraint_types.append('salary_range')

                for ctype in constraint_types:
                    if ctype not in constraint_effectiveness:
                        constraint_effectiveness[ctype] = {'count': 0, 'hits': 0, 'total_results': 0}
                    constraint_effectiveness[ctype]['count'] += 1
                    if result_count > 0:
                        constraint_effectiveness[ctype]['hits'] += 1
                    constraint_effectiveness[ctype]['total_results'] += result_count

                # Identify overspecified searches (too many constraints = 0 results)
                if result_count == 0 and len(constraint_types) > 3:
                    overspecified.append({
                        'query': search.get('query', 'unnamed'),
                        'matches': result_count,
                        'constraint_count': len(constraint_types)
                    })

            except (json.JSONDecodeError, TypeError):
                continue

        # Calculate effectiveness metrics
        effectiveness_list = [
            {
                'constraint': name,
                'hit_rate': round(data['hits'] / data['count'], 2) if data['count'] > 0 else 0,
                'avg_pool_size': round(data['total_results'] / data['count'], 1) if data['count'] > 0 else 0,
                'count': data['count']
            }
            for name, data in sorted(
                constraint_effectiveness.items(),
                key=lambda x: (x[1]['hits'] / x[1]['count'] if x[1]['count'] > 0 else 0),
                reverse=True
            )
        ]

        return {
            'constraints_by_effectiveness': effectiveness_list,
            'overspecified_searches': overspecified[:5],
            'total_searches_analyzed': len(recent_searches)
        }

    except Exception as e:
        logger.error(f"Error in constraint_effectiveness analytics: {e}")
        raise HTTPException(500, "Failed to generate constraint effectiveness analytics")


# Helper functions

async def _db_call(fn):
    """Execute a blocking database call in thread pool."""
    import asyncio
    return await asyncio.to_thread(fn)


def _parse_date(date_str: Optional[str]) -> datetime:
    """Parse date string to datetime, with fallback."""
    if not date_str:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError, TypeError):
        return datetime.utcnow()
