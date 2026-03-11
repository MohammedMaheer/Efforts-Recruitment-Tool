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
import hmac
from dotenv import load_dotenv
import logging
from contextlib import asynccontextmanager
from cachetools import TTLCache
import threading
import time

from services.resume_parser import ResumeParser, is_spaced_text, collapse_spaced_chars, text_quality_score
from services.matching_engine import MatchingEngine
from services.email_parser import EmailParser
from services.microsoft_graph import MicrosoftGraphService
from services.token_storage import get_token_storage
from services.local_ai_service import get_local_ai_service
from services.gemini_service import get_gemini_service
from services.email_scraper import get_scraper_service
from services.database_service import get_db_service
from services.oauth_automation_service import get_oauth_automation, OAuthAutomationService
from services.auth_service import get_auth_service
from services.db_repair import audit_database, repair_database, quick_health_check
from models.candidate import Candidate, JobDescription, MatchResult
from core.config import get_settings
from core.db_wrapper import IS_POSTGRES
from core.dependencies import require_auth, optional_auth, require_admin

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

# Configure logging with structured format
logging.basicConfig(
    level=logging.INFO if not _settings.is_production else logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Performance: Response cache (5 minutes TTL) with thread-safe lock
response_cache = TTLCache(maxsize=1000, ttl=300)
_cache_lock = threading.Lock()

# Background email sync task
background_sync_task = None
oauth_automation_service: OAuthAutomationService = None

# Strong reference set for background tasks — prevents GC from collecting them
_persistent_tasks: set = set()

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
    # On startup, always look back at least 7 days to catch emails missed during downtime/deploys
    try:
        persisted_time = await asyncio.to_thread(db_service.get_sync_metadata, 'last_email_sync_time')
        if persisted_time:
            min_lookback = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
            if persisted_time > min_lookback:
                _last_email_sync_time = min_lookback
                logger.info(f"Startup lookback: using 7-day window ({min_lookback}) instead of persisted ({persisted_time})")
            else:
                _last_email_sync_time = persisted_time
                logger.info(f"Restored last sync time from DB: {_last_email_sync_time}")
    except Exception as e:
        logger.warning(f"Could not load sync metadata: {e}")
    
    # Startup: clear orphaned processing entries (candidates lost during GCS restore)
    # This ensures the auto_sync loop can re-process emails whose candidates were lost
    try:
        orphaned = await asyncio.to_thread(db_service.clear_orphaned_processing_entries)
        if orphaned > 0:
            logger.warning(f"Startup: cleared {orphaned} orphaned processing entries (candidates lost during restore)")
        blocked = await asyncio.to_thread(db_service.clear_all_blocked_entries)
        if blocked > 0:
            logger.warning(f"Startup: cleared {blocked} blocked/failed entries for retry")
    except Exception as e:
        logger.warning(f"Startup orphan clearing failed (non-fatal): {e}")
    
    while True:
        try:
            logger.info("🔄 Auto-sync: Starting email sync...")
            
            # Get OAuth2 configuration
            client_id = os.getenv('MICROSOFT_CLIENT_ID')
            client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
            tenant_id = os.getenv('MICROSOFT_TENANT_ID')
            primary_email = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
            
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
                        
                        # PRIORITY 2: No refresh token — try app credentials first, then prompt user
                        if not auth_success and not refresh_token:
                            logger.warning("No refresh token available. Trying app credentials (Mail.Read)...")
                            try:
                                cred_result = await graph_service.authenticate_with_credentials()
                                if cred_result.get('status') == 'success':
                                    auth_success = True
                                    logger.warning("Authenticated via APP CREDENTIALS for email sync")
                                    token_data = {
                                        'access_token': cred_result['access_token'],
                                        'auth_type': 'application',
                                        'is_expired': False,
                                        'expires_at_dt': datetime.now() + timedelta(seconds=cred_result.get('expires_in', 3600))
                                    }
                            except Exception as e:
                                logger.debug(f"App credentials auth fallback failed: {e}")
                            
                            if not auth_success:
                                logger.info("=" * 60)
                                logger.info("EMAIL SYNC REQUIRES ONE-TIME MICROSOFT OAUTH LOGIN")
                                logger.info("=" * 60)
                                logger.info(f"   1. Open: {os.getenv('CORS_ORIGINS', 'https://efforts-recruitment.web.app')}")
                                logger.info("   2. Go to: Settings > Email Integration")
                                logger.info("   3. Click: Connect Microsoft Account")
                                logger.info("=" * 60)
                        elif not auth_success and refresh_token:
                            # Refresh failed — try application credentials as fallback
                            # This requires Mail.Read application permission on Azure app
                            logger.warning(f"Refresh token failed for {primary_email}. Trying app credentials fallback...")
                            try:
                                cred_result = await graph_service.authenticate_with_credentials()
                                if cred_result.get('status') == 'success':
                                    auth_success = True
                                    logger.warning(f"Authenticated via APP CREDENTIALS for email sync (Mail.Read permission)")
                                    # Update token_data so the check below passes
                                    token_data = {
                                        'access_token': cred_result['access_token'],
                                        'auth_type': 'application',
                                        'is_expired': False,
                                        'expires_at_dt': datetime.now() + timedelta(seconds=cred_result.get('expires_in', 3600))
                                    }
                                else:
                                    logger.warning(f"App credentials auth failed: {cred_result.get('error', 'unknown')}")
                                    logger.info("Re-authenticate: Settings > Email Integration > Connect Microsoft Account")
                            except Exception as cred_err:
                                logger.warning(f"App credentials fallback error: {cred_err}")
                                logger.info("Re-authenticate: Settings > Email Integration > Connect Microsoft Account")
                    
                    # Use the token if we have a valid one
                    if token_data and token_data.get('access_token') and not token_data.get('is_expired', True):
                        logger.info(f"🔐 Using OAuth2 ({token_data.get('auth_type', 'unknown')}) for {primary_email}...")
                        
                        graph_service.access_token = token_data['access_token']
                        graph_service.auth_type = token_data.get('auth_type', 'delegated')
                        graph_service.token_expiry = token_data.get('expires_at_dt', datetime.now() + timedelta(hours=1))
                        
                        # ===== MEMORY-EFFICIENT PAGED SYNC =====
                        # Instead of loading ALL emails into memory (caused OOM with 2Gi),
                        # we stream pages of 50 emails, process each page, then discard.
                        # Dedup via email_processing_log ensures no duplicates.
                        
                        processed_count_before = await asyncio.to_thread(
                            lambda: db_service.get_processed_email_count()
                        )
                        
                        logger.info(f"📧 Starting paged email sync (already processed: {processed_count_before})...")
                        
                        # Record sync start time
                        sync_start_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
                        
                        new_count = 0
                        total_fetched = 0
                        consecutive_all_seen = 0  # Stop early if 3+ pages all already processed

                        async def process_graph_message(msg):
                            """Process a single Graph API email message into a candidate."""
                            nonlocal new_count
                            try:
                                # ------ DEDUP: skip emails already processed ------
                                msg_id = msg.get('id', '') or msg.get('internetMessageId', '')
                                if not msg_id:
                                    # Generate stable dedup key from sender + subject
                                    import hashlib
                                    dedup_input = f"{msg.get('from', {}).get('emailAddress', {}).get('address', '')}{msg.get('subject', '')}"
                                    msg_id = f"gen_{hashlib.sha256(dedup_input.encode()).hexdigest()[:16]}"
                                if await asyncio.to_thread(db_service.is_email_processed, msg_id):
                                    return 'seen'  # already handled — skip entirely

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
                                
                                # PRE-FILTER: skip obvious non-candidate emails BEFORE extraction
                                # This saves Gemini API calls and processing time
                                _pre_subj_lower = subject.lower() if subject else ''
                                _pre_sender_lower = sender_name.lower().strip() if sender_name else ''
                                _pre_email_lower = sender_email.lower() if sender_email else ''
                                
                                # Skip Indeed/LinkedIn employer notifications (not candidate applications)
                                _notification_patterns = [
                                    # Employer/recruiter notifications
                                    r'^your\s+job[,:]',
                                    r'you\s+have\s+\d+\s+new\s+applicants',
                                    r'^your\s+sponsored\s+job',
                                    r'^your\s+posting',
                                    r'job\s+performance\s+report',
                                    r'^hiring\s+insights',
                                    r'^budget\s+alert',
                                    r'^find\s+your\s+next\s+star',
                                    r'^your\s+jobs\s+are\s+on',
                                    # Account & security
                                    r'^confirm\s+your\s+account',
                                    r'^welcome\s+to\s+microsoft',
                                    r'^password\s+reset',
                                    r'^verify\s+your\s+email',
                                    r'^sign.in\s+activity',
                                    r'^security\s+alert',
                                    r'^unusual\s+sign.in',
                                    # Billing & transactional
                                    r'^your\s+invoice',
                                    r'^your\s+subscription',
                                    r'^payment\s+received',
                                    r'^billing\s+statement',
                                    r'^receipt\s+for\s+your',
                                    r'^order\s+confirm',
                                    # Delivery & system
                                    r'^undeliverable:',
                                    r'wants\s+to\s+access',
                                    # Marketing & promotional
                                    r'^weekly\s+digest',
                                    r'^monthly\s+roundup',
                                    r'^your\s+weekly',
                                    r'limited\s+time\s+offer',
                                    r'^upgrade\s+your\s+plan',
                                    r'^trial\s+expir',
                                    r'^your\s+free\s+trial',
                                    r'^special\s+offer',
                                    r'^act\s+now',
                                    r'^don.t\s+miss\s+out',
                                ]
                                if any(re.search(p, _pre_subj_lower) for p in _notification_patterns):
                                    return 'no-candidate'
                                
                                # Skip emails from system senders — but WHITELIST job board
                                # noreply addresses that forward real candidate applications
                                _job_board_domains = [
                                    'indeed.com', 'linkedin.com', 'glassdoor.com', 'ziprecruiter.com',
                                    'naukri.com', 'bayt.com', 'gulftalent.com', 'monster.com',
                                    'careerbuilder.com', 'dice.com', 'reed.co.uk', 'seek.com',
                                ]
                                _is_job_board = any(d in _pre_email_lower for d in _job_board_domains)
                                if not _is_job_board:
                                    _system_senders = ['noreply', 'no-reply', 'postmaster', 'mailer-daemon',
                                                       'notifications', 'system', 'donotreply', 'do-not-reply']
                                    if any(s in _pre_email_lower for s in _system_senders):
                                        return 'no-candidate'
                                
                                # Extract candidate
                                candidate = await scraper_service.extract_candidate_from_email(email_data)
                                if not candidate or not candidate.get('email'):
                                    return 'no-candidate'
                                
                                # Smart filter: block Indeed relay / junk / system emails
                                if db_service.is_blocked_email(candidate['email']):
                                    logger.debug(f"🚫 Blocked Indeed relay candidate: {candidate['email'][:50]}")
                                    if msg_id:
                                        try:
                                            await asyncio.to_thread(
                                                db_service.mark_email_processed,
                                                msg_id, '', 'blocked-indeed-relay'
                                            )
                                        except Exception as e:
                                            logger.debug(f"Non-critical: mark_email_processed failed for indeed relay: {e}")
                                    return 'blocked'

                                # Block system/noreply emails and obviously bad candidates
                                _email_lower = candidate['email'].lower()
                                _name_lower = (candidate.get('name', '') or '').lower().strip()
                                _BLOCKED_EMAIL_PATTERNS = [
                                    'noreply', 'no-reply', 'no_reply', 'donotreply', 'do-not-reply',
                                    'mailer-daemon', 'postmaster', 'notifications@', 'notification@',
                                    'messages-noreply', 'alert@', 'alerts@', 'system@', 'bounce@',
                                    'auto-confirm', 'autoconfirm', 'feedback@noreply',
                                ]
                                _BLOCKED_NAMES = [
                                    'unknown', 'messages', 'notification', 'noreply', 'no reply',
                                    'system', 'admin', 'administrator', 'postmaster', 'mailer-daemon',
                                    'indeed', 'linkedin', 'glassdoor', 'monster', 'info', 'support',
                                    'test', 'null', 'none', 'n/a', 'na', '',
                                    'lusha', 'maestrorecruiter', 'maestro recruiter', 'recruiter',
                                    'hiring manager', 'hiring team', 'dear sir', 'dear madam',
                                    'candidate', 'applicant', 'resume', 'cv',
                                    'naukri', 'bayt', 'gulftalent', 'ziprecruiter', 'careerbuilder',
                                    'jobstreet', 'seek', 'reed', 'totaljobs', 'cwjobs',
                                    'user', 'guest', 'subscriber', 'member',
                                ]
                                if any(pat in _email_lower for pat in _BLOCKED_EMAIL_PATTERNS):
                                    logger.debug(f"🚫 Blocked system/noreply email: {candidate['email'][:50]}")
                                    if msg_id:
                                        try:
                                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, '', 'blocked-system-email')
                                        except Exception as e:
                                            logger.debug(f"Non-critical: mark_email_processed failed for system email: {e}")
                                    return 'blocked'

                                if _name_lower in _BLOCKED_NAMES or len(_name_lower) < 2:
                                    logger.debug(f"🚫 Blocked trash candidate name: '{candidate.get('name')}'")
                                    if msg_id:
                                        try:
                                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, '', 'blocked-bad-name')
                                        except Exception as e:
                                            logger.debug(f"Non-critical: mark_email_processed failed for bad name: {e}")
                                    return 'blocked'
                                
                                # Check if candidate already exists in DB
                                existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])
                                
                                needs_ai = False
                                if not existing:
                                    needs_ai = True
                                    new_count += 1
                                else:
                                    candidate = db_service.smart_merge_candidate(existing, candidate)
                                    existing_score = existing.get('matchScore') or existing.get('match_score') or 0
                                    # Re-run AI if: no analysis, or score is 0 (unscored)
                                    if (not existing.get('ai_analysis')
                                            or existing_score <= 0):
                                        needs_ai = True
                                
                                # AI processing
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
                                            # Smart merge: keep the RICHER data (more skills, higher experience)
                                            ai_skills = ai_analysis.get('skills', []) or []
                                            existing_skills = candidate.get('skills', []) or []
                                            merged_skills = ai_skills if len(ai_skills) >= len(existing_skills) else existing_skills
                                            ai_exp = ai_analysis.get('experience', 0) or 0
                                            existing_exp = candidate.get('experience', 0) or 0
                                            merged_exp = max(ai_exp, existing_exp)
                                            # Use AI-extracted name if current name looks like email-derived garbage
                                            curr_name = candidate.get('name', '')
                                            ai_name = ai_analysis.get('name', '')
                                            if ai_name and (not curr_name or curr_name.lower() == 'unknown' or '.' in curr_name.split()[0] if curr_name else True):
                                                candidate['name'] = ai_name
                                            # Validate AI summary isn't garbage email text
                                            from services.email_scraper import sanitize_summary
                                            ai_summary = ai_analysis.get('summary', '')
                                            existing_summary = candidate.get('summary', '')
                                            final_summary = sanitize_summary(ai_summary, candidate) or sanitize_summary(existing_summary, candidate) or ''
                                            candidate.update({
                                                'job_category': ai_analysis.get('job_category', 'General'),
                                                'job_subcategory': ai_analysis.get('job_subcategory', ''),
                                                'matchScore': ai_analysis.get('quality_score'),
                                                'summary': final_summary,
                                                'skills': merged_skills,
                                                'experience': merged_exp,
                                                'education': ai_analysis.get('education', []) or candidate.get('education', []),
                                                'phone': ai_analysis.get('phone', '') or candidate.get('phone', ''),
                                                'location': ai_analysis.get('location', '') or candidate.get('location', ''),
                                                'linkedin': ai_analysis.get('linkedin', '') or candidate.get('linkedin', ''),
                                                'certifications': ai_analysis.get('certifications', []),
                                                'languages': ai_analysis.get('languages', []),
                                                'work_history': ai_analysis.get('work_history', []),
                                            })
                                            score = ai_analysis.get('quality_score')
                                            candidate['status'] = 'Strong' if score >= 70 else ('Partial' if score >= 40 else 'Reject')
                                            logger.info(f"✅ AI scored {candidate.get('name')}: {score}%")
                                    except Exception as ai_err:
                                        logger.warning(f"AI analysis failed ({type(ai_err).__name__}): {str(ai_err)[:100]}")
                                        skills = candidate.get('skills', [])
                                        exp = candidate.get('experience', 0) or 0
                                        has_edu = bool(candidate.get('education'))
                                        has_certs = bool(candidate.get('certifications'))
                                        has_summary = bool(candidate.get('summary', '').strip())
                                        if skills or exp:
                                            fallback_score = 25.0
                                            fallback_score += min(30, len(skills) * 3)
                                            fallback_score += min(25, exp * 3)
                                            fallback_score += 10 if has_edu else 0
                                            fallback_score += 5 if has_certs else 0
                                            fallback_score += 3 if has_summary else 0
                                            candidate['matchScore'] = min(90, max(15, round(fallback_score, 1)))
                                            logger.info(f"📊 Fallback score for {candidate.get('name')}: {candidate['matchScore']}% (from {len(skills)} skills, {exp}yr exp)")
                                        else:
                                            candidate['matchScore'] = 20  # Clearly indicates needs AI reprocessing
                                
                                # If AI didn't run and matchScore is 0, calculate a basic fallback
                                if not candidate.get('matchScore'):
                                    skills = candidate.get('skills', [])
                                    exp = candidate.get('experience', 0) or 0
                                    has_edu = bool(candidate.get('education'))
                                    has_summary = bool(candidate.get('summary', '').strip())
                                    if skills or exp:
                                        fs = 20.0 + min(30, len(skills) * 3) + min(25, exp * 3) + (10 if has_edu else 0) + (3 if has_summary else 0)
                                        candidate['matchScore'] = min(85, max(10, round(fs, 1)))
                                    else:
                                        candidate['matchScore'] = 15  # Minimal info, needs AI reprocessing

                                # Save resume file if present
                                resume_file = candidate.pop('resume_file_data', None)
                                resume_filename = candidate.pop('resume_filename', None)
                                
                                # Save to database
                                if existing:
                                    await asyncio.to_thread(db_service.update_candidate, candidate)
                                else:
                                    await asyncio.to_thread(db_service.insert_candidate, candidate)
                                
                                if resume_file and resume_filename:
                                    try:
                                        content_type = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                                        await asyncio.to_thread(db_service.save_resume, candidate['id'], resume_filename, resume_file, content_type)
                                    except Exception as e:
                                        logger.warning(f"Failed to save resume for {candidate.get('id', 'unknown')}: {e}")

                                # Mark email as processed
                                if msg_id:
                                    action = 'updated' if existing else 'inserted'
                                    try:
                                        await asyncio.to_thread(
                                            db_service.mark_email_processed,
                                            msg_id,
                                            candidate.get('id', ''),
                                            action
                                        )
                                    except Exception as e:
                                        logger.debug(f"Non-critical: mark_email_processed failed: {e}")

                                # Save AI analysis
                                if needs_ai and analysis_text and len(analysis_text) > 20:
                                    try:
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
                                    except Exception as e:
                                        logger.warning(f"Failed to save AI analysis for {candidate.get('id', 'unknown')}: {e}")
                                    
                                return 'new'
                            except Exception as e:
                                logger.warning(f"Error processing message: {str(e)[:100]}")
                                return 'error'

                        # ===== STREAM PAGES: fetch → process → discard =====
                        # Use receivedDateTime filter for incremental sync (huge perf boost)
                        filter_query = None
                        sync_max_pages = 200  # First sync: 200×50 = 10,000 emails
                        if _last_email_sync_time:
                            filter_query = f"receivedDateTime ge {_last_email_sync_time}"
                            sync_max_pages = 60  # Incremental: 60×50 = 3,000 recent
                            logger.info(f"📧 Incremental sync: emails since {_last_email_sync_time}")
                        else:
                            logger.info("📧 First sync: fetching ALL inbox emails")

                        try:
                            import gc
                            import psutil
                            page_count = 0
                            async for page in graph_service.get_messages_paged(
                                folder='inbox',
                                filter_query=filter_query,
                                page_size=50,
                                max_pages=sync_max_pages
                            ):
                                page_count += 1
                                page_size_actual = len(page)
                                total_fetched += page_size_actual

                                # Process each message in the page sequentially
                                page_seen = 0
                                page_new = 0
                                for msg in page:
                                    try:
                                        result = await process_graph_message(msg)
                                        if result == 'seen':
                                            page_seen += 1
                                        elif result == 'new':
                                            page_new += 1
                                    except Exception as msg_err:
                                        logger.warning(f"Message processing crashed: {str(msg_err)[:100]}")
                                        continue

                                # Memory safety: GC every 5 pages, abort if memory > 85%
                                if page_count % 5 == 0:
                                    gc.collect()
                                    try:
                                        try:
                                            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes') as f:
                                                _usage = int(f.read().strip())
                                            with open('/sys/fs/cgroup/memory/memory.limit_in_bytes') as f:
                                                _limit = int(f.read().strip())
                                            mem_pct = (_usage / _limit) * 100
                                        except FileNotFoundError:
                                            try:
                                                with open('/sys/fs/cgroup/memory.current') as f:
                                                    _usage = int(f.read().strip())
                                                with open('/sys/fs/cgroup/memory.max') as f:
                                                    _limit = int(f.read().strip())
                                                mem_pct = (_usage / _limit) * 100
                                            except Exception:
                                                mem_pct = psutil.virtual_memory().percent
                                        if mem_pct > 85:
                                            logger.error(f"Container memory at {mem_pct:.1f}% - stopping sync to prevent OOM")
                                            break
                                    except Exception as e:
                                        logger.debug(f"Non-critical: memory check failed: {e}")

                                # If ALL messages in this page were already processed,
                                # increment consecutive counter. Since emails are sorted
                                # newest-first, 3 consecutive all-seen pages means we've
                                # reached emails we've already fully processed.
                                if page_seen == page_size_actual:
                                    consecutive_all_seen += 1
                                    logger.info(f"📧 Page fully seen ({consecutive_all_seen}/3) — {page_seen}/{page_size_actual} already processed")
                                    if consecutive_all_seen >= 3:
                                        logger.info(f"⏭️ Stopping early: 3 consecutive pages all already processed")
                                        break
                                else:
                                    consecutive_all_seen = 0  # Reset — found new emails
                                    logger.info(f"📧 Page processed: {page_size_actual - page_seen} new, {page_seen} seen")

                            # Sync complete
                            total_processed_after = await asyncio.to_thread(lambda: db_service.get_processed_email_count())
                            newly_processed = total_processed_after - processed_count_before
                            logger.warning(f"OAuth2 sync: {primary_email} - {total_fetched} fetched, {new_count} new candidates, {newly_processed} newly processed")
                            oauth2_success = True
                            _last_email_sync_time = sync_start_time
                            try:
                                await asyncio.to_thread(db_service.set_sync_metadata, 'last_email_sync_time', sync_start_time)
                            except Exception as e:
                                logger.debug(f"Non-critical: set_sync_metadata failed: {e}")
                            # Clear cache so new candidates appear immediately
                            if new_count > 0:
                                response_cache.clear()
                                logger.info(f"🧹 Cache cleared after adding {new_count} new candidates")

                        except Exception as fetch_err:
                            error_msg = str(fetch_err)
                            logger.warning(f"OAuth2 paged fetch failed: {error_msg}")

                            if '403' in error_msg and token_data.get('auth_type') == 'application':
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
                                if token_data.get('auth_type') == 'application' and not token_data.get('refresh_token'):
                                    token_storage.delete_token(primary_email)
                            elif '400' in error_msg or '404' in error_msg:
                                logger.info("=" * 70)
                                logger.info("📋 EMAIL SYNC REQUIRES MICROSOFT OAUTH LOGIN")
                                logger.info("=" * 70)
                                logger.info("")
                                logger.info("The email address may not be an Azure AD mailbox.")
                                logger.info("To sync emails, complete the ONE-TIME Microsoft OAuth login:")
                                logger.info(f"   1. Open: {os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')[0]}")
                                logger.info("   2. Go to: Settings → Email Integration")
                                logger.info("   3. Click: Connect Microsoft Account")
                                logger.info("   4. Sign in with your Microsoft/Outlook account")
                                logger.info("   → After this ONE-TIME login, auto-refresh works FOREVER")
                                logger.info("=" * 70)
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
                                                score = ai_analysis.get('quality_score')
                                                # Validate AI summary isn't garbage email text  
                                                from services.email_scraper import sanitize_summary
                                                ai_summary_2 = ai_analysis.get('summary', '')
                                                existing_summary_2 = candidate.get('summary', '')
                                                final_summary_2 = sanitize_summary(ai_summary_2, candidate) or sanitize_summary(existing_summary_2, candidate) or ''
                                                # Email-parsed values take priority over LLM for contact info
                                                candidate.update({
                                                    'job_category': normalize_category_backend(ai_analysis.get('job_category', 'General')),
                                                    'matchScore': score,
                                                    'summary': final_summary_2,
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
                                        exp = candidate.get('experience', 0) or 0
                                        has_edu = bool(candidate.get('education'))
                                        has_certs = bool(candidate.get('certifications'))
                                        has_summary = bool(candidate.get('summary', '').strip())
                                        if skills or exp:
                                            fallback_score = 25.0
                                            fallback_score += min(30, len(skills) * 3)
                                            fallback_score += min(25, exp * 3)
                                            fallback_score += 10 if has_edu else 0
                                            fallback_score += 5 if has_certs else 0
                                            fallback_score += 3 if has_summary else 0
                                            candidate['matchScore'] = min(90, max(15, round(fallback_score, 1)))
                                        else:
                                            candidate['matchScore'] = 20
                                    except Exception as ai_err:
                                        logger.warning(f"AI error ({type(ai_err).__name__}): {str(ai_err)[:100]}")
                                        skills = candidate.get('skills', [])
                                        exp = candidate.get('experience', 0) or 0
                                        has_edu = bool(candidate.get('education'))
                                        has_certs = bool(candidate.get('certifications'))
                                        has_summary = bool(candidate.get('summary', '').strip())
                                        if skills or exp:
                                            fallback_score = 25.0
                                            fallback_score += min(30, len(skills) * 3)
                                            fallback_score += min(25, exp * 3)
                                            fallback_score += 10 if has_edu else 0
                                            fallback_score += 5 if has_certs else 0
                                            fallback_score += 3 if has_summary else 0
                                            candidate['matchScore'] = min(90, max(15, round(fallback_score, 1)))
                                        else:
                                            candidate['matchScore'] = 20
                                
                                # If AI didn't run and matchScore is 0, calculate a basic fallback
                                if not candidate.get('matchScore'):
                                    skills = candidate.get('skills', [])
                                    exp = candidate.get('experience', 0) or 0
                                    has_edu = bool(candidate.get('education'))
                                    has_summary = bool(candidate.get('summary', '').strip())
                                    if skills or exp:
                                        fs = 20.0 + min(30, len(skills) * 3) + min(25, exp * 3) + (10 if has_edu else 0) + (3 if has_summary else 0)
                                        candidate['matchScore'] = min(85, max(10, round(fs, 1)))
                                    else:
                                        candidate['matchScore'] = 15

                                # Save resume file if present (mirror Graph API path)
                                resume_file = candidate.pop('resume_file_data', None)
                                resume_filename = candidate.pop('resume_filename', None)
                                
                                # Save to database
                                if existing:
                                    await asyncio.to_thread(db_service.update_candidate, candidate)
                                else:
                                    await asyncio.to_thread(db_service.insert_candidate, candidate)
                                
                                # Save resume binary for download
                                if resume_file and resume_filename:
                                    try:
                                        content_type = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                                        await asyncio.to_thread(db_service.save_resume, candidate['id'], resume_filename, resume_file, content_type)
                                    except Exception as e:
                                        logger.warning(f"Failed to save resume for {candidate.get('id', 'unknown')}: {e}")
                                
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
                                    except Exception as e:
                                        logger.debug(f"Non-critical: mark_email_processed failed: {e}")  # non-critical
                                        
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
                        
                        # Clear the response cache so new candidates appear immediately
                        if new_count > 0:
                            response_cache.clear()
                            logger.info(f"🧹 Cache cleared after adding {new_count} new candidates")
                        
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
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "efforts-recruitment-ai-data")
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
    if IS_POSTGRES:
        print("💾 GCS restore: Skipped (using PostgreSQL)", flush=True)
        return False
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
                    except Exception as e:
                        logger.warning(f"Failed to close DB connection: {e}")
        except Exception as e:
            logger.debug(f"Non-critical: pool cleanup during GCS restore: {e}")
        
        # Remove stale WAL/SHM files
        for suffix in ['-wal', '-shm']:
            wal_path = LOCAL_DB_PATH + suffix
            if os.path.exists(wal_path):
                try:
                    os.remove(wal_path)
                except Exception as e:
                    logger.debug(f"Non-critical: failed to remove {wal_path}: {e}")
        
        # Remove existing DB file before download
        if os.path.exists(LOCAL_DB_PATH):
            try:
                os.remove(LOCAL_DB_PATH)
            except Exception as e:
                logger.warning(f"Failed to remove existing DB file: {e}")
        
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
    if IS_POSTGRES:
        return False  # PostgreSQL handles its own persistence
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
        except Exception as e:
            logger.debug(f"Non-critical: failed to clean old GCS snapshots: {e}")
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
    if IS_POSTGRES:
        print("💾 [BG] JSON seed skipped (using PostgreSQL)", flush=True)
        return
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


async def _background_process_candidates(interval_minutes: int = 240):
    """
    Background task that periodically processes unprocessed candidates.
    - Checks for candidates with missing ai_analysis or low match_score
    - Processes them in small batches using Gemini (thinking disabled for cost savings)
    - Runs every 4 hours to catch stragglers from email sync
    """
    await asyncio.sleep(180)  # Wait 3 min for startup + model loading to complete
    
    while True:
        try:
            loop = asyncio.get_event_loop()
            
            # Find candidates needing processing — use db_service context manager
            def _get_unprocessed():
                with db_service.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, name, skills, experience, education, location, 
                               match_score, job_category, summary, resume_text
                        FROM candidates 
                        WHERE is_active = 1 
                        AND (
                            ai_analysis IS NULL OR ai_analysis = '' 
                            OR match_score = 0 OR match_score IS NULL
                            OR (match_score <= 35 AND (job_category = 'General' OR job_category IS NULL OR job_category = ''))
                            OR (match_score = 35 AND (skills IS NULL OR skills = '' OR skills = '[]' OR skills = '["R"]'))
                        )
                        ORDER BY created_at DESC
                        LIMIT 5
                    """)
                    rows = [dict(r) for r in cursor.fetchall()]
                    return rows
            
            unprocessed = await loop.run_in_executor(None, _get_unprocessed)
            
            if unprocessed:
                logger.info(f"🔄 [BG-Process] Found {len(unprocessed)} unprocessed candidates, starting batch AI analysis...")
                
                ai_service = None
                try:
                    gemini_svc = get_gemini_service()
                    if gemini_svc and gemini_svc.available:
                        ai_service = gemini_svc
                except Exception as _gemini_err:
                    logger.debug(f"[BG-Process] Gemini init failed: {_gemini_err}")
                
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
                                    import json as _json
                                    with db_service.get_connection() as conn:
                                        # Safely extract score — handle None, missing keys, quality_score alias
                                        raw_score = ai_result.get('match_score') or ai_result.get('quality_score') or ai_result.get('overall_score')
                                        try:
                                            match_score = int(float(raw_score)) if raw_score is not None else 0
                                        except (ValueError, TypeError):
                                            match_score = 0
                                        # Calculate fallback from AI-extracted data if score is 0
                                        if match_score <= 0:
                                            ai_skills = ai_result.get('skills', [])
                                            ai_exp = ai_result.get('experience', 0) or 0
                                            if isinstance(ai_exp, str):
                                                try: ai_exp = int(float(ai_exp))
                                                except: ai_exp = 0
                                            has_edu = bool(ai_result.get('education'))
                                            has_certs = bool(ai_result.get('certifications'))
                                            has_summary = bool(ai_result.get('summary', '').strip())
                                            match_score = 25 + min(30, len(ai_skills) * 3) + min(25, ai_exp * 3) + (10 if has_edu else 0) + (5 if has_certs else 0) + (3 if has_summary else 0)
                                            match_score = min(90, max(15, match_score))
                                        job_category = ai_result.get('job_category', ai_result.get('category', '')) or ''
                                        
                                        # Extract skills and experience from AI result for better data
                                        ai_skills = ai_result.get('skills', [])
                                        if isinstance(ai_skills, list) and ai_skills:
                                            skills_json = _json.dumps(ai_skills)
                                        else:
                                            skills_json = None
                                        ai_experience = ai_result.get('experience', 0)
                                        try:
                                            ai_experience = int(float(ai_experience)) if ai_experience else 0
                                        except (ValueError, TypeError):
                                            ai_experience = 0
                                        ai_summary = ai_result.get('summary', '') or ''
                                        
                                        conn.execute("""
                                            UPDATE candidates 
                                            SET ai_analysis = ?, 
                                                match_score = CASE WHEN ? > 0 THEN ? ELSE match_score END,
                                                job_category = CASE WHEN ? != '' THEN ? ELSE job_category END,
                                                skills = CASE WHEN ? IS NOT NULL AND length(?) > 4 THEN ? ELSE skills END,
                                                experience = CASE WHEN ? > 0 THEN MAX(experience, ?) ELSE experience END,
                                                summary = CASE WHEN ? != '' AND length(?) > length(COALESCE(summary, '')) THEN ? ELSE summary END,
                                                last_updated = datetime('now')
                                            WHERE id = ?
                                        """, [_json.dumps(ai_result), match_score, match_score, 
                                              job_category, job_category,
                                              skills_json, skills_json, skills_json,
                                              ai_experience, ai_experience,
                                              ai_summary, ai_summary, ai_summary,
                                              cid])
                                        conn.commit()
                                
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
                        except Exception as _backup_err:
                            logger.warning(f"[BG-Process] GCS backup failed: {_backup_err}")
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
                    except Exception as e:
                        logger.warning(f"Failed to close stale DB connection: {e}")
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
        # Seed admin user from env vars (never hardcode credentials)
        try:
            with _auth_svc._get_connection() as _conn:
                _cur = _conn.cursor()
                _cur.execute("SELECT COUNT(*) FROM users")
                _user_count = _cur.fetchone()[0]
                if _user_count == 0:
                    _admin_email = os.getenv('ADMIN_EMAIL', 'admin@developer.com')
                    _admin_password = os.getenv('ADMIN_PASSWORD', '')
                    if not _admin_password:
                        import secrets
                        _admin_password = secrets.token_urlsafe(16)
                        logger.warning("⚠️ No ADMIN_PASSWORD env var set — generated random password. Set ADMIN_PASSWORD env var for production.")
                    _auth_svc.register(
                        email=_admin_email,
                        password=_admin_password,
                        name=os.getenv('ADMIN_NAME', 'Admin User'),
                        username=os.getenv('ADMIN_USERNAME', 'admin')
                    )
                    print(f"✅ Admin user created: {_admin_email}", flush=True)
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
    _oauth_task = asyncio.create_task(_init_oauth_background())
    _persistent_tasks.add(_oauth_task)
    _oauth_task.add_done_callback(_persistent_tasks.discard)
    
    # Auto-sync enabled? - Use new OAuth automation
    auto_sync_enabled = os.getenv('AUTO_SYNC_ENABLED', 'true').lower() == 'true'
    has_email_accounts = len(scraper_service.email_accounts) > 0
    
    if auto_sync_enabled and (has_email_accounts or oauth_automation_service.is_configured):
        logger.info(f"🔄 Auto-sync: ENABLED (every {os.getenv('SYNC_INTERVAL_MINUTES', '15')} minutes)")
        
        # Start single unified email sync loop (cost-optimized: no duplicate loops)
        try:
            background_sync_task = asyncio.create_task(auto_sync_emails())
            _persistent_tasks.add(background_sync_task)
            background_sync_task.add_done_callback(_persistent_tasks.discard)
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
        _persistent_tasks.add(campaign_task)
        campaign_task.add_done_callback(_persistent_tasks.discard)
        logger.info("📬 Campaign processor started (checks every 60 minutes)")
    except Exception as e:
        logger.warning(f"⚠️ Advanced services initialization warning: {str(e)}")
    
    # Start periodic GCS database backup (every 30 minutes in production)
    if _settings.is_production:
        _db_backup_task = asyncio.create_task(periodic_db_backup(interval_minutes=120))
        _persistent_tasks.add(_db_backup_task)
        _db_backup_task.add_done_callback(_persistent_tasks.discard)
        logger.info("💾 GCS auto-backup: Enabled (every 2 hours)")
    
    # Launch background seed from JSON if no DB was restored
    _seed_task = None
    if _seed_needed:
        print("💾 Launching background JSON seed task...", flush=True)
        _seed_task = asyncio.create_task(_background_seed_from_json())
    
    # Launch background candidate processing (every 4 hours — email sync handles most new candidates)
    _process_task = asyncio.create_task(_background_process_candidates(interval_minutes=240))
    _persistent_tasks.add(_process_task)
    _process_task.add_done_callback(_persistent_tasks.discard)
    logger.info("🧠 Background candidate processing: Enabled (every 240 minutes, 5 per batch)")
    
    # Auto-repair: Quick health check and repair if issues found
    async def _auto_repair_on_startup():
        """Run lightweight DB repair 5s after startup to fix garbled data."""
        await asyncio.sleep(5)
        _conn = None
        _conn2 = None
        try:
            _db = get_db_service()
            _conn = _db.get_connection_raw()
            health = quick_health_check(_conn)
            _conn.close()
            _conn = None
            
            if health['needs_repair']:
                logger.warning(
                    f"🔧 DB health: {health['issue_count']} issues found "
                    f"(bad names: {health['bad_names']}, zero score: {health['zero_score']}, "
                    f"mojibake: {health['mojibake_text']}, system emails: {health['system_emails']}, "
                    f"cid artifacts: {health.get('cid_artifacts', 0)}). "
                    f"Running auto-repair..."
                )
                _conn2 = _db.get_connection_raw()
                repair_result = repair_database(_conn2)
                _conn2.close()
                _conn2 = None
                logger.info(
                    f"✅ Auto-repair done: {repair_result['total_fixed']} fixed, "
                    f"{repair_result['summary']['deleted']} deleted, "
                    f"{repair_result['summary']['encoding_fixed']} encoding fixed, "
                    f"{repair_result['summary']['names_recovered']} names recovered"
                )
            else:
                logger.info(f"✅ DB health OK: {health['total_candidates']} candidates, no issues")
        except Exception as _repair_err:
            logger.warning(f"⚠️ Auto-repair skipped: {_repair_err}")
        finally:
            if _conn:
                try:
                    _conn.close()
                except Exception as e:
                    logger.warning(f"Failed to close DB connection: {e}")
            if _conn2:
                try:
                    _conn2.close()
                except Exception as e:
                    logger.warning(f"Failed to close DB connection: {e}")
    
    asyncio.create_task(_auto_repair_on_startup())
    # Note: auto-repair is fire-and-forget (short-lived), no need to persist
    
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

# Set up security, caching, rate-limiting, and timing middleware FIRST
from core.middleware import setup_middleware
setup_middleware(app)

# CORS configuration - MUST be registered LAST so it's the outermost middleware
# (Starlette executes middleware in reverse registration order)
if _settings.is_production:
    _cors_origin = os.getenv(
        'CORS_ORIGINS',
        'https://efforts-recruitment-ai.web.app,https://efforts-recruitment-ai.firebaseapp.com'
    )
    allowed_origins = [o.strip() for o in _cors_origin.split(',') if o.strip()]
else:
    allowed_origins = ['http://localhost:3000', 'http://localhost:3001', 'http://localhost:5173', 'http://localhost:5174']
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
    max_age=3600,  # Cache preflight requests for 1 hour
)
logger.info(f"✅ CORS enabled for: {', '.join(allowed_origins)}")

@app.middleware("http")
async def add_performance_headers(request, call_next):
    """Add performance monitoring headers and prevent browser caching of API data"""
    start_time = time.time()
    try:
        response = await call_next(request)
    except RuntimeError as exc:
        # Starlette raises RuntimeError("No response returned.") when the downstream
        # handler crashes.  Return a proper 502 instead of letting it bubble up.
        if "No response returned" in str(exc):
            from starlette.responses import JSONResponse
            logger.error(f"Middleware: downstream handler failed for {request.url.path}")
            return JSONResponse({"detail": "Bad Gateway"}, status_code=502)
        raise
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time * 1000, 2))
    # Prevent browsers from caching API responses — stale data causes ghost candidates
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

# Initialize services
resume_parser = ResumeParser()
matching_engine = MatchingEngine()
email_parser = EmailParser()

# AI Services - Smart Tier Selection
# Priority: auto-detect based on environment (production → Gemini, local → Ollama)
local_ai_service = get_local_ai_service()
gemini_service = get_gemini_service()  # Initialized if GEMINI_API_KEY is set

# PRIMARY AI service for candidate analysis: prefer Gemini in production (local_ai_service is regex-only in Cloud Run)
# Gemini provides proper skill extraction, experience detection, and job categorization
if gemini_service and gemini_service.available:
    ai_service = gemini_service
    logger.info("Using Gemini as PRIMARY AI service for candidate analysis")
else:
    ai_service = local_ai_service
    logger.warning("Gemini not available — falling back to local_ai_service (reduced quality)")

# Log AI tier configuration
_ai_tier = _settings.ai_tier_order
logger.info(f"🤖 AI Tier Order: {' → '.join(_ai_tier)}")
if gemini_service and gemini_service.available:
    logger.info(f"   ✅ Gemini: {gemini_service.model_name} (ready)")
else:
    logger.info(f"   ⚠️ Gemini: not configured (set GEMINI_API_KEY)")

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
            "Smart AI tier fallback (Gemini → Ollama → Keyword)",
            "High-load optimized (100+ concurrent)",
            "Response caching (5min TTL)",
            "Connection pooling (50 max)",
            "Auto job categorization",
            "Duplicate detection"
        ]
    }

@app.get("/version")
async def version():
    return {
        "version": os.getenv('MODEL_VERSION', _settings.app_version),
        "deployed": datetime.now().strftime('%Y-%m-%d'),
        "environment": "production" if _settings.is_production else "development"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring — includes DB connectivity"""
    db_ok = False
    candidate_count = 0
    try:
        def _health_db_check():
            with db_service.get_connection() as _check_conn:
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
    return {
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "version": os.getenv('MODEL_VERSION', _settings.app_version),
        "database": {"connected": db_ok, "candidates": candidate_count},
        "scraper_running": scraper_task is not None and not scraper_task.done(),
        "system": system_info,
        "cache": {
            "response_cache_size": len(response_cache),
            "ai_embedding_cache": len(ai_service.embedding_cache) if hasattr(ai_service, 'embedding_cache') else 0
        }
    }

@app.post("/api/admin/reset-database")
async def reset_database(auth=Depends(require_admin)):
    """Nuclear reset: wipe all candidates, resumes, caches, logs. Keeps users."""
    def _reset():
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            tables_to_wipe = [
                'ai_score_cache',
                'resumes',
                'email_processing_log',
                'search_history',
                'candidates',
                'sync_metadata',
            ]
            for table in tables_to_wipe:
                try:
                    cursor.execute(f"DELETE FROM {table}")
                    logger.info(f"🗑️ Wiped table: {table}")
                except Exception as e:
                    logger.warning(f"Could not wipe {table}: {e}")
            conn.commit()
            cursor.execute("SELECT COUNT(*) FROM candidates")
            remaining = cursor.fetchone()[0]
            return remaining
    remaining = await asyncio.to_thread(_reset)
    # Clear in-memory caches
    response_cache.clear()
    if hasattr(ai_service, 'embedding_cache'):
        ai_service.embedding_cache.clear()
    return {
        "status": "success",
        "message": "Database reset complete — all candidates, resumes, caches, and logs wiped",
        "remaining_candidates": remaining
    }

@app.post("/api/admin/backup-db")
async def manual_backup_db(auth=Depends(require_admin)):
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
        logger.error(f"Backup failed: {e}")
        raise HTTPException(status_code=500, detail="Backup failed. Check server logs for details.")


# ============================================
# Setup & Configuration Verification Endpoints
# ============================================

@app.get("/api/setup/verify")
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


@app.get("/api/setup/status")
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
        "ai_mode": "gemini" if gemini_service and gemini_service.available else "local (free)",
        "email_oauth": bool(os.getenv('MICROSOFT_CLIENT_ID')),
        "sms_enabled": bool(os.getenv('TWILIO_ACCOUNT_SID')),
        "calendar_enabled": bool(os.getenv('GOOGLE_CLIENT_ID') or os.getenv('CALENDLY_API_KEY')),
        "redis_enabled": bool(os.getenv('REDIS_URL')),
        "version": os.getenv('APP_VERSION', '4.1.0')
    }


@app.get("/api/setup/instructions")
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


@app.post("/api/setup/test-connection/{service}")
async def test_service_connection(service: str, current_user: dict = Depends(require_auth)):
    """
    Test connection to a specific service
    """
    results = {"service": service, "status": "unknown"}
    
    if service == "database":
        try:
            count = await asyncio.to_thread(db_service.get_total_candidates)
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
            test_result = await ai_service.analyze_candidate("Software engineer with 5 years Python experience")
            results = {"service": service, "status": "working", "sample_score": test_result.get('quality_score')}
        except Exception as e:
            results = {"service": service, "status": "error", "error": "AI service unavailable"}
    
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
    
    scraper_task = asyncio.create_task(scraper_service.run_continuous_scraper(db_service=db_service))
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
async def scraper_status(current_user: dict = Depends(require_auth)):
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
        logger.error(f"Scraping error: {e}")
        raise HTTPException(500, "Email scraping failed. Check server logs for details.")


# ====== DELETE INDIVIDUAL CANDIDATE ======
@app.delete("/api/candidates/{candidate_id}")
async def delete_candidate(candidate_id: str, current_user: dict = Depends(require_auth)):
    """Delete a single candidate by ID."""
    try:
        def _delete_candidate_db():
            with db_service.get_connection() as conn:
                cursor = conn.execute("SELECT name, email FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                name, email = row[0], row[1]
                conn.execute("UPDATE candidates SET is_active = 0, last_updated = ? WHERE id = ?",
                             (datetime.now().isoformat(), candidate_id))
                conn.execute("DELETE FROM resumes WHERE candidate_id = ?", (candidate_id,))
                conn.commit()
                return (name, email)
        result = await asyncio.to_thread(_delete_candidate_db)
        if result is None:
            raise HTTPException(404, f"Candidate {candidate_id} not found")
        name, email = result
        response_cache.clear()
        logger.info(f"🗑️ Deleted candidate: {name} ({email})")
        return {"status": "success", "message": f"Candidate {name} deleted", "candidate_id": candidate_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete candidate error: {e}")
        raise HTTPException(500, "Error deleting candidate. Check server logs for details.")


# ====== PURGE INDEED RELAY / JUNK CANDIDATES ======
@app.post("/api/candidates/purge-indeed")
async def purge_indeed_candidates(current_user: dict = Depends(require_admin)):
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
        raise HTTPException(500, "Purge failed. Check server logs for details.")


# ====== INTELLIGENT GIBBERISH PROFILE CLEANUP ======
@app.post("/api/admin/cleanup-gibberish")
async def cleanup_gibberish_profiles(current_user: dict = Depends(require_admin)):
    """
    Comprehensive database repair: fix gibberish, garbled names, encoding issues,
    HTML in data, bad phones, duplicate emails, empty profiles, and more.
    Uses the new db_repair service for thorough detection and fixing.
    """
    try:
        response_cache.clear()

        def _cleanup_gibberish_db():
            conn = db_service.get_connection_raw()
            try:
                _results = repair_database(conn)
            finally:
                conn.close()
            # Flush connection pool so subsequent reads see the repaired data
            try:
                with db_service.connection_lock:
                    while db_service._connection_pool:
                        old = db_service._connection_pool.pop()
                        try:
                            old.close()
                        except Exception as e:
                            logger.warning(f"Failed to close DB connection: {e}")
            except Exception as e:
                logger.debug(f"Non-critical: pool cleanup during DB repair: {e}")
            return _results
        results = await asyncio.to_thread(_cleanup_gibberish_db)
        
        logger.warning(
            f"DB Repair: {results['total_fixed']} fixed, "
            f"{results['summary']['deleted']} deleted, "
            f"{results['summary']['encoding_fixed']} encoding, "
            f"{results['summary']['names_recovered']} names recovered, "
            f"{results['summary']['duplicates_merged']} duplicates merged"
        )
        
        return {
            "status": "success",
            "deleted_count": results['summary']['deleted'],
            "reprocessed_count": results['summary']['names_recovered'],
            "encoding_fixed_count": results['summary']['encoding_fixed'],
            "skills_extracted_count": results['summary'].get('skills_extracted', 0),
            "rescored_count": results['summary'].get('rescored', 0),
            "total_fixed": results['total_fixed'],
            "details": results['summary'],
            "deleted": results['deleted'][:20],
            "encoding_fixed": results['encoding_fixed'][:20],
            "names_recovered": results['names_recovered'][:20],
            "skills_extracted": results.get('skills_extracted', [])[:20],
            "rescored": results.get('rescored', [])[:20],
            "duplicates_merged": results['duplicates_merged'][:10],
            "needs_rescore": len(results['needs_rescore']),
        }
    except Exception as e:
        logger.error(f"DB Repair failed: {e}")
        raise HTTPException(500, "Database repair failed. Check server logs for details.")


@app.get("/api/admin/database-audit")
async def database_audit_report(current_user: dict = Depends(require_admin)):
    """
    Full database health audit — returns detailed issue report without modifying data.
    Shows gibberish profiles, encoding issues, empty fields, score distribution, etc.
    """
    try:
        def _audit_db():
            conn = db_service.get_connection_raw()
            try:
                return audit_database(conn)
            finally:
                conn.close()
        report = await asyncio.to_thread(_audit_db)
        return {"status": "success", **report}
    except Exception as e:
        logger.error(f"Audit failed: {e}")
        raise HTTPException(500, "Database audit failed. Check server logs for details.")


@app.post("/api/admin/database-repair-full")
async def full_database_repair(current_user: dict = Depends(require_admin)):
    """
    Full repair pipeline: cleanup → fix encoding → recover names → deduplicate → re-score.
    This is the nuclear option — fixes everything in one pass.
    """
    try:
        response_cache.clear()
        
        # Phase 1: Run comprehensive repair (delete gibberish, fix encoding, etc.)
        def _phase1_repair():
            conn = db_service.get_connection_raw()
            try:
                return repair_database(conn)
            finally:
                conn.close()
        repair_results = await asyncio.to_thread(_phase1_repair)
        
        # Phase 2: Re-score all candidates that need it (0, NULL, or 50 default)
        rescore_count = 0
        rescore_errors = 0
        try:
            def _fetch_rescore_candidates():
                with db_service.get_connection() as conn2:
                    cursor = conn2.cursor()
                    cursor.execute("""
                        SELECT id, email, name, skills, summary, education, work_history, 
                               resume_text, experience
                        FROM candidates 
                        WHERE is_active = 1 AND (match_score = 0 OR match_score IS NULL OR match_score <= 35)
                    """)
                    return cursor.fetchall()
            rows = await asyncio.to_thread(_fetch_rescore_candidates)
            
            for row in rows:
                try:
                    cid, email, name, skills_json, summary, education, work_history, resume_text, experience = row
                    try:
                        skills = json.loads(skills_json) if skills_json else []
                    except (json.JSONDecodeError, TypeError):
                        skills = []
                    
                    text_parts = []
                    if summary: text_parts.append(summary)
                    if resume_text: text_parts.append(resume_text[:2000])
                    if skills: text_parts.append(' '.join(str(s) for s in skills))
                    if education:
                        try:
                            ed = json.loads(education) if isinstance(education, str) else education
                            for e in (ed if isinstance(ed, list) else []):
                                if isinstance(e, dict):
                                    text_parts.append(f"{e.get('degree', '')} {e.get('institution', '')}")
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse education JSON: {e}")
                    if work_history:
                        try:
                            wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                            for job in (wh if isinstance(wh, list) else []):
                                if isinstance(job, dict):
                                    text_parts.append(f"{job.get('title', '')} at {job.get('company', '')}")
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse work_history JSON: {e}")
                    
                    combined_text = ' '.join(text_parts)
                    try:
                        exp_years = int(float(experience)) if experience else 0
                    except (ValueError, TypeError):
                        exp_years = 0
                    
                    if len(combined_text.strip()) < 10:
                        new_score = 40
                        new_category = 'General'
                    else:
                        try:
                            _rescore_ai = ai_service
                            try:
                                _g = get_gemini_service()
                                if _g and _g.available:
                                    _rescore_ai = _g
                            except Exception:
                                pass
                            analysis_result = await asyncio.wait_for(
                                _rescore_ai.analyze_candidate(combined_text),
                                timeout=AI_ANALYSIS_TIMEOUT
                            )
                            new_score = analysis_result.get('quality_score') or analysis_result.get('match_score')
                            try:
                                new_score = int(float(new_score)) if new_score else 0
                            except (ValueError, TypeError):
                                new_score = 0
                            if new_score <= 0:
                                new_score = min(90, max(15, 25 + min(30, len(skills) * 3) + min(25, exp_years * 3)))
                            new_category = analysis_result.get('job_category', 'General')
                        except Exception as ae:
                            logger.warning(f"Re-score error for {name}: {type(ae).__name__}: {str(ae)[:200]}")
                            new_score = min(90, max(15, 25 + min(30, len(skills) * 3) + min(25, exp_years * 3)))
                            new_category = 'General'
                    
                    def _update_rescore(cid, new_score, new_category):
                        with db_service.get_connection() as uc:
                            ucur = uc.cursor()
                            ucur.execute("""
                                UPDATE candidates SET match_score = ?, job_category = ?, last_updated = ? WHERE id = ?
                            """, (new_score, new_category, datetime.now().isoformat(), cid))
                            uc.commit()
                    await asyncio.to_thread(_update_rescore, cid, new_score, new_category)
                    rescore_count += 1
                except Exception as rescore_err:
                    logger.warning(f"Re-score error for {name}: {type(rescore_err).__name__}: {str(rescore_err)[:200]}")
                    rescore_errors += 1
        except Exception as se:
            logger.warning(f"Re-score phase error: {se}")
        
        # Phase 3: Post-repair audit
        def _phase3_audit():
            conn3 = db_service.get_connection_raw()
            try:
                return quick_health_check(conn3)
            finally:
                conn3.close()
        post_audit = await asyncio.to_thread(_phase3_audit)
        
        # Phase 4: Backup repaired DB to GCS
        try:
            await asyncio.to_thread(backup_db_to_gcs)
            logger.info("DB Repair: backed up repaired database to GCS")
        except Exception as bke:
            logger.warning(f"DB Repair: GCS backup failed: {bke}")
        
        return {
            "status": "success",
            "repair": repair_results['summary'],
            "total_fixed": repair_results['total_fixed'],
            "rescored": rescore_count,
            "rescore_errors": rescore_errors,
            "post_repair_health": post_audit,
            "message": (
                f"Repaired {repair_results['total_fixed']} profiles, "
                f"rescored {rescore_count}, "
                f"{post_audit['issue_count']} issues remaining"
            ),
        }
    except Exception as e:
        logger.error(f"Full repair failed: {e}")
        raise HTTPException(500, "Full database repair failed. Check server logs for details.")


@app.post("/api/admin/relookup-from-email")
async def relookup_garbled_from_email(current_user: dict = Depends(require_admin)):
    """
    For candidates with garbled/empty data, search the original emails by sender address
    via Microsoft Graph and re-extract candidate info from the email body + attachments.
    This recovers data that was lost during initial parsing.
    """
    try:
        response_cache.clear()
        
        # Find candidates that need re-lookup: bad names, no skills, empty summary
        def _fetch_garbled_candidates():
            conn = db_service.get_connection_raw()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, email, name, skills, summary, resume_text, match_score
                    FROM candidates
                    WHERE (is_active = 1 OR is_active IS NULL)
                    AND (
                        name = '' OR name = 'Unknown' OR name IS NULL
                        OR name LIKE '%Ã%' OR name LIKE '%â€%'
                        OR (skills IS NULL OR skills = '[]' OR skills = '')
                        OR (summary IS NULL OR summary = '')
                        OR match_score = 0 OR match_score IS NULL
                    )
                """)
                rows = cursor.fetchall()
                cols = [d[0] for d in cursor.description]
                return [dict(zip(cols, r)) for r in rows]
            finally:
                conn.close()
        candidates_to_fix = await asyncio.to_thread(_fetch_garbled_candidates)
        
        if not candidates_to_fix:
            return {"status": "success", "message": "No candidates need re-lookup", "fixed": 0}
        
        logger.info(f"Re-lookup: {len(candidates_to_fix)} candidates need email re-lookup")
        
        # Get OAuth token for email access
        primary_email = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL') or _settings.email_address or ''
        token_storage = get_token_storage()
        token_data = token_storage.get_token(primary_email)
        
        if not token_data:
            return {
                "status": "partial",
                "message": "No OAuth2 token — cannot search emails. Authenticate first.",
                "candidates_needing_fix": len(candidates_to_fix)
            }
        
        # Initialize Graph service
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id)
        graph_service.access_token = token_data['access_token']
        graph_service.token_expiry = datetime.fromisoformat(token_data['expires_at'])
        
        fixed_count = 0
        errors = 0
        fixed_details = []
        
        for cand in candidates_to_fix[:50]:  # Limit to 50 per call to avoid timeouts
            try:
                sender_email = cand['email']
                if not sender_email or '@' not in sender_email:
                    continue
                
                # Search for emails from this sender
                filter_q = f"from/emailAddress/address eq '{sender_email}'"
                result = await graph_service.get_messages(
                    folder='inbox', filter_query=filter_q, top=5
                )
                
                if result['status'] != 'success' or not result.get('messages'):
                    continue
                
                # Process the most recent email from this sender
                for msg in result['messages'][:3]:
                    sender = msg.get('from', {}).get('emailAddress', {})
                    sender_name = sender.get('name', '')
                    subject = msg.get('subject', '')
                    body = msg.get('body', {}).get('content', '')
                    
                    # Get attachments
                    attachments = []
                    if msg.get('hasAttachments'):
                        try:
                            attach_result = await graph_service.get_message_with_attachments(msg['id'])
                            if attach_result['status'] == 'success':
                                attachments = attach_result['attachments']
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to get attachments: {e}")
                    
                    received_dt = msg.get('receivedDateTime')
                    received_date = datetime.now()
                    if received_dt:
                        try:
                            received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00'))
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse receivedDateTime: {e}")
                    
                    email_data = {
                        'subject': subject,
                        'sender_email': sender_email,
                        'sender_name': sender_name,
                        'body': body,
                        'attachments': attachments,
                        'received_date': received_date
                    }
                    
                    # Re-extract candidate from email
                    new_candidate = await scraper_service.extract_candidate_from_email(email_data)
                    if not new_candidate or not new_candidate.get('email'):
                        continue
                    
                    # Save resume file if extracted from attachment
                    resume_file = new_candidate.pop('resume_file_data', None)
                    resume_filename = new_candidate.pop('resume_filename', None)
                    if resume_file and resume_filename:
                        try:
                            ct = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                            await asyncio.to_thread(db_service.save_resume, cand['id'], resume_filename, resume_file, ct)
                        except Exception as e:
                            logger.warning(f"Failed to save resume for {cand.get('id', 'unknown')}: {e}")
                    
                    # Merge improved data into existing record
                    updates = {}
                    old_name = cand.get('name') or ''
                    new_name = new_candidate.get('name') or ''
                    if new_name and len(new_name) > len(old_name) and new_name.lower() not in ('unknown', 'n/a'):
                        updates['name'] = new_name
                    
                    new_skills = new_candidate.get('skills') or []
                    old_skills = json.loads(cand.get('skills') or '[]') if isinstance(cand.get('skills'), str) else (cand.get('skills') or [])
                    if len(new_skills) > len(old_skills):
                        updates['skills'] = json.dumps(new_skills)
                    
                    new_summary = new_candidate.get('summary') or ''
                    old_summary = cand.get('summary') or ''
                    if len(new_summary) > len(old_summary):
                        updates['summary'] = new_summary
                    
                    new_resume = new_candidate.get('resume_text') or ''
                    old_resume = cand.get('resume_text') or ''
                    if len(new_resume) > len(old_resume):
                        updates['resume_text'] = new_resume
                    
                    if new_candidate.get('phone') and not cand.get('phone'):
                        updates['phone'] = new_candidate['phone']
                    if new_candidate.get('location') and not cand.get('location'):
                        updates['location'] = new_candidate['location']
                    if new_candidate.get('experience') and not cand.get('experience'):
                        updates['experience'] = new_candidate['experience']
                    
                    if updates:
                        updates['last_updated'] = datetime.now().isoformat()
                        def _update_relookup_candidate(updates_dict, cand_id):
                            with db_service.get_connection() as uc:
                                ucur = uc.cursor()
                                set_parts = [f"{k} = ?" for k in updates_dict]
                                vals = list(updates_dict.values()) + [cand_id]
                                ucur.execute(
                                    f"UPDATE candidates SET {', '.join(set_parts)} WHERE id = ?",
                                    vals
                                )
                                uc.commit()
                        await asyncio.to_thread(_update_relookup_candidate, updates, cand['id'])
                        fixed_count += 1
                        fixed_details.append({
                            'id': cand['id'],
                            'email': sender_email,
                            'old_name': old_name[:30],
                            'new_name': updates.get('name', old_name)[:30],
                            'fields_updated': list(updates.keys()),
                        })
                        break  # Found good data, move to next candidate
                
            except Exception as e:
                errors += 1
                logger.warning(f"Re-lookup error for {cand.get('email')}: {str(e)[:60]}")
        
        return {
            "status": "success",
            "candidates_checked": len(candidates_to_fix),
            "fixed": fixed_count,
            "errors": errors,
            "fixed_details": fixed_details[:20],
            "message": f"Re-looked up {len(candidates_to_fix)} candidates, fixed {fixed_count}",
        }
    except Exception as e:
        logger.error(f"Re-lookup failed: {e}")
        raise HTTPException(500, "Re-lookup failed. Check server logs for details.")


@app.patch("/api/admin/candidates/{candidate_id}")
async def admin_update_candidate(candidate_id: str, request: Request, current_user: dict = Depends(require_admin)):
    """Admin endpoint to directly update candidate fields (name, email, summary, etc.)."""
    updates = await request.json()
    allowed_fields = {'name', 'email', 'summary', 'resume_text', 'status', 'match_score', 'skills'}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}
    if not filtered:
        raise HTTPException(400, f"No valid fields. Allowed: {allowed_fields}")
    conn = None
    try:
        def _admin_update_db(filtered_fields, candidate_id_val):
            conn = db_service.get_connection_raw()
            try:
                cursor = conn.cursor()
                set_parts = [f"{k} = ?" for k in filtered_fields]
                set_parts.append("last_updated = ?")
                vals = list(filtered_fields.values()) + [datetime.now().isoformat(), candidate_id_val]
                cursor.execute(f"UPDATE candidates SET {', '.join(set_parts)} WHERE id = ?", vals)
                if cursor.rowcount == 0:
                    return None
                conn.commit()
                return True
            finally:
                conn.close()
        result = await asyncio.to_thread(_admin_update_db, filtered, candidate_id)
        if result is None:
            raise HTTPException(404, "Candidate not found")
        return {"status": "success", "updated_fields": list(filtered.keys()), "candidate_id": candidate_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin update candidate error: {e}")
        raise HTTPException(500, "Update failed. Check server logs for details.")


@app.post("/api/candidates/reset-and-reparse")
async def reset_and_reparse_all_emails(current_user: dict = Depends(require_admin)):
    """
    Clear all candidates and re-parse ALL emails from inbox.
    Parses email body, attached resumes, and uses Local AI for analysis.
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
        primary_email = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL') or _settings.email_address or ''
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
        
        # Fetch emails in batches to avoid OOM (max 5000 per batch, paginated)
        logger.info("📧 Fetching emails from inbox for re-parsing (paginated)...")
        result = await graph_service.get_messages(folder='inbox', top=5000, fetch_all=True)
        
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

                # Block system/noreply emails and bad candidate names
                _email_lower = candidate['email'].lower()
                _name_lower = (candidate.get('name', '') or '').lower().strip()
                _BLOCKED_EMAIL_PATS = ['noreply', 'no-reply', 'no_reply', 'donotreply', 'do-not-reply',
                    'mailer-daemon', 'postmaster', 'notifications@', 'notification@',
                    'messages-noreply', 'alert@', 'alerts@', 'system@', 'bounce@']
                _BLOCKED_NAMES = ['unknown', 'messages', 'notification', 'noreply', 'no reply',
                    'system', 'admin', 'administrator', 'postmaster', 'mailer-daemon',
                    'indeed', 'linkedin', 'glassdoor', 'monster', 'info', 'support',
                    'test', 'null', 'none', 'n/a', 'na', '']
                if any(pat in _email_lower for pat in _BLOCKED_EMAIL_PATS):
                    return
                if _name_lower in _BLOCKED_NAMES or len(_name_lower) < 2:
                    return
                
                # AI Analysis: Use Local AI first, Gemini as fallback
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
                            candidate['job_category'] = normalize_category_backend(ai_analysis.get('job_category', candidate.get('job_category', 'General')))
                            candidate['matchScore'] = ai_analysis.get('quality_score')
                            # Update skills and experience from AI if better
                            if ai_analysis.get('skills'):
                                ai_skills = ai_analysis['skills']
                                if isinstance(ai_skills, list) and len(ai_skills) > len(candidate.get('skills', [])):
                                    candidate['skills'] = ai_skills
                            if ai_analysis.get('experience') and ai_analysis['experience'] > candidate.get('experience', 0):
                                candidate['experience'] = ai_analysis['experience']
                            ai_analyzed_count += 1
                    except asyncio.TimeoutError:
                        # Local AI timeout - calculate from available data
                        logger.warning(f"⏱️ Local AI timeout for {sender_email} - using calculated fallback")
                        _skills = candidate.get('skills', [])
                        _exp = candidate.get('experience', 0) or 0
                        _has_edu = bool(candidate.get('education'))
                        _has_summary = bool(candidate.get('summary', '').strip())
                        candidate['matchScore'] = min(92, max(15, 30 + min(25, len(_skills) * 3) + min(25, (_exp if isinstance(_exp, int) else 0) * 3) + (5 if _has_edu else 0) + (3 if _has_summary else 0)))
                    except Exception as ai_err:
                        logger.warning(f"AI analysis error: {str(ai_err)[:50]} - using calculated fallback")
                        _skills = candidate.get('skills', [])
                        _exp = candidate.get('experience', 0) or 0
                        _has_edu = bool(candidate.get('education'))
                        _has_summary = bool(candidate.get('summary', '').strip())
                        candidate['matchScore'] = min(92, max(15, 30 + min(25, len(_skills) * 3) + min(25, (_exp if isinstance(_exp, int) else 0) * 3) + (5 if _has_edu else 0) + (3 if _has_summary else 0)))
                
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
        logger.error(f"Reset and reparse error: {e}")
        raise HTTPException(500, "Reset and reparse failed. Check server logs for details.")

# Candidate Management Endpoints (Database-backed)
@app.get("/api/candidates")
async def get_candidates(
    page: int = 1,
    limit: int = 50,
    job_category: Optional[str] = None,
    min_score: Optional[int] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    fields: Optional[str] = None,
    current_user: dict = Depends(require_auth)
):
    """
    Get candidates with OPTIMIZED pagination and caching
    Efficiently handles 100,000+ candidates
    Use fields=light for fast list views (skips resume_text, ai_analysis)
    Use search= to filter by name, email, skills, or job category
    Use status= to filter by candidate status (e.g. Shortlisted, Reviewed)
    """
    # Validate pagination bounds
    page = max(1, page)
    limit = max(1, min(500, limit))
    if search and len(search) > 500:
        raise HTTPException(400, "Search term too long (max 500 characters)")
    is_light = fields == 'light'
    # Create cache key
    cache_key = f"candidates_p{page}_l{limit}_c{job_category}_s{min_score}_q{search}_st{status}_f{fields}"
    
    # Check cache first
    if cache_key in response_cache:
        logger.info("Cache hit for candidates")
        cached_result = response_cache[cache_key]
        cached_result["from_cache"] = True
        return cached_result
    
    try:
        filters = {}
        if job_category:
            filters['job_category'] = job_category
        if min_score:
            filters['min_score'] = min_score
        if search:
            filters['search'] = search
        if status:
            filters['status'] = status
        
        # Use lightweight query for list views, full query for detail views
        # Reads don't need the db_semaphore — SQLite handles concurrent reads natively
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
        raise HTTPException(500, "Error fetching candidates")

@app.get("/api/candidates/new")
async def get_new_candidates(since: str, limit: int = 500, current_user: dict = Depends(require_auth)):
    """
    Get only NEW candidates since specified date
    Incremental processing - avoids reprocessing 100,000s
    """
    try:
        new_candidates = await asyncio.to_thread(db_service.get_new_candidates_since, since)
        total_new = len(new_candidates)
        # Limit response size to prevent middleware/memory issues
        capped = new_candidates[:limit]
        return {
            "new_count": total_new,
            "returned": len(capped),
            "candidates": capped
        }
    except Exception as e:
        logger.error(f"Error fetching new candidates since {since}: {e}")
        raise HTTPException(500, "Error fetching new candidates. Check server logs for details.")


@app.post("/api/admin/fix-corrupted-resume-text")
async def fix_corrupted_resume_text(current_user: dict = Depends(require_admin)):
    """
    Scan ALL candidates for corrupted resume_text (spaced characters, gibberish).
    For each corrupted entry that has a stored resume file, re-extract the text
    using the improved parser. For entries without a resume file, attempt in-place
    text repair (collapse spaced chars).
    """
    try:
        response_cache.clear()
        results = {
            "scanned": 0,
            "corrupted_found": 0,
            "re_extracted": 0,
            "text_repaired": 0,
            "no_resume_file": 0,
            "errors": 0,
            "details": []
        }

        # Fetch all active candidates (with or without resume_text — we check names too)
        def _fetch_all():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, email, resume_text
                    FROM candidates
                    WHERE is_active = 1
                """)
                return cursor.fetchall()

        rows = await asyncio.to_thread(_fetch_all)
        results["scanned"] = len(rows)

        for row in rows:
            cid, name, email, resume_text = row
            try:
                # Check name for spaced-char corruption
                name_corrupted = bool(name and is_spaced_text(name, threshold=0.15, min_length=5))

                # Skip if no resume_text and name is fine
                if (not resume_text or len((resume_text or '').strip()) < 20) and not name_corrupted:
                    continue

                # Check if text is corrupted (spaced chars or very low quality)
                quality = text_quality_score(resume_text) if resume_text else 0.0
                is_spaced = is_spaced_text(resume_text) if resume_text else False

                if not is_spaced and quality >= 0.3 and not name_corrupted:
                    continue  # Text and name look fine

                results["corrupted_found"] += 1
                detail = {"id": cid, "name": name, "quality_before": round(quality, 3)}

                # Try to get the stored resume file and re-extract
                resume_data = await asyncio.to_thread(db_service.get_resume, cid)

                if resume_data and resume_data.get("file_data"):
                    try:
                        new_text = await resume_parser.extract_text(
                            resume_data["file_data"],
                            resume_data.get("filename", "resume.pdf")
                        )
                        new_quality = text_quality_score(new_text)
                        detail["quality_after"] = round(new_quality, 3)
                        detail["method"] = "re-extract"

                        if new_quality > quality and len(new_text.strip()) > 20:
                            # Update the candidate's resume_text
                            def _update_text(c_id, txt):
                                with db_service.get_connection() as conn:
                                    cursor = conn.cursor()
                                    cursor.execute(
                                        "UPDATE candidates SET resume_text = ?, last_updated = ? WHERE id = ?",
                                        (txt[:10000], datetime.now().isoformat(), c_id)
                                    )
                                    conn.commit()
                            await asyncio.to_thread(_update_text, cid, new_text)
                            results["re_extracted"] += 1
                            detail["status"] = "re-extracted"
                        else:
                            detail["status"] = "re-extract-no-improvement"
                    except Exception as ex:
                        detail["status"] = f"re-extract-error: {str(ex)[:80]}"
                        results["errors"] += 1
                else:
                    # No resume file — try in-place text repair
                    results["no_resume_file"] += 1
                    if is_spaced:
                        repaired = collapse_spaced_chars(resume_text)
                        repaired_quality = text_quality_score(repaired)
                        if repaired_quality >= quality:  # accept equal or better
                            def _update_repaired(c_id, txt, fixed_name):
                                with db_service.get_connection() as conn:
                                    cursor = conn.cursor()
                                    if fixed_name:
                                        cursor.execute(
                                            "UPDATE candidates SET resume_text = ?, name = ?, last_updated = ? WHERE id = ?",
                                            (txt[:10000], fixed_name, datetime.now().isoformat(), c_id)
                                        )
                                    else:
                                        cursor.execute(
                                            "UPDATE candidates SET resume_text = ?, last_updated = ? WHERE id = ?",
                                            (txt[:10000], datetime.now().isoformat(), c_id)
                                        )
                                    conn.commit()
                            # Also fix the name if it's spaced
                            fixed_name = None
                            if name and is_spaced_text(name, threshold=0.15, min_length=5):
                                fixed_name = collapse_spaced_chars(name).strip()
                                detail["name_fixed"] = fixed_name
                            await asyncio.to_thread(_update_repaired, cid, repaired, fixed_name)
                            results["text_repaired"] += 1
                            detail["method"] = "collapse-spaced"
                            detail["quality_after"] = round(repaired_quality, 3)
                            detail["status"] = "text-repaired"
                        else:
                            detail["status"] = "repair-no-improvement"
                    else:
                        detail["status"] = "no-resume-file"

                # Fix name even if resume text wasn't repaired
                if name and is_spaced_text(name, threshold=0.15, min_length=5) and detail.get("status") != "text-repaired":
                    fixed_name = collapse_spaced_chars(name).strip()
                    if fixed_name and fixed_name != name:
                        def _fix_name(c_id, new_name):
                            with db_service.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute(
                                    "UPDATE candidates SET name = ?, last_updated = ? WHERE id = ?",
                                    (new_name, datetime.now().isoformat(), c_id)
                                )
                                conn.commit()
                        await asyncio.to_thread(_fix_name, cid, fixed_name)
                        detail["name_fixed"] = fixed_name

                results["details"].append(detail)

            except Exception as e:
                results["errors"] += 1
                logger.warning(f"Fix resume text error for {cid}: {e}")

        # Only include first 50 details to avoid huge response
        results["details"] = results["details"][:50]

        return {
            "status": "success",
            "message": (
                f"Scanned {results['scanned']} candidates, "
                f"found {results['corrupted_found']} corrupted, "
                f"re-extracted {results['re_extracted']}, "
                f"text-repaired {results['text_repaired']}, "
                f"{results['errors']} errors"
            ),
            **results
        }
    except Exception as e:
        logger.error(f"Fix corrupted resume text error: {e}")
        raise HTTPException(500, "Fix corrupted resume text failed. Check server logs for details.")


@app.post("/api/candidates/reprocess-garbled")
async def reprocess_garbled_candidates(current_user: dict = Depends(require_admin)):
    """
    Comprehensive reprocessing of all poorly processed candidates:
    1. Cleanup gibberish/system profiles
    2. Fix encoding issues (mojibake)
    3. Re-score candidates with 0 or default scores
    4. Regenerate AI analysis for candidates with no or stale analysis
    Runs cleanup first, then re-scores, all in one call.
    """
    try:
        response_cache.clear()
        results = {'cleaned': 0, 'rescored': 0, 'encoding_fixed': 0, 'errors': 0}
        
        # Step 1: Run gibberish cleanup
        try:
            # Create a mock user for internal call
            cleanup_result = await cleanup_gibberish_profiles(current_user)
            results['cleaned'] = cleanup_result.get('deleted_count', 0)
            results['encoding_fixed'] = cleanup_result.get('encoding_fixed_count', 0)
        except Exception as ce:
            logger.warning(f"Gibberish cleanup phase error: {ce}")
        
        # Step 2: Re-score all candidates with 0/null/default(50) scores
        try:
            def _fetch_garbled_rescore():
                with db_service.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, email, name, skills, summary, education, work_history, resume_text, experience
                        FROM candidates 
                        WHERE is_active = 1 AND (match_score = 0 OR match_score IS NULL OR match_score = 50)
                    """)
                    return cursor.fetchall()
            rows = await asyncio.to_thread(_fetch_garbled_rescore)
            
            if rows:
                for row in rows:
                    try:
                        cid, email, name, skills_json, summary, education, work_history, resume_text, experience = row
                        try:
                            skills = json.loads(skills_json) if skills_json else []
                        except (json.JSONDecodeError, TypeError):
                            skills = []
                        
                        text_parts = []
                        if summary: text_parts.append(summary)
                        if resume_text: text_parts.append(resume_text[:2000])
                        if skills: text_parts.append(' '.join(str(s) for s in skills))
                        if education:
                            try:
                                ed = json.loads(education) if isinstance(education, str) else education
                                for e in (ed if isinstance(ed, list) else []):
                                    if isinstance(e, dict):
                                        text_parts.append(f"{e.get('degree', '')} {e.get('institution', '')}")
                            except Exception as e:
                                logger.debug(f"Non-critical: failed to parse education JSON: {e}")
                        if work_history:
                            try:
                                wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                                for job in (wh if isinstance(wh, list) else []):
                                    if isinstance(job, dict):
                                        text_parts.append(f"{job.get('title', '')} at {job.get('company', '')}")
                            except Exception as e:
                                logger.debug(f"Non-critical: failed to parse work_history JSON: {e}")
                        
                        combined_text = ' '.join(text_parts)
                        try:
                            exp_years = int(float(experience)) if experience else 0
                        except (ValueError, TypeError):
                            exp_years = 0
                        
                        if len(combined_text.strip()) < 10:
                            new_score = 15
                            new_category = 'Insufficient Data'
                        else:
                            try:
                                # Use Gemini if available for better results
                                _rescore_ai = ai_service
                                try:
                                    _g = get_gemini_service()
                                    if _g and _g.available:
                                        _rescore_ai = _g
                                except Exception as e:
                                    logger.debug(f"Non-critical: Gemini service not available for rescore: {e}")
                                analysis_result = await asyncio.wait_for(
                                    _rescore_ai.analyze_candidate(combined_text),
                                    timeout=AI_ANALYSIS_TIMEOUT
                                )
                                new_score = analysis_result.get('quality_score') or analysis_result.get('match_score')
                                try:
                                    new_score = int(float(new_score)) if new_score else 0
                                except Exception as e:
                                    logger.debug(f"Non-critical: failed to parse rescore value: {e}")
                                    new_score = 0
                                if new_score <= 0:
                                    new_score = min(95, max(25, len(skills) * 5 + exp_years * 3 + 20))
                                new_category = analysis_result.get('job_category', 'General')
                            except Exception:
                                # Rule-based scoring
                                new_score = min(95, max(25, len(skills) * 5 + exp_years * 3 + 20))
                                new_category = 'General'
                        
                        def _update_garbled_rescore(cid_val, score_val, cat_val):
                            with db_service.get_connection() as uc:
                                ucur = uc.cursor()
                                ucur.execute("""
                                    UPDATE candidates SET match_score = ?, job_category = ?, last_updated = ? WHERE id = ?
                                """, (score_val, cat_val, datetime.now().isoformat(), cid_val))
                                uc.commit()
                        await asyncio.to_thread(_update_garbled_rescore, cid, new_score, new_category)
                        
                        results['rescored'] += 1
                    except Exception as re_err:
                        results['errors'] += 1
                        logger.warning(f"Rescore error for {name}: {str(re_err)[:50]}")
        except Exception as se:
            logger.warning(f"Rescore phase error: {se}")
        
        return {
            "status": "success",
            "message": f"Reprocessed: {results['cleaned']} cleaned, {results['rescored']} rescored, {results['encoding_fixed']} encoding fixed",
            **results
        }
    except Exception as e:
        logger.error(f"Reprocess garbled error: {e}")
        raise HTTPException(500, "Error")


@app.post("/api/candidates/fix-summaries")
async def fix_garbage_summaries(current_user: dict = Depends(require_auth)):
    """
    Find all candidates with garbage summaries (raw email body text like 'Dear HR...')
    and regenerate proper summaries using Gemini AI or structured field generation.
    """
    try:
        from services.email_scraper import is_garbage_summary, generate_structured_summary, sanitize_summary
        
        def _fetch_all_for_summary_fix():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, summary, resume_text, skills, experience, location, education, job_category, job_subcategory FROM candidates WHERE is_active = 1")
                rows = cursor.fetchall()
                col_names = [desc[0] for desc in cursor.description]
                return [dict(zip(col_names, row)) for row in rows]
        
        candidates = await asyncio.to_thread(_fetch_all_for_summary_fix)
        
        garbage_count = 0
        fixed_ai = 0
        fixed_structured = 0
        cleared = 0
        
        # Initialize AI service
        ai_svc = None
        try:
            from services.gemini_service import GeminiService
            ai_svc = GeminiService()
            logger.info("🤖 Gemini AI available for summary regeneration")
        except Exception as e:
            logger.warning(f"Gemini not available, will use structured summaries: {e}")
        
        for candidate in candidates:
            summary = candidate.get('summary', '') or ''
            
            if not is_garbage_summary(summary):
                continue
            
            garbage_count += 1
            candidate_id = candidate['id']
            new_summary = ''
            
            # Parse skills back from JSON if stored as string
            skills = candidate.get('skills', '[]')
            if isinstance(skills, str):
                try:
                    skills = json.loads(skills)
                except:
                    skills = []
            
            # Try AI regeneration first if resume text is available
            resume_text = candidate.get('resume_text', '') or ''
            if ai_svc and resume_text and len(resume_text) > 50:
                try:
                    ai_result = await asyncio.wait_for(
                        ai_svc.analyze_candidate(resume_text),
                        timeout=30
                    )
                    if ai_result:
                        ai_summary = ai_result.get('summary', '')
                        if ai_summary and not is_garbage_summary(ai_summary):
                            new_summary = ai_summary
                            fixed_ai += 1
                            
                            # Also update skills/experience if AI provided better data
                            ai_skills = ai_result.get('skills', [])
                            if ai_skills and len(ai_skills) > len(skills):
                                def _update_skills(cid, sk):
                                    with db_service.get_connection() as conn:
                                        conn.execute("UPDATE candidates SET skills = ? WHERE id = ?", 
                                                    [json.dumps(sk), cid])
                                        conn.commit()
                                await asyncio.to_thread(_update_skills, candidate_id, ai_skills)
                except Exception as e:
                    logger.warning(f"AI summary regen failed for {candidate.get('name')}: {e}")
            
            # Fallback to structured summary from fields
            if not new_summary:
                candidate_data = {
                    'name': candidate.get('name', ''),
                    'skills': skills,
                    'experience': candidate.get('experience', 0),
                    'location': candidate.get('location', ''),
                    'education': candidate.get('education', ''),
                    'job_category': candidate.get('job_category', ''),
                    'job_subcategory': candidate.get('job_subcategory', ''),
                }
                new_summary = generate_structured_summary(candidate_data)
                if new_summary:
                    fixed_structured += 1
                else:
                    cleared += 1
            
            # Update in DB
            def _update_summary(cid, s):
                with db_service.get_connection() as conn:
                    conn.execute("UPDATE candidates SET summary = ? WHERE id = ?", [s, cid])
                    conn.commit()
            await asyncio.to_thread(_update_summary, candidate_id, new_summary)
        
        # Backup to GCS if we made changes
        if garbage_count > 0:
            try:
                from google.cloud import storage
                gcs_client = storage.Client()
                bucket = gcs_client.bucket(GCS_BUCKET_NAME)
                blob = bucket.blob('db/recruitment.db')
                blob.upload_from_filename('/app/data/recruitment.db')
                logger.info("☁️ Database backed up to GCS after summary fix")
            except Exception as e:
                logger.warning(f"GCS backup after summary fix failed: {e}")
        
        result = {
            "status": "success",
            "total_garbage_found": garbage_count,
            "fixed_with_ai": fixed_ai,
            "fixed_with_structured": fixed_structured,
            "cleared_empty": cleared,
            "message": f"Fixed {garbage_count} garbage summaries: {fixed_ai} via AI, {fixed_structured} via structured generation, {cleared} cleared"
        }
        logger.info(f"✅ Summary cleanup: {result['message']}")
        return result
        
    except Exception as e:
        logger.error(f"Fix summaries error: {e}")
        raise HTTPException(500, "Error")


@app.post("/api/candidates/reprocess-scores")
async def reprocess_candidate_scores(current_user: dict = Depends(require_auth)):
    """
    Reprocess all candidates with match_score = 0 to calculate proper AI scores.
    This fixes candidates that were imported before AI scoring was properly connected.
    """
    try:
        def _fetch_rescore_candidates():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                # Get candidates with 0 or very low scores
                cursor.execute("""
                    SELECT id, email, name, skills, summary, education, work_history, resume_text
                    FROM candidates 
                    WHERE match_score = 0 OR match_score IS NULL OR (match_score <= 35 AND (job_category = 'General' OR job_category IS NULL))
                    LIMIT 200
                """)
                return cursor.fetchall()
        rows = await asyncio.to_thread(_fetch_rescore_candidates)
        
        if not rows:
            return {"status": "success", "message": "No candidates need reprocessing", "processed": 0}
        
        # Prefer Gemini for reprocessing
        reprocess_ai = ai_service
        try:
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                reprocess_ai = gemini_svc
        except Exception as e:
            logger.debug(f"Non-critical: Gemini service not available for reprocessing: {e}")
        
        processed = 0
        errors = 0
        
        for row in rows:
            try:
                candidate_id, email, name, skills_json, summary, education, work_history, resume_text = row
                
                # Prefer resume_text from PDF parsing
                if resume_text and len(resume_text.strip()) > 50:
                    analysis_text = resume_text[:3000]
                else:
                    # Build text for AI analysis from available fields
                    try:
                        skills = json.loads(skills_json) if skills_json else []
                    except (json.JSONDecodeError, TypeError):
                        skills = []
                    text_parts = []
                    if summary:
                        text_parts.append(summary)
                    if skills and skills != ['R']:
                        text_parts.append(' '.join(str(s) for s in skills))
                    if education:
                        text_parts.append(education)
                    if work_history:
                        try:
                            wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                            for job in wh:
                                if isinstance(job, dict):
                                    text_parts.append(f"{job.get('title', '')} at {job.get('company', '')}")
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse work_history JSON: {e}")
                    analysis_text = ' '.join(text_parts)
                
                if len(analysis_text.strip()) < 10:
                    # Not enough text - assign low score
                    new_score = 15
                    new_category = 'Insufficient Data'
                else:
                    # Use AI to analyze (prefer Gemini)
                    try:
                        ai_result = await asyncio.wait_for(
                            reprocess_ai.analyze_candidate(analysis_text),
                            timeout=AI_ANALYSIS_TIMEOUT
                        )
                        new_score = ai_result.get('quality_score') or ai_result.get('match_score')
                        try:
                            new_score = int(float(new_score)) if new_score else 0
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse reprocess score: {e}")
                            new_score = 0
                        if new_score <= 0:
                            new_score = 15  # Minimal score — AI returned nothing useful
                        new_category = ai_result.get('job_category', 'General')
                    except Exception:
                        new_score = 15
                        new_category = 'General'
                
                # Update database
                def _update_rescore_db(new_score_val, new_category_val, cid_val):
                    with db_service.get_connection() as update_conn:
                        update_cursor = update_conn.cursor()
                        update_cursor.execute("""
                            UPDATE candidates SET match_score = ?, job_category = ? WHERE id = ?
                        """, (new_score_val, new_category_val, cid_val))
                        update_conn.commit()
                await asyncio.to_thread(_update_rescore_db, new_score, new_category, candidate_id)
                
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
        raise HTTPException(500, "Error reprocessing")


# ── Canonical category list + normalization map (backend-side) ──
CANONICAL_CATEGORIES = {
    'Software Engineering', 'Data & Analytics', 'IT & Systems', 'Engineering',
    'HR & Admin', 'Finance & Accounting', 'Sales', 'Operations',
    'Project Management', 'Consulting', 'Healthcare', 'Design & Creative',
    'QA & Testing', 'Marketing', 'Customer Service', 'Insurance & Safety',
    'Retail & Hospitality', 'Education', 'Legal', 'Business Analyst', 'General',
}

BACKEND_CATEGORY_NORMALIZE: dict = {
    'software development': 'Software Engineering', 'software engineer': 'Software Engineering',
    'software engineering': 'Software Engineering', 'web developer': 'Software Engineering',
    'web development': 'Software Engineering', 'frontend developer': 'Software Engineering',
    'front-end developer': 'Software Engineering', 'backend developer': 'Software Engineering',
    'full-stack developer': 'Software Engineering', 'full stack developer': 'Software Engineering',
    'mobile developer': 'Software Engineering', 'mobile development': 'Software Engineering',
    'devops engineer': 'Software Engineering', 'devops': 'Software Engineering',
    'cloud engineer': 'Software Engineering', 'rpa developer': 'Software Engineering',
    'developer': 'Software Engineering', 'programmer': 'Software Engineering',
    'data analyst': 'Data & Analytics', 'data science': 'Data & Analytics',
    'data scientist': 'Data & Analytics', 'data analytics': 'Data & Analytics',
    'data engineering': 'Data & Analytics', 'data engineer': 'Data & Analytics',
    'machine learning': 'Data & Analytics', 'ml engineer': 'Data & Analytics',
    'ai engineer': 'Data & Analytics', 'business intelligence': 'Data & Analytics',
    'bioinformatics': 'Data & Analytics',
    'it': 'IT & Systems', 'information technology': 'IT & Systems',
    'it support': 'IT & Systems', 'it/automation': 'IT & Systems',
    'network engineering': 'IT & Systems', 'network engineer': 'IT & Systems',
    'system administrator': 'IT & Systems', 'systems engineer': 'IT & Systems',
    'cybersecurity': 'IT & Systems', 'information security': 'IT & Systems',
    'database administrator': 'IT & Systems', 'technical support': 'IT & Systems',
    'engineering': 'Engineering', 'mechanical engineering': 'Engineering',
    'electrical engineering': 'Engineering', 'civil engineering': 'Engineering',
    'chemical engineering': 'Engineering', 'petroleum engineer': 'Engineering',
    'structural engineering': 'Engineering', 'mep engineer': 'Engineering',
    'hvac engineer': 'Engineering', 'construction': 'Engineering',
    'construction management': 'Engineering', 'architecture': 'Engineering',
    'oil & gas': 'Engineering', 'telecommunications': 'Engineering',
    'hr': 'HR & Admin', 'human resources': 'HR & Admin',
    'hr/admin': 'HR & Admin', 'administration': 'HR & Admin',
    'administrative': 'HR & Admin', 'talent acquisition': 'HR & Admin',
    'recruitment': 'HR & Admin', 'office administrator': 'HR & Admin',
    'executive assistant': 'HR & Admin',
    'finance': 'Finance & Accounting', 'accounting': 'Finance & Accounting',
    'accounting/finance': 'Finance & Accounting', 'financial services': 'Finance & Accounting',
    'audit': 'Finance & Accounting', 'banking': 'Finance & Accounting',
    'taxation': 'Finance & Accounting', 'bookkeeping': 'Finance & Accounting',
    'sales': 'Sales', 'business development': 'Sales',
    'sales & marketing': 'Sales', 'account management': 'Sales',
    'real estate': 'Sales', 'technical sales': 'Sales',
    'operations': 'Operations', 'supply chain': 'Operations',
    'supply chain management': 'Operations', 'logistics': 'Operations',
    'warehouse/logistics': 'Operations', 'procurement': 'Operations',
    'inventory management': 'Operations',
    'project management': 'Project Management', 'project manager': 'Project Management',
    'product management': 'Project Management', 'product manager': 'Project Management',
    'scrum master': 'Project Management', 'program manager': 'Project Management',
    'consulting': 'Consulting', 'management consulting': 'Consulting',
    'healthcare': 'Healthcare', 'medical': 'Healthcare',
    'nursing': 'Healthcare', 'pharmacy': 'Healthcare',
    'design': 'Design & Creative', 'graphic design': 'Design & Creative',
    'ui/ux': 'Design & Creative', 'interior design': 'Design & Creative',
    'media & creative': 'Design & Creative',
    'quality assurance': 'QA & Testing', 'qa': 'QA & Testing',
    'quality control': 'QA & Testing', 'testing': 'QA & Testing',
    'marketing': 'Marketing', 'digital marketing': 'Marketing',
    'social media': 'Marketing', 'content writing': 'Marketing',
    'public relations': 'Marketing', 'communications': 'Marketing',
    'customer service': 'Customer Service', 'customer support': 'Customer Service',
    'call center': 'Customer Service', 'client relations': 'Customer Service',
    'insurance': 'Insurance & Safety', 'safety officer': 'Insurance & Safety',
    'safety': 'Insurance & Safety', 'security': 'Insurance & Safety',
    'retail': 'Retail & Hospitality', 'hospitality': 'Retail & Hospitality',
    'hotel management': 'Retail & Hospitality', 'food & beverage': 'Retail & Hospitality',
    'education': 'Education', 'teaching': 'Education', 'training': 'Education',
    'legal': 'Legal', 'lawyer': 'Legal',
    'business analyst': 'Business Analyst', 'business analysis': 'Business Analyst',
    'systems analyst': 'Business Analyst', 'crm administrator/business analyst': 'Business Analyst',
    'insufficient data': 'General', 'other': 'General', 'n/a': 'General', 'not specified': 'General',
}

# Contains-based fuzzy fallback patterns (same as frontend)
BACKEND_CATEGORY_CONTAINS: list = [
    ('software', 'Software Engineering'), ('developer', 'Software Engineering'),
    ('full stack', 'Software Engineering'), ('full-stack', 'Software Engineering'),
    ('devops', 'Software Engineering'),
    ('data scien', 'Data & Analytics'), ('data analy', 'Data & Analytics'),
    ('data engineer', 'Data & Analytics'), ('machine learning', 'Data & Analytics'),
    ('business intelligence', 'Data & Analytics'),
    ('network', 'IT & Systems'), ('system admin', 'IT & Systems'),
    ('cyber', 'IT & Systems'), ('information technology', 'IT & Systems'),
    ('mechanical', 'Engineering'), ('electrical', 'Engineering'),
    ('civil', 'Engineering'), ('petroleum', 'Engineering'),
    ('structural', 'Engineering'), ('mep', 'Engineering'),
    ('construction', 'Engineering'),
    ('human resource', 'HR & Admin'), ('talent acqui', 'HR & Admin'),
    ('recruit', 'HR & Admin'), ('administrat', 'HR & Admin'),
    ('accounting', 'Finance & Accounting'), ('finance', 'Finance & Accounting'),
    ('audit', 'Finance & Accounting'), ('banking', 'Finance & Accounting'),
    ('sales', 'Sales'), ('business develop', 'Sales'), ('account manag', 'Sales'),
    ('supply chain', 'Operations'), ('logistics', 'Operations'),
    ('warehouse', 'Operations'), ('procurement', 'Operations'),
    ('operations', 'Operations'),
    ('project manag', 'Project Management'), ('program manag', 'Project Management'),
    ('product manag', 'Project Management'), ('scrum', 'Project Management'),
    ('consult', 'Consulting'),
    ('healthcare', 'Healthcare'), ('medical', 'Healthcare'),
    ('nurs', 'Healthcare'), ('pharma', 'Healthcare'),
    ('design', 'Design & Creative'), ('graphic', 'Design & Creative'),
    ('creative', 'Design & Creative'), ('ui/ux', 'Design & Creative'),
    ('quality assur', 'QA & Testing'), ('quality control', 'QA & Testing'),
    ('testing', 'QA & Testing'),
    ('marketing', 'Marketing'), ('social media', 'Marketing'),
    ('content writ', 'Marketing'), ('public relation', 'Marketing'),
    ('customer serv', 'Customer Service'), ('customer supp', 'Customer Service'),
    ('call center', 'Customer Service'),
    ('insurance', 'Insurance & Safety'), ('safety', 'Insurance & Safety'),
    ('retail', 'Retail & Hospitality'), ('hospitality', 'Retail & Hospitality'),
    ('hotel', 'Retail & Hospitality'),
    ('education', 'Education'), ('teaching', 'Education'), ('training', 'Education'),
    ('legal', 'Legal'), ('lawyer', 'Legal'),
    ('business analy', 'Business Analyst'),
]


def normalize_category_backend(raw: str) -> str:
    """Normalize a category name to a canonical category. Zero AI cost."""
    if not raw:
        return 'General'
    key = raw.lower().strip()
    # Already canonical?
    if raw in CANONICAL_CATEGORIES:
        return raw
    # Exact match
    if key in BACKEND_CATEGORY_NORMALIZE:
        return BACKEND_CATEGORY_NORMALIZE[key]
    # Contains-based fuzzy
    for pattern, category in BACKEND_CATEGORY_CONTAINS:
        if pattern in key:
            return category
    return raw  # Return as-is if no match


@app.post("/api/candidates/normalize-categories")
async def normalize_all_categories(current_user: dict = Depends(require_auth)):
    """
    Normalize ALL candidate categories to canonical names.
    This is a fast, zero-AI-cost operation that uses pattern matching.
    Fixes messy/duplicate category names across the entire database.
    """
    try:
        def _fetch_all_categories():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, name, job_category FROM candidates WHERE is_active = 1"
                )
                return cursor.fetchall()

        rows = await asyncio.to_thread(_fetch_all_categories)
        if not rows:
            return {"status": "success", "message": "No candidates found", "updated": 0}

        updated = 0
        changes: dict = {}

        for cid, name, raw_cat in rows:
            if not raw_cat:
                continue
            new_cat = normalize_category_backend(raw_cat)
            if new_cat != raw_cat:
                def _do_update(c_id, c_cat):
                    with db_service.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE candidates SET job_category = ? WHERE id = ?",
                            (c_cat, c_id),
                        )
                        conn.commit()
                await asyncio.to_thread(_do_update, cid, new_cat)
                updated += 1
                key = f"{raw_cat} → {new_cat}"
                changes[key] = changes.get(key, 0) + 1

        # Persist to GCS
        if updated > 0:
            try:
                await asyncio.to_thread(backup_db_to_gcs)
                logger.info(f"✅ GCS backup after category normalization ({updated} updates)")
            except Exception as e:
                logger.warning(f"Non-critical: GCS backup after normalize failed: {e}")

        return {
            "status": "success",
            "message": f"Normalized {updated} candidates' categories",
            "updated": updated,
            "total_checked": len(rows),
            "changes": changes,
        }

    except Exception as e:
        logger.error(f"Normalize categories error: {e}")
        raise HTTPException(500, "Error normalizing categories")


@app.post("/api/candidates/recategorize-general")
async def recategorize_general_candidates(current_user: dict = Depends(require_auth)):
    """
    Re-categorize all candidates with job_category='General' using skills-based classification.
    Uses the rule-based classify_job_title() from job_taxonomy for speed (no AI cost).
    Falls back to skills keyword matching when job title classification returns General.
    """
    try:
        from services.job_taxonomy import classify_job_title

        # Skills-to-category fallback mapping
        SKILL_CATEGORY_MAP = {
            "Software Engineer": ["python", "java", "javascript", "react", "angular", "vue", "node", "django",
                "flask", "spring", "c++", "c#", "go", "rust", "typescript", "php", "ruby", "swift",
                "kotlin", "flutter", "mobile", "android", "ios", "html", "css", "sass", "webpack",
                "rest", "graphql", "api", "microservices", "oop", ".net", "asp.net", "laravel",
                "rails", "next.js", "nuxt", "svelte", "redux", "sql", "nosql", "postgresql",
                "mysql", "mongodb", "redis", "elasticsearch", "software development", "full stack",
                "backend", "frontend", "web development"],
            "DevOps Engineer": ["docker", "kubernetes", "k8s", "terraform", "ansible", "jenkins",
                "ci/cd", "aws", "azure", "gcp", "linux", "devops", "helm", "prometheus", "grafana",
                "cloudformation", "puppet", "chef", "vagrant", "openshift", "argocd", "github actions"],
            "Data Scientist": ["machine learning", "deep learning", "tensorflow", "pytorch", "pandas",
                "numpy", "scikit", "data science", "nlp", "computer vision", "ml", "ai",
                "artificial intelligence", "data analysis", "tableau", "power bi", "r", "statistics",
                "regression", "classification", "neural network", "big data", "spark", "hadoop",
                "data engineering", "etl", "data pipeline", "airflow", "dbt"],
            "Cybersecurity": ["security", "penetration testing", "soc", "siem", "firewall", "ids",
                "ips", "cyber", "vulnerability", "infosec", "owasp", "nist", "iso 27001",
                "ethical hacking", "incident response", "malware", "encryption", "forensics"],
            "QA / Testing": ["selenium", "cypress", "testing", "qa", "jest", "junit", "testng",
                "automation testing", "manual testing", "quality assurance", "appium", "postman",
                "performance testing", "jmeter", "loadrunner", "regression testing", "sdet",
                "test plan", "test cases", "bug tracking"],
            "IT & Systems": ["system admin", "network", "cisco", "vmware", "active directory",
                "windows server", "it support", "helpdesk", "tcp/ip", "dns", "dhcp", "vpn",
                "firewall", "cloud architecture", "solutions architect", "enterprise", "sap",
                "erp", "crm", "dynamics", "dba", "database admin", "oracle", "sharepoint"],
            "Product Manager": ["product management", "roadmap", "user stories", "backlog",
                "product strategy", "mvp", "stakeholder", "product owner", "product lifecycle"],
            "Design": ["figma", "sketch", "adobe xd", "photoshop", "illustrator", "ui", "ux",
                "user experience", "user interface", "wireframe", "prototype", "design system",
                "indesign", "after effects", "graphic design", "web design", "responsive design"],
            "Project Management": ["project management", "pmp", "prince2", "agile", "scrum",
                "kanban", "jira", "confluence", "gantt", "risk management", "scrum master",
                "sprint planning", "waterfall", "program management", "delivery management"],
            "Business Analyst": ["business analysis", "requirements gathering", "brd", "frd",
                "use case", "process mapping", "gap analysis", "swimlane", "visio", "bpmn",
                "systems analysis"],
            "Marketing": ["marketing", "seo", "sem", "google ads", "facebook ads", "social media",
                "content marketing", "email marketing", "digital marketing", "analytics",
                "google analytics", "hubspot", "mailchimp", "brand", "campaign", "ppc", "crm marketing"],
            "Sales": ["sales", "salesforce", "crm", "business development", "lead generation",
                "pipeline", "account management", "b2b", "b2c", "cold calling", "negotiation"],
            "Finance": ["finance", "accounting", "budgeting", "forecasting", "financial modeling",
                "excel", "quickbooks", "tally", "gst", "taxation", "audit", "ifrs", "gaap",
                "accounts payable", "accounts receivable", "bookkeeping", "ebitda"],
            "HR": ["human resources", "recruitment", "talent acquisition", "onboarding",
                "performance management", "hris", "payroll", "employee engagement", "l&d",
                "compensation", "benefits", "workday", "bamboohr", "succession planning"],
            "Content & Communications": ["content writing", "copywriting", "blogging", "editing",
                "journalism", "public relations", "corporate communications", "technical writing",
                "documentation", "newsletter"],
            "Healthcare": ["healthcare", "medical", "clinical", "nursing", "pharmacy", "patient care",
                "ehr", "hipaa", "biomedical", "diagnosis", "treatment", "hospital"],
            "Education": ["teaching", "curriculum", "e-learning", "lms", "moodle", "education",
                "tutoring", "instructional design", "training", "course development"],
            "Engineering": ["mechanical", "electrical", "civil", "structural", "cad", "autocad",
                "solidworks", "matlab", "aerospace", "robotics", "plc", "scada", "manufacturing",
                "industrial engineering"],
            "Customer Support": ["customer service", "customer success", "call center", "zendesk",
                "freshdesk", "live chat", "ticketing", "client relations", "help desk support"],
            "Media & Creative": ["video editing", "premiere pro", "final cut", "animation",
                "3d modeling", "blender", "maya", "photography", "lightroom", "motion graphics"],
            "Operations": ["operations", "supply chain", "logistics", "procurement", "inventory",
                "warehouse", "vendor management", "lean", "six sigma", "kaizen"],
            "Consulting": ["consulting", "strategy", "business consulting", "management consulting",
                "advisory", "change management", "transformation"],
        }

        def _fetch_general_candidates():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, skills, summary, work_history, resume_text
                    FROM candidates 
                    WHERE job_category = 'General' OR job_category IS NULL OR job_category = ''
                """)
                return cursor.fetchall()

        rows = await asyncio.to_thread(_fetch_general_candidates)

        if not rows:
            return {"status": "success", "message": "No General category candidates found", "updated": 0}

        updated = 0
        still_general = 0
        category_counts = {}

        for row in rows:
            try:
                candidate_id, name, skills_json, summary, work_history, resume_text = row

                # Build comprehensive text for classification
                text_parts = []

                # 1. Try work history job titles first (best signal)
                if work_history:
                    try:
                        wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                        if isinstance(wh, list):
                            for job in wh:
                                if isinstance(job, dict):
                                    title = job.get('title', '') or job.get('position', '')
                                    if title:
                                        text_parts.append(title)
                    except Exception:
                        pass

                # 2. Summary
                if summary:
                    text_parts.append(summary[:200])

                # Try classify_job_title with work history titles
                new_category = "General"
                new_subcategory = "Other"
                combined_text = ' '.join(text_parts)

                if combined_text.strip():
                    cat, sub = classify_job_title(combined_text)
                    if cat != "General":
                        new_category = cat
                        new_subcategory = sub

                # 3. If still General, try skills-based matching
                if new_category == "General":
                    skills_list = []
                    if skills_json:
                        try:
                            skills_list = json.loads(skills_json) if isinstance(skills_json, str) else skills_json
                            if isinstance(skills_list, list):
                                skills_list = [s.lower().strip() for s in skills_list if isinstance(s, str)]
                        except Exception:
                            skills_list = []

                    # Also extract text from resume
                    all_text = ' '.join(skills_list)
                    if resume_text:
                        all_text += ' ' + resume_text[:500].lower()
                    if summary:
                        all_text += ' ' + summary.lower()

                    best_match = "General"
                    best_score = 0
                    for category, keywords in SKILL_CATEGORY_MAP.items():
                        score = sum(1 for kw in keywords if kw in all_text)
                        if score > best_score:
                            best_score = score
                            best_match = category
                    
                    if best_score >= 2:  # Need at least 2 skill matches
                        new_category = best_match
                        from services.job_taxonomy import _match_subcategory
                        new_subcategory = _match_subcategory(all_text, new_category)

                if new_category != "General":
                    def _update_category(cid, cat, sub):
                        with db_service.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                UPDATE candidates SET job_category = ?, job_subcategory = ? WHERE id = ?
                            """, (cat, sub, cid))
                            conn.commit()
                    await asyncio.to_thread(_update_category, candidate_id, new_category, new_subcategory)
                    updated += 1
                    category_counts[new_category] = category_counts.get(new_category, 0) + 1
                    logger.info(f"✅ Re-categorized {name}: {new_category} / {new_subcategory}")
                else:
                    still_general += 1

            except Exception as e:
                logger.warning(f"Error re-categorizing {row[1]}: {str(e)[:80]}")

        # Persist changes to GCS
        if updated > 0:
            try:
                await asyncio.to_thread(backup_db_to_gcs)
                logger.info(f"✅ GCS backup after recategorization ({updated} updates)")
            except Exception as e:
                logger.warning(f"Non-critical: GCS backup after recategorize failed: {e}")

        return {
            "status": "success",
            "message": f"Re-categorized {updated} candidates, {still_general} remain General",
            "updated": updated,
            "still_general": still_general,
            "category_breakdown": category_counts
        }

    except Exception as e:
        logger.error(f"Recategorize error: {str(e)}")
        raise HTTPException(500, "Error re-categorizing")


@app.post("/api/candidates/reprocess-with-gemini")
async def reprocess_candidates_with_gemini(current_user: dict = Depends(require_auth)):
    """
    Bulk reprocess ALL poorly-scored candidates using Gemini AI.
    Targets candidates with: score <= 35, category='General', minimal skills like '["R"]'.
    Uses resume_text (from PDF parsing) for best results. Cost-optimized with batching and delays.
    """
    try:
        gemini_svc = get_gemini_service()
        if not gemini_svc or not gemini_svc.available:
            raise HTTPException(503, "Gemini service not available")
        
        def _fetch_gemini_rescore():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, email, skills, summary, education, work_history, 
                           experience, resume_text, match_score, job_category
                    FROM candidates 
                    WHERE is_active = 1
                    AND (
                        (match_score <= 35 AND match_score > 0)
                        OR (job_category = 'General' AND match_score <= 50)
                        OR (skills IS NULL OR skills = '' OR skills = '[]' OR skills = '["R"]' OR skills = '["r"]')
                    )
                    ORDER BY created_at DESC
                    LIMIT 500
                """)
                return cursor.fetchall()
        rows = await asyncio.to_thread(_fetch_gemini_rescore)
        
        if not rows:
            return {"status": "success", "message": "No candidates need Gemini reprocessing", "processed": 0, "total_found": 0}
        
        total_found = len(rows)
        processed = 0
        improved = 0
        errors = 0
        
        logger.info(f"🚀 Gemini bulk reprocess: Found {total_found} poorly-scored candidates")
        
        for row in rows:
            try:
                candidate_id = row[0]
                name = row[1] or 'Unknown'
                email = row[2] or ''
                skills_json = row[3] or '[]'
                summary = row[4] or ''
                education = row[5] or ''
                work_history = row[6] or ''
                experience = row[7] or 0
                resume_text = row[8] or ''
                old_score = row[9] or 0
                old_category = row[10] or 'General'
                
                # Build the best possible analysis text — prefer resume_text (from PDF)
                text_parts = []
                if resume_text and len(resume_text.strip()) > 50:
                    text_parts.append(resume_text[:3000])
                else:
                    # Fallback: reconstruct from fields
                    if name and name != 'Unknown':
                        text_parts.append(f"Name: {name}")
                    if summary:
                        text_parts.append(f"Summary: {summary}")
                    try:
                        skills = json.loads(skills_json) if isinstance(skills_json, str) else skills_json
                        if skills and isinstance(skills, list) and skills != ['R']:
                            text_parts.append(f"Skills: {', '.join(skills)}")
                    except Exception as e:
                        logger.debug(f"Non-critical: failed to parse skills JSON: {e}")
                    if education:
                        text_parts.append(f"Education: {education}")
                    if work_history:
                        try:
                            wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                            for job in (wh if isinstance(wh, list) else []):
                                if isinstance(job, dict):
                                    text_parts.append(f"Work: {job.get('title', '')} at {job.get('company', '')} ({job.get('duration', '')})")
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to parse work_history JSON: {e}")
                
                analysis_text = '\n'.join(text_parts)
                
                if len(analysis_text.strip()) < 20:
                    # Not enough data to analyze — set a low score honestly
                    def _set_insufficient_data(cid):
                        with db_service.get_connection() as uc:
                            ucur = uc.cursor()
                            ucur.execute("""
                                UPDATE candidates SET match_score = 15, job_category = 'Insufficient Data',
                                last_updated = datetime('now') WHERE id = ?
                            """, (cid,))
                            uc.commit()
                    await asyncio.to_thread(_set_insufficient_data, candidate_id)
                    continue
                
                # Call Gemini for analysis
                result = await asyncio.wait_for(
                    gemini_svc.analyze_candidate(analysis_text),
                    timeout=60
                )
                
                if result:
                    new_score = result.get('quality_score') or result.get('match_score') or result.get('overall_score')
                    try:
                        new_score = int(float(new_score)) if new_score else 0
                        new_score = max(10, min(95, new_score))  # Sane bounds
                    except (ValueError, TypeError):
                        new_score = 0
                    if new_score <= 0:
                        # Calculate from AI-extracted data
                        _sk = result.get('skills', [])
                        _ex = result.get('experience', 0) or 0
                        try: _ex = int(float(_ex))
                        except: _ex = 0
                        new_score = max(15, min(90, 25 + len(_sk) * 3 + _ex * 3 + (10 if result.get('education') else 0)))
                    
                    new_category = result.get('job_category', result.get('category', '')) or 'General'
                    new_skills = result.get('skills', [])
                    new_experience = result.get('experience', 0)
                    new_summary = result.get('summary', '') or ''
                    
                    try:
                        new_experience = int(float(new_experience)) if new_experience else 0
                    except (ValueError, TypeError):
                        new_experience = 0
                    
                    skills_str = json.dumps(new_skills) if isinstance(new_skills, list) and new_skills else None
                    
                    def _update_gemini_result(ns, nc, ss, ne, nsm, jr, cid):
                        with db_service.get_connection() as uc:
                            ucur = uc.cursor()
                            ucur.execute("""
                                UPDATE candidates 
                                SET match_score = ?,
                                    job_category = CASE WHEN ? != '' AND ? != 'General' THEN ? ELSE job_category END,
                                    skills = CASE WHEN ? IS NOT NULL AND length(?) > 4 THEN ? ELSE skills END,
                                    experience = CASE WHEN ? > 0 THEN MAX(COALESCE(experience, 0), ?) ELSE experience END,
                                    summary = CASE WHEN ? != '' AND length(?) > length(COALESCE(summary, '')) THEN ? ELSE summary END,
                                    ai_analysis = ?,
                                    last_updated = datetime('now')
                                WHERE id = ?
                            """, (ns,
                                  nc, nc, nc,
                                  ss, ss, ss,
                                  ne, ne,
                                  nsm, nsm, nsm,
                                  jr,
                                  cid))
                            uc.commit()
                    await asyncio.to_thread(_update_gemini_result, new_score,
                                           new_category, skills_str, new_experience,
                                           new_summary, json.dumps(result), candidate_id)
                    
                    if new_score > old_score:
                        improved += 1
                    processed += 1
                    
                    if processed % 25 == 0:
                        logger.info(f"📊 Gemini reprocess progress: {processed}/{total_found} done, {improved} improved")
                
                # Rate limit: 1.5 second delay between Gemini calls (cost optimization)
                await asyncio.sleep(1.5)
                
            except asyncio.TimeoutError:
                errors += 1
                logger.warning(f"⏳ Gemini timeout for {name}")
            except Exception as e:
                errors += 1
                logger.warning(f"⚠️ Gemini reprocess error for {name}: {str(e)[:100]}")
                await asyncio.sleep(1)
        
        # Backup after bulk reprocessing
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, backup_db_to_gcs)
            logger.info("💾 Database backed up after Gemini reprocessing")
        except Exception as backup_err:
            logger.warning(f"Backup after reprocess failed: {backup_err}")
        
        logger.info(f"✅ Gemini bulk reprocess complete: {processed} processed, {improved} improved, {errors} errors out of {total_found}")
        
        return {
            "status": "success",
            "message": f"Gemini reprocessed {processed} candidates, {improved} improved scores",
            "total_found": total_found,
            "processed": processed,
            "improved": improved,
            "errors": errors
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Gemini bulk reprocess error: {str(e)}")
        raise HTTPException(500, "Error")


@app.get("/api/candidates/{candidate_id}")
async def get_candidate(candidate_id: str, current_user: dict = Depends(require_auth)):
    """Get single candidate by ID"""
    try:
        def _get_candidate_by_id():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
                row = cursor.fetchone()
            if not row:
                return None
            return db_service._row_to_candidate(row)
        candidate = await asyncio.to_thread(_get_candidate_by_id)
        if candidate is None:
            raise HTTPException(404, "Candidate not found")
        return candidate
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Error")


@app.get("/api/candidates/{candidate_id}/resume")
async def download_resume(candidate_id: str, current_user: dict = Depends(require_auth)):
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
                'Content-Disposition': f'attachment; filename="{os.path.basename(resume.get("filename") or "resume.pdf")}"'
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume download error: {str(e)}")
        raise HTTPException(500, "Error downloading resume")


@app.post("/api/candidates/{candidate_id}/resume")
async def upload_resume_for_candidate(candidate_id: str, file: UploadFile = File(...), current_user: dict = Depends(require_auth)):
    """Upload a resume file for an existing candidate. Also re-parses the resume and updates candidate data."""
    try:
        filename = file.filename or "resume.pdf"
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in ('pdf', 'docx', 'doc'):
            raise HTTPException(400, "Only PDF and DOCX files are supported.")
        
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(400, "File too large. Max 10MB.")
        if len(content) < 100:
            raise HTTPException(400, "File too small or empty.")
        
        # Verify candidate exists
        def _check_exists():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name FROM candidates WHERE id = ?", [candidate_id])
                return cursor.fetchone()
        
        existing = await asyncio.to_thread(_check_exists)
        if not existing:
            raise HTTPException(404, "Candidate not found")
        
        # Save the resume binary file
        content_type = 'application/pdf' if ext == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        await asyncio.to_thread(db_service.save_resume, candidate_id, filename, content, content_type)
        
        # Parse the resume to extract text and structured data
        try:
            parsed = await resume_parser.parse_resume(content, filename)
            resume_text = parsed.get('raw_text', '') or ''
            
            updates = {}
            if resume_text:
                updates['resume_text'] = resume_text[:10000]
            
            # Run AI analysis on the resume text
            if resume_text and len(resume_text) > 50:
                try:
                    ai_result = await asyncio.wait_for(
                        ai_service.analyze_candidate(resume_text),
                        timeout=AI_ANALYSIS_TIMEOUT
                    )
                    if ai_result:
                        if ai_result.get('summary'):
                            from services.email_scraper import is_garbage_summary
                            if not is_garbage_summary(ai_result['summary']):
                                updates['summary'] = ai_result['summary']
                        if ai_result.get('skills'):
                            updates['skills'] = json.dumps(ai_result['skills'])
                        if ai_result.get('experience'):
                            updates['experience'] = ai_result['experience']
                        if ai_result.get('quality_score'):
                            updates['match_score'] = ai_result['quality_score']
                        if ai_result.get('job_category') and ai_result['job_category'] != 'General':
                            updates['job_category'] = ai_result['job_category']
                        if ai_result.get('job_subcategory'):
                            updates['job_subcategory'] = ai_result['job_subcategory']
                        if ai_result.get('education'):
                            updates['education'] = json.dumps(ai_result['education']) if isinstance(ai_result['education'], list) else ai_result['education']
                        if ai_result.get('location'):
                            updates['location'] = ai_result['location']
                        if ai_result.get('phone'):
                            updates['phone'] = ai_result['phone']
                        if ai_result.get('certifications'):
                            updates['certifications'] = json.dumps(ai_result['certifications'])
                        if ai_result.get('languages'):
                            updates['languages'] = json.dumps(ai_result['languages'])
                except Exception as ai_err:
                    logger.warning(f"AI analysis failed for uploaded resume: {ai_err}")
            
            # Update candidate with extracted data
            if updates:
                def _update_candidate():
                    with db_service.get_connection() as conn:
                        set_clause = ', '.join(f"{k} = ?" for k in updates.keys())
                        values = list(updates.values()) + [candidate_id]
                        conn.execute(f"UPDATE candidates SET {set_clause} WHERE id = ?", values)
                        conn.commit()
                await asyncio.to_thread(_update_candidate)
                
        except Exception as parse_err:
            logger.warning(f"Resume parsing failed (file still saved): {parse_err}")
        
        # Backup to GCS
        try:
            await asyncio.to_thread(backup_db_to_gcs)
        except Exception:
            pass
        
        logger.info(f"✅ Resume uploaded for candidate {candidate_id}: {filename}")
        return {"status": "success", "message": f"Resume '{filename}' uploaded successfully", "candidate_id": candidate_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resume upload for candidate error: {e}")
        raise HTTPException(500, "Error")


# Resume upload endpoints
@app.post("/api/resumes/upload")
async def upload_resume(file: UploadFile = File(...), current_user: dict = Depends(require_auth)):
    """
    Upload a single resume file (PDF or DOCX).
    Parses the resume, runs AI analysis, and saves the candidate.
    """
    try:
        filename = file.filename or "unknown.pdf"
        # Sanitize filename: strip path components and restrict to safe characters
        filename = os.path.basename(filename)
        filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
        if len(filename) > 255:
            filename = filename[:255]
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
        ai_score = None
        job_category = 'General'
        job_subcategory = ''
        summary = parsed.get('summary', '')

        if resume_text.strip():
            try:
                ai_analysis = await asyncio.wait_for(
                    ai_service.analyze_candidate(resume_text),
                    timeout=AI_ANALYSIS_TIMEOUT
                )
                if ai_analysis:
                    ai_score = ai_analysis.get('quality_score')
                    job_category = ai_analysis.get('job_category', 'General')
                    job_subcategory = ai_analysis.get('job_subcategory', '')
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
                    # Prefer AI-extracted contact info if parser missed it
                    if not parsed.get('phone') and ai_analysis.get('phone'):
                        parsed['phone'] = ai_analysis['phone']
                    if not parsed.get('location') and ai_analysis.get('location'):
                        parsed['location'] = ai_analysis['location']
                    if not parsed.get('name', '').strip() or parsed.get('name') == 'Unknown':
                        if ai_analysis.get('name'):
                            parsed['name'] = ai_analysis['name']
                    if ai_analysis.get('certifications'):
                        parsed['certifications'] = ai_analysis['certifications']
                    if ai_analysis.get('languages'):
                        parsed['languages'] = ai_analysis['languages']
                    if ai_analysis.get('linkedin'):
                        parsed['linkedin'] = ai_analysis['linkedin']
            except Exception as ai_err:
                logger.warning(f"AI analysis failed for upload: {str(ai_err)[:100]}")

        # Calculate fallback score if AI didn't return one
        if ai_score is None:
            skills_count = len(parsed.get('skills', []))
            exp = parsed.get('experience', 0) or 0
            if isinstance(exp, str):
                nums = re.findall(r'\d+', str(exp))
                exp = int(nums[0]) if nums else 0
            has_edu = bool(parsed.get('education'))
            ai_score = 25 + min(30, skills_count * 3) + min(25, exp * 3) + (10 if has_edu else 0)
            ai_score = min(90, max(15, ai_score))
            logger.info(f"📊 Calculated fallback score for upload: {ai_score} (skills={skills_count}, exp={exp})")

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
            'job_subcategory': job_subcategory or parsed.get('job_subcategory', ''),
            'appliedDate': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'raw_email_subject': f"Resume Upload: {filename}",
            'certifications': parsed.get('certifications', []),
            'languages': parsed.get('languages', []),
            'resume_text': resume_text[:10000] if resume_text else '',
        }

        # Check if candidate with same email exists
        existing = await asyncio.to_thread(db_service.get_candidate_by_email, parsed['email'])
        if existing:
            candidate['id'] = existing['id']
            await asyncio.to_thread(db_service.update_candidate, candidate)
            logger.info(f"📝 Updated candidate from upload: {candidate['name']}")
        else:
            await asyncio.to_thread(db_service.insert_candidate, candidate)
            logger.info(f"✨ New candidate from upload: {candidate['name']} ({candidate['email']}) - Score: {ai_score}")

        # Save detailed AI analysis if available
        if resume_text.strip():
            try:
                await asyncio.to_thread(db_service.save_ai_analysis, candidate['id'], {
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
            await asyncio.to_thread(db_service.save_resume, candidate['id'], filename, content, content_type)
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
        raise HTTPException(500, "Error processing resume")


@app.post("/api/resumes/upload-multiple")
async def upload_multiple_resumes(files: List[UploadFile] = File(...), current_user: dict = Depends(require_auth)):
    """
    Upload multiple resume files at once.
    Returns results for each file.
    """
    if len(files) > 50:
        raise HTTPException(400, "Too many files. Maximum 50 per upload.")
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
                file_hash = hashlib.sha256(content[:1024]).hexdigest()[:8]
                clean_name = re.sub(r'[^a-zA-Z]', '', parsed.get('name', ''))[:20] or 'candidate'
                parsed['email'] = f"{clean_name.lower()}.{file_hash}@uploaded.local"

            candidate_id = f"upload_{parsed['email']}_{int(datetime.now().timestamp())}"

            resume_text = parsed.get('raw_text', '') or parsed.get('summary', '')
            ai_score = None
            job_category = 'General'
            job_subcategory = ''
            summary = parsed.get('summary', '')

            if resume_text.strip():
                try:
                    ai_analysis = await asyncio.wait_for(
                        ai_service.analyze_candidate(resume_text),
                        timeout=AI_ANALYSIS_TIMEOUT
                    )
                    if ai_analysis:
                        ai_score = ai_analysis.get('quality_score')
                        job_category = ai_analysis.get('job_category', 'General')
                        job_subcategory = ai_analysis.get('job_subcategory', '')
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
                        # Prefer AI-extracted contact info if parser missed it
                        if not parsed.get('phone') and ai_analysis.get('phone'):
                            parsed['phone'] = ai_analysis['phone']
                        if not parsed.get('location') and ai_analysis.get('location'):
                            parsed['location'] = ai_analysis['location']
                        if not parsed.get('name', '').strip() or parsed.get('name') == 'Unknown':
                            if ai_analysis.get('name'):
                                parsed['name'] = ai_analysis['name']
                        if ai_analysis.get('certifications'):
                            parsed['certifications'] = ai_analysis['certifications']
                        if ai_analysis.get('languages'):
                            parsed['languages'] = ai_analysis['languages']
                        if ai_analysis.get('linkedin'):
                            parsed['linkedin'] = ai_analysis['linkedin']
                except Exception as e:
                    logger.warning(f"AI analysis failed for multi-upload {filename}: {e}")

            # Calculate fallback score if AI didn't return one
            if ai_score is None:
                skills_count = len(parsed.get('skills', []))
                exp = parsed.get('experience', 0) or 0
                if isinstance(exp, str):
                    nums = re.findall(r'\d+', str(exp))
                    exp = int(nums[0]) if nums else 0
                has_edu = bool(parsed.get('education'))
                ai_score = 25 + min(30, skills_count * 3) + min(25, exp * 3) + (10 if has_edu else 0)
                ai_score = min(90, max(15, ai_score))

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
                'job_subcategory': job_subcategory or parsed.get('job_subcategory', ''),
                'appliedDate': datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                'raw_email_subject': f"Resume Upload: {filename}",
                'certifications': parsed.get('certifications', []),
                'languages': parsed.get('languages', []),
                'resume_text': resume_text[:10000] if resume_text else '',
            }

            existing = await asyncio.to_thread(db_service.get_candidate_by_email, parsed['email'])
            if existing:
                candidate['id'] = existing['id']
                await asyncio.to_thread(db_service.update_candidate, candidate)
            else:
                await asyncio.to_thread(db_service.insert_candidate, candidate)

            # Save AI analysis
            try:
                await asyncio.to_thread(db_service.save_ai_analysis, candidate['id'], {
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
                logger.warning(f"Failed to save AI analysis for {candidate.get('id', 'unknown')}: {e}")

            # Save resume file
            try:
                await asyncio.to_thread(db_service.save_resume, candidate['id'], filename, content)
            except Exception as e:
                logger.warning(f"Failed to save resume file {filename}: {e}")

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
async def get_candidate_deep_analysis(candidate_id: str, current_user: dict = Depends(require_auth)):
    """
    Get deep AI analysis of a candidate including:
    - Detailed pros and cons
    - Career trajectory analysis
    - Hiring recommendation
    - Interview focus areas
    """
    try:
        # Get candidate from database
        def _get_candidate_for_analysis():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
                row = cursor.fetchone()
            if not row:
                return None
            return db_service._row_to_candidate(row)
        candidate = await asyncio.to_thread(_get_candidate_for_analysis)
        
        if not candidate:
            raise HTTPException(404, "Candidate not found")
        
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
        
        # TIER 2: Basic fallback — No AI
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
                "AI analysis unavailable - configure Gemini or Ollama"
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
        raise HTTPException(500, "Error analyzing candidate")


@app.post("/api/ai/match-job-file")
async def match_candidates_to_job_file(
    file: UploadFile = File(None),
    job_description: str = Form(None),
    top_n: int = Form(10),
    current_user: dict = Depends(require_auth),
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

        # TIER 2: Enhanced keyword matching fallback
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
        raise HTTPException(500, "Error matching candidates to job description")


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
        if len(job_description) > 50000:
            raise HTTPException(400, "Job description too long (max 50,000 characters)")
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
        
        # TIER 2: Basic keyword matching fallback
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
        raise HTTPException(500, "Error matching candidates")


@app.post("/api/ai/compare-candidates")
async def compare_candidates(
    candidate_ids: List[str] = Body(..., embed=True),
    job_description: Optional[str] = Body(None, embed=True),
    current_user: dict = Depends(require_auth),
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
        def _get_candidates_for_comparison():
            candidates = []
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                for cid in candidate_ids:
                    cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (cid,))
                    row = cursor.fetchone()
                    if row:
                        candidates.append(db_service._row_to_candidate(row))
            return candidates
        candidates = await asyncio.to_thread(_get_candidates_for_comparison)
        
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
        
        # TIER 2: Rule-based fallback
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
            "recommendation": "Configure Ollama or Gemini API key for detailed comparison",
            "ai_powered": False,
            "source": "rule_based"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        raise HTTPException(500, "Error comparing candidates")


@app.post("/api/ai/chat")
async def ai_chat(
    message: str = Body(..., embed=True),
    include_candidates: bool = Body(True, embed=True),
    conversation_history: list = Body([], embed=True),
    num_candidates: int = Body(15, embed=True),
    current_user: dict = Depends(require_auth)
):
    """
    Enhanced AI chat with full database search capability.
    2-STAGE APPROACH: Pre-filter candidates by query relevance → Send subset to AI
    3-TIER FALLBACK: Gemini → Ollama → Rule-based
    """
    # ── Input validation ──
    if not message or not message.strip():
        raise HTTPException(400, "Message cannot be empty")
    message = message.strip()
    if len(message) > 2000:
        raise HTTPException(400, "Message too long (max 2000 characters)")
    num_candidates = max(1, min(num_candidates, 50))  # Clamp 1-50
    conversation_history = (conversation_history or [])[-10:]  # Keep last 10 turns max

    # ── Simple rate limiting (10 requests per minute per user) ──
    import time as _time
    user_id = current_user.get("id", "anon")
    if not hasattr(app.state, "_chat_rate_limits"):
        app.state._chat_rate_limits = {}
    now = _time.time()
    user_hits = app.state._chat_rate_limits.get(user_id, [])
    user_hits = [t for t in user_hits if now - t < 60]  # Keep last 60s
    if len(user_hits) >= 10:
        raise HTTPException(429, "Too many requests. Please wait a moment before sending another message.")
    user_hits.append(now)
    app.state._chat_rate_limits[user_id] = user_hits
    # Periodic cleanup: remove stale users (every ~100 requests)
    if len(app.state._chat_rate_limits) > 100:
        app.state._chat_rate_limits = {
            uid: hits for uid, hits in app.state._chat_rate_limits.items()
            if any(now - t < 60 for t in hits)
        }

    try:
        candidates_data = None
        context = None
        
        if include_candidates:
            stats = await asyncio.to_thread(db_service.get_statistics)
            # Fetch ALL candidates so the Gemini pre-filter can search the ENTIRE database.
            # The pre-filter scores every candidate by query relevance (Python-side, no AI cost)
            # and only sends the top 150 most relevant to the Gemini prompt.
            # Token cost stays the same (~$0.001/request) regardless of DB size.
            candidates = await asyncio.to_thread(
                db_service.get_candidates_for_ai, {}, None
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
                        cands = gemini_result.get('candidates_lookup', [])
                        # Save to search history (fire-and-forget)
                        try:
                            import uuid as _uuid_mod
                            top3 = [{"name": c.get("name",""), "score": c.get("matchScore", 0), "id": c.get("id","")} for c in cands[:3]]
                            await asyncio.to_thread(
                                db_service.save_search,
                                str(_uuid_mod.uuid4())[:12], message,
                                (cands[0].get("jobCategory","") if cands else ""),
                                len(cands), top3, current_user.get("sub","")
                            )
                        except Exception as e:
                            logger.debug(f"Non-critical: save_search failed: {e}")
                        return {
                            "response": gemini_result.get('response', ''),
                            "ai_powered": True,
                            "context_included": include_candidates,
                            "source": "gemini",
                            "candidates_lookup": cands
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
            import traceback as _tb
            logger.warning(f"Gemini chat error: {gemini_err}\n{_tb.format_exc()}")
        
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
        
        # TIER 3: Rule-based fallback
        return {
            "response": f"I understand you're asking about: '{message}'. Currently no AI services are available. "
                        f"Please configure GEMINI_API_KEY or Ollama for intelligent responses.",
            "ai_powered": False,
            "context_included": include_candidates,
            "source": "rule_based"
        }
        
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        raise HTTPException(status_code=500, detail="AI chat service temporarily unavailable")


# ============================================================================
# REAL-TIME STATS ENDPOINT - For live updates
# ============================================================================

@app.get("/api/stats/live")
async def get_live_stats(current_user: dict = Depends(require_auth)):
    """
    Get real-time statistics for dashboard updates.
    Lightweight endpoint for frequent polling.
    """
    try:
        def _get_live_stats_db():
            with db_service.get_connection() as conn:
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


# JOB DESCRIPTIONS - REMOVED
# System now auto-generates job categories from candidate emails
# No manual job description upload needed

# @app.post("/api/job-descriptions/analyze")
# @app.post("/api/job-descriptions/upload")
# ^^^ REMOVED - Auto-categorization via AI

# ── Search History API ──────────────────────────────────────────────────
@app.get("/api/search-history")
async def get_search_history(limit: int = 50, current_user: dict = Depends(require_auth)):
    """Get search history for reports page"""
    try:
        history = await asyncio.to_thread(db_service.get_search_history, limit)
        return {"history": history, "total": len(history)}
    except Exception as e:
        logger.error(f"Search history error: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve search history")

@app.delete("/api/search-history")
async def clear_search_history(current_user: dict = Depends(require_auth)):
    """Clear all search history"""
    try:
        await asyncio.to_thread(db_service.clear_search_history)
        return {"status": "success", "message": "Search history cleared"}
    except Exception as e:
        logger.error(f"Clear search history error: {e}")
        return {"status": "error", "message": "Failed to clear search history"}

@app.delete("/api/search-history/{entry_id}")
async def delete_search_entry(entry_id: str, current_user: dict = Depends(require_auth)):
    """Delete a single search history entry"""
    try:
        deleted = await asyncio.to_thread(db_service.delete_search_entry, entry_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Search entry {entry_id} not found")
        return {"status": "success", "message": f"Search entry {entry_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete search entry error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete search entry")

# ── Pipeline Stats API ──────────────────────────────────────────────────
@app.get("/api/stats/pipeline")
async def get_pipeline_stats(current_user: dict = Depends(require_auth)):
    """Get pipeline status counts for dashboard"""
    try:
        counts = await asyncio.to_thread(db_service.get_pipeline_counts)
        stats = await asyncio.to_thread(db_service.get_statistics)
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

# ── JD Generator API ──────────────────────────────────────────────────
@app.post("/api/jd/generate")
async def generate_job_description(
    title: str = Body(..., embed=True),
    department: str = Body("", embed=True),
    experience_level: str = Body("Mid Level", embed=True),
    employment_type: str = Body("Full-time", embed=True),
    location: str = Body("", embed=True),
    description: str = Body("", embed=True),
    skills: list = Body([], embed=True),
    current_user: dict = Depends(require_auth)
):
    """Generate a job description using AI"""
    try:
        prompt = f"""Generate a professional, detailed job description for the following role:

Title: {title}
Department: {department}
Experience Level: {experience_level}
Employment Type: {employment_type}
Location: {location}
Brief Description: {description}
Required Skills: {', '.join(skills) if skills else 'Not specified'}

Generate a comprehensive job description with these sections:
1. Company Overview (2-3 sentences about a modern innovative company)
2. Position Summary (3-4 sentences about the role)
3. Key Responsibilities (6-8 bullet points)
4. Required Qualifications (5-7 bullet points)
5. Preferred Qualifications (3-5 bullet points)
6. What We Offer (4-6 bullet points about benefits/culture)

Format as clean text with section headers. Be specific and compelling."""

        result_text = None
        source = "fallback"

        # Try Gemini first
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                result = await asyncio.wait_for(
                    gemini_svc.chat(prompt, None),
                    timeout=30
                )
                if result:
                    result_text = result
                    source = "gemini"
        except Exception as e:
            logger.warning(f"Gemini JD generation failed: {e}")

        # Rule-based fallback
        if not result_text:
            skills_text = ', '.join(skills) if skills else 'relevant technical skills'
            result_text = f"""# {title}

**Department:** {department or 'Technology'} | **Location:** {location or 'Flexible'} | **Experience Level:** {experience_level} | **Employment Type:** {employment_type}

## Company Overview

We are a pioneering company dedicated to pushing boundaries in our industry. We are at the forefront of developing innovative solutions that solve real-world challenges and create significant value for our clients.

## Position Summary

We are seeking a talented and driven {title} to join our team{' in ' + location if location else ''}. This {employment_type.lower()} role is crucial for {description or 'driving our technical initiatives forward'}, leveraging your expertise in {skills_text}.

## Key Responsibilities

- Lead and contribute to the design, development, and deployment of solutions
- Collaborate with cross-functional teams to define and implement requirements
- Write clean, maintainable, and well-tested code following best practices
- Participate in code reviews and contribute to technical documentation
- Mentor junior team members and share knowledge across the organization
- Stay current with industry trends and emerging technologies

## Required Qualifications

- {experience_level} experience in a similar role
- Strong proficiency in {skills_text}
- Excellent problem-solving and analytical skills
- Strong communication and collaboration abilities
- Bachelor's degree in a relevant field or equivalent experience

## Preferred Qualifications

- Experience with cloud platforms (AWS, GCP, Azure)
- Experience in an agile development environment
- Contributions to open-source projects
- Relevant industry certifications

## What We Offer

- Competitive salary and comprehensive benefits package
- Flexible working arrangements
- Professional development opportunities
- Collaborative and innovative work environment
- Health and wellness programs
"""
            source = "template"

        return {
            "status": "success",
            "job_description": result_text,
            "title": title,
            "source": source
        }
    except Exception as e:
        logger.error(f"JD generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
async def get_stats(current_user: dict = Depends(require_auth)):
    """Get platform statistics with high-volume support"""
    try:
        # Use optimized database statistics method
        stats = await asyncio.to_thread(db_service.get_statistics)
        
        # Add AI service stats if available
        ai_stats = {}
        try:
            ai_stats = ai_service.get_cache_stats()
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
                                    'matchScore': analysis.get('quality_score'),
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
        raise HTTPException(500, "Batch import failed")

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
        existing = await asyncio.to_thread(db_service.get_candidate_by_linkedin, profile.linkedin)
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
            "matchScore": analysis.get('quality_score') if analysis else 0,
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
            await asyncio.to_thread(db_service.update_candidate, candidate_data)
        else:
            await asyncio.to_thread(db_service.insert_candidate, candidate_data)
        
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
        raise HTTPException(500, "Failed to import LinkedIn profile")

# Stream candidates endpoint for large exports
@app.get("/api/candidates/stream")
async def stream_all_candidates(batch_size: int = 100, current_user: dict = Depends(require_auth)):
    """
    Stream all candidates for large exports (10,000+)
    Returns JSON array streamed in batches
    """
    from fastapi.responses import StreamingResponse
    
    async def generate():
        yield "["
        first = True
        all_batches = await asyncio.to_thread(lambda: list(db_service.get_candidates_stream(min(batch_size, 500))))
        for batch in all_batches:
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
    candidate_ids: Optional[List[str]] = None,
    current_user: dict = Depends(require_auth),
):
    """
    Match candidates against a job description using LLM + semantic + TF-IDF matching.
    Resolves IDs to data, then calls the multi-tier matching engine.
    """
    try:
        # Resolve job description from database
        def _resolve_match_data():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT description, title, required_skills FROM job_descriptions WHERE id = ?", (job_description_id,))
                jd_row = cursor.fetchone()
                if not jd_row:
                    return None, []
                
                job_text = f"{jd_row[1] or ''}\n{jd_row[0] or ''}\nRequired Skills: {jd_row[2] or ''}"
                
                if candidate_ids:
                    placeholders = ','.join(['?' for _ in candidate_ids])
                    cursor.execute(f"SELECT * FROM candidates WHERE id IN ({placeholders}) AND is_active = 1", candidate_ids)
                else:
                    cursor.execute("SELECT * FROM candidates WHERE is_active = 1 ORDER BY match_score DESC LIMIT 100")
                
                rows = cursor.fetchall()
            candidates = [db_service._row_to_candidate(row) for row in rows]
            return job_text, candidates
        job_text, candidates = await asyncio.to_thread(_resolve_match_data)
        if job_text is None:
            raise HTTPException(404, f"Job description not found: {job_description_id}")
        
        if not candidates:
            return []
        
        results = await matching_engine.match_candidates(job_text, candidates)
        return results
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Error matching candidates")

@app.post("/api/matching/evaluate-candidate")
async def evaluate_candidate(candidate_id: str, job_description_id: str, current_user: dict = Depends(require_auth)):
    """
    Detailed AI evaluation of a single candidate using LLM.
    Resolves IDs to data, then calls the multi-tier matching engine.
    """
    try:
        # Resolve job description
        def _resolve_eval_data():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT description, title, required_skills FROM job_descriptions WHERE id = ?", (job_description_id,))
                jd_row = cursor.fetchone()
                if not jd_row:
                    return None, None
                
                job_text = f"{jd_row[1] or ''}\n{jd_row[0] or ''}\nRequired Skills: {jd_row[2] or ''}"
                
                cursor.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
                cand_row = cursor.fetchone()
            if not cand_row:
                return job_text, None
            return job_text, db_service._row_to_candidate(cand_row)
        job_text, candidate_data = await asyncio.to_thread(_resolve_eval_data)
        if job_text is None:
            raise HTTPException(404, f"Job description not found: {job_description_id}")
        if candidate_data is None:
            raise HTTPException(404, f"Candidate not found: {candidate_id}")
        
        evaluation = await matching_engine.evaluate_candidate(candidate_data, job_text)
        return evaluation
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "Error evaluating candidate")

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
        raise HTTPException(500, "Error connecting email")

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
                # Dedup: skip already-processed emails
                msg_id = message.get('id', '') or message.get('internetMessageId', '')
                if msg_id and await asyncio.to_thread(db_service.is_email_processed, msg_id):
                    continue

                # Parse candidate using the full extraction pipeline
                sender = message.get('from', {}).get('emailAddress', {})
                sender_email = sender.get('address', '')
                sender_name = sender.get('name', sender_email.split('@')[0] if sender_email else '')
                subject = message.get('subject', '') or ''
                body = message.get('body', {}).get('content', '') or ''

                # Get attachments if present
                attachments = []
                if message.get('hasAttachments'):
                    attachments_result = await graph_service.get_message_with_attachments(message['id'])
                    if attachments_result['status'] == 'success':
                        attachments = attachments_result['attachments']

                received_dt = message.get('receivedDateTime')
                try:
                    received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00')) if received_dt else datetime.now()
                except Exception:
                    received_date = datetime.now()

                email_data = {
                    'subject': subject,
                    'sender_email': sender_email,
                    'sender_name': sender_name,
                    'body': body,
                    'attachments': attachments,
                    'received_date': received_date
                }

                candidate_info = await scraper_service.extract_candidate_from_email(email_data)
                if candidate_info and candidate_info.get('email'):
                    candidates.append(candidate_info)
                    # Mark as processed
                    if msg_id:
                        try:
                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, candidate_info.get('id', ''), 'manual-sync')
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for manual-sync: {e}")
            
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
                        # Use Local AI ONLY (zero cost)
                        try:
                            ai_analysis = await asyncio.wait_for(
                                ai_service.analyze_candidate(resume_content),
                                timeout=AI_ANALYSIS_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            logger.warning(f"⏱️ Local AI timeout for {candidate_data['name']} - using smart defaults")
                            # Calculate fallback from available data instead of hardcoding 45
                            skills = candidate_data.get('skills', [])
                            exp = candidate_data.get('experience', 0) or 0
                            if isinstance(exp, str):
                                nums = re.findall(r'\d+', str(exp))
                                exp = int(nums[0]) if nums else 0
                            calc_score = 25 + min(30, (len(skills) if isinstance(skills, list) else 3) * 3) + min(25, exp * 3)
                            ai_analysis = {
                                'quality_score': min(90, max(15, calc_score)),
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
                            candidate_data['matchScore'] = ai_analysis.get('quality_score')
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
        raise HTTPException(500, "Error syncing emails")

@app.post("/api/auth/auto-authenticate")
async def auto_authenticate(current_user: dict = Depends(require_auth)):
    """
    Automatically authenticate using credentials from .env
    Stores token for future use - no need to re-authenticate
    Automatically triggers email sync after authentication
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
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
            
            # Trigger sync inline after successful authentication (CPU throttling safe)
            await trigger_reset_and_reparse(email_address)
            
            return {
                'status': 'authenticated',
                'email': email_address,
                'provider': 'microsoft',
                'message': f'Successfully authenticated {email_address} and synced emails.',
                'token_expires_in': result['expires_in']
            }
        else:
            raise HTTPException(400, result.get('error', 'Authentication failed'))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auto-authentication error: {str(e)}")
        raise HTTPException(500, "Error during auto-authentication")


async def trigger_reset_and_reparse(email_address: str, incremental: bool = False):
    """
    Full inbox cross-verify: page through ALL inbox emails, compare with
    email_processing_log, and process any missing/unprocessed candidates.
    Uses smart_merge for existing candidates. Marks every message processed.
    
    When incremental=True (used by Cloud Scheduler cron), only fetches emails
    since last sync time — much faster for periodic runs.
    Returns a result dict when called inline (e.g. from cron endpoint).
    """
    global _last_email_sync_time
    # Prevent concurrent syncs
    if not hasattr(trigger_reset_and_reparse, '_lock'):
        trigger_reset_and_reparse._lock = asyncio.Lock()
    if trigger_reset_and_reparse._lock.locked():
        logger.info("Sync already in progress, skipping duplicate request")
        return
    await trigger_reset_and_reparse._lock.acquire()
    try:
        logger.info("🔄 Full inbox cross-verify started (incremental — keeping existing data)...")
        
        # Clear response cache only — NOT candidates
        response_cache.clear()
        
        current_count = await asyncio.to_thread(db_service.get_total_candidates)
        logger.info(f"📊 Starting with {current_count} existing candidates in database")
        
        # Load all processed message IDs into memory for fast lookup
        processed_ids = await asyncio.to_thread(db_service.get_all_processed_message_ids)
        logger.info(f"📧 Already processed: {len(processed_ids)} emails in log")
        
        # Get token and auth — with automatic refresh if expired
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        
        if not token_data:
            logger.warning("No token found for cross-verify sync")
            return
        
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        saved_auth_type = token_data.get('auth_type', 'delegated')
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
        
        # Check if token is expired and refresh before using
        token_expired = token_data.get('is_expired', True) or not token_data.get('access_token')
        if not token_expired:
            try:
                expires_at = datetime.fromisoformat(token_data['expires_at'])
                # Consider expired if less than 5 minutes remaining
                if expires_at < datetime.now() + timedelta(minutes=5):
                    token_expired = True
            except Exception:
                token_expired = True
        
        if token_expired:
            logger.warning(f"Cross-verify: token expired for {email_address}, refreshing...")
            refresh_success = False
            
            # Try refresh token first (delegated auth)
            refresh_token = token_data.get('refresh_token')
            if refresh_token:
                try:
                    refresh_result = await graph_service.refresh_access_token(refresh_token)
                    if refresh_result.get('status') == 'success':
                        token_storage.save_token(
                            email=email_address,
                            access_token=refresh_result['access_token'],
                            refresh_token=refresh_result.get('refresh_token', refresh_token),
                            expires_in=refresh_result['expires_in'],
                            auth_type='delegated'
                        )
                        token_data = token_storage.get_token(email_address)
                        refresh_success = True
                        logger.warning(f"Cross-verify: delegated token refreshed successfully")
                except Exception as e:
                    logger.warning(f"Cross-verify: delegated refresh failed: {e}")
            
            # Fallback: try client credentials (application auth)
            if not refresh_success:
                try:
                    cred_result = await graph_service.authenticate_with_credentials()
                    if cred_result.get('status') == 'success':
                        token_data = {
                            'access_token': cred_result['access_token'],
                            'auth_type': 'application',
                            'is_expired': False,
                            'expires_at': (datetime.now() + timedelta(seconds=cred_result.get('expires_in', 3600))).isoformat()
                        }
                        refresh_success = True
                        saved_auth_type = 'application'
                        logger.warning(f"Cross-verify: authenticated via app credentials")
                except Exception as e:
                    logger.warning(f"Cross-verify: app credentials failed: {e}")
            
            if not refresh_success:
                logger.warning("Cross-verify: ALL authentication methods failed — aborting")
                return
        
        graph_service.access_token = token_data['access_token']
        graph_service.auth_type = saved_auth_type
        try:
            graph_service.token_expiry = datetime.fromisoformat(token_data['expires_at'])
        except Exception:
            graph_service.token_expiry = datetime.now() + timedelta(hours=1)
        
        logger.warning(f"Cross-verify: using {saved_auth_type} auth — {'incremental' if incremental else 'full'} mode")
        
        new_count = 0
        updated_count = 0
        skipped_count = 0
        error_count = 0
        total_fetched = 0
        
        # Incremental mode: only emails since last sync (used by Cloud Scheduler cron)
        filter_query = None
        scan_max_pages = 200  # Full scan: 200×50 = 10,000
        if incremental and _last_email_sync_time:
            filter_query = f"receivedDateTime ge {_last_email_sync_time}"
            scan_max_pages = 60  # Incremental: 60×50 = 3,000 max
            logger.info(f"📧 Incremental: emails since {_last_email_sync_time}")
        else:
            logger.info(f"📧 Full scan: paging through entire inbox")
        
        # Track consecutive all-seen pages for early stop (incremental mode)
        consecutive_all_seen = 0
        
        async for page in graph_service.get_messages_paged(
            folder='inbox',
            filter_query=filter_query,
            page_size=50,
            max_pages=scan_max_pages
        ):
            total_fetched += len(page)
            
            for msg in page:
                try:
                    msg_id = msg.get('id', '') or msg.get('internetMessageId', '')
                    if not msg_id:
                        import hashlib
                        dedup_input = f"{msg.get('from', {}).get('emailAddress', {}).get('address', '')}{msg.get('subject', '')}"
                        msg_id = f"gen_{hashlib.sha256(dedup_input.encode()).hexdigest()[:16]}"
                    
                    # Fast dedup via in-memory set (loaded from email_processing_log)
                    if msg_id in processed_ids:
                        skipped_count += 1
                        continue
                    
                    # === Unprocessed email — full extraction pipeline ===
                    sender = msg.get('from', {}).get('emailAddress', {})
                    sender_email = sender.get('address', '')
                    sender_name = sender.get('name', sender_email.split('@')[0] if sender_email else '')
                    subject = msg.get('subject', '') or ''
                    body = msg.get('body', {}).get('content', '') or ''
                    
                    # PRE-FILTER: skip obvious non-candidate emails
                    _pre_subj_lower = subject.lower()
                    _pre_email_lower = sender_email.lower()
                    _notification_patterns = [
                        r'^your\s+job[,:]',
                        r'you\s+have\s+\d+\s+new\s+applicants',
                        r'^your\s+sponsored\s+job',
                        r'^your\s+posting',
                        r'job\s+performance\s+report',
                        r'^hiring\s+insights',
                        r'^budget\s+alert',
                        r'^confirm\s+your\s+account',
                        r'^welcome\s+to\s+microsoft',
                        r'^find\s+your\s+next\s+star',
                        r'^your\s+jobs\s+are\s+on',
                        r'^undeliverable:',
                        r'wants\s+to\s+access',
                        r'^your\s+invoice',
                        r'^password\s+reset',
                        r'^verify\s+your\s+email',
                        r'^your\s+subscription',
                    ]
                    if any(re.search(p, _pre_subj_lower) for p in _notification_patterns):
                        continue
                    _system_senders = ['noreply', 'no-reply', 'postmaster', 'mailer-daemon',
                                       'notifications', 'system', 'donotreply', 'do-not-reply']
                    if any(s in _pre_email_lower for s in _system_senders):
                        continue
                    
                    # Attachments — process and immediately free raw bytes
                    attachments = []
                    if msg.get('hasAttachments'):
                        try:
                            attach_result = await graph_service.get_message_with_attachments(msg['id'])
                            if attach_result['status'] == 'success':
                                attachments = attach_result['attachments']
                        except Exception as e:
                            logger.debug(f"Non-critical: failed to get attachments for cross-verify: {e}")
                    
                    received_dt = msg.get('receivedDateTime')
                    try:
                        received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00')) if received_dt else datetime.now()
                    except Exception as e:
                        logger.debug(f"Non-critical: failed to parse receivedDateTime: {e}")
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
                        # Do NOT permanently mark as no-candidate; allows retry on next sync
                        # with improved extraction logic
                        # Log at every 50th failure for diagnostics (avoid log spam)
                        if not hasattr(trigger_reset_and_reparse, '_fail_count'):
                            trigger_reset_and_reparse._fail_count = 0
                        trigger_reset_and_reparse._fail_count += 1
                        if trigger_reset_and_reparse._fail_count <= 10 or trigger_reset_and_reparse._fail_count % 100 == 0:
                            logger.warning(f"Extraction failed #{trigger_reset_and_reparse._fail_count}: '{subject[:60]}' from {sender_email} (body: {len(body)} chars, attachments: {len(attachments)})")
                        continue
                    
                    # Block Indeed relay / junk
                    if db_service.is_blocked_email(candidate['email']):
                        if msg_id:
                            try:
                                await asyncio.to_thread(db_service.mark_email_processed, msg_id, '', 'blocked-relay')
                                processed_ids.add(msg_id)
                            except Exception as e:
                                logger.debug(f"Non-critical: mark_email_processed failed for blocked-relay: {e}")
                        continue
                    
                    # Check existing candidate in DB
                    existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])
                    
                    needs_ai = False
                    if not existing:
                        needs_ai = True
                    else:
                        candidate = db_service.smart_merge_candidate(existing, candidate)
                        if (not existing.get('ai_analysis')
                                and (existing.get('matchScore') or existing.get('match_score') or 0) <= 0):
                            needs_ai = True
                    
                    # AI processing
                    analysis_text = candidate.get('resume_text') or candidate.get('summary', '')
                    if analysis_text:
                        candidate['resume_text'] = analysis_text[:5000]
                    
                    if needs_ai and analysis_text and len(analysis_text) > 20:
                        try:
                            ai_analysis = await asyncio.wait_for(
                                ai_service.analyze_candidate(analysis_text),
                                timeout=AI_ANALYSIS_TIMEOUT
                            )
                            if ai_analysis and ai_analysis.get('quality_score', 0) > 0:
                                score = ai_analysis.get('quality_score')
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
                                    'work_history': ai_analysis.get('work_history', []),
                                })
                                candidate['status'] = 'Strong' if score >= 70 else ('Partial' if score >= 40 else 'Reject')
                                logger.info(f"✅ AI scored {candidate.get('name')}: {score}%")
                        except Exception as ai_err:
                            logger.warning(f"AI error for cross-verify ({type(ai_err).__name__}): {str(ai_err)[:80]}")
                            skills = candidate.get('skills', [])
                            exp = candidate.get('experience', 0)
                            if skills or exp:
                                fb = 35.0 + min(30, len(skills) * 2.5 + (10 if skills else 0)) + (min(20, 6 + exp * 2) if exp else 0)
                                candidate['matchScore'] = min(90, round(fb, 1))
                            else:
                                candidate['matchScore'] = 45
                    
                    # Ensure score is never 0
                    if candidate.get('matchScore', 0) == 0:
                        candidate['matchScore'] = 35
                    
                    # Save resume file
                    resume_file = candidate.pop('resume_file_data', None)
                    resume_filename = candidate.pop('resume_filename', None)
                    
                    # Save to database
                    if existing:
                        await asyncio.to_thread(db_service.update_candidate, candidate)
                        updated_count += 1
                    else:
                        await asyncio.to_thread(db_service.insert_candidate, candidate)
                        new_count += 1
                    
                    if resume_file and resume_filename:
                        try:
                            ct = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                            await asyncio.to_thread(db_service.save_resume, candidate['id'], resume_filename, resume_file, ct)
                        except Exception as e:
                            logger.warning(f"Failed to save resume for {candidate.get('id', 'unknown')}: {e}")
                    
                    # Mark email as processed (critical for dedup)
                    if msg_id:
                        action = 'updated' if existing else 'inserted'
                        try:
                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, candidate.get('id', ''), action)
                            processed_ids.add(msg_id)
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for {action}: {e}")
                    
                    # Save AI analysis
                    if needs_ai and analysis_text and len(analysis_text) > 20:
                        try:
                            _skills = candidate.get('skills', [])
                            _exp = candidate.get('experience', 0) or 0
                            _edu = candidate.get('education', [])
                            _certs = candidate.get('certifications', [])
                            _score = candidate.get('matchScore', 50)
                            _strengths = []
                            if len(_skills) >= 8: _strengths.append(f"Strong technical profile with {len(_skills)} identified skills")
                            elif len(_skills) >= 4: _strengths.append(f"Solid skill set covering {len(_skills)} technologies")
                            if _exp >= 5: _strengths.append(f"{_exp} years of professional experience")
                            elif _exp >= 2: _strengths.append(f"{_exp} years of relevant experience")
                            if _edu and len(_edu) > 0: _strengths.append("Formal educational background documented")
                            if _certs and len(_certs) > 0: _strengths.append(f"Certified: {', '.join(_certs[:3])}")
                            if _score >= 70: _strengths.append("High overall profile quality")
                            _gaps = []
                            if len(_skills) < 3: _gaps.append("Limited skills information available")
                            if _exp == 0: _gaps.append("Experience level not specified")
                            if not _edu or len(_edu) == 0: _gaps.append("No education details provided")
                            if not candidate.get('phone'): _gaps.append("No phone number on file")
                            if not candidate.get('linkedin'): _gaps.append("No LinkedIn profile available")
                            await asyncio.to_thread(db_service.save_ai_analysis, candidate.get('id', ''), {
                                'score': _score, 'job_category': candidate.get('job_category', 'General'),
                                'summary': candidate.get('summary', ''), 'skills': _skills,
                                'experience': _exp, 'strengths': _strengths[:5], 'gaps': _gaps[:5],
                                'analyzed_at': datetime.now().isoformat(),
                            })
                        except Exception as e:
                            logger.warning(f"Failed to save AI analysis for {candidate.get('id', 'unknown')}: {e}")
                    
                    # Per-email memory cleanup: free large objects immediately
                    del body, attachments, email_data
                    if 'candidate' in dir(): del candidate
                    if 'analysis_text' in dir(): del analysis_text
                    if 'attach_result' in dir(): del attach_result
                    
                    # GC every 10 processed emails to prevent memory accumulation
                    _emails_processed_this_page = new_count + updated_count + error_count
                    if _emails_processed_this_page % 10 == 0 and _emails_processed_this_page > 0:
                        import gc
                        gc.collect()
                    
                except Exception as e:
                    error_count += 1
                    logger.warning(f"Cross-verify error: {str(e)[:100]}")
            
            # Early stop for incremental mode: 3 consecutive all-seen pages
            page_seen = sum(1 for _ in [m for m in page if (m.get('id', '') or m.get('internetMessageId', '')) in processed_ids])
            if page_seen == len(page):
                consecutive_all_seen += 1
                if incremental and consecutive_all_seen >= 3:
                    logger.info(f"Incremental: 3 pages all already-processed - stopping early")
                    break
            else:
                consecutive_all_seen = 0
            
            # Memory safety: GC every 5 pages, abort if memory > 85% of container limit
            if total_fetched % 250 < len(page):
                import gc
                gc.collect()
                try:
                    # Cloud Run: use cgroup memory info for accurate container limits
                    try:
                        with open('/sys/fs/cgroup/memory/memory.usage_in_bytes') as f:
                            usage = int(f.read().strip())
                        with open('/sys/fs/cgroup/memory/memory.limit_in_bytes') as f:
                            limit = int(f.read().strip())
                        mem_pct = (usage / limit) * 100
                    except FileNotFoundError:
                        try:
                            with open('/sys/fs/cgroup/memory.current') as f:
                                usage = int(f.read().strip())
                            with open('/sys/fs/cgroup/memory.max') as f:
                                limit = int(f.read().strip())
                            mem_pct = (usage / limit) * 100
                        except Exception:
                            import psutil
                            mem_pct = psutil.virtual_memory().percent
                    if mem_pct > 85:
                        logger.error(f"Container memory at {mem_pct:.1f}% - stopping cross-verify to prevent OOM")
                        break
                except Exception as e:
                    logger.debug(f"Non-critical: memory check failed: {e}")
            
            # Progress logging every 200 emails (WARNING level for production visibility)
            if total_fetched % 200 < len(page):
                logger.warning(f"Cross-verify progress: {total_fetched} scanned, {new_count} new, {updated_count} updated, {skipped_count} already-processed, {error_count} errors")
        
        final_count = await asyncio.to_thread(db_service.get_total_candidates)
        logger.warning(f"Cross-verify complete! {total_fetched} emails scanned, {new_count} new, {updated_count} updated, {skipped_count} skipped, {error_count} errors")
        logger.warning(f"Database: {current_count} -> {final_count} candidates")
        
        # Update last sync time
        _last_email_sync_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        try:
            await asyncio.to_thread(db_service.set_sync_metadata, 'last_email_sync_time', _last_email_sync_time)
        except Exception as e:
            logger.debug(f"Non-critical: set_sync_metadata failed after cross-verify: {e}")
        
        # Clear cache if new candidates found
        if new_count > 0:
            response_cache.clear()
            logger.info(f"🧹 Cache cleared after adding {new_count} new candidates")
        
        # 🔒 CRITICAL: Backup DB to GCS after every sync so changes survive container restarts.
        # Without this, cron sync results are lost when min-instances=0 scales the container down.
        if _settings.is_production:
            try:
                logger.info("💾 Backing up DB to GCS after sync...")
                await asyncio.to_thread(backup_db_to_gcs)
                logger.info("✅ Post-sync GCS backup complete")
            except Exception as _bk_err:
                logger.error(f"⚠️ Post-sync GCS backup failed: {_bk_err}")
        
        return {
            'status': 'completed',
            'mode': 'incremental' if incremental else 'full',
            'emails_scanned': total_fetched,
            'new_candidates': new_count,
            'updated_candidates': updated_count,
            'skipped_already_processed': skipped_count,
            'errors': error_count,
            'candidates_before': current_count,
            'candidates_after': final_count,
            'sync_time': _last_email_sync_time,
        }
        
    except Exception as e:
        logger.error(f"Error in cross-verify sync: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {'status': 'error', 'message': str(e)}
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
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
        default_redirect = f"{frontend_url}/auth/callback"
        redirect_uri = os.getenv('MICROSOFT_REDIRECT_URI', default_redirect)
        
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
async def get_oauth2_url_simple(request: Request = None, current_user: dict = Depends(require_auth)):
    """
    Get Microsoft OAuth2 authorization URL using config from .env
    Simple endpoint - no parameters needed. Auto-detects production redirect URI.
    """
    try:
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID', 'common')
        # Use env var if set, otherwise derive from CORS_ORIGINS (same logic as GET callback)
        frontend_url = os.getenv('CORS_ORIGINS', 'http://localhost:5173').split(',')[0].strip()
        default_redirect = f"{frontend_url}/auth/callback"
        redirect_uri = os.getenv('MICROSOFT_REDIRECT_URI', default_redirect)
        
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
        raise HTTPException(500, "Error generating authorization URL")

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
        raise HTTPException(500, "Error generating authorization URL")

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
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')  # Primary email account
        
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
        raise HTTPException(500, "Error processing OAuth2 callback")

@app.post("/api/email/sync-now")
async def sync_emails_now(current_user: dict = Depends(require_auth)):
    """
    Trigger immediate email sync using saved OAuth2 token.
    Runs inline (completes before response) to work with CPU throttling.
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
        
        if not email_address:
            raise HTTPException(400, "No email configured in .env")
        
        # Check if we have a token
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        
        if not token_data:
            raise HTTPException(401, "No OAuth2 token found. Please authenticate first.")
        
        # Run sync inline (CPU throttling safe)
        result = await trigger_reset_and_reparse(email_address)
        
        return result or {
            'status': 'completed',
            'message': 'Email sync completed',
            'email': email_address
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sync error: {str(e)}")
        raise HTTPException(500, "Error starting sync")

@app.post("/api/email/deep-sync")
async def deep_sync_emails(current_user: dict = Depends(require_auth)):
    """
    Deep sync: clear 'no-candidate' entries from email_processing_log,
    optionally clear entries since a given date, then trigger a full
    cross-verify to re-process previously skipped emails.
    Use query param ?since=2025-02-17 to clear entries after a date.
    """
    from fastapi import Query
    try:
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
        if not email_address:
            raise HTTPException(400, "No email configured in .env")
        
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        if not token_data:
            raise HTTPException(401, "No OAuth2 token found. Please authenticate first.")
        
        # Clear ALL blocked/failed entries (not just no-candidate) to allow full re-processing
        # This is essential: previous code may have wrongly blocked genuine candidates
        cleared_blocked = await asyncio.to_thread(db_service.clear_all_blocked_entries)
        logger.warning(f"Deep sync: cleared {cleared_blocked} blocked/failed entries from processing log")
        
        # Clear orphaned entries: emails marked "inserted"/"updated" but candidate record lost
        # This handles candidates lost during DB restore from GCS
        cleared_orphaned = await asyncio.to_thread(db_service.clear_orphaned_processing_entries)
        logger.warning(f"Deep sync: cleared {cleared_orphaned} orphaned entries (candidate records lost)")
        
        # Get current counts
        total_before = await asyncio.to_thread(db_service.get_total_candidates)
        processed_before = await asyncio.to_thread(db_service.get_processed_email_count)
        
        # Run full (non-incremental) cross-verify inline (CPU throttling safe)
        result = await trigger_reset_and_reparse(email_address, incremental=False)
        
        total_after = await asyncio.to_thread(db_service.get_total_candidates)
        
        return {
            'status': 'deep-sync-completed',
            'message': f'Deep sync completed. Cleared {cleared_blocked} blocked + {cleared_orphaned} orphaned log entries. Candidates: {total_before} → {total_after}.',
            'cleared_blocked_entries': cleared_blocked,
            'cleared_orphaned_entries': cleared_orphaned,
            'candidates_before': total_before,
            'processed_emails_before': processed_before,
            'email': email_address
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Deep sync error: {str(e)}")
        raise HTTPException(500, "Error starting deep sync")

@app.post("/api/email/cross-verify")
async def cross_verify_inbox(current_user: dict = Depends(require_auth)):
    """
    Full inbox cross-verify: fetch ALL inbox emails, compare against
    email_processing_log, and process any missing/unprocessed candidates.
    Returns immediately — work runs in background.
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
        if not email_address:
            raise HTTPException(400, "No email configured in .env")

        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        if not token_data:
            raise HTTPException(401, "No OAuth2 token found. Please authenticate first.")

        # Get counts for the response
        total_candidates = await asyncio.to_thread(db_service.get_total_candidates)
        processed_emails = await asyncio.to_thread(db_service.get_processed_email_count)

        # Run full cross-verify inline (CPU throttling safe)
        result = await trigger_reset_and_reparse(email_address)
        
        total_after = await asyncio.to_thread(db_service.get_total_candidates)

        return {
            'status': 'cross-verify-completed',
            'message': f'Full inbox cross-verify completed. DB: {total_candidates} → {total_after} candidates, {processed_emails} emails processed.',
            'candidates_before': total_candidates,
            'processed_emails': processed_emails,
            'email': email_address
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cross-verify error: {str(e)}")
        raise HTTPException(500, "Error starting cross-verify")


async def _backfill_resumes_task(email_address: str):
    """
    Backfill resumes using the email_processing_log message_id→candidate_id
    mapping.  For every candidate that has no resume stored, fetch the
    original email by its Graph API message ID, grab any resume-type
    attachment and store it.  This handles forwarded/relayed emails where
    the sender doesn't match the candidate.
    """
    import base64 as b64
    import requests
    try:
        logger.warning("📎 Resume backfill v2 started (message-id based)")

        # ── OAuth token ──────────────────────────────────────────────
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        if not token_data:
            return {'status': 'error', 'message': 'No OAuth token'}

        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')

        graph_service = MicrosoftGraphService(
            client_id, client_secret, tenant_id, user_email=email_address
        )
        graph_service.access_token = token_data['access_token']
        graph_service.auth_type = token_data.get('auth_type', 'delegated')
        graph_service.token_expiry = datetime.fromisoformat(token_data['expires_at'])

        # ── Pre-load mappings ────────────────────────────────────────
        msg_candidates = await asyncio.to_thread(db_service.get_candidate_message_ids)
        existing_resumes = await asyncio.to_thread(db_service.get_all_resume_candidate_ids)

        # Build candidate_id → [message_ids] (take first msg per candidate)
        need_resume: dict[str, str] = {}  # candidate_id -> message_id
        for msg_id, cid in msg_candidates:
            if cid not in existing_resumes and cid not in need_resume:
                need_resume[cid] = msg_id

        total = len(need_resume)
        logger.warning(
            f"📊 {len(msg_candidates)} msg→candidate pairs, "
            f"{len(existing_resumes)} already have resumes, "
            f"{total} need resume backfill"
        )

        if total == 0:
            return {
                'status': 'completed', 'emails_scanned': 0,
                'resumes_stored': 0, 'already_had': len(existing_resumes),
                'errors': 0, 'message': 'All candidates already have resumes or no mappings found'
            }

        # ── Build Graph API headers ──────────────────────────────────
        headers = {
            'Authorization': f'Bearer {graph_service.access_token}',
            'Content-Type': 'application/json'
        }

        # ── Token refresh helper ─────────────────────────────────────
        async def _refresh_token():
            """Refresh OAuth2 token when it expires mid-backfill."""
            nonlocal headers
            rt = token_data.get('refresh_token')
            if not rt:
                logger.warning("⚠️ No refresh_token available — cannot renew")
                return False
            result = await graph_service.refresh_access_token(rt)
            if result.get('status') == 'success':
                headers['Authorization'] = f"Bearer {result['access_token']}"
                # Persist the refreshed token
                token_storage.save_token(
                    email=email_address,
                    access_token=result['access_token'],
                    refresh_token=result.get('refresh_token', rt),
                    expires_in=result.get('expires_in', 3600),
                    auth_type='delegated'
                )
                logger.warning("🔄 OAuth2 token refreshed mid-backfill")
                return True
            logger.warning(f"⚠️ Token refresh failed: {result.get('error', 'unknown')}")
            return False

        if graph_service.auth_type == 'application' and graph_service.user_email:
            base = f"{graph_service.graph_url}/users/{graph_service.user_email}/messages"
        else:
            base = f"{graph_service.graph_url}/me/messages"

        stored = 0
        skipped_no_attach = 0
        skipped_no_resume = 0
        errors = 0
        checked = 0
        last_upload_count = 0

        items = list(need_resume.items())
        for candidate_id, message_id in items:
            checked += 1
            try:
                # 1) Lightweight check: does this message have attachments?
                meta_url = f"{base}/{message_id}?$select=id,hasAttachments"
                meta_resp = await asyncio.to_thread(
                    lambda u=meta_url: requests.get(u, headers=headers, timeout=30)
                )

                # Handle 429 rate limiting
                if meta_resp.status_code == 429:
                    retry_after = int(meta_resp.headers.get('Retry-After', '30'))
                    logger.warning(f"⏳ Rate limited, sleeping {retry_after}s (checked {checked}/{total})")
                    await asyncio.sleep(retry_after)
                    meta_resp = await asyncio.to_thread(
                        lambda u=meta_url: requests.get(u, headers=headers, timeout=30)
                    )

                if meta_resp.status_code == 404:
                    # Message was deleted from the mailbox
                    skipped_no_attach += 1
                    continue

                # Handle 401 — refresh token and retry once
                if meta_resp.status_code == 401:
                    if await _refresh_token():
                        meta_resp = await asyncio.to_thread(
                            lambda u=meta_url: requests.get(u, headers=headers, timeout=30)
                        )
                        if meta_resp.status_code in (401, 403):
                            errors += 1
                            if errors <= 5:
                                logger.warning(f"⚠️ 401 even after refresh for {candidate_id}")
                            continue
                    else:
                        errors += 1
                        continue

                meta_resp.raise_for_status()
                meta = meta_resp.json()

                if not meta.get('hasAttachments'):
                    skipped_no_attach += 1
                    if checked % 200 == 0:
                        await asyncio.sleep(0.05)
                    continue

                # 2) Fetch attachments
                att_url = f"{base}/{message_id}/attachments"
                att_resp = await asyncio.to_thread(
                    lambda u=att_url: requests.get(u, headers=headers, timeout=60)
                )

                if att_resp.status_code == 429:
                    retry_after = int(att_resp.headers.get('Retry-After', '30'))
                    logger.warning(f"⏳ Rate limited on attachments, sleeping {retry_after}s")
                    await asyncio.sleep(retry_after)
                    att_resp = await asyncio.to_thread(
                        lambda u=att_url: requests.get(u, headers=headers, timeout=60)
                    )

                att_resp.raise_for_status()
                attachments = att_resp.json().get('value', [])

                # 3) Find a resume-type attachment
                found_resume = False
                for att in attachments:
                    if att.get('@odata.type') != '#microsoft.graph.fileAttachment':
                        continue
                    att_name = (att.get('name') or '').lower()
                    att_ct = (att.get('contentType') or '').lower()
                    is_resume = (
                        att_ct in ('application/pdf', 'application/msword',
                                   'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
                        or att_name.endswith(('.pdf', '.doc', '.docx'))
                    )
                    if not is_resume:
                        continue
                    raw = att.get('contentBytes', '')
                    if not raw or len(raw) < 100:
                        continue

                    try:
                        file_bytes = b64.b64decode(raw) if isinstance(raw, str) else raw
                    except Exception:
                        file_bytes = raw

                    ct = 'application/pdf' if ('pdf' in att_ct or att_name.endswith('.pdf')) else att_ct
                    filename = att.get('name') or f"resume.{'pdf' if 'pdf' in ct else 'docx'}"

                    await asyncio.to_thread(
                        db_service.save_resume, candidate_id, filename, file_bytes, ct
                    )
                    existing_resumes.add(candidate_id)
                    stored += 1
                    found_resume = True
                    break  # one resume per candidate

                if not found_resume:
                    skipped_no_resume += 1

                # Small delay to stay under rate limits
                await asyncio.sleep(0.15)

            except Exception as e:
                errors += 1
                if errors <= 10:
                    logger.warning(f"Backfill error [{type(e).__name__}] cid={candidate_id} mid={message_id[:40]}: {str(e)[:120]}")

            # Progress every 100 candidates
            if checked % 100 == 0:
                logger.warning(
                    f"📊 Backfill progress: {checked}/{total} checked, "
                    f"{stored} stored, {skipped_no_attach} no-attach, "
                    f"{skipped_no_resume} no-resume, {errors} errors"
                )

            # Periodic GCS upload every 200 resumes to save progress
            if stored > 0 and stored % 200 == 0 and stored != last_upload_count:
                try:
                    await asyncio.to_thread(backup_db_to_gcs)
                    last_upload_count = stored
                    logger.warning(f"☁️ Periodic DB upload at {stored} resumes stored")
                except Exception as ue:
                    logger.warning(f"⚠️ Periodic DB upload failed at {stored}: {str(ue)[:100]}")

        # ── Upload DB ────────────────────────────────────────────────
        if stored > 0:
            try:
                await asyncio.to_thread(backup_db_to_gcs)
                logger.warning(f"☁️ DB uploaded to GCS after {stored} resume backfills")
            except Exception as e:
                logger.warning(f"Failed to upload DB to GCS after resume backfill: {e}")

        logger.warning(
            f"✅ Resume backfill v2 complete: {checked}/{total} checked, "
            f"{stored} resumes stored, {skipped_no_attach} no attachments, "
            f"{skipped_no_resume} had attachments but no resume, {errors} errors"
        )
        return {
            'status': 'completed',
            'candidates_checked': checked,
            'total_needing_resume': total,
            'resumes_stored': stored,
            'skipped_no_attachments': skipped_no_attach,
            'skipped_no_resume_file': skipped_no_resume,
            'already_had_resume': len(existing_resumes) - stored,
            'errors': errors,
        }
    except Exception as e:
        logger.error(f"Resume backfill v2 error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {'status': 'error', 'message': str(e)}


@app.post("/api/email/backfill-resumes")
async def backfill_resumes(current_user: dict = Depends(require_auth)):
    """
    Scan inbox for emails with attachments and store resume files
    for existing candidates that don't have resumes yet.
    Runs synchronously (keeps container alive until done).
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
        if not email_address:
            raise HTTPException(400, "No email configured")
        
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        if not token_data:
            raise HTTPException(401, "No OAuth2 token. Please authenticate first.")
        
        # Run synchronously to keep Cloud Run container alive
        result = await _backfill_resumes_task(email_address)
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backfill error: {str(e)}")
        raise HTTPException(500, "Internal server error")


@app.get("/api/email/backfill-debug")
async def backfill_debug(current_user: dict = Depends(require_auth)):
    """
    Diagnostic endpoint: inspect message_id format and test one Graph API lookup.
    """
    import traceback as tb
    import requests as req
    results = {"steps": []}

    # 1) Get sample message_ids from DB
    msg_candidates = await asyncio.to_thread(db_service.get_candidate_message_ids)
    results["total_msg_candidate_pairs"] = len(msg_candidates)

    samples = msg_candidates[:5] if len(msg_candidates) >= 5 else msg_candidates
    results["sample_message_ids"] = [
        {"message_id_len": len(mid), "message_id_first60": mid[:60], "candidate_id": cid}
        for mid, cid in samples
    ]

    # 2) Setup Graph API (same as backfill)
    email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
    token_storage = get_token_storage()
    token_data = token_storage.get_token(email_address) if email_address else None
    if not token_data:
        results["error"] = "No OAuth token"
        return results

    client_id = os.getenv('MICROSOFT_CLIENT_ID')
    client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
    tenant_id = os.getenv('MICROSOFT_TENANT_ID')

    graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
    graph_service.access_token = token_data['access_token']
    graph_service.auth_type = token_data.get('auth_type', 'delegated')
    graph_service.token_expiry = datetime.fromisoformat(token_data['expires_at'])
    results["auth_type"] = graph_service.auth_type
    results["token_prefix"] = (token_data['access_token'] or '')[:20] + "..."

    if graph_service.auth_type == 'application' and graph_service.user_email:
        base = f"{graph_service.graph_url}/users/{graph_service.user_email}/messages"
    else:
        base = f"{graph_service.graph_url}/me/messages"
    results["base_url"] = base

    # 3) Try fetching the first sample message
    if samples:
        test_mid, test_cid = samples[0]
        test_url = f"{base}/{test_mid}?$select=id,hasAttachments,subject"
        results["test_url_len"] = len(test_url)
        results["test_url_first100"] = test_url[:100]
        try:
            resp = await asyncio.to_thread(
                lambda: req.get(test_url, headers={
                    'Authorization': f'Bearer {graph_service.access_token}',
                    'Content-Type': 'application/json'
                }, timeout=30)
            )
            results["test_status_code"] = resp.status_code
            results["test_response_first200"] = resp.text[:200]
        except Exception as e:
            results["test_exception_type"] = type(e).__name__
            results["test_exception_msg"] = str(e)[:300]
            results["test_traceback"] = tb.format_exc()[-500:]

    return results


@app.post("/api/candidates/deduplicate")
async def deduplicate_candidates(current_user: dict = Depends(require_auth)):
    """Merge duplicate candidates (same email, different case)."""
    try:
        result = await asyncio.to_thread(db_service.deduplicate_candidates)
        if result.get('merged', 0) > 0:
            response_cache.clear()
        return result
    except Exception as e:
        logger.error(f"Dedup error: {str(e)}")
        raise HTTPException(500, "Error deduplicating")


@app.post("/api/cron/sync")
async def cron_sync(request: Request):
    """
    Cloud Scheduler endpoint — runs incremental email sync INLINE (not background).
    Designed for min-instances=0 setups where the container may scale down after
    the response is sent. The sync completes within this request lifecycle.
    
    Auth: X-Cron-Secret header must match CRON_SECRET env var.
    """
    # Verify shared secret
    secret = request.headers.get('X-Cron-Secret', '').strip()
    expected = os.getenv('CRON_SECRET', '').strip()
    if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
        raise HTTPException(403, "Unauthorized")
    
    email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
    if not email_address:
        raise HTTPException(400, "No email configured")
    
    # === Ensure _last_email_sync_time is loaded (cold start safety) ===
    # On a cold start, auto_sync_emails() may not have run yet (5s delay),
    # so _last_email_sync_time could be None. Load it from DB to ensure
    # incremental mode uses the correct time window.
    global _last_email_sync_time
    if not _last_email_sync_time:
        try:
            persisted_time = await asyncio.to_thread(db_service.get_sync_metadata, 'last_email_sync_time')
            if persisted_time:
                _last_email_sync_time = persisted_time
                logger.info(f"⏰ Cron: Loaded last sync time from DB: {_last_email_sync_time}")
            else:
                logger.info("⏰ Cron: No previous sync time found — will do full scan")
        except Exception as _meta_err:
            logger.warning(f"⏰ Cron: Could not load sync metadata: {_meta_err}")
    
    # === Ensure valid OAuth2 token (refresh if needed) ===
    try:
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        if not all([client_id, client_secret, tenant_id]):
            raise HTTPException(400, "OAuth2 credentials not configured")
        
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        
        needs_refresh = (
            not token_data
            or token_data.get('is_expired', True)
            or not token_data.get('access_token')
        )
        
        if needs_refresh:
            graph_svc = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
            refreshed = False
            
            # Priority 1: Refresh token (delegated auth)
            refresh_token = token_data.get('refresh_token') if token_data else None
            if refresh_token:
                try:
                    ref_result = await graph_svc.refresh_access_token(refresh_token)
                    if ref_result.get('status') == 'success':
                        token_storage.save_token(
                            email=email_address,
                            access_token=ref_result['access_token'],
                            refresh_token=ref_result.get('refresh_token', refresh_token),
                            expires_in=ref_result['expires_in'],
                            auth_type='delegated'
                        )
                        refreshed = True
                        logger.info("🔐 Cron: Delegated token refreshed")
                except Exception as ref_err:
                    logger.warning(f"Cron: Refresh token failed: {ref_err}")
            
            # Priority 2: Client credentials (application permissions)
            if not refreshed:
                try:
                    cred_result = await graph_svc.authenticate_with_credentials()
                    if cred_result.get('status') == 'success':
                        token_storage.save_token(
                            email=email_address,
                            access_token=cred_result['access_token'],
                            refresh_token=None,
                            expires_in=cred_result.get('expires_in', 3600),
                            auth_type='application'
                        )
                        refreshed = True
                        logger.info("🔐 Cron: App credentials authenticated")
                except Exception as cred_err:
                    logger.warning(f"Cron: Client credentials failed: {cred_err}")
            
            if not refreshed:
                raise HTTPException(401, "Could not obtain valid OAuth2 token")
    except HTTPException:
        raise
    except Exception as auth_err:
        logger.error(f"Cron auth error: {auth_err}")
        raise HTTPException(500, f"Auth error: {str(auth_err)}")
    
    # === Run incremental sync inline (completes before response) ===
    logger.info("⏰ Cron sync triggered by Cloud Scheduler")
    result = await trigger_reset_and_reparse(email_address, incremental=True)
    
    return result or {'status': 'completed', 'message': 'Sync cycle complete'}


@app.get("/api/email/sync-status")
async def get_sync_status(current_user: dict = Depends(require_auth)):
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
            except Exception as e:
                logger.debug(f"Non-critical: failed to load sync metadata: {e}")
        
        candidate_count = await asyncio.to_thread(lambda: db_service.get_total_candidates())
        sync_interval = int(os.getenv('SYNC_INTERVAL_MINUTES', '30'))
        now_utc = datetime.utcnow()
        next_sync_utc = None
        if _last_email_sync_time:
            try:
                last_dt = datetime.fromisoformat(_last_email_sync_time.replace('Z', '+00:00'))
                next_dt = last_dt + timedelta(minutes=sync_interval)
                next_sync_utc = next_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            except Exception as e:
                logger.debug(f"Non-critical: failed to parse last sync time: {e}")
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
            'error': 'Failed to fetch sync status'
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
                        'matchScore': ai_analysis.get('quality_score'),
                        'summary': ai_analysis.get('summary', candidate.get('summary', '')),
                        'skills': ai_analysis.get('skills', candidate.get('skills', [])),
                        'experience': ai_analysis.get('experience', candidate.get('experience', 0)),
                        'phone': ai_analysis.get('phone', '') or candidate.get('phone', ''),
                        'location': ai_analysis.get('location', '') or candidate.get('location', ''),
                    })
                    if ai_analysis.get('name') and (not candidate.get('name') or candidate.get('name') == 'Unknown'):
                        candidate['name'] = ai_analysis['name']
                    score = ai_analysis.get('quality_score')
                    if score:
                        candidate['status'] = 'Strong' if score >= 70 else ('Partial' if score >= 40 else 'Reject')
            except Exception as ai_err:
                logger.warning(f"AI analysis failed: {str(ai_err)[:50]}")
                # Calculate fallback instead of hardcoding 50
                skills = candidate.get('skills', [])
                exp = candidate.get('experience', 0) or 0
                if isinstance(exp, str):
                    nums = re.findall(r'\d+', str(exp))
                    exp = int(nums[0]) if nums else 0
                if skills or exp:
                    fallback = 25 + min(30, len(skills) * 3) + min(25, exp * 3)
                    candidate['matchScore'] = min(90, max(15, fallback))
                else:
                    candidate['matchScore'] = 30
        
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
                        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
                        token_storage = get_token_storage()
                        token_data = token_storage.get_token(email_address)
                        
                        if token_data and token_data.get('access_token'):
                            client_id = os.getenv('MICROSOFT_CLIENT_ID')
                            client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
                            tenant_id = os.getenv('MICROSOFT_TENANT_ID')
                            
                            graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
                            graph_service.access_token = token_data['access_token']
                            graph_service.auth_type = token_data.get('auth_type', 'delegated')
                            
                            await process_single_email(message_id, graph_service)
        
        return {"status": "processed"}
        
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}", exc_info=True)
        return {"status": "error", "message": "Internal webhook processing error"}


@app.post("/api/email/subscribe-webhook")
async def subscribe_to_email_webhook(current_user: dict = Depends(require_auth)):
    """
    Create a Microsoft Graph subscription for real-time email notifications
    This needs to be called once to set up real-time sync
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
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
                "clientState": os.getenv('WEBHOOK_CLIENT_STATE', 'recruitment-tool-secret')
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
        raise HTTPException(500, "Error creating webhook subscription")


@app.post("/api/email/outlook/connect")
async def connect_outlook(request: Request, current_user: dict = Depends(require_auth)):
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
    redirect_uri: str,
    current_user: dict = Depends(require_auth),
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
        raise HTTPException(500, "Error generating auth URL")

@app.post("/api/email/outlook/sync")
async def sync_outlook_applications(
    access_token: str,
    folder: str = 'inbox',
    limit: int = 50,
    current_user: dict = Depends(require_auth),
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
        raise HTTPException(500, "Error syncing Outlook")

@app.post("/api/email/setup-auto-sync")
async def setup_auto_sync(
    provider: str,
    email: str,
    password: Optional[str] = None,
    access_token: Optional[str] = None,
    sync_interval_minutes: int = 15,
    current_user: dict = Depends(require_auth),
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
        raise HTTPException(500, "Error setting up auto-sync")

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
async def get_oauth_automation_status(current_user: dict = Depends(require_auth)):
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
            'error': 'Failed to check OAuth status'
        }


@app.post("/api/oauth/refresh")
async def force_token_refresh(current_user: dict = Depends(require_auth)):
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
        raise HTTPException(500, "Token refresh error")


@app.post("/api/oauth/sync")
async def trigger_oauth_sync(current_user: dict = Depends(require_auth)):
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
            result = await trigger_reset_and_reparse(email_address)
            return result or {
                'status': 'success',
                'message': 'Sync completed',
                'email': email_address
            }
        
        result = await oauth_automation_service.trigger_manual_sync(sync_callback)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering OAuth sync: {e}")
        raise HTTPException(500, "Sync error")


@app.get("/api/oauth/stats")
async def get_oauth_stats(current_user: dict = Depends(require_auth)):
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
        return {'status': 'error', 'message': 'Failed to fetch OAuth stats'}


@app.post("/api/oauth/start-automation")
async def start_oauth_automation(current_user: dict = Depends(require_auth)):
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
        raise HTTPException(500, "Internal server error")


@app.post("/api/oauth/stop-automation")
async def stop_oauth_automation(current_user: dict = Depends(require_auth)):
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
        raise HTTPException(500, "Internal server error")


@app.post("/api/email/manual-sync")
async def manual_email_sync(current_user: dict = Depends(require_auth)):
    """
    Emergency manual sync endpoint
    Bypasses OAuth automation for direct sync
    This is the fallback when automatic sync fails
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL')
        
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
        
        # Trigger sync inline (CPU throttling safe)
        result = await trigger_reset_and_reparse(email_address)
        
        return result or {
            'status': 'completed',
            'message': 'Manual email sync completed',
            'email': email_address,
            'note': 'This is the emergency fallback. Automatic sync should handle this normally.'
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Manual sync error: {e}")
        raise HTTPException(500, "Manual sync error")


@app.post("/api/email/smart-refetch")
async def smart_email_refetch(current_user: dict = Depends(require_auth)):
    """
    Smart re-fetch: scans ALL inbox emails, compares against existing DB,
    and processes any that were skipped or missed. Does NOT delete or modify
    existing candidates — only adds new ones and fills gaps.
    
    Safe to run at any time — idempotent via email_processing_log dedup.
    """
    try:
        email_address = os.getenv('EMAIL_ADDRESS', 'hr@effortz.com')
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        
        if not all([client_id, client_secret, tenant_id]):
            raise HTTPException(400, "Microsoft OAuth2 not configured")
        
        # Get or refresh OAuth token
        token_storage = get_token_storage()
        token_data = token_storage.get_token(email_address)
        
        if not token_data:
            raise HTTPException(401, "No OAuth token found. Please authenticate first.")
        
        access_token = token_data.get('access_token')
        if token_data.get('is_expired'):
            refresh_token = token_data.get('refresh_token')
            if not refresh_token:
                raise HTTPException(401, "Token expired with no refresh token")
            graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
            refresh_result = await graph_service.refresh_access_token(refresh_token)
            if refresh_result['status'] != 'success':
                raise HTTPException(401, "Token refresh failed")
            access_token = refresh_result['access_token']
            token_storage.save_token(
                email=email_address,
                access_token=access_token,
                refresh_token=refresh_result.get('refresh_token', refresh_token),
                expires_in=refresh_result['expires_in'],
                auth_type='delegated'
            )
        
        graph_service = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=email_address)
        graph_service.access_token = access_token
        
        # Fetch ALL inbox emails
        logger.info("🔍 Smart re-fetch: Scanning all inbox emails...")
        messages_result = await graph_service.get_messages(
            folder='inbox',
            top=5000,
            fetch_all=True
        )
        
        if messages_result['status'] != 'success':
            raise HTTPException(500, f"Failed to fetch emails: {messages_result.get('message', 'Unknown error')}")
        
        all_messages = messages_result.get('messages', [])
        total_emails = len(all_messages)
        
        # Get existing candidate emails from DB for comparison
        existing_emails = set()
        try:
            all_candidates = await asyncio.to_thread(db_service.get_all_candidates)
            for c in all_candidates:
                email = c.get('email', '')
                if email:
                    existing_emails.add(email.lower().strip())
        except Exception as e:
            logger.warning(f"Failed to load existing candidates for refetch: {e}")
        
        new_count = 0
        updated_count = 0
        skipped_already_processed = 0
        skipped_no_candidate = 0
        skipped_blocked = 0
        errors = 0
        
        for msg in all_messages:
            try:
                msg_id = msg.get('id', '') or msg.get('internetMessageId', '')
                if not msg_id:
                    import hashlib
                    dedup_input = f"{msg.get('from', {}).get('emailAddress', {}).get('address', '')}{msg.get('subject', '')}"
                    msg_id = f"gen_{hashlib.sha256(dedup_input.encode()).hexdigest()[:16]}"
                
                # Check if already processed
                if await asyncio.to_thread(db_service.is_email_processed, msg_id):
                    skipped_already_processed += 1
                    continue
                
                # Extract sender info
                sender = msg.get('from', {}).get('emailAddress', {})
                sender_email = sender.get('address', '')
                sender_name = sender.get('name', sender_email.split('@')[0] if sender_email else '')
                subject = msg.get('subject', '')
                body = msg.get('body', {}).get('content', '')
                
                # Quick relevance filter — skip obvious non-candidate emails
                subject_lower = subject.lower() if subject else ''
                skip_keywords = ['unsubscribe', 'newsletter', 'invoice', 'receipt', 'password reset',
                                 'verify your email', 'out of office', 'automatic reply', 'delivery notification',
                                 'mailer-daemon', 'postmaster', 'noreply', 'no-reply']
                if any(kw in subject_lower for kw in skip_keywords):
                    if msg_id:
                        try:
                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, '', 'skipped-irrelevant')
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for skipped-irrelevant: {e}")
                    skipped_no_candidate += 1
                    continue
                
                # Check attachments
                has_attachments = msg.get('hasAttachments', False)
                attachments = []
                if has_attachments:
                    try:
                        attach_result = await graph_service.get_message_with_attachments(msg['id'])
                        if attach_result['status'] == 'success':
                            attachments = attach_result['attachments']
                    except Exception as e:
                        logger.debug(f"Non-critical: failed to get attachments for refetch: {e}")
                
                # Parse received date
                received_dt = msg.get('receivedDateTime')
                try:
                    received_date = datetime.fromisoformat(received_dt.replace('Z', '+00:00')) if received_dt else datetime.now()
                except Exception as e:
                    logger.debug(f"Non-critical: failed to parse receivedDateTime: {e}")
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
                    if msg_id:
                        try:
                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, '', 'no-candidate')
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for no-candidate: {e}")
                    skipped_no_candidate += 1
                    continue
                
                # Block check
                if db_service.is_blocked_email(candidate['email']):
                    if msg_id:
                        try:
                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, '', 'blocked')
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for blocked: {e}")
                    skipped_blocked += 1
                    continue
                
                # Check if exists in DB
                existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])
                
                if not existing:
                    # New candidate — process with AI if we have text
                    analysis_text = candidate.get('resume_text') or candidate.get('summary', '')
                    if analysis_text and len(analysis_text) > 20:
                        try:
                            ai_analysis = await asyncio.wait_for(
                                ai_service.analyze_candidate(analysis_text[:5000]),
                                timeout=45
                            )
                            if ai_analysis and ai_analysis.get('quality_score', 0) > 0:
                                score = ai_analysis.get('quality_score')
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
                                    'status': 'Strong' if score >= 70 else ('Partial' if score >= 40 else 'Reject'),
                                })
                        except Exception as ai_err:
                            logger.debug(f"AI timeout for {candidate.get('name')}: {ai_err}")
                            skills = candidate.get('skills', [])
                            exp = candidate.get('experience', 0)
                            fallback_score = 35.0 + min(30, len(skills) * 2.5) + min(20, exp * 2 if exp else 0)
                            candidate['matchScore'] = min(90, round(fallback_score, 1))
                    
                    # Save resume file if present
                    resume_file = candidate.pop('resume_file_data', None)
                    resume_filename = candidate.pop('resume_filename', None)
                    
                    await asyncio.to_thread(db_service.insert_candidate, candidate)
                    
                    if resume_file and resume_filename:
                        try:
                            ct = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/octet-stream'
                            await asyncio.to_thread(db_service.save_resume, candidate['id'], resume_filename, resume_file, ct)
                        except Exception as e:
                            logger.warning(f"Failed to save resume for {candidate.get('id', 'unknown')}: {e}")
                    
                    new_count += 1
                else:
                    # Existing candidate — only update if they have gaps (no score, no skills, etc.)
                    if (existing.get('match_score', 0) or 0) <= 0 or not existing.get('skills'):
                        merged = db_service.smart_merge_candidate(existing, candidate)
                        resume_file = merged.pop('resume_file_data', None)
                        resume_filename = merged.pop('resume_filename', None)
                        await asyncio.to_thread(db_service.update_candidate, merged)
                        updated_count += 1
                
                # Mark as processed
                if msg_id:
                    action = 'refetch-new' if not existing else 'refetch-update'
                    try:
                        await asyncio.to_thread(db_service.mark_email_processed, msg_id, candidate.get('id', ''), action)
                    except Exception as e:
                        logger.debug(f"Non-critical: mark_email_processed failed for {action}: {e}")
                        
            except Exception as e:
                errors += 1
                logger.debug(f"Smart refetch error for message: {str(e)[:100]}")
        
        # Clear cache so new candidates are immediately visible
        response_cache.clear()
        
        result = {
            'status': 'success',
            'total_emails_scanned': total_emails,
            'new_candidates_added': new_count,
            'existing_candidates_updated': updated_count,
            'skipped_already_processed': skipped_already_processed,
            'skipped_no_candidate_data': skipped_no_candidate,
            'skipped_blocked': skipped_blocked,
            'errors': errors,
            'existing_db_candidates': len(existing_emails),
        }
        logger.info(f"🔍 Smart re-fetch complete: {result}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Smart re-fetch error: {e}")
        raise HTTPException(500, "Smart re-fetch error")


# Authentication endpoints

# Login rate limiter: max 5 attempts per email per 15 minutes
_login_attempts: dict = {}  # email -> list of timestamps
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 900  # 15 minutes

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
    - Rate limited: max 5 attempts per 15 minutes per email
    """
    try:
        if not request.email or not request.password:
            raise HTTPException(400, "Email and password are required")
        
        # Rate limiting check
        login_key = request.email.strip().lower()
        now = time.time()
        attempts = _login_attempts.get(login_key, [])
        # Purge old attempts outside the window
        attempts = [t for t in attempts if now - t < _LOGIN_WINDOW_SECONDS]
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "Too many login attempts. Please try again in 15 minutes.")
        
        try:
            result = auth_service.login(request.email, request.password)
            # Clear attempts on successful login
            _login_attempts.pop(login_key, None)
            return result
        except ValueError as e:
            # Record failed attempt
            attempts.append(now)
            _login_attempts[login_key] = attempts
            # Evict stale entries if dict grows too large
            if len(_login_attempts) > 10000:
                _login_attempts.clear()
            raise HTTPException(401, "Invalid credentials")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(500, "Login failed. Please try again later.")

@app.post("/api/auth/register")
async def register(request: RegisterRequest):
    """
    Register new user account
    
    - Creates new user with hashed password
    - Returns user data and JWT access token
    - Email must be unique
    - Registration can be disabled via REGISTRATION_ENABLED=false env var
    """
    try:
        # Gate registration behind env flag (default: enabled for dev, disabled for prod)
        reg_enabled = os.getenv('REGISTRATION_ENABLED', 'true').lower() == 'true'
        if not reg_enabled:
            raise HTTPException(403, "Registration is disabled. Contact an administrator.")
        
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
        raise HTTPException(400, str(e) if str(e) else "Invalid registration data")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(500, "Registration error")

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
        raise HTTPException(400, str(e) if str(e) else "Invalid profile data")
    except Exception as e:
        logger.error(f"Profile update error: {e}")
        raise HTTPException(500, "Error updating profile")

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
        raise HTTPException(400, str(e) if str(e) else "Invalid password data")
    except Exception as e:
        logger.error(f"Password update error: {e}")
        raise HTTPException(500, "Error updating password")

# Candidate management endpoints - status update only, other routes defined earlier
VALID_CANDIDATE_STATUSES = {'New', 'Reviewed', 'Shortlisted', 'Interviewing', 'Offered', 'Hired', 'Rejected', 'Withdrawn', 'Strong', 'Partial', 'Reject'}

class CandidateStatusUpdate(BaseModel):
    status: str  # 'Shortlisted', 'Strong', 'Partial', 'Reject', 'Interviewing', 'Offered', 'Hired', 'Rejected'


async def _send_rejection_email(candidate: Dict):
    """Send a professional, empathetic rejection email to a candidate via Microsoft Graph.
    The email is polite, encourages future applications, and matches the Efforts Solutions brand."""
    try:
        candidate_email = candidate.get('email', '')
        candidate_name = candidate.get('name', 'Candidate')
        if not candidate_email:
            logger.warning(f"⚠️ Cannot send rejection email - no email for {candidate_name}")
            return {'status': 'skipped', 'reason': 'no_email'}

        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        client_secret = os.getenv('MICROSOFT_CLIENT_SECRET')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID')
        sender_email = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL') or _settings.email_address or 'hr@effortz.com'
        company_name = os.getenv('COMPANY_NAME', _settings.company_name) or 'Efforts Solutions'
        recruiter_name = os.getenv('RECRUITER_NAME', _settings.recruiter_name) or 'Recruitment Team'

        if not all([client_id, client_secret, tenant_id]):
            logger.warning("⚠️ Cannot send rejection email - Microsoft Graph credentials not configured")
            return {'status': 'skipped', 'reason': 'no_credentials'}

        job_title = candidate.get('jobCategory', '') or candidate.get('job_category', '') or ''
        job_sub = candidate.get('jobSubcategory', '') or candidate.get('job_subcategory', '') or ''
        display_title = job_sub if job_sub else job_title

        # Personalised subject
        subject = "Application Update - Efforts Solutions"
        if display_title:
            subject = f"Application Update for {display_title} - Efforts Solutions"

        # Professional HTML rejection email — personalised with name and role
        first_name = candidate_name.split()[0] if candidate_name else 'Candidate'
        role_mention = f' for the {display_title} position' if display_title else ''
        body_html = f"""
<div style="font-family: Calibri, 'Segoe UI', Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #000000; max-width: 650px;">
  <p style="margin: 0 0 12px 0;">Hi {first_name},</p>
  <p style="margin: 0 0 12px 0;">&nbsp;</p>
  <p style="margin: 0 0 12px 0;">Thank you for taking the time to apply{role_mention} at {company_name}. We truly appreciate your interest in joining our team and the effort you put into your application.</p>
  <p style="margin: 0 0 12px 0;">After careful review, we have decided to move forward with other candidates whose profile more closely matches the current requirements for this role. Please know that this decision does not diminish the value of your experience and skills.</p>
  <p style="margin: 0 0 12px 0;">We would be happy to consider you for future opportunities that align better with your expertise. We encourage you to keep an eye on our openings and apply again in the future.</p>
  <p style="margin: 0 0 12px 0;">We wish you all the best in your career journey and future endeavours.</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0; color: #808080;">Warm Regards,</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0;"><strong>{recruiter_name}</strong></p>
  <p style="margin: 0; color: #808080;">HR &amp; Admin Department</p>
  <p style="margin: 0; color: #808080;">{company_name}, M12 Burooj Tower, Al Khalidhiya, Abu Dhabi, UAE</p>
  <p style="margin: 0; color: #808080;">T: +971 2 546 8880 | E: <a href="mailto:hr@effortz.com" style="color: #0563C1;">hr@effortz.com</a> | W: <a href="https://effortz.com" style="color: #0563C1;">effortz.com</a> | <a href="https://safeye.ai" style="color: #0563C1;">safeye.ai</a></p>
  <p style="margin: 6px 0 0 0;">
    <strong>ICV Certified</strong> &#9989; | <strong>ISO 9001</strong> &#9989; | <strong>ISO/IEC 27001</strong> &#9989; | <strong>ISO 45001</strong> &#9989; | <strong>MCC Approved</strong> &#9989;
  </p>
  <p style="margin: 4px 0 0 0; font-size: 12px; color: #808080;">Enterprise Solutions | IT Outsourcing | Digital Solutions | ICT/ELV Managed Services | AI/ML/IOT/ERP/ECM/BPM/OCR/RPA</p>
  <hr style="border: none; border-top: 2px solid #1a3c6e; margin: 12px 0 8px 0;" />
  <p style="margin: 0; font-size: 10px; color: #999999;">This email and its contents are confidential and intended solely for the recipient. Sharing this message without the sender's written consent is strictly prohibited. If received in error, please reply and delete it immediately.</p>
</div>"""

        # Setup Graph service and authenticate (same logic as shortlist email)
        graph = MicrosoftGraphService(client_id, client_secret, tenant_id, user_email=sender_email)
        token_storage = get_token_storage()
        token_data = token_storage.get_token(sender_email)

        authenticated = False

        # Strategy 1: App credentials
        if not authenticated:
            try:
                app_result = await graph.authenticate_with_credentials()
                if app_result.get('status') == 'success':
                    authenticated = True
            except Exception:
                pass

        # Strategy 2: Delegated token
        if not authenticated and token_data and token_data.get('access_token'):
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
                authenticated = True

        if not authenticated:
            return {'status': 'failed', 'reason': 'no_token'}

        result = await graph.send_mail(
            to_email=candidate_email,
            subject=subject,
            body=body_html,
            content_type='HTML'
        )

        if result.get('status') == 'success':
            logger.warning(f"✅ Rejection email sent to {candidate_name} ({candidate_email})")
        else:
            logger.warning(f"⚠️ Failed to send rejection email to {candidate_email}: {result.get('message')}")

        return result

    except Exception as e:
        logger.error(f"❌ Error sending rejection email: {str(e)}")
        return {'status': 'error', 'message': str(e)}


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
        sender_email = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL') or _settings.email_address or 'hr@effortz.com'
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

        bullets_html = "".join([f"<p style='margin: 4px 0 4px 10px;'>&bull; {d}</p>" for d in details_requested])

        # Build personalised opening sentence matching the reference template
        # Example: "currently we are looking for immediate an .net core resource for my client office which is based in Abu Dhabi location"
        if display_title and is_uae:
            opening = f"Thank you for your interest, currently we are looking for immediate {display_title} resource for our client office which is based in Abu Dhabi location. If interested, could you please share the following details with us:"
        elif display_title:
            opening = f"Thank you for your interest, currently we are looking for {display_title} resource for our office based in Chennai. If interested, could you please share the following details with us:"
        else:
            opening = "Thank you for your interest. To proceed further, could you please share the following details with us:"

        # Professional HTML email matching Efforts Solutions signature — personalised with role + location
        body_html = f"""
<div style="font-family: Calibri, 'Segoe UI', Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #000000; max-width: 650px;">
  <p style="margin: 0 0 12px 0;">Hi,</p>
  <p style="margin: 0 0 12px 0;">&nbsp;</p>
  <p style="margin: 0 0 12px 0;">{opening}</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  {bullets_html}
  <p style="margin: 12px 0 4px 0;">&nbsp;</p>
  <p style="margin: 0 0 12px 0;">Look forward to your response.</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0; color: #808080;">Best Regards,</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0 0 4px 0;">&nbsp;</p>
  <p style="margin: 0;"><strong>{recruiter_name}</strong></p>
  <p style="margin: 0; color: #808080;">HR &amp; Admin Department</p>
  <p style="margin: 0; color: #808080;">{company_name}, M12 Burooj Tower, Al Khalidhiya, Abu Dhabi, UAE</p>
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

        # Strategy 1: Try application credentials FIRST (most reliable for server-side sending)
        # This works when Mail.Send APPLICATION permission is granted in Azure AD
        if not authenticated:
            try:
                app_result = await graph.authenticate_with_credentials()
                if app_result.get('status') == 'success':
                    logger.info(f"✅ Email auth: app credentials for {sender_email}")
                    authenticated = True
                else:
                    logger.info(f"ℹ️ App credentials unavailable: {app_result.get('error', 'unknown')}")
            except Exception as app_err:
                logger.info(f"ℹ️ App credentials error: {app_err}")

        # Strategy 2: Try delegated token (user's OAuth token)
        if not authenticated and token_data and token_data.get('access_token'):
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
        # Validate status value
        if status_update.status not in VALID_CANDIDATE_STATUSES:
            raise HTTPException(400, f"Invalid status '{status_update.status}'. Must be one of: {', '.join(sorted(VALID_CANDIDATE_STATUSES))}")

        # AUDIT LOG: Record who changed what status and when
        logger.info(f"🔒 STATUS CHANGE AUDIT: candidate={candidate_id} new_status='{status_update.status}' user='{current_user.get('username', 'unknown')}' timestamp={datetime.utcnow().isoformat()}")

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

        # Auto-send email when candidate is shortlisted — send inline so caller gets real status
        if status_update.status.lower() in ('shortlisted', 'shortlist'):
            candidate = await asyncio.to_thread(db_service.get_candidate_by_id, candidate_id)
            if candidate:
                try:
                    email_result = await _send_shortlist_email(candidate)
                    logger.warning(f"📧 Shortlist email result for {candidate.get('name','?')}: {email_result}")
                except Exception as email_err:
                    logger.error(f"❌ Shortlist email error: {email_err}")
                    email_result = {'status': 'error', 'message': str(email_err)}
            else:
                email_result = {'status': 'skipped', 'reason': 'candidate_not_found'}

        # Auto-send rejection email when candidate is rejected
        if status_update.status.lower() in ('rejected', 'reject'):
            candidate = await asyncio.to_thread(db_service.get_candidate_by_id, candidate_id)
            if candidate:
                try:
                    email_result = await _send_rejection_email(candidate)
                    logger.warning(f"📧 Rejection email result for {candidate.get('name','?')}: {email_result}")
                except Exception as email_err:
                    logger.error(f"❌ Rejection email error: {email_err}")
                    email_result = {'status': 'error', 'message': str(email_err)}
            else:
                email_result = {'status': 'skipped', 'reason': 'candidate_not_found'}

        # Persist to GCS immediately so status survives redeploys
        if _settings.is_production:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, backup_db_to_gcs)
            except Exception as gcs_err:
                logger.warning(f"⚠️ Post-status-update GCS backup failed (non-fatal): {gcs_err}")

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
        raise HTTPException(500, "Error updating candidate status")


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
        sender_email = os.getenv('EMAIL_ADDRESS') or os.getenv('IMAP_EMAIL') or _settings.email_address or ''
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
        return {'status': 'error', 'message': 'Email sending failed. Check server logs for details.'}


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
        raise HTTPException(500, "Error generating email template")


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
        # AUDIT LOG: Record bulk shortlist request
        logger.info(f"🔒 BULK SHORTLIST AUDIT: count={len(request.candidate_ids)} user='{current_user.get('username', 'unknown')}' send_emails={request.send_emails} timestamp={datetime.utcnow().isoformat()} ids={request.candidate_ids[:10]}{'...' if len(request.candidate_ids) > 10 else ''}")

        results = []
        shortlisted = 0
        emails_sent = 0
        emails_failed = 0

        # Clear cache upfront so even partial completion reflects in listings
        response_cache.clear()

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
        raise HTTPException(500, "Error in bulk shortlist")


@app.post("/api/candidates/reset-shortlist")
async def reset_all_shortlisted(current_user: dict = Depends(require_auth)):
    """Reset ALL candidates with status 'Shortlisted' back to 'Strong'."""
    try:
        def _reset_shortlisted_db():
            with db_service.get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, name FROM candidates WHERE status = 'Shortlisted' AND is_active = 1"
                )
                rows = cursor.fetchall()
                count = 0
                for row in rows:
                    conn.execute(
                        "UPDATE candidates SET status = 'Strong' WHERE id = ?", (row[0],)
                    )
                    count += 1
                    logger.info(f"Reset shortlist: {row[1]} ({row[0]}) -> Strong")
                conn.commit()
            return rows, count
        rows, count = await asyncio.to_thread(_reset_shortlisted_db)
        response_cache.clear()
        return {"status": "success", "reset_count": count, "candidates": [{"id": r[0], "name": r[1]} for r in rows]}
    except Exception as e:
        logger.error(f"Reset shortlist error: {e}")
        raise HTTPException(500, "Internal server error")


@app.get("/api/audit/shortlist-log")
async def get_shortlist_audit_log(current_user: dict = Depends(require_auth)):
    """Return all currently-shortlisted candidates with their shortlisted_at timestamp for audit purposes."""
    try:
        def _get_log():
            with db_service.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT id, name, email, status, shortlisted_at, last_updated
                    FROM candidates
                    WHERE status = 'Shortlisted' AND is_active = 1
                    ORDER BY shortlisted_at DESC NULLS LAST
                """)
                rows = cursor.fetchall()
                cols = [desc[0] for desc in cursor.description]
                return [dict(zip(cols, row)) for row in rows]
        log = await asyncio.to_thread(_get_log)
        return {"status": "success", "count": len(log), "shortlisted": log}
    except Exception as e:
        logger.error(f"Shortlist audit log error: {e}")
        raise HTTPException(500, "Internal server error")


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
# Bounded to 100 entries to prevent memory leaks from crashed analyses
_analysis_in_progress: dict = {}  # candidate_id -> asyncio.Event
_MAX_CONCURRENT_ANALYSES = 100


@app.post("/api/candidates/{candidate_id}/rescore")
async def rescore_single_candidate(candidate_id: str, current_user: dict = Depends(require_auth)):
    """
    Re-run Gemini AI scoring for a single candidate.
    Updates matchScore, jobCategory, skills, experience in the database.
    Called when user clicks 'Refresh' on candidate detail page.
    """
    try:
        def _get_candidate_for_rescore():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, name, email, skills, summary, education, work_history, "
                    "experience, resume_text, match_score, job_category "
                    "FROM candidates WHERE id = ? AND is_active = 1",
                    (candidate_id,),
                )
                return cursor.fetchone()

        row = await asyncio.to_thread(_get_candidate_for_rescore)
        if not row:
            raise HTTPException(404, "Candidate not found")

        cid, name, email, skills_json, summary, education, work_history, experience, resume_text, old_score, old_category = row

        # Build analysis text — prefer resume_text from PDF
        text_parts = []
        if resume_text and len(str(resume_text).strip()) > 50:
            text_parts.append(str(resume_text)[:3000])
        else:
            if name and name != "Unknown":
                text_parts.append(f"Name: {name}")
            if summary:
                text_parts.append(f"Summary: {summary}")
            try:
                skills = json.loads(skills_json) if isinstance(skills_json, str) else (skills_json or [])
                if skills and isinstance(skills, list) and skills != ["R"]:
                    text_parts.append(f"Skills: {', '.join(skills)}")
            except Exception:
                skills = []
            if education:
                text_parts.append(f"Education: {education}")
            if work_history:
                try:
                    wh = json.loads(work_history) if isinstance(work_history, str) else work_history
                    for job in (wh if isinstance(wh, list) else []):
                        if isinstance(job, dict):
                            text_parts.append(f"Work: {job.get('title', '')} at {job.get('company', '')} ({job.get('duration', '')})")
                except Exception:
                    pass

        analysis_text = "\n".join(text_parts)

        if len(analysis_text.strip()) < 20:
            return {
                "status": "skipped",
                "message": "Not enough data to re-score",
                "matchScore": old_score,
                "jobCategory": old_category,
            }

        # Use Gemini (preferred) or fallback AI service
        rescore_ai = ai_service
        try:
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                rescore_ai = gemini_svc
        except Exception:
            pass

        new_score = old_score
        new_category = old_category
        new_skills = None
        new_experience = None

        try:
            ai_result = await asyncio.wait_for(
                rescore_ai.analyze_candidate(analysis_text),
                timeout=AI_ANALYSIS_TIMEOUT,
            )
            if ai_result:
                raw_score = ai_result.get("quality_score") or ai_result.get("match_score")
                try:
                    parsed_score = int(float(raw_score)) if raw_score else 0
                except (TypeError, ValueError):
                    parsed_score = 0
                if parsed_score > 0:
                    new_score = parsed_score
                else:
                    # AI returned 0 — calculate fallback from AI-extracted data
                    ai_skills_fb = ai_result.get("skills", [])
                    ai_exp_fb = 0
                    try:
                        ai_exp_fb = int(float(ai_result.get("experience", 0) or 0))
                    except (TypeError, ValueError):
                        pass
                    has_edu = bool(ai_result.get("education"))
                    has_certs = bool(ai_result.get("certifications"))
                    has_summary = bool(ai_result.get("summary", "").strip())
                    fb = 25 + min(30, len(ai_skills_fb) * 3) + min(25, ai_exp_fb * 3) + (10 if has_edu else 0) + (5 if has_certs else 0) + (3 if has_summary else 0)
                    new_score = min(90, max(15, fb))
                new_category = ai_result.get("job_category", old_category) or old_category
                ai_skills = ai_result.get("skills", [])
                if isinstance(ai_skills, list) and len(ai_skills) > 0:
                    new_skills = ai_skills
                ai_exp = ai_result.get("experience")
                if ai_exp is not None:
                    try:
                        new_experience = int(float(ai_exp))
                    except (TypeError, ValueError):
                        pass
        except asyncio.TimeoutError:
            logger.warning(f"Rescore timeout for {name}")
        except Exception as e:
            logger.warning(f"Rescore AI error for {name}: {e}")

        # Update database
        def _update_rescore(sid, scat, sskills, sexp, scid):
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                parts = ["match_score = ?", "job_category = ?"]
                vals = [sid, scat]
                if sskills is not None:
                    parts.append("skills = ?")
                    vals.append(json.dumps(sskills))
                if sexp is not None:
                    parts.append("experience = ?")
                    vals.append(sexp)
                vals.append(scid)
                cursor.execute(f"UPDATE candidates SET {', '.join(parts)} WHERE id = ?", vals)
                conn.commit()

        await asyncio.to_thread(_update_rescore, new_score, new_category, new_skills, new_experience, candidate_id)

        # Clear cached AI analysis so it regenerates with new data
        def _clear_ai_analysis(cid):
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE candidates SET ai_analysis = NULL WHERE id = ?", (cid,))
                conn.commit()
        try:
            await asyncio.to_thread(_clear_ai_analysis, candidate_id)
        except Exception:
            pass  # OK if no cached analysis

        logger.info(f"✅ Rescored {name}: {old_score}→{new_score}%, {old_category}→{new_category}")

        return {
            "status": "success",
            "candidate_id": candidate_id,
            "old_score": old_score,
            "new_score": new_score,
            "old_category": old_category,
            "new_category": new_category,
            "matchScore": new_score,
            "jobCategory": new_category,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Rescore error for {candidate_id}: {e}")
        raise HTTPException(500, "Error re-scoring candidate")


@app.get("/api/candidates/{candidate_id}/ai-analysis")
async def get_candidate_ai_analysis(candidate_id: str, refresh: bool = False, current_user: dict = Depends(require_auth)):
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
        # Evict stale entries if dict is too large (prevents memory leak from crashed analyses)
        if len(_analysis_in_progress) >= _MAX_CONCURRENT_ANALYSES:
            # Clear oldest entries (likely stale from crashed requests)
            _analysis_in_progress.clear()
            logger.warning("⚠️ Cleared _analysis_in_progress dict (exceeded max concurrent)")
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
        raise HTTPException(500, "Error generating AI analysis")


async def _run_candidate_analysis(candidate_id: str, refresh: bool = False):
    """Internal: actually run the LLM analysis for a candidate."""
    try:
        # Get full candidate data
        def _get_candidate_for_llm_analysis():
            with db_service.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM candidates WHERE id = ? AND is_active = 1", (candidate_id,))
                row = cursor.fetchone()
            if not row:
                return None
            return db_service._row_to_candidate(row)
        candidate = await asyncio.to_thread(_get_candidate_for_llm_analysis)
        
        if not candidate:
            raise HTTPException(404, "Candidate not found")
        
        # Also try to get resume text for richer analysis
        resume_text = candidate.get('resume_text', '') or ''
        if not resume_text:
            try:
                resume_data = await asyncio.to_thread(db_service.get_resume, candidate_id)
                if resume_data and resume_data.get('file_data'):
                    parsed = await resume_parser.parse_resume(resume_data['file_data'], resume_data['filename'])
                    resume_text = parsed.get('raw_text', '') if parsed else ''
            except Exception as e:
                logger.debug(f"Non-critical: failed to get/parse resume for {candidate_id}: {e}")
        
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
        
        # TIER 2: Fallback — use candidate data to build a meaningful report
        # Rating/recommendation derived from matchScore for consistency
        if not analysis:
            skills = candidate_for_analysis.get('skills', [])
            exp = candidate_for_analysis.get('experience', 0)
            name = candidate_for_analysis.get('name', 'Unknown')
            # FIX: Read 'matchScore' (the actual key set above), not 'match_score'
            match_score = candidate_for_analysis.get('matchScore', candidate_for_analysis.get('match_score', 50))
            location = candidate_for_analysis.get('location', '')
            
            # Derive rating from actual match score so grade aligns with percentage
            if match_score >= 90:
                fb_rating, fb_rec, fb_conf = 'A+', 'STRONGLY_RECOMMEND', 95
            elif match_score >= 80:
                fb_rating, fb_rec, fb_conf = 'A', 'STRONGLY_RECOMMEND', 88
            elif match_score >= 70:
                fb_rating, fb_rec, fb_conf = 'A-', 'RECOMMEND', 80
            elif match_score >= 60:
                fb_rating, fb_rec, fb_conf = 'B+', 'RECOMMEND', 72
            elif match_score >= 50:
                fb_rating, fb_rec, fb_conf = 'B', 'CONSIDER', 65
            elif match_score >= 40:
                fb_rating, fb_rec, fb_conf = 'B-', 'CONSIDER', 55
            elif match_score >= 30:
                fb_rating, fb_rec, fb_conf = 'C+', 'REVIEW', 45
            else:
                fb_rating, fb_rec, fb_conf = 'C', 'REVIEW', 35
            
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
        raise HTTPException(500, "Error generating AI analysis")


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
            result = _quick_fallback_analysis(request.candidate, request.job_description)
            result['source'] = 'fallback_timeout'
                
        except Exception as local_error:
            logger.warning(f"⚠️ Local AI error: {local_error}")
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
    3-TIER FALLBACK: Local AI → Gemini → Rule-based
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
        
        # TIER 2: Rule-based fallback
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
            "note": "Configure Gemini or Ollama for AI-generated interview questions"
        }
    except Exception as e:
        raise HTTPException(500, "Error generating questions")

class SummarizeResumeRequest(BaseModel):
    resume_text: str

@app.post("/api/ai/summarize-resume")
async def summarize_resume(request: SummarizeResumeRequest, current_user: dict = Depends(require_auth)):
    """
    Generate AI summary of resume
    3-TIER FALLBACK: Local AI → Gemini → Rule-based
    """
    try:
        # TIER 1: Try Local AI first (FREE)
        try:
            summary = ai_service.summarize_resume(request.resume_text)
            if summary:
                return {"summary": summary, "source": "local_ai"}
        except Exception as local_error:
            logger.warning(f"⚠️ Local AI summarize failed: {local_error}")
        
        # TIER 2: Rule-based fallback
        text = request.resume_text[:500]
        return {
            "summary": f"Resume summary (basic extraction): {text}...",
            "source": "rule_based",
            "note": "Configure Gemini or Ollama for AI-powered summaries"
        }
    except Exception as e:
        raise HTTPException(500, "Error summarizing resume")

@app.post("/api/ai/batch-analyze")
async def batch_analyze_new_candidates(job_id: str = "general", batch_size: int = 50, current_user: dict = Depends(require_auth)):
    """
    Batch analyze ONLY NEW candidates with CONCURRENT processing
    PRIMARY: Local AI (FREE, handles 100+ concurrent requests)
    FALLBACK: Rule-based (keyword matching)
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
        batch = new_candidates[:batch_size]
        
        async def analyze_one(candidate):
            nonlocal analyzed_count, failed_count, fallback_used
            try:
                # Run CPU-bound Local AI analysis in thread pool to avoid blocking event loop
                result = await asyncio.to_thread(
                    ai_service.analyze_candidate_match,
                    candidate,
                    {"id": job_id, "title": "General Position", "required_skills": []}
                )
                await asyncio.to_thread(db_service.cache_ai_score, candidate['id'], job_id, result)
                analyzed_count += 1
            except Exception as local_error:
                logger.error(f"AI analysis failed for {candidate['id']}: {local_error}")
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
            "ai_engine": "gemini_primary_ollama_fallback",
            "concurrent_processing": True
        }
    except Exception as e:
        raise HTTPException(500, "Error")

@app.get("/api/ai/status")
async def ai_status(current_user: dict = Depends(require_auth)):
    """
    Check AI service status and configuration
    """
    # Get LLM service status
    llm_status = {}
    try:
        from services.llm_service import get_llm_service
        llm_svc = await get_llm_service()
        llm_status = llm_svc.get_status()
    except Exception as e:
        logger.debug(f"Non-critical: LLM service status check failed: {e}")
        llm_status = {'available': False}
    
    # Get local AI cache stats
    ai_cache = {}
    try:
        ai_cache = ai_service.get_cache_stats()
    except Exception as e:
        logger.debug(f"Non-critical: AI cache stats failed: {e}")
    
    return {
        "available": True,
        "ai_tier_mode": _settings.ai_tier_mode,
        "ai_tier_order": _settings.ai_tier_order,
        "environment": "production" if _settings.is_production else "development",
        "primary_engine": _determine_primary_engine(llm_status),
        "fallback_engine": "keyword",
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
        "fallback_model": None,
        "message": _determine_ai_message(llm_status),
        "caching_enabled": True,
        "concurrent_processing": True,
        "max_concurrent": "100+ requests",
        "cost": _determine_cost_info(llm_status),
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
    return "local_ai"


def _determine_model_description(llm_status: Dict) -> str:
    """Dynamic model description based on what's available."""
    parts = []
    if gemini_service and gemini_service.available:
        parts.append(f"Gemini ({gemini_service.model_name})")
    if llm_status.get('available'):
        parts.append(f"Ollama ({llm_status.get('primary_model', 'local')})")
    parts.extend(["Sentence-Transformers", "SpaCy NER", "Keyword"])
    return "Multi-Tier AI: " + " → ".join(parts)


def _determine_ai_message(llm_status: Dict) -> str:
    """Generate AI status message based on current configuration."""
    primary = _determine_primary_engine(llm_status)
    if primary == "gemini":
        return f"🌟 AI Stack: Gemini {gemini_service.model_name} (primary) + Local Embeddings + NER"
    elif primary == "ollama_llm":
        return "🤖 AI Stack: Local LLM + Embeddings + NER (FREE) with Gemini fallback"
    return "⚡ AI Stack: Sentence-Transformers + SpaCy NER + Keyword (FREE, no LLM)"


def _determine_cost_info(llm_status: Dict) -> str:
    """Generate cost information string."""
    primary = _determine_primary_engine(llm_status)
    if primary == "gemini":
        return "~$0.01-0.05/day (Gemini 2.5 Flash is very low cost)"
    elif primary == "ollama_llm":
        return "$0 (all local, Gemini fallback charges only if local AI fails)"
    return "$0 (all local, no API costs)"

@app.get("/api/llm/status")
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
async def classify_title(title: str = Body(..., embed=True), current_user: dict = Depends(require_auth)):
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
    # Validate inputs
    if not query or not query.strip():
        raise HTTPException(400, "Query cannot be empty")
    query = query.strip()[:2000]
    top_n = max(1, min(100, top_n))
    
    try:
        # 1. Get active candidates (lightweight for AI matching)
        candidates = await asyncio.to_thread(
            db_service.get_candidates_lightweight, {}, 5000
        )
        if not candidates:
            return {"results": [], "total": 0, "query": query, "message": "No candidates in database"}

        # 2. Try Gemini-based matching first (cost-effective, always available)
        try:
            from services.gemini_service import get_gemini_service
            gemini_svc = get_gemini_service()
            if gemini_svc and gemini_svc.available:
                ranked = await asyncio.wait_for(
                    gemini_svc.rank_candidates_for_job(candidates, query, top_n),
                    timeout=45
                )
                if ranked:
                    formatted = _format_search_results(ranked, candidates)
                    return {
                        "results": formatted,
                        "total_searched": len(candidates),
                        "query": query,
                        "source": "gemini",
                        "message": f"Found {len(formatted)} matches using Gemini AI search"
                    }
        except asyncio.TimeoutError:
            logger.warning("Gemini smart search timed out after 45s")
        except Exception as gemini_err:
            logger.warning(f"Gemini smart search failed: {gemini_err}")

        # 3. Try Local LLM matching (for local dev)
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc and llm_svc.available:
                ranked = await asyncio.wait_for(
                    llm_svc.rank_candidates_for_job(candidates, query, top_n),
                    timeout=30
                )
                formatted = _format_search_results(ranked, candidates)
                return {
                    "results": formatted,
                    "total_searched": len(candidates),
                    "query": query,
                    "source": "local_llm",
                    "message": f"Found {len(formatted)} matches using AI search"
                }
        except asyncio.TimeoutError:
            logger.warning("LLM smart search timed out after 30s")
        except Exception as llm_err:
            logger.warning(f"LLM smart search failed: {llm_err}")

        # 4. Try matching engine (semantic / TF-IDF)
        try:
            matching_engine = MatchingEngine()
            results = await asyncio.wait_for(
                matching_engine.match_candidates(query, candidates, top_n),
                timeout=20
            )
            formatted = _format_search_results(results, candidates)
            return {
                "results": formatted,
                "total_searched": len(candidates),
                "query": query,
                "source": "semantic",
                "message": f"Found {len(formatted)} matches using semantic search"
            }
        except asyncio.TimeoutError:
            logger.warning("Semantic search timed out after 20s")
        except Exception as sem_err:
            logger.warning(f"Semantic search failed: {sem_err}")

        # 5. Basic keyword fallback
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
            # Location matching in keyword fallback
            c_location = str(c.get('location', '')).lower()
            if c_location:
                for q_word in q_lower.split():
                    if len(q_word) > 2 and q_word in c_location:
                        score += 15
                        match_reasons.append(f"Location: {c.get('location', '')}")
                        break
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
        raise HTTPException(500, "Internal server error")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
