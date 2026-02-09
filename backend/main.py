from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import uvicorn
import asyncio
import os
import json
import re
from dotenv import load_dotenv
import logging
from contextlib import asynccontextmanager
from cachetools import TTLCache
import time

from services.resume_parser import ResumeParser
from services.matching_engine import MatchingEngine
from services.email_parser import EmailParser
from services.microsoft_graph import MicrosoftGraphService
from services.token_storage import get_token_storage
from services.openai_service import get_openai_service
from services.local_ai_service import get_local_ai_service
from services.email_scraper import get_scraper_service
from services.database_service import get_db_service
from services.oauth_automation_service import get_oauth_automation, OAuthAutomationService
from services.auth_service import get_auth_service
from models.candidate import Candidate, JobDescription, MatchResult

# Advanced AI services
from api.advanced_routes import router as advanced_router
from services.followup_service import get_followup_service, run_campaign_processor
from services.sms_notification_service import get_sms_service
from services.email_templates_service import get_templates_service

# Pydantic models for request bodies
class EmailConnectRequest(BaseModel):
    provider: str
    email: str
    password: Optional[str] = None
    access_token: Optional[str] = None
    custom_imap_server: Optional[str] = None

class EmailSyncRequest(BaseModel):
    provider: str
    email: str
    password: Optional[str] = None
    access_token: Optional[str] = None
    folder: str = 'INBOX'
    limit: int = 50

class OAuth2CallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None
    redirect_uri: str

# Load environment variables
load_dotenv()

# Configuration
DEBUG = os.getenv('DEBUG', 'true').lower() == 'true'
AI_TIMEOUT = float(os.getenv('AI_TIMEOUT_SECONDS', '15'))  # Increased for local AI
AI_ANALYSIS_TIMEOUT = float(os.getenv('AI_ANALYSIS_TIMEOUT', '180'))  # LLM inference needs time on CPU
MAX_CONCURRENT_REQUESTS = int(os.getenv('MAX_CONCURRENT_REQUESTS', '100'))
USE_OPENAI_FALLBACK = os.getenv('USE_OPENAI_FALLBACK', 'false').lower() == 'true'  # Disabled by default

# Configure logging with structured format
logging.basicConfig(
    level=logging.INFO if DEBUG else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Performance: Response cache (5 minutes TTL)
response_cache = TTLCache(maxsize=1000, ttl=300)

# Performance: Semaphore for connection pooling
db_semaphore = asyncio.Semaphore(50)  # Max 50 concurrent DB operations

# Background email sync task
background_sync_task = None
oauth_automation_service: OAuthAutomationService = None

async def auto_sync_emails():
    """
    FULLY AUTOMATED email sync with OAuth2 Client Credentials Flow
    NO user intervention required - authenticates automatically using app credentials
    
    Authentication Priority:
    1. Client Credentials (Application Permissions) - FULLY AUTOMATIC
    2. Refresh Token (if available from previous delegated auth)
    3. IMAP fallback (if OAuth2 not configured)
    """
    # Wait 5 seconds before first sync to allow server to fully start
    await asyncio.sleep(5)
    
    while True:
        try:
            logger.info("🔄 Auto-sync: Starting email sync...")
            
            # Get OAuth2 configuration
            client_id = os.getenv('MICROSOFT_CLIENT_ID')
            client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
            tenant_id = os.getenv('MICROSOFT_TENANT_ID')
            primary_email = os.getenv('EMAIL_ADDRESS')
            
            oauth2_success = False
            
            if all([client_id, client_secret, tenant_id, primary_email]):
                try:
                    token_storage = get_token_storage()
                    token_data = token_storage.get_token(primary_email)
                    graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=primary_email)
                    
                    # PRIORITY 1: Try Client Credentials Flow (FULLY AUTOMATIC - no user interaction)
                    # This uses Application Permissions configured in Azure AD
                    needs_new_token = (
                        not token_data or 
                        token_data.get('is_expired', True) or
                        not token_data.get('access_token')
                    )
                    
                    if needs_new_token:
                        logger.info(f"🔐 Auto-authenticating with Client Credentials for {primary_email}...")
                        
                        # Try refresh token first if available
                        refresh_token = token_data.get('refresh_token') if token_data else None
                        auth_success = False
                        
                        if refresh_token:
                            logger.info("🔄 Attempting token refresh...")
                            refresh_result = await graph_service.refresh_access_token(refresh_token)
                            if refresh_result['status'] == 'success':
                                token_storage.save_token(
                                    email=primary_email,
                                    access_token=refresh_result['access_token'],
                                    refresh_token=refresh_result.get('refresh_token', refresh_token),
                                    expires_in=refresh_result['expires_in'],
                                    auth_type='delegated'
                                )
                                token_data = token_storage.get_token(primary_email)
                                auth_success = True
                                logger.info(f"✅ Token refreshed successfully for {primary_email}")
                        
                        # If refresh failed or no refresh token, use Client Credentials (AUTOMATIC)
                        if not auth_success:
                            logger.info("🤖 Using Client Credentials Flow (automatic authentication)...")
                            cred_result = graph_service.authenticate_with_credentials()
                            
                            if cred_result['status'] == 'success':
                                token_storage.save_token(
                                    email=primary_email,
                                    access_token=cred_result['access_token'],
                                    refresh_token=None,  # Client credentials don't use refresh tokens
                                    expires_in=cred_result['expires_in'],
                                    auth_type='application'
                                )
                                token_data = token_storage.get_token(primary_email)
                                logger.info(f"✅ Client Credentials authentication successful for {primary_email}")
                            else:
                                error_msg = cred_result.get('error', 'Unknown error')
                                logger.warning(f"⚠️ Client Credentials failed: {error_msg}")
                                
                                # Provide helpful setup instructions
                                if 'unauthorized' in str(error_msg).lower() or 'consent' in str(error_msg).lower():
                                    logger.info("📋 To enable FULLY AUTOMATIC authentication:")
                                    logger.info("   1. Go to Azure Portal → App Registrations → Your App")
                                    logger.info("   2. API Permissions → Add Permission → Microsoft Graph")
                                    logger.info("   3. Application Permissions → Mail.Read, Mail.ReadBasic")
                                    logger.info("   4. Click 'Grant admin consent' (requires admin)")
                                    logger.info("   Once configured, authentication will be fully automatic!")
                    
                    # Use the token if we have a valid one
                    if token_data and token_data.get('access_token') and not token_data.get('is_expired', True):
                        logger.info(f"🔐 Using OAuth2 ({token_data.get('auth_type', 'unknown')}) for {primary_email}...")
                        
                        graph_service.access_token = token_data['access_token']
                        graph_service.auth_type = token_data.get('auth_type', 'application')
                        graph_service.token_expiry = token_data.get('expires_at_dt', datetime.now() + timedelta(hours=1))
                        
                        # Check if database is empty - if empty, fetch ALL emails
                        candidate_count = await asyncio.to_thread(
                            lambda: db_service.get_total_candidates()
                        )
                        process_all = (candidate_count == 0)
                        
                        # Fetch emails
                        logger.info(f"📧 {'Initial setup - fetching ALL emails...' if process_all else 'Syncing emails...'}")
                        result = await graph_service.get_messages(
                            folder='inbox', 
                            top=100000,
                            fetch_all=True
                        )
                    
                        if result['status'] == 'success':
                            messages = result['messages']
                            logger.info(f"📧 Found {len(messages)} emails from OAuth2")
                            
                            # Process messages in parallel batches
                            new_count = 0
                            
                            async def process_graph_message(msg):
                                nonlocal new_count
                                try:
                                    # Convert Graph API message to candidate format
                                    sender = msg.get('from', {}).get('emailAddress', {})
                                    sender_email = sender.get('address', '')
                                    sender_name = sender.get('name', sender_email.split('@')[0])
                                    
                                    subject = msg.get('subject', '')
                                    body = msg.get('body', {}).get('content', '')
                                    
                                    # Check for attachments
                                    has_attachments = msg.get('hasAttachments', False)
                                    attachments = []
                                    
                                    if has_attachments:
                                        # Fetch attachments
                                        attach_result = await graph_service.get_message_with_attachments(msg['id'])
                                        if attach_result['status'] == 'success':
                                            attachments = attach_result['attachments']
                                    
                                    # Build email data - use actual email received date
                                    received_dt = msg.get('receivedDateTime')
                                    if received_dt:
                                        try:
                                            received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                                        except:
                                            received_date = datetime.now()
                                    else:
                                        received_date = datetime.now()
                                    
                                    email_data = {
                                        'subject': subject,
                                        'sender_email': sender_email,
                                        'sender_name': sender_name,
                                        'body': body,
                                        'attachments': attachments,
                                        'received_date': received_date
                                    }
                                    
                                    # Extract candidate
                                    candidate = await scraper_service.extract_candidate_from_email(email_data)
                                    if not candidate or not candidate.get('email'):
                                        return
                                    
                                    # Check if exists
                                    existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])
                                    
                                    needs_ai = False
                                    if not existing:
                                        needs_ai = True
                                        new_count += 1
                                    elif not existing.get('ai_score'):
                                        needs_ai = True
                                    
                                    # AI processing - use resume text OR email summary
                                    analysis_text = candidate.get('resume_text') or candidate.get('summary', '')
                                    if needs_ai and analysis_text and len(analysis_text) > 20:
                                        try:
                                            ai_analysis = await asyncio.wait_for(
                                                ai_service.analyze_candidate(analysis_text),
                                                timeout=AI_ANALYSIS_TIMEOUT
                                            )
                                            if ai_analysis and ai_analysis.get('quality_score'):
                                                # Map quality_score to matchScore for database
                                                candidate.update({
                                                    'job_category': ai_analysis.get('job_category', 'General'),
                                                    'matchScore': ai_analysis.get('quality_score', 50),
                                                    'summary': ai_analysis.get('summary', candidate.get('summary', '')),
                                                    'skills': ai_analysis.get('skills', candidate.get('skills', [])),
                                                    'experience': ai_analysis.get('experience', candidate.get('experience', 0)),
                                                    'education': ai_analysis.get('education', []),
                                                    'phone': ai_analysis.get('phone') or candidate.get('phone', ''),
                                                    'location': ai_analysis.get('location') or candidate.get('location', ''),
                                                    'linkedin': ai_analysis.get('linkedin') or candidate.get('linkedin', ''),
                                                })
                                                logger.info(f"✅ AI scored {candidate.get('name')}: {ai_analysis.get('quality_score')}%")
                                        except Exception as ai_err:
                                            logger.warning(f"AI analysis failed: {str(ai_err)[:50]}")
                                            candidate['matchScore'] = 45  # Default score for error
                                    
                                    # Save to database
                                    if existing:
                                        await asyncio.to_thread(db_service.update_candidate, candidate)
                                    else:
                                        await asyncio.to_thread(db_service.insert_candidate, candidate)
                                        
                                except Exception as e:
                                    logger.warning(f"Error processing message: {str(e)[:100]}")
                            
                            # Process in batches (smaller batch for SQLite safety)
                            BATCH_SIZE = 3
                            for i in range(0, len(messages), BATCH_SIZE):
                                batch = messages[i:i+BATCH_SIZE]
                                await asyncio.gather(*[process_graph_message(msg) for msg in batch], return_exceptions=True)
                                
                                if len(messages) > 50 and (i + BATCH_SIZE) % 50 == 0:
                                    logger.info(f"📊 Progress: {min(i+BATCH_SIZE, len(messages))}/{len(messages)} emails processed...")
                            
                            logger.info(f"✅ OAuth2 sync: {primary_email} - {len(messages)} emails, {new_count} new candidates")
                            oauth2_success = True
                            
                        else:
                            error_msg = result.get('message', 'Unknown error')
                            logger.warning(f"OAuth2 fetch failed: {error_msg}")
                            
                            # If 403 Forbidden with application auth, need Azure AD permissions
                            if '403' in str(error_msg) and token_data.get('auth_type') == 'application':
                                logger.info("=" * 70)
                                logger.info("📋 APPLICATION PERMISSIONS NOT CONFIGURED IN AZURE AD")
                                logger.info("=" * 70)
                                logger.info("")
                                logger.info("OPTION 1: Enable FULLY AUTOMATIC sync (recommended if you have Azure admin)")
                                logger.info("   1. Go to: Azure Portal → App Registrations → AI Recruitment Tool")
                                logger.info("   2. Click: API Permissions → Add a permission")
                                logger.info("   3. Select: Microsoft Graph → Application permissions")
                                logger.info("   4. Add: Mail.Read and Mail.ReadBasic")
                                logger.info("   5. Click: 'Grant admin consent for [Organization]'")
                                logger.info("")
                                logger.info("OPTION 2: Authenticate ONCE via frontend (if no Azure admin access)")
                                logger.info("   1. Open: http://localhost:3000")
                                logger.info("   2. Go to: Settings → Email Integration")
                                logger.info("   3. Click: Connect Microsoft Account")
                                logger.info("   4. Sign in and grant permissions")
                                logger.info("   → After this ONE-TIME login, auto-refresh works FOREVER")
                                logger.info("")
                                logger.info("=" * 70)
                                # Clear the application token since it won't work
                                token_storage.delete_token(primary_email)
                            elif 'token' in error_msg.lower() or 'unauthorized' in error_msg.lower():
                                logger.info("🔄 Clearing invalid token...")
                                token_storage.delete_token(primary_email)
                    
                except Exception as oauth_error:
                    logger.error(f"OAuth2 sync error: {str(oauth_error)}")
            
            # FALLBACK: Try IMAP if OAuth2 not available or failed
            if not oauth2_success:
                logger.info("Falling back to IMAP sync...")
                for account in scraper_service.email_accounts:
                    try:
                        # Wrap connection in timeout (20 seconds max per account)
                        try:
                            mail = await asyncio.wait_for(
                                asyncio.to_thread(scraper_service.connect_to_inbox, account),
                                timeout=20
                            )
                        except asyncio.TimeoutError:
                            logger.warning(f"⚠️ Connection timeout for {account.name} - skipping...")
                            continue
                        
                        if not mail:
                            logger.warning(f"⚠️ Skipping {account.name} - connection failed, continuing to next account...")
                            continue
                        
                        # Check if database is empty - if so, process ALL emails initially
                        candidate_count = await asyncio.to_thread(
                            lambda: db_service.get_total_candidates()
                        )
                        process_all = (candidate_count == 0)
                        
                        if process_all:
                            logger.info(f"📥 First sync for {account.name} - processing entire inbox...")
                        
                        # Fetch emails (ALL if first time, UNSEEN afterwards)
                        emails = await asyncio.wait_for(
                            scraper_service.fetch_emails(mail, process_all=process_all),
                            timeout=600  # 10 minute max for fetching large inboxes
                        )
                        new_count = 0
                        
                        # PARALLEL PROCESSING - Process candidates in batches of 10 concurrently
                        async def process_single_candidate(email_data):
                            nonlocal new_count
                            try:
                                candidate = await scraper_service.extract_candidate_from_email(email_data)
                                if not candidate or not candidate.get('email'):
                                    return
                                
                                # Check if candidate already exists
                                existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])
                                
                                # Only process with AI if NEW or needs update
                                needs_ai_processing = False
                                if not existing:
                                    needs_ai_processing = True
                                    new_count += 1
                                elif not existing.get('ai_score') or not existing.get('job_category'):
                                    # Existing candidate without AI analysis
                                    needs_ai_processing = True
                                
                                # AI Processing only for new/unprocessed candidates
                                if needs_ai_processing:
                                    try:
                                        # Use resume text OR summary for analysis
                                        analysis_text = candidate.get('resume_text', '') or candidate.get('summary', '')
                                        if analysis_text and len(analysis_text) > 20:
                                            ai_analysis = await asyncio.wait_for(
                                                ai_service.analyze_candidate(analysis_text),
                                                timeout=AI_ANALYSIS_TIMEOUT
                                            )
                                            if ai_analysis and ai_analysis.get('quality_score'):
                                                # Map quality_score to matchScore for database
                                                candidate.update({
                                                    'job_category': ai_analysis.get('job_category', 'General'),
                                                    'matchScore': ai_analysis.get('quality_score', 50),
                                                    'summary': ai_analysis.get('summary', candidate.get('summary', '')),
                                                    'skills': ai_analysis.get('skills', candidate.get('skills', [])),
                                                    'experience': ai_analysis.get('experience', candidate.get('experience', 0)),
                                                    'education': ai_analysis.get('education', []),
                                                    'phone': ai_analysis.get('phone') or candidate.get('phone', ''),
                                                    'location': ai_analysis.get('location') or candidate.get('location', ''),
                                                    'linkedin': ai_analysis.get('linkedin') or candidate.get('linkedin', ''),
                                                })
                                                logger.info(f"✅ AI scored {candidate.get('name')}: {ai_analysis.get('quality_score')}%")
                                    except asyncio.TimeoutError:
                                        logger.warning(f"AI timeout for {candidate.get('name')} - using default score")
                                        candidate['matchScore'] = 45
                                    except Exception as ai_err:
                                        logger.warning(f"AI error: {str(ai_err)}")
                                        candidate['matchScore'] = 45
                                
                                # Save to database
                                if existing:
                                    await asyncio.to_thread(db_service.update_candidate, candidate)
                                else:
                                    await asyncio.to_thread(db_service.insert_candidate, candidate)
                            except Exception as e:
                                logger.warning(f"Error processing candidate: {str(e)[:100]}")
                        
                        # Process in batches of 10 for optimal performance
                        BATCH_SIZE = 10
                        for i in range(0, len(emails), BATCH_SIZE):
                            batch = emails[i:i+BATCH_SIZE]
                            await asyncio.gather(*[process_single_candidate(email_data) for email_data in batch], return_exceptions=True)
                            
                            # Log progress for large syncs
                            if len(emails) > 50 and (i + BATCH_SIZE) % 50 == 0:
                                logger.info(f"📊 Progress: {min(i+BATCH_SIZE, len(emails))}/{len(emails)} emails processed...")
                        
                        mail.logout()
                        logger.info(f"✅ Auto-sync: {account.name} - {len(emails)} emails, {new_count} new candidates")
                        
                    except Exception as e:
                        logger.error(f"Auto-sync error for {account.name}: {str(e)}")
            
            # Wait for next sync
            sync_interval = int(os.getenv('SYNC_INTERVAL_MINUTES', '15')) * 60
            logger.info(f"⏰ Auto-sync: Next sync in {sync_interval//60} minutes")
            await asyncio.sleep(sync_interval)
            
        except Exception as e:
            logger.error(f"Auto-sync background task error: {str(e)}")
            await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup/shutdown"""
    global background_sync_task, oauth_automation_service
    
    # Startup
    logger.info("🚀 AI Recruitment Platform Starting...")
    logger.info(f"Environment: {'Development' if DEBUG else 'Production'}")
    
    if ai_service == local_ai_service:
        logger.info("🤖 AI: Local AI (FREE - Zero API costs)")
    else:
        logger.info(f"🤖 AI: OpenAI ({openai_service.model if openai_service else 'N/A'})")
    
    # Initialize Local LLM (Ollama)
    try:
        from services.llm_service import get_llm_service
        llm_svc = await get_llm_service()
        if llm_svc.available:
            logger.info(f"🧠 LLM: Ollama connected! Primary: {llm_svc.primary_model}")
            logger.info(f"   Models: {', '.join(llm_svc.available_models)}")
        else:
            logger.warning("⚠️ LLM: Ollama not available - using sentence-transformers + regex")
            logger.warning("   Install: https://ollama.com/download → ollama pull qwen2.5:7b")
    except Exception as e:
        logger.warning(f"⚠️ LLM initialization skipped: {e}")
    
    logger.info(f"📧 Email Accounts: {len(scraper_service.email_accounts)} configured")
    logger.info(f"⚡ Max Concurrent Requests: {MAX_CONCURRENT_REQUESTS}")
    
    # Initialize OAuth Automation Service
    oauth_automation_service = get_oauth_automation()
    
    # Check if OAuth is properly configured
    if oauth_automation_service.is_configured:
        logger.info(f"🔐 OAuth2 Automation: Configured for {oauth_automation_service.primary_email}")
        
        # Check and auto-refresh token if needed
        auth_status = await oauth_automation_service.check_auth_status()
        logger.info(f"🔐 OAuth2 Status: {auth_status.value}")
        
        if auth_status.value in ['expired', 'no_token']:
            # Try to auto-authenticate
            result = await oauth_automation_service.ensure_valid_token()
            if result['status'] == 'success':
                logger.info(f"✅ OAuth2 auto-authenticated successfully")
            else:
                logger.warning(f"⚠️ OAuth2 auto-auth failed: {result.get('message')} - manual auth may be needed")
    else:
        logger.info("📧 OAuth2 Automation: Not configured (missing credentials)")
    
    # Auto-sync enabled? - Use new OAuth automation
    auto_sync_enabled = os.getenv('AUTO_SYNC_ENABLED', 'true').lower() == 'true'
    has_email_accounts = len(scraper_service.email_accounts) > 0
    
    if auto_sync_enabled and (has_email_accounts or oauth_automation_service.is_configured):
        logger.info(f"🔄 Auto-sync: ENABLED (every {os.getenv('SYNC_INTERVAL_MINUTES', '15')} minutes)")
        
        # Start OAuth automation service (handles token refresh and scheduling)
        await oauth_automation_service.start()
        logger.info("🔐 OAuth Automation Service: Started (auto-refresh enabled)")
        
        # Also start legacy auto-sync for IMAP fallback
        try:
            background_sync_task = asyncio.create_task(auto_sync_emails())
        except Exception as e:
            logger.error(f"Failed to start auto-sync: {str(e)}")
    else:
        logger.info("🔄 Auto-sync: DISABLED (no email accounts or OAuth configured)")
    
    # Initialize advanced services
    try:
        # Wire up follow-up service with email and SMS
        followup_service = get_followup_service()
        sms_service = get_sms_service()
        templates_service = get_templates_service()
        followup_service.set_services(
            email_service=templates_service,
            sms_service=sms_service
        )
        logger.info("📬 Advanced services initialized (ML, Analytics, Campaigns, SMS)")
        
        # Start campaign processor background task
        campaign_task = asyncio.create_task(run_campaign_processor(interval_seconds=300))
        logger.info("📬 Campaign processor started (checks every 5 minutes)")
    except Exception as e:
        logger.warning(f"⚠️ Advanced services initialization warning: {str(e)}")
    
    logger.info("✅ Server ready - Visit /api/docs for API documentation" if DEBUG else "✅ Server ready")
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down gracefully...")
    if oauth_automation_service:
        await oauth_automation_service.stop()
    if background_sync_task:
        background_sync_task.cancel()
    response_cache.clear()

app = FastAPI(
    title="AI Recruiter API - High Performance Edition",
    description="Optimized recruitment platform with email scraping, AI job matching, ML ranking, and automated campaigns",
    version="4.0.0",
    docs_url="/api/docs" if DEBUG else None,
    redoc_url="/api/redoc" if DEBUG else None,
    lifespan=lifespan
)

# Include advanced AI services router
app.include_router(advanced_router)

# CORS configuration - Environment-based with performance headers
allowed_origins = os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:3001,http://localhost:5173').split(',')
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,  # Cache preflight requests for 1 hour
)
logger.info(f"✅ CORS enabled for: {', '.join(allowed_origins)}")

# Performance: Rate limiting and request tracking
from collections import defaultdict
from datetime import datetime as dt

request_counts = defaultdict(int)
last_reset = dt.now()

async def rate_limit_check():
    """Simple in-memory rate limiting"""
    global last_reset, request_counts
    now = dt.now()
    if (now - last_reset).seconds > 60:
        request_counts.clear()
        last_reset = now
    
    # Allow up to 1000 requests per minute per IP (adjust as needed)
    # For production, use Redis or proper rate limiting middleware

@app.middleware("http")
async def add_performance_headers(request, call_next):
    """Add performance monitoring headers"""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time * 1000, 2))
    return response

# Initialize services
resume_parser = ResumeParser()
matching_engine = MatchingEngine()
email_parser = EmailParser()

# AI Services - Local AI is PRIMARY (zero cost, emergency fallback to OpenAI)
local_ai_service = get_local_ai_service()
openai_service = get_openai_service()  # Emergency fallback only

# ALWAYS use Local AI as primary
# OpenAI is only used as emergency fallback when Local AI fails
ai_service = local_ai_service
fallback_service = openai_service  # Emergency backup

scraper_service = get_scraper_service()
db_service = get_db_service()

# Background scraper task
scraper_task = None

@app.get("/")
async def root():
    return {
        "message": "AI Recruiter API - High Performance Edition",
        "version": "3.0.0",
        "status": "operational",
        "performance": {
            "max_concurrent_requests": MAX_CONCURRENT_REQUESTS,
            "ai_timeout": AI_TIMEOUT,
            "cache_enabled": True,
            "connection_pooling": True
        },
        "features": [
            "Automated email scraping (Gmail + MS365)",
            "AI-powered candidate extraction",
            "Intelligent timeout-based OpenAI fallback",
            "High-load optimized (100+ concurrent)",
            "Response caching (5min TTL)",
            "Connection pooling (50 max)",
            "Auto job categorization",
            "Duplicate detection"
        ]
    }

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    try:
        import psutil
        system_info = {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage('/').percent
        }
    except:
        system_info = {"status": "unavailable"}
    
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "scraper_running": scraper_task is not None and not scraper_task.done(),
        "system": system_info,
        "cache": {
            "response_cache_size": len(response_cache),
            "ai_embedding_cache": len(ai_service.embedding_cache) if hasattr(ai_service, 'embedding_cache') else 0
        }
    }


# ============================================
# Setup & Configuration Verification Endpoints
# ============================================

@app.get("/api/setup/verify")
async def verify_setup():
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
            "error": str(e)
        }


@app.get("/api/setup/status")
async def get_setup_status():
    """
    Get quick setup status summary
    """
    env = os.getenv('ENVIRONMENT', 'development')
    
    return {
        "environment": env,
        "is_production": env == 'production',
        "debug": os.getenv('DEBUG', 'true').lower() == 'true',
        "database": "postgresql" if "postgresql" in os.getenv('DATABASE_URL', '') else "sqlite",
        "ai_mode": "local (free)" if os.getenv('USE_LOCAL_AI', 'true').lower() == 'true' else "openai",
        "email_oauth": bool(os.getenv('MICROSOFT_CLIENT_ID')),
        "sms_enabled": bool(os.getenv('TWILIO_ACCOUNT_SID')),
        "calendar_enabled": bool(os.getenv('GOOGLE_CLIENT_ID') or os.getenv('CALENDLY_API_KEY')),
        "redis_enabled": bool(os.getenv('REDIS_URL')),
        "version": os.getenv('APP_VERSION', '4.0.0')
    }


@app.get("/api/setup/instructions")
async def get_setup_instructions():
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
                    "1. Go to portal.azure.com → Azure Active Directory → App registrations",
                    "2. Click 'New registration' with name 'AI Recruiter'",
                    "3. Set redirect URI: http://localhost:5173/email (Web type)",
                    "4. Go to 'Certificates & secrets' → New client secret",
                    "5. Go to 'API permissions' → Add: Mail.Read, Mail.ReadWrite, User.Read, offline_access",
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
                    "5. Optional: Set USE_OPENAI_FALLBACK=true for emergency fallback"
                ],
                "env_vars": [
                    "USE_LOCAL_AI=true",
                    "LOCAL_AI_MODEL=all-mpnet-base-v2",
                    "USE_OPENAI_FALLBACK=false"
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


@app.post("/api/setup/test-connection/{service}")
async def test_service_connection(service: str):
    """
    Test connection to a specific service
    """
    results = {"service": service, "status": "unknown"}
    
    if service == "database":
        try:
            count = await asyncio.to_thread(db_service.get_total_candidates)
            results = {"service": service, "status": "connected", "candidate_count": count}
        except Exception as e:
            results = {"service": service, "status": "error", "error": str(e)}
    
    elif service == "email":
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        if client_id:
            results = {"service": service, "status": "configured", "client_id": client_id[:8] + "..."}
        else:
            results = {"service": service, "status": "not_configured"}
    
    elif service == "ai":
        try:
            test_result = await ai_service.analyze_candidate("Software engineer with 5 years Python experience")
            results = {"service": service, "status": "working", "sample_score": test_result.get('quality_score')}
        except Exception as e:
            results = {"service": service, "status": "error", "error": str(e)}
    
    elif service == "sms":
        if os.getenv('TWILIO_ACCOUNT_SID'):
            results = {"service": service, "status": "configured"}
        else:
            results = {"service": service, "status": "not_configured"}
    
    return results

# Email Scraper Control Endpoints
@app.post("/api/scraper/start")
async def start_scraper(background_tasks: BackgroundTasks):
    """Start the email scraper manually"""
    global scraper_task
    if scraper_task and not scraper_task.done():
        return {"message": "Scraper already running"}
    
    scraper_task = asyncio.create_task(scraper_service.run_continuous_scraper())
    return {"message": "Email scraper started"}

@app.post("/api/scraper/stop")
async def stop_scraper():
    """Stop the email scraper"""
    global scraper_task
    if scraper_task:
        scraper_task.cancel()
        return {"message": "Email scraper stopped"}
    return {"message": "Scraper not running"}

@app.get("/api/scraper/status")
async def scraper_status():
    """Get scraper status for all accounts"""
    accounts_status = []
    for account in scraper_service.email_accounts:
        accounts_status.append({
            "name": account.name,
            "email": account.email,
            "server": account.server,
            "processed_count": account.processed_count,
            "last_check": account.last_check.isoformat() if account.last_check else None
        })
    
    return {
        "running": scraper_task is not None and not scraper_task.done(),
        "total_accounts": len(scraper_service.email_accounts),
        "accounts": accounts_status,
        "total_processed": len(scraper_service.processed_message_ids),
        "process_all_history": scraper_service.process_all_history
    }

@app.post("/api/scraper/process-now")
async def trigger_manual_scrape(process_all: bool = True):
    """
    Manually trigger email scraping
    process_all=True: Process ALL historical emails (default)
    process_all=False: Process only NEW emails
    """
    try:
        total_emails = 0
        total_candidates = 0
        results_by_account = []
        
        for account in scraper_service.email_accounts:
            try:
                mail = scraper_service.connect_to_inbox(account)
                if not mail:
                    results_by_account.append({
                        "account": account.name,
                        "error": "Connection failed"
                    })
                    continue
                
                emails = await scraper_service.fetch_emails(mail, process_all=process_all)
                
                candidates = []
                for email_data in emails:
                    candidate = await scraper_service.extract_candidate_from_email(email_data)
                    if candidate:
                        candidates.append(candidate)
                        
                        # Save to database
                        existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])
                        if existing:
                            await asyncio.to_thread(db_service.update_candidate, candidate)
                        else:
                            await asyncio.to_thread(db_service.insert_candidate, candidate)
                
                mail.logout()
                
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
            "total_accounts": len(scraper_service.email_accounts),
            "total_emails_found": total_emails,
            "total_candidates_extracted": total_candidates,
            "accounts": results_by_account
        }
    except Exception as e:
        raise HTTPException(500, f"Scraping error: {str(e)}")


@app.post("/api/candidates/reset-and-reparse")
async def reset_and_reparse_all_emails():
    """
    Clear all candidates and re-parse ALL emails from inbox.
    Parses email body, attached resumes, and uses Local AI (with OpenAI fallback) for analysis.
    """
    try:
        # Clear response cache
        response_cache.clear()
        
        # Step 1: Clear all candidates from database
        deleted_count = await asyncio.to_thread(db_service.clear_all_candidates)
        logger.info(f"🗑️ Cleared {deleted_count} candidates from database")
        
        # Step 2: Clear processed message IDs to force reprocessing
        scraper_service.processed_message_ids.clear()
        
        # Step 3: Trigger full email sync via OAuth2 (Microsoft Graph)
        primary_email = os.getenv('EMAIL_ADDRESS', 'hr@effortz.com')
        token_storage = get_token_storage()
        token_data = token_storage.get_token(primary_email)
        
        if not token_data:
            return {
                "status": "error",
                "message": "No OAuth2 token found. Please authenticate first.",
                "deleted_count": deleted_count
            }
        
        # Initialize Graph service
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id)
        graph_service.access_token = token_data['access_token']
        graph_service.token_expiry = datetime.fromisoformat(token_data['expires_at'])
        
        # Fetch ALL emails from entire inbox history
        logger.info("📧 Fetching ALL emails from entire inbox for re-parsing...")
        result = await graph_service.get_messages(folder='inbox', top=100000, fetch_all=True)
        
        if result['status'] != 'success':
            return {
                "status": "error",
                "message": f"Failed to fetch emails: {result.get('message')}",
                "deleted_count": deleted_count
            }
        
        messages = result['messages']
        logger.info(f"📧 Found {len(messages)} emails to re-parse")
        
        # Process all messages
        new_count = 0
        ai_analyzed_count = 0
        
        async def process_message(msg):
            nonlocal new_count, ai_analyzed_count
            try:
                sender = msg.get('from', {}).get('emailAddress', {})
                sender_email = sender.get('address', '')
                sender_name = sender.get('name', '')
                
                subject = msg.get('subject', '')
                body = msg.get('body', {}).get('content', '')
                
                # Get attachments (resumes)
                attachments = []
                if msg.get('hasAttachments'):
                    attach_result = await graph_service.get_message_with_attachments(msg['id'])
                    if attach_result['status'] == 'success':
                        attachments = attach_result['attachments']
                
                # Use actual email received date from Graph API
                received_dt = msg.get('receivedDateTime')
                if received_dt:
                    try:
                        received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                    except:
                        received_date = datetime.now()
                else:
                    received_date = datetime.now()
                
                email_data = {
                    'subject': subject,
                    'sender_email': sender_email,
                    'sender_name': sender_name,
                    'body': body,
                    'attachments': attachments,
                    'received_date': received_date
                }
                
                # Extract candidate from email (parses body + resume attachments)
                candidate = await scraper_service.extract_candidate_from_email(email_data)
                if not candidate or not candidate.get('email'):
                    return
                
                # AI Analysis: Use Local AI first, OpenAI as fallback
                # Prefer resume_text if available, otherwise use summary + skills
                resume_text = candidate.get('resume_text', '') or (candidate.get('summary', '') + ' ' + ' '.join(candidate.get('skills', [])))
                if resume_text.strip():
                    try:
                        # Try Local AI first (FREE)
                        ai_analysis = await asyncio.wait_for(
                            ai_service.analyze_candidate(resume_text),
                            timeout=AI_ANALYSIS_TIMEOUT
                        )
                        if ai_analysis:
                            candidate['job_category'] = ai_analysis.get('job_category', candidate.get('job_category', 'General'))
                            candidate['matchScore'] = ai_analysis.get('quality_score', 50)
                            # Update skills and experience from AI if better
                            if ai_analysis.get('skills'):
                                ai_skills = ai_analysis['skills']
                                if isinstance(ai_skills, list) and len(ai_skills) > len(candidate.get('skills', [])):
                                    candidate['skills'] = ai_skills
                            if ai_analysis.get('experience') and ai_analysis['experience'] > candidate.get('experience', 0):
                                candidate['experience'] = ai_analysis['experience']
                            ai_analyzed_count += 1
                    except asyncio.TimeoutError:
                        # Local AI timeout - use smart defaults (NO OpenAI = zero cost)
                        logger.warning(f"⏱️ Local AI timeout for {sender_email} - using smart defaults")
                        candidate['matchScore'] = 45 + min(15, len(candidate.get('skills', [])) * 2)
                    except Exception as ai_err:
                        logger.warning(f"AI analysis error: {str(ai_err)[:50]} - using defaults")
                        candidate['matchScore'] = 42
                
                # Save resume file if present
                resume_file = candidate.pop('resume_file_data', None)
                resume_filename = candidate.pop('resume_filename', None)
                
                # Save candidate to database
                await asyncio.to_thread(db_service.insert_candidate, candidate)
                
                # Save resume file separately
                if resume_file and resume_filename:
                    content_type = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                    await asyncio.to_thread(db_service.save_resume, candidate['id'], resume_filename, resume_file, content_type)
                
                new_count += 1
                    
            except Exception as e:
                logger.warning(f"Error processing message: {str(e)[:100]}")
        
        # Process in batches
        BATCH_SIZE = 5
        for i in range(0, len(messages), BATCH_SIZE):
            batch = messages[i:i+BATCH_SIZE]
            await asyncio.gather(*[process_message(msg) for msg in batch], return_exceptions=True)
            
            if len(messages) > 50 and (i + BATCH_SIZE) % 50 == 0:
                logger.info(f"📊 Progress: {min(i+BATCH_SIZE, len(messages))}/{len(messages)} emails processed, {new_count} candidates...")
        
        logger.info(f"✅ Re-parse complete: {new_count} candidates from {len(messages)} emails, {ai_analyzed_count} AI-analyzed")
        
        return {
            "status": "success",
            "message": "All emails re-parsed successfully",
            "deleted_count": deleted_count,
            "emails_processed": len(messages),
            "candidates_created": new_count,
            "ai_analyzed": ai_analyzed_count
        }
        
    except Exception as e:
        logger.error(f"Reset and reparse error: {str(e)}")
        raise HTTPException(500, f"Error: {str(e)}")

# Candidate Management Endpoints (Database-backed)
@app.get("/api/candidates")
async def get_candidates(
    page: int = 1,
    limit: int = 50,
    job_category: Optional[str] = None,
    min_score: Optional[int] = None
):
    """
    Get candidates with OPTIMIZED pagination and caching
    Efficiently handles 100,000+ candidates
    """
    # Create cache key
    cache_key = f"candidates_p{page}_l{limit}_c{job_category}_s{min_score}"
    
    # Check cache first
    if cache_key in response_cache:
        logger.info("⚡ Cache hit for candidates")
        cached_result = response_cache[cache_key]
        cached_result["from_cache"] = True
        return cached_result
    
    try:
        filters = {}
        if job_category:
            filters['job_category'] = job_category
        if min_score:
            filters['min_score'] = min_score
        
        # Use connection pooling
        async with db_semaphore:
            candidates = await asyncio.to_thread(
                db_service.get_candidates_paginated,
                page,
                limit,
                filters
            )
        
        result = {
            "page": page,
            "limit": limit,
            "candidates": candidates,
            "total": len(candidates),
            "from_cache": False
        }
        
        # Cache result
        response_cache[cache_key] = result
        
        return result
    except Exception as e:
        logger.error(f"Error fetching candidates: {e}")
        raise HTTPException(500, f"Error fetching candidates: {str(e)}")

@app.get("/api/candidates/new")
async def get_new_candidates(since: str):
    """
    Get only NEW candidates since specified date
    Incremental processing - avoids reprocessing 100,000s
    """
    try:
        new_candidates = await asyncio.to_thread(db_service.get_new_candidates_since, since)
        return {
            "new_count": len(new_candidates),
            "candidates": new_candidates
        }
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")


@app.post("/api/candidates/reprocess-scores")
async def reprocess_candidate_scores():
    """
    Reprocess all candidates with match_score = 0 to calculate proper AI scores.
    This fixes candidates that were imported before AI scoring was properly connected.
    """
    try:
        conn = db_service.get_connection()
        cursor = conn.cursor()
        
        # Get candidates with 0 score (not yet scored)
        cursor.execute("""
            SELECT id, email, name, skills, summary, education, work_history 
            FROM candidates 
            WHERE match_score = 0 OR match_score IS NULL
        """)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return {"status": "success", "message": "No candidates need reprocessing", "processed": 0}
        
        processed = 0
        errors = 0
        
        for row in rows:
            try:
                candidate_id, email, name, skills_json, summary, education, work_history = row
                
                # Build text for AI analysis
                skills = json.loads(skills_json) if skills_json else []
                text_parts = []
                if summary:
                    text_parts.append(summary)
                if skills:
                    text_parts.append(' '.join(skills))
                if education:
                    text_parts.append(education)
                if work_history:
                    try:
                        wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                        for job in wh:
                            if isinstance(job, dict):
                                text_parts.append(f"{job.get('title', '')} at {job.get('company', '')}")
                    except:
                        pass
                
                resume_text = ' '.join(text_parts)
                
                if len(resume_text.strip()) < 10:
                    # Not enough text - assign default score
                    new_score = 40
                    new_category = 'General'
                else:
                    # Use AI to analyze
                    try:
                        ai_analysis = await asyncio.wait_for(
                            ai_service.analyze_candidate(resume_text),
                            timeout=5
                        )
                        new_score = ai_analysis.get('quality_score', 50)
                        new_category = ai_analysis.get('job_category', 'General')
                    except:
                        new_score = 50
                        new_category = 'General'
                
                # Update database
                update_conn = db_service.get_connection()
                update_cursor = update_conn.cursor()
                update_cursor.execute("""
                    UPDATE candidates SET match_score = ?, job_category = ? WHERE id = ?
                """, (new_score, new_category, candidate_id))
                update_conn.commit()
                update_conn.close()
                
                processed += 1
                logger.info(f"✅ Reprocessed {name}: Score={new_score}%, Category={new_category}")
                
            except Exception as e:
                errors += 1
                logger.warning(f"Error reprocessing candidate {row[2]}: {str(e)[:50]}")
        
        return {
            "status": "success",
            "message": f"Reprocessed {processed} candidates",
            "processed": processed,
            "errors": errors
        }
        
    except Exception as e:
        logger.error(f"Reprocess error: {str(e)}")
        raise HTTPException(500, f"Error reprocessing: {str(e)}")

@app.get("/api/candidates/{candidate_id}")
async def get_candidate(candidate_id: str):
    """Get single candidate by ID"""
    try:
        conn = db_service.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(404, "Candidate not found")
        
        return db_service._row_to_candidate(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")


@app.get("/api/candidates/{candidate_id}/resume")
async def download_resume(candidate_id: str):
    """Download candidate's resume file"""
    from fastapi.responses import Response
    
    try:
        resume = await asyncio.to_thread(db_service.get_resume, candidate_id)
        
        if not resume:
            raise HTTPException(404, "Resume not found for this candidate")
        
        return Response(
            content=resume['file_data'],
            media_type=resume['content_type'],
            headers={
                'Content-Disposition': f'attachment; filename="{resume["filename"]}"'
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume download error: {str(e)}")
        raise HTTPException(500, f"Error downloading resume: {str(e)}")


# Resume upload endpoints
@app.post("/api/resumes/upload")
async def upload_resume(file: UploadFile = File(...)):
    """
    Upload a single resume file (PDF or DOCX).
    Parses the resume, runs AI analysis, and saves the candidate.
    """
    try:
        filename = file.filename or "unknown.pdf"
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in ('pdf', 'docx'):
            raise HTTPException(400, "Only PDF and DOCX files are supported.")

        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(400, "File too large. Max 10MB.")

        # Parse resume
        parsed = await resume_parser.parse_resume(content, filename)
        if not parsed.get('email'):
            # Generate a placeholder email from filename
            import hashlib
            file_hash = hashlib.md5(content[:1024]).hexdigest()[:8]
            clean_name = re.sub(r'[^a-zA-Z]', '', parsed.get('name', ''))[:20] or 'candidate'
            parsed['email'] = f"{clean_name.lower()}.{file_hash}@uploaded.local"

        candidate_id = f"upload_{parsed['email']}_{int(datetime.now().timestamp())}"

        # AI analysis
        resume_text = parsed.get('raw_text', '') or parsed.get('summary', '')
        ai_score = 50.0
        job_category = 'General'
        summary = parsed.get('summary', '')

        if resume_text.strip():
            try:
                ai_analysis = await asyncio.wait_for(
                    ai_service.analyze_candidate(resume_text),
                    timeout=AI_ANALYSIS_TIMEOUT
                )
                if ai_analysis:
                    ai_score = ai_analysis.get('quality_score', 50.0)
                    job_category = ai_analysis.get('job_category', 'General')
                    summary = ai_analysis.get('summary', summary)
                    if ai_analysis.get('skills'):
                        parsed['skills'] = ai_analysis['skills']
                    if ai_analysis.get('experience'):
                        parsed['experience'] = ai_analysis['experience']
            except Exception as ai_err:
                logger.warning(f"AI analysis failed for upload: {str(ai_err)[:100]}")

        # Infer category from skills if still General
        if job_category == 'General' and parsed.get('skills'):
            email_data = {'subject': filename, 'body': resume_text[:500]}
            job_category = await scraper_service.infer_job_category(email_data, parsed)

        # Determine status
        status = 'Strong' if ai_score >= 70 else ('Partial' if ai_score >= 40 else 'Reject')

        candidate = {
            'id': candidate_id,
            'email': parsed['email'],
            'name': parsed.get('name', 'Unknown'),
            'phone': parsed.get('phone', ''),
            'location': parsed.get('location', ''),
            'skills': parsed.get('skills', []),
            'experience': parsed.get('experience', 0),
            'education': json.dumps(parsed.get('education', [])) if isinstance(parsed.get('education'), list) else parsed.get('education', ''),
            'summary': summary,
            'workHistory': parsed.get('work_history', []),
            'linkedin': parsed.get('linkedin', ''),
            'status': status,
            'matchScore': round(ai_score, 1),
            'job_category': job_category,
            'appliedDate': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'raw_email_subject': f"Resume Upload: {filename}",
        }

        # Check if candidate with same email exists
        existing = db_service.get_candidate_by_email(parsed['email'])
        if existing:
            candidate['id'] = existing['id']
            db_service.update_candidate(candidate)
            logger.info(f"📝 Updated candidate from upload: {candidate['name']}")
        else:
            db_service.insert_candidate(candidate)
            logger.info(f"✨ New candidate from upload: {candidate['name']} ({candidate['email']}) - Score: {ai_score}")

        return {
            "status": "success",
            "candidate": {
                "id": candidate['id'],
                "name": candidate['name'],
                "email": candidate['email'],
                "matchScore": candidate['matchScore'],
                "jobCategory": job_category,
                "status": status,
                "skills": candidate['skills'],
            },
            "filename": filename,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume upload error: {str(e)}")
        raise HTTPException(500, f"Error processing resume: {str(e)}")


@app.post("/api/resumes/upload-multiple")
async def upload_multiple_resumes(files: List[UploadFile] = File(...)):
    """
    Upload multiple resume files at once.
    Returns results for each file.
    """
    results = []
    for file in files:
        try:
            filename = file.filename or "unknown.pdf"
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            if ext not in ('pdf', 'docx'):
                results.append({"filename": filename, "status": "error", "message": "Unsupported format. Only PDF/DOCX."})
                continue

            content = await file.read()
            if len(content) > 10 * 1024 * 1024:
                results.append({"filename": filename, "status": "error", "message": "File too large (max 10MB)."})
                continue

            parsed = await resume_parser.parse_resume(content, filename)
            if not parsed.get('email'):
                import hashlib
                file_hash = hashlib.md5(content[:1024]).hexdigest()[:8]
                clean_name = re.sub(r'[^a-zA-Z]', '', parsed.get('name', ''))[:20] or 'candidate'
                parsed['email'] = f"{clean_name.lower()}.{file_hash}@uploaded.local"

            candidate_id = f"upload_{parsed['email']}_{int(datetime.now().timestamp())}"

            resume_text = parsed.get('raw_text', '') or parsed.get('summary', '')
            ai_score = 50.0
            job_category = 'General'
            summary = parsed.get('summary', '')

            if resume_text.strip():
                try:
                    ai_analysis = await asyncio.wait_for(
                        ai_service.analyze_candidate(resume_text),
                        timeout=AI_ANALYSIS_TIMEOUT
                    )
                    if ai_analysis:
                        ai_score = ai_analysis.get('quality_score', 50.0)
                        job_category = ai_analysis.get('job_category', 'General')
                        summary = ai_analysis.get('summary', summary)
                        if ai_analysis.get('skills'):
                            parsed['skills'] = ai_analysis['skills']
                        if ai_analysis.get('experience'):
                            parsed['experience'] = ai_analysis['experience']
                except Exception:
                    pass

            if job_category == 'General' and parsed.get('skills'):
                email_data = {'subject': filename, 'body': resume_text[:500]}
                job_category = await scraper_service.infer_job_category(email_data, parsed)

            status = 'Strong' if ai_score >= 70 else ('Partial' if ai_score >= 40 else 'Reject')

            candidate = {
                'id': candidate_id,
                'email': parsed['email'],
                'name': parsed.get('name', 'Unknown'),
                'phone': parsed.get('phone', ''),
                'location': parsed.get('location', ''),
                'skills': parsed.get('skills', []),
                'experience': parsed.get('experience', 0),
                'education': json.dumps(parsed.get('education', [])) if isinstance(parsed.get('education'), list) else parsed.get('education', ''),
                'summary': summary,
                'workHistory': parsed.get('work_history', []),
                'linkedin': parsed.get('linkedin', ''),
                'status': status,
                'matchScore': round(ai_score, 1),
                'job_category': job_category,
                'appliedDate': datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                'raw_email_subject': f"Resume Upload: {filename}",
            }

            existing = db_service.get_candidate_by_email(parsed['email'])
            if existing:
                candidate['id'] = existing['id']
                db_service.update_candidate(candidate)
            else:
                db_service.insert_candidate(candidate)

            logger.info(f"✨ Processed upload: {candidate['name']} - Score: {ai_score}")

            results.append({
                "filename": filename,
                "status": "success",
                "candidate": {
                    "id": candidate['id'],
                    "name": candidate['name'],
                    "email": candidate['email'],
                    "matchScore": candidate['matchScore'],
                    "jobCategory": job_category,
                    "candidateStatus": status,
                },
            })

        except Exception as e:
            logger.error(f"Error processing {file.filename}: {str(e)}")
            results.append({"filename": file.filename or "unknown", "status": "error", "message": str(e)})

    success_count = sum(1 for r in results if r['status'] == 'success')
    return {
        "status": "completed",
        "total": len(files),
        "success": success_count,
        "failed": len(files) - success_count,
        "results": results,
    }


# ============================================================================
# AI ANALYSIS ENDPOINTS - Deep candidate analysis with pros/cons
# ============================================================================

@app.get("/api/ai/candidate/{candidate_id}/analysis")
async def get_candidate_ai_analysis(candidate_id: str):
    """
    Get deep AI analysis of a candidate including:
    - Detailed pros and cons
    - Career trajectory analysis
    - Hiring recommendation
    - Interview focus areas
    """
    try:
        # Get candidate from database
        conn = db_service.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(404, "Candidate not found")
        
        candidate = db_service._row_to_candidate(row)
        
        # Check cache first
        cache_key = f"deep_analysis_{candidate_id}"
        if cache_key in response_cache:
            cached = response_cache[cache_key]
            cached['from_cache'] = True
            return cached
        
        # Get OpenAI service
        from services.openai_service import get_openai_service
        openai_svc = get_openai_service()
        
        if not openai_svc:
            # Fallback: generate basic analysis without AI
            return {
                "candidate_id": candidate_id,
                "candidate_name": candidate['name'],
                "overall_score": candidate.get('matchScore', 50),
                "pros": [
                    f"Has {candidate.get('experience', 0)} years of experience",
                    f"Skills include: {', '.join(candidate.get('skills', [])[:5]) or 'Not specified'}",
                    "Resume available in database"
                ],
                "cons": [
                    "AI analysis unavailable - configure OPENAI_API_KEY"
                ],
                "hiring_recommendation": {
                    "verdict": "Review Needed",
                    "confidence": 50,
                    "ideal_roles": [],
                    "interview_focus_areas": ["Technical skills", "Experience verification"]
                },
                "ai_powered": False
            }
        
        # Run deep AI analysis
        analysis = openai_svc.analyze_candidate_deep(candidate)
        
        result = {
            "candidate_id": candidate_id,
            "candidate_name": candidate['name'],
            **analysis,
            "ai_powered": True
        }
        
        # Cache for 1 hour
        response_cache[cache_key] = result
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        raise HTTPException(500, f"Error analyzing candidate: {str(e)}")


@app.post("/api/ai/match-job")
async def match_candidates_to_job_description(
    job_description: str = Body(..., embed=True),
    top_n: int = Body(10, embed=True),
    min_experience: Optional[int] = Body(None, embed=True)
):
    """
    Match candidates from database against a job description.
    Returns ranked list with AI scores specific to this JD.
    
    Body params:
    - job_description: The full job description text
    - top_n: Number of top candidates to return (default 10)
    - min_experience: Minimum years of experience filter (optional)
    """
    try:
        if not job_description or len(job_description.strip()) < 50:
            raise HTTPException(400, "Job description must be at least 50 characters")
        
        # Get candidates from database
        filters = {}
        if min_experience:
            filters['min_experience'] = min_experience
        
        candidates = await asyncio.to_thread(
            db_service.get_candidates_paginated,
            1, 100, filters  # Get up to 100 candidates for matching
        )
        
        if not candidates:
            return {
                "status": "no_candidates",
                "message": "No candidates in database to match",
                "rankings": [],
                "job_analysis": {}
            }
        
        # Get OpenAI service
        from services.openai_service import get_openai_service
        openai_svc = get_openai_service()
        
        if not openai_svc:
            # Fallback: basic keyword matching
            jd_lower = job_description.lower()
            for c in candidates:
                skill_matches = sum(1 for s in c.get('skills', []) if s.lower() in jd_lower)
                c['job_fit_score'] = min(40 + skill_matches * 10, 95)
            
            candidates.sort(key=lambda x: x.get('job_fit_score', 0), reverse=True)
            
            return {
                "status": "basic_match",
                "message": "Basic keyword matching (AI unavailable)",
                "rankings": [
                    {
                        "rank": i + 1,
                        "candidate_id": c['id'],
                        "candidate_name": c['name'],
                        "job_fit_score": c.get('job_fit_score', 50),
                        "match_reasons": [f"Skills: {', '.join(c.get('skills', [])[:5])}"],
                        "recommendation": "Review Needed"
                    }
                    for i, c in enumerate(candidates[:top_n])
                ],
                "job_analysis": {},
                "ai_powered": False
            }
        
        # Run AI job matching
        result = openai_svc.match_candidates_to_job(job_description, candidates, top_n)
        result['ai_powered'] = True
        result['total_candidates_searched'] = len(candidates)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job matching error: {e}")
        raise HTTPException(500, f"Error matching candidates: {str(e)}")


@app.post("/api/ai/compare-candidates")
async def compare_candidates(
    candidate_ids: List[str] = Body(..., embed=True),
    job_description: Optional[str] = Body(None, embed=True)
):
    """
    Generate AI comparison of multiple candidates.
    Useful for final hiring decisions.
    """
    try:
        if len(candidate_ids) < 2:
            raise HTTPException(400, "Need at least 2 candidates to compare")
        if len(candidate_ids) > 5:
            raise HTTPException(400, "Can compare up to 5 candidates at a time")
        
        # Get candidates from database
        candidates = []
        conn = db_service.get_connection()
        cursor = conn.cursor()
        
        for cid in candidate_ids:
            cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (cid,))
            row = cursor.fetchone()
            if row:
                candidates.append(db_service._row_to_candidate(row))
        
        conn.close()
        
        if len(candidates) < 2:
            raise HTTPException(404, "Could not find enough candidates to compare")
        
        # Get OpenAI service
        from services.openai_service import get_openai_service
        openai_svc = get_openai_service()
        
        if not openai_svc:
            # Basic comparison without AI
            candidates.sort(key=lambda x: x.get('matchScore', 0), reverse=True)
            return {
                "comparison_matrix": [
                    {
                        "name": c['name'],
                        "overall_rank": i + 1,
                        "score": c.get('matchScore', 50),
                        "key_strengths": c.get('skills', [])[:3],
                        "key_weaknesses": ["AI analysis unavailable"],
                        "best_for": "General roles",
                        "risk_level": "unknown"
                    }
                    for i, c in enumerate(candidates)
                ],
                "head_to_head": {
                    "winner": candidates[0]['name'],
                    "reasoning": "Highest match score",
                    "runner_up": candidates[1]['name'] if len(candidates) > 1 else None
                },
                "recommendation": "Configure OPENAI_API_KEY for detailed comparison",
                "ai_powered": False
            }
        
        # Run AI comparison
        result = openai_svc.generate_candidate_comparison(candidates, job_description)
        result['ai_powered'] = True
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        raise HTTPException(500, f"Error comparing candidates: {str(e)}")


@app.post("/api/ai/chat")
async def ai_chat(
    message: str = Body(..., embed=True),
    include_candidates: bool = Body(True, embed=True)
):
    """
    Enhanced AI chat with database context.
    The AI has access to your candidate database for intelligent responses.
    """
    try:
        candidates_data = None
        context = None
        
        if include_candidates:
            # Get candidate summary for context
            stats = await asyncio.to_thread(db_service.get_statistics)
            candidates = await asyncio.to_thread(
                db_service.get_candidates_paginated, 1, 20, {}
            )
            candidates_data = candidates
            context = f"Database has {stats.get('total_candidates', 0)} candidates across {len(stats.get('categories', {}))} job categories."
        
        from services.openai_service import get_openai_service
        openai_svc = get_openai_service()
        
        if not openai_svc:
            return {
                "response": "AI service unavailable. Please configure OPENAI_API_KEY in your environment.",
                "ai_powered": False
            }
        
        response = openai_svc.chat_with_ai(message, context, candidates_data)
        
        return {
            "response": response,
            "ai_powered": True,
            "context_included": include_candidates
        }
        
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return {
            "response": f"Error: {str(e)}",
            "ai_powered": False
        }


# ============================================================================
# REAL-TIME STATS ENDPOINT - For live updates
# ============================================================================

@app.get("/api/stats/live")
async def get_live_stats():
    """
    Get real-time statistics for dashboard updates.
    Lightweight endpoint for frequent polling.
    """
    try:
        conn = db_service.get_connection()
        cursor = conn.cursor()
        
        # Get counts efficiently
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
        
        conn.close()
        
        return {
            "total_candidates": total,
            "new_24h": new_24h,
            "categories": categories,
            "category_count": len(categories),
            "average_score": round(avg_score, 1),
            "strong_matches": strong_matches,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Live stats error: {e}")
        return {
            "total_candidates": 0,
            "new_24h": 0,
            "categories": {},
            "category_count": 0,
            "average_score": 0,
            "strong_matches": 0,
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }


# JOB DESCRIPTIONS - REMOVED
# System now auto-generates job categories from candidate emails
# No manual job description upload needed

# @app.post("/api/job-descriptions/analyze")
# @app.post("/api/job-descriptions/upload")
# ^^^ REMOVED - Auto-categorization via AI

@app.get("/api/stats")
async def get_stats():
    """Get platform statistics with high-volume support"""
    try:
        # Use optimized database statistics method
        stats = await asyncio.to_thread(db_service.get_statistics)
        
        # Add AI service stats if available
        ai_stats = {}
        try:
            ai_stats = ai_service.get_cache_stats()
        except Exception:
            pass
        
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

# High-volume batch processing endpoint
@app.post("/api/candidates/batch")
async def batch_import_candidates(
    candidates: List[dict],
    analyze: bool = True
):
    """
    Import candidates in batch for high-volume scenarios (10,000+)
    - Uses batch database inserts for speed
    - Optional AI analysis with batch processing
    """
    try:
        start_time = time.time()
        logger.info(f"📊 Batch import started: {len(candidates)} candidates")
        
        # AI analyze in batches if requested
        if analyze and len(candidates) > 0:
            texts = [c.get('summary', '') or c.get('resume_text', '') for c in candidates]
            
            # Process in small batches to avoid memory issues
            BATCH_SIZE = 50
            for i in range(0, len(candidates), BATCH_SIZE):
                batch_texts = texts[i:i + BATCH_SIZE]
                batch_candidates = candidates[i:i + BATCH_SIZE]
                
                for idx, text in enumerate(batch_texts):
                    if text and len(text) > 20:
                        try:
                            analysis = await asyncio.wait_for(
                                ai_service.analyze_candidate(text),
                                timeout=AI_ANALYSIS_TIMEOUT
                            )
                            if analysis:
                                batch_candidates[idx].update({
                                    'job_category': analysis.get('job_category', 'General'),
                                    'matchScore': analysis.get('quality_score', 50),
                                    'skills': analysis.get('skills', []),
                                    'experience': analysis.get('experience', 0),
                                    'education': analysis.get('education', []),
                                    'phone': analysis.get('phone') or batch_candidates[idx].get('phone', ''),
                                    'location': analysis.get('location') or batch_candidates[idx].get('location', ''),
                                    'linkedin': analysis.get('linkedin') or batch_candidates[idx].get('linkedin', ''),
                                })
                        except Exception as e:
                            logger.warning(f"AI batch analysis failed for item {i+idx}: {str(e)[:50]}")
                
                if (i + BATCH_SIZE) % 500 == 0:
                    logger.info(f"📊 AI Progress: {min(i + BATCH_SIZE, len(candidates))}/{len(candidates)}")
        
        # Bulk insert to database
        result = await asyncio.to_thread(
            db_service.insert_candidates_batch, 
            candidates
        )
        
        elapsed = time.time() - start_time
        rate = len(candidates) / elapsed if elapsed > 0 else 0
        
        logger.info(f"✅ Batch complete: {result['inserted']} inserted, {result['updated']} updated in {elapsed:.2f}s ({rate:.0f}/sec)")
        
        return {
            "status": "completed",
            "total": len(candidates),
            "inserted": result['inserted'],
            "updated": result['updated'],
            "elapsed_seconds": round(elapsed, 2),
            "rate_per_second": round(rate, 1)
        }
        
    except Exception as e:
        logger.error(f"Batch import error: {e}")
        raise HTTPException(500, f"Batch import failed: {str(e)}")

# LinkedIn Profile Import Endpoint (for browser extension)
class LinkedInProfileImport(BaseModel):
    name: str
    email: str
    phone: Optional[str] = ""
    location: Optional[str] = ""
    linkedin: str
    source: str = "linkedin_extension"
    job_category: Optional[str] = "General"
    skills: Optional[List[str]] = []
    experience: Optional[float] = 0
    resume_text: Optional[str] = ""
    profile_image: Optional[str] = ""
    headline: Optional[str] = ""
    education: Optional[List[dict]] = []
    work_experience: Optional[List[dict]] = []
    certifications: Optional[List[dict]] = []
    languages: Optional[List[dict]] = []
    scraped_at: Optional[str] = None

@app.post("/api/candidates/linkedin")
async def import_linkedin_profile(profile: LinkedInProfileImport):
    """
    Import a candidate from LinkedIn profile scraped by browser extension.
    Analyzes the profile and stores in database.
    """
    try:
        logger.info(f"📥 LinkedIn import: {profile.name}")
        
        # Check for existing candidate with same LinkedIn URL
        existing = db_service.get_candidate_by_linkedin(profile.linkedin)
        if existing:
            logger.info(f"Updating existing LinkedIn profile: {profile.name}")
            # Update existing record
            candidate_id = existing.get('id')
        else:
            candidate_id = None
        
        # Analyze the profile using AI
        analysis = None
        if profile.resume_text and len(profile.resume_text) > 50:
            try:
                analysis = await asyncio.wait_for(
                    ai_service.analyze_candidate(profile.resume_text),
                    timeout=AI_ANALYSIS_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning(f"AI analysis timeout for {profile.name}")
            except Exception as e:
                logger.warning(f"AI analysis error for {profile.name}: {e}")
        
        # Build candidate record
        candidate_data = {
            "id": candidate_id or f"linkedin_{datetime.now().strftime('%Y%m%d%H%M%S')}_{profile.name.replace(' ', '_')}",
            "name": profile.name,
            "email": profile.email,
            "phone": profile.phone or "",
            "location": profile.location or "",
            "linkedin": profile.linkedin,
            "source": profile.source,
            "skills": analysis.get('skills', profile.skills) if analysis else (profile.skills or []),
            "experience": profile.experience or 0,
            "matchScore": analysis.get('quality_score', 50) if analysis else 50,
            "status": "new",
            "appliedDate": profile.scraped_at or datetime.now().isoformat(),
            "summary": profile.headline or "",
            "jobCategory": analysis.get('job_category', profile.job_category) if analysis else profile.job_category,
            "resumeText": profile.resume_text or "",
            "profileImage": profile.profile_image or "",
            "education": profile.education or [],
            "workExperience": profile.work_experience or [],
            "certifications": profile.certifications or [],
            "languages": profile.languages or [],
            "aiAnalysis": analysis
        }
        
        # Store in database
        if existing:
            db_service.update_candidate(candidate_id, candidate_data)
        else:
            db_service.add_candidate(candidate_data)
        
        logger.info(f"✅ LinkedIn profile imported: {profile.name} (Score: {candidate_data['matchScore']})")
        
        return {
            "success": True,
            "id": candidate_data["id"],
            "name": profile.name,
            "matchScore": candidate_data["matchScore"],
            "skills": candidate_data["skills"],
            "jobCategory": candidate_data["jobCategory"]
        }
        
    except Exception as e:
        logger.error(f"LinkedIn import error: {e}")
        raise HTTPException(500, f"Failed to import LinkedIn profile: {str(e)}")

# Stream candidates endpoint for large exports
@app.get("/api/candidates/stream")
async def stream_all_candidates(batch_size: int = 100):
    """
    Stream all candidates for large exports (10,000+)
    Returns JSON array streamed in batches
    """
    from fastapi.responses import StreamingResponse
    
    async def generate():
        yield "["
        first = True
        for batch in db_service.get_candidates_stream(batch_size):
            for candidate in batch:
                if not first:
                    yield ","
                yield json.dumps(candidate)
                first = False
        yield "]"
    
    return StreamingResponse(
        generate(),
        media_type="application/json",
        headers={"X-Stream-Type": "batch"}
    )

# Matching endpoints
@app.post("/api/matching/match-candidates", response_model=List[MatchResult])
async def match_candidates(
    job_description_id: str,
    candidate_ids: Optional[List[str]] = None
):
    """
    Match candidates against a job description using LLM + semantic + TF-IDF matching.
    Resolves IDs to data, then calls the multi-tier matching engine.
    """
    try:
        # Resolve job description from database
        conn = db_service.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT description, title, required_skills FROM job_descriptions WHERE id = ?", (job_description_id,))
        jd_row = cursor.fetchone()
        if not jd_row:
            conn.close()
            raise HTTPException(404, f"Job description not found: {job_description_id}")
        
        job_text = f"{jd_row[1] or ''}\n{jd_row[0] or ''}\nRequired Skills: {jd_row[2] or ''}"
        
        # Resolve candidates
        if candidate_ids:
            placeholders = ','.join(['?' for _ in candidate_ids])
            cursor.execute(f"SELECT * FROM candidates WHERE id IN ({placeholders}) AND is_active = 1", candidate_ids)
        else:
            cursor.execute("SELECT * FROM candidates WHERE is_active = 1 ORDER BY match_score DESC LIMIT 100")
        
        rows = cursor.fetchall()
        conn.close()
        
        candidates = [db_service._row_to_candidate(row) for row in rows]
        
        if not candidates:
            return []
        
        results = await matching_engine.match_candidates(job_text, candidates)
        return results
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error matching candidates: {str(e)}")

@app.post("/api/matching/evaluate-candidate")
async def evaluate_candidate(candidate_id: str, job_description_id: str):
    """
    Detailed AI evaluation of a single candidate using LLM.
    Resolves IDs to data, then calls the multi-tier matching engine.
    """
    try:
        # Resolve job description
        conn = db_service.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT description, title, required_skills FROM job_descriptions WHERE id = ?", (job_description_id,))
        jd_row = cursor.fetchone()
        if not jd_row:
            conn.close()
            raise HTTPException(404, f"Job description not found: {job_description_id}")
        
        job_text = f"{jd_row[1] or ''}\n{jd_row[0] or ''}\nRequired Skills: {jd_row[2] or ''}"
        
        # Resolve candidate
        cursor.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
        cand_row = cursor.fetchone()
        conn.close()
        
        if not cand_row:
            raise HTTPException(404, f"Candidate not found: {candidate_id}")
        
        candidate_data = db_service._row_to_candidate(cand_row)
        
        evaluation = await matching_engine.evaluate_candidate(candidate_data, job_text)
        return evaluation
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error evaluating candidate: {str(e)}")

# NOTE: Candidate management routes are defined earlier (line ~770) with proper database queries
# Do not duplicate them here - removed duplicate empty routes

# Email integration endpoints
@app.post("/api/email/connect")
async def connect_email_account(request: EmailConnectRequest):
    """
    Connect email account (Gmail, Outlook, Yahoo, etc.)
    Supports OAuth2 and app passwords
    """
    try:
        result = await email_parser.connect_email_account(
            provider=request.provider,
            email_address=request.email,
            password=request.password,
            access_token=request.access_token,
            custom_imap_server=request.custom_imap_server
        )
        
        # Remove connection object before returning (can't be serialized)
        if 'connection' in result:
            if result['connection']:
                result['connection'].logout()  # Close connection
            del result['connection']
        
        return result
    except Exception as e:
        raise HTTPException(500, f"Error connecting email: {str(e)}")

@app.post("/api/email/sync")
async def sync_email_applications(request: EmailSyncRequest):
    """
    Sync and parse candidate applications from email
    Supports both OAuth2 (Microsoft Graph) and IMAP
    """
    try:
        # OAuth2 Mode - Use Microsoft Graph API directly
        if request.access_token:
            client_id = os.getenv('MICROSOFT_CLIENT_ID', 'dummy')
            client_secret = os.getenv('MICROSOFT_CLIENT_SECRET', 'dummy')
            tenant_id = os.getenv('MICROSOFT_TENANT_ID', 'common')
            
            graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id)
            graph_service.access_token = request.access_token
            graph_service.token_expiry = datetime.now() + timedelta(hours=1)
            
            # Fetch emails via Microsoft Graph
            messages_result = await graph_service.get_messages(
                folder=request.folder.lower(),
                top=request.limit
            )
            
            if messages_result['status'] != 'success':
                raise HTTPException(500, f"Failed to fetch emails: {messages_result.get('message')}")
            
            candidates = []
            for message in messages_result['messages']:
                # Parse candidate from message
                candidate_info = await graph_service.parse_email_for_candidate(message)
                
                # Get attachments if present
                if message.get('hasAttachments'):
                    attachments_result = await graph_service.get_message_with_attachments(message['id'])
                    if attachments_result['status'] == 'success':
                        candidate_info['attachments'] = attachments_result['attachments']
                
                candidates.append(candidate_info)
            
            return {
                'status': 'success',
                'candidates_found': len(candidates),
                'candidates': candidates,
                'auth_type': 'oauth2'
            }
        
        # IMAP Mode - Traditional password authentication
        connection_result = await email_parser.connect_email_account(
            provider=request.provider,
            email_address=request.email,
            password=request.password,
            access_token=None
        )
        
        if connection_result['status'] != 'connected':
            raise HTTPException(400, connection_result.get('error', 'Connection failed'))
        
        # Fetch and parse emails
        mail_connection = connection_result['connection']
        candidates = await email_parser.fetch_candidate_emails(
            mail_connection=mail_connection,
            folder=request.folder,
            limit=request.limit
        )
        
        # Parse attachments and save to database
        saved_count = 0
        ai_processed_count = 0
        
        # Batch process candidates for better performance
        async def process_candidate(candidate):
            """Process single candidate with AI and save to DB"""
            nonlocal saved_count, ai_processed_count
            
            try:
                candidate_data = {
                    'name': candidate.get('from_name', 'Unknown'),
                    'email': candidate.get('from_email', ''),
                    'phone': candidate.get('extracted_info', {}).get('phone', ''),
                    'location': candidate.get('extracted_info', {}).get('location', ''),
                    'experience': candidate.get('extracted_info', {}).get('experience', ''),
                    'skills': candidate.get('extracted_info', {}).get('skills', ''),
                    'education': candidate.get('extracted_info', {}).get('education', ''),
                    'resume_text': candidate.get('body', ''),
                    'source': f"Email - {request.provider}",
                    'application_date': candidate.get('date', ''),
                    'notes': candidate.get('subject', '')
                }
                
                # AI Processing: Analyze candidate with timeout fallback
                try:
                    resume_content = f"{candidate.get('body', '')}\n\n{candidate.get('extracted_info', {}).get('text', '')}"
                    
                    if resume_content.strip():
                        # Use Local AI ONLY (zero cost, no OpenAI fallback)
                        try:
                            ai_analysis = await asyncio.wait_for(
                                ai_service.analyze_candidate(resume_content),
                                timeout=AI_ANALYSIS_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            logger.warning(f"⏱️ Local AI timeout for {candidate_data['name']} - using smart defaults")
                            ai_analysis = {
                                'quality_score': 45,
                                'job_category': 'General'
                            }
                        except Exception:
                            ai_analysis = {}
                        
                        # Enrich candidate data with AI insights
                        if ai_analysis:
                            candidate_data['skills'] = ai_analysis.get('skills', candidate_data['skills'])
                            candidate_data['experience'] = ai_analysis.get('experience', candidate_data['experience'])
                            candidate_data['education'] = ai_analysis.get('education', candidate_data['education'])
                            candidate_data['job_category'] = ai_analysis.get('job_category', 'General')
                            candidate_data['matchScore'] = ai_analysis.get('quality_score', 50)
                            candidate_data['summary'] = ai_analysis.get('summary', candidate_data.get('summary', ''))
                            ai_processed_count += 1
                    
                except Exception as ai_error:
                    logger.warning(f"AI processing failed for {candidate_data['name']}: {str(ai_error)}")
                
                # Save to database with semaphore (prevent DB lock)
                async with db_semaphore:
                    existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate_data['email'])
                    if existing:
                        await asyncio.to_thread(db_service.update_candidate, candidate_data)
                    else:
                        await asyncio.to_thread(db_service.insert_candidate, candidate_data)
                        saved_count += 1
                
                return True
            except Exception as e:
                logger.error(f"Error processing candidate: {str(e)}")
                return False
        
        # Process candidates in parallel batches (10 at a time to avoid overwhelming)
        batch_size = 10
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            await asyncio.gather(*[process_candidate(c) for c in batch], return_exceptions=True)
            logger.info(f"Processed batch {i//batch_size + 1}/{(len(candidates)-1)//batch_size + 1}")
        
        return {
            'status': 'success',
            'candidates_found': len(candidates),
            'candidates_saved': saved_count,
            'ai_processed': ai_processed_count,
            'candidates': candidates,
            'auth_type': 'imap',
            'processing_time': f"{(time.time() - time.time()):.2f}s"
        }
    
    except Exception as e:
        import traceback
        logger.error(f"Error syncing emails: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"Error syncing emails: {str(e)}")

@app.post("/api/auth/auto-authenticate")
async def auto_authenticate():
    """
    Automatically authenticate using credentials from .env
    Stores token for future use - no need to re-authenticate
    Automatically triggers email sync after authentication
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS')
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        if not all([email_address, client_id, client_secret, tenant_id]):
            raise HTTPException(400, "Microsoft OAuth2 not configured. Set MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, MICROSOFT_TENANT_ID, and EMAIL_ADDRESS in .env")
        
        # Initialize Graph service with user email for application permissions
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
        
        # Use client credentials flow (doesn't require user interaction)
        result = graph_service.authenticate_with_credentials()
        
        if result['status'] == 'success':
            # Save token to storage
            token_storage = get_token_storage()
            token_storage.save_token(
                email=email_address,
                access_token=result['access_token'],
                refresh_token=result.get('refresh_token'),
                expires_in=result['expires_in'],
                auth_type='application'  # Client credentials = application permissions
            )
            
            logger.info(f"✅ Auto-authentication successful for {email_address}")
            
            # Trigger background sync after successful authentication
            # Don't wait for it to complete
            asyncio.create_task(trigger_reset_and_reparse(email_address))
            
            return {
                'status': 'authenticated',
                'email': email_address,
                'provider': 'microsoft',
                'message': f'Successfully authenticated {email_address} - no re-authentication needed. Starting email sync...',
                'token_expires_in': result['expires_in']
            }
        else:
            raise HTTPException(400, result.get('error', 'Authentication failed'))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auto-authentication error: {str(e)}")
        raise HTTPException(500, f"Error during auto-authentication: {str(e)}")


async def trigger_reset_and_reparse(email_address: str):
    """Background task to sync emails after authentication - INCREMENTAL (no data loss)"""
    try:
        logger.info("🔄 Auto-triggered email sync after authentication (incremental - keeping existing data)...")
        
        # Clear response cache only
        response_cache.clear()
        
        # NOTE: We do NOT clear candidates - this is incremental sync
        # Existing candidates are preserved, only new ones are added/updated
        
        # Get current candidate count
        current_count = await asyncio.to_thread(db_service.get_total_candidates)
        logger.info(f"📊 Starting with {current_count} existing candidates in database")
        
        # Get token and fetch emails
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        
        if not token_data:
            logger.warning("No token found for auto-triggered sync")
            return
        
        # Initialize Graph service - use auth_type from saved token (delegated for user login, application for client credentials)
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        # Get auth type from saved token - defaults to 'delegated' for browser-based OAuth
        saved_auth_type = token_data.get('auth_type', 'delegated')
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
        graph_service.access_token = token_data['access_token']
        graph_service.auth_type = saved_auth_type  # Use saved auth type (delegated uses /me/, application uses /users/)
        graph_service.token_expiry = datetime.fromisoformat(token_data['expires_at'])
        
        logger.info(f"🔑 Using {saved_auth_type} permissions for Graph API")
        
        # Fetch ALL emails from entire inbox history
        logger.info("📧 Fetching ALL emails from inbox (incremental sync)...")
        result = await graph_service.get_messages(folder='inbox', top=100000, fetch_all=True)
        
        if result['status'] != 'success':
            logger.error(f"Failed to fetch emails: {result.get('message')}")
            return
        
        messages = result['messages']
        logger.info(f"📧 Found {len(messages)} emails to process")
        
        # Process messages
        new_count = 0
        updated_count = 0
        skipped_count = 0
        ai_analyzed_count = 0
        
        async def process_message(msg):
            nonlocal new_count, updated_count, skipped_count, ai_analyzed_count
            try:
                sender = msg.get('from', {}).get('emailAddress', {})
                sender_email = sender.get('address', '')
                sender_name = sender.get('name', '')
                
                if not sender_email:
                    return
                
                # Check if candidate already exists
                existing = await asyncio.to_thread(db_service.get_candidate_by_email, sender_email)
                
                # If exists and has a good score, skip processing
                if existing and existing.get('matchScore', 0) > 35:
                    skipped_count += 1
                    return
                
                subject = msg.get('subject', '')
                body = msg.get('body', {}).get('content', '')
                
                # Get attachments
                attachments = []
                if msg.get('hasAttachments'):
                    attach_result = await graph_service.get_message_with_attachments(msg['id'])
                    if attach_result['status'] == 'success':
                        attachments = attach_result['attachments']
                
                # Use actual email received date from Graph API
                received_dt = msg.get('receivedDateTime')
                if received_dt:
                    try:
                        received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                    except:
                        received_date = datetime.now()
                else:
                    received_date = datetime.now()
                
                email_data = {
                    'subject': subject,
                    'sender_email': sender_email,
                    'sender_name': sender_name,
                    'body': body,
                    'attachments': attachments,
                    'received_date': received_date
                }
                
                # Extract candidate from email
                candidate = await scraper_service.extract_candidate_from_email(email_data)
                if not candidate or not candidate.get('email'):
                    return
                
                # AI Analysis - build text from all available sources
                resume_text = candidate.get('resume_text', '')
                if not resume_text or len(resume_text.strip()) < 20:
                    # Fallback: build from available data
                    parts = []
                    if candidate.get('summary'):
                        parts.append(candidate['summary'])
                    if candidate.get('skills'):
                        skills = candidate['skills']
                        if isinstance(skills, list):
                            parts.append(' '.join(skills))
                        else:
                            parts.append(str(skills))
                    if subject:
                        parts.append(subject)
                    if body:
                        # Use first 500 chars of cleaned body
                        clean_body = body[:500].replace('<', ' ').replace('>', ' ')
                        parts.append(clean_body)
                    resume_text = ' '.join(parts)
                
                # Always try AI analysis if we have any text
                if resume_text and len(resume_text.strip()) >= 10:
                    try:
                        ai_analysis = await asyncio.wait_for(
                            ai_service.analyze_candidate(resume_text),
                            timeout=AI_ANALYSIS_TIMEOUT
                        )
                        if ai_analysis:
                            candidate['job_category'] = ai_analysis.get('job_category', candidate.get('job_category', 'General'))
                            candidate['matchScore'] = ai_analysis.get('quality_score', 50)
                            if ai_analysis.get('skills'):
                                ai_skills = ai_analysis['skills']
                                if isinstance(ai_skills, list) and len(ai_skills) > len(candidate.get('skills', [])):
                                    candidate['skills'] = ai_skills
                            if ai_analysis.get('experience') and ai_analysis['experience'] > candidate.get('experience', 0):
                                candidate['experience'] = ai_analysis['experience']
                            if ai_analysis.get('summary'):
                                candidate['summary'] = ai_analysis['summary']
                            ai_analyzed_count += 1
                            logger.info(f"✅ Local AI scored {sender_email}: {candidate['matchScore']}%")
                    except asyncio.TimeoutError:
                        # Local AI timeout - use default scoring (NO OpenAI to save costs)
                        logger.warning(f"⏱️ Local AI timeout for {sender_email} - using smart defaults (OpenAI disabled)")
                        # Apply intelligent defaults based on available data
                        candidate['matchScore'] = 45 + min(20, len(candidate.get('skills', [])) * 2)
                        if candidate.get('experience', 0) > 0:
                            candidate['matchScore'] += min(15, candidate['experience'] * 2)
                    except Exception as ai_err:
                        logger.warning(f"AI analysis error: {str(ai_err)[:50]} - using defaults")
                        candidate['matchScore'] = 42
                else:
                    # No text to analyze - set default score based on what we have
                    candidate['matchScore'] = 40  # Default for minimal info candidates
                
                # Ensure score is never 0 (indicates unprocessed)
                if candidate.get('matchScore', 0) == 0:
                    candidate['matchScore'] = 35
                
                # Save resume and candidate
                resume_file = candidate.pop('resume_file_data', None)
                resume_filename = candidate.pop('resume_filename', None)
                
                await asyncio.to_thread(db_service.insert_candidate, candidate)
                
                if resume_file and resume_filename:
                    content_type = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                    await asyncio.to_thread(db_service.save_resume, candidate['id'], resume_filename, resume_file, content_type)
                
                if existing:
                    updated_count += 1
                else:
                    new_count += 1
                    
            except Exception as e:
                logger.warning(f"Error processing message: {str(e)[:100]}")
        
        # Process in batches
        BATCH_SIZE = 5
        for i in range(0, len(messages), BATCH_SIZE):
            batch = messages[i:i+BATCH_SIZE]
            await asyncio.gather(*[process_message(msg) for msg in batch], return_exceptions=True)
            
            if len(messages) > 50 and (i + BATCH_SIZE) % 50 == 0:
                logger.info(f"📊 Progress: {min(i+BATCH_SIZE, len(messages))}/{len(messages)} emails, {new_count} new, {updated_count} updated, {skipped_count} skipped...")
        
        final_count = await asyncio.to_thread(db_service.get_total_candidates)
        logger.info(f"✅ Incremental sync complete! {new_count} new, {updated_count} updated, {skipped_count} skipped from {len(messages)} emails")
        logger.info(f"📊 Database: {current_count} → {final_count} candidates (no data lost!)")
        
    except Exception as e:
        logger.error(f"Error in auto-triggered sync: {str(e)}")

@app.get("/api/email/oauth2/url")
async def get_oauth2_url_simple():
    """
    Get Microsoft OAuth2 authorization URL using config from .env
    Simple endpoint - no parameters needed
    """
    try:
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID', 'common')
        redirect_uri = os.getenv('MICROSOFT_REDIRECT_URI', 'http://localhost:3000/auth/callback')
        
        if not client_id:
            raise HTTPException(400, "Microsoft OAuth2 not configured. Set MICROSOFT_CLIENT_ID in .env")
        
        graph_service = MicrosoftGraphService(client_id, '', tenant_id)
        auth_url = graph_service.get_authorization_url(redirect_uri)
        
        return {
            'status': 'success',
            'auth_url': auth_url,
            'provider': 'microsoft'
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error generating authorization URL: {str(e)}")

@app.get("/api/email/oauth2/authorize")
async def get_oauth2_authorization_url(provider: str, redirect_uri: str):
    """
    Get OAuth2 authorization URL for Microsoft/Google
    User will be redirected to this URL to grant permissions
    """
    try:
        if provider.lower() in ['outlook', 'office365', 'microsoft']:
            # Microsoft OAuth2
            client_id = os.getenv('MICROSOFT_CLIENT_ID')
            tenant_id = os.getenv('MICROSOFT_TENANT_ID', 'common')
            
            if not client_id:
                raise HTTPException(400, "Microsoft OAuth2 not configured. Set MICROSOFT_CLIENT_ID in .env")
            
            graph_service = MicrosoftGraphService(client_id, '', tenant_id)
            auth_url = graph_service.get_authorization_url(redirect_uri)
            
            return {
                'status': 'success',
                'authorization_url': auth_url,
                'provider': 'microsoft'
            }
        
        elif provider.lower() == 'gmail':
            # Google OAuth2 (future implementation)
            raise HTTPException(501, "Gmail OAuth2 coming soon. Use app password for now.")
        
        else:
            raise HTTPException(400, f"OAuth2 not supported for provider: {provider}")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error generating authorization URL: {str(e)}")

@app.post("/api/email/oauth2/callback")
async def oauth2_callback(request: OAuth2CallbackRequest):
    """
    Handle OAuth2 callback after user grants permissions
    Exchange authorization code for access token and SAVE to storage
    """
    try:
        # Microsoft OAuth2
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID', 'common')
        email_address = os.getenv('EMAIL_ADDRESS')  # Primary email account
        
        if not all([client_id, client_secret, email_address]):
            raise HTTPException(400, "Microsoft OAuth2 not configured. Set MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, and EMAIL_ADDRESS in .env")
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id)
        result = await graph_service.authenticate(request.code, request.redirect_uri)
        
        if result['status'] == 'success':
            # Save token to storage - ENSURE refresh_token is saved for auto-refresh
            token_storage = get_token_storage()
            refresh_token = result.get('refresh_token')
            
            if not refresh_token:
                logger.warning("⚠️ No refresh token received from Microsoft. Auto-refresh will not work!")
            else:
                logger.info(f"✅ Refresh token received - auto-refresh enabled")
            
            token_storage.save_token(
                email=email_address,
                access_token=result['access_token'],
                refresh_token=refresh_token,
                expires_in=result['expires_in'],
                auth_type='delegated'  # User login = delegated permissions (uses /me/ endpoint)
            )
            
            logger.info(f"✅ OAuth2 token saved for {email_address}")
            
            return {
                'status': 'connected',
                'email': email_address,
                'provider': 'microsoft',
                'expires_in': result['expires_in'],
                'message': f'Successfully authenticated {email_address} with Microsoft OAuth2'
            }
        else:
            raise HTTPException(400, result.get('error', 'Authentication failed'))
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error processing OAuth2 callback: {str(e)}")

@app.post("/api/email/sync-now")
async def sync_emails_now():
    """
    Trigger immediate email sync using saved OAuth2 token
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS')
        
        if not email_address:
            raise HTTPException(400, "No email configured in .env")
        
        # Check if we have a token
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        
        if not token_data:
            raise HTTPException(401, "No OAuth2 token found. Please authenticate first.")
        
        # Trigger sync in background
        asyncio.create_task(trigger_reset_and_reparse(email_address))
        
        return {
            'status': 'syncing',
            'message': 'Email sync started in background',
            'email': email_address
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync error: {str(e)}")
        raise HTTPException(500, f"Error starting sync: {str(e)}")


# ============================================
# REAL-TIME EMAIL PROCESSING
# ============================================

async def process_single_email(message_id: str, graph_service):
    """
    Process a single email immediately when it arrives
    Used for real-time notifications
    """
    try:
        # Get the message with attachments
        result = await graph_service.get_message_with_attachments(message_id)
        if result['status'] != 'success':
            logger.warning(f"Failed to fetch message {message_id}")
            return None
        
        msg = result.get('message', {})
        attachments = result.get('attachments', [])
        
        sender = msg.get('from', {}).get('emailAddress', {})
        sender_email = sender.get('address', '')
        sender_name = sender.get('name', sender_email.split('@')[0])
        
        subject = msg.get('subject', '')
        body = msg.get('body', {}).get('content', '')
        
        # Use actual email received date from Graph API
        received_dt = msg.get('receivedDateTime')
        if received_dt:
            try:
                received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
            except:
                received_date = datetime.now()
        else:
            received_date = datetime.now()
        
        email_data = {
            'subject': subject,
            'sender_email': sender_email,
            'sender_name': sender_name,
            'body': body,
            'attachments': attachments,
            'received_date': received_date
        }
        
        # Extract candidate
        candidate = await scraper_service.extract_candidate_from_email(email_data)
        if not candidate or not candidate.get('email'):
            return None
        
        # Check if exists
        existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])
        
        # AI processing for new candidates
        if candidate.get('resume_text'):
            try:
                ai_analysis = await asyncio.wait_for(
                    ai_service.analyze_candidate(candidate['resume_text']),
                    timeout=AI_ANALYSIS_TIMEOUT
                )
                if ai_analysis:
                    candidate.update({
                        'job_category': ai_analysis.get('job_category', 'General'),
                        'matchScore': ai_analysis.get('quality_score', 50),
                        'summary': ai_analysis.get('summary', candidate.get('summary', '')),
                        'skills': ai_analysis.get('skills', candidate.get('skills', [])),
                        'experience': ai_analysis.get('experience', candidate.get('experience', 0))
                    })
            except Exception as ai_err:
                logger.warning(f"AI analysis failed: {str(ai_err)[:50]}")
                candidate['matchScore'] = 50
        
        # Save to database
        if existing:
            await asyncio.to_thread(db_service.update_candidate, candidate)
            logger.info(f"📝 Updated candidate: {candidate.get('name', 'Unknown')}")
        else:
            await asyncio.to_thread(db_service.insert_candidate, candidate)
            logger.info(f"✨ NEW candidate from real-time sync: {candidate.get('name', 'Unknown')} - {candidate.get('email', '')}")
        
        return candidate
        
    except Exception as e:
        logger.error(f"Error processing single email {message_id}: {str(e)}")
        return None


@app.post("/api/email/webhook")
async def email_webhook(request: Request):
    """
    Microsoft Graph webhook endpoint for real-time email notifications
    When a new email arrives, Microsoft calls this endpoint
    """
    try:
        # Handle validation token (required when creating subscription)
        query_params = dict(request.query_params)
        if 'validationToken' in query_params:
            # Return the validation token as plain text
            return Response(content=query_params['validationToken'], media_type="text/plain")
        
        # Process the notification
        body = await request.json()
        notifications = body.get('value', [])
        
        logger.info(f"📬 Received {len(notifications)} webhook notification(s)")
        
        for notification in notifications:
            resource = notification.get('resource', '')
            change_type = notification.get('changeType', '')
            
            if change_type == 'created' and 'messages' in resource:
                # Extract message ID from resource path
                # Format: Users/{user-id}/Messages/{message-id} or me/messages/{message-id}
                parts = resource.split('/')
                if 'messages' in resource.lower():
                    message_id = parts[-1] if parts else None
                    
                    if message_id:
                        # Process the new email in background
                        email_address = os.getenv('EMAIL_ADDRESS')
                        token_storage = get_token_storage()
                        token_data = token_storage.get_token(email_address)
                        
                        if token_data and token_data.get('access_token'):
                            client_id = os.getenv('MICROSOFT_CLIENT_ID')
                            client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
                            tenant_id = os.getenv('MICROSOFT_TENANT_ID')
                            
                            graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
                            graph_service.access_token = token_data['access_token']
                            graph_service.auth_type = token_data.get('auth_type', 'delegated')
                            
                            asyncio.create_task(process_single_email(message_id, graph_service))
        
        return {"status": "processed"}
        
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return {"status": "error", "message": str(e)}


@app.post("/api/email/subscribe-webhook")
async def subscribe_to_email_webhook():
    """
    Create a Microsoft Graph subscription for real-time email notifications
    This needs to be called once to set up real-time sync
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS')
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        
        if not token_data:
            raise HTTPException(401, "No OAuth2 token. Please authenticate first.")
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
        graph_service.access_token = token_data['access_token']
        graph_service.auth_type = token_data.get('auth_type', 'delegated')
        
        # Get the webhook URL (this should be your public URL)
        webhook_url = os.getenv('WEBHOOK_URL', 'http://localhost:8000/api/email/webhook')
        
        # Create subscription
        import httpx
        async with httpx.AsyncClient() as client:
            # Subscription expires in 3 days max for mail
            expiration = (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z"
            
            subscription_data = {
                "changeType": "created",
                "notificationUrl": webhook_url,
                "resource": "me/mailFolders/inbox/messages",
                "expirationDateTime": expiration,
                "clientState": "recruitment-tool-secret"
            }
            
            response = await client.post(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                json=subscription_data
            )
            
            if response.status_code == 201:
                result = response.json()
                logger.info(f"✅ Webhook subscription created: {result.get('id')}")
                return {
                    "status": "success",
                    "subscription_id": result.get('id'),
                    "expires": result.get('expirationDateTime'),
                    "message": "Real-time email notifications enabled!"
                }
            else:
                error_detail = response.text
                logger.warning(f"Subscription failed: {error_detail}")
                return {
                    "status": "error", 
                    "message": f"Failed to create subscription: {response.status_code}",
                    "detail": error_detail
                }
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Subscription error: {str(e)}")
        raise HTTPException(500, f"Error creating webhook subscription: {str(e)}")


@app.post("/api/email/outlook/connect")
async def connect_outlook(
    client_id: str,
    client_secret: str,
    tenant_id: str,
    authorization_code: str,
    redirect_uri: str
):
    """
    Connect Microsoft Outlook/Office 365 using Graph API
    Enterprise OAuth2 authentication
    """
    try:
        graph_service = MicrosoftGraphService(
            client_id=client_id,
            client_secret=client_secret,
            tenant_id=tenant_id
        )
        
        result = await graph_service.authenticate(
            authorization_code=authorization_code,
            redirect_uri=redirect_uri
        )
        
        return result
    except Exception as e:
        raise HTTPException(500, f"Error connecting Outlook: {str(e)}")

@app.get("/api/email/outlook/auth-url")
async def get_outlook_auth_url(
    client_id: str,
    tenant_id: str,
    redirect_uri: str
):
    """
    Get Microsoft OAuth2 authorization URL
    User will be redirected here to grant permissions
    """
    try:
        graph_service = MicrosoftGraphService(
            client_id=client_id,
            client_secret='',  # Not needed for URL generation
            tenant_id=tenant_id
        )
        
        auth_url = graph_service.get_authorization_url(redirect_uri=redirect_uri)
        
        return {
            'status': 'success',
            'authorization_url': auth_url
        }
    except Exception as e:
        raise HTTPException(500, f"Error generating auth URL: {str(e)}")

@app.post("/api/email/outlook/sync")
async def sync_outlook_applications(
    access_token: str,
    folder: str = 'inbox',
    limit: int = 50
):
    """
    Sync applications from Outlook using Graph API
    """
    try:
        # Note: In production, store graph_service instance per user
        # This is a simplified example
        
        return {
            'status': 'success',
            'message': 'Use /api/email/sync with Outlook credentials'
        }
    except Exception as e:
        raise HTTPException(500, f"Error syncing Outlook: {str(e)}")

@app.post("/api/email/setup-auto-sync")
async def setup_auto_sync(
    provider: str,
    email: str,
    password: Optional[str] = None,
    access_token: Optional[str] = None,
    sync_interval_minutes: int = 15
):
    """
    Setup automatic email synchronization
    System will check for new applications every N minutes
    """
    try:
        email_config = {
            'provider': provider,
            'email': email,
            'password': password,
            'access_token': access_token
        }
        
        result = await email_parser.setup_auto_sync(
            email_config=email_config,
            sync_interval_minutes=sync_interval_minutes
        )
        
        return result
    except Exception as e:
        raise HTTPException(500, f"Error setting up auto-sync: {str(e)}")

@app.get("/api/email/supported-providers")
async def get_supported_email_providers():
    """
    Get list of supported email providers
    """
    return {
        'providers': [
            {
                'id': 'gmail',
                'name': 'Gmail',
                'requires_app_password': True,
                'supports_oauth': True,
                'instructions': 'Enable 2FA and create app password at https://myaccount.google.com/apppasswords'
            },
            {
                'id': 'outlook',
                'name': 'Outlook / Office 365',
                'requires_app_password': False,
                'supports_oauth': True,
                'enterprise_ready': True,
                'instructions': 'Use OAuth2 for enterprise integration'
            },
            {
                'id': 'yahoo',
                'name': 'Yahoo Mail',
                'requires_app_password': True,
                'supports_oauth': False,
                'instructions': 'Create app password in Yahoo account security settings'
            },
            {
                'id': 'icloud',
                'name': 'iCloud Mail',
                'requires_app_password': True,
                'supports_oauth': False,
                'instructions': 'Generate app-specific password at appleid.apple.com'
            },
            {
                'id': 'custom',
                'name': 'Custom IMAP Server',
                'requires_app_password': False,
                'supports_oauth': False,
                'instructions': 'Enter your custom IMAP server details'
            }
        ]
    }


# ============================================
# OAuth2 Automation Endpoints
# ============================================

@app.get("/api/oauth/status")
async def get_oauth_automation_status():
    """
    Get comprehensive OAuth2 automation status
    Returns auth status, sync status, and statistics
    """
    try:
        if oauth_automation_service:
            return oauth_automation_service.get_status_summary()
        else:
            return {
                'is_configured': False,
                'auth_status': 'not_initialized',
                'sync_status': 'idle',
                'message': 'OAuth automation service not initialized'
            }
    except Exception as e:
        logger.error(f"Error getting OAuth status: {e}")
        return {
            'is_configured': False,
            'auth_status': 'error',
            'error': str(e)
        }


@app.post("/api/oauth/refresh")
async def force_token_refresh():
    """
    Force refresh OAuth2 token
    Use when automatic refresh fails
    """
    try:
        if not oauth_automation_service:
            raise HTTPException(503, "OAuth automation service not initialized")
        
        if not oauth_automation_service.is_configured:
            raise HTTPException(400, "OAuth2 not configured. Set MICROSOFT_CLIENT_ID, CLIENT_SECRET, TENANT_ID, and EMAIL_ADDRESS in .env")
        
        result = await oauth_automation_service.refresh_token()
        
        if result['status'] == 'success':
            return {
                'status': 'success',
                'message': 'Token refreshed successfully',
                'auth_status': oauth_automation_service.auth_status.value
            }
        else:
            return {
                'status': 'failed',
                'message': result.get('message', 'Token refresh failed'),
                'needs_manual_auth': result.get('needs_manual_auth', False),
                'auth_url': result.get('auth_url')
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        raise HTTPException(500, f"Token refresh error: {str(e)}")


@app.post("/api/oauth/sync")
async def trigger_oauth_sync():
    """
    Trigger immediate email sync via OAuth automation
    Uses automatic token management
    """
    try:
        if not oauth_automation_service:
            raise HTTPException(503, "OAuth automation service not initialized")
        
        if not oauth_automation_service.is_configured:
            raise HTTPException(400, "OAuth2 not configured")
        
        # Define sync callback that uses the email processing logic
        async def sync_callback(token_data):
            email_address = oauth_automation_service.primary_email
            asyncio.create_task(trigger_reset_and_reparse(email_address))
            return {
                'status': 'success',
                'message': 'Sync triggered in background',
                'email': email_address
            }
        
        result = await oauth_automation_service.trigger_manual_sync(sync_callback)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering OAuth sync: {e}")
        raise HTTPException(500, f"Sync error: {str(e)}")


@app.get("/api/oauth/stats")
async def get_oauth_stats():
    """
    Get OAuth automation statistics
    """
    try:
        if oauth_automation_service:
            return {
                'status': 'success',
                'stats': oauth_automation_service.stats
            }
        else:
            return {
                'status': 'error',
                'message': 'OAuth automation not initialized'
            }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


@app.post("/api/oauth/start-automation")
async def start_oauth_automation():
    """
    Start OAuth automation service (if stopped)
    """
    try:
        if oauth_automation_service:
            await oauth_automation_service.start()
            return {
                'status': 'success',
                'message': 'OAuth automation started'
            }
        else:
            raise HTTPException(503, "OAuth automation service not initialized")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/oauth/stop-automation")
async def stop_oauth_automation():
    """
    Stop OAuth automation service
    Manual sync will still be available
    """
    try:
        if oauth_automation_service:
            await oauth_automation_service.stop()
            return {
                'status': 'success',
                'message': 'OAuth automation stopped. Manual sync still available.'
            }
        else:
            raise HTTPException(503, "OAuth automation service not initialized")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/email/manual-sync")
async def manual_email_sync():
    """
    Emergency manual sync endpoint
    Bypasses OAuth automation for direct sync
    This is the fallback when automatic sync fails
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS')
        
        if not email_address:
            raise HTTPException(400, "No email configured in .env")
        
        # Check for token
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        
        if not token_data:
            # Try to generate auth URL
            client_id = os.getenv('MICROSOFT_CLIENT_ID')
            tenant_id = os.getenv('MICROSOFT_TENANT_ID')
            
            if client_id and tenant_id:
                auth_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?client_id={client_id}&response_type=code&redirect_uri=http://localhost:5173/email&scope=https://graph.microsoft.com/Mail.Read%20https://graph.microsoft.com/User.Read%20offline_access"
                return {
                    'status': 'needs_auth',
                    'message': 'No OAuth token found. Please authenticate manually.',
                    'auth_url': auth_url
                }
            raise HTTPException(401, "No OAuth2 token found and credentials not configured")
        
        # Check if expired and try to refresh
        if token_data.get('is_expired'):
            refresh_token = token_data.get('refresh_token')
            if refresh_token:
                client_id = os.getenv('MICROSOFT_CLIENT_ID')
                client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
                tenant_id = os.getenv('MICROSOFT_TENANT_ID')
                
                graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
                refresh_result = await graph_service.refresh_access_token(refresh_token)
                
                if refresh_result['status'] == 'success':
                    token_storage.save_token(
                        email=email_address,
                        access_token=refresh_result['access_token'],
                        refresh_token=refresh_result.get('refresh_token', refresh_token),
                        expires_in=refresh_result['expires_in'],
                        auth_type='delegated'
                    )
                    logger.info(f"✅ Manual sync: Token refreshed for {email_address}")
                else:
                    raise HTTPException(401, "Token expired and refresh failed. Please re-authenticate.")
            else:
                raise HTTPException(401, "Token expired with no refresh token. Please re-authenticate.")
        
        # Trigger sync in background
        asyncio.create_task(trigger_reset_and_reparse(email_address))
        
        return {
            'status': 'syncing',
            'message': 'Manual email sync started in background',
            'email': email_address,
            'note': 'This is the emergency fallback. Automatic sync should handle this normally.'
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Manual sync error: {e}")
        raise HTTPException(500, f"Manual sync error: {str(e)}")


# Authentication endpoints
class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    username: Optional[str] = None

class UserProfile(BaseModel):
    firstName: str
    lastName: str
    email: str
    company: Optional[str] = None
    phone: Optional[str] = None

class PasswordUpdate(BaseModel):
    currentPassword: str
    newPassword: str

# Initialize auth service
auth_service = get_auth_service()

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """
    Authenticate user and return JWT token
    
    - Validates email and password
    - Returns user data and JWT access token
    - Token expires in 7 days
    """
    try:
        if not request.email or not request.password:
            raise HTTPException(400, "Email and password are required")
        
        result = auth_service.login(request.email, request.password)
        return result
        
    except ValueError as e:
        raise HTTPException(401, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(500, f"Login error: {str(e)}")

@app.post("/api/auth/register")
async def register(request: RegisterRequest):
    """
    Register new user account
    
    - Creates new user with hashed password
    - Returns user data and JWT access token
    - Email must be unique
    """
    try:
        if not request.email or not request.password or not request.name:
            raise HTTPException(400, "Name, email and password are required")
        
        result = auth_service.register(
            email=request.email, 
            password=request.password, 
            name=request.name,
            username=request.username
        )
        logger.info(f"✅ New user registered: {request.email} ({request.name})")
        return result
        
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(500, f"Registration error: {str(e)}")

@app.get("/api/auth/me")
async def get_current_user(authorization: Optional[str] = Header(None)):
    """
    Get current user from JWT token
    
    - Validates Authorization header
    - Returns user data if token is valid
    """
    try:
        if not authorization:
            raise HTTPException(401, "Authorization header required")
        
        # Extract token from "Bearer <token>"
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(401, "Invalid authorization format. Use 'Bearer <token>'")
        
        token = parts[1]
        user = auth_service.verify_token(token)
        
        if not user:
            raise HTTPException(401, "Invalid or expired token")
        
        return {"user": user}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth verification error: {e}")
        raise HTTPException(500, f"Auth error: {str(e)}")

# User profile endpoints
@app.put("/api/users/profile")
async def update_profile(profile: UserProfile, authorization: Optional[str] = Header(None)):
    """
    Update user profile information
    """
    try:
        if not authorization:
            raise HTTPException(401, "Authorization required")
        
        # Extract and verify token
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(401, "Invalid authorization format")
        
        user = auth_service.verify_token(parts[1])
        if not user:
            raise HTTPException(401, "Invalid or expired token")
        
        # Update profile
        updated_user = auth_service.update_profile(user['id'], {
            'name': f"{profile.firstName} {profile.lastName}",
            'first_name': profile.firstName,
            'company': profile.company,
            'phone': profile.phone
        })
        
        return {
            'status': 'success',
            'message': 'Profile updated successfully',
            'user': updated_user
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Profile update error: {e}")
        raise HTTPException(500, f"Error updating profile: {str(e)}")

@app.put("/api/users/password")
async def update_password(password_update: PasswordUpdate, authorization: Optional[str] = Header(None)):
    """
    Update user password
    """
    try:
        if not authorization:
            raise HTTPException(401, "Authorization required")
        
        # Extract and verify token
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(401, "Invalid authorization format")
        
        user = auth_service.verify_token(parts[1])
        if not user:
            raise HTTPException(401, "Invalid or expired token")
        
        # Change password
        auth_service.change_password(
            user['id'],
            password_update.currentPassword,
            password_update.newPassword
        )
        
        return {
            'status': 'success',
            'message': 'Password updated successfully'
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Password update error: {e}")
        raise HTTPException(500, f"Error updating password: {str(e)}")

# Candidate management endpoints - status update only, other routes defined earlier
class CandidateStatusUpdate(BaseModel):
    status: str  # 'Strong', 'Partial', 'Reject'

@app.put("/api/candidates/{candidate_id}/status")
async def update_candidate_status(candidate_id: str, status_update: CandidateStatusUpdate):
    """
    Update candidate status (shortlist, reject, etc.)
    """
    try:
        # Update in database
        await asyncio.to_thread(db_service.update_candidate, {
            'id': candidate_id,
            'status': status_update.status
        })
        return {
            'status': 'success',
            'message': f'Candidate {candidate_id} status updated to {status_update.status}',
            'candidate_id': candidate_id,
            'new_status': status_update.status
        }
    except Exception as e:
        raise HTTPException(500, f"Error updating candidate status: {str(e)}")

# NOTE: Resume download route is defined earlier (line ~953) with proper database query

# AI Chat endpoints
class ChatMessage(BaseModel):
    message: str
    context: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    timestamp: str

@app.post("/api/ai/chat", response_model=ChatResponse)
async def ai_chat(chat_message: ChatMessage):
    """
    Chat with AI assistant - INTELLIGENT DATABASE SEARCH
    Can search entire candidate database using natural language
    Examples: "Find Python developers with 5+ years"
    PRIMARY: Local AI (FREE, 8s timeout)
    FALLBACK: OpenAI (auto-triggers on timeout or error)
    """
    try:
        from datetime import datetime
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        
        # Try Local AI first with TIMEOUT (8 seconds)
        try:
            executor = ThreadPoolExecutor(max_workers=1)
            loop = asyncio.get_event_loop()
            
            # Run Local AI with timeout
            response_text = await asyncio.wait_for(
                loop.run_in_executor(
                    executor,
                    ai_service.chat_with_ai,
                    chat_message.message,
                    chat_message.context,
                    db_service
                ),
                timeout=AI_TIMEOUT  # Use configured timeout
            )
            source = "local_ai"
            logger.info("✅ Local AI responded (fast & free)")
            
        except asyncio.TimeoutError:
            # TIMEOUT - use OpenAI for fast response
            if fallback_service:
                logger.warning(f"⏱️ Local AI timeout (>{AI_TIMEOUT}s), using OpenAI for instant response")
                response_text = fallback_service.chat_with_ai(
                    chat_message.message,
                    chat_message.context
                )
                source = "openai_timeout_fallback"
            else:
                raise HTTPException(500, "Local AI timeout and no OpenAI fallback configured")
                
        except Exception as local_error:
            # ERROR - fallback to OpenAI
            if fallback_service:
                logger.warning(f"⚠️ Local AI error, using OpenAI fallback: {local_error}")
                response_text = fallback_service.chat_with_ai(
                    chat_message.message,
                    chat_message.context
                )
                source = "openai_error_fallback"
            else:
                raise HTTPException(500, f"Local AI error and no fallback: {str(local_error)}")
        
        return {
            "response": response_text,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(500, f"Error processing chat: {str(e)}")

class AnalyzeMatchRequest(BaseModel):
    candidate: dict
    job_description: dict

# Global thread pool for AI operations (reusable, efficient)
from concurrent.futures import ThreadPoolExecutor
_ai_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai_worker")

@app.post("/api/ai/analyze-match")
async def analyze_match(request: AnalyzeMatchRequest):
    """
    Use AI to analyze candidate-job match - OPTIMIZED
    Runs AI in separate thread pool to avoid blocking
    """
    try:
        candidate_id = request.candidate.get('id', 'temp')
        job_id = request.job_description.get('id', 'general')
        
        # Check cache first (non-blocking)
        cached = await asyncio.to_thread(db_service.get_cached_ai_score, candidate_id, job_id)
        if cached:
            cached['from_cache'] = True
            return cached
        
        # Run AI analysis in thread pool (non-blocking)
        loop = asyncio.get_running_loop()
        
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    _ai_executor,
                    ai_service.analyze_candidate_match,
                    request.candidate,
                    request.job_description
                ),
                timeout=AI_ANALYSIS_TIMEOUT
            )
            result['source'] = 'local_ai'
            logger.info("✅ Local AI analysis completed")
            
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Local AI timeout (>{AI_ANALYSIS_TIMEOUT}s)")
            # Quick fallback analysis
            result = _quick_fallback_analysis(request.candidate, request.job_description)
            result['source'] = 'fallback_timeout'
                
        except Exception as local_error:
            logger.warning(f"⚠️ Local AI error: {local_error}")
            # Quick fallback analysis
            result = _quick_fallback_analysis(request.candidate, request.job_description)
            result['source'] = 'fallback_error'
        
        # Cache result in background (non-blocking)
        result['from_cache'] = False
        asyncio.create_task(
            asyncio.to_thread(db_service.cache_ai_score, candidate_id, job_id, result)
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Analyze match error: {e}")
        # Return fallback instead of error
        return _quick_fallback_analysis(request.candidate, request.job_description)


def _quick_fallback_analysis(candidate: dict, job_description: dict) -> dict:
    """Quick rule-based fallback when AI is unavailable"""
    candidate_skills = set(s.lower() for s in candidate.get('skills', []))
    required_skills = set(s.lower() for s in job_description.get('required_skills', []))
    
    # Simple skill match
    matched = candidate_skills & required_skills
    skill_score = (len(matched) / max(len(required_skills), 1)) * 100 if required_skills else 50
    
    # Experience
    exp = candidate.get('experience', 0)
    if isinstance(exp, str):
        exp = int(''.join(filter(str.isdigit, str(exp))) or '0')
    exp_score = min(100, exp * 15)  # 15 points per year, max 100
    
    # Final score
    score = int(skill_score * 0.7 + exp_score * 0.3)
    score = max(20, min(95, score))
    
    missing = list(required_skills - candidate_skills)[:3]
    
    return {
        "score": score,
        "strengths": [
            f"Matched {len(matched)} of {len(required_skills)} required skills" if required_skills else "Skills review needed",
            f"{exp} years of experience" if exp else "Experience to be verified"
        ],
        "gaps": [f"Missing: {', '.join(missing)}"] if missing else ["No major gaps identified"],
        "recommendation": "Recommended" if score >= 60 else "Consider with reservations",
        "matched_skills": len(matched),
        "total_required": len(required_skills),
        "semantic_used": False
    }

class InterviewQuestionsRequest(BaseModel):
    candidate: dict
    job_description: dict
    num_questions: int = 5

@app.post("/api/ai/interview-questions")
async def generate_interview_questions(request: InterviewQuestionsRequest):
    """
    Generate AI-powered interview questions
    PRIMARY: Local AI (FREE)
    FALLBACK: OpenAI (emergency only)
    """
    try:
        # Try Local AI first
        try:
            questions = ai_service.generate_interview_questions(
                request.candidate,
                request.job_description,
                request.num_questions
            )
        except Exception as local_error:
            # Emergency fallback to OpenAI
            if fallback_service:
                logger.warning(f"Local AI questions failed, using OpenAI fallback")
                questions = fallback_service.generate_interview_questions(
                    request.candidate,
                    request.job_description,
                    request.num_questions
                )
            else:
                raise local_error
        return {"questions": questions}
    except Exception as e:
        raise HTTPException(500, f"Error generating questions: {str(e)}")

class SummarizeResumeRequest(BaseModel):
    resume_text: str

@app.post("/api/ai/summarize-resume")
async def summarize_resume(request: SummarizeResumeRequest):
    """
    Generate AI summary of resume
    PRIMARY: Local AI (FREE)
    FALLBACK: OpenAI (emergency only)
    """
    try:
        # Try Local AI first
        try:
            summary = ai_service.summarize_resume(request.resume_text)
        except Exception as local_error:
            # Emergency fallback to OpenAI
            if fallback_service:
                logger.warning(f"Local AI summarize failed, using OpenAI fallback")
                summary = fallback_service.summarize_resume(request.resume_text)
            else:
                raise local_error
        return {"summary": summary}
    except Exception as e:
        raise HTTPException(500, f"Error summarizing resume: {str(e)}")

@app.post("/api/ai/batch-analyze")
async def batch_analyze_new_candidates(job_id: str = "general", batch_size: int = 50):
    """
    Batch analyze ONLY NEW candidates with CONCURRENT processing
    PRIMARY: Local AI (FREE, handles 100+ concurrent requests)
    FALLBACK: OpenAI (emergency only per candidate)
    Optimized for high-load scenarios with 10,000+ candidates
    """
    try:
        # Get only candidates without AI scores
        new_candidates = await asyncio.to_thread(db_service.get_candidates_needing_ai_analysis, job_id)
        
        if not new_candidates:
            return {
                "message": "All candidates already analyzed",
                "new_count": 0,
                "analyzed_count": 0
            }
        
        # Process in batches with concurrent execution
        analyzed_count = 0
        failed_count = 0
        fallback_used = 0
        
        # Process batch_size candidates at a time (default 50 for high throughput)
        import asyncio
        batch = new_candidates[:batch_size]
        
        async def analyze_one(candidate):
            nonlocal analyzed_count, failed_count, fallback_used
            try:
                # Try Local AI first (fast, concurrent-safe)
                result = ai_service.analyze_candidate_match(
                    candidate,
                    {"id": job_id, "title": "General Position", "required_skills": []}
                )
                await asyncio.to_thread(db_service.cache_ai_score, candidate['id'], job_id, result)
                analyzed_count += 1
            except Exception as local_error:
                # Emergency fallback to OpenAI for this candidate
                if fallback_service:
                    try:
                        logger.warning(f"Local AI failed for {candidate['id']}, using OpenAI fallback")
                        result = fallback_service.analyze_candidate_match(
                            candidate,
                            {"id": job_id, "title": "General Position", "required_skills": []}
                        )
                        result['source'] = 'openai_fallback'
                        await asyncio.to_thread(db_service.cache_ai_score, candidate['id'], job_id, result)
                        analyzed_count += 1
                        fallback_used += 1
                    except Exception as fallback_error:
                        logger.error(f"Both AI services failed for {candidate['id']}")
                        failed_count += 1
                else:
                    logger.error(f"Local AI failed for {candidate['id']}, no fallback available")
                    failed_count += 1
        
        # Execute all analyses concurrently (Local AI can handle 100+ parallel)
        await asyncio.gather(*[analyze_one(c) for c in batch], return_exceptions=True)
        
        return {
            "message": "Batch analysis complete",
            "total_candidates": len(new_candidates),
            "processed_batch": len(batch),
            "analyzed_count": analyzed_count,
            "failed_count": failed_count,
            "fallback_used": fallback_used,
            "ai_engine": "local_primary_openai_fallback",
            "concurrent_processing": True
        }
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")

@app.get("/api/ai/status")
async def ai_status():
    """
    Check AI service status and configuration
    """
    # Get LLM service status
    llm_status = {}
    try:
        from services.llm_service import get_llm_service
        llm_svc = await get_llm_service()
        llm_status = llm_svc.get_status()
    except Exception:
        llm_status = {'available': False}
    
    # Get local AI cache stats
    ai_cache = {}
    try:
        ai_cache = ai_service.get_cache_stats()
    except Exception:
        pass
    
    return {
        "available": True,
        "primary_engine": "ollama_llm" if llm_status.get('available') else "local_ai",
        "fallback_engine": "openai" if fallback_service else "local_ai",
        "llm": {
            "available": llm_status.get('available', False),
            "primary_model": llm_status.get('primary_model', 'Not loaded'),
            "fast_model": llm_status.get('fast_model', 'Not loaded'),
            "reasoning_model": llm_status.get('reasoning_model', 'Not loaded'),
            "available_models": llm_status.get('available_models', []),
            "requests_processed": llm_status.get('requests_processed', 0),
            "avg_response_time": llm_status.get('average_response_time', 0),
            "ollama_url": llm_status.get('ollama_url', 'http://localhost:11434'),
        },
        "sentence_model": ai_cache.get('model_loaded', False),
        "ner_model": ai_cache.get('ner_loaded', False),
        "device": ai_cache.get('device', 'cpu'),
        "cache": {
            "embedding": ai_cache.get('embedding_cache_size', 0),
            "ner": ai_cache.get('ner_cache_size', 0),
            "analysis": ai_cache.get('analysis_cache_size', 0),
            "llm": llm_status.get('cache_size', 0),
        },
        "model": "Multi-Tier AI: LLM (Ollama) → Sentence-Transformers → SpaCy NER → Regex",
        "fallback_model": openai_service.model if fallback_service else None,
        "message": "🤖 AI Stack: Local LLM + Embeddings + NER (FREE) with OpenAI emergency fallback",
        "caching_enabled": True,
        "concurrent_processing": True,
        "max_concurrent": "100+ requests",
        "cost": "$0 (all local, OpenAI fallback charges only if all local AI fails)",
        "fallback_available": fallback_service is not None,
        "setup_instructions": {
            "ollama": "Install from https://ollama.com/download then run: ollama pull qwen2.5:7b",
            "models_recommended": ["qwen2.5:7b (extraction)", "phi3.5 (fast)", "llama3.1:8b (reasoning)"]
        }
    }

@app.get("/api/llm/status")
async def llm_status():
    """Get detailed LLM service status"""
    try:
        from services.llm_service import get_llm_service
        llm_svc = await get_llm_service()
        return llm_svc.get_status()
    except Exception as e:
        return {
            "available": False,
            "error": str(e),
            "setup": "Install Ollama from https://ollama.com/download, then: ollama pull qwen2.5:7b"
        }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
