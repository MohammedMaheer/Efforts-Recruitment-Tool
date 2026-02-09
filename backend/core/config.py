"""
Application Configuration with Type Safety and Validation
Following 12-factor app principles
"""
from functools import lru_cache
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
import os


class Settings(BaseSettings):
    """
    Application settings with environment variable support.
    All settings are validated and typed.
    """
    
    # Application
    app_name: str = "AI Recruiter Platform"
    app_version: str = "3.0.0"
    debug: bool = Field(default=True, description="Enable debug mode")
    environment: str = Field(default="development", description="Environment name")
    
    # Server
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")
    workers: int = Field(default=4, description="Number of worker processes")
    
    # Database
    database_url: str = Field(default="./recruitment.db", description="SQLite database path")
    db_pool_size: int = Field(default=10, description="Database connection pool size")
    db_timeout: float = Field(default=30.0, description="Database timeout in seconds")
    
    # AI Services
    ai_timeout: float = Field(default=8.0, description="AI request timeout")
    ai_analysis_timeout: float = Field(default=5.0, description="AI analysis timeout")
    use_local_ai: bool = Field(default=True, description="Use local AI (free) as primary")
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API key for fallback")
    openai_model: str = Field(default="gpt-3.5-turbo", description="OpenAI model to use")
    
    # Local LLM (Ollama)
    ollama_base_url: str = Field(default="http://localhost:11434", description="Ollama API URL")
    ollama_primary_model: str = Field(default="qwen2.5:7b", description="Primary LLM model for extraction")
    ollama_fast_model: str = Field(default="phi3.5", description="Fast model for simple tasks")
    ollama_reasoning_model: str = Field(default="llama3.1:8b", description="Reasoning model for analysis")
    llm_temperature: float = Field(default=0.1, description="LLM temperature (lower = more accurate)")
    llm_max_tokens: int = Field(default=4096, description="Max tokens for LLM responses")
    
    # Microsoft Graph (Email)
    microsoft_client_id: Optional[str] = Field(default=None)
    microsoft_client_secret: Optional[str] = Field(default=None)
    microsoft_tenant_id: Optional[str] = Field(default=None)
    email_address: Optional[str] = Field(default=None, description="Primary email for sync")
    
    # Email Sync
    auto_sync_enabled: bool = Field(default=True, description="Enable auto email sync")
    sync_interval_minutes: int = Field(default=15, description="Email sync interval")
    max_emails_per_sync: int = Field(default=100000, description="Max emails to fetch")
    
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
    
    # OpenAI (for GPT-4 skill extraction)
    openai_org_id: Optional[str] = Field(default=None, description="OpenAI Organization ID")
    gpt4_model: str = Field(default="gpt-4-turbo-preview", description="GPT-4 model for advanced analysis")
    
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
        return self.environment == "production"
    
    @property
    def is_development(self) -> bool:
        return self.environment == "development"
    
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
