"""
Dependency Injection Container
Clean separation of concerns with proper dependency management
"""
from functools import lru_cache
from typing import Generator, AsyncGenerator, Optional
from contextlib import asynccontextmanager

from fastapi import Depends, HTTPException, Header, status

from services.database_service import DatabaseService, get_db_service
from services.local_ai_service import LocalAIService, get_local_ai_service
from services.openai_service import OpenAIService, get_openai_service
from services.email_scraper import EmailScraperService, get_scraper_service
from services.resume_parser import ResumeParser
from services.matching_engine import MatchingEngine
from services.token_storage import TokenStorage, get_token_storage


# ============================================================================
# Authentication Dependencies
# ============================================================================

async def require_auth(authorization: Optional[str] = Header(None)) -> dict:
    """
    FastAPI dependency that enforces Bearer token authentication.
    Use as: current_user: dict = Depends(require_auth)
    Returns the authenticated user dict with id, email, name, role, etc.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization format. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    from services.auth_service import get_auth_service
    auth_service = get_auth_service()
    user = auth_service.verify_token(parts[1])
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user


async def require_admin(current_user: dict = Depends(require_auth)) -> dict:
    """
    FastAPI dependency that enforces admin role.
    Use as: current_user: dict = Depends(require_admin)
    Raises 403 if the authenticated user is not an admin.
    """
    if current_user.get('role') != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


async def optional_auth(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """
    FastAPI dependency that optionally validates auth.
    Returns user dict if valid token provided, None otherwise.
    Use for endpoints that work with or without authentication.
    """
    if not authorization:
        return None
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    
    from services.auth_service import get_auth_service
    auth_service = get_auth_service()
    return auth_service.verify_token(parts[1])


class ServiceContainer:
    """
    Centralized service container for dependency injection.
    Provides singleton instances of all services.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._db_service = None
        self._local_ai_service = None
        self._openai_service = None
        self._scraper_service = None
        self._resume_parser = None
        self._matching_engine = None
        self._token_storage = None
        self._initialized = True
    
    @property
    def db(self) -> DatabaseService:
        if self._db_service is None:
            self._db_service = get_db_service()
        return self._db_service
    
    @property
    def local_ai(self) -> LocalAIService:
        if self._local_ai_service is None:
            self._local_ai_service = get_local_ai_service()
        return self._local_ai_service
    
    @property
    def openai(self) -> OpenAIService:
        if self._openai_service is None:
            self._openai_service = get_openai_service()
        return self._openai_service
    
    @property
    def ai(self) -> LocalAIService:
        """Primary AI service (local, free)"""
        return self.local_ai
    
    @property
    def ai_fallback(self) -> OpenAIService:
        """Fallback AI service (OpenAI)"""
        return self.openai
    
    @property
    def scraper(self) -> EmailScraperService:
        if self._scraper_service is None:
            self._scraper_service = get_scraper_service()
        return self._scraper_service
    
    @property
    def resume_parser(self) -> ResumeParser:
        if self._resume_parser is None:
            self._resume_parser = ResumeParser()
        return self._resume_parser
    
    @property
    def matching_engine(self) -> MatchingEngine:
        if self._matching_engine is None:
            self._matching_engine = MatchingEngine()
        return self._matching_engine
    
    @property
    def token_storage(self) -> TokenStorage:
        if self._token_storage is None:
            self._token_storage = get_token_storage()
        return self._token_storage


# Singleton instance
_container = None


def get_container() -> ServiceContainer:
    """Get the service container singleton"""
    global _container
    if _container is None:
        _container = ServiceContainer()
    return _container


# FastAPI dependency functions
def get_db() -> DatabaseService:
    """FastAPI dependency for database service"""
    return get_container().db


def get_ai() -> LocalAIService:
    """FastAPI dependency for AI service"""
    return get_container().ai


def get_parser() -> ResumeParser:
    """FastAPI dependency for resume parser"""
    return get_container().resume_parser


def get_scraper() -> EmailScraperService:
    """FastAPI dependency for email scraper"""
    return get_container().scraper


def get_tokens() -> TokenStorage:
    """FastAPI dependency for token storage"""
    return get_container().token_storage
