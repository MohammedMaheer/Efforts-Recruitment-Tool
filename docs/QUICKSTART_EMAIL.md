# Quick Start - Email Integration

## 5-Minute Setup Guide

Get your AI Recruiter Platform running with email integration in just 5 minutes!

## Step 1: Install Backend Dependencies (1 minute)

```bash
cd backend
pip install -r requirements.txt
```

## Step 2: Configure Email Credentials (2 minutes)

### Option A: Gmail (Recommended for Testing)

1. **Enable 2-Factor Authentication**
   - Visit: https://myaccount.google.com/security
   - Turn on 2-Step Verification

2. **Generate App Password**
   - Visit: https://myaccount.google.com/apppasswords
   - App: Mail
   - Device: Other (AI Recruiter)
   - Copy the 16-character password

3. **Configure .env file**
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env`:
   ```env
   GMAIL_EMAIL=your-email@gmail.com
   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
   ```

### Option B: Outlook/Office 365 (Enterprise)

See [EMAIL_SETUP_GUIDE.md](EMAIL_SETUP_GUIDE.md#outlook--office-365-enterprise-oauth2) for detailed Azure AD setup.

## Step 3: Start Backend Server (30 seconds)

```bash
cd backend
uvicorn main:app --reload
```

✅ Backend running at: http://localhost:8000

## Step 4: Start Frontend (30 seconds)

Open a new terminal:

```bash
npm install
npm run dev
```

✅ Frontend running at: http://localhost:5173

## Step 5: Test Email Integration (1 minute)

1. **Open the application**
   - Visit: http://localhost:5173
   - Login with any email/password (demo mode)

2. **Navigate to Email Integration**
   - Click "Email Integration" in the sidebar

3. **Connect Your Email**
   - Select "Gmail" (or your provider)
   - Enter your email address
   - Enter your app password
   - Click "Connect Account"

4. **Sync Applications**
   - Click "Sync Now"
   - Watch as candidates are automatically imported!

## What Happens Next?

The system will:
1. ✅ Connect to your inbox
2. ✅ Search for job application emails (last 30 days)
3. ✅ Extract candidate information from email body
4. ✅ Download and parse resume attachments
5. ✅ Create candidate profiles automatically
6. ✅ Display results dashboard

## Test With Sample Email

Send yourself a test application email:

**Subject:** Application for Software Engineer Position

**Body:**
```
Dear Hiring Manager,

I am John Smith applying for the Software Engineer position.
With 5 years of experience in Python, React, and AWS, I believe 
I would be a great fit for your team.

Skills: Python, JavaScript, React, Node.js, AWS, Docker
Experience: 5 years
Location: Dubai, UAE
Phone: +1-555-123-4567
LinkedIn: linkedin.com/in/johnsmith
Email: john.smith@example.com

Please find my resume attached.

Best regards,
John Smith
```

**Attachment:** Any PDF resume

Then run sync again - you'll see John's profile automatically created!

## Verify It Works

1. **Check Sync Results**
   - Should show: "X candidates found"
   - Should show: "Y resumes parsed"

2. **View Candidates**
   - Click "View Candidates"
   - See automatically parsed profiles
   - Check extracted skills, experience, contact info

3. **Review Candidate Details**
   - Click on a candidate
   - Verify all information was extracted correctly
   - Resume should be available for download

## Enable Auto-Sync (Optional)

Click "Setup Auto-Sync" to automatically import new applications every 15 minutes.

## Troubleshooting

### "Authentication Failed"
- Double-check your app password
- Make sure it's the app password, not your regular password
- Remove spaces from the password if you copied them

### "No Candidates Found"
- Check that you have emails with keywords: "application", "resume", "cv"
- Verify date range (default: last 30 days)
- Check folder name (should be "INBOX")

### Backend Won't Start
```bash
# Check Python version (need 3.9+)
python --version

# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Frontend Won't Start
```bash
# Clear cache and reinstall
rm -rf node_modules package-lock.json
npm install
npm run dev
```

## Next Steps

Once email integration is working:

1. **Add Multiple Email Accounts**
   - Connect Outlook, Yahoo, iCloud
   - Centralize candidate sources

2. **Upload Job Descriptions**
   - Navigate to "Job Descriptions"
   - Upload or paste JD
   - Get AI analysis

3. **Match Candidates**
   - System will auto-rank candidates
   - Review match scores
   - Build shortlist

4. **Enable Auto-Sync**
   - Never miss an application
   - Automatic candidate import
   - Real-time updates

## API Testing (Optional)

Test email integration via API:

```bash
# Check available providers
curl http://localhost:8000/api/email/supported-providers

# Test connection
curl -X POST http://localhost:8000/api/email/connect \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "gmail",
    "email": "your-email@gmail.com",
    "password": "your-app-password"
  }'

# Sync emails
curl -X POST http://localhost:8000/api/email/sync \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "gmail",
    "email": "your-email@gmail.com",
    "password": "your-app-password",
    "max_emails": 10
  }'
```

## Interactive API Docs

Visit http://localhost:8000/docs for interactive Swagger documentation where you can test all endpoints directly in your browser.

## Success! 🎉

You now have a fully functional AI Recruiter Platform with automatic email integration!

**Time to first sync:** ~5 minutes  
**Candidates per hour:** Unlimited  
**Manual data entry:** Zero  
**Your productivity boost:** 10x  

## OAuth2 Automation (New!)

For enterprise email integration with automatic token refresh:

```env
# backend/.env
MICROSOFT_CLIENT_ID=your-azure-client-id
MICROSOFT_CLIENT_SECRET=your-azure-secret
MICROSOFT_TENANT_ID=your-tenant-id
AUTO_SYNC_ENABLED=true
SYNC_INTERVAL_MINUTES=15
AUTO_TOKEN_REFRESH=true
```

API Endpoints:
```bash
# Check OAuth status
curl http://localhost:8000/api/oauth/status

# Trigger manual sync
curl -X POST http://localhost:8000/api/oauth/sync
```

## Setup Wizard

Use the Setup Wizard at `/setup` to:
- Verify email configuration
- Test email connection
- View sync statistics
- Follow step-by-step instructions

## Support

Need help?
- Read: [EMAIL_SETUP_GUIDE.md](EMAIL_SETUP_GUIDE.md) - Comprehensive setup guide
- Read: [EMAIL_INTEGRATION_SUMMARY.md](EMAIL_INTEGRATION_SUMMARY.md) - Feature overview
- Read: [OAUTH2_SETUP.md](OAUTH2_SETUP.md) - Microsoft OAuth2 setup
- Read: [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) - Production deployment
- Check: Backend logs at `backend/app.log`
- Visit: API docs at http://localhost:8000/docs
- Review: [README.md](README.md) - Full project documentation

Happy recruiting! 🚀
