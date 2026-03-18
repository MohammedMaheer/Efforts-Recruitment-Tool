"""
Application Configuration with Type Safety and Validation
Following 12-factor app principles
"""
from functools import lru_cache
from typing import List, Optional  # List kept for cors_origins_list / allowed_extensions_list
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
import os


class Settings(BaseSettings):
    """
    Application settings with environment variable support.
    All settings are validated and typed.
    """
    
    # Application
    app_name: str = "Efforts Solutions - AI Recruiter"
    app_version: str = "4.0.0"
    debug: bool = Field(default=False, description="Enable debug mode")
    environment: str = Field(default="development", description="Environment name")
    
    # Server
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")
    workers: int = Field(default=4, description="Number of worker processes")
    
    # Database
    database_url: str = Field(default="./recruitment.db", description="Database URL (PostgreSQL in prod, SQLite in dev)")
    db_pool_size: int = Field(default=25, description="Database connection pool size")
    db_timeout: float = Field(default=30.0, description="Database timeout in seconds")

    @field_validator('database_url')
    @classmethod
    def validate_database_url(cls, v):
        """Warn loudly if production is using SQLite (DATABASE_URL secret likely missing)."""
        if os.getenv('K_SERVICE') and not v.startswith('postgres'):
            import logging
            logging.getLogger(__name__).critical(
                "DATABASE_URL is not PostgreSQL on Cloud Run! "
                "Check that the DATABASE_URL secret is mounted correctly. "
                f"Current value prefix: {v[:20]}"
            )
        return v
    
    # AI Services
    ai_timeout: float = Field(default=30.0, description="AI request timeout")
    ai_analysis_timeout: float = Field(default=30.0, description="AI analysis timeout for LLM inference")

    # Google Gemini (cloud AI — optional, used when GEMINI_API_KEY is set)
    gemini_api_key: Optional[str] = Field(default=None, description="Google Gemini API key")
    gemini_model: str = Field(default="gemini-2.5-flash", description="Gemini model (2.5 Flash is fast & capable)")

    # Ollama (local LLM — primary when running locally)
    ollama_host: str = Field(default="http://localhost:11434", description="Ollama API base URL")
    ollama_model: str = Field(default="qwen2.5:14b", description="Ollama model for AI analysis")
    ollama_timeout: float = Field(default=120.0, description="Ollama request timeout in seconds")
    use_ollama: bool = Field(default=True, description="Use Ollama as primary AI service when available")
    
    # Microsoft Graph (Email)
    microsoft_client_id: Optional[str] = Field(default=None)
    microsoft_client_secret: Optional[str] = Field(default=None)
    microsoft_tenant_id: Optional[str] = Field(default=None)
    email_address: Optional[str] = Field(default=None, description="Primary email for sync")
    company_name: str = Field(default="Efforts Solutions", description="Company name for emails")
    recruiter_name: str = Field(default="HR Team", description="Recruiter name for emails")
    
    # Email Sync
    auto_sync_enabled: bool = Field(default=True, description="Enable auto email sync")
    sync_interval_minutes: int = Field(default=60, description="Email sync interval")
    max_emails_per_sync: int = Field(default=500, description="Max emails to fetch per sync cycle")
    
    # Twilio SMS
    twilio_account_sid: Optional[str] = Field(default=None, description="Twilio Account SID")
    twilio_auth_token: Optional[str] = Field(default=None, description="Twilio Auth Token")
    twilio_phone_number: Optional[str] = Field(default=None, description="Twilio Phone Number")
    
    # Google Calendar
    google_client_id: Optional[str] = Field(default=None, description="Google OAuth Client ID")
    google_client_secret: Optional[str] = Field(default=None, description="Google OAuth Client Secret")
    google_calendar_id: Optional[str] = Field(default="primary", description="Google Calendar ID")
    
    # Calendly
    calendly_api_key: Optional[str] = Field(default=None, description="Calendly API Key")
    calendly_user_uri: Optional[str] = Field(default=None, description="Calendly User URI")
    calendly_event_type: Optional[str] = Field(default=None, description="Calendly Event Type URI")
    
    # Performance
    max_concurrent_requests: int = Field(default=100, description="Max concurrent API requests")
    cache_ttl_seconds: int = Field(default=300, description="Response cache TTL")
    cache_max_size: int = Field(default=1000, description="Max cache entries")
    
    # CORS - Use str type to avoid pydantic-settings JSON parsing
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        description="Allowed CORS origins (comma-separated)"
    )
    
    # Rate Limiting
    rate_limit_requests: int = Field(default=1000, description="Requests per minute")
    rate_limit_window: int = Field(default=60, description="Rate limit window in seconds")
    
    # File Upload
    max_file_size_mb: int = Field(default=10, description="Max upload file size in MB")
    allowed_extensions: str = Field(default="pdf,docx", description="Allowed file types (comma-separated)")
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Get CORS origins as a list"""
        return [origin.strip() for origin in self.cors_origins.split(',') if origin.strip()]
    
    @property
    def allowed_extensions_list(self) -> List[str]:
        """Get allowed extensions as a list"""
        return [ext.strip() for ext in self.allowed_extensions.split(',') if ext.strip()]
    
    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024
    
    @property
    def is_production(self) -> bool:
        """Detect production: explicit env var or Cloud Run environment"""
        if self.environment == "production":
            return True
        # Auto-detect GCP Cloud Run (K_SERVICE is always set in Cloud Run)
        return bool(os.getenv('K_SERVICE'))
    
    @property
    def is_development(self) -> bool:
        return not self.is_production
    
    @property
    def ai_tier_order(self) -> list:
        if self.use_ollama:
            return ["ollama", "gemini", "keyword"]
        return ["gemini", "keyword"]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached application settings.
    Uses lru_cache to ensure singleton pattern.
    """
    return Settings()


# Convenience alias
settings = get_settings()
