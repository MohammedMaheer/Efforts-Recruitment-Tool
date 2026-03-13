"""
Shared dependencies, services, and state used across route modules.

All route files import from here instead of reaching into main.py globals.
This avoids circular imports and provides a single source of truth for
service instances and shared state.
"""
import os
import logging
import asyncio
import threading
from datetime import datetime
from cachetools import TTLCache

from core.config import get_settings
from core.dependencies import require_auth, optional_auth, require_admin

logger = logging.getLogger(__name__)

# Centralized settings
settings = get_settings()

# Configuration
DEBUG = settings.debug if not settings.is_production else False
AI_TIMEOUT = float(os.getenv('AI_TIMEOUT', os.getenv('AI_TIMEOUT_SECONDS', str(settings.ai_timeout))))
AI_ANALYSIS_TIMEOUT = float(os.getenv('AI_ANALYSIS_TIMEOUT', '60.0'))
MAX_CONCURRENT_REQUESTS = settings.max_concurrent_requests

# Performance: Response cache (5 minutes TTL) with thread-safe lock
response_cache = TTLCache(maxsize=1000, ttl=300)
_cache_lock = threading.Lock()

# Database write semaphore (prevents SQLite lock contention)
db_semaphore = asyncio.Semaphore(5)

# Login rate limiting
_login_attempts: dict = {}
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 900  # 15 minutes


def get_services():
    """
    Lazy-load and return all service singletons.
    This is called once during app startup and the results are stored.
    """
    from services.resume_parser import ResumeParser
    from services.matching_engine import MatchingEngine
    from services.email_parser import EmailParser
    from services.local_ai_service import get_local_ai_service
    from services.gemini_service import get_gemini_service
    from services.email_scraper import get_scraper_service
    from services.database_service import get_db_service
    from services.auth_service import get_auth_service
    from services.token_storage import get_token_storage

    resume_parser = ResumeParser()
    matching_engine = MatchingEngine()
    email_parser = EmailParser()
    local_ai_service = get_local_ai_service()
    gemini_service = get_gemini_service()
    scraper_service = get_scraper_service()
    db_service = get_db_service()
    auth_service = get_auth_service()

    # Determine primary AI service
    if gemini_service and gemini_service.available:
        ai_service = gemini_service
        logger.info("Using Gemini as PRIMARY AI service for candidate analysis")
    else:
        ai_service = local_ai_service
        logger.warning("Gemini not available — falling back to local_ai_service (reduced quality)")

    return {
        'resume_parser': resume_parser,
        'matching_engine': matching_engine,
        'email_parser': email_parser,
        'local_ai_service': local_ai_service,
        'gemini_service': gemini_service,
        'ai_service': ai_service,
        'scraper_service': scraper_service,
        'db_service': db_service,
        'auth_service': auth_service,
    }


# Global services dict — populated during app startup
_services: dict = {}


def init_services():
    """Initialize all services. Called once during app startup."""
    global _services
    _services = get_services()
    return _services


def get_db():
    """Get database service instance."""
    return _services['db_service']


def get_ai():
    """Get primary AI service instance."""
    return _services['ai_service']


def get_gemini():
    """Get Gemini service instance (may be None)."""
    return _services.get('gemini_service')


def get_scraper():
    """Get scraper service instance."""
    return _services['scraper_service']


def get_auth():
    """Get auth service instance."""
    return _services['auth_service']


def get_resume_parser():
    """Get resume parser instance."""
    return _services['resume_parser']


def get_matching_engine():
    """Get matching engine instance."""
    return _services['matching_engine']


def get_email_parser():
    """Get email parser instance."""
    return _services['email_parser']


def get_local_ai():
    """Get local AI service instance."""
    return _services.get('local_ai_service')
