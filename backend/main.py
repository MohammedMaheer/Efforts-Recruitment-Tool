"""AI Recruitment Platform v16-stable — deployed 2026-02-17"""
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Request, Header, Body, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, RedirectResponse
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import uvicorn
import asyncio
import os
import json
import re
import shutil
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
from services.gemini_service import get_gemini_service
from services.email_scraper import get_scraper_service
from services.database_service import get_db_service
from services.oauth_automation_service import get_oauth_automation, OAuthAutomationService
from services.auth_service import get_auth_service
from models.candidate import Candidate, JobDescription, MatchResult
from core.config import get_settings
from core.dependencies import require_auth, optional_auth

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

# Get centralized settings
_settings = get_settings()

# Configuration - use centralized config with env var overrides
DEBUG = _settings.debug if not _settings.is_production else False
AI_TIMEOUT = float(os.getenv('AI_TIMEOUT', os.getenv('AI_TIMEOUT_SECONDS', str(_settings.ai_timeout))))
AI_ANALYSIS_TIMEOUT = float(os.getenv('AI_ANALYSIS_TIMEOUT', str(_settings.ai_analysis_timeout)))  # Use centralized config
MAX_CONCURRENT_REQUESTS = _settings.max_concurrent_requests
USE_OPENAI_FALLBACK = os.getenv('USE_OPENAI_FALLBACK', 'false').lower() == 'true'  # Disabled by default

# Configure logging with structured format
logging.basicConfig(
    level=logging.INFO if not _settings.is_production else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Performance: Response cache (5 minutes TTL)
response_cache = TTLCache(maxsize=1000, ttl=300)
cache_lock = asyncio.Lock()

# Semaphore for database access to prevent SQLite lock contention
db_semaphore = asyncio.Semaphore(10)

# Background email sync task
background_sync_task = None
oauth_automation_service: OAuthAutomationService = None

# Track last sync timestamp for incremental fetching (loaded from DB on startup)
_last_email_sync_time: str = None

async def auto_sync_emails():
    """
    FULLY AUTOMATED email sync with OAuth2 Client Credentials Flow
    NO user intervention required - authenticates automatically using app credentials
    
    Uses incremental sync: first run fetches all emails, subsequent runs only fetch
    emails received AFTER the last successful sync (using receivedDateTime filter).
    
    IDEMPOTENT: Tracks processed email message IDs in `email_processing_log` table.
    On restart, already-processed emails are automatically skipped.
    Last sync timestamp is persisted in `sync_metadata` table so incremental
    sync survives server restarts.
    
    Authentication Priority:
    1. Client Credentials (Application Permissions) - FULLY AUTOMATIC
    2. Refresh Token (if available from previous delegated auth)
    3. IMAP fallback (if OAuth2 not configured)
    """
    global _last_email_sync_time
    # Wait 5 seconds before first sync to allow server to fully start
    await asyncio.sleep(5)
    
    # Load last sync time from DB (survives server restarts)
    try:
        persisted_time = await asyncio.to_thread(db_service.get_sync_metadata, 'last_email_sync_time')
        if persisted_time:
            _last_email_sync_time = persisted_time
            logger.info(f"📧 Restored last sync time from DB: {_last_email_sync_time}")
    except Exception as e:
        logger.warning(f"Could not load sync metadata: {e}")
    
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
                    
                    # Authentication priority:
                    # 1. Refresh token (delegated auth — user logged in via Settings → Connect Microsoft)
                    # 2. Client Credentials (application — requires Azure AD mailbox + admin consent)
                    needs_new_token = (
                        not token_data or 
                        token_data.get('is_expired', True) or
                        not token_data.get('access_token')
                    )
                    
                    if needs_new_token:
                        logger.info(f"🔐 Authenticating for {primary_email}...")
                        
                        # PRIORITY 1: Try refresh token (delegated auth — works with /me/ endpoint)
                        refresh_token = token_data.get('refresh_token') if token_data else None
                        auth_success = False
                        
                        if refresh_token:
                            logger.info("🔄 Attempting delegated token refresh (refresh_token)...")
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
                                logger.info(f"✅ Delegated token refreshed for {primary_email} — using /me/ endpoint")
                            else:
                                logger.warning(f"⚠️ Refresh token failed: {refresh_result.get('error', 'unknown')}")
                        
                        # PRIORITY 2: No refresh token available — user must authenticate via frontend
                        # NOTE: Client Credentials Flow only works with Azure AD mailboxes.
                        # Our email (hr@effortz.com) uses delegated auth with refresh tokens.
                        # User must authenticate ONCE via frontend, then auto-refresh works forever.
                        if not auth_success and not refresh_token:
                            logger.info("=" * 60)
                            logger.info("📋 EMAIL SYNC REQUIRES ONE-TIME MICROSOFT OAUTH LOGIN")
                            logger.info("=" * 60)
                            logger.info(f"   1. Open: {os.getenv('CORS_ORIGINS', 'https://efforts-recruitment.web.app')}")
                            logger.info("   2. Go to: Settings → Email Integration")
                            logger.info("   3. Click: Connect Microsoft Account")
                            logger.info("   4. Sign in with your Microsoft/Outlook account")
                            logger.info("   → After this ONE-TIME login, auto-refresh works FOREVER")
                            logger.info("=" * 60)
                        elif not auth_success and refresh_token:
                            # Refresh failed but we have a refresh_token — DON'T try client_credentials
                            # because it would overwrite the delegated token + refresh_token
                            logger.warning(f"⚠️ Refresh token failed for {primary_email}. User needs to re-authenticate via Settings.")
                            logger.info("📋 Re-authenticate: Settings → Email Integration → Connect Microsoft Account")
                    
                    # Use the token if we have a valid one
                    if token_data and token_data.get('access_token') and not token_data.get('is_expired', True):
                        logger.info(f"🔐 Using OAuth2 ({token_data.get('auth_type', 'unknown')}) for {primary_email}...")
                        
                        graph_service.access_token = token_data['access_token']
                        graph_service.auth_type = token_data.get('auth_type', 'delegated')
                        graph_service.token_expiry = token_data.get('expires_at_dt', datetime.now() + timedelta(hours=1))
                        
                        # ===== FETCH ALL EMAILS — rely on is_email_processed for dedup =====
                        # We always fetch ALL inbox emails. The email_processing_log table
                        # tracks which message IDs have been processed, so already-handled
                        # emails are skipped instantly (just a DB lookup). This ensures:
                        #   - Old unprocessed emails are always retried
                        #   - No emails are missed due to date filters
                        #   - Restarts don't lose track of what was processed
                        
                        processed_count_before = await asyncio.to_thread(
                            lambda: db_service.get_processed_email_count()
                        )
                        
                        logger.info(f"📧 Fetching ALL inbox emails (already processed: {processed_count_before})...")
                        
                        # Record sync start time (ISO 8601 format for Graph API)
                        sync_start_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
                        
                        # Fetch ALL emails — no date filter
                        result = await graph_service.get_messages(
                            folder='inbox', 
                            top=100000,
                            fetch_all=True,
                            filter_query=None
                        )
                    
                        if result['status'] == 'success':
                            messages = result['messages']
                            logger.info(f"📧 Found {len(messages)} emails from OAuth2")
                            
                            # Process messages in parallel batches
                            new_count = 0
                            
                            async def process_graph_message(msg):
                                nonlocal new_count
                                try:
                                    # ------ DEDUP: skip emails already processed ------
                                    msg_id = msg.get('id', '') or msg.get('internetMessageId', '')
                                    if msg_id and await asyncio.to_thread(db_service.is_email_processed, msg_id):
                                        return  # already handled — skip entirely

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
                                        except Exception:
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
                                    
                                    # Smart filter: block Indeed relay / junk emails
                                    if db_service.is_blocked_email(candidate['email']):
                                        logger.debug(f"🚫 Blocked Indeed relay candidate: {candidate['email'][:50]}")
                                        # Mark email as processed so we don't re-check it every sync
                                        if msg_id:
                                            try:
                                                await asyncio.to_thread(
                                                    db_service.mark_email_processed,
                                                    msg_id, '', 'blocked-indeed-relay'
                                                )
                                            except Exception:
                                                pass
                                        return
                                    
                                    # Check if candidate already exists in DB
                                    existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])
                                    
                                    needs_ai = False
                                    if not existing:
                                        needs_ai = True
                                        new_count += 1
                                    else:
                                        # Returning candidate — smart-merge new info
                                        candidate = db_service.smart_merge_candidate(existing, candidate)
                                        # Only re-run AI if we never scored them properly
                                        if (not existing.get('ai_analysis')
                                                and (existing.get('matchScore') or existing.get('match_score') or 0) <= 0):
                                            needs_ai = True
                                    
                                    # AI processing - use resume text OR email summary
                                    analysis_text = candidate.get('resume_text') or candidate.get('summary', '')
                                    if analysis_text:
                                        candidate['resume_text'] = analysis_text[:5000]
                                    
                                    if needs_ai and analysis_text and len(analysis_text) > 20:
                                        try:
                                            ai_analysis = await asyncio.wait_for(
                                                ai_service.analyze_candidate(analysis_text),
                                                timeout=AI_ANALYSIS_TIMEOUT
                                            )
                                            if ai_analysis and ai_analysis.get('quality_score') is not None and ai_analysis.get('quality_score') > 0:
                                                # Map quality_score to matchScore for database
                                                # Email-parsed values take priority over LLM for contact info
                                                candidate.update({
                                                    'job_category': ai_analysis.get('job_category', 'General'),
                                                    'matchScore': ai_analysis.get('quality_score', 50),
                                                    'summary': ai_analysis.get('summary', candidate.get('summary', '')),
                                                    'skills': ai_analysis.get('skills', candidate.get('skills', [])),
                                                    'experience': ai_analysis.get('experience', candidate.get('experience', 0)),
                                                    'education': ai_analysis.get('education', []),
                                                    'phone': candidate.get('phone') or ai_analysis.get('phone', ''),
                                                    'location': candidate.get('location') or ai_analysis.get('location', ''),
                                                    'linkedin': candidate.get('linkedin') or ai_analysis.get('linkedin', ''),
                                                    'certifications': ai_analysis.get('certifications', []),
                                                    'languages': ai_analysis.get('languages', []),
                                                    'work_history': ai_analysis.get('work_history', []),
                                                })
                                                # Determine status from score
                                                score = ai_analysis.get('quality_score', 50)
                                                candidate['status'] = 'Strong' if score >= 70 else ('Partial' if score >= 40 else 'Reject')
                                                logger.info(f"✅ AI scored {candidate.get('name')}: {score}%")
                                        except Exception as ai_err:
                                            logger.warning(f"AI analysis failed ({type(ai_err).__name__}): {str(ai_err)[:100]}")
                                            # Calculate score from already-extracted candidate data instead of hardcoding
                                            skills = candidate.get('skills', [])
                                            exp = candidate.get('experience', 0)
                                            if skills or exp:
                                                fallback_score = 35.0
                                                fallback_score += min(30, len(skills) * 2.5 + (10 if skills else 0))
                                                if exp:
                                                    fallback_score += min(20, 6 + exp * 2)
                                                candidate['matchScore'] = min(90, round(fallback_score, 1))
                                                logger.info(f"📊 Fallback score for {candidate.get('name')}: {candidate['matchScore']}% (from {len(skills)} skills, {exp}yr exp)")
                                            else:
                                                candidate['matchScore'] = 45
                                    
                                    # Save to database
                                    if existing:
                                        await asyncio.to_thread(db_service.update_candidate, candidate)
                                    else:
                                        await asyncio.to_thread(db_service.insert_candidate, candidate)
                                    
                                    # ------ Mark email as processed so we skip it next sync ------
                                    if msg_id:
                                        action = 'updated' if existing else 'inserted'
                                        try:
                                            await asyncio.to_thread(
                                                db_service.mark_email_processed,
                                                msg_id,
                                                candidate.get('id', ''),
                                                action
                                            )
                                        except Exception:
                                            pass  # non-critical

                                    # Save AI analysis if we got one
                                    if needs_ai and analysis_text and len(analysis_text) > 20:
                                        try:
                                            # Generate strengths/gaps from candidate data
                                            _skills = candidate.get('skills', [])
                                            _exp = candidate.get('experience', 0) or 0
                                            _edu = candidate.get('education', [])
                                            _certs = candidate.get('certifications', [])
                                            _score = candidate.get('matchScore', 50)
                                            
                                            _strengths = []
                                            if len(_skills) >= 8:
                                                _strengths.append(f"Strong technical profile with {len(_skills)} identified skills")
                                            elif len(_skills) >= 4:
                                                _strengths.append(f"Solid skill set covering {len(_skills)} technologies")
                                            if _exp >= 5:
                                                _strengths.append(f"{_exp} years of professional experience")
                                            elif _exp >= 2:
                                                _strengths.append(f"{_exp} years of relevant experience")
                                            if _edu and len(_edu) > 0:
                                                _strengths.append("Formal educational background documented")
                                            if _certs and len(_certs) > 0:
                                                _strengths.append(f"Certified: {', '.join(_certs[:3])}")
                                            if _score >= 70:
                                                _strengths.append("High overall profile quality")
                                            
                                            _gaps = []
                                            if len(_skills) < 3:
                                                _gaps.append("Limited skills information available")
                                            if _exp == 0:
                                                _gaps.append("Experience level not specified")
                                            if not _edu or len(_edu) == 0:
                                                _gaps.append("No education details provided")
                                            if not candidate.get('phone'):
                                                _gaps.append("No phone number on file")
                                            if not candidate.get('linkedin'):
                                                _gaps.append("No LinkedIn profile available")
                                            
                                            await asyncio.to_thread(
                                                db_service.save_ai_analysis,
                                                candidate.get('id', ''),
                                                {
                                                    'score': _score,
                                                    'job_category': candidate.get('job_category', 'General'),
                                                    'summary': candidate.get('summary', ''),
                                                    'skills': _skills,
                                                    'experience': _exp,
                                                    'strengths': _strengths[:5],
                                                    'gaps': _gaps[:5],
                                                    'analyzed_at': datetime.now().isoformat(),
                                                }
                                            )
                                        except Exception:
                                            pass
                                        
                                except Exception as e:
                                    logger.warning(f"Error processing message: {str(e)[:100]}")
                            
                            # Process sequentially to avoid overwhelming Ollama LLM
                            # (concurrent LLM requests cause TimeoutError on single-GPU setups)
                            BATCH_SIZE = 1
                            skipped_count = 0
                            for i in range(0, len(messages), BATCH_SIZE):
                                batch = messages[i:i+BATCH_SIZE]
                                await asyncio.gather(*[process_graph_message(msg) for msg in batch], return_exceptions=True)
                                
                                if len(messages) > 50 and (i + BATCH_SIZE) % 50 == 0:
                                    logger.info(f"📊 Progress: {min(i+BATCH_SIZE, len(messages))}/{len(messages)} emails processed...")
                            
                            total_processed_after = await asyncio.to_thread(lambda: db_service.get_processed_email_count())
                            newly_processed = total_processed_after - processed_count_before
                            logger.info(f"✅ OAuth2 sync: {primary_email} - {len(messages)} total emails, {new_count} new candidates, {newly_processed} newly processed, {len(messages) - newly_processed} already processed")
                            oauth2_success = True
                            # Update last sync time on success — persist to DB
                            _last_email_sync_time = sync_start_time
                            try:
                                await asyncio.to_thread(db_service.set_sync_metadata, 'last_email_sync_time', sync_start_time)
                            except Exception:
                                pass  # non-critical
                            
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
                                logger.info(f"   1. Open: {os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')[0]}")
                                logger.info("   2. Go to: Settings → Email Integration")
                                logger.info("   3. Click: Connect Microsoft Account")
                                logger.info("   4. Sign in and grant permissions")
                                logger.info("   → After this ONE-TIME login, auto-refresh works FOREVER")
                                logger.info("")
                                logger.info("=" * 70)
                                # Only clear application tokens — NEVER delete delegated tokens with refresh_token
                                if token_data.get('auth_type') == 'application' and not token_data.get('refresh_token'):
                                    token_storage.delete_token(primary_email)
                            elif '400' in str(error_msg) or '404' in str(error_msg):
                                logger.info("="  * 70)
                                logger.info("📋 EMAIL SYNC REQUIRES MICROSOFT OAUTH LOGIN")
                                logger.info("="  * 70)
                                logger.info("")
                                logger.info("The email address may not be an Azure AD mailbox.")
                                logger.info("To sync emails, complete the ONE-TIME Microsoft OAuth login:")
                                logger.info(f"   1. Open: {os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')[0]}")
                                logger.info("   2. Go to: Settings → Email Integration")
                                logger.info("   3. Click: Connect Microsoft Account")
                                logger.info("   4. Sign in with your Microsoft/Outlook account")
                                logger.info("   → After this ONE-TIME login, auto-refresh works FOREVER")
                                logger.info("="  * 70)
                                # Only clear application tokens — NEVER delete delegated tokens with refresh_token
                                if token_data.get('auth_type') == 'application' and not token_data.get('refresh_token'):
                                    token_storage.delete_token(primary_email)
                            elif 'token' in error_msg.lower() or 'unauthorized' in error_msg.lower():
                                logger.info("🔄 Token issue detected - only clearing application tokens")
                                if token_data.get('auth_type') == 'application' and not token_data.get('refresh_token'):
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
                        
                        # Always process ALL emails — rely on is_email_processed for dedup
                        logger.info(f"📥 Fetching ALL emails for {account.name} (dedup via processing log)...")
                        
                        # Fetch ALL emails
                        emails = await asyncio.wait_for(
                            scraper_service.fetch_emails(mail, process_all=True),
                            timeout=600  # 10 minute max for fetching large inboxes
                        )
                        new_count = 0
                        
                        # PARALLEL PROCESSING - Process candidates in batches of 10 concurrently
                        async def process_single_candidate(email_data):
                            nonlocal new_count
                            try:
                                # ------ IDEMPOTENT: skip already-processed emails ------
                                msg_id = email_data.get('message_id', '')
                                if msg_id and await asyncio.to_thread(db_service.is_email_processed, msg_id):
                                    return  # already handled — skip
                                
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
                                elif not existing.get('ai_analysis') or not existing.get('job_category') or existing.get('job_category') == 'General':
                                    # Existing candidate without AI analysis
                                    needs_ai_processing = True
                                
                                # AI Processing only for new/unprocessed candidates
                                if needs_ai_processing:
                                    try:
                                        # Use resume text OR summary for analysis
                                        analysis_text = candidate.get('resume_text', '') or candidate.get('summary', '')
                                        # Store resume text for future AI chat access
                                        if analysis_text:
                                            candidate['resume_text'] = analysis_text[:10000]
                                        
                                        if analysis_text and len(analysis_text) > 20:
                                            ai_analysis = await asyncio.wait_for(
                                                ai_service.analyze_candidate(analysis_text),
                                                timeout=AI_ANALYSIS_TIMEOUT
                                            )
                                            if ai_analysis and ai_analysis.get('quality_score') is not None and ai_analysis.get('quality_score') > 0:
                                                # Map quality_score to matchScore for database
                                                score = ai_analysis.get('quality_score', 50)
                                                # Email-parsed values take priority over LLM for contact info
                                                candidate.update({
                                                    'job_category': ai_analysis.get('job_category', 'General'),
                                                    'matchScore': score,
                                                    'summary': ai_analysis.get('summary', candidate.get('summary', '')),
                                                    'skills': ai_analysis.get('skills', candidate.get('skills', [])),
                                                    'experience': ai_analysis.get('experience', candidate.get('experience', 0)),
                                                    'education': ai_analysis.get('education', []),
                                                    'phone': candidate.get('phone') or ai_analysis.get('phone', ''),
                                                    'location': candidate.get('location') or ai_analysis.get('location', ''),
                                                    'linkedin': candidate.get('linkedin') or ai_analysis.get('linkedin', ''),
                                                    'certifications': ai_analysis.get('certifications', []),
                                                    'languages': ai_analysis.get('languages', []),
                                                    'status': 'Strong' if score >= 70 else ('Partial' if score >= 40 else 'Reject'),
                                                })
                                                logger.info(f"✅ AI scored {candidate.get('name')}: {score}%")
                                    except asyncio.TimeoutError:
                                        logger.warning(f"AI timeout for {candidate.get('name')} - using fallback score")
                                        skills = candidate.get('skills', [])
                                        exp = candidate.get('experience', 0)
                                        if skills or exp:
                                            fallback_score = 35.0 + min(30, len(skills) * 2.5 + (10 if skills else 0)) + (min(20, 6 + exp * 2) if exp else 0)
                                            candidate['matchScore'] = min(90, round(fallback_score, 1))
                                        else:
                                            candidate['matchScore'] = 45
                                    except Exception as ai_err:
                                        logger.warning(f"AI error ({type(ai_err).__name__}): {str(ai_err)[:100]}")
                                        skills = candidate.get('skills', [])
                                        exp = candidate.get('experience', 0)
                                        if skills or exp:
                                            fallback_score = 35.0 + min(30, len(skills) * 2.5 + (10 if skills else 0)) + (min(20, 6 + exp * 2) if exp else 0)
                                            candidate['matchScore'] = min(90, round(fallback_score, 1))
                                        else:
                                            candidate['matchScore'] = 45
                                
                                # Save to database
                                if existing:
                                    await asyncio.to_thread(db_service.update_candidate, candidate)
                                else:
                                    await asyncio.to_thread(db_service.insert_candidate, candidate)
                                
                                # ------ Mark email as processed for idempotent sync ------
                                if msg_id:
                                    try:
                                        action = 'updated' if existing else 'inserted'
                                        await asyncio.to_thread(
                                            db_service.mark_email_processed,
                                            msg_id,
                                            candidate.get('id', ''),
                                            action
                                        )
                                    except Exception:
                                        pass  # non-critical
                                        
                            except Exception as e:
                                logger.warning(f"Error processing candidate: {str(e)[:100]}")
                        
                        # Process sequentially to avoid overwhelming Ollama LLM
                        BATCH_SIZE = 1
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
            
            # Wait for next sync - uses config value (default 15 min), env var overrides
            sync_interval = int(os.getenv('SYNC_INTERVAL_MINUTES', str(_settings.sync_interval_minutes))) * 60
            logger.info(f"⏰ Auto-sync: Next sync in {sync_interval//60} minutes")
            await asyncio.sleep(sync_interval)
            
        except Exception as e:
            logger.error(f"Auto-sync background task error: {str(e)}")
            await asyncio.sleep(60)

# =====================================================
# GCS Database Persistence (prevents data loss on redeploy)
# =====================================================
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "efforts-recruitment-data")
GCS_DB_BLOB_PATH = "db/recruitment.db"
LOCAL_DB_PATH = "./recruitment.db"
_db_backup_task = None

def _get_gcs_bucket():
    """Get GCS bucket client (lazy import to avoid startup failure if not installed)"""
    try:
        from google.cloud import storage
        client = storage.Client()
        return client.bucket(GCS_BUCKET_NAME)
    except Exception as e:
        logger.warning(f"⚠️ GCS: Could not connect to bucket '{GCS_BUCKET_NAME}': {e}")
        return None

def restore_db_from_gcs():
    """Download recruitment.db from GCS on startup (blocking, runs before DB init)"""
    if not _settings.is_production:
        print("💾 GCS restore: Skipped (not production)", flush=True)
        return False
    try:
        bucket = _get_gcs_bucket()
        if not bucket:
            print("💾 GCS restore: No bucket connection", flush=True)
            return False
        blob = bucket.blob(GCS_DB_BLOB_PATH)
        if not blob.exists():
            print("💾 GCS restore: No database backup found — starting fresh", flush=True)
            return False
        
        # 🔒 CRITICAL: Close ALL existing SQLite connections and remove WAL/SHM files
        # before downloading. Module-level init may have created stale WAL files
        # that interfere with the downloaded backup.
        import sqlite3 as _sqlite3
        try:
            _db_svc = get_db_service()
            with _db_svc.connection_lock:
                while _db_svc._connection_pool:
                    _old = _db_svc._connection_pool.pop()
                    try:
                        _old.close()
                    except Exception:
                        pass
        except Exception:
            pass
        
        # Remove stale WAL/SHM files
        for suffix in ['-wal', '-shm']:
            wal_path = LOCAL_DB_PATH + suffix
            if os.path.exists(wal_path):
                try:
                    os.remove(wal_path)
                except Exception:
                    pass
        
        # Remove existing DB file before download
        if os.path.exists(LOCAL_DB_PATH):
            try:
                os.remove(LOCAL_DB_PATH)
            except Exception:
                pass
        
        blob.download_to_filename(LOCAL_DB_PATH)
        size_mb = os.path.getsize(LOCAL_DB_PATH) / (1024 * 1024)
        # Validate: check if DB actually has candidates (not just an empty schema)
        import sqlite3
        try:
            conn = sqlite3.connect(LOCAL_DB_PATH)
            count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
            conn.close()
            if count == 0:
                print(f"⚠️ GCS restore: DB downloaded ({size_mb:.1f} MB) but has 0 candidates — will try JSON seed", flush=True)
                os.remove(LOCAL_DB_PATH)
                return False
            print(f"✅ GCS restore: Downloaded recruitment.db ({size_mb:.1f} MB, {count} candidates)", flush=True)
            return True
        except Exception as db_err:
            print(f"⚠️ GCS restore: DB downloaded but validation failed: {db_err} — will try JSON seed", flush=True)
            if os.path.exists(LOCAL_DB_PATH):
                os.remove(LOCAL_DB_PATH)
            return False
    except Exception as e:
        print(f"❌ GCS restore failed: {e}", flush=True)
        return False

def backup_db_to_gcs():
    """Upload recruitment.db to GCS (blocking)"""
    try:
        if not os.path.exists(LOCAL_DB_PATH):
            logger.warning("💾 GCS backup: No local database file found")
            return False
        bucket = _get_gcs_bucket()
        if not bucket:
            return False
        # Upload main backup
        blob = bucket.blob(GCS_DB_BLOB_PATH)
        blob.upload_from_filename(LOCAL_DB_PATH, timeout=120)
        size_mb = os.path.getsize(LOCAL_DB_PATH) / (1024 * 1024)
        # Also keep a timestamped snapshot (last 3 rotated)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_blob = bucket.blob(f"db/snapshots/recruitment_{ts}.db")
        snapshot_blob.upload_from_filename(LOCAL_DB_PATH, timeout=120)
        logger.info(f"✅ GCS backup: Uploaded recruitment.db ({size_mb:.1f} MB) + snapshot")
        # Clean old snapshots — keep last 3
        try:
            blobs = list(bucket.list_blobs(prefix="db/snapshots/"))
            if len(blobs) > 3:
                blobs.sort(key=lambda b: b.name)
                for old_blob in blobs[:-3]:
                    old_blob.delete()
                    logger.info(f"🗑️ GCS: Deleted old snapshot {old_blob.name}")
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"❌ GCS backup failed: {e}")
        return False

async def periodic_db_backup(interval_minutes: int = 30):
    """Periodically backup the database to GCS"""
    while True:
        try:
            await asyncio.sleep(interval_minutes * 60)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, backup_db_to_gcs)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Periodic DB backup error: {e}")
            await asyncio.sleep(60)

async def _background_seed_from_json():
    """Seed database from JSON backup in GCS — runs as background task after server starts"""
    await asyncio.sleep(2)  # Let server finish startup first
    try:
        print("💾 [BG] Starting JSON seed from GCS...", flush=True)
        import sqlite3, hashlib as _hashlib
        
        bucket = _get_gcs_bucket()
        if not bucket:
            print("⚠️ [BG] No GCS bucket available", flush=True)
            return
        
        json_blob = bucket.blob("backups/candidates_backup.json")
        if not json_blob.exists():
            print("⚠️ [BG] No JSON backup found in GCS", flush=True)
            return
        
        import tempfile
        tmp_path = tempfile.mktemp(suffix='.json')
        
        # Download in executor to not block event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, json_blob.download_to_filename, tmp_path)
        file_size = os.path.getsize(tmp_path) / (1024*1024)
        print(f"💾 [BG] Downloaded JSON backup ({file_size:.1f} MB)", flush=True)
        
        def _do_seed():
            with open(tmp_path, 'r', encoding='utf-8-sig') as jf:
                backup_data = json.load(jf)
            
            candidates = backup_data.get('candidates', [])
            print(f"💾 [BG] Found {len(candidates)} candidates", flush=True)
            
            if not candidates:
                return 0
            
            db_path = LOCAL_DB_PATH
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, email_hash TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, phone TEXT, location TEXT, skills TEXT, experience INTEGER,
                    education TEXT, summary TEXT, work_history TEXT, linkedin TEXT,
                    status TEXT DEFAULT 'New', match_score REAL DEFAULT 0.0,
                    job_category TEXT, job_subcategory TEXT, applied_date TEXT, last_updated TEXT,
                    raw_email_subject TEXT, is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    ai_analysis TEXT, certifications TEXT, languages TEXT, resume_text TEXT,
                    strengths TEXT, gaps TEXT
                )
            """)
            conn.commit()
            
            count = 0
            errors = 0
            for c in candidates:
                try:
                    email = c.get('email', '')
                    if not email:
                        continue
                    email_hash = _hashlib.sha256(email.lower().strip().encode()).hexdigest()
                    education_data = c.get('education', '[]')
                    if isinstance(education_data, list):
                        education_data = json.dumps(education_data)
                    conn.execute("""
                        INSERT OR REPLACE INTO candidates (
                            id, email, email_hash, name, phone, location, 
                            skills, experience, education, summary, work_history,
                            linkedin, status, match_score, job_category, job_subcategory,
                            applied_date, last_updated, raw_email_subject,
                            certifications, languages, resume_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        c.get('id', email_hash[:12]),
                        email, email_hash,
                        c.get('name', 'Unknown'),
                        c.get('phone', ''),
                        c.get('location', ''),
                        json.dumps(c.get('skills', [])),
                        c.get('experience', 0),
                        education_data,
                        c.get('summary', ''),
                        json.dumps(c.get('workHistory', c.get('work_history', []))),
                        c.get('linkedin', ''),
                        c.get('status', 'New'),
                        c.get('matchScore', c.get('match_score', 45)),
                        c.get('job_category', c.get('jobCategory', 'General')),
                        c.get('job_subcategory', c.get('jobSubcategory', '')),
                        c.get('appliedDate', c.get('applied_date', '')),
                        c.get('last_updated', ''),
                        c.get('raw_email_subject', ''),
                        json.dumps(c.get('certifications', [])),
                        json.dumps(c.get('languages', [])),
                        c.get('resume_text', ''),
                    ))
                    count += 1
                    if count % 1000 == 0:
                        conn.commit()
                        print(f"💾 [BG] Progress: {count}/{len(candidates)}...", flush=True)
                except Exception as ins_err:
                    errors += 1
                    if errors <= 3:
                        print(f"⚠️ [BG] Insert error #{errors}: {ins_err}", flush=True)
            
            conn.commit()
            conn.close()
            print(f"✅ [BG] Seeded {count}/{len(candidates)} candidates ({errors} errors)", flush=True)
            return count
        
        count = await loop.run_in_executor(None, _do_seed)
        os.unlink(tmp_path)
        
        if count and count > 0:
            await loop.run_in_executor(None, backup_db_to_gcs)
            print(f"✅ [BG] Database backed up to GCS", flush=True)
    except Exception as e:
        print(f"❌ [BG] JSON seed failed: {e}", flush=True)
        import traceback
        traceback.print_exc()


async def _background_process_candidates(interval_minutes: int = 30):
    """
    Background task that periodically processes unprocessed candidates.
    - Checks for candidates with missing ai_analysis or low match_score
    - Processes them in batches using Gemini (cost-effective)
    - Runs every 30 minutes to catch new email imports
    """
    await asyncio.sleep(180)  # Wait 3 min for startup + model loading to complete
    
    while True:
        try:
            loop = asyncio.get_event_loop()
            
            # Find candidates needing processing
            def _get_unprocessed():
                import sqlite3
                db_path = os.path.join(os.getcwd(), 'recruitment.db')
                if not os.path.exists(db_path):
                    return []
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, skills, experience, education, location, 
                           match_score, job_category, summary, resume_text
                    FROM candidates 
                    WHERE is_active = 1 
                    AND (ai_analysis IS NULL OR ai_analysis = '' OR match_score = 0 OR match_score IS NULL)
                    ORDER BY created_at DESC
                    LIMIT 50
                """)
                rows = [dict(r) for r in cursor.fetchall()]
                conn.close()
                return rows
            
            unprocessed = await loop.run_in_executor(None, _get_unprocessed)
            
            if unprocessed:
                logger.info(f"🔄 [BG-Process] Found {len(unprocessed)} unprocessed candidates, starting batch AI analysis...")
                
                ai_service = None
                try:
                    gemini_svc = get_gemini_service()
                    if gemini_svc and gemini_svc.available:
                        ai_service = gemini_svc
                except:
                    pass
                
                if ai_service:
                    processed = 0
                    for candidate in unprocessed:
                        try:
                            # Build analysis text from available data
                            resume_text = candidate.get('resume_text', '') or ''
                            skills = candidate.get('skills', '') or ''
                            summary = candidate.get('summary', '') or ''
                            
                            analysis_text = resume_text[:3000] if resume_text else f"Name: {candidate.get('name', '')}\nSkills: {skills}\nExperience: {candidate.get('experience', '')}\nLocation: {candidate.get('location', '')}\nSummary: {summary}"
                            
                            if len(analysis_text.strip()) < 20:
                                continue
                            
                            # Run AI analysis with timeout
                            result = await asyncio.wait_for(
                                ai_service.analyze_candidate(analysis_text),
                                timeout=60
                            )
                            
                            if result:
                                # Update candidate in DB
                                def _update_candidate(cid, ai_result):
                                    import sqlite3, json as _json
                                    db_path = os.path.join(os.getcwd(), 'recruitment.db')
                                    conn = sqlite3.connect(db_path)
                                    
                                    # Safely extract score — handle None, missing keys, quality_score alias
                                    raw_score = ai_result.get('match_score') or ai_result.get('quality_score') or ai_result.get('overall_score')
                                    try:
                                        match_score = int(float(raw_score)) if raw_score is not None else 50
                                    except (ValueError, TypeError):
                                        match_score = 50
                                    job_category = ai_result.get('job_category', ai_result.get('category', '')) or ''
                                    
                                    conn.execute("""
                                        UPDATE candidates 
                                        SET ai_analysis = ?, match_score = CASE WHEN ? > 0 THEN ? ELSE match_score END,
                                            job_category = CASE WHEN ? != '' THEN ? ELSE job_category END
                                        WHERE id = ?
                                    """, [_json.dumps(ai_result), match_score, match_score, 
                                          job_category, job_category, cid])
                                    conn.commit()
                                    conn.close()
                                
                                await loop.run_in_executor(None, _update_candidate, candidate['id'], result)
                                processed += 1
                            
                            # Small delay to avoid API rate limits (cost optimization)
                            await asyncio.sleep(2)
                            
                        except asyncio.TimeoutError:
                            logger.warning(f"⏳ [BG-Process] Timeout processing {candidate.get('name', 'unknown')}")
                        except Exception as proc_err:
                            logger.warning(f"⚠️ [BG-Process] Error processing {candidate.get('name', 'unknown')}: {proc_err}")
                    
                    if processed > 0:
                        logger.info(f"✅ [BG-Process] Processed {processed}/{len(unprocessed)} candidates")
                        # Backup to GCS after processing
                        try:
                            await loop.run_in_executor(None, backup_db_to_gcs)
                        except:
                            pass
                else:
                    logger.info("⚠️ [BG-Process] No AI service available for processing")
            
            # Wait before next check
            await asyncio.sleep(interval_minutes * 60)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ [BG-Process] Error: {e}")
            await asyncio.sleep(300)  # Wait 5 min on error

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup/shutdown"""
    global background_sync_task, oauth_automation_service, _db_backup_task
    
    # Startup
    print("🚀 AI Recruitment Platform Starting...", flush=True)
    
    # 🔒 CRITICAL: Restore database from GCS BEFORE anything else
    db_restored = restore_db_from_gcs()
    
    # 🔒 CRITICAL: After GCS restore, the SQLite file was replaced on disk.
    # Existing connections (from module-level init) still point to the OLD file descriptor.
    # We must close them so new connections open the restored file.
    if db_restored:
        try:
            _db_svc = get_db_service()
            with _db_svc.connection_lock:
                while _db_svc._connection_pool:
                    _old_conn = _db_svc._connection_pool.pop()
                    try:
                        _old_conn.close()
                    except Exception:
                        pass
            print("✅ Cleared stale DB connections after GCS restore", flush=True)
        except Exception as _pool_err:
            print(f"⚠️ Pool clear: {_pool_err}", flush=True)
    
    # 🔒 CRITICAL: Re-initialize all tables after GCS restore
    # GCS backup may not have email_processing_log, sync_metadata, etc.
    try:
        _db_svc = get_db_service()
        _db_svc.init_database()
        print("✅ Database tables initialized (email_processing_log, sync_metadata, etc.)", flush=True)
    except Exception as _init_err:
        print(f"⚠️ DB table init: {_init_err}", flush=True)
    
    # 🔒 CRITICAL: Ensure users table exists AFTER GCS restore
    # GCS backup only has candidates, so users table may be missing
    try:
        _auth_svc = get_auth_service()
        _auth_svc._init_users_table()
        # Seed default admin user if no users exist
        try:
            with _auth_svc._get_connection() as _conn:
                _cur = _conn.cursor()
                _cur.execute("SELECT COUNT(*) FROM users")
                _user_count = _cur.fetchone()[0]
                if _user_count == 0:
                    _auth_svc.register(
                        email="admin@developer.com",
                        password="Maahir@12",
                        name="Admin User",
                        username="admin"
                    )
                    print("✅ Default admin user created", flush=True)
                else:
                    print(f"✅ Users table OK ({_user_count} users)", flush=True)
        except Exception as _user_err:
            print(f"⚠️ Admin user seed: {_user_err}", flush=True)
    except Exception as _tbl_err:
        print(f"⚠️ Users table init failed: {_tbl_err}", flush=True)
    
    # If no DB was restored, schedule background seeding (don't block startup)
    _seed_needed = not db_restored and _settings.is_production
    
    print(f"Environment: {'Production' if _settings.is_production else 'Development'}", flush=True)
    logger.info(f"🤖 AI Tier Mode: {_settings.ai_tier_mode} → {' → '.join(_settings.ai_tier_order)}")
    
    # Initialize Gemini (if configured)
    if gemini_service and gemini_service.available:
        logger.info(f"🌟 Gemini: {gemini_service.model_name} (ready)")
    elif _settings.gemini_api_key:
        logger.warning("⚠️ Gemini: API key set but service failed to initialize")
    else:
        logger.info("💡 Gemini: not configured (set GEMINI_API_KEY for cloud deployment)")
    
    # Initialize Local LLM (Ollama) — SKIP in production (Cloud Run has no Ollama)
    if _settings.is_production:
        logger.info("🧠 LLM: Ollama SKIPPED (production — using Gemini + sentence-transformers)")
    else:
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
    
    # OpenAI status
    if fallback_service:
        logger.info(f"💳 OpenAI: {openai_service.model} (fallback ready)")
    
    logger.info(f"📧 Email Accounts: {len(scraper_service.email_accounts)} configured")
    logger.info(f"⚡ Max Concurrent Requests: {MAX_CONCURRENT_REQUESTS}")
    
    # Initialize OAuth Automation Service
    oauth_automation_service = get_oauth_automation()
    
    # Check if OAuth is properly configured — do this in background to avoid blocking startup
    async def _init_oauth_background():
        """Initialize OAuth in background so server starts accepting requests immediately"""
        try:
            if oauth_automation_service.is_configured:
                logger.info(f"🔐 OAuth2 Automation: Configured for {oauth_automation_service.primary_email}")
                
                # Check and auto-refresh token if needed (with timeout)
                try:
                    auth_status = await asyncio.wait_for(
                        oauth_automation_service.check_auth_status(),
                        timeout=10
                    )
                    logger.info(f"🔐 OAuth2 Status: {auth_status.value}")
                    
                    if auth_status.value in ['expired', 'no_token']:
                        result = await asyncio.wait_for(
                            oauth_automation_service.ensure_valid_token(),
                            timeout=15
                        )
                        if result['status'] == 'success':
                            logger.info(f"✅ OAuth2 auto-authenticated successfully")
                        else:
                            logger.warning(f"⚠️ OAuth2 auto-auth failed: {result.get('message')} - manual auth may be needed")
                except asyncio.TimeoutError:
                    logger.warning("⚠️ OAuth2 initialization timed out — will retry during sync")
            else:
                logger.info("📧 OAuth2 Automation: Not configured (missing credentials)")
        except Exception as e:
            logger.warning(f"⚠️ OAuth2 background init error: {e}")
    
    # Launch OAuth init as background task — don't block server startup
    asyncio.create_task(_init_oauth_background())
    
    # Auto-sync enabled? - Use new OAuth automation
    auto_sync_enabled = os.getenv('AUTO_SYNC_ENABLED', 'true').lower() == 'true'
    has_email_accounts = len(scraper_service.email_accounts) > 0
    
    if auto_sync_enabled and (has_email_accounts or oauth_automation_service.is_configured):
        logger.info(f"🔄 Auto-sync: ENABLED (every {os.getenv('SYNC_INTERVAL_MINUTES', '15')} minutes)")
        
        # Start single unified email sync loop (cost-optimized: no duplicate loops)
        try:
            background_sync_task = asyncio.create_task(auto_sync_emails())
            logger.info("🔐 Email sync: Single unified loop started")
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
        
        # Start campaign processor background task (every 1 hour to reduce costs)
        campaign_task = asyncio.create_task(run_campaign_processor(interval_seconds=3600))
        logger.info("📬 Campaign processor started (checks every 60 minutes)")
    except Exception as e:
        logger.warning(f"⚠️ Advanced services initialization warning: {str(e)}")
    
    # Start periodic GCS database backup (every 30 minutes in production)
    if _settings.is_production:
        _db_backup_task = asyncio.create_task(periodic_db_backup(interval_minutes=120))
        logger.info("💾 GCS auto-backup: Enabled (every 2 hours)")
    
    # Launch background seed from JSON if no DB was restored
    _seed_task = None
    if _seed_needed:
        print("💾 Launching background JSON seed task...", flush=True)
        _seed_task = asyncio.create_task(_background_seed_from_json())
    
    # Launch background candidate processing (every 30 min for faster catch-up)
    _process_task = asyncio.create_task(_background_process_candidates(interval_minutes=30))
    logger.info("🧠 Background candidate processing: Enabled (every 30 minutes)")
    
    print("✅ Server ready", flush=True)
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down gracefully...")
    
    # 🔒 CRITICAL: Backup database to GCS before shutdown
    if _settings.is_production:
        logger.info("💾 Saving database to GCS before shutdown...")
        backup_db_to_gcs()
    
    if _db_backup_task:
        _db_backup_task.cancel()
    if oauth_automation_service:
        await oauth_automation_service.stop()
    if background_sync_task:
        background_sync_task.cancel()
    response_cache.clear()

app = FastAPI(
    title=_settings.app_name,
    description="Optimized recruitment platform with email scraping, AI job matching, ML ranking, and automated campaigns",
    version=_settings.app_version,
    docs_url="/api/docs" if DEBUG else None,
    redoc_url="/api/redoc" if DEBUG else None,
    lifespan=lifespan
)

# Global exception handler — sanitize error details in production

@app.exception_handler(HTTPException)
async def sanitized_http_exception_handler(request, exc: HTTPException):
    """Sanitize error messages to prevent internal info leakage in production"""
    detail = exc.detail
    if not DEBUG and exc.status_code >= 500:
        # In production, log the real error but return a generic message
        logger.error(f"HTTP {exc.status_code} on {request.url.path}: {detail}")
        detail = "An internal error occurred. Please try again later."
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})

@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    """Catch unhandled exceptions — never leak stack traces"""
    logger.error(f"Unhandled exception on {request.url.path}: {type(exc).__name__}: {str(exc)}")
    detail = f"Internal server error: {type(exc).__name__}" if DEBUG else "An internal error occurred. Please try again later."
    return JSONResponse(status_code=500, content={"detail": detail})

# Include advanced AI services router
app.include_router(advanced_router)

# CORS configuration - allow all origins in production for Cloud Run flexibility
allowed_origins = os.getenv('CORS_ORIGINS', _settings.cors_origins).split(',')
# In production, always allow the Cloud Run frontend
if _settings.is_production:
    # In production, only allow the specific frontend origin
    _cors_origin = os.getenv('CORS_ORIGINS', 'https://efforts-recruitment.web.app')
    allowed_origins = [o.strip() for o in _cors_origin.split(',') if o.strip()]
else:
    for dev_origin in ['http://localhost:3000', 'http://localhost:3001', 'http://localhost:5173']:
        if dev_origin not in allowed_origins:
            allowed_origins.append(dev_origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,  # Cache preflight requests for 1 hour
)
logger.info(f"✅ CORS enabled for: {', '.join(allowed_origins)}")

# Set up security, caching, rate-limiting, and timing middleware
from core.middleware import setup_middleware
setup_middleware(app)

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

# AI Services - Smart Tier Selection
# Priority: auto-detect based on environment (production → Gemini, local → Ollama)
local_ai_service = get_local_ai_service()
openai_service = get_openai_service() if USE_OPENAI_FALLBACK else None
gemini_service = get_gemini_service()  # Initialized if GEMINI_API_KEY is set

# ALWAYS use Local AI (sentence-transformers) as the base embedding/analysis service
ai_service = local_ai_service
fallback_service = openai_service if USE_OPENAI_FALLBACK else None  # Only if explicitly enabled

# Log AI tier configuration
_ai_tier = _settings.ai_tier_order
logger.info(f"🤖 AI Tier Order: {' → '.join(_ai_tier)}")
if gemini_service and gemini_service.available:
    logger.info(f"   ✅ Gemini: {gemini_service.model_name} (ready)")
else:
    logger.info(f"   ⚠️ Gemini: not configured (set GEMINI_API_KEY)")
if openai_service:
    logger.info(f"   ✅ OpenAI: {openai_service.model} (ready)")
else:
    logger.info(f"   ⚠️ OpenAI: not configured")

scraper_service = get_scraper_service()
db_service = get_db_service()

# Background scraper task
scraper_task = None

@app.get("/")
async def root():
    return {
        "message": _settings.app_name,
        "version": _settings.app_version,
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

@app.get("/version")
async def version():
    return {"version": "v16-stable", "deployed": "2026-02-17"}

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
    except Exception:
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

@app.post("/api/admin/backup-db")
async def manual_backup_db(auth=Depends(require_auth)):
    """Manually trigger a database backup to GCS"""
    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, backup_db_to_gcs)
        if success:
            size_mb = os.path.getsize(LOCAL_DB_PATH) / (1024 * 1024) if os.path.exists(LOCAL_DB_PATH) else 0
            return {"status": "success", "message": f"Database backed up to GCS ({size_mb:.1f} MB)"}
        else:
            return {"status": "warning", "message": "Backup skipped — GCS not available or no database file"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {str(e)}")


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
        "version": os.getenv('APP_VERSION', '4.1.0')
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
                    "2. Click 'New registration' with name 'Efforts Solutions AI Recruiter'",
                    "3. Set redirect URI: http://localhost:5173/email (Web type)",
                    "4. Go to 'Certificates & secrets' → New client secret",
                    "5. Go to 'API permissions' → Add: Mail.Read, Mail.ReadWrite, Mail.Send, User.Read, offline_access",
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
async def start_scraper(background_tasks: BackgroundTasks, current_user: dict = Depends(require_auth)):
    """Start the email scraper manually"""
    global scraper_task
    if scraper_task and not scraper_task.done():
        return {"message": "Scraper already running"}
    
    scraper_task = asyncio.create_task(scraper_service.run_continuous_scraper())
    return {"message": "Email scraper started"}

@app.post("/api/scraper/stop")
async def stop_scraper(current_user: dict = Depends(require_auth)):
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
async def trigger_manual_scrape(process_all: bool = False, current_user: dict = Depends(require_auth)):
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


# ====== PURGE INDEED RELAY / JUNK CANDIDATES ======
@app.post("/api/candidates/purge-indeed")
async def purge_indeed_candidates(current_user: dict = Depends(require_auth)):
    """
    Delete all candidates with Indeed relay emails (@indeedemail.com, conversation-* IDs).
    These are system-generated relay addresses, not real candidate emails.
    """
    try:
        response_cache.clear()
        result = await asyncio.to_thread(db_service.purge_indeed_candidates)
        logger.info(f"🗑️ Purged Indeed candidates: {result}")
        return {
            "status": "success",
            "message": f"Purged {result['total_deleted']} Indeed relay candidates",
            **result
        }
    except Exception as e:
        logger.error(f"Purge failed: {e}")
        raise HTTPException(500, f"Purge error: {str(e)}")


@app.post("/api/candidates/reset-and-reparse")
async def reset_and_reparse_all_emails(current_user: dict = Depends(require_auth)):
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
        primary_email = os.getenv('EMAIL_ADDRESS') or _settings.email_address or ''
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
                    except Exception:
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
    min_score: Optional[int] = None,
    fields: Optional[str] = None
):
    """
    Get candidates with OPTIMIZED pagination and caching
    Efficiently handles 100,000+ candidates
    Use fields=light for fast list views (skips resume_text, ai_analysis)
    """
    is_light = fields == 'light'
    # Create cache key
    cache_key = f"candidates_p{page}_l{limit}_c{job_category}_s{min_score}_f{fields}"
    
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
        
        # Use lightweight query for list views, full query for detail views
        async with db_semaphore:
            if is_light:
                candidates, total_count = await asyncio.to_thread(
                    db_service.get_candidates_light,
                    page,
                    limit,
                    filters
                )
            else:
                candidates, total_count = await asyncio.to_thread(
                    db_service.get_candidates_paginated,
                    page,
                    limit,
                    filters
                )
        
        result = {
            "page": page,
            "limit": limit,
            "candidates": candidates,
            "total": total_count,
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
async def reprocess_candidate_scores(current_user: dict = Depends(require_auth)):
    """
    Reprocess all candidates with match_score = 0 to calculate proper AI scores.
    This fixes candidates that were imported before AI scoring was properly connected.
    """
    try:
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get candidates with 0 score (not yet scored)
            cursor.execute("""
                SELECT id, email, name, skills, summary, education, work_history 
                FROM candidates 
                WHERE match_score = 0 OR match_score IS NULL
            """)
            rows = cursor.fetchall()
        
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
                    except Exception:
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
                            timeout=AI_ANALYSIS_TIMEOUT
                        )
                        new_score = ai_analysis.get('quality_score', 50)
                        new_category = ai_analysis.get('job_category', 'General')
                    except Exception:
                        new_score = 50
                        new_category = 'General'
                
                # Update database
                with db_service.get_connection() as update_conn:
                    update_cursor = update_conn.cursor()
                    update_cursor.execute("""
                        UPDATE candidates SET match_score = ?, job_category = ? WHERE id = ?
                    """, (new_score, new_category, candidate_id))
                    update_conn.commit()
                
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
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
            row = cursor.fetchone()
        
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
async def upload_resume(file: UploadFile = File(...), current_user: dict = Depends(require_auth)):
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
                    # Merge AI skills with parsed skills instead of overwriting
                    if ai_analysis.get('skills'):
                        existing_skills = set(s.lower() for s in parsed.get('skills', []))
                        merged_skills = list(parsed.get('skills', []))
                        for skill in ai_analysis['skills']:
                            if skill.lower() not in existing_skills:
                                merged_skills.append(skill)
                                existing_skills.add(skill.lower())
                        parsed['skills'] = merged_skills
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
            'job_subcategory': parsed.get('job_subcategory', ''),
            'appliedDate': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'raw_email_subject': f"Resume Upload: {filename}",
            'certifications': parsed.get('certifications', []),
            'languages': parsed.get('languages', []),
            'resume_text': resume_text[:10000] if resume_text else '',
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

        # Save detailed AI analysis if available
        if resume_text.strip():
            try:
                db_service.save_ai_analysis(candidate['id'], {
                    'score': round(ai_score, 1),
                    'job_category': job_category,
                    'summary': summary,
                    'skills': parsed.get('skills', []),
                    'experience': parsed.get('experience', 0),
                    'education': parsed.get('education', []),
                    'certifications': parsed.get('certifications', []),
                    'languages': parsed.get('languages', []),
                    'analyzed_at': datetime.now().isoformat(),
                })
            except Exception as e:
                logger.warning(f"Failed to save AI analysis: {e}")

        # Save resume file for future re-analysis
        try:
            content_type = 'application/pdf' if filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' if filename.lower().endswith('.docx') else 'application/octet-stream'
            db_service.save_resume(candidate['id'], filename, content, content_type)
        except Exception as e:
            logger.warning(f"Failed to save resume file: {e}")

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
async def upload_multiple_resumes(files: List[UploadFile] = File(...), current_user: dict = Depends(require_auth)):
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
                            existing_skills = set(s.lower() for s in parsed.get('skills', []))
                            merged_skills = list(parsed.get('skills', []))
                            for skill in ai_analysis['skills']:
                                if skill.lower() not in existing_skills:
                                    merged_skills.append(skill)
                                    existing_skills.add(skill.lower())
                            parsed['skills'] = merged_skills
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
                'job_subcategory': parsed.get('job_subcategory', ''),
                'appliedDate': datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                'raw_email_subject': f"Resume Upload: {filename}",
                'certifications': parsed.get('certifications', []),
                'languages': parsed.get('languages', []),
                'resume_text': resume_text[:10000] if resume_text else '',
            }

            existing = db_service.get_candidate_by_email(parsed['email'])
            if existing:
                candidate['id'] = existing['id']
                db_service.update_candidate(candidate)
            else:
                db_service.insert_candidate(candidate)

            # Save AI analysis
            try:
                db_service.save_ai_analysis(candidate['id'], {
                    'score': round(ai_score, 1),
                    'job_category': job_category,
                    'summary': summary,
                    'skills': parsed.get('skills', []),
                    'experience': parsed.get('experience', 0),
                    'education': parsed.get('education', []),
                    'certifications': parsed.get('certifications', []),
                    'languages': parsed.get('languages', []),
                    'analyzed_at': datetime.now().isoformat(),
                })
            except Exception:
                pass

            # Save resume file
            try:
                db_service.save_resume(candidate['id'], filename, content)
            except Exception:
                pass

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
async def get_candidate_deep_analysis(candidate_id: str):
    """
    Get deep AI analysis of a candidate including:
    - Detailed pros and cons
    - Career trajectory analysis
    - Hiring recommendation
    - Interview focus areas
    """
    try:
        # Get candidate from database
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
            row = cursor.fetchone()
        
        if not row:
            raise HTTPException(404, "Candidate not found")
        
        candidate = db_service._row_to_candidate(row)
        
        # Check cache first
        cache_key = f"deep_analysis_{candidate_id}"
        if cache_key in response_cache:
            cached = response_cache[cache_key]
            cached['from_cache'] = True
            return cached
        
        # TIER 1: Try Local LLM (Ollama) — Free
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                analysis = await llm_svc.analyze_candidate_deep(candidate)
                if analysis and analysis.get('overall_assessment', '') != 'Unable to perform deep analysis':
                    result = {
                        "candidate_id": candidate_id,
                        "candidate_name": candidate['name'],
                        **analysis,
                        "ai_powered": True,
                        "source": "local_llm"
                    }
                    response_cache[cache_key] = result
                    return result
        except Exception as llm_err:
            logger.warning(f"LLM deep analysis failed: {llm_err}")
        
        # TIER 2: Try OpenAI — Emergency fallback (only if explicitly enabled)
        if USE_OPENAI_FALLBACK:
            from services.openai_service import get_openai_service
            openai_svc = get_openai_service()
            
            if openai_svc:
                analysis = openai_svc.analyze_candidate_deep(candidate)
                result = {
                    "candidate_id": candidate_id,
                    "candidate_name": candidate['name'],
                    **analysis,
                    "ai_powered": True,
                    "source": "openai_fallback"
                }
                response_cache[cache_key] = result
                return result
        
        # TIER 3: Basic fallback — No AI
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
                "AI analysis unavailable - configure Ollama or OPENAI_API_KEY"
            ],
            "hiring_recommendation": {
                "verdict": "Review Needed",
                "confidence": 50,
                "ideal_roles": [],
                "interview_focus_areas": ["Technical skills", "Experience verification"]
            },
            "ai_powered": False,
            "source": "rule_based"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        raise HTTPException(500, f"Error analyzing candidate: {str(e)}")


@app.post("/api/ai/match-job-file")
async def match_candidates_to_job_file(
    file: UploadFile = File(None),
    job_description: str = Form(None),
    top_n: int = Form(10),
):
    """
    Match candidates from database against a job description supplied as a file (PDF/DOCX/TXT) or text.
    At least one of 'file' or 'job_description' is required.
    Parses the file to extract JD text, then runs AI matching against all DB candidates.
    """
    try:
        jd_text = ""

        # 1. Extract text from uploaded file
        if file and file.filename:
            filename = file.filename
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            if ext not in ('pdf', 'docx', 'doc', 'txt'):
                raise HTTPException(400, "Only PDF, DOCX and TXT files are supported for job descriptions.")
            content = await file.read()
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(400, "File too large. Max 10MB.")

            if ext == 'txt':
                jd_text = content.decode('utf-8', errors='ignore')
            else:
                # Use resume_parser to extract text from PDF/DOCX
                parsed = await resume_parser.parse_resume(content, filename)
                jd_text = parsed.get('raw_text', '') or parsed.get('summary', '')

        # 2. Use text input as fallback or supplement
        if job_description:
            if jd_text:
                jd_text = jd_text + "\n\n" + job_description
            else:
                jd_text = job_description

        if not jd_text or len(jd_text.strip()) < 30:
            raise HTTPException(400, "Could not extract sufficient text from the job description. Please provide a file or paste the JD text.")

        # Search the ENTIRE database for comprehensive matching
        candidates_list = await asyncio.to_thread(
            db_service.get_all_candidates_for_matching, {}
        )

        if not candidates_list:
            return {
                "status": "no_candidates",
                "message": "No candidates in database to match",
                "rankings": [],
                "job_analysis": {},
                "jd_text_length": len(jd_text)
            }

        total_searched = len(candidates_list)

        # TIER 1: Try Local LLM (Ollama)
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                ranked = await llm_svc.rank_candidates_for_job(candidates_list, jd_text, top_n)
                # Format for frontend: {rank, candidate_id, candidate_name, job_fit_score, ..., candidate_data}
                formatted_rankings = []
                for i, r in enumerate(ranked):
                    c = r.get('candidate', {})
                    m = r.get('match', {})
                    formatted_rankings.append({
                        'rank': i + 1,
                        'candidate_id': c.get('id', ''),
                        'candidate_name': c.get('name', 'Unknown'),
                        'job_fit_score': round(m.get('match_score', r.get('score', 50)), 1),
                        'overall_fit': m.get('overall_fit', 'Review Needed'),
                        'matched_skills': m.get('matched_skills', []),
                        'missing_skills': m.get('missing_skills', []),
                        'strengths': m.get('strengths', []),
                        'gaps': m.get('gaps', []),
                        'recommendation': m.get('recommendation', 'Review candidate profile'),
                        'match_reasons': m.get('strengths', [])[:3] or [f"Skills: {', '.join(c.get('skills', [])[:5])}"],
                        'interview_questions': m.get('interview_questions', []),
                        'candidate_data': {
                            'id': c.get('id', ''),
                            'name': c.get('name', 'Unknown'),
                            'email': c.get('email', ''),
                            'phone': c.get('phone', ''),
                            'location': c.get('location', ''),
                            'experience': c.get('experience', 0),
                            'matchScore': round(m.get('match_score', r.get('score', 50)), 1),
                            'status': c.get('status', 'New'),
                            'skills': c.get('skills', []),
                            'summary': c.get('summary', ''),
                            'jobCategory': c.get('jobCategory', c.get('job_category', 'General')),
                            'jobSubcategory': c.get('jobSubcategory', c.get('job_subcategory', '')),
                            'education': c.get('education', []),
                            'workHistory': c.get('workHistory', c.get('work_history', [])),
                            'hasResume': bool(c.get('resume_text') or c.get('hasResume')),
                            'appliedDate': c.get('appliedDate', c.get('applied_date', '')),
                            'isShortlisted': c.get('status', '') == 'Shortlisted',
                        },
                    })
                return {
                    "status": "success",
                    "rankings": formatted_rankings,
                    "ai_powered": True,
                    "source": "local_llm",
                    "total_candidates_searched": total_searched,
                    "jd_text_length": len(jd_text)
                }
        except Exception as llm_err:
            logger.warning(f"LLM job file matching failed: {llm_err}")

        # TIER 2: Try OpenAI (only if explicitly enabled)
        if USE_OPENAI_FALLBACK:
            from services.openai_service import get_openai_service
            openai_svc = get_openai_service()

            if openai_svc:
                result = openai_svc.match_candidates_to_job(jd_text, candidates_list, top_n)
                result['ai_powered'] = True
                result['source'] = 'openai_fallback'
                result['total_candidates_searched'] = total_searched
                result['jd_text_length'] = len(jd_text)
                return result

        # TIER 3: Enhanced keyword matching fallback
        jd_lower = jd_text.lower()
        for c in candidates_list:
            skill_matches = sum(1 for s in c.get('skills', []) if s.lower() in jd_lower)
            exp = c.get('experience', 0) or 0
            base_score = c.get('matchScore', 50) or 50
            c['job_fit_score'] = min(30 + skill_matches * 12 + min(exp, 10) * 2 + base_score * 0.2, 98)

        candidates_list.sort(key=lambda x: x.get('job_fit_score', 0), reverse=True)

        return {
            "status": "basic_match",
            "message": "Keyword-based matching (AI models unavailable)",
            "rankings": [
                {
                    "rank": i + 1,
                    "candidate_id": c['id'],
                    "candidate_name": c.get('name', 'Unknown'),
                    "job_fit_score": round(c.get('job_fit_score', 50), 1),
                    "match_reasons": [f"Skills: {', '.join(c.get('skills', [])[:5])}"],
                    "recommendation": "Strong Fit" if c.get('job_fit_score', 0) >= 70 else "Potential Fit" if c.get('job_fit_score', 0) >= 50 else "Review Needed"
                }
                for i, c in enumerate(candidates_list[:top_n])
            ],
            "job_analysis": {},
            "ai_powered": False,
            "source": "keyword_fallback",
            "total_candidates_searched": total_searched,
            "jd_text_length": len(jd_text)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job file matching error: {e}")
        raise HTTPException(500, f"Error matching candidates to job description: {str(e)}")


@app.post("/api/ai/match-job")
async def match_candidates_to_job_description(
    job_description: str = Body(..., embed=True),
    top_n: int = Body(10, embed=True),
    min_experience: Optional[int] = Body(None, embed=True),
    current_user: dict = Depends(require_auth)
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
        
        # Search the ENTIRE database for comprehensive matching
        filters = {}
        if min_experience:
            filters['min_experience'] = min_experience
        
        candidates = await asyncio.to_thread(
            db_service.get_all_candidates_for_matching, filters
        )
        
        if not candidates:
            return {
                "status": "no_candidates",
                "message": "No candidates in database to match",
                "rankings": [],
                "job_analysis": {}
            }
        
        # TIER 1: Try Local LLM (Ollama) — Free
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                ranked = await llm_svc.rank_candidates_for_job(candidates, job_description, top_n)
                # Format for frontend: {rank, candidate_id, candidate_name, job_fit_score, ..., candidate_data}
                formatted_rankings = []
                for i, r in enumerate(ranked):
                    c = r.get('candidate', {})
                    m = r.get('match', {})
                    formatted_rankings.append({
                        'rank': i + 1,
                        'candidate_id': c.get('id', ''),
                        'candidate_name': c.get('name', 'Unknown'),
                        'job_fit_score': round(m.get('match_score', r.get('score', 50)), 1),
                        'overall_fit': m.get('overall_fit', 'Review Needed'),
                        'matched_skills': m.get('matched_skills', []),
                        'missing_skills': m.get('missing_skills', []),
                        'strengths': m.get('strengths', []),
                        'gaps': m.get('gaps', []),
                        'recommendation': m.get('recommendation', 'Review candidate profile'),
                        'match_reasons': m.get('strengths', [])[:3] or [f"Skills: {', '.join(c.get('skills', [])[:5])}"],
                        'interview_questions': m.get('interview_questions', []),
                        'candidate_data': {
                            'id': c.get('id', ''),
                            'name': c.get('name', 'Unknown'),
                            'email': c.get('email', ''),
                            'phone': c.get('phone', ''),
                            'location': c.get('location', ''),
                            'experience': c.get('experience', 0),
                            'matchScore': round(m.get('match_score', r.get('score', 50)), 1),
                            'status': c.get('status', 'New'),
                            'skills': c.get('skills', []),
                            'summary': c.get('summary', ''),
                            'jobCategory': c.get('jobCategory', c.get('job_category', 'General')),
                            'jobSubcategory': c.get('jobSubcategory', c.get('job_subcategory', '')),
                            'education': c.get('education', []),
                            'workHistory': c.get('workHistory', c.get('work_history', [])),
                            'hasResume': bool(c.get('resume_text') or c.get('hasResume')),
                            'appliedDate': c.get('appliedDate', c.get('applied_date', '')),
                            'isShortlisted': c.get('status', '') == 'Shortlisted',
                        },
                    })
                return {
                    "status": "success",
                    "rankings": formatted_rankings,
                    "ai_powered": True,
                    "source": "local_llm",
                    "total_candidates_searched": len(candidates)
                }
        except Exception as llm_err:
            logger.warning(f"LLM job matching failed: {llm_err}")
        
        # TIER 2: Try OpenAI (only if explicitly enabled)
        if USE_OPENAI_FALLBACK:
            from services.openai_service import get_openai_service
            openai_svc = get_openai_service()
            
            if openai_svc:
                result = openai_svc.match_candidates_to_job(job_description, candidates, top_n)
                result['ai_powered'] = True
                result['source'] = 'openai_fallback'
                result['total_candidates_searched'] = len(candidates)
                return result
        
        # TIER 3: Basic keyword matching fallback
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
            "ai_powered": False,
            "source": "keyword_fallback"
        }
        
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
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            
            for cid in candidate_ids:
                cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (cid,))
                row = cursor.fetchone()
                if row:
                    candidates.append(db_service._row_to_candidate(row))
        
        if len(candidates) < 2:
            raise HTTPException(404, "Could not find enough candidates to compare")
        
        # TIER 1: Try Local LLM — Free
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                result = await llm_svc.compare_candidates(candidates, job_description)
                if result and not result.get('error'):
                    result['ai_powered'] = True
                    result['source'] = 'local_llm'
                    return result
        except Exception as llm_err:
            logger.warning(f"LLM comparison failed: {llm_err}")
        
        # TIER 2: OpenAI fallback (only if explicitly enabled)
        if USE_OPENAI_FALLBACK:
            from services.openai_service import get_openai_service
            openai_svc = get_openai_service()
            
            if openai_svc:
                result = openai_svc.generate_candidate_comparison(candidates, job_description)
                result['ai_powered'] = True
                result['source'] = 'openai_fallback'
                return result
        
        # TIER 3: Rule-based fallback
        candidates.sort(key=lambda x: x.get('matchScore', 0), reverse=True)
        return {
            "comparison_matrix": [
                {
                    "name": c['name'],
                    "overall_rank": i + 1,
                    "score": c.get('matchScore', 50),
                    "key_strengths": c.get('skills', [])[:3],
                    "key_weaknesses": ["AI analysis unavailable"],
                    "best_for": c.get('jobCategory', 'General'),
                    "risk_level": "unknown"
                }
                for i, c in enumerate(candidates)
            ],
            "head_to_head": {
                "winner": candidates[0]['name'],
                "reasoning": "Highest match score",
                "runner_up": candidates[1]['name'] if len(candidates) > 1 else None
            },
            "recommendation": "Configure Ollama or OPENAI_API_KEY for detailed comparison",
            "ai_powered": False,
            "source": "rule_based"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        raise HTTPException(500, f"Error comparing candidates: {str(e)}")


@app.post("/api/ai/chat")
async def ai_chat(
    message: str = Body(..., embed=True),
    include_candidates: bool = Body(True, embed=True),
    conversation_history: list = Body([], embed=True),
    num_candidates: int = Body(10, embed=True),
    current_user: dict = Depends(require_auth)
):
    """
    Enhanced AI chat with full database search capability.
    2-STAGE APPROACH: Pre-filter candidates by query relevance → Send subset to AI
    3-TIER FALLBACK: Gemini → OpenAI → Rule-based
    """
    try:
        candidates_data = None
        context = None
        
        if include_candidates:
            stats = await asyncio.to_thread(db_service.get_statistics)
            # Get ALL candidates with FULL data (work_history, certs, languages) for AI intelligence
            candidates = await asyncio.to_thread(
                db_service.get_candidates_for_ai, {}, 10000
            )
            candidates_data = candidates
            context = {
                'totalCandidates': stats.get('total_candidates', 0),
                'avgMatchScore': stats.get('avg_score', 0),
                'strongMatches': stats.get('strong_matches', 0),
                'recentCount': stats.get('recent_count', 0),
                'categories': stats.get('categories', {}),
            }
        
        # TIER 1: Try Gemini (cost-effective, always available in production)
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                gemini_result = await asyncio.wait_for(
                    gemini_svc.chat(message, context, conversation_history=conversation_history, candidates_data=candidates_data, return_candidates=True, num_candidates=num_candidates),
                    timeout=AI_TIMEOUT
                )
                if gemini_result:
                    # gemini_result is a dict with 'response' and 'candidates_lookup'
                    if isinstance(gemini_result, dict):
                        return {
                            "response": gemini_result.get('response', ''),
                            "ai_powered": True,
                            "context_included": include_candidates,
                            "source": "gemini",
                            "candidates_lookup": gemini_result.get('candidates_lookup', [])
                        }
                    else:
                        # Fallback for string response
                        return {
                            "response": gemini_result,
                            "ai_powered": True,
                            "context_included": include_candidates,
                            "source": "gemini"
                        }
            else:
                logger.warning(f"Gemini unavailable: svc={gemini_svc is not None}, available={getattr(gemini_svc, 'available', 'N/A')}")
        except asyncio.TimeoutError:
            logger.warning(f"Gemini chat timeout (>{AI_TIMEOUT}s)")
        except Exception as gemini_err:
            logger.warning(f"Gemini chat error: {gemini_err}")
        
        # TIER 2: Try Local LLM (Ollama) — Free, for local dev
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                llm_response = await asyncio.wait_for(
                    llm_svc.chat(message, context, conversation_history=conversation_history, candidates_data=candidates_data),
                    timeout=AI_TIMEOUT
                )
                if llm_response:
                    return {
                        "response": llm_response,
                        "ai_powered": True,
                        "context_included": include_candidates,
                        "source": "local_llm"
                    }
        except asyncio.TimeoutError:
            logger.warning(f"LLM chat timeout (>{AI_TIMEOUT}s)")
        except Exception as llm_err:
            logger.warning(f"LLM chat error: {llm_err}")
        
        # TIER 3: OpenAI fallback
        if USE_OPENAI_FALLBACK:
            from services.openai_service import get_openai_service
            openai_svc = get_openai_service()
            
            if openai_svc:
                response = openai_svc.chat_with_ai(message, context, candidates_data)
                return {
                    "response": response,
                    "ai_powered": True,
                    "context_included": include_candidates,
                    "source": "openai_fallback"
                }
        
        # TIER 4: Rule-based fallback
        return {
            "response": f"I understand you're asking about: '{message}'. Currently no AI services are available. "
                        f"Please configure GEMINI_API_KEY, Ollama, or OPENAI_API_KEY for intelligent responses.",
            "ai_powered": False,
            "context_included": include_candidates,
            "source": "rule_based"
        }
        
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return {
            "response": f"Error: {str(e)}",
            "ai_powered": False,
            "source": "error"
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
        with db_service.get_connection() as conn:
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
    analyze: bool = True,
    current_user: dict = Depends(require_auth)
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
                                # Email-parsed values take priority over LLM for contact info
                                batch_candidates[idx].update({
                                    'job_category': analysis.get('job_category', 'General'),
                                    'matchScore': analysis.get('quality_score', 50),
                                    'skills': analysis.get('skills', []),
                                    'experience': analysis.get('experience', 0),
                                    'education': analysis.get('education', []),
                                    'phone': batch_candidates[idx].get('phone') or analysis.get('phone', ''),
                                    'location': batch_candidates[idx].get('location') or analysis.get('location', ''),
                                    'linkedin': batch_candidates[idx].get('linkedin') or analysis.get('linkedin', ''),
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
async def import_linkedin_profile(profile: LinkedInProfileImport, current_user: dict = Depends(require_auth)):
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
            candidate_data['id'] = candidate_id
            db_service.update_candidate(candidate_data)
        else:
            db_service.insert_candidate(candidate_data)
        
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
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description, title, required_skills FROM job_descriptions WHERE id = ?", (job_description_id,))
            jd_row = cursor.fetchone()
            if not jd_row:
                raise HTTPException(404, f"Job description not found: {job_description_id}")
            
            job_text = f"{jd_row[1] or ''}\n{jd_row[0] or ''}\nRequired Skills: {jd_row[2] or ''}"
            
            # Resolve candidates
            if candidate_ids:
                placeholders = ','.join(['?' for _ in candidate_ids])
                cursor.execute(f"SELECT * FROM candidates WHERE id IN ({placeholders}) AND is_active = 1", candidate_ids)
            else:
                cursor.execute("SELECT * FROM candidates WHERE is_active = 1 ORDER BY match_score DESC LIMIT 100")
            
            rows = cursor.fetchall()
        
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
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT description, title, required_skills FROM job_descriptions WHERE id = ?", (job_description_id,))
            jd_row = cursor.fetchone()
            if not jd_row:
                raise HTTPException(404, f"Job description not found: {job_description_id}")
            
            job_text = f"{jd_row[1] or ''}\n{jd_row[0] or ''}\nRequired Skills: {jd_row[2] or ''}"
            
            # Resolve candidate
            cursor.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
            cand_row = cursor.fetchone()
        
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
async def connect_email_account(request: EmailConnectRequest, current_user: dict = Depends(require_auth)):
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
async def sync_email_applications(request: EmailSyncRequest, current_user: dict = Depends(require_auth)):
    """
    Sync and parse candidate applications from email
    Supports both OAuth2 (Microsoft Graph) and IMAP
    """
    try:
        sync_start_time = time.time()
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
            'processing_time': f"{(time.time() - sync_start_time):.2f}s"
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
        result = await graph_service.authenticate_with_credentials()
        
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
    # Prevent concurrent syncs
    if not hasattr(trigger_reset_and_reparse, '_lock'):
        trigger_reset_and_reparse._lock = asyncio.Lock()
    if trigger_reset_and_reparse._lock.locked():
        logger.info("Sync already in progress, skipping duplicate request")
        return
    await trigger_reset_and_reparse._lock.acquire()
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
                    except Exception:
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
        
        # Update last sync time on success (so sync-status shows it)
        global _last_email_sync_time
        _last_email_sync_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        try:
            await asyncio.to_thread(db_service.set_sync_metadata, 'last_email_sync_time', _last_email_sync_time)
        except Exception:
            pass  # non-critical
        
    except Exception as e:
        logger.error(f"Error in auto-triggered sync: {str(e)}")
    finally:
        if hasattr(trigger_reset_and_reparse, '_lock') and trigger_reset_and_reparse._lock.locked():
            trigger_reset_and_reparse._lock.release()

@app.get("/api/oauth2/callback")
async def oauth2_callback_get(code: str = None, error: str = None, error_description: str = None):
    """
    Handle OAuth2 GET redirect from Microsoft.
    Microsoft sends: GET /api/oauth2/callback?code=...&state=...
    We exchange the code for a token server-side and redirect to the frontend.
    """
    frontend_url = os.getenv('CORS_ORIGINS', 'http://localhost:5173').split(',')[0].strip()
    
    if error:
        logger.error(f"OAuth2 redirect error: {error} — {error_description}")
        return RedirectResponse(url=f"{frontend_url}/email?error={error}")
    
    if not code:
        return RedirectResponse(url=f"{frontend_url}/email?error=no_code")
    
    try:
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID', 'common')
        email_address = os.getenv('EMAIL_ADDRESS')
        redirect_uri = os.getenv('MICROSOFT_REDIRECT_URI', 'http://localhost:3000/auth/callback')
        
        if not all([client_id, client_secret, email_address]):
            return RedirectResponse(url=f"{frontend_url}/email?error=not_configured")
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id)
        result = await graph_service.authenticate(code, redirect_uri)
        
        if result['status'] == 'success':
            token_storage = get_token_storage()
            refresh_token = result.get('refresh_token')
            token_storage.save_token(
                email=email_address,
                access_token=result['access_token'],
                refresh_token=refresh_token,
                expires_in=result['expires_in'],
                auth_type='delegated'
            )
            logger.info(f"\u2705 OAuth2 token saved for {email_address} (GET callback)")
            return RedirectResponse(url=f"{frontend_url}/email?auth=success")
        else:
            error_msg = result.get('error', 'unknown')
            logger.error(f"OAuth2 token exchange failed: {error_msg}")
            return RedirectResponse(url=f"{frontend_url}/email?error=token_exchange_failed")
    except Exception as e:
        logger.error(f"OAuth2 GET callback error: {e}")
        return RedirectResponse(url=f"{frontend_url}/email?error=server_error")


@app.get("/api/email/oauth2/url")
async def get_oauth2_url_simple(request: Request = None):
    """
    Get Microsoft OAuth2 authorization URL using config from .env
    Simple endpoint - no parameters needed. Auto-detects production redirect URI.
    """
    try:
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID', 'common')
        # Use env var if set, otherwise auto-detect from FRONTEND_URL or default to production Firebase
        default_redirect = 'https://efforts-recruitment.web.app/auth/callback'
        if os.getenv('ENVIRONMENT') != 'production':
            default_redirect = 'http://localhost:3000/auth/callback'
        redirect_uri = os.getenv('MICROSOFT_REDIRECT_URI', os.getenv('OAUTH_REDIRECT_URI', default_redirect))
        
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
            
            # Notify oauth_automation_service to refresh its cached auth status
            if oauth_automation_service:
                try:
                    await oauth_automation_service.check_auth_status()
                    logger.info("✅ OAuth automation service notified of new token")
                except Exception as e:
                    logger.warning(f"⚠️ Could not notify OAuth automation service: {e}")
            
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
async def sync_emails_now(current_user: dict = Depends(require_auth)):
    """
    Trigger immediate email sync using saved OAuth2 token.
    Runs as background task. Requires --no-cpu-throttling on Cloud Run
    so the async task can execute after the HTTP response is sent.
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
        
        # Run sync in background (requires --no-cpu-throttling on Cloud Run)
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

@app.get("/api/email/sync-status")
async def get_sync_status():
    """
    Get current email sync status including last sync time and candidate count.
    All times are in UTC (ISO 8601 with Z suffix).
    Frontend can poll this to detect new candidates.
    """
    global _last_email_sync_time
    try:
        # If in-memory value is null, try loading from DB (handles cold starts)
        if not _last_email_sync_time:
            try:
                persisted = await asyncio.to_thread(db_service.get_sync_metadata, 'last_email_sync_time')
                if persisted:
                    _last_email_sync_time = persisted
            except Exception:
                pass
        
        candidate_count = await asyncio.to_thread(lambda: db_service.get_total_candidates())
        sync_interval = int(os.getenv('SYNC_INTERVAL_MINUTES', '30'))
        now_utc = datetime.utcnow()
        next_sync_utc = None
        if _last_email_sync_time:
            try:
                last_dt = datetime.fromisoformat(_last_email_sync_time.replace('Z', '+00:00'))
                next_dt = last_dt + timedelta(minutes=sync_interval)
                next_sync_utc = next_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            except Exception:
                next_sync_utc = (now_utc + timedelta(minutes=sync_interval)).strftime('%Y-%m-%dT%H:%M:%SZ')
        else:
            next_sync_utc = (now_utc + timedelta(minutes=sync_interval)).strftime('%Y-%m-%dT%H:%M:%SZ')
        return {
            'last_sync_time': _last_email_sync_time,
            'next_sync_time': next_sync_utc,
            'candidate_count': candidate_count,
            'sync_interval_minutes': sync_interval,
            'server_time_utc': now_utc.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'status': 'active'
        }
    except Exception as e:
        return {
            'last_sync_time': _last_email_sync_time,
            'candidate_count': 0,
            'sync_interval_minutes': int(os.getenv('SYNC_INTERVAL_MINUTES', '30')),
            'status': 'error',
            'error': str(e)
        }


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
            except Exception:
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
            # Validate clientState to prevent forged webhook calls
            expected_state = os.getenv('WEBHOOK_CLIENT_STATE', 'recruitment-tool-secret')
            if notification.get('clientState') != expected_state:
                logger.warning(f"Invalid clientState in webhook notification — ignoring")
                continue
            
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
async def connect_outlook(request: Request):
    """
    Connect Microsoft Outlook/Office 365 using Graph API
    Enterprise OAuth2 authentication
    """
    try:
        body = await request.json()
        graph_service = MicrosoftGraphService(
            client_id=body.get('client_id', ''),
            client_secret=body.get('client_secret', ''),
            tenant_id=body.get('tenant_id', '')
        )
        
        result = await graph_service.authenticate(
            authorization_code=body.get('authorization_code', ''),
            redirect_uri=body.get('redirect_uri', '')
        )
        
        return result
    except Exception as e:
        logger.error(f"Error connecting Outlook: {e}")
        raise HTTPException(500, "Error connecting Outlook")

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
                auth_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?client_id={client_id}&response_type=code&redirect_uri={os.getenv('MICROSOFT_REDIRECT_URI', os.getenv('OAUTH_REDIRECT_URI', 'http://localhost:3000/auth/callback'))}&scope=https://graph.microsoft.com/Mail.Read%20https://graph.microsoft.com/Mail.ReadWrite%20https://graph.microsoft.com/Mail.Send%20https://graph.microsoft.com/User.Read%20offline_access"
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
async def get_current_user(current_user: dict = Depends(require_auth)):
    """
    Get current user from JWT token
    
    - Validates Authorization header
    - Returns user data if token is valid
    """
    return {"user": current_user}

# User profile endpoints
@app.put("/api/users/profile")
async def update_profile(profile: UserProfile, current_user: dict = Depends(require_auth)):
    """
    Update user profile information
    """
    try:
        # Update profile
        updated_user = auth_service.update_profile(current_user['id'], {
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
async def update_password(password_update: PasswordUpdate, current_user: dict = Depends(require_auth)):
    """
    Update user password
    """
    try:
        # Change password
        auth_service.change_password(
            current_user['id'],
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
    status: str  # 'Shortlisted', 'Strong', 'Partial', 'Reject', 'Interviewing', 'Offered', 'Hired', 'Rejected'

async def _send_shortlist_email(candidate: Dict):
    """Send smart shortlist notification email to candidate via Microsoft Graph.
    Dynamically adapts content based on candidate location (UAE asks for visa details)."""
    try:
        candidate_email = candidate.get('email', '')
        candidate_name = candidate.get('name', 'Candidate')
        if not candidate_email:
            logger.warning(f"⚠️ Cannot send shortlist email - no email for {candidate_name}")
            return {'status': 'skipped', 'reason': 'no_email'}

        # Get OAuth token for sending email
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        sender_email = os.getenv('EMAIL_ADDRESS') or _settings.email_address or ''
        company_name = os.getenv('COMPANY_NAME', _settings.company_name) or 'Efforts Solutions'
        recruiter_name = os.getenv('RECRUITER_NAME', _settings.recruiter_name) or 'Recruitment Team'

        if not all([client_id, client_secret, tenant_id]):
            logger.warning("⚠️ Cannot send shortlist email - Microsoft Graph credentials not configured")
            return {'status': 'skipped', 'reason': 'no_credentials'}

        # Determine location context for dynamic email content
        candidate_location = (candidate.get('location', '') or '').lower()
        job_title = candidate.get('jobCategory', '') or candidate.get('job_category', '') or ''
        job_sub = candidate.get('jobSubcategory', '') or candidate.get('job_subcategory', '') or ''
        display_title = job_sub if job_sub else job_title

        # UAE / GCC locations → Abu Dhabi office; India/others → Chennai office
        uae_gcc_locations = ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'ras al khaimah',
                             'fujairah', 'umm al quwain', 'uae', 'united arab emirates',
                             'bahrain', 'kuwait', 'oman', 'qatar', 'saudi', 'riyadh',
                             'jeddah', 'dammam', 'muscat', 'doha', 'manama']
        is_uae = any(loc in candidate_location for loc in uae_gcc_locations)

        # Office assignment: GCC → Abu Dhabi, everyone else → Chennai
        work_office = 'Abu Dhabi office' if is_uae else 'Chennai office'

        # Build subject
        subject = f"Thank you for your interest - Efforts Solutions"
        if display_title:
            subject = f"Thank you for your interest in {display_title} - Efforts Solutions"

        # Build bullet points based on location
        details_requested = []
        details_requested.append("Your availability to join")
        if is_uae:
            details_requested.append("Your visa status")
        details_requested.append(f"Willingness to work from {work_office}")
        details_requested.append("Your current salary and expected salary for this role")

        bullets_html = "".join([f"<p style='margin: 4px 0 4px 10px;'>* {d}</p>" for d in details_requested])

        # Professional HTML email matching Efforts Solutions signature
        body_html = f"""
<div style="font-family: Calibri, 'Segoe UI', Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #000000; max-width: 650px;">
  <p style="margin: 0 0 12px 0;">Hi,</p>
  <p style="margin: 0 0 12px 0;">&nbsp;</p>
  <p style="margin: 0 0 12px 0;">Thank you for your interest. To proceed further, could you please share the following details with us:</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  {bullets_html}
  <p style="margin: 12px 0 4px 0;">&nbsp;</p>
  <p style="margin: 0 0 12px 0;">Look forward to your response.</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0; color: #808080;">Best Regards,</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0;"><strong>Shenaz Farhana</strong></p>
  <p style="margin: 0; color: #808080;">HR &amp; Admin Department</p>
  <p style="margin: 0; color: #808080;">Efforts Solutions IT, M12 Burooj Tower, Al Khalidhiya, Abu Dhabi, UAE</p>
  <p style="margin: 0; color: #808080;">T: +971 2 546 8880 | E: <a href="mailto:hr@effortz.com" style="color: #0563C1;">hr@effortz.com</a> | W: <a href="https://effortz.com" style="color: #0563C1;">effortz.com</a> | <a href="https://safeye.ai" style="color: #0563C1;">safeye.ai</a></p>
  <p style="margin: 6px 0 0 0;">
    <strong>ICV Certified</strong> &#9989; | <strong>ISO 9001</strong> &#9989; | <strong>ISO/IEC 27001</strong> &#9989; | <strong>ISO 45001</strong> &#9989; | <strong>MCC Approved</strong> &#9989;
  </p>
  <p style="margin: 4px 0 0 0; font-size: 12px; color: #808080;">Enterprise Solutions | IT Outsourcing | Digital Solutions | ICT/ELV Managed Services | AI/ML/IOT/ERP/ECM/BPM/OCR/RPA</p>
  <hr style="border: none; border-top: 2px solid #1a3c6e; margin: 12px 0 8px 0;" />
  <p style="margin: 0; font-size: 10px; color: #999999;">This email and its contents are confidential and intended solely for the recipient. Sharing this message without the sender's written consent is strictly prohibited. If received in error, please reply and delete it immediately.</p>
</div>"""

        # Setup Graph service and authenticate
        graph = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=sender_email)
        token_storage = get_token_storage()
        token_data = token_storage.get_token(sender_email)

        authenticated = False

        if token_data and token_data.get('access_token'):
            # Check if token is expired and try to refresh
            if token_data.get('is_expired') and token_data.get('refresh_token'):
                logger.info("🔄 Token expired, refreshing before sending email...")
                refresh_result = await graph.refresh_access_token(token_data['refresh_token'])
                if refresh_result['status'] == 'success':
                    token_storage.save_token(
                        email=sender_email,
                        access_token=refresh_result['access_token'],
                        refresh_token=refresh_result.get('refresh_token', token_data['refresh_token']),
                        expires_in=refresh_result['expires_in'],
                        auth_type='delegated'
                    )
                    token_data = token_storage.get_token(sender_email)
                    logger.info("✅ Token refreshed for email sending")
                else:
                    logger.warning(f"⚠️ Delegated token refresh failed: {refresh_result.get('error')}")
            if token_data and token_data.get('access_token') and not token_data.get('is_expired'):
                graph.access_token = token_data['access_token']
                graph.auth_type = token_data.get('auth_type', 'delegated')
                graph.token_expiry = datetime.now() + timedelta(hours=1)
                authenticated = True

        # Fallback: Use application credentials (client_credentials flow)
        # This works when Mail.Send APPLICATION permission is granted in Azure AD
        if not authenticated:
            logger.warning("🔑 No delegated token, trying application credentials (Mail.Send app permission)...")
            try:
                app_result = await graph.authenticate_with_credentials()
                if app_result.get('status') == 'success':
                    logger.warning(f"✅ Authenticated via app credentials for {sender_email}")
                    authenticated = True
                else:
                    logger.warning(f"⚠️ App credentials auth failed: {app_result.get('error')}")
            except Exception as app_err:
                logger.warning(f"⚠️ App credentials auth error: {app_err}")

        if not authenticated:
            logger.warning("⚠️ No auth method available for sending email. Check delegated token or app permissions.")
            return {'status': 'failed', 'reason': 'no_token'}

        # Send the email
        result = await graph.send_mail(
            to_email=candidate_email,
            subject=subject,
            body=body_html,
            content_type='HTML'
        )

        if result.get('status') == 'success':
            logger.warning(f"✅ Shortlist email sent to {candidate_name} ({candidate_email}) [UAE={is_uae}]")
        else:
            logger.warning(f"⚠️ Failed to send shortlist email to {candidate_email}: {result.get('message')}")

        return result

    except Exception as e:
        logger.error(f"❌ Error sending shortlist email: {str(e)}")
        return {'status': 'error', 'message': str(e)}

@app.put("/api/candidates/{candidate_id}/status")
async def update_candidate_status(candidate_id: str, status_update: CandidateStatusUpdate, background_tasks: BackgroundTasks, current_user: dict = Depends(require_auth)):
    """
    Update candidate status (shortlist, reject, etc.)
    When status is 'Shortlisted', automatically sends a notification email to the candidate.
    Cache is invalidated so the candidates list is always fresh.
    """
    try:
        # Persist status in database
        updated = await asyncio.to_thread(
            db_service.update_candidate_status,
            candidate_id,
            status_update.status
        )

        if not updated:
            raise HTTPException(404, f"Candidate {candidate_id} not found")

        # Invalidate candidate cache so list endpoint returns fresh data
        response_cache.clear()

        email_result = None

        # Auto-send email when candidate is shortlisted (non-blocking background task)
        if status_update.status.lower() in ('shortlisted', 'shortlist'):
            candidate = await asyncio.to_thread(db_service.get_candidate_by_id, candidate_id)
            if candidate:
                # Send email in background so the API responds immediately
                background_tasks.add_task(_send_shortlist_email, candidate)
                email_result = {'status': 'queued', 'message': 'Shortlist email queued for sending'}
            else:
                email_result = {'status': 'skipped', 'reason': 'candidate_not_found'}

        return {
            'status': 'success',
            'message': f'Candidate {candidate_id} status updated to {status_update.status}',
            'candidate_id': candidate_id,
            'new_status': status_update.status,
            'email_sent': email_result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Status update error: {str(e)}")
        raise HTTPException(500, f"Error updating candidate status: {str(e)}")


# ============================================================================
# TEST EMAIL SENDING
# ============================================================================

@app.post("/api/email/test-send")
async def test_email_send(current_user: dict = Depends(require_auth)):
    """
    Test email sending via Microsoft Graph.
    Sends a test email from hr@effortz.com to the logged-in user's email.
    Tests both delegated and application credential flows.
    """
    try:
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        sender_email = os.getenv('EMAIL_ADDRESS') or _settings.email_address or ''
        recipient = current_user.get('email', sender_email)

        if not all([client_id, client_secret, tenant_id]):
            return {'status': 'error', 'message': 'Microsoft Graph credentials not configured'}

        graph = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=sender_email)
        auth_method = 'none'

        # Try delegated token first
        token_storage = get_token_storage()
        token_data = token_storage.get_token(sender_email)

        if token_data and token_data.get('access_token'):
            if token_data.get('is_expired') and token_data.get('refresh_token'):
                refresh_result = await graph.refresh_access_token(token_data['refresh_token'])
                if refresh_result['status'] == 'success':
                    token_storage.save_token(
                        email=sender_email,
                        access_token=refresh_result['access_token'],
                        refresh_token=refresh_result.get('refresh_token', token_data['refresh_token']),
                        expires_in=refresh_result['expires_in'],
                        auth_type='delegated'
                    )
                    token_data = token_storage.get_token(sender_email)
            if token_data and token_data.get('access_token') and not token_data.get('is_expired'):
                graph.access_token = token_data['access_token']
                graph.auth_type = token_data.get('auth_type', 'delegated')
                graph.token_expiry = datetime.now() + timedelta(hours=1)
                auth_method = 'delegated'

        # Fallback to app credentials
        if auth_method == 'none':
            app_result = await graph.authenticate_with_credentials()
            if app_result.get('status') == 'success':
                auth_method = 'application'
            else:
                return {
                    'status': 'error',
                    'message': f'Both delegated and app auth failed. App error: {app_result.get("error")}',
                    'sender_email': sender_email,
                    'auth_method': 'none'
                }

        # Send test email
        test_body = f"""
<div style="font-family: 'Segoe UI', Arial, sans-serif; padding: 20px; max-width: 500px;">
  <h2 style="color: #172554;">✅ Email Test Successful</h2>
  <p>This test email was sent from <strong>{sender_email}</strong> using <strong>{auth_method}</strong> authentication.</p>
  <p style="color: #6b7280; font-size: 12px;">Sent by Efforts Solutions Recruitment Platform</p>
</div>"""

        result = await graph.send_mail(
            to_email=recipient,
            subject=f"[Test] Efforts Recruitment Email Test - {auth_method}",
            body=test_body,
            content_type='HTML'
        )

        return {
            'status': result.get('status'),
            'auth_method': auth_method,
            'sender': sender_email,
            'recipient': recipient,
            'message': result.get('message', '')
        }

    except Exception as e:
        logger.error(f"Test email error: {e}")
        return {'status': 'error', 'message': str(e)}


# ============================================================================
# BULK SHORTLIST + AI EMAIL
# ============================================================================

class BulkShortlistRequest(BaseModel):
    candidate_ids: List[str]
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    send_emails: bool = True

class GenerateEmailRequest(BaseModel):
    candidate_ids: List[str]
    job_title: Optional[str] = None
    tone: Optional[str] = "professional"
    custom_instructions: Optional[str] = None


@app.post("/api/ai/generate-shortlist-email")
async def generate_shortlist_email_template(
    request: GenerateEmailRequest,
    current_user: dict = Depends(require_auth)
):
    """
    Use AI to generate a customizable shortlist notification email.
    Returns subject + body that the user can edit before sending.
    """
    try:
        # Get candidate info for context
        candidates = []
        for cid in request.candidate_ids[:10]:
            c = await asyncio.to_thread(db_service.get_candidate_by_id, cid)
            if c:
                candidates.append(c)

        company_name = os.getenv('COMPANY_NAME', _settings.company_name)
        recruiter_name = os.getenv('RECRUITER_NAME', _settings.recruiter_name)
        job_title = request.job_title or (candidates[0].get('jobCategory', 'the open position') if candidates else 'the open position')

        # Try LLM to generate a tailored email
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                custom_note = f"\nAdditional instructions: {request.custom_instructions}" if request.custom_instructions else ""
                prompt = f"""Generate a professional shortlist notification email for a recruitment process.

Context:
- Company: {company_name}
- Recruiter: {recruiter_name}
- Position: {job_title}
- Tone: {request.tone}
- Number of candidates being notified: {len(request.candidate_ids)}
{custom_note}

The email should:
- Congratulate the candidate on being shortlisted
- Mention the position/role
- Explain next steps briefly
- Be warm but professional
- Use {{{{candidate_name}}}} as placeholder for the candidate's name
- Use {{{{company_name}}}} as placeholder for company name
- Use {{{{job_title}}}} as placeholder for the job title
- Use {{{{recruiter_name}}}} as placeholder for recruiter name

Return JSON:
{{
    "subject": "Email subject line",
    "body": "Full email body text with placeholders"
}}"""
                result = await llm_svc._generate_json(prompt, temperature=0.4)
                if result and result.get('subject') and result.get('body'):
                    return {
                        "status": "success",
                        "subject": result['subject'],
                        "body": result['body'],
                        "placeholders": ["candidate_name", "company_name", "job_title", "recruiter_name"],
                        "source": "ai_generated",
                        "company_name": company_name,
                        "recruiter_name": recruiter_name,
                        "job_title": job_title,
                    }
        except Exception as llm_err:
            logger.warning(f"LLM email generation failed: {llm_err}")

        # Fallback: use the default template
        templates_svc = get_templates_service()
        template = templates_svc.get_template('shortlist_notification')
        return {
            "status": "success",
            "subject": template['subject'].replace('{{company_name}}', company_name),
            "body": template['body'],
            "placeholders": ["candidate_name", "company_name", "job_title", "recruiter_name"],
            "source": "default_template",
            "company_name": company_name,
            "recruiter_name": recruiter_name,
            "job_title": job_title,
        }
    except Exception as e:
        logger.error(f"Email template generation error: {e}")
        raise HTTPException(500, f"Error generating email template: {str(e)}")


@app.post("/api/candidates/bulk-shortlist")
async def bulk_shortlist_candidates(
    request: BulkShortlistRequest,
    current_user: dict = Depends(require_auth)
):
    """
    Bulk shortlist candidates and send personalized notification emails.
    Reuses the same rich _send_shortlist_email() pipeline as single shortlist,
    so every candidate gets a fully branded, location-aware, personalized email.
    """
    try:
        results = []
        shortlisted = 0
        emails_sent = 0
        emails_failed = 0

        for cid in request.candidate_ids:
            try:
                # Update status
                updated = await asyncio.to_thread(
                    db_service.update_candidate_status, cid, 'Shortlisted'
                )
                if not updated:
                    results.append({'candidate_id': cid, 'status': 'not_found'})
                    continue

                shortlisted += 1

                # Send personalized email using the same rich pipeline as single shortlist
                if request.send_emails:
                    candidate = await asyncio.to_thread(db_service.get_candidate_by_id, cid)
                    if not candidate or not candidate.get('email'):
                        results.append({
                            'candidate_id': cid,
                            'name': (candidate or {}).get('name', 'Unknown'),
                            'status': 'shortlisted',
                            'email': 'no_email'
                        })
                        continue

                    candidate_name = candidate.get('name', 'Candidate')

                    try:
                        # Use the same rich, branded, location-aware email as single shortlist
                        email_result = await _send_shortlist_email(candidate)
                        email_status = email_result.get('status', 'error') if email_result else 'error'

                        if email_status == 'success':
                            emails_sent += 1
                            results.append({'candidate_id': cid, 'name': candidate_name, 'status': 'shortlisted', 'email': 'sent'})
                        elif email_status == 'skipped':
                            results.append({'candidate_id': cid, 'name': candidate_name, 'status': 'shortlisted', 'email': email_result.get('reason', 'skipped')})
                        else:
                            emails_failed += 1
                            results.append({'candidate_id': cid, 'name': candidate_name, 'status': 'shortlisted', 'email': 'failed'})
                    except Exception as email_err:
                        emails_failed += 1
                        logger.warning(f"Email send error for {candidate_name}: {email_err}")
                        results.append({'candidate_id': cid, 'name': candidate_name, 'status': 'shortlisted', 'email': 'error'})
                else:
                    results.append({'candidate_id': cid, 'status': 'shortlisted', 'email': 'not_requested'})

            except Exception as e:
                logger.warning(f"Bulk shortlist error for {cid}: {e}")
                results.append({'candidate_id': cid, 'status': 'error', 'message': str(e)})

        # Invalidate cache after all updates
        response_cache.clear()

        return {
            'status': 'success',
            'total': len(request.candidate_ids),
            'shortlisted': shortlisted,
            'emails_sent': emails_sent,
            'emails_failed': emails_failed,
            'results': results
        }
    except Exception as e:
        logger.error(f"Bulk shortlist error: {e}")
        raise HTTPException(500, f"Error in bulk shortlist: {str(e)}")


# NOTE: Resume download route is defined earlier (line ~953) with proper database query

# AI Chat endpoints
class ChatMessage(BaseModel):
    message: str
    context: Optional[str] = None

class AnalyzeMatchRequest(BaseModel):
    candidate: dict
    job_description: dict

# Global thread pool for AI operations (reusable, efficient)
from concurrent.futures import ThreadPoolExecutor
_ai_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai_worker")

# Request deduplication: prevent multiple concurrent LLM calls for the same candidate
_analysis_in_progress: dict = {}  # candidate_id -> asyncio.Event


@app.get("/api/candidates/{candidate_id}/ai-analysis")
async def get_candidate_ai_analysis(candidate_id: str, refresh: bool = False):
    """
    Get or generate detailed AI analysis for a candidate.
    Returns comprehensive paragraph-style assessment with pros, cons,
    executive summary, career trajectory, and hiring recommendation.
    Results are persisted in DB for fast retrieval.
    """
    try:
        # Check if we already have a stored analysis (unless refresh requested)
        if not refresh:
            stored = await asyncio.to_thread(db_service.get_ai_analysis, candidate_id)
            if stored and stored.get('executive_summary'):
                stored['from_cache'] = True
                return stored
        
        # Request deduplication: if another request is already generating analysis
        # for this candidate, wait for it to complete instead of running a second LLM call
        if candidate_id in _analysis_in_progress:
            logger.info(f"⏳ Waiting for in-progress analysis for {candidate_id}")
            try:
                await asyncio.wait_for(_analysis_in_progress[candidate_id].wait(), timeout=65)
                stored = await asyncio.to_thread(db_service.get_ai_analysis, candidate_id)
                if stored and stored.get('executive_summary'):
                    stored['from_cache'] = True
                    return stored
            except asyncio.TimeoutError:
                pass  # Fall through to generate
        
        # Mark this candidate as in-progress
        event = asyncio.Event()
        _analysis_in_progress[candidate_id] = event
        
        try:
            return await _run_candidate_analysis(candidate_id, refresh)
        finally:
            event.set()
            _analysis_in_progress.pop(candidate_id, None)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI analysis error for {candidate_id}: {e}")
        raise HTTPException(500, f"Error generating AI analysis: {str(e)}")


async def _run_candidate_analysis(candidate_id: str, refresh: bool = False):
    """Internal: actually run the LLM analysis for a candidate."""
    try:
        # Get full candidate data
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
            row = cursor.fetchone()
        
        if not row:
            raise HTTPException(404, "Candidate not found")
        
        candidate = db_service._row_to_candidate(row)
        
        # Also try to get resume text for richer analysis
        resume_text = candidate.get('resume_text', '') or ''
        if not resume_text:
            try:
                resume_data = await asyncio.to_thread(db_service.get_resume, candidate_id)
                if resume_data and resume_data.get('file_data'):
                    parsed = await resume_parser.parse_resume(resume_data['file_data'], resume_data['filename'])
                    resume_text = parsed.get('raw_text', '') if parsed else ''
            except Exception:
                pass
        
        # Build enriched candidate data for analysis
        candidate_for_analysis = {
            'name': candidate.get('name', 'Unknown'),
            'email': candidate.get('email', ''),
            'location': candidate.get('location', ''),
            'skills': candidate.get('skills', []),
            'experience': candidate.get('experience', 0),
            'education': candidate.get('education', []),
            'work_history': candidate.get('workHistory', []),
            'summary': candidate.get('summary', ''),
            'matchScore': candidate.get('matchScore', 0),
            'job_category': candidate.get('jobCategory', candidate.get('job_category', 'General')),
            'job_subcategory': candidate.get('jobSubcategory', candidate.get('job_subcategory', '')),
        }
        
        # If we have resume text, add it for richer context
        if resume_text:
            candidate_for_analysis['resume_text'] = resume_text[:4000]
        
        # TIER 1: Try LLM deep analysis
        analysis = None
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                analysis = await asyncio.wait_for(
                    llm_svc.analyze_candidate_deep(candidate_for_analysis),
                    timeout=AI_ANALYSIS_TIMEOUT
                )
                if analysis:
                    analysis['source'] = 'local_llm'
        except asyncio.TimeoutError:
            logger.warning(f"LLM deep analysis timeout for {candidate_id}")
        except Exception as llm_err:
            logger.warning(f"LLM deep analysis error: {llm_err}")
        
        # TIER 2: Try OpenAI (only if explicitly enabled)
        if not analysis and USE_OPENAI_FALLBACK:
            try:
                openai_svc = get_openai_service()
                if openai_svc:
                    analysis = openai_svc.deep_analyze_candidate(candidate_for_analysis)
                    if analysis:
                        analysis['source'] = 'openai'
            except Exception as oai_err:
                logger.warning(f"OpenAI deep analysis error: {oai_err}")
        
        # TIER 3: Fallback — use candidate data to build a meaningful report
        # Rating/recommendation derived from matchScore for consistency
        if not analysis:
            skills = candidate_for_analysis.get('skills', [])
            exp = candidate_for_analysis.get('experience', 0)
            name = candidate_for_analysis.get('name', 'Unknown')
            match_score = candidate_for_analysis.get('match_score', 50)
            location = candidate_for_analysis.get('location', '')
            
            # Derive rating from actual match score so PDF values are consistent
            if match_score >= 85:
                fb_rating, fb_rec, fb_conf = 'A', 'STRONGLY_RECOMMEND', 85
            elif match_score >= 75:
                fb_rating, fb_rec, fb_conf = 'A-', 'RECOMMEND', 78
            elif match_score >= 65:
                fb_rating, fb_rec, fb_conf = 'B+', 'RECOMMEND', 72
            elif match_score >= 55:
                fb_rating, fb_rec, fb_conf = 'B', 'CONSIDER', 65
            elif match_score >= 45:
                fb_rating, fb_rec, fb_conf = 'B-', 'CONSIDER', 58
            elif match_score >= 35:
                fb_rating, fb_rec, fb_conf = 'C+', 'REVIEW', 50
            else:
                fb_rating, fb_rec, fb_conf = 'C', 'REVIEW', 42
            
            # Build profile-relevant pros/cons
            fb_pros = []
            if exp > 0:
                fb_pros.append(f'Brings {exp} years of domain experience')
            if len(skills) > 5:
                fb_pros.append(f'Well-rounded skill set with {len(skills)} competencies')
            elif len(skills) > 0:
                fb_pros.append(f'Focused expertise in {", ".join(skills[:3])}')
            if match_score >= 70:
                fb_pros.append('Strong overall match score for the target role')
            fb_pros.append('Profile is complete and in active pipeline')
            
            fb_cons = []
            if len(skills) < 5:
                fb_cons.append('Limited skills breadth — expanding technical portfolio recommended')
            if exp < 3:
                fb_cons.append('Early career stage — may need mentorship and onboarding support')
            if not location or location in ('Not Specified', 'Unknown', ''):
                fb_cons.append('Location not specified — remote/relocation flexibility should be verified')
            if match_score < 60:
                fb_cons.append('Below-average match score — verify alignment with role requirements')
            if not fb_cons:
                fb_cons.append('Profile appears strong overall — detailed AI review recommended for deeper insights')
            
            analysis = {
                'executive_summary': f'{name} is a professional with {exp} years of experience specializing in {", ".join(skills[:5]) if skills else "their field"}. With a match score of {match_score}%, they {"show strong alignment" if match_score >= 70 else "show moderate alignment" if match_score >= 50 else "may need further evaluation"} for the target role. Based on the available profile data, {"they are a strong candidate" if match_score >= 70 else "they are worth considering" if match_score >= 50 else "additional screening is recommended"}.',
                'technical_assessment': f'The candidate lists {len(skills)} technical skills including {", ".join(skills[:8]) if skills else "unspecified technologies"}. The breadth of their technical stack suggests {"a well-rounded professional" if len(skills) > 5 else "a focused specialist"} capable of contributing to relevant projects.',
                'experience_assessment': f'With {exp} years of professional experience, {name} {"demonstrates significant industry tenure" if exp > 5 else "is building their career foundation"}. Further details about career progression should be explored in interview.',
                'education_assessment': 'Educational credentials are listed in their profile. Verification of qualifications is recommended during the screening process.',
                'pros': fb_pros,
                'cons': fb_cons,
                'career_trajectory': f'Based on {exp} years of experience, the candidate appears to be at a {"senior" if exp > 7 else "mid" if exp > 3 else "junior"}-level career stage.',
                'ideal_roles': [candidate_for_analysis.get('job_category', 'General')],
                'interview_focus_areas': ['Technical depth verification', 'Cultural alignment', 'Career motivation'],
                'hiring_recommendation': fb_rec,
                'hiring_recommendation_rationale': f'Based on a {match_score}% match score with {exp} years of experience and {len(skills)} listed skills. {"Strong candidate for interview." if match_score >= 70 else "Worth considering with targeted interview questions." if match_score >= 50 else "Additional screening recommended before interview."}',
                'confidence_score': fb_conf,
                'overall_rating': fb_rating,
                'source': 'fallback',
            }
        
        # Persist analysis to database
        await asyncio.to_thread(db_service.save_ai_analysis, candidate_id, analysis)
        analysis['from_cache'] = False
        
        return analysis
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI analysis error for {candidate_id}: {e}")
        raise HTTPException(500, f"Error generating AI analysis: {str(e)}")


@app.post("/api/ai/analyze-match")
async def analyze_match(request: AnalyzeMatchRequest, current_user: dict = Depends(require_auth)):
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
            # Try OpenAI before rule-based
            if fallback_service:
                try:
                    result = fallback_service.analyze_candidate_match(
                        request.candidate,
                        request.job_description
                    )
                    result['source'] = 'openai_fallback'
                    logger.info("✅ OpenAI fallback analysis completed")
                except Exception:
                    result = _quick_fallback_analysis(request.candidate, request.job_description)
                    result['source'] = 'fallback_timeout'
            else:
                result = _quick_fallback_analysis(request.candidate, request.job_description)
                result['source'] = 'fallback_timeout'
                
        except Exception as local_error:
            logger.warning(f"⚠️ Local AI error: {local_error}")
            # Try OpenAI before rule-based
            if fallback_service:
                try:
                    result = fallback_service.analyze_candidate_match(
                        request.candidate,
                        request.job_description
                    )
                    result['source'] = 'openai_fallback'
                    logger.info("✅ OpenAI fallback analysis completed")
                except Exception:
                    result = _quick_fallback_analysis(request.candidate, request.job_description)
                    result['source'] = 'fallback_error'
            else:
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
async def generate_interview_questions(request: InterviewQuestionsRequest, current_user: dict = Depends(require_auth)):
    """
    Generate AI-powered interview questions
    3-TIER FALLBACK: Local AI → OpenAI → Rule-based
    """
    try:
        # TIER 1: Try Local AI first (FREE)
        try:
            questions = ai_service.generate_interview_questions(
                request.candidate,
                request.job_description,
                request.num_questions
            )
            if questions:
                return {"questions": questions, "source": "local_ai"}
        except Exception as local_error:
            logger.warning(f"⚠️ Local AI interview questions failed: {local_error}")
        
        # TIER 2: OpenAI fallback
        if fallback_service:
            try:
                questions = fallback_service.generate_interview_questions(
                    request.candidate,
                    request.job_description,
                    request.num_questions
                )
                if questions:
                    return {"questions": questions, "source": "openai_fallback"}
            except Exception as openai_err:
                logger.warning(f"⚠️ OpenAI interview questions failed: {openai_err}")
        
        # TIER 3: Rule-based fallback
        candidate_skills = request.candidate.get('skills', [])
        job_title = request.job_description.get('title', 'the position')
        default_questions = [
            f"Tell me about your experience that's most relevant to {job_title}.",
            f"How do you stay current with developments in your field?",
            f"Describe a challenging project you worked on and how you overcame obstacles.",
            f"What interests you most about this role?",
            f"Where do you see yourself in the next 3-5 years?"
        ]
        if candidate_skills:
            skill_q = f"Can you describe your experience with {', '.join(candidate_skills[:3])}?"
            default_questions[1] = skill_q
        
        return {
            "questions": default_questions[:request.num_questions],
            "source": "rule_based",
            "note": "Configure Ollama or OpenAI for AI-generated interview questions"
        }
    except Exception as e:
        raise HTTPException(500, f"Error generating questions: {str(e)}")

class SummarizeResumeRequest(BaseModel):
    resume_text: str

@app.post("/api/ai/summarize-resume")
async def summarize_resume(request: SummarizeResumeRequest, current_user: dict = Depends(require_auth)):
    """
    Generate AI summary of resume
    3-TIER FALLBACK: Local AI → OpenAI → Rule-based
    """
    try:
        # TIER 1: Try Local AI first (FREE)
        try:
            summary = ai_service.summarize_resume(request.resume_text)
            if summary:
                return {"summary": summary, "source": "local_ai"}
        except Exception as local_error:
            logger.warning(f"⚠️ Local AI summarize failed: {local_error}")
        
        # TIER 2: OpenAI fallback
        if fallback_service:
            try:
                summary = fallback_service.summarize_resume(request.resume_text)
                if summary:
                    return {"summary": summary, "source": "openai_fallback"}
            except Exception as openai_err:
                logger.warning(f"⚠️ OpenAI summarize failed: {openai_err}")
        
        # TIER 3: Rule-based fallback
        text = request.resume_text[:500]
        return {
            "summary": f"Resume summary (basic extraction): {text}...",
            "source": "rule_based",
            "note": "Configure Ollama or OpenAI for AI-powered summaries"
        }
    except Exception as e:
        raise HTTPException(500, f"Error summarizing resume: {str(e)}")

@app.post("/api/ai/batch-analyze")
async def batch_analyze_new_candidates(job_id: str = "general", batch_size: int = 50, current_user: dict = Depends(require_auth)):
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
        "ai_tier_mode": _settings.ai_tier_mode,
        "ai_tier_order": _settings.ai_tier_order,
        "environment": "production" if _settings.is_production else "development",
        "primary_engine": _determine_primary_engine(llm_status),
        "fallback_engine": "openai" if fallback_service else "keyword",
        "gemini": {
            "available": gemini_service.available if gemini_service else False,
            "model": gemini_service.model_name if gemini_service else None,
            "requests_processed": gemini_service._request_count if gemini_service else 0,
            "avg_response_time": round(gemini_service._total_time / max(gemini_service._request_count, 1), 2) if gemini_service else 0,
            "error_count": gemini_service._error_count if gemini_service else 0,
            "cache_size": len(gemini_service._cache) if gemini_service else 0,
        },
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
            "gemini": len(gemini_service._cache) if gemini_service else 0,
        },
        "model": _determine_model_description(llm_status),
        "fallback_model": openai_service.model if fallback_service else None,
        "message": _determine_ai_message(llm_status),
        "caching_enabled": True,
        "concurrent_processing": True,
        "max_concurrent": "100+ requests",
        "cost": _determine_cost_info(llm_status),
        "fallback_available": fallback_service is not None,
        "gemini_available": gemini_service.available if gemini_service else False,
        "setup_instructions": {
            "gemini": "Set GEMINI_API_KEY env var. Get key from https://aistudio.google.com/apikey",
            "ollama": "Install from https://ollama.com/download then run: ollama pull qwen2.5:7b",
            "models_recommended": ["qwen2.5:7b (extraction)", "phi3.5 (fast)", "llama3.1:8b (reasoning)"]
        }
    }


def _determine_primary_engine(llm_status: Dict) -> str:
    """Determine which AI engine is currently primary based on tier order and availability."""
    tier = _settings.ai_tier_order
    for engine in tier:
        if engine == "gemini" and gemini_service and gemini_service.available:
            return "gemini"
        if engine == "ollama" and llm_status.get('available'):
            return "ollama_llm"
        if engine == "openai" and fallback_service:
            return "openai"
    return "local_ai"


def _determine_model_description(llm_status: Dict) -> str:
    """Dynamic model description based on what's available."""
    parts = []
    if gemini_service and gemini_service.available:
        parts.append(f"Gemini ({gemini_service.model_name})")
    if llm_status.get('available'):
        parts.append(f"Ollama ({llm_status.get('primary_model', 'local')})")
    if fallback_service:
        parts.append(f"OpenAI ({openai_service.model})")
    parts.extend(["Sentence-Transformers", "SpaCy NER", "Keyword"])
    return "Multi-Tier AI: " + " → ".join(parts)


def _determine_ai_message(llm_status: Dict) -> str:
    """Generate AI status message based on current configuration."""
    primary = _determine_primary_engine(llm_status)
    if primary == "gemini":
        return f"🌟 AI Stack: Gemini {gemini_service.model_name} (primary) + Local Embeddings + NER"
    elif primary == "ollama_llm":
        return "🤖 AI Stack: Local LLM + Embeddings + NER (FREE) with Gemini/OpenAI fallback"
    elif primary == "openai":
        return f"💳 AI Stack: OpenAI {openai_service.model} (primary) — costs apply"
    return "⚡ AI Stack: Sentence-Transformers + SpaCy NER + Keyword (FREE, no LLM)"


def _determine_cost_info(llm_status: Dict) -> str:
    """Generate cost information string."""
    primary = _determine_primary_engine(llm_status)
    if primary == "gemini":
        return "~$0.01-0.05/day (Gemini 2.0 Flash is very low cost)"
    elif primary == "ollama_llm":
        return "$0 (all local, Gemini/OpenAI fallback charges only if local AI fails)"
    elif primary == "openai":
        return "~$0.01-0.10/request (OpenAI pricing applies)"
    return "$0 (all local, no API costs)"

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

# ===========================================================================
# JOB TAXONOMY ENDPOINTS
# ===========================================================================

@app.get("/api/taxonomy")
async def get_job_taxonomy():
    """Get the full hierarchical job taxonomy (categories → subcategories)"""
    from services.job_taxonomy import get_all_categories_with_subcategories, ALL_CATEGORIES
    return {
        "categories": ALL_CATEGORIES,
        "taxonomy": get_all_categories_with_subcategories(),
    }

@app.get("/api/taxonomy/{category}/subcategories")
async def get_subcategories(category: str):
    """Get subcategories for a specific category"""
    from services.job_taxonomy import get_subcategories as _get_subs
    subs = _get_subs(category)
    if not subs:
        raise HTTPException(404, f"Category '{category}' not found")
    return {"category": category, "subcategories": subs}

@app.post("/api/taxonomy/classify")
async def classify_title(title: str = Body(..., embed=True)):
    """Classify a free-text job title into category + subcategory"""
    from services.job_taxonomy import classify_job_title
    cat, sub = classify_job_title(title)
    return {"title": title, "category": cat, "subcategory": sub}

# ===========================================================================
# AI SMART SEARCH — LLM-powered candidate search
# ===========================================================================

def _format_search_results(raw_results: list, candidates: list) -> list:
    """Normalize search results into {candidate, relevance_score, match_reasons} format.
    Deduplicates by candidate ID — keeps the first (highest-ranked) occurrence."""
    formatted = []
    seen_ids = set()  # Track seen candidate IDs to prevent duplicates
    for item in raw_results:
        if isinstance(item, dict):
            # Extract candidate ID for dedup check
            cand_id = None
            if 'candidate' in item and isinstance(item['candidate'], dict):
                cand_id = str(item['candidate'].get('id', ''))
            elif 'id' in item:
                cand_id = str(item['id'])
            elif 'candidate_id' in item:
                cand_id = str(item['candidate_id'])

            # Skip if we've already seen this candidate
            if cand_id and cand_id in seen_ids:
                continue

            # If it already has the expected shape
            if 'candidate' in item and 'relevance_score' in item:
                formatted.append(item)
                if cand_id: seen_ids.add(cand_id)
            # rank_candidates_for_job format: {candidate, match, score}
            elif 'candidate' in item and 'score' in item:
                match_data = item.get('match', {})
                formatted.append({
                    "candidate": item['candidate'],
                    "relevance_score": int(item['score']),
                    "match_reasons": match_data.get('strengths', match_data.get('matched_skills', ["AI matched"])),
                    "matched_skills": match_data.get('matched_skills', []),
                    "missing_skills": match_data.get('missing_skills', []),
                    "recommendation": match_data.get('recommendation', ''),
                })
                if cand_id: seen_ids.add(cand_id)
            # If it's a raw candidate dict with a score field
            elif 'id' in item or 'name' in item:
                score = item.get('score', item.get('match_score', item.get('matchScore', 50)))
                formatted.append({
                    "candidate": item,
                    "relevance_score": score,
                    "match_reasons": item.get('match_reasons', item.get('key_strengths', ["AI matched"]))
                })
                if cand_id: seen_ids.add(cand_id)
            # LLM ranking format: {candidate_id, score, ...}
            elif 'candidate_id' in item:
                cand = next((c for c in candidates if str(c.get('id')) == str(item['candidate_id'])), None)
                if cand:
                    formatted.append({
                        "candidate": cand,
                        "relevance_score": int(item.get('score', item.get('job_fit_score', 50))),
                        "match_reasons": item.get('match_reasons', item.get('key_strengths', ["AI matched"]))
                    })
                    if cand_id: seen_ids.add(cand_id)
    return formatted

@app.post("/api/ai/smart-search")
async def ai_smart_search(
    query: str = Body(..., embed=True),
    top_n: int = Body(20, embed=True),
    current_user: dict = Depends(require_auth)
):
    """
    LLM-powered smart search: takes a natural language query and returns
    the best-matching candidates using semantic understanding.
    Scans the ENTIRE database using efficient pre-filtering.
    """
    try:
        # 1. Get ALL active candidates (lightweight for AI matching)
        candidates = await asyncio.to_thread(
            db_service.get_candidates_lightweight, {}, 10000
        )
        if not candidates:
            return {"results": [], "total": 0, "query": query, "message": "No candidates in database"}

        # 2. Try Gemini-based matching first (cost-effective, always available)
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                ranked = await gemini_svc.rank_candidates_for_job(candidates, query, top_n)
                if ranked:
                    formatted = _format_search_results(ranked, candidates)
                    return {
                        "results": formatted,
                        "total_searched": len(candidates),
                        "query": query,
                        "source": "gemini",
                        "message": f"Found {len(formatted)} matches using Gemini AI search"
                    }
        except Exception as gemini_err:
            logger.warning(f"Gemini smart search failed: {gemini_err}")

        # 3. Try Local LLM matching (for local dev)
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                ranked = await llm_svc.rank_candidates_for_job(candidates, query, top_n)
                formatted = _format_search_results(ranked, candidates)
                return {
                    "results": formatted,
                    "total_searched": len(candidates),
                    "query": query,
                    "source": "local_llm",
                    "message": f"Found {len(formatted)} matches using AI search"
                }
        except Exception as llm_err:
            logger.warning(f"LLM smart search failed: {llm_err}")

        # 4. Try matching engine (semantic / TF-IDF)
        try:
            matching_engine = MatchingEngine()
            results = await matching_engine.match_candidates(query, candidates, top_n)
            formatted = _format_search_results(results, candidates)
            return {
                "results": formatted,
                "total_searched": len(candidates),
                "query": query,
                "source": "semantic",
                "message": f"Found {len(formatted)} matches using semantic search"
            }
        except Exception as sem_err:
            logger.warning(f"Semantic search failed: {sem_err}")

        # 5. OpenAI fallback
        openai_svc = get_openai_service()
        if openai_svc:
            result = openai_svc.match_candidates_to_job(query, candidates, top_n)
            raw = result.get("rankings", [])
            formatted = _format_search_results(raw, candidates)
            return {
                "results": formatted,
                "total_searched": len(candidates),
                "query": query,
                "source": "openai",
                "message": "Used OpenAI for search"
            }

        # 6. Basic keyword fallback
        q_lower = query.lower()
        scored = []
        for c in candidates:
            score = 0
            match_reasons = []
            skills = c.get('skills', [])
            for s in skills:
                if s.lower() in q_lower or q_lower in s.lower():
                    score += 15
                    match_reasons.append(f"Skill: {s}")
            if str(c.get('summary', '')).lower().find(q_lower) >= 0:
                score += 10
                match_reasons.append("Summary match")
            if str(c.get('jobCategory', '')).lower() in q_lower:
                score += 10
                match_reasons.append(f"Category: {c.get('jobCategory', '')}")
            scored.append({
                "candidate": c,
                "relevance_score": min(score, 100),
                "match_reasons": match_reasons or ["Keyword match"]
            })

        scored.sort(key=lambda x: x['relevance_score'], reverse=True)
        return {
            "results": scored[:top_n],
            "total_searched": len(candidates),
            "query": query,
            "source": "keyword",
            "message": "Used basic keyword matching"
        }
    except Exception as e:
        logger.error(f"Smart search error: {e}")
        raise HTTPException(500, str(e))

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
