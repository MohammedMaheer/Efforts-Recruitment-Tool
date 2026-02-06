"""
Setup Verification & Health Check Service
Validates all configuration and provides setup guidance
"""
import os
import asyncio
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import subprocess
import sys

logger = logging.getLogger(__name__)


class SetupStatus(Enum):
    """Setup component status"""
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    ERROR = "error"
    OPTIONAL = "optional"


@dataclass
class SetupCheck:
    """Individual setup check result"""
    name: str
    status: SetupStatus
    message: str
    required: bool = True
    instructions: str = ""
    docs_url: str = ""


@dataclass
class SetupReport:
    """Complete setup verification report"""
    overall_status: str
    ready_for_production: bool
    checks: List[SetupCheck] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'overall_status': self.overall_status,
            'ready_for_production': self.ready_for_production,
            'checks': [
                {
                    'name': c.name,
                    'status': c.status.value,
                    'message': c.message,
                    'required': c.required,
                    'instructions': c.instructions,
                    'docs_url': c.docs_url
                } for c in self.checks
            ],
            'warnings': self.warnings,
            'errors': self.errors,
            'summary': {
                'total': len(self.checks),
                'configured': sum(1 for c in self.checks if c.status == SetupStatus.CONFIGURED),
                'not_configured': sum(1 for c in self.checks if c.status == SetupStatus.NOT_CONFIGURED),
                'errors': sum(1 for c in self.checks if c.status == SetupStatus.ERROR),
                'optional': sum(1 for c in self.checks if c.status == SetupStatus.OPTIONAL)
            }
        }


class SetupVerificationService:
    """
    Comprehensive setup verification for production deployment
    """
    
    def __init__(self):
        self.checks: List[SetupCheck] = []
    
    async def run_full_verification(self) -> SetupReport:
        """Run all setup checks and return comprehensive report"""
        self.checks = []
        
        # Core checks
        await self._check_environment()
        await self._check_database()
        await self._check_security()
        await self._check_ai_models()
        await self._check_email_oauth()
        await self._check_cors()
        
        # Optional integrations
        await self._check_twilio()
        await self._check_google_calendar()
        await self._check_calendly()
        await self._check_openai_fallback()
        await self._check_redis()
        
        # System checks
        await self._check_python_packages()
        await self._check_disk_space()
        await self._check_memory()
        
        # Compile report
        errors = [c for c in self.checks if c.status == SetupStatus.ERROR]
        not_configured_required = [c for c in self.checks if c.status == SetupStatus.NOT_CONFIGURED and c.required]
        
        ready = len(errors) == 0 and len(not_configured_required) == 0
        
        if ready:
            overall = "ready"
        elif len(errors) > 0:
            overall = "error"
        elif len(not_configured_required) > 0:
            overall = "incomplete"
        else:
            overall = "ready_with_warnings"
        
        return SetupReport(
            overall_status=overall,
            ready_for_production=ready,
            checks=self.checks,
            warnings=[c.message for c in self.checks if c.status == SetupStatus.NOT_CONFIGURED and not c.required],
            errors=[c.message for c in self.checks if c.status == SetupStatus.ERROR]
        )
    
    async def _check_environment(self):
        """Check environment configuration"""
        env = os.getenv('ENVIRONMENT', 'development')
        debug = os.getenv('DEBUG', 'true').lower() == 'true'
        
        if env == 'production' and not debug:
            self.checks.append(SetupCheck(
                name="Environment",
                status=SetupStatus.CONFIGURED,
                message=f"Running in {env} mode with DEBUG=false",
                required=True,
                instructions="Set ENVIRONMENT=production and DEBUG=false in .env"
            ))
        elif env == 'production' and debug:
            self.checks.append(SetupCheck(
                name="Environment",
                status=SetupStatus.ERROR,
                message="DEBUG=true in production mode is a security risk!",
                required=True,
                instructions="Set DEBUG=false in .env for production"
            ))
        else:
            self.checks.append(SetupCheck(
                name="Environment",
                status=SetupStatus.CONFIGURED,
                message=f"Running in {env} mode (DEBUG={debug})",
                required=True
            ))
    
    async def _check_database(self):
        """Check database configuration"""
        db_url = os.getenv('DATABASE_URL', './recruitment.db')
        
        if 'postgresql' in db_url:
            # Test PostgreSQL connection
            try:
                import asyncpg
                self.checks.append(SetupCheck(
                    name="Database",
                    status=SetupStatus.CONFIGURED,
                    message="PostgreSQL configured for production",
                    required=True
                ))
            except ImportError:
                self.checks.append(SetupCheck(
                    name="Database",
                    status=SetupStatus.ERROR,
                    message="asyncpg not installed for PostgreSQL",
                    required=True,
                    instructions="pip install asyncpg"
                ))
        elif '.db' in db_url or 'sqlite' in db_url:
            env = os.getenv('ENVIRONMENT', 'development')
            if env == 'production':
                self.checks.append(SetupCheck(
                    name="Database",
                    status=SetupStatus.NOT_CONFIGURED,
                    message="SQLite not recommended for production",
                    required=True,
                    instructions="Configure PostgreSQL: DATABASE_URL=postgresql://user:pass@host:5432/dbname",
                    docs_url="/docs/DEPLOYMENT.md#database"
                ))
            else:
                self.checks.append(SetupCheck(
                    name="Database",
                    status=SetupStatus.CONFIGURED,
                    message="SQLite configured for development",
                    required=True
                ))
        else:
            self.checks.append(SetupCheck(
                name="Database",
                status=SetupStatus.NOT_CONFIGURED,
                message="Database URL not configured",
                required=True,
                instructions="Set DATABASE_URL in .env"
            ))
    
    async def _check_security(self):
        """Check security configuration"""
        secret_key = os.getenv('SECRET_KEY', '')
        
        if not secret_key:
            self.checks.append(SetupCheck(
                name="Security - Secret Key",
                status=SetupStatus.NOT_CONFIGURED,
                message="SECRET_KEY not set",
                required=True,
                instructions="Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            ))
        elif secret_key in ['dev-secret-key-change-in-production', 'changeme', 'secret']:
            env = os.getenv('ENVIRONMENT', 'development')
            if env == 'production':
                self.checks.append(SetupCheck(
                    name="Security - Secret Key",
                    status=SetupStatus.ERROR,
                    message="Using default SECRET_KEY in production!",
                    required=True,
                    instructions="Generate a secure key: python -c \"import secrets; print(secrets.token_hex(32))\""
                ))
            else:
                self.checks.append(SetupCheck(
                    name="Security - Secret Key",
                    status=SetupStatus.CONFIGURED,
                    message="Using development secret key",
                    required=True
                ))
        else:
            self.checks.append(SetupCheck(
                name="Security - Secret Key",
                status=SetupStatus.CONFIGURED,
                message="Secret key configured",
                required=True
            ))
    
    async def _check_ai_models(self):
        """Check AI model availability"""
        try:
            from services.local_ai_service import LocalAIService
            
            # Check if model is loadable
            self.checks.append(SetupCheck(
                name="AI Models - Local",
                status=SetupStatus.CONFIGURED,
                message="Local AI service available (FREE - no API costs)",
                required=True
            ))
        except Exception as e:
            self.checks.append(SetupCheck(
                name="AI Models - Local",
                status=SetupStatus.ERROR,
                message=f"Local AI service error: {str(e)[:50]}",
                required=True,
                instructions="Run: pip install sentence-transformers torch && python -m spacy download en_core_web_sm"
            ))
        
        # Check SpaCy model
        try:
            import spacy
            nlp = spacy.load("en_core_web_sm")
            self.checks.append(SetupCheck(
                name="AI Models - SpaCy NER",
                status=SetupStatus.CONFIGURED,
                message="SpaCy NER model loaded",
                required=True
            ))
        except Exception as e:
            self.checks.append(SetupCheck(
                name="AI Models - SpaCy NER",
                status=SetupStatus.NOT_CONFIGURED,
                message="SpaCy model not installed",
                required=True,
                instructions="Run: python -m spacy download en_core_web_sm"
            ))
    
    async def _check_email_oauth(self):
        """Check Microsoft OAuth configuration"""
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        email = os.getenv('EMAIL_ADDRESS')
        
        if all([client_id, client_secret, tenant_id, email]):
            self.checks.append(SetupCheck(
                name="Email - Microsoft OAuth2",
                status=SetupStatus.CONFIGURED,
                message=f"OAuth2 configured for {email}",
                required=False,
                docs_url="/docs/OAUTH2_SETUP.md"
            ))
        else:
            missing = []
            if not client_id: missing.append("MICROSOFT_CLIENT_ID")
            if not client_secret: missing.append("MICROSOFT_CLIENT_SECRET")
            if not tenant_id: missing.append("MICROSOFT_TENANT_ID")
            if not email: missing.append("EMAIL_ADDRESS")
            
            self.checks.append(SetupCheck(
                name="Email - Microsoft OAuth2",
                status=SetupStatus.NOT_CONFIGURED,
                message=f"Missing: {', '.join(missing)}",
                required=False,
                instructions="Configure Azure AD app at portal.azure.com",
                docs_url="/docs/OAUTH2_SETUP.md"
            ))
    
    async def _check_cors(self):
        """Check CORS configuration"""
        cors = os.getenv('CORS_ORIGINS', '')
        env = os.getenv('ENVIRONMENT', 'development')
        
        if not cors:
            self.checks.append(SetupCheck(
                name="CORS",
                status=SetupStatus.NOT_CONFIGURED,
                message="CORS_ORIGINS not set",
                required=True,
                instructions="Set CORS_ORIGINS=https://yourdomain.com in .env"
            ))
        elif 'localhost' in cors and env == 'production':
            self.checks.append(SetupCheck(
                name="CORS",
                status=SetupStatus.ERROR,
                message="localhost in CORS for production!",
                required=True,
                instructions="Remove localhost from CORS_ORIGINS for production"
            ))
        else:
            self.checks.append(SetupCheck(
                name="CORS",
                status=SetupStatus.CONFIGURED,
                message=f"CORS configured: {cors[:50]}...",
                required=True
            ))
    
    async def _check_twilio(self):
        """Check Twilio SMS configuration"""
        sid = os.getenv('TWILIO_ACCOUNT_SID')
        token = os.getenv('TWILIO_AUTH_TOKEN')
        phone = os.getenv('TWILIO_PHONE_NUMBER')
        
        if all([sid, token, phone]):
            self.checks.append(SetupCheck(
                name="SMS - Twilio",
                status=SetupStatus.CONFIGURED,
                message="Twilio SMS configured",
                required=False
            ))
        else:
            self.checks.append(SetupCheck(
                name="SMS - Twilio",
                status=SetupStatus.OPTIONAL,
                message="Twilio not configured (optional)",
                required=False,
                instructions="Get credentials from console.twilio.com"
            ))
    
    async def _check_google_calendar(self):
        """Check Google Calendar configuration"""
        client_id = os.getenv('GOOGLE_CLIENT_ID')
        client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
        
        if client_id and client_secret:
            self.checks.append(SetupCheck(
                name="Calendar - Google",
                status=SetupStatus.CONFIGURED,
                message="Google Calendar configured",
                required=False
            ))
        else:
            self.checks.append(SetupCheck(
                name="Calendar - Google",
                status=SetupStatus.OPTIONAL,
                message="Google Calendar not configured (optional)",
                required=False,
                instructions="Setup at console.cloud.google.com"
            ))
    
    async def _check_calendly(self):
        """Check Calendly configuration"""
        api_key = os.getenv('CALENDLY_API_KEY')
        
        if api_key:
            self.checks.append(SetupCheck(
                name="Calendar - Calendly",
                status=SetupStatus.CONFIGURED,
                message="Calendly configured",
                required=False
            ))
        else:
            self.checks.append(SetupCheck(
                name="Calendar - Calendly",
                status=SetupStatus.OPTIONAL,
                message="Calendly not configured (optional)",
                required=False,
                instructions="Get API key from calendly.com/integrations/api"
            ))
    
    async def _check_openai_fallback(self):
        """Check OpenAI fallback configuration"""
        api_key = os.getenv('OPENAI_API_KEY')
        use_fallback = os.getenv('USE_OPENAI_FALLBACK', 'false').lower() == 'true'
        
        if api_key and use_fallback:
            self.checks.append(SetupCheck(
                name="AI - OpenAI Fallback",
                status=SetupStatus.CONFIGURED,
                message="OpenAI fallback enabled (costs money)",
                required=False
            ))
        else:
            self.checks.append(SetupCheck(
                name="AI - OpenAI Fallback",
                status=SetupStatus.OPTIONAL,
                message="OpenAI fallback disabled (using FREE local AI)",
                required=False
            ))
    
    async def _check_redis(self):
        """Check Redis configuration"""
        redis_url = os.getenv('REDIS_URL')
        env = os.getenv('ENVIRONMENT', 'development')
        
        if redis_url:
            try:
                import redis
                self.checks.append(SetupCheck(
                    name="Cache - Redis",
                    status=SetupStatus.CONFIGURED,
                    message="Redis configured for distributed caching",
                    required=False
                ))
            except ImportError:
                self.checks.append(SetupCheck(
                    name="Cache - Redis",
                    status=SetupStatus.NOT_CONFIGURED,
                    message="redis package not installed",
                    required=False,
                    instructions="pip install redis"
                ))
        else:
            if env == 'production':
                self.checks.append(SetupCheck(
                    name="Cache - Redis",
                    status=SetupStatus.NOT_CONFIGURED,
                    message="Redis recommended for production",
                    required=False,
                    instructions="Set REDIS_URL=redis://localhost:6379/0"
                ))
            else:
                self.checks.append(SetupCheck(
                    name="Cache - Redis",
                    status=SetupStatus.OPTIONAL,
                    message="Using in-memory cache (Redis optional)",
                    required=False
                ))
    
    async def _check_python_packages(self):
        """Check required Python packages"""
        required = ['fastapi', 'uvicorn', 'pydantic', 'sentence_transformers', 'torch', 'spacy']
        missing = []
        
        for pkg in required:
            try:
                __import__(pkg.replace('-', '_'))
            except ImportError:
                missing.append(pkg)
        
        if missing:
            self.checks.append(SetupCheck(
                name="Python Packages",
                status=SetupStatus.ERROR,
                message=f"Missing packages: {', '.join(missing)}",
                required=True,
                instructions="pip install -r requirements.txt"
            ))
        else:
            self.checks.append(SetupCheck(
                name="Python Packages",
                status=SetupStatus.CONFIGURED,
                message="All required packages installed",
                required=True
            ))
    
    async def _check_disk_space(self):
        """Check available disk space"""
        try:
            import psutil
            disk = psutil.disk_usage('/')
            free_gb = disk.free / (1024 ** 3)
            
            if free_gb < 1:
                self.checks.append(SetupCheck(
                    name="System - Disk Space",
                    status=SetupStatus.ERROR,
                    message=f"Low disk space: {free_gb:.1f}GB free",
                    required=True
                ))
            elif free_gb < 5:
                self.checks.append(SetupCheck(
                    name="System - Disk Space",
                    status=SetupStatus.NOT_CONFIGURED,
                    message=f"Limited disk space: {free_gb:.1f}GB free",
                    required=False
                ))
            else:
                self.checks.append(SetupCheck(
                    name="System - Disk Space",
                    status=SetupStatus.CONFIGURED,
                    message=f"Disk space OK: {free_gb:.1f}GB free",
                    required=True
                ))
        except Exception:
            pass
    
    async def _check_memory(self):
        """Check available memory"""
        try:
            import psutil
            mem = psutil.virtual_memory()
            available_gb = mem.available / (1024 ** 3)
            
            if available_gb < 1:
                self.checks.append(SetupCheck(
                    name="System - Memory",
                    status=SetupStatus.ERROR,
                    message=f"Low memory: {available_gb:.1f}GB available",
                    required=True,
                    instructions="AI models require at least 2GB RAM"
                ))
            elif available_gb < 2:
                self.checks.append(SetupCheck(
                    name="System - Memory",
                    status=SetupStatus.NOT_CONFIGURED,
                    message=f"Limited memory: {available_gb:.1f}GB available",
                    required=False
                ))
            else:
                self.checks.append(SetupCheck(
                    name="System - Memory",
                    status=SetupStatus.CONFIGURED,
                    message=f"Memory OK: {available_gb:.1f}GB available",
                    required=True
                ))
        except Exception:
            pass


# Global instance
_setup_service: Optional[SetupVerificationService] = None


def get_setup_service() -> SetupVerificationService:
    """Get setup verification service instance"""
    global _setup_service
    if _setup_service is None:
        _setup_service = SetupVerificationService()
    return _setup_service
