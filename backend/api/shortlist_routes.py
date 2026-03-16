"""Route module: shortlist. Auto-extracted from main_legacy.py."""
import os
import json
import asyncio
import html
import logging
import time
from core.lifespan import backup_db_to_gcs
from services.microsoft_graph import MicrosoftGraphService
from services.token_storage import get_token_storage
import re
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, Depends, UploadFile, File, Body, Query
from fastapi.responses import Response, JSONResponse, StreamingResponse, RedirectResponse

from core.config import get_settings
from core.dependencies import require_auth, optional_auth, require_admin
from models.schemas import CandidateStatusUpdate, BulkShortlistRequest, GenerateEmailRequest

logger = logging.getLogger(__name__)
_settings = get_settings()

VALID_CANDIDATE_STATUSES = {'New', 'Reviewed', 'Shortlisted', 'Interviewing', 'Offered', 'Hired', 'Rejected', 'Withdrawn', 'Strong', 'Partial', 'Reject'}

router = APIRouter(tags=["shortlist"])


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


# ---- Email helpers ----
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
  <p style="margin: 0 0 12px 0;">Hi {html.escape(first_name)},</p>
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
                expires_str = (token_data.get('expires_at') or '').replace('Z', '+00:00')
                graph.token_expiry = datetime.fromisoformat(expires_str) if expires_str else datetime.utcnow() + timedelta(hours=1)
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
            logger.info(f"✅ Rejection email sent to {candidate_name} ({candidate_email})")
        else:
            logger.warning(f"⚠️ Failed to send rejection email to {candidate_email}: {result.get('message')}")

        return result

    except Exception as e:
        logger.error(f"❌ Error sending rejection email: {str(e)}")
        return {'status': 'error', 'message': 'Email sending failed'}


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

        # UAE / GCC locations -> Abu Dhabi office; India/others -> Chennai office
        uae_gcc_locations = ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'ras al khaimah',
                             'fujairah', 'umm al quwain', 'uae', 'united arab emirates',
                             'bahrain', 'kuwait', 'oman', 'qatar', 'saudi', 'riyadh',
                             'jeddah', 'dammam', 'muscat', 'doha', 'manama']
        is_uae = any(loc in candidate_location for loc in uae_gcc_locations)

        # Office assignment: GCC -> Abu Dhabi, everyone else -> Chennai
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
                expires_str = (token_data.get('expires_at') or '').replace('Z', '+00:00')
                graph.token_expiry = datetime.fromisoformat(expires_str) if expires_str else datetime.utcnow() + timedelta(hours=1)
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
            logger.info(f"✅ Shortlist email sent to {candidate_name} ({candidate_email}) [UAE={is_uae}]")
        else:
            logger.warning(f"⚠️ Failed to send shortlist email to {candidate_email}: {result.get('message')}")

        return result

    except Exception as e:
        logger.error(f"❌ Error sending shortlist email: {str(e)}")
        return {'status': 'error', 'message': 'Email sending failed'}


@router.put("/api/candidates/{candidate_id}/status")
async def update_candidate_status(candidate_id: str, status_update: CandidateStatusUpdate, background_tasks: BackgroundTasks, current_user: dict = Depends(require_admin)):
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
        logger.info(f"🔒 STATUS CHANGE AUDIT: candidate={candidate_id} new_status='{status_update.status}' user='{current_user.get('username', 'unknown')}' timestamp={datetime.now(timezone.utc).isoformat()}")

        # Persist status in database
        updated = await asyncio.to_thread(
            _db().update_candidate_status,
            candidate_id,
            status_update.status
        )

        if not updated:
            raise HTTPException(404, f"Candidate {candidate_id} not found")

        # Invalidate candidate cache so list endpoint returns fresh data
        _cache().clear()

        email_result = None

        # Auto-send email when candidate is shortlisted — send inline so caller gets real status
        if status_update.status.lower() in ('shortlisted', 'shortlist'):
            candidate = await asyncio.to_thread(_db().get_candidate_by_id, candidate_id)
            if candidate:
                try:
                    email_result = await _send_shortlist_email(candidate)
                    logger.warning(f"📧 Shortlist email result for {candidate.get('name','?')}: {email_result}")
                except Exception as email_err:
                    logger.error(f"❌ Shortlist email error: {email_err}")
                    email_result = {'status': 'error', 'message': 'Email sending failed'}
            else:
                email_result = {'status': 'skipped', 'reason': 'candidate_not_found'}

        # Auto-send rejection email when candidate is rejected
        if status_update.status.lower() in ('rejected', 'reject'):
            candidate = await asyncio.to_thread(_db().get_candidate_by_id, candidate_id)
            if candidate:
                try:
                    email_result = await _send_rejection_email(candidate)
                    logger.warning(f"📧 Rejection email result for {candidate.get('name','?')}: {email_result}")
                except Exception as email_err:
                    logger.error(f"❌ Rejection email error: {email_err}")
                    email_result = {'status': 'error', 'message': 'Email sending failed'}
            else:
                email_result = {'status': 'skipped', 'reason': 'candidate_not_found'}

        # Persist to GCS immediately so status survives redeploys
        if _settings.is_production:
            try:
                await asyncio.to_thread(backup_db_to_gcs)
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




@router.post("/api/email/test-send")
async def test_email_send(current_user: dict = Depends(require_admin)):
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
        test_body = f"""<div style="font-family: 'Segoe UI', Arial, sans-serif; padding: 20px; max-width: 500px;">
  <h2 style="color: #172554;">&#x2705; Email Test Successful</h2>
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



@router.post("/api/ai/generate-shortlist-email")
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
            c = await asyncio.to_thread(_db().get_candidate_by_id, cid)
            if c:
                candidates.append(c)

        company_name = os.getenv('COMPANY_NAME', _settings.company_name)
        recruiter_name = os.getenv('RECRUITER_NAME', _settings.recruiter_name)
        job_title = request.job_title or (candidates[0].get('jobCategory', 'the open position') if candidates else 'the open position')

        # Fallback: return a default template
        default_subject = f"You've been shortlisted for {job_title} at {company_name}"
        default_body = (
            f"Dear {{{{candidate_name}}}},\n\n"
            f"Congratulations! We are pleased to inform you that you have been shortlisted "
            f"for the position of {{{{job_title}}}} at {{{{company_name}}}}.\n\n"
            f"We will be in touch with next steps shortly.\n\n"
            f"Best regards,\n{{{{recruiter_name}}}}\n{{{{company_name}}}}"
        )
        return {
            "status": "success",
            "subject": default_subject,
            "body": default_body,
            "placeholders": ["candidate_name", "company_name", "job_title", "recruiter_name"],
            "source": "default_template",
            "company_name": company_name,
            "recruiter_name": recruiter_name,
            "job_title": job_title,
        }
    except Exception as e:
        logger.error(f"Email template generation error: {e}")
        raise HTTPException(500, "Error generating email template")



@router.post("/api/candidates/bulk-shortlist")
async def bulk_shortlist_candidates(
    request: BulkShortlistRequest,
    current_user: dict = Depends(require_admin)
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
        _cache().clear()

        for cid in request.candidate_ids:
            try:
                # Update status
                updated = await asyncio.to_thread(
                    _db().update_candidate_status, cid, 'Shortlisted'
                )
                if not updated:
                    results.append({'candidate_id': cid, 'status': 'not_found'})
                    continue

                shortlisted += 1

                # Send personalized email using the same rich pipeline as single shortlist
                if request.send_emails:
                    candidate = await asyncio.to_thread(_db().get_candidate_by_id, cid)
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
                results.append({'candidate_id': cid, 'status': 'error', 'message': 'Processing error'})

        # Invalidate cache after all updates
        _cache().clear()

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




@router.post("/api/candidates/reset-shortlist")
async def reset_all_shortlisted(current_user: dict = Depends(require_admin)):
    try:
        def _reset_shortlisted_db():
            with _db().get_connection() as conn:
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
        _cache().clear()
        return {"status": "success", "reset_count": count, "candidates": [{"id": r[0], "name": r[1]} for r in rows]}
    except Exception as e:
        logger.error(f"Reset shortlist error: {e}")
        raise HTTPException(500, "Internal server error")




@router.get("/api/audit/shortlist-log")
async def get_shortlist_audit_log(current_user: dict = Depends(require_admin)):
    """Return all currently-shortlisted candidates with their shortlisted_at timestamp for audit purposes."""
    try:
        def _get_log():
            with _db().get_connection() as conn:
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



