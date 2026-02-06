"""
Dependency Injection Container
Clean separation of concerns with proper dependency management
"""
from functools import lru_cache
from typing import Generator, AsyncGenerator
from contextlib import asynccontextmanager

from services.database_service import DatabaseService, get_db_service
from services.local_ai_service import LocalAIService, get_local_ai_service
from services.openai_service import OpenAIService, get_openai_service
from services.email_scraper import EmailScraperService, get_scraper_service
from services.resume_parser import ResumeParser
from services.matching_engine import MatchingEngine
from services.token_storage import TokenStorage, get_token_storage


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
