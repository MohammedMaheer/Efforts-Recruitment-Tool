"""Lifespan management: GCS persistence, background tasks, startup/shutdown."""
import os
import json
import asyncio
import logging
import time
import re
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

from core.config import get_settings
from core.db_wrapper import IS_POSTGRES
from services.database_service import get_db_service
from services.email_scraper import get_scraper_service
from services.gemini_service import get_gemini_service
from services.token_storage import get_token_storage
from services.microsoft_graph import MicrosoftGraphService
from services.oauth_automation_service import get_oauth_automation, OAuthAutomationService
from services.auth_service import get_auth_service
from services.db_repair import audit_database, repair_database, quick_health_check
from services.followup_service import get_followup_service, run_campaign_processor
from services.sms_notification_service import get_sms_service
from services.email_templates_service import get_templates_service

logger = logging.getLogger(__name__)
_settings = get_settings()

# ── Module-level state ──────────────────────────────────────────────────
background_sync_task = None
oauth_automation_service: OAuthAutomationService = None
_persistent_tasks: set = set()
_last_email_sync_time: str = None
_db_backup_task = None

# GCS configuration
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "efforts-recruitment-ai-data")
GCS_DB_BLOB_PATH = "db/recruitment.db"
LOCAL_DB_PATH = "./recruitment.db"


# ── GCS Persistence ─────────────────────────────────────────────────────

def _get_instance_id() -> str:
    """Stable-ish instance identifier for distributed task leasing."""
    return f"{os.getenv('K_REVISION', 'local')}:{os.getenv('HOSTNAME', 'host')}"


async def _acquire_distributed_lease(key: str, ttl_seconds: int = 120) -> bool:
    """
    Acquire/renew a DB-backed lease.
    Returns True if current instance owns the lease and may execute work.
    """
    instance_id = _get_instance_id()
    now_epoch = int(time.time())
    expires_epoch = now_epoch + ttl_seconds
    db_service = get_db_service()

    def _acquire() -> bool:
        with db_service.get_connection() as conn:
            cursor = conn.cursor()
            ph = '%s' if IS_POSTGRES else '?'
            cursor.execute(f"SELECT value FROM sync_metadata WHERE key = {ph}", [key])
            row = cursor.fetchone()

            if row and row[0]:
                try:
                    owner, exp = str(row[0]).split('|', 1)
                    exp_i = int(exp)
                    if exp_i > now_epoch and owner != instance_id:
                        return False
                except Exception:
                    pass

            if IS_POSTGRES:
                cursor.execute(
                    "INSERT INTO sync_metadata (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    [key, f"{instance_id}|{expires_epoch}"]
                )
            else:
                cursor.execute(
                    "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES (?, ?)",
                    [key, f"{instance_id}|{expires_epoch}"]
                )
            conn.commit()
            return True

    try:
        return await asyncio.to_thread(_acquire)
    except Exception as e:
        logger.warning(f"Lease acquire failed for {key}: {e}")
        return True


def _get_gcs_bucket():
    """Get GCS bucket client (lazy import to avoid startup failure if not installed)"""
    try:
        from google.cloud import storage
        client = storage.Client()
        return client.bucket(GCS_BUCKET_NAME)
    except Exception as e:
        logger.warning(f"GCS: Could not connect to bucket '{GCS_BUCKET_NAME}': {e}")
        return None


def restore_db_from_gcs():
    """Download recruitment.db from GCS on startup (blocking, runs before DB init)"""
    if IS_POSTGRES:
        logger.info("GCS restore: Skipped (using PostgreSQL)")
        return False
    if not _settings.is_production:
        logger.info("GCS restore: Skipped (not production)")
        return False
    try:
        bucket = _get_gcs_bucket()
        if not bucket:
            logger.info("GCS restore: No bucket connection")
            return False
        blob = bucket.blob(GCS_DB_BLOB_PATH)
        if not blob.exists():
            logger.info("GCS restore: No database backup found - starting fresh")
            return False

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

        for suffix in ['-wal', '-shm']:
            wal_path = LOCAL_DB_PATH + suffix
            if os.path.exists(wal_path):
                try:
                    os.remove(wal_path)
                except Exception as e:
                    logger.debug(f"Non-critical: failed to remove {wal_path}: {e}")

        if os.path.exists(LOCAL_DB_PATH):
            try:
                os.remove(LOCAL_DB_PATH)
            except Exception as e:
                logger.warning(f"Failed to remove existing DB file: {e}")

        blob.download_to_filename(LOCAL_DB_PATH)
        size_mb = os.path.getsize(LOCAL_DB_PATH) / (1024 * 1024)
        import sqlite3
        try:
            conn = sqlite3.connect(LOCAL_DB_PATH)
            count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
            conn.close()
            if count == 0:
                logger.warning(f"GCS restore: DB downloaded ({size_mb:.1f} MB) but has 0 candidates - will try JSON seed")
                os.remove(LOCAL_DB_PATH)
                return False
            logger.info(f"GCS restore: Downloaded recruitment.db ({size_mb:.1f} MB, {count} candidates)")
            return True
        except Exception as db_err:
            logger.warning(f"GCS restore: DB downloaded but validation failed: {db_err} - will try JSON seed")
            if os.path.exists(LOCAL_DB_PATH):
                os.remove(LOCAL_DB_PATH)
            return False
    except Exception as e:
        logger.error(f"GCS restore failed: {e}")
        return False


def backup_db_to_gcs():
    """Upload recruitment.db to GCS (blocking)"""
    if IS_POSTGRES:
        return False
    try:
        if not os.path.exists(LOCAL_DB_PATH):
            logger.warning("GCS backup: No local database file found")
            return False
        bucket = _get_gcs_bucket()
        if not bucket:
            return False
        blob = bucket.blob(GCS_DB_BLOB_PATH)
        blob.upload_from_filename(LOCAL_DB_PATH, timeout=120)
        size_mb = os.path.getsize(LOCAL_DB_PATH) / (1024 * 1024)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_blob = bucket.blob(f"db/snapshots/recruitment_{ts}.db")
        snapshot_blob.upload_from_filename(LOCAL_DB_PATH, timeout=120)
        logger.info(f"GCS backup: Uploaded recruitment.db ({size_mb:.1f} MB) + snapshot")
        try:
            blobs = list(bucket.list_blobs(prefix="db/snapshots/"))
            if len(blobs) > 3:
                blobs.sort(key=lambda b: b.name)
                for old_blob in blobs[:-3]:
                    old_blob.delete()
                    logger.info(f"GCS: Deleted old snapshot {old_blob.name}")
        except Exception as e:
            logger.debug(f"Non-critical: failed to clean old GCS snapshots: {e}")
        return True
    except Exception as e:
        logger.error(f"GCS backup failed: {e}")
        return False


async def periodic_db_backup(interval_minutes: int = 30):
    """Periodically backup the database to GCS"""
    while True:
        try:
            await asyncio.sleep(interval_minutes * 60)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, backup_db_to_gcs)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Periodic DB backup error: {e}")
            await asyncio.sleep(60)


# ── Background Tasks ────────────────────────────────────────────────────

async def auto_sync_emails():
    """
    FULLY AUTOMATED email sync with OAuth2 Client Credentials Flow.
    NO user intervention required - authenticates automatically using app credentials.

    Uses incremental sync: first run fetches all emails, subsequent runs only fetch
    emails received AFTER the last successful sync (using receivedDateTime filter).

    IDEMPOTENT: Tracks processed email message IDs in `email_processing_log` table.
    """
    global _last_email_sync_time
    from api.deps import response_cache, AI_ANALYSIS_TIMEOUT

    db_service = get_db_service()
    scraper_service = get_scraper_service()

    # Wait 5 seconds before first sync to allow server to fully start
    await asyncio.sleep(5)

    _is_first_sync = True
    _last_email_sync_time = None

    # ── Restore watermark from DB so restarts use incremental sync ──
    try:
        stored_watermark = await asyncio.to_thread(db_service.get_sync_metadata, 'last_email_sync_time')
        if stored_watermark:
            _last_email_sync_time = stored_watermark
            _is_first_sync = False
            logger.info(f"Startup: Resuming incremental sync from watermark {_last_email_sync_time}")
        else:
            logger.info("Startup: No watermark found — performing first full inbox scan")
    except Exception as e:
        logger.warning(f"Startup: Could not load sync watermark (will do full scan): {e}")

    # Startup: clear only orphaned processing entries (entries with no matching candidate)
    # Do NOT clear blocked entries — they represent legitimate rejections that should not be retried
    try:
        orphaned = await asyncio.to_thread(db_service.clear_orphaned_processing_entries)
        if orphaned > 0:
            logger.warning(f"Startup: cleared {orphaned} orphaned processing entries (candidates lost during restore)")
    except Exception as e:
        logger.warning(f"Startup orphan clearing failed (non-fatal): {e}")

    async def _run_single_sync():
        """Execute a single sync cycle (Graph API + IMAP fallback)."""
        global _last_email_sync_time
        nonlocal _is_first_sync
        try:
            logger.info("Auto-sync: Starting email sync...")

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

                    needs_new_token = (
                        not token_data or
                        token_data.get('is_expired', True) or
                        not token_data.get('access_token')
                    )

                    if needs_new_token:
                        logger.info(f"Authenticating for {primary_email}...")
                        refresh_token = token_data.get('refresh_token') if token_data else None
                        auth_success = False

                        if refresh_token:
                            logger.info("Attempting delegated token refresh (refresh_token)...")
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
                                logger.info(f"Delegated token refreshed for {primary_email} - using /me/ endpoint")
                            else:
                                logger.warning(f"Refresh token failed: {refresh_result.get('error', 'unknown')}")

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
                            logger.warning(f"Refresh token failed for {primary_email}. Trying app credentials fallback...")
                            try:
                                cred_result = await graph_service.authenticate_with_credentials()
                                if cred_result.get('status') == 'success':
                                    auth_success = True
                                    logger.warning(f"Authenticated via APP CREDENTIALS for email sync (Mail.Read permission)")
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

                    if token_data and token_data.get('access_token') and not token_data.get('is_expired', True):
                        logger.info(f"Using OAuth2 ({token_data.get('auth_type', 'unknown')}) for {primary_email}...")

                        graph_service.access_token = token_data['access_token']
                        graph_service.auth_type = token_data.get('auth_type', 'delegated')
                        graph_service.token_expiry = token_data.get('expires_at_dt', datetime.now() + timedelta(hours=1))

                        # Get AI service for candidate analysis
                        ai_service = None
                        try:
                            gemini_svc = get_gemini_service()
                            if gemini_svc and gemini_svc.available:
                                ai_service = gemini_svc
                        except Exception:
                            pass
                        if not ai_service:
                            from services.local_ai_service import get_local_ai_service
                            ai_service = get_local_ai_service()

                        processed_count_before = await asyncio.to_thread(
                            lambda: db_service.get_processed_email_count()
                        )

                        logger.info(f"Starting paged email sync (already processed: {processed_count_before})...")

                        sync_start_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

                        new_count = 0
                        total_fetched = 0
                        consecutive_all_seen = 0

                        async def process_graph_message(msg):
                            """Process a single Graph API email message into a candidate."""
                            nonlocal new_count
                            try:
                                msg_id = msg.get('id', '') or msg.get('internetMessageId', '')
                                if not msg_id:
                                    import hashlib
                                    dedup_input = f"{msg.get('from', {}).get('emailAddress', {}).get('address', '')}{msg.get('subject', '')}"
                                    msg_id = f"gen_{hashlib.sha256(dedup_input.encode()).hexdigest()[:16]}"
                                if await asyncio.to_thread(db_service.is_email_processed, msg_id):
                                    return 'seen'

                                sender = msg.get('from', {}).get('emailAddress', {})
                                sender_email = sender.get('address', '')
                                sender_name = sender.get('name', sender_email.split('@')[0])

                                subject = msg.get('subject', '')
                                body = msg.get('body', {}).get('content', '')

                                has_attachments = msg.get('hasAttachments', False)
                                attachments = []

                                if has_attachments:
                                    attach_result = await graph_service.get_message_with_attachments(msg['id'])
                                    if attach_result['status'] == 'success':
                                        attachments = attach_result['attachments']
                                    else:
                                        # Retry once after a short delay before giving up
                                        await asyncio.sleep(2)
                                        attach_result2 = await graph_service.get_message_with_attachments(msg['id'])
                                        if attach_result2['status'] == 'success':
                                            attachments = attach_result2['attachments']
                                        else:
                                            logger.warning(
                                                f"Attachment fetch failed for msg {msg['id'][:20]} "
                                                f"from {sender_email}: {attach_result2.get('error', 'unknown')}. "
                                                "Candidate will be stored without resume."
                                            )

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

                                _pre_subj_lower = subject.lower() if subject else ''
                                _pre_sender_lower = sender_name.lower().strip() if sender_name else ''
                                _pre_email_lower = sender_email.lower() if sender_email else ''

                                _notification_patterns = [
                                    r'^your\s+job[,:]',
                                    r'you\s+have\s+\d+\s+new\s+applicants',
                                    r'^your\s+sponsored\s+job',
                                    r'^your\s+posting',
                                    r'job\s+performance\s+report',
                                    r'^hiring\s+insights',
                                    r'^budget\s+alert',
                                    r'^find\s+your\s+next\s+star',
                                    r'^your\s+jobs\s+are\s+on',
                                    r'^confirm\s+your\s+account',
                                    r'^welcome\s+to\s+microsoft',
                                    r'^password\s+reset',
                                    r'^verify\s+your\s+email',
                                    r'^sign.in\s+activity',
                                    r'^security\s+alert',
                                    r'^unusual\s+sign.in',
                                    r'^your\s+invoice',
                                    r'^your\s+subscription',
                                    r'^payment\s+received',
                                    r'^billing\s+statement',
                                    r'^receipt\s+for\s+your',
                                    r'^order\s+confirm',
                                    r'^undeliverable:',
                                    r'wants\s+to\s+access',
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

                                _job_board_domains = [
                                    'indeed.com', 'linkedin.com', 'glassdoor.com', 'ziprecruiter.com',
                                    'naukri.com', 'bayt.com', 'gulftalent.com', 'monster.com',
                                    'careerbuilder.com', 'dice.com', 'reed.co.uk', 'seek.com',
                                    'the-candidates.com', 'thecandidates.com',
                                ]
                                _is_job_board = any(d in _pre_email_lower for d in _job_board_domains)
                                if not _is_job_board:
                                    _system_senders = ['noreply', 'no-reply', 'postmaster', 'mailer-daemon',
                                                       'notifications', 'system', 'donotreply', 'do-not-reply']
                                    if any(s in _pre_email_lower for s in _system_senders):
                                        return 'no-candidate'

                                candidate = await scraper_service.extract_candidate_from_email(email_data)
                                if not candidate or not candidate.get('email'):
                                    return 'no-candidate'

                                if db_service.is_blocked_email(candidate['email']):
                                    logger.debug(f"Blocked Indeed relay candidate: {candidate['email'][:50]}")
                                    if msg_id:
                                        try:
                                            await asyncio.to_thread(
                                                db_service.mark_email_processed,
                                                msg_id, '', 'blocked-indeed-relay'
                                            )
                                        except Exception as e:
                                            logger.debug(f"Non-critical: mark_email_processed failed for indeed relay: {e}")
                                    return 'blocked'

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
                                    'candidate', 'applicant', 'resume', 'cv', 'cover letter',
                                    'naukri', 'bayt', 'gulftalent', 'ziprecruiter', 'careerbuilder',
                                    'jobstreet', 'seek', 'reed', 'totaljobs', 'cwjobs',
                                    'user', 'guest', 'subscriber', 'member',
                                ]
                                if any(pat in _email_lower for pat in _BLOCKED_EMAIL_PATTERNS):
                                    logger.debug(f"Blocked system/noreply email: {candidate['email'][:50]}")
                                    if msg_id:
                                        try:
                                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, '', 'blocked-system-email')
                                        except Exception as e:
                                            logger.debug(f"Non-critical: mark_email_processed failed for system email: {e}")
                                    return 'blocked'

                                if _name_lower in _BLOCKED_NAMES or len(_name_lower) < 2:
                                    logger.debug(f"Blocked trash candidate name: '{candidate.get('name')}'")
                                    if msg_id:
                                        try:
                                            await asyncio.to_thread(db_service.mark_email_processed, msg_id, '', 'blocked-bad-name')
                                        except Exception as e:
                                            logger.debug(f"Non-critical: mark_email_processed failed for bad name: {e}")
                                    return 'blocked'

                                existing = await asyncio.to_thread(db_service.get_candidate_by_email, candidate['email'])

                                needs_ai = False
                                if not existing:
                                    needs_ai = True
                                    new_count += 1
                                else:
                                    candidate = db_service.smart_merge_candidate(existing, candidate)
                                    existing_score = existing.get('matchScore') or existing.get('match_score') or 0
                                    if (not existing.get('ai_analysis')
                                            or existing_score <= 0):
                                        needs_ai = True

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
                                            ai_skills = ai_analysis.get('skills', []) or []
                                            existing_skills = candidate.get('skills', []) or []
                                            merged_skills = ai_skills if len(ai_skills) >= len(existing_skills) else existing_skills
                                            ai_exp = ai_analysis.get('experience', 0) or 0
                                            existing_exp = candidate.get('experience', 0) or 0
                                            merged_exp = max(ai_exp, existing_exp)
                                            curr_name = candidate.get('name', '')
                                            ai_name = ai_analysis.get('name', '')
                                            if ai_name and (not curr_name or curr_name.lower() == 'unknown' or '.' in curr_name.split()[0] if curr_name else True):
                                                candidate['name'] = ai_name
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
                                            logger.info(f"AI scored {candidate.get('name')}: {score}%")
                                            _ms = len(merged_skills)
                                            _me = merged_exp
                                            _floor = 25 + min(30, _ms * 3) + min(25, _me * 3) + (10 if candidate.get('education') else 0)
                                            _floor = min(90, max(15, _floor))
                                            if candidate['matchScore'] < _floor:
                                                logger.info(f"Score boosted for {candidate.get('name')}: {candidate['matchScore']} -> {_floor} (skills={_ms}, exp={_me})")
                                                candidate['matchScore'] = _floor
                                                candidate['status'] = 'Strong' if _floor >= 70 else ('Partial' if _floor >= 40 else 'Reject')
                                            if candidate.get('job_category') == 'General' and _ms >= 3:
                                                _sl = {s.lower() for s in merged_skills}
                                                _tech = {'python','java','javascript','react','angular','vue','node','django','flask','.net','c#','c++','typescript','php','ruby','swift','kotlin','sql','mongodb','postgresql','docker','kubernetes','aws','azure','gcp','html','css','git','rest','api','spring','fastapi','express'}
                                                _data = {'power bi','tableau','pandas','numpy','tensorflow','pytorch','machine learning','data science','spark','hadoop','etl'}
                                                _sec = {'penetration testing','soc','siem','firewall','cybersecurity','nmap','wireshark'}
                                                if len(_sl & _tech) >= 3:
                                                    candidate['job_category'] = 'Software Engineering'
                                                elif len(_sl & _data) >= 2:
                                                    candidate['job_category'] = 'Data & Analytics'
                                                elif len(_sl & _sec) >= 2:
                                                    candidate['job_category'] = 'Cybersecurity'
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
                                            logger.info(f"Fallback score for {candidate.get('name')}: {candidate['matchScore']}% (from {len(skills)} skills, {exp}yr exp)")
                                        else:
                                            candidate['matchScore'] = 20

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

                                resume_file = candidate.pop('resume_file_data', None)
                                resume_filename = candidate.pop('resume_filename', None)

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
                                        logger.warning(f"mark_email_processed failed for {msg_id[:20]}: {e} — email may be reprocessed on next sync")

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

                        # Stream pages: fetch -> process -> discard
                        filter_query = None
                        if _is_first_sync:
                            sync_max_pages = 99999
                            logger.info("FULL INBOX SYNC: fetching ALL emails from entire inbox history")
                        elif _last_email_sync_time:
                            filter_query = f"receivedDateTime ge {_last_email_sync_time}"
                            sync_max_pages = 300
                            logger.info(f"Incremental sync: emails since {_last_email_sync_time}")
                        else:
                            sync_max_pages = 99999
                            logger.info("First sync: fetching ALL inbox emails")

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

                                if page_seen == page_size_actual and not _is_first_sync:
                                    consecutive_all_seen += 1
                                    early_stop_threshold = 3
                                    logger.info(f"Page fully seen ({consecutive_all_seen}/{early_stop_threshold}) - {page_seen}/{page_size_actual} already processed")
                                    if consecutive_all_seen >= early_stop_threshold:
                                        logger.info(f"Stopping: reached {early_stop_threshold} consecutive pages of old emails")
                                        break
                                else:
                                    if not _is_first_sync:
                                        consecutive_all_seen = 0
                                    if page_new > 0:
                                        logger.info(f"Page {page_count}: {page_new} new candidates, {page_seen} old")
                                    else:
                                        logger.info(f"Page {page_count}: processing {page_size_actual} (first sync)")

                            total_processed_after = await asyncio.to_thread(lambda: db_service.get_processed_email_count())
                            newly_processed = total_processed_after - processed_count_before
                            logger.warning(f"OAuth2 sync: {primary_email} - {total_fetched} fetched, {new_count} new candidates, {newly_processed} newly processed")
                            oauth2_success = True
                            _last_email_sync_time = sync_start_time
                            if _is_first_sync:
                                _is_first_sync = False
                                logger.info(f"First full inbox sync complete - switching to incremental mode")
                            try:
                                await asyncio.to_thread(db_service.set_sync_metadata, 'last_email_sync_time', sync_start_time)
                            except Exception as e:
                                logger.debug(f"Non-critical: set_sync_metadata failed: {e}")
                            if new_count > 0:
                                response_cache.clear()
                                logger.info(f"Cache cleared after adding {new_count} new candidates")

                        except Exception as fetch_err:
                            error_msg = str(fetch_err)
                            logger.warning(f"OAuth2 paged fetch failed: {error_msg}")

                            if '403' in error_msg and token_data.get('auth_type') == 'application':
                                logger.info("=" * 70)
                                logger.info("APPLICATION PERMISSIONS NOT CONFIGURED IN AZURE AD")
                                logger.info("=" * 70)
                                logger.info("")
                                logger.info("OPTION 1: Enable FULLY AUTOMATIC sync (recommended if you have Azure admin)")
                                logger.info("   1. Go to: Azure Portal -> App Registrations -> AI Recruitment Tool")
                                logger.info("   2. Click: API Permissions -> Add a permission")
                                logger.info("   3. Select: Microsoft Graph -> Application permissions")
                                logger.info("   4. Add: Mail.Read and Mail.ReadBasic")
                                logger.info("   5. Click: 'Grant admin consent for [Organization]'")
                                logger.info("")
                                logger.info("OPTION 2: Authenticate ONCE via frontend (if no Azure admin access)")
                                logger.info(f"   1. Open: {os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')[0]}")
                                logger.info("   2. Go to: Settings -> Email Integration")
                                logger.info("   3. Click: Connect Microsoft Account")
                                logger.info("   4. Sign in and grant permissions")
                                logger.info("   -> After this ONE-TIME login, auto-refresh works FOREVER")
                                logger.info("")
                                logger.info("=" * 70)
                                if token_data.get('auth_type') == 'application' and not token_data.get('refresh_token'):
                                    token_storage.delete_token(primary_email)
                            elif '400' in error_msg or '404' in error_msg:
                                logger.info("=" * 70)
                                logger.info("EMAIL SYNC REQUIRES MICROSOFT OAUTH LOGIN")
                                logger.info("=" * 70)
                                logger.info("")
                                logger.info("The email address may not be an Azure AD mailbox.")
                                logger.info("To sync emails, complete the ONE-TIME Microsoft OAuth login:")
                                logger.info(f"   1. Open: {os.getenv('CORS_ORIGINS', 'http://localhost:3000').split(',')[0]}")
                                logger.info("   2. Go to: Settings -> Email Integration")
                                logger.info("   3. Click: Connect Microsoft Account")
                                logger.info("   4. Sign in with your Microsoft/Outlook account")
                                logger.info("   -> After this ONE-TIME login, auto-refresh works FOREVER")
                                logger.info("=" * 70)
                                if token_data.get('auth_type') == 'application' and not token_data.get('refresh_token'):
                                    token_storage.delete_token(primary_email)
                            elif 'token' in error_msg.lower() or 'unauthorized' in error_msg.lower():
                                logger.info("Token issue detected - only clearing application tokens")
                                if token_data.get('auth_type') == 'application' and not token_data.get('refresh_token'):
                                    token_storage.delete_token(primary_email)

                except Exception as oauth_error:
                    logger.error(f"OAuth2 sync error: {str(oauth_error)}")

            if not oauth2_success:
                logger.warning("Microsoft Graph sync did not complete - verify Microsoft OAuth2 credentials are configured. IMAP has been removed.")

        except Exception as e:
            logger.error(f"Sync cycle error: {str(e)}")

    while True:
        try:
            has_sync_lease = await _acquire_distributed_lease('lease:auto_sync', ttl_seconds=120)
            if not has_sync_lease:
                await asyncio.sleep(15)
                continue

            if _is_first_sync:
                try:
                    await asyncio.wait_for(_run_single_sync(), timeout=600)
                except asyncio.TimeoutError:
                    logger.error("First email sync timed out after 600s — will retry next cycle")
            else:
                try:
                    await asyncio.wait_for(_run_single_sync(), timeout=600)
                except asyncio.TimeoutError:
                    logger.error("Email sync timed out after 600s - will retry next cycle")

            sync_interval = int(os.getenv('SYNC_INTERVAL_MINUTES', str(_settings.sync_interval_minutes))) * 60
            logger.info(f"Auto-sync: Next sync in {sync_interval//60} minutes")
            await asyncio.sleep(sync_interval)

        except Exception as e:
            logger.error(f"Auto-sync background task error: {str(e)}")
            await asyncio.sleep(60)


async def _background_seed_from_json():
    """Seed database from JSON backup in GCS - runs as background task after server starts"""
    if IS_POSTGRES:
        logger.info("[BG] JSON seed skipped (using PostgreSQL)")
        return
    await asyncio.sleep(2)
    try:
        logger.info("[BG] Starting JSON seed from GCS...")
        import sqlite3, hashlib as _hashlib

        bucket = _get_gcs_bucket()
        if not bucket:
            logger.warning("[BG] No GCS bucket available")
            return

        json_blob = bucket.blob("backups/candidates_backup.json")
        if not json_blob.exists():
            logger.warning("[BG] No JSON backup found in GCS")
            return

        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.json') as _tf:
            tmp_path = _tf.name

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, json_blob.download_to_filename, tmp_path)
        file_size = os.path.getsize(tmp_path) / (1024*1024)
        logger.info(f"[BG] Downloaded JSON backup ({file_size:.1f} MB)")

        def _do_seed():
            with open(tmp_path, 'r', encoding='utf-8-sig') as jf:
                backup_data = json.load(jf)

            candidates = backup_data.get('candidates', [])
            logger.info(f"[BG] Found {len(candidates)} candidates")

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
                        logger.info(f"[BG] Progress: {count}/{len(candidates)}...")
                except Exception as ins_err:
                    errors += 1
                    if errors <= 3:
                        logger.warning(f"[BG] Insert error #{errors}: {ins_err}")

            conn.commit()
            conn.close()
            logger.info(f"[BG] Seeded {count}/{len(candidates)} candidates ({errors} errors)")
            return count

        count = await loop.run_in_executor(None, _do_seed)
        os.unlink(tmp_path)

        if count and count > 0:
            await loop.run_in_executor(None, backup_db_to_gcs)
            logger.info("[BG] Database backed up to GCS")
    except Exception as e:
        logger.error(f"[BG] JSON seed failed: {e}")
        import traceback
        traceback.print_exc()


async def _background_process_candidates(interval_minutes: int = 5):
    """
    Background task that continuously processes unprocessed candidates.
    Checks ALL candidates with missing ai_analysis or low match_score,
    processes them using Gemini, runs every 5 minutes.
    """
    from api.deps import AI_ANALYSIS_TIMEOUT
    await asyncio.sleep(60)
    db_service = get_db_service()

    while True:
        try:
            has_bg_lease = await _acquire_distributed_lease('lease:bg_process', ttl_seconds=300)
            if not has_bg_lease:
                await asyncio.sleep(interval_minutes * 60)
                continue

            loop = asyncio.get_running_loop()

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
                            OR job_category IS NULL OR job_category = ''
                        )
                        ORDER BY created_at DESC
                    """)
                    rows = [dict(r) for r in cursor.fetchall()]
                    return rows

            unprocessed = await loop.run_in_executor(None, _get_unprocessed)

            if unprocessed:
                logger.info(f"[BG-Process] Found {len(unprocessed)} unprocessed candidates, starting batch AI analysis...")

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
                            resume_text = candidate.get('resume_text', '') or ''
                            skills = candidate.get('skills', '') or ''
                            summary = candidate.get('summary', '') or ''

                            analysis_text = resume_text[:10000] if resume_text else f"Name: {candidate.get('name', '')}\nSkills: {skills}\nExperience: {candidate.get('experience', '')}\nLocation: {candidate.get('location', '')}\nSummary: {summary}"

                            if len(analysis_text.strip()) < 20:
                                continue

                            result = await asyncio.wait_for(
                                ai_service.analyze_candidate(analysis_text),
                                timeout=AI_ANALYSIS_TIMEOUT
                            )

                            if result:
                                def _update_candidate(cid, ai_result):
                                    import json as _json
                                    with db_service.get_connection() as conn:
                                        raw_score = ai_result.get('match_score') or ai_result.get('quality_score') or ai_result.get('overall_score')
                                        try:
                                            match_score = int(float(raw_score)) if raw_score is not None else 0
                                        except (ValueError, TypeError):
                                            match_score = 0
                                        if match_score <= 0:
                                            ai_skills = ai_result.get('skills', [])
                                            ai_exp = ai_result.get('experience', 0) or 0
                                            if isinstance(ai_exp, str):
                                                try: ai_exp = int(float(ai_exp))
                                                except (ValueError, TypeError): ai_exp = 0
                                            has_edu = bool(ai_result.get('education'))
                                            has_certs = bool(ai_result.get('certifications'))
                                            has_summary = bool(ai_result.get('summary', '').strip())
                                            match_score = 25 + min(30, len(ai_skills) * 3) + min(25, ai_exp * 3) + (10 if has_edu else 0) + (5 if has_certs else 0) + (3 if has_summary else 0)
                                            match_score = min(90, max(15, match_score))
                                        job_category = ai_result.get('job_category', ai_result.get('category', '')) or ''

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

                        except asyncio.TimeoutError:
                            logger.warning(f"[BG-Process] Timeout processing {candidate.get('name', 'unknown')}")
                        except Exception as proc_err:
                            logger.warning(f"[BG-Process] Error processing {candidate.get('name', 'unknown')}: {proc_err}")

                    if processed > 0:
                        logger.info(f"[BG-Process] Processed {processed}/{len(unprocessed)} candidates")
                        try:
                            await loop.run_in_executor(None, backup_db_to_gcs)
                        except Exception as _backup_err:
                            logger.warning(f"[BG-Process] GCS backup failed: {_backup_err}")
                else:
                    logger.info("[BG-Process] No AI service available for processing")

            await asyncio.sleep(interval_minutes * 60)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[BG-Process] Error: {e}")
            await asyncio.sleep(300)


# ── Lifespan Context Manager ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    """Lifespan event handler for startup/shutdown"""
    global background_sync_task, oauth_automation_service, _db_backup_task
    from api.deps import response_cache, MAX_CONCURRENT_REQUESTS, init_services

    # Initialize shared service singletons used by all route modules
    init_services()

    db_service = get_db_service()
    scraper_service = get_scraper_service()

    logger.info("AI Recruitment Platform Starting...")

    # Restore database from GCS BEFORE anything else
    db_restored = restore_db_from_gcs()

    # After GCS restore, close stale connections
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
            logger.info("Cleared stale DB connections after GCS restore")
        except Exception as _pool_err:
            logger.warning(f"Pool clear: {_pool_err}")

    # Re-initialize all tables after GCS restore
    try:
        _db_svc = get_db_service()
        _db_svc.init_database()
        logger.info("Database tables initialized (email_processing_log, sync_metadata, etc.)")
    except Exception as _init_err:
        logger.warning(f"DB table init: {_init_err}")

    # Optional one-time reset for clean full re-sync
    _reset_run_id = (os.getenv('FORCE_RESET_RUN_ID') or '').strip()
    if _reset_run_id:
        try:
            _reset_svc = get_db_service()
            _already_done = False
            with _reset_svc.get_connection() as _conn:
                _cur = _conn.cursor()
                _cur.execute("SELECT value FROM sync_metadata WHERE key = 'last_force_reset_run_id'")
                _row = _cur.fetchone()
                if _row and str(_row[0]) == _reset_run_id:
                    _already_done = True

            if _already_done:
                logger.info(f"FORCE_RESET_RUN_ID={_reset_run_id} already applied previously; skipping reset")
            else:
                logger.warning(f"FORCE_RESET_RUN_ID={_reset_run_id} detected; wiping candidate data for clean re-sync")
                with _reset_svc.get_connection() as _conn:
                    _cur = _conn.cursor()
                    for _table in ['ai_score_cache', 'resumes', 'email_processing_log', 'search_history', 'candidates']:
                        try:
                            _cur.execute(f"DELETE FROM {_table}")
                        except Exception as _tbl_err:
                            logger.warning(f"Reset skip {_table}: {_tbl_err}")
                    _cur.execute("DELETE FROM sync_metadata WHERE key = 'last_email_sync_time'")
                    _cur.execute(
                        "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES ('last_force_reset_run_id', ?)",
                        [_reset_run_id]
                    )
                    _conn.commit()
                logger.warning("One-time reset complete; service will re-fetch full inbox from scratch")
        except Exception as _reset_err:
            logger.error(f"One-time reset failed: {_reset_err}")

    # Ensure users table exists AFTER GCS restore
    try:
        _auth_svc = get_auth_service()
        _auth_svc._init_users_table()
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
                        logger.warning("No ADMIN_PASSWORD env var set - generated random password. Set ADMIN_PASSWORD env var for production.")
                    _auth_svc.register(
                        email=_admin_email,
                        password=_admin_password,
                        name=os.getenv('ADMIN_NAME', 'Admin User'),
                        username=os.getenv('ADMIN_USERNAME', 'admin')
                    )
                    logger.info(f"Admin user created: {_admin_email}")
                else:
                    logger.info(f"Users table OK ({_user_count} users)")
        except Exception as _user_err:
            logger.warning(f"Admin user seed: {_user_err}")
    except Exception as _tbl_err:
        logger.warning(f"Users table init failed: {_tbl_err}")

    _seed_needed = not db_restored and _settings.is_production

    logger.info(f"Environment: {'Production' if _settings.is_production else 'Development'}")
    logger.info(f"AI Tier Mode: {_settings.ai_tier_mode} -> {' -> '.join(_settings.ai_tier_order)}")

    # Initialize Gemini
    gemini_service = get_gemini_service()
    if gemini_service and gemini_service.available:
        logger.info(f"Gemini: {gemini_service.model_name} (ready)")
    elif _settings.gemini_api_key:
        logger.warning("Gemini: API key set but service failed to initialize")
    else:
        logger.info("Gemini: not configured (set GEMINI_API_KEY for cloud deployment)")

    # Initialize Local LLM (Ollama) - SKIP in production
    if _settings.is_production:
        logger.info("LLM: Ollama SKIPPED (production - using Gemini + sentence-transformers)")
    else:
        try:
            from services.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            if llm_svc.available:
                logger.info(f"LLM: Ollama connected! Primary: {llm_svc.primary_model}")
                logger.info(f"   Models: {', '.join(llm_svc.available_models)}")
            else:
                logger.warning("LLM: Ollama not available - using sentence-transformers + regex")
                logger.warning("   Install: https://ollama.com/download -> ollama pull qwen2.5:7b")
        except Exception as e:
            logger.warning(f"LLM initialization skipped: {e}")

    logger.info(f"Email Accounts: {len(scraper_service.email_accounts)} configured")
    logger.info(f"Max Concurrent Requests: {MAX_CONCURRENT_REQUESTS}")

    # Initialize OAuth Automation Service
    oauth_automation_service = get_oauth_automation()

    async def _init_oauth_background():
        """Initialize OAuth in background so server starts accepting requests immediately"""
        try:
            if oauth_automation_service.is_configured:
                logger.info(f"OAuth2 Automation: Configured for {oauth_automation_service.primary_email}")
                try:
                    auth_status = await asyncio.wait_for(
                        oauth_automation_service.check_auth_status(),
                        timeout=10
                    )
                    logger.info(f"OAuth2 Status: {auth_status.value}")

                    if auth_status.value in ['expired', 'no_token']:
                        result = await asyncio.wait_for(
                            oauth_automation_service.ensure_valid_token(),
                            timeout=15
                        )
                        if result['status'] == 'success':
                            logger.info(f"OAuth2 auto-authenticated successfully")
                        else:
                            logger.warning(f"OAuth2 auto-auth failed: {result.get('message')} - manual auth may be needed")
                except asyncio.TimeoutError:
                    logger.warning("OAuth2 initialization timed out - will retry during sync")
            else:
                logger.info("OAuth2 Automation: Not configured (missing credentials)")
        except Exception as e:
            logger.warning(f"OAuth2 background init error: {e}")

    _oauth_task = asyncio.create_task(_init_oauth_background())
    _persistent_tasks.add(_oauth_task)
    _oauth_task.add_done_callback(_persistent_tasks.discard)

    # Auto-sync
    auto_sync_enabled = os.getenv('AUTO_SYNC_ENABLED', 'true').lower() == 'true'
    has_email_accounts = len(scraper_service.email_accounts) > 0

    if auto_sync_enabled and (has_email_accounts or oauth_automation_service.is_configured):
        logger.info(f"Auto-sync: ENABLED (every {os.getenv('SYNC_INTERVAL_MINUTES', '15')} minutes)")
        try:
            background_sync_task = asyncio.create_task(auto_sync_emails())
            _persistent_tasks.add(background_sync_task)
            background_sync_task.add_done_callback(_persistent_tasks.discard)
            logger.info("Email sync: Single unified loop started")
        except Exception as e:
            logger.error(f"Failed to start auto-sync: {str(e)}")
    else:
        logger.info("Auto-sync: DISABLED (no email accounts or OAuth configured)")

    # Initialize advanced services
    try:
        followup_service = get_followup_service()
        sms_service = get_sms_service()
        templates_service = get_templates_service()
        followup_service.set_services(
            email_service=templates_service,
            sms_service=sms_service
        )
        logger.info("Advanced services initialized (ML, Analytics, Campaigns, SMS)")

        campaign_task = asyncio.create_task(run_campaign_processor(interval_seconds=3600))
        _persistent_tasks.add(campaign_task)
        campaign_task.add_done_callback(_persistent_tasks.discard)
        logger.info("Campaign processor started (checks every 60 minutes)")
    except Exception as e:
        logger.warning(f"Advanced services initialization warning: {str(e)}")

    # Start periodic GCS database backup
    if _settings.is_production:
        _db_backup_task = asyncio.create_task(periodic_db_backup(interval_minutes=120))
        _persistent_tasks.add(_db_backup_task)
        _db_backup_task.add_done_callback(_persistent_tasks.discard)
        logger.info("GCS auto-backup: Enabled (every 2 hours)")

    # Launch background seed from JSON if no DB was restored
    _seed_task = None
    if _seed_needed:
        logger.info("Launching background JSON seed task...")
        _seed_task = asyncio.create_task(_background_seed_from_json())
        _persistent_tasks.add(_seed_task)
        _seed_task.add_done_callback(_persistent_tasks.discard)

    # Launch background candidate processing
    _process_task = asyncio.create_task(_background_process_candidates(interval_minutes=5))
    _persistent_tasks.add(_process_task)
    _process_task.add_done_callback(_persistent_tasks.discard)
    logger.info("Background candidate processing: Enabled (every 5 minutes, unlimited batch)")

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
                    f"DB health: {health['issue_count']} issues found "
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
                    f"Auto-repair done: {repair_result['total_fixed']} fixed, "
                    f"{repair_result['summary']['deleted']} deleted, "
                    f"{repair_result['summary']['encoding_fixed']} encoding fixed, "
                    f"{repair_result['summary']['names_recovered']} names recovered"
                )
            else:
                logger.info(f"DB health OK: {health['total_candidates']} candidates, no issues")
        except Exception as _repair_err:
            logger.warning(f"Auto-repair skipped: {_repair_err}")
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

    _repair_task = asyncio.create_task(_auto_repair_on_startup())
    _persistent_tasks.add(_repair_task)
    _repair_task.add_done_callback(_persistent_tasks.discard)

    logger.info("Server ready")

    yield

    # Shutdown
    logger.info("Shutting down gracefully...")

    # Cancel all tracked background tasks to prevent mid-write DB corruption
    tasks_to_cancel = [t for t in _persistent_tasks if not t.done()]
    for t in tasks_to_cancel:
        t.cancel()
    if tasks_to_cancel:
        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

    if oauth_automation_service:
        await oauth_automation_service.stop()

    if _settings.is_production:
        logger.info("Saving database to GCS before shutdown...")
        try:
            await asyncio.wait_for(asyncio.to_thread(backup_db_to_gcs), timeout=8.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"GCS shutdown backup skipped: {e}")

    response_cache.clear()
