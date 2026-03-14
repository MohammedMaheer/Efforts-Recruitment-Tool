"""Route module: email. Auto-extracted from main_legacy.py."""
import os
import json
import asyncio
import hmac
import logging
import time
from core.lifespan import backup_db_to_gcs
import re
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends, UploadFile, File, Body, Query
from fastapi.responses import Response, JSONResponse, StreamingResponse, RedirectResponse
from services.microsoft_graph import MicrosoftGraphService
from services.token_storage import get_token_storage
from api.deps import db_semaphore

from core.config import get_settings
from core.dependencies import require_auth, optional_auth, require_admin
from models.schemas import EmailConnectRequest, EmailSyncRequest, OAuth2CallbackRequest

logger = logging.getLogger(__name__)
_settings = get_settings()
_last_email_sync_time = None

router = APIRouter(tags=["email"])


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

def _oauth_automation():
    """Get the OAuth automation service singleton from lifespan."""
    from core.lifespan import oauth_automation_service
    return oauth_automation_service


# ---- Helpers ported from main_legacy.py ----

async def trigger_reset_and_reparse(email_address: str, incremental: bool = False):
    """
    Full inbox cross-verify: page through ALL inbox emails, compare with
    email_processing_log, and process any missing/unprocessed candidates.
    Uses smart_merge for existing candidates. Marks every message processed.

    When incremental=True (used by Cloud Scheduler cron), only fetches emails
    since last sync time -- much faster for periodic runs.
    Returns a result dict when called inline (e.g. from cron endpoint).
    """
    global _last_email_sync_time
    if not hasattr(trigger_reset_and_reparse, '_lock'):
        trigger_reset_and_reparse._lock = asyncio.Lock()
    if trigger_reset_and_reparse._lock.locked():
        logger.info("Sync already in progress, skipping duplicate request")
        return
    await trigger_reset_and_reparse._lock.acquire()
    try:
        logger.info("Full inbox cross-verify started (incremental -- keeping existing data)...")

        _cache().clear()

        current_count = await asyncio.to_thread(_db().get_total_candidates)
        logger.info(f"Starting with {current_count} existing candidates in database")

        processed_ids = await asyncio.to_thread(_db().get_all_processed_message_ids)
        logger.info(f"Already processed: {len(processed_ids)} emails in log")

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

        token_expired = token_data.get('is_expired', True) or not token_data.get('access_token')
        if not token_expired:
            try:
                expires_at = datetime.fromisoformat(token_data['expires_at'])
                if expires_at < datetime.now() + timedelta(minutes=5):
                    token_expired = True
            except Exception:
                token_expired = True

        if token_expired:
            logger.warning(f"Cross-verify: token expired for {email_address}, refreshing...")
            refresh_success = False

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
                        logger.warning("Cross-verify: delegated token refreshed successfully")
                except Exception as e:
                    logger.warning(f"Cross-verify: delegated refresh failed: {e}")

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
                        logger.warning("Cross-verify: authenticated via app credentials")
                except Exception as e:
                    logger.warning(f"Cross-verify: app credentials failed: {e}")

            if not refresh_success:
                logger.warning("Cross-verify: ALL authentication methods failed -- aborting")
                return

        graph_service.access_token = token_data['access_token']
        graph_service.auth_type = saved_auth_type
        try:
            graph_service.token_expiry = datetime.fromisoformat(token_data['expires_at'])
        except Exception:
            graph_service.token_expiry = datetime.now() + timedelta(hours=1)

        logger.warning(f"Cross-verify: using {saved_auth_type} auth -- {'incremental' if incremental else 'full'} mode")

        new_count = 0
        updated_count = 0
        skipped_count = 0
        error_count = 0
        total_fetched = 0

        filter_query = None
        scan_max_pages = 200
        if incremental and _last_email_sync_time:
            filter_query = f"receivedDateTime ge {_last_email_sync_time}"
            scan_max_pages = 60
            logger.info(f"Incremental: emails since {_last_email_sync_time}")
        else:
            logger.info("Full scan: paging through entire inbox")

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
                        import hashlib as _hashlib
                        dedup_input = f"{msg.get('from', {}).get('emailAddress', {}).get('address', '')}{msg.get('subject', '')}"
                        msg_id = f"gen_{_hashlib.sha256(dedup_input.encode()).hexdigest()[:16]}"

                    if msg_id in processed_ids:
                        skipped_count += 1
                        continue

                    sender = msg.get('from', {}).get('emailAddress', {})
                    sender_email = sender.get('address', '')
                    sender_name = sender.get('name', sender_email.split('@')[0] if sender_email else '')
                    subject = msg.get('subject', '') or ''
                    body = msg.get('body', {}).get('content', '') or ''

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

                    candidate = await _scraper().extract_candidate_from_email(email_data)
                    if not candidate or not candidate.get('email'):
                        if not hasattr(trigger_reset_and_reparse, '_fail_count'):
                            trigger_reset_and_reparse._fail_count = 0
                        trigger_reset_and_reparse._fail_count += 1
                        if trigger_reset_and_reparse._fail_count <= 10 or trigger_reset_and_reparse._fail_count % 100 == 0:
                            logger.warning(f"Extraction failed #{trigger_reset_and_reparse._fail_count}: '{subject[:60]}' from {sender_email} (body: {len(body)} chars, attachments: {len(attachments)})")
                        continue

                    if _db().is_blocked_email(candidate['email']):
                        if msg_id:
                            try:
                                await asyncio.to_thread(_db().mark_email_processed, msg_id, '', 'blocked-relay')
                                processed_ids.add(msg_id)
                            except Exception as e:
                                logger.debug(f"Non-critical: mark_email_processed failed for blocked-relay: {e}")
                        continue

                    existing = await asyncio.to_thread(_db().get_candidate_by_email, candidate['email'])

                    needs_ai = False
                    if not existing:
                        needs_ai = True
                    else:
                        candidate = _db().smart_merge_candidate(existing, candidate)
                        if (not existing.get('ai_analysis')
                                and (existing.get('matchScore') or existing.get('match_score') or 0) <= 0):
                            needs_ai = True

                    analysis_text = candidate.get('resume_text') or candidate.get('summary', '')
                    if analysis_text:
                        candidate['resume_text'] = analysis_text[:5000]

                    if needs_ai and analysis_text and len(analysis_text) > 20:
                        try:
                            # Pass job context so Gemini scores role fit, not just generic quality
                            _job_ctx = candidate.get('job_applied_for') or candidate.get('job_category') or None
                            ai_analysis = await asyncio.wait_for(
                                _ai().analyze_candidate(analysis_text, job_context=_job_ctx),
                                timeout=_deps().AI_ANALYSIS_TIMEOUT
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
                                logger.info(f"AI scored {candidate.get('name')}: {score}%")
                        except Exception as ai_err:
                            logger.warning(f"AI error for cross-verify ({type(ai_err).__name__}): {str(ai_err)[:80]}")
                            skills = candidate.get('skills', [])
                            exp = candidate.get('experience', 0)
                            if skills or exp:
                                fb = 35.0 + min(30, len(skills) * 2.5 + (10 if skills else 0)) + (min(20, 6 + exp * 2) if exp else 0)
                                candidate['matchScore'] = min(90, round(fb, 1))
                            else:
                                candidate['matchScore'] = 45

                    if candidate.get('matchScore', 0) == 0:
                        candidate['matchScore'] = 35

                    resume_file = candidate.pop('resume_file_data', None)
                    resume_filename = candidate.pop('resume_filename', None)

                    if existing:
                        await asyncio.to_thread(_db().update_candidate, candidate)
                        updated_count += 1
                    else:
                        await asyncio.to_thread(_db().insert_candidate, candidate)
                        new_count += 1

                    if resume_file and resume_filename:
                        try:
                            ct = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                            await asyncio.to_thread(_db().save_resume, candidate['id'], resume_filename, resume_file, ct)
                        except Exception as e:
                            logger.warning(f"Failed to save resume for {candidate.get('id', 'unknown')}: {e}")

                    if msg_id:
                        action = 'updated' if existing else 'inserted'
                        try:
                            await asyncio.to_thread(_db().mark_email_processed, msg_id, candidate.get('id', ''), action)
                            processed_ids.add(msg_id)
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for {action}: {e}")

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
                            await asyncio.to_thread(_db().save_ai_analysis, candidate.get('id', ''), {
                                'score': _score, 'job_category': candidate.get('job_category', 'General'),
                                'summary': candidate.get('summary', ''), 'skills': _skills,
                                'experience': _exp, 'strengths': _strengths[:5], 'gaps': _gaps[:5],
                                'analyzed_at': datetime.now().isoformat(),
                            })
                        except Exception as e:
                            logger.warning(f"Failed to save AI analysis for {candidate.get('id', 'unknown')}: {e}")

                    del body, attachments, email_data
                    if 'candidate' in dir(): del candidate
                    if 'analysis_text' in dir(): del analysis_text
                    if 'attach_result' in dir(): del attach_result

                    _emails_processed_this_page = new_count + updated_count + error_count
                    if _emails_processed_this_page % 10 == 0 and _emails_processed_this_page > 0:
                        import gc
                        gc.collect()

                except Exception as e:
                    error_count += 1
                    logger.warning(f"Cross-verify error: {str(e)[:100]}")

            page_seen = sum(1 for _ in [m for m in page if (m.get('id', '') or m.get('internetMessageId', '')) in processed_ids])
            if page_seen == len(page):
                consecutive_all_seen += 1
                if incremental and consecutive_all_seen >= 3:
                    logger.info("Incremental: 3 pages all already-processed - stopping early")
                    break
            else:
                consecutive_all_seen = 0

            if total_fetched % 250 < len(page):
                import gc
                gc.collect()
                try:
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

            if total_fetched % 200 < len(page):
                logger.warning(f"Cross-verify progress: {total_fetched} scanned, {new_count} new, {updated_count} updated, {skipped_count} already-processed, {error_count} errors")

        final_count = await asyncio.to_thread(_db().get_total_candidates)
        logger.warning(f"Cross-verify complete! {total_fetched} emails scanned, {new_count} new, {updated_count} updated, {skipped_count} skipped, {error_count} errors")
        logger.warning(f"Database: {current_count} -> {final_count} candidates")

        _last_email_sync_time = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        try:
            await asyncio.to_thread(_db().set_sync_metadata, 'last_email_sync_time', _last_email_sync_time)
        except Exception as e:
            logger.debug(f"Non-critical: set_sync_metadata failed after cross-verify: {e}")

        if new_count > 0:
            _cache().clear()
            logger.info(f"Cache cleared after adding {new_count} new candidates")

        if _settings.is_production:
            try:
                logger.info("Backing up DB to GCS after sync...")
                await asyncio.to_thread(backup_db_to_gcs)
                logger.info("Post-sync GCS backup complete")
            except Exception as _bk_err:
                logger.error(f"Post-sync GCS backup failed: {_bk_err}")

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


async def _backfill_resumes_task(email_address: str):
    """
    Backfill resumes using the email_processing_log message_id->candidate_id
    mapping.  For every candidate that has no resume stored, fetch the
    original email by its Graph API message ID, grab any resume-type
    attachment and store it.
    """
    import base64 as b64
    import requests
    try:
        logger.warning("Resume backfill v2 started (message-id based)")

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
        _exp_raw = token_data.get('expires_at') or token_data.get('expires_at_dt')
        if _exp_raw:
            try:
                graph_service.token_expiry = (
                    datetime.fromisoformat(str(_exp_raw).replace('Z', '+00:00'))
                    if isinstance(_exp_raw, str) else _exp_raw
                )
            except Exception:
                graph_service.token_expiry = datetime.now() + timedelta(hours=1)
        else:
            graph_service.token_expiry = datetime.now() + timedelta(hours=1)

        msg_candidates = await asyncio.to_thread(_db().get_candidate_message_ids)
        existing_resumes = await asyncio.to_thread(_db().get_all_resume_candidate_ids)

        need_resume: dict = {}
        for msg_id, cid in msg_candidates:
            if cid not in existing_resumes and cid not in need_resume:
                need_resume[cid] = msg_id

        total = len(need_resume)
        logger.warning(
            f"{len(msg_candidates)} msg->candidate pairs, "
            f"{len(existing_resumes)} already have resumes, "
            f"{total} need resume backfill"
        )

        if total == 0:
            return {
                'status': 'completed', 'emails_scanned': 0,
                'resumes_stored': 0, 'already_had': len(existing_resumes),
                'errors': 0, 'message': 'All candidates already have resumes or no mappings found'
            }

        headers = {
            'Authorization': f'Bearer {graph_service.access_token}',
            'Content-Type': 'application/json'
        }

        async def _refresh_token():
            nonlocal headers
            rt = token_data.get('refresh_token')
            if not rt:
                logger.warning("No refresh_token available -- cannot renew")
                return False
            result = await graph_service.refresh_access_token(rt)
            if result.get('status') == 'success':
                headers['Authorization'] = f"Bearer {result['access_token']}"
                token_storage.save_token(
                    email=email_address,
                    access_token=result['access_token'],
                    refresh_token=result.get('refresh_token', rt),
                    expires_in=result.get('expires_in', 3600),
                    auth_type='delegated'
                )
                logger.warning("OAuth2 token refreshed mid-backfill")
                return True
            logger.warning(f"Token refresh failed: {result.get('error', 'unknown')}")
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
                meta_url = f"{base}/{message_id}?$select=id,hasAttachments"
                meta_resp = await asyncio.to_thread(
                    lambda u=meta_url: requests.get(u, headers=headers, timeout=30)
                )

                if meta_resp.status_code == 429:
                    retry_after = int(meta_resp.headers.get('Retry-After', '30'))
                    logger.warning(f"Rate limited, sleeping {retry_after}s (checked {checked}/{total})")
                    await asyncio.sleep(retry_after)
                    meta_resp = await asyncio.to_thread(
                        lambda u=meta_url: requests.get(u, headers=headers, timeout=30)
                    )

                if meta_resp.status_code == 404:
                    skipped_no_attach += 1
                    continue

                if meta_resp.status_code == 401:
                    if await _refresh_token():
                        meta_resp = await asyncio.to_thread(
                            lambda u=meta_url: requests.get(u, headers=headers, timeout=30)
                        )
                        if meta_resp.status_code in (401, 403):
                            errors += 1
                            if errors <= 5:
                                logger.warning(f"401 even after refresh for {candidate_id}")
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

                att_url = f"{base}/{message_id}/attachments"
                att_resp = await asyncio.to_thread(
                    lambda u=att_url: requests.get(u, headers=headers, timeout=60)
                )

                if att_resp.status_code == 429:
                    retry_after = int(att_resp.headers.get('Retry-After', '30'))
                    logger.warning(f"Rate limited on attachments, sleeping {retry_after}s")
                    await asyncio.sleep(retry_after)
                    att_resp = await asyncio.to_thread(
                        lambda u=att_url: requests.get(u, headers=headers, timeout=60)
                    )

                att_resp.raise_for_status()
                attachments = att_resp.json().get('value', [])

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
                        _db().save_resume, candidate_id, filename, file_bytes, ct
                    )
                    existing_resumes.add(candidate_id)
                    stored += 1
                    found_resume = True
                    break

                if not found_resume:
                    skipped_no_resume += 1

                await asyncio.sleep(0.15)

            except Exception as e:
                errors += 1
                if errors <= 10:
                    logger.warning(f"Backfill error [{type(e).__name__}] cid={candidate_id} mid={message_id[:40]}: {str(e)[:120]}")

            if checked % 100 == 0:
                logger.warning(
                    f"Backfill progress: {checked}/{total} checked, "
                    f"{stored} stored, {skipped_no_attach} no-attach, "
                    f"{skipped_no_resume} no-resume, {errors} errors"
                )

            if stored > 0 and stored % 200 == 0 and stored != last_upload_count:
                try:
                    await asyncio.to_thread(backup_db_to_gcs)
                    last_upload_count = stored
                    logger.warning(f"Periodic DB upload at {stored} resumes stored")
                except Exception as ue:
                    logger.warning(f"Periodic DB upload failed at {stored}: {str(ue)[:100]}")

        if stored > 0:
            try:
                await asyncio.to_thread(backup_db_to_gcs)
                logger.warning(f"DB uploaded to GCS after {stored} resume backfills")
            except Exception as e:
                logger.warning(f"Failed to upload DB to GCS after resume backfill: {e}")

        logger.warning(
            f"Resume backfill v2 complete: {checked}/{total} checked, "
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


@router.post("/api/email/connect")
async def connect_email_account(request: EmailConnectRequest, current_user: dict = Depends(require_auth)):
    """
    Connect email account (Gmail, Outlook, Yahoo, etc.)
    Supports OAuth2 and app passwords
    """
    try:
        result = await _email_parser().connect_email_account(
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



@router.post("/api/email/sync")
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
                if msg_id and await asyncio.to_thread(_db().is_email_processed, msg_id):
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

                candidate_info = await _scraper().extract_candidate_from_email(email_data)
                if candidate_info and candidate_info.get('email'):
                    candidates.append(candidate_info)
                    # Mark as processed
                    if msg_id:
                        try:
                            await asyncio.to_thread(_db().mark_email_processed, msg_id, candidate_info.get('id', ''), 'manual-sync')
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for manual-sync: {e}")
            
            return {
                'status': 'success',
                'candidates_found': len(candidates),
                'candidates': candidates,
                'auth_type': 'oauth2'
            }
        
        # IMAP Mode - Traditional password authentication
        connection_result = await _email_parser().connect_email_account(
            provider=request.provider,
            email_address=request.email,
            password=request.password,
            access_token=None
        )
        
        if connection_result['status'] != 'connected':
            raise HTTPException(400, connection_result.get('error', 'Connection failed'))
        
        # Fetch and parse emails
        mail_connection = connection_result['connection']
        candidates = await _email_parser().fetch_candidate_emails(
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
                    'experience': candidate.get('extracted_info', {}).get('experience', 0),
                    'skills': candidate.get('extracted_info', {}).get('skills', []),
                    'education': candidate.get('extracted_info', {}).get('education', []),
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
                                _ai().analyze_candidate(resume_content),
                                timeout=_deps().AI_ANALYSIS_TIMEOUT
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
                    existing = await asyncio.to_thread(_db().get_candidate_by_email, candidate_data['email'])
                    if existing:
                        await asyncio.to_thread(_db().update_candidate, candidate_data)
                    else:
                        await asyncio.to_thread(_db().insert_candidate, candidate_data)
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



@router.post("/api/auth/auto-authenticate")
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




@router.get("/api/oauth2/callback")
async def oauth2_callback_get(code: str = None, error: str = None, error_description: str = None):
    """
    Handle OAuth2 GET redirect from Microsoft.
    Microsoft sends: GET /api/oauth2/callback?code=...&state=...
    We exchange the code for a token server-side and redirect to the frontend.
    """
    frontend_url = os.getenv('CORS_ORIGINS', 'https://efforts-recruitment-ai.web.app').split(',')[0].strip()
    
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




@router.get("/api/email/oauth2/url")
async def get_oauth2_url_simple(request: Request = None, current_user: dict = Depends(require_auth)):
    """
    Get Microsoft OAuth2 authorization URL using config from .env
    Simple endpoint - no parameters needed. Auto-detects production redirect URI.
    """
    try:
        client_id = os.getenv('MICROSOFT_CLIENT_ID')
        tenant_id = os.getenv('MICROSOFT_TENANT_ID', 'common')
        # Use env var if set, otherwise derive from CORS_ORIGINS (same logic as GET callback)
        frontend_url = os.getenv('CORS_ORIGINS', 'https://efforts-recruitment-ai.web.app').split(',')[0].strip()
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



@router.get("/api/email/oauth2/authorize")
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



@router.post("/api/email/oauth2/callback")
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
            
            # Notify _oauth_automation() to refresh its cached auth status
            if _oauth_automation():
                try:
                    await _oauth_automation().check_auth_status()
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



@router.post("/api/email/sync-now")
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



@router.post("/api/email/deep-sync")
async def deep_sync_emails(current_user: dict = Depends(require_admin)):
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
        cleared_blocked = await asyncio.to_thread(_db().clear_all_blocked_entries)
        logger.warning(f"Deep sync: cleared {cleared_blocked} blocked/failed entries from processing log")
        
        # Clear orphaned entries: emails marked "inserted"/"updated" but candidate record lost
        # This handles candidates lost during DB restore from GCS
        cleared_orphaned = await asyncio.to_thread(_db().clear_orphaned_processing_entries)
        logger.warning(f"Deep sync: cleared {cleared_orphaned} orphaned entries (candidate records lost)")
        
        # Get current counts
        total_before = await asyncio.to_thread(_db().get_total_candidates)
        processed_before = await asyncio.to_thread(_db().get_processed_email_count)
        
        # Run full (non-incremental) cross-verify inline (CPU throttling safe)
        result = await trigger_reset_and_reparse(email_address, incremental=False)
        
        total_after = await asyncio.to_thread(_db().get_total_candidates)
        
        return {
            'status': 'deep-sync-completed',
            'message': f'Deep sync completed. Cleared {cleared_blocked} blocked + {cleared_orphaned} orphaned log entries. Candidates: {total_before} -> {total_after}.',
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



@router.post("/api/email/cross-verify")
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
        total_candidates = await asyncio.to_thread(_db().get_total_candidates)
        processed_emails = await asyncio.to_thread(_db().get_processed_email_count)

        # Run full cross-verify inline (CPU throttling safe)
        result = await trigger_reset_and_reparse(email_address)
        
        total_after = await asyncio.to_thread(_db().get_total_candidates)

        return {
            'status': 'cross-verify-completed',
            'message': f'Full inbox cross-verify completed. DB: {total_candidates} -> {total_after} candidates, {processed_emails} emails processed.',
            'candidates_before': total_candidates,
            'processed_emails': processed_emails,
            'email': email_address
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cross-verify error: {str(e)}")
        raise HTTPException(500, "Error starting cross-verify")




@router.post("/api/email/backfill-resumes")
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




@router.get("/api/email/backfill-debug")
async def backfill_debug(current_user: dict = Depends(require_admin)):
    """
    Diagnostic endpoint: inspect message_id format and test one Graph API lookup.
    """
    import traceback as tb
    import requests as req
    results = {"steps": []}

    # 1) Get sample message_ids from DB
    msg_candidates = await asyncio.to_thread(_db().get_candidate_message_ids)
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




@router.post("/api/cron/sync")
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
            persisted_time = await asyncio.to_thread(_db().get_sync_metadata, 'last_email_sync_time')
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
        # Also check actual expiry clock — stored is_expired flag may be stale
        if not needs_refresh and token_data.get('expires_at'):
            try:
                from datetime import datetime as _dt, timedelta as _td
                exp = _dt.fromisoformat(token_data['expires_at'].replace('Z', '+00:00'))
                exp_naive = exp.replace(tzinfo=None)
                if exp_naive < _dt.utcnow() + _td(minutes=5):
                    needs_refresh = True
            except Exception:
                needs_refresh = True
        
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




@router.get("/api/email/sync-status")
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
                persisted = await asyncio.to_thread(_db().get_sync_metadata, 'last_email_sync_time')
                if persisted:
                    _last_email_sync_time = persisted
            except Exception as e:
                logger.debug(f"Non-critical: failed to load sync metadata: {e}")
        
        candidate_count = await asyncio.to_thread(lambda: _db().get_total_candidates())
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




# ---- Webhook helper ----
async def process_single_email(message_id: str, graph_service):
    """
    Process a single email immediately when it arrives
    Used for real-time notifications
    """
    try:
        if await asyncio.to_thread(_db().is_email_processed, message_id):
            return None
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
        candidate = await _scraper().extract_candidate_from_email(email_data)
        if not candidate or not candidate.get('email'):
            return None
        
        # Check if exists
        existing = await asyncio.to_thread(_db().get_candidate_by_email, candidate['email'])
        
        # AI processing for new candidates
        if candidate.get('resume_text'):
            try:
                _job_ctx = candidate.get('job_applied_for') or candidate.get('job_category') or None
                ai_analysis = await asyncio.wait_for(
                    _ai().analyze_candidate(candidate['resume_text'], job_context=_job_ctx),
                    timeout=_deps().AI_ANALYSIS_TIMEOUT
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
        
        # Extract resume bytes before DB save
        resume_file = candidate.pop('resume_file_data', None)
        resume_fname = candidate.pop('resume_filename', None)

        # Save to database
        if existing:
            await asyncio.to_thread(_db().update_candidate, candidate)
            logger.info(f"📝 Updated candidate: {candidate.get('name', 'Unknown')}")
        else:
            await asyncio.to_thread(_db().insert_candidate, candidate)
            logger.info(f"✨ NEW candidate from real-time sync: {candidate.get('name', 'Unknown')} - {candidate.get('email', '')}")

        # Save resume attachment
        if resume_file and resume_fname and candidate.get('id'):
            try:
                ct = 'application/pdf' if resume_fname.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                await asyncio.to_thread(_db().save_resume, candidate['id'], resume_fname, resume_file, ct)
            except Exception as e:
                logger.warning(f"Failed to save webhook resume for {candidate.get('id', '')}: {e}")

        try:
            _db().save_ai_analysis(candidate.get('id', ''), {
                'score': candidate.get('matchScore', 0),
                'category': candidate.get('job_category', 'General'),
            })
        except Exception:
            pass
        try:
            await asyncio.to_thread(
                _db().mark_email_processed, message_id,
                candidate.get('id', '') if isinstance(candidate, dict) else '', 'webhook'
            )
        except Exception:
            pass
        return candidate
        
    except Exception as e:
        logger.error(f"Error processing single email {message_id}: {str(e)}")
        return None



@router.post("/api/email/webhook")
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
            # Validate clientState using constant-time comparison to prevent timing attacks
            expected_state = os.getenv('WEBHOOK_CLIENT_STATE', 'recruitment-tool-secret')
            client_state = notification.get('clientState') or ''
            if not hmac.compare_digest(client_state, expected_state):
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




@router.post("/api/email/subscribe-webhook")
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




@router.post("/api/email/outlook/connect")
async def connect_outlook(request: Request, current_user: dict = Depends(require_admin)):
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



@router.get("/api/email/outlook/auth-url")
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



@router.post("/api/email/outlook/sync")
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



@router.post("/api/email/setup-auto-sync")
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
        
        result = await _email_parser().setup_auto_sync(
            email_config=email_config,
            sync_interval_minutes=sync_interval_minutes
        )
        
        return result
    except Exception as e:
        raise HTTPException(500, "Error setting up auto-sync")



@router.get("/api/email/supported-providers")
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




@router.get("/api/oauth/status")
async def get_oauth_automation_status(current_user: dict = Depends(require_auth)):
    """
    Get comprehensive OAuth2 automation status
    Returns auth status, sync status, and statistics
    """
    try:
        if _oauth_automation():
            return _oauth_automation().get_status_summary()
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




@router.post("/api/oauth/refresh")
async def force_token_refresh(current_user: dict = Depends(require_auth)):
    """
    Force refresh OAuth2 token
    Use when automatic refresh fails
    """
    try:
        if not _oauth_automation():
            raise HTTPException(503, "OAuth automation service not initialized")
        
        if not _oauth_automation().is_configured:
            raise HTTPException(400, "OAuth2 not configured. Set MICROSOFT_CLIENT_ID, CLIENT_SECRET, TENANT_ID, and EMAIL_ADDRESS in .env")
        
        result = await _oauth_automation().refresh_token()
        
        if result['status'] == 'success':
            return {
                'status': 'success',
                'message': 'Token refreshed successfully',
                'auth_status': _oauth_automation().auth_status.value
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




@router.post("/api/oauth/sync")
async def trigger_oauth_sync(current_user: dict = Depends(require_auth)):
    """
    Trigger immediate email sync via OAuth automation
    Uses automatic token management
    """
    try:
        if not _oauth_automation():
            raise HTTPException(503, "OAuth automation service not initialized")
        
        if not _oauth_automation().is_configured:
            raise HTTPException(400, "OAuth2 not configured")
        
        # Define sync callback that uses the email processing logic
        async def sync_callback(token_data):
            email_address = _oauth_automation().primary_email
            result = await trigger_reset_and_reparse(email_address)
            return result or {
                'status': 'success',
                'message': 'Sync completed',
                'email': email_address
            }
        
        result = await _oauth_automation().trigger_manual_sync(sync_callback)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering OAuth sync: {e}")
        raise HTTPException(500, "Sync error")




@router.get("/api/oauth/stats")
async def get_oauth_stats(current_user: dict = Depends(require_auth)):
    """
    Get OAuth automation statistics
    """
    try:
        if _oauth_automation():
            return {
                'status': 'success',
                'stats': _oauth_automation().stats
            }
        else:
            return {
                'status': 'error',
                'message': 'OAuth automation not initialized'
            }
    except Exception as e:
        return {'status': 'error', 'message': 'Failed to fetch OAuth stats'}




@router.post("/api/oauth/start-automation")
async def start_oauth_automation(current_user: dict = Depends(require_auth)):
    """
    Start OAuth automation service (if stopped)
    """
    try:
        if _oauth_automation():
            await _oauth_automation().start()
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




@router.post("/api/oauth/stop-automation")
async def stop_oauth_automation(current_user: dict = Depends(require_auth)):
    """
    Stop OAuth automation service
    Manual sync will still be available
    """
    try:
        if _oauth_automation():
            await _oauth_automation().stop()
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




@router.post("/api/email/manual-sync")
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
                auth_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?client_id={client_id}&response_type=code&redirect_uri={os.getenv('MICROSOFT_REDIRECT_URI', 'https://efforts-recruitment-ai.web.app/auth/callback')}&scope=https://graph.microsoft.com/Mail.Read%20https://graph.microsoft.com/Mail.ReadWrite%20https://graph.microsoft.com/Mail.Send%20https://graph.microsoft.com/User.Read%20offline_access"
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




@router.post("/api/email/smart-refetch")
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
            all_candidates = await asyncio.to_thread(_db().get_all_candidates)
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
                if await asyncio.to_thread(_db().is_email_processed, msg_id):
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
                            await asyncio.to_thread(_db().mark_email_processed, msg_id, '', 'skipped-irrelevant')
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
                candidate = await _scraper().extract_candidate_from_email(email_data)
                if not candidate or not candidate.get('email'):
                    if msg_id:
                        try:
                            await asyncio.to_thread(_db().mark_email_processed, msg_id, '', 'no-candidate')
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for no-candidate: {e}")
                    skipped_no_candidate += 1
                    continue
                
                # Block check
                if _db().is_blocked_email(candidate['email']):
                    if msg_id:
                        try:
                            await asyncio.to_thread(_db().mark_email_processed, msg_id, '', 'blocked')
                        except Exception as e:
                            logger.debug(f"Non-critical: mark_email_processed failed for blocked: {e}")
                    skipped_blocked += 1
                    continue
                
                # Check if exists in DB
                existing = await asyncio.to_thread(_db().get_candidate_by_email, candidate['email'])
                
                if not existing:
                    # New candidate — process with AI if we have text
                    analysis_text = candidate.get('resume_text') or candidate.get('summary', '')
                    if analysis_text and len(analysis_text) > 20:
                        try:
                            ai_analysis = await asyncio.wait_for(
                                _ai().analyze_candidate(analysis_text[:5000]),
                                timeout=_deps().AI_ANALYSIS_TIMEOUT
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
                    
                    await asyncio.to_thread(_db().insert_candidate, candidate)
                    
                    if resume_file and resume_filename:
                        try:
                            ct = 'application/pdf' if resume_filename.lower().endswith('.pdf') else 'application/octet-stream'
                            await asyncio.to_thread(_db().save_resume, candidate['id'], resume_filename, resume_file, ct)
                        except Exception as e:
                            logger.warning(f"Failed to save resume for {candidate.get('id', 'unknown')}: {e}")
                    
                    new_count += 1
                else:
                    # Existing candidate — only update if they have gaps (no score, no skills, etc.)
                    if (existing.get('match_score', 0) or 0) <= 0 or not existing.get('skills'):
                        merged = _db().smart_merge_candidate(existing, candidate)
                        resume_file = merged.pop('resume_file_data', None)
                        resume_filename = merged.pop('resume_filename', None)
                        await asyncio.to_thread(_db().update_candidate, merged)
                        updated_count += 1
                
                # Mark as processed
                if msg_id:
                    action = 'refetch-new' if not existing else 'refetch-update'
                    try:
                        await asyncio.to_thread(_db().mark_email_processed, msg_id, candidate.get('id', ''), action)
                    except Exception as e:
                        logger.debug(f"Non-critical: mark_email_processed failed for {action}: {e}")
                        
            except Exception as e:
                errors += 1
                logger.debug(f"Smart refetch error for message: {str(e)[:100]}")
        
        # Clear cache so new candidates are immediately visible
        _cache().clear()
        
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



