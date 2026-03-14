"""Route module: settings. Auto-extracted from main_legacy.py."""
import os
import json
import asyncio
import logging
import time
import re
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends, UploadFile, File, Body, Query
from fastapi.responses import Response, JSONResponse, StreamingResponse, RedirectResponse

from core.config import get_settings
from core.dependencies import require_auth, optional_auth, require_admin

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(tags=["settings"])

scraper_task = None  # Module-level scraper task handle




# ---- Service accessors (lazy imports to avoid circular deps) ----

def _db():
    from api.deps import get_db
    return get_db()

def _ai():
    from api.deps import get_ai
    return get_ai()

def _gemini():
    from api.deps import get_gemini
    return get_gemini()

def _scraper():
    from api.deps import get_scraper
    return get_scraper()

def _resume_parser():
    from api.deps import get_resume_parser
    return get_resume_parser()

def _email_parser():
    from api.deps import get_email_parser
    return get_email_parser()

def _auth_svc():
    from api.deps import get_auth
    return get_auth()

def _matching_engine():
    from api.deps import get_matching_engine
    return get_matching_engine()

def _cache():
    from api.deps import response_cache
    return response_cache

def _get_cache_lock():
    from api.deps import _cache_lock
    return _cache_lock

def _deps():
    """Get deps module for constants."""
    import api.deps as deps
    return deps


@router.get("/")
async def root():
    return {
        "message": _settings.app_name,
        "version": _settings.app_version,
        "status": "operational",
        "performance": {
            "max_concurrent_requests": _deps().MAX_CONCURRENT_REQUESTS,
            "ai_timeout": _deps().AI_TIMEOUT,
            "cache_enabled": True,
            "connection_pooling": True
        },
        "features": [
            "Automated email scraping (Gmail + MS365)",
            "AI-powered candidate extraction",
            "Smart AI tier fallback (Gemini -> Ollama -> Keyword)",
            "High-load optimized (100+ concurrent)",
            "Response caching (5min TTL)",
            "Connection pooling (50 max)",
            "Auto job categorization",
            "Duplicate detection"
        ]
    }



@router.get("/version")
async def version():
    return {
        "version": os.getenv('MODEL_VERSION', _settings.app_version),
        "deployed": datetime.now().strftime('%Y-%m-%d'),
        "environment": "production" if _settings.is_production else "development"
    }



@router.get("/health")
async def health_check():
    """Health check endpoint for monitoring — includes DB connectivity"""
    db_ok = False
    candidate_count = 0
    try:
        def _health_db_check():
            with _db().get_connection() as _check_conn:
                cursor = _check_conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM candidates WHERE is_active = 1")
                return cursor.fetchone()[0]
        candidate_count = await asyncio.to_thread(_health_db_check)
        db_ok = True
    except Exception as e:
        logger.warning(f"Health check DB probe failed: {e}")

    try:
        import psutil
        system_info = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage('/').percent
        }
    except Exception:
        system_info = {"status": "unavailable"}
    
    status = "healthy" if db_ok else "degraded"
    result = {
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "version": os.getenv('MODEL_VERSION', _settings.app_version),
        "database": {"connected": db_ok, "candidates": candidate_count},
        "scraper_running": scraper_task is not None and not scraper_task.done(),
        "system": system_info,
        "cache": {
            "response_cache_size": len(_cache()),
            "ai_embedding_cache": len(_ai().embedding_cache) if hasattr(_ai(), 'embedding_cache') else 0
        }
    }
    return JSONResponse(content=result, status_code=200 if db_ok else 503)



@router.get("/api/setup/verify")
async def verify_setup(current_user: dict = Depends(require_auth)):
    """
    Run comprehensive setup verification
    Returns detailed status of all configuration items
    """
    try:
        from services.setup_service import get_setup_service
        service = get_setup_service()
        report = await service.run_full_verification()
        return report.to_dict()
    except Exception as e:
        logger.error(f"Setup verification error: {e}")
        return {
            "overall_status": "error",
            "ready_for_production": False,
            "error": "Setup verification failed"
        }




@router.get("/api/setup/status")
async def get_setup_status(current_user: dict = Depends(require_auth)):
    """
    Get quick setup status summary
    """
    env = os.getenv('ENVIRONMENT', 'development')
    
    return {
        "environment": env,
        "is_production": env == 'production',
        "debug": os.getenv('DEBUG', 'true').lower() == 'true',
        "database": "postgresql" if "postgresql" in os.getenv('DATABASE_URL', '') else "sqlite",
        "ai_mode": "gemini" if _gemini() and _gemini().available else "local (free)",
        "email_oauth": bool(os.getenv('MICROSOFT_CLIENT_ID')),
        "sms_enabled": bool(os.getenv('TWILIO_ACCOUNT_SID')),
        "calendar_enabled": bool(os.getenv('GOOGLE_CLIENT_ID') or os.getenv('CALENDLY_API_KEY')),
        "redis_enabled": bool(os.getenv('REDIS_URL')),
        "version": os.getenv('APP_VERSION', '4.1.0')
    }




@router.get("/api/setup/instructions")
async def get_setup_instructions(current_user: dict = Depends(require_auth)):
    """
    Get detailed setup instructions for each component
    """
    return {
        "sections": [
            {
                "id": "quick_start",
                "title": "Quick Start",
                "description": "Get the platform running in 5 minutes",
                "steps": [
                    "1. Copy backend/.env.example to backend/.env",
                    "2. Run: cd backend && pip install -r requirements.txt",
                    "3. Download SpaCy model: python -m spacy download en_core_web_sm",
                    "4. Start backend: python main.py",
                    "5. In another terminal: npm install && npm run dev",
                    "6. Open http://localhost:5173"
                ]
            },
            {
                "id": "email_oauth",
                "title": "Email Integration (Microsoft OAuth2)",
                "description": "Connect Outlook/Office365 for automatic email sync",
                "required": False,
                "steps": [
                    "1. Go to portal.azure.com -> Azure Active Directory -> App registrations",
                    "2. Click 'New registration' with name 'Efforts Solutions AI Recruiter'",
                    "3. Set redirect URI: http://localhost:5173/email (Web type)",
                    "4. Go to 'Certificates & secrets' -> New client secret",
                    "5. Go to 'API permissions' -> Add: Mail.Read, Mail.ReadWrite, Mail.Send, User.Read, offline_access",
                    "6. Copy Application ID, Directory ID, and Secret to .env",
                    "7. Set EMAIL_ADDRESS to your Outlook email"
                ],
                "env_vars": [
                    "MICROSOFT_CLIENT_ID=your_application_id",
                    "MICROSOFT_CLIENT_SECRET=your_secret",
                    "MICROSOFT_TENANT_ID=your_directory_id",
                    "EMAIL_ADDRESS=your@email.com"
                ],
                "docs_url": "/docs/OAUTH2_SETUP.md"
            },
            {
                "id": "ai_models",
                "title": "AI Models",
                "description": "Local AI runs FREE with no API costs",
                "required": True,
                "steps": [
                    "1. Install sentence-transformers: pip install sentence-transformers",
                    "2. Install SpaCy: pip install spacy",
                    "3. Download SpaCy model: python -m spacy download en_core_web_sm",
                    "4. First run will download ~420MB AI model (one-time)",
                    "5. Set GEMINI_API_KEY for cloud AI inference"
                ],
                "env_vars": [
                    "USE_LOCAL_AI=true",
                    "LOCAL_AI_MODEL=all-mpnet-base-v2",
                    "GEMINI_API_KEY=your-gemini-key"
                ]
            },
            {
                "id": "production",
                "title": "Production Deployment",
                "description": "Deploy for production use",
                "required": False,
                "steps": [
                    "1. Set ENVIRONMENT=production and DEBUG=false",
                    "2. Generate secure SECRET_KEY: python -c \"import secrets; print(secrets.token_hex(32))\"",
                    "3. Configure PostgreSQL: DATABASE_URL=postgresql://user:pass@host:5432/dbname",
                    "4. Set CORS_ORIGINS to your production domain",
                    "5. Optional: Configure Redis for distributed caching",
                    "6. Use gunicorn: gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker",
                    "7. Set up reverse proxy (nginx) with SSL"
                ],
                "env_vars": [
                    "ENVIRONMENT=production",
                    "DEBUG=false",
                    "SECRET_KEY=your_64_char_hex_string",
                    "DATABASE_URL=postgresql://...",
                    "CORS_ORIGINS=https://yourdomain.com"
                ],
                "docs_url": "/docs/DEPLOYMENT.md"
            },
            {
                "id": "twilio",
                "title": "SMS Notifications (Twilio)",
                "description": "Send SMS notifications to candidates",
                "required": False,
                "steps": [
                    "1. Create account at twilio.com",
                    "2. Get Account SID and Auth Token from console",
                    "3. Get or buy a phone number",
                    "4. Add credentials to .env"
                ],
                "env_vars": [
                    "TWILIO_ACCOUNT_SID=your_sid",
                    "TWILIO_AUTH_TOKEN=your_token",
                    "TWILIO_PHONE_NUMBER=+1234567890"
                ]
            },
            {
                "id": "google_calendar",
                "title": "Google Calendar",
                "description": "Schedule interviews via Google Calendar",
                "required": False,
                "steps": [
                    "1. Go to console.cloud.google.com",
                    "2. Create new project or select existing",
                    "3. Enable Google Calendar API",
                    "4. Create OAuth 2.0 credentials",
                    "5. Add credentials to .env"
                ],
                "env_vars": [
                    "GOOGLE_CLIENT_ID=your_client_id",
                    "GOOGLE_CLIENT_SECRET=your_secret",
                    "GOOGLE_CALENDAR_ID=primary"
                ]
            },
            {
                "id": "calendly",
                "title": "Calendly Integration",
                "description": "Use Calendly for interview scheduling",
                "required": False,
                "steps": [
                    "1. Go to calendly.com/integrations/api",
                    "2. Generate Personal Access Token",
                    "3. Get your User URI and Event Type URI",
                    "4. Add to .env"
                ],
                "env_vars": [
                    "CALENDLY_API_KEY=your_token",
                    "CALENDLY_USER_URI=https://api.calendly.com/users/...",
                    "CALENDLY_EVENT_TYPE=https://api.calendly.com/event_types/..."
                ]
            }
        ]
    }




@router.post("/api/setup/test-connection/{service}")
async def test_service_connection(service: str, current_user: dict = Depends(require_auth)):
    """
    Test connection to a specific service
    """
    results = {"service": service, "status": "unknown"}
    
    if service == "database":
        try:
            count = await asyncio.to_thread(_db().get_total_candidates)
            results = {"service": service, "status": "connected", "candidate_count": count}
        except Exception as e:
            results = {"service": service, "status": "error", "error": "Database connection failed"}
    
    elif service == "email":
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        if client_id:
            results = {"service": service, "status": "configured", "client_id": client_id[:8] + "..."}
        else:
            results = {"service": service, "status": "not_configured"}
    
    elif service == "ai":
        try:
            test_result = await _ai().analyze_candidate("Software engineer with 5 years Python experience")
            results = {"service": service, "status": "working", "sample_score": test_result.get('quality_score')}
        except Exception as e:
            results = {"service": service, "status": "error", "error": "AI service unavailable"}
    
    elif service == "sms":
        if os.getenv('TWILIO_ACCOUNT_SID'):
            results = {"service": service, "status": "configured"}
        else:
            results = {"service": service, "status": "not_configured"}
    
    return results



@router.post("/api/scraper/start")
async def start_scraper(background_tasks: BackgroundTasks, current_user: dict = Depends(require_admin)):
    """Start the email scraper manually"""
    global scraper_task
    if scraper_task and not scraper_task.done():
        return {"message": "Scraper already running"}
    
    scraper_task = asyncio.create_task(_scraper().run_continuous_scraper(db_service=_db()))
    return {"message": "Email scraper started"}



@router.post("/api/scraper/stop")
async def stop_scraper(current_user: dict = Depends(require_admin)):
    """Stop the email scraper"""
    global scraper_task
    if scraper_task:
        scraper_task.cancel()
        return {"message": "Email scraper stopped"}
    return {"message": "Scraper not running"}



@router.get("/api/scraper/status")
async def scraper_status(current_user: dict = Depends(require_auth)):
    """Get scraper status for all accounts"""
    accounts_status = []
    for account in _scraper().email_accounts:
        accounts_status.append({
            "name": account.name,
            "email": account.email,
            "server": account.server,
            "processed_count": account.processed_count,
            "last_check": account.last_check.isoformat() if account.last_check else None
        })
    
    return {
        "running": scraper_task is not None and not scraper_task.done(),
        "total_accounts": len(_scraper().email_accounts),
        "accounts": accounts_status,
        "total_processed": len(_scraper().processed_message_ids),
        "process_all_history": _scraper().process_all_history
    }



@router.post("/api/scraper/process-now")
async def trigger_manual_scrape(
    process_all: bool = False,
    max_emails: int = 0,
    days_back: int = 0,
    current_user: dict = Depends(require_admin)
):
    """
    Manually trigger email scraping.
    process_all=True: Process ALL historical emails
    max_emails: Max emails to fetch (0 = no limit)
    days_back: Only fetch emails from last N days (0 = no limit)
    """
    try:
        total_emails = 0
        total_candidates = 0
        results_by_account = []
        
        for account in _scraper().email_accounts:
            try:
                mail = await asyncio.to_thread(_scraper().connect_to_inbox, account)
                if not mail:
                    results_by_account.append({
                        "account": account.name,
                        "error": "Connection failed"
                    })
                    continue
                
                emails = await _scraper().fetch_emails(mail, process_all=process_all)
                
                candidates = []
                for email_data in emails:
                    candidate = await _scraper().extract_candidate_from_email(email_data)
                    if candidate:
                        candidates.append(candidate)
                        
                        # Save to database
                        existing = await asyncio.to_thread(_db().get_candidate_by_email, candidate['email'])
                        if existing:
                            await asyncio.to_thread(_db().update_candidate, candidate)
                        else:
                            await asyncio.to_thread(_db().insert_candidate, candidate)
                
                await asyncio.to_thread(mail.logout)
                
                total_emails += len(emails)
                total_candidates += len(candidates)
                
                results_by_account.append({
                    "account": account.name,
                    "email": account.email,
                    "emails_found": len(emails),
                    "candidates_extracted": len(candidates)
                })
                
            except Exception as e:
                results_by_account.append({
                    "account": account.name,
                    "error": str(e)
                })
        
        return {
            "mode": "ALL emails" if process_all else "NEW emails only",
            "total_accounts": len(_scraper().email_accounts),
            "total_emails_found": total_emails,
            "total_candidates_extracted": total_candidates,
            "accounts": results_by_account
        }
    except Exception as e:
        logger.error(f"Scraping error: {e}")
        raise HTTPException(500, "Email scraping failed. Check server logs for details.")




@router.get("/api/stats/live")
async def get_live_stats(current_user: dict = Depends(require_auth)):
    """
    Get real-time statistics for dashboard updates.
    Lightweight endpoint for frequent polling.
    """
    try:
        def _get_live_stats_db():
            with _db().get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM candidates WHERE is_active = 1")
                total = cursor.fetchone()[0]
                
                cursor.execute("""
                    SELECT COUNT(*) FROM candidates 
                    WHERE is_active = 1 AND datetime(applied_date) > datetime('now', '-24 hours')
                """)
                new_24h = cursor.fetchone()[0]
                
                cursor.execute("""
                    SELECT job_category, COUNT(*) as count, AVG(match_score) as avg_score
                    FROM candidates WHERE is_active = 1
                    GROUP BY job_category
                """)
                categories = {row[0]: {"count": row[1], "avg_score": round(row[2] or 0, 1)} for row in cursor.fetchall()}
                
                cursor.execute("SELECT AVG(match_score) FROM candidates WHERE is_active = 1")
                avg_score = cursor.fetchone()[0] or 0
                
                cursor.execute("""
                    SELECT COUNT(*) FROM candidates 
                    WHERE is_active = 1 AND match_score >= 70
                """)
                strong_matches = cursor.fetchone()[0]
            return {
                'total': total,
                'new_24h': new_24h,
                'categories': categories,
                'avg_score': avg_score,
                'strong_matches': strong_matches,
            }
        stats = await asyncio.to_thread(_get_live_stats_db)
        
        return {
            "total_candidates": stats['total'],
            "new_24h": stats['new_24h'],
            "categories": stats['categories'],
            "category_count": len(stats['categories']),
            "average_score": round(stats['avg_score'], 1),
            "strong_matches": stats['strong_matches'],
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Live stats error: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve live statistics")




@router.get("/api/search-history")
async def get_search_history(limit: int = 50, current_user: dict = Depends(require_auth)):
    """Get search history for reports page"""
    try:
        history = await asyncio.to_thread(_db().get_search_history, limit)
        return {"history": history, "total": len(history)}
    except Exception as e:
        logger.error(f"Search history error: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve search history")



@router.delete("/api/search-history")
async def clear_search_history(current_user: dict = Depends(require_auth)):
    """Clear all search history"""
    try:
        await asyncio.to_thread(_db().clear_search_history)
        return {"status": "success", "message": "Search history cleared"}
    except Exception as e:
        logger.error(f"Clear search history error: {e}")
        return {"status": "error", "message": "Failed to clear search history"}



@router.delete("/api/search-history/{entry_id}")
async def delete_search_entry(entry_id: str, current_user: dict = Depends(require_auth)):
    """Delete a single search history entry"""
    try:
        deleted = await asyncio.to_thread(_db().delete_search_entry, entry_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Search entry {entry_id} not found")
        return {"status": "success", "message": f"Search entry {entry_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete search entry error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete search entry")



@router.get("/api/stats/pipeline")
async def get_pipeline_stats(current_user: dict = Depends(require_auth)):
    """Get pipeline status counts for dashboard"""
    try:
        counts = await asyncio.to_thread(_db().get_pipeline_counts)
        stats = await asyncio.to_thread(_db().get_statistics)
        return {
            "selected": counts.get('Selected', 0),
            "rejected": counts.get('Rejected', 0),
            "shortlisted": counts.get('Shortlisted', 0),
            "interviewed": counts.get('Interviewed', 0),
            "new": counts.get('New', 0),
            "total": stats.get('total_candidates', 0),
            "recent_24h": stats.get('recent_24h', 0),
        }
    except Exception as e:
        logger.error(f"Pipeline stats error: {e}")
        return {"selected": 0, "rejected": 0, "shortlisted": 0, "interviewed": 0, "new": 0, "total": 0}



@router.get("/api/stats")
async def get_stats(current_user: dict = Depends(require_auth)):
    """Get platform statistics with high-volume support"""
    try:
        # Use optimized database statistics method
        stats = await asyncio.to_thread(_db().get_statistics)
        
        # Add AI service stats if available
        ai_stats = {}
        try:
            ai_stats = _ai().get_cache_stats()
        except Exception as e:
            logger.debug(f"Non-critical: get AI cache stats failed: {e}")
        
        return {
            "total_candidates": stats.get('total_candidates', 0),
            "categories": stats.get('categories', {}),
            "recent_24h": stats.get('recent_24h', 0),
            "job_categories": len(stats.get('categories', {})),
            "average_match_score": round(
                sum(c.get('avg_score', 0) for c in stats.get('categories', {}).values()) / 
                max(len(stats.get('categories', {})), 1), 1
            ),
            "ai_cache": ai_stats
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return {
            "total_candidates": 0,
            "categories": {},
            "recent_24h": 0,
            "job_categories": 0,
            "average_match_score": 0,
            "ai_cache": {}
        }



@router.get("/api/llm/status")
async def llm_status(current_user: dict = Depends(require_auth)):
    """Get detailed LLM service status"""
    try:
        from services.llm_service import get_llm_service
        llm_svc = await get_llm_service()
        return llm_svc.get_status()
    except Exception as e:
        return {
            "available": False,
            "error": "LLM service unavailable",
            "setup": "Install Ollama from https://ollama.com/download, then: ollama pull qwen2.5:7b"
        }


