# Email Integration Setup Guide

This guide will help you set up email integration for the AI Recruiter Platform, allowing automatic parsing of candidate applications from Gmail, Outlook, Yahoo, iCloud, and custom IMAP servers.

## Table of Contents
- [Overview](#overview)
- [Supported Email Providers](#supported-email-providers)
- [Backend Setup](#backend-setup)
- [Provider-Specific Configuration](#provider-specific-configuration)
- [Testing Email Integration](#testing-email-integration)
- [Auto-Sync Configuration](#auto-sync-configuration)
- [Troubleshooting](#troubleshooting)

## Overview

The email integration system automatically:
- Monitors your inbox for job applications
- Extracts candidate information from email body
- Parses resume attachments (PDF, DOCX)
- Creates candidate profiles with extracted data
- Supports OAuth2 for enterprise Outlook/Office 365

## Supported Email Providers

✅ **Gmail** - IMAP with app password  
✅ **Outlook/Office 365** - Microsoft Graph API with OAuth2 (Enterprise-ready)  
✅ **Yahoo Mail** - IMAP with app password  
✅ **iCloud Mail** - IMAP with app password  
✅ **Custom IMAP** - Any IMAP-compatible server  

## Backend Setup

### 1. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

New dependencies added:
- `requests` - HTTP library for Graph API
- `msal` - Microsoft Authentication Library
- `email-validator` - Email validation

### 2. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

## Provider-Specific Configuration

### Gmail

1. **Enable 2-Factor Authentication**
   - Go to Google Account → Security
   - Enable 2-Step Verification

2. **Generate App Password**
   - Visit: https://myaccount.google.com/apppasswords
   - Select "Mail" and "Other (Custom name)"
   - Name it "AI Recruiter"
   - Copy the 16-character password

3. **Configure .env**
   ```env
   GMAIL_EMAIL=your-email@gmail.com
   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
   ```

### Outlook / Office 365 (Enterprise OAuth2)

1. **Register Azure AD Application**
   - Go to: https://portal.azure.com
   - Navigate to "Azure Active Directory" → "App registrations"
   - Click "New registration"
   - Name: "AI Recruiter Email Integration"
   - Supported account types: Select appropriate option
   - Redirect URI: `http://localhost:8000/api/email/outlook/callback`
   - Click "Register"

2. **Configure API Permissions**
   - In your app, go to "API permissions"
   - Click "Add a permission" → "Microsoft Graph"
   - Select "Delegated permissions"
   - Add these permissions:
     - `Mail.Read`
     - `Mail.ReadWrite`
     - `User.Read`
   - Click "Grant admin consent" (requires admin)

3. **Create Client Secret**
   - Go to "Certificates & secrets"
   - Click "New client secret"
   - Description: "AI Recruiter Secret"
   - Expiry: 24 months (recommended)
   - Copy the Value (not ID)

4. **Get Tenant ID**
   - In your app overview, copy the "Directory (tenant) ID"

5. **Configure .env**
   ```env
   MICROSOFT_CLIENT_ID=your-application-client-id
   MICROSOFT_CLIENT_SECRET=your-client-secret-value
   MICROSOFT_TENANT_ID=your-tenant-id
   MICROSOFT_REDIRECT_URI=http://localhost:8000/api/email/outlook/callback
   ```

### Yahoo Mail

1. **Generate App Password**
   - Go to: https://login.yahoo.com/account/security
   - Scroll to "Generate app password"
   - Select "Other App" and name it "AI Recruiter"
   - Click "Generate"
   - Copy the password

2. **Configure .env**
   ```env
   YAHOO_EMAIL=your-email@yahoo.com
   YAHOO_APP_PASSWORD=your-app-password
   ```

### iCloud Mail

1. **Generate App-Specific Password**
   - Go to: https://appleid.apple.com
   - Sign in with your Apple ID
   - Go to "Security" section
   - Under "App-Specific Passwords", click "Generate Password"
   - Label: "AI Recruiter"
   - Copy the password

2. **Configure .env**
   ```env
   ICLOUD_EMAIL=your-email@icloud.com
   ICLOUD_APP_PASSWORD=your-app-specific-password
   ```

### Custom IMAP Server

1. **Get IMAP Settings from Provider**
   - IMAP server address (e.g., mail.example.com)
   - IMAP port (usually 993 for SSL)
   - Your email and password

2. **Configure .env**
   ```env
   CUSTOM_IMAP_SERVER=mail.example.com
   CUSTOM_IMAP_PORT=993
   CUSTOM_IMAP_EMAIL=your-email@example.com
   CUSTOM_IMAP_PASSWORD=your-password
   ```

## Testing Email Integration

### 1. Start Backend Server

```bash
cd backend
uvicorn main:app --reload
```

Server will start at: http://localhost:8000

### 2. Test API Endpoints

Visit: http://localhost:8000/docs for interactive API documentation

#### Get Supported Providers
```bash
curl http://localhost:8000/api/email/supported-providers
```

#### Connect Gmail Account
```bash
curl -X POST http://localhost:8000/api/email/connect \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "gmail",
    "email": "your-email@gmail.com",
    "password": "your-app-password"
  }'
```

#### Sync Emails and Parse Candidates
```bash
curl -X POST http://localhost:8000/api/email/sync \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "gmail",
    "email": "your-email@gmail.com",
    "password": "your-app-password",
    "max_emails": 50
  }'
```

#### Outlook OAuth Flow

1. Get authorization URL:
```bash
curl http://localhost:8000/api/email/outlook/auth-url
```

2. Visit the URL in browser and sign in with Microsoft account

3. After redirect, extract the code parameter from URL

4. Complete authentication:
```bash
curl -X POST http://localhost:8000/api/email/outlook/connect \
  -H "Content-Type: application/json" \
  -d '{
    "code": "authorization-code-from-redirect"
  }'
```

### 3. Test Frontend UI

1. Start frontend development server:
```bash
npm run dev
```

2. Visit: http://localhost:5173

3. Login and navigate to "Email Integration" in sidebar

4. Select provider and connect your account

5. Click "Sync Now" to import candidates

## Auto-Sync Configuration

### Using OAuth Automation Service (Recommended)

The platform now includes an **OAuth Automation Service** that handles:
- Automatic token refresh before expiry
- Background email sync at configurable intervals
- Status monitoring and statistics

**Enable in .env:**
```env
AUTO_SYNC_ENABLED=true
SYNC_INTERVAL_MINUTES=15
AUTO_TOKEN_REFRESH=true
```

**API Endpoints:**
```bash
# Check OAuth status
curl http://localhost:8000/api/oauth/status

# Manual token refresh
curl -X POST http://localhost:8000/api/oauth/refresh

# Trigger manual sync
curl -X POST http://localhost:8000/api/oauth/sync

# Get sync statistics
curl http://localhost:8000/api/oauth/stats
```

**Statistics tracked:**
- `total_syncs` - Total sync operations
- `successful_syncs` - Successful sync count
- `failed_syncs` - Failed sync count
- `token_refreshes` - Token refresh count
- `emails_processed` - Total emails processed
- `candidates_added` - Candidates extracted

### Manual Auto-Sync Setup

Enable automatic email monitoring:

```bash
curl -X POST http://localhost:8000/api/email/setup-auto-sync \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "gmail",
    "email": "your-email@gmail.com",
    "password": "your-app-password",
    "interval_minutes": 15,
    "folders": ["INBOX"],
    "auto_match_jd": true
  }'
```

This will:
- Check for new emails every 15 minutes
- Parse candidate information automatically
- Match candidates against active job descriptions
- Update candidate database

## Email Parsing Features

The system automatically extracts:

**From Email Body:**
- Full name
- Email address
- Phone number
- LinkedIn profile URL
- GitHub profile URL
- Years of experience
- Key skills mentioned
- Current location

**From Resume Attachments:**
- Complete resume text
- Education details
- Work experience
- Technical skills
- Certifications
- Projects

**Smart Detection:**
- Identifies job applications using keywords:
  - "application", "resume", "cv", "position"
  - "applying for", "interested in", "job opening"
- Filters spam and non-application emails
- Handles multiple attachments per email

## Troubleshooting

### Gmail: "Authentication Failed"
- Ensure 2FA is enabled
- Use app password, not regular password
- Check if "Less secure app access" is OFF (should be)
- App password should be 16 characters without spaces

### Outlook: "Invalid Client"
- Verify client ID and secret in .env
- Check redirect URI matches exactly (including trailing slash)
- Ensure API permissions are granted
- Wait 5-10 minutes after granting permissions

### Yahoo: "Connection Timeout"
- Verify app password is correct
- Check account security settings
- Ensure "Allow apps that use less secure sign in" is ON

### iCloud: "Invalid Credentials"
- Must use app-specific password
- Regular password will not work
- Check if 2FA is enabled (required)

### No Emails Found
- Check folder name (INBOX vs Inbox)
- Verify emails contain application keywords
- Check date range (default: last 30 days)
- Look for emails with resume attachments

### Resume Not Parsing
- Supported formats: PDF, DOCX only
- File size limit: 10MB
- Resume must be attached, not embedded in email body
- Check backend logs for parsing errors

## Production Deployment

### Security Considerations

1. **Use Environment Variables**
   - Never commit .env file to git
   - Use secrets management (AWS Secrets Manager, Azure Key Vault)

2. **Enable HTTPS**
   - Update redirect URI to use HTTPS
   - Configure SSL certificates

3. **Rate Limiting**
   - Implement API rate limiting
   - Add retry logic with exponential backoff

4. **OAuth Token Storage**
   - Store tokens in encrypted database
   - Implement token refresh logic
   - Set up token expiration handling

### Background Job Setup

For production auto-sync, use a job scheduler:

**Option 1: APScheduler (Python)**
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
scheduler.add_job(sync_emails, 'interval', minutes=15)
scheduler.start()
```

**Option 2: Celery (Distributed)**
```python
from celery import Celery

celery = Celery('tasks', broker='redis://localhost:6379')

@celery.task
def sync_emails_task():
    # Email sync logic
    pass
```

**Option 3: Cron Job**
```bash
# Add to crontab: sync every 15 minutes
*/15 * * * * cd /path/to/backend && python -c "from services.email_parser import sync_all_accounts; sync_all_accounts()"
```

## API Reference

### POST /api/email/connect
Connect an email account.

**Request:**
```json
{
  "provider": "gmail|outlook|yahoo|icloud|custom",
  "email": "user@example.com",
  "password": "app-password"
}
```

**Response:**
```json
{
  "status": "connected",
  "email": "user@example.com",
  "provider": "gmail"
}
```

### POST /api/email/sync
Sync emails and parse candidates.

**Request:**
```json
{
  "provider": "gmail",
  "email": "user@example.com",
  "password": "app-password",
  "max_emails": 50,
  "days_back": 30
}
```

**Response:**
```json
{
  "candidates_found": 12,
  "new_applications": 8,
  "resumes_parsed": 8,
  "updated_profiles": 4,
  "candidates": [...]
}
```

### GET /api/email/outlook/auth-url
Get Microsoft OAuth authorization URL.

**Response:**
```json
{
  "auth_url": "https://login.microsoftonline.com/..."
}
```

### POST /api/email/outlook/connect
Complete OAuth flow with authorization code.

**Request:**
```json
{
  "code": "authorization-code"
}
```

**Response:**
```json
{
  "status": "connected",
  "email": "user@company.com",
  "access_token": "eyJ0eXAi..."
}
```

## Support

For issues or questions:
- Check backend logs: `tail -f backend/app.log`
- Review API documentation: http://localhost:8000/docs
- Test with single email first before bulk sync
- Verify credentials in .env file

## Next Steps

After successful setup:
1. Connect your primary recruitment email
2. Run initial sync to import existing applications
3. Enable auto-sync for continuous monitoring
4. Configure automatic JD matching
5. Set up email notifications for new candidates
6. Customize parsing rules if needed
