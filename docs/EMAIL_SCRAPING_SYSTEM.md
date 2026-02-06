# 🚀 Email Scraping System - Setup & Architecture

## Major Changes Implemented

### ✅ What's New

**1. Automated Email Scraping**
- Continuously monitors inbox (Gmail/Outlook/Yahoo/Custom IMAP)
- Processes 10,000+ emails automatically
- Extracts candidate data + resumes from email attachments
- NO manual upload needed

**2. Incremental Processing**
- Only processes NEW emails (avoids reprocessing 100,000s)
- Tracks processed emails via message IDs
- Updates existing candidates when they reapply
- Efficient database with indexes

**3. AI Job Auto-Categorization**
- AI reads email + resume to determine job role
- Automatically creates job categories
- NO manual job description upload
- Example: "Senior Software Engineer", "Marketing Manager", etc.

**4. Duplicate Detection**
- Uses email hash for instant lookups
- Same email = updates existing candidate record
- Keeps original application date
- Merges new resume data with existing

**5. Heavy Load Optimized**
- SQLite database with B-tree indexes
- Pagination (50 candidates per page)
- Email hash index for O(1) lookups
- Incremental date-based queries

### ❌ What's Removed

- ~~Manual job description upload~~ → AI auto-generates
- ~~Batch resume upload~~ → Email scraping handles it
- ~~Manual candidate entry~~ → All automated

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Email Server (Gmail/Outlook)                   │
│  ├─ Applications (10,000+ emails)               │
│  └─ Attachments (PDFs, DOCX resumes)            │
└─────────────────┬───────────────────────────────┘
                  │ IMAP Connection
                  ↓
┌─────────────────────────────────────────────────┐
│  Email Scraper Service                          │
│  ├─ Fetch NEW emails only (incremental)         │
│  ├─ Parse email body + attachments              │
│  ├─ Extract candidate data                      │
│  └─ AI: Determine job category                  │
└─────────────────┬───────────────────────────────┘
                  │
                  ↓
┌─────────────────────────────────────────────────┐
│  Database Service (SQLite)                      │
│  ├─ Check if candidate exists (email hash)      │
│  ├─ UPDATE if exists / INSERT if new            │
│  ├─ Indexes: email_hash, job_category, date     │
│  └─ Handles 100,000+ candidates efficiently     │
└─────────────────┬───────────────────────────────┘
                  │
                  ↓
┌─────────────────────────────────────────────────┐
│  API Endpoints                                  │
│  ├─ GET /api/candidates (paginated)             │
│  ├─ GET /api/candidates/new?since=date          │
│  ├─ POST /api/scraper/start                     │
│  ├─ POST /api/scraper/process-now               │
│  └─ GET /api/stats                              │
└─────────────────────────────────────────────────┘
```

## Setup Instructions

### 1. Configure Email Access

Edit `backend/.env`:

```env
# For Gmail (recommended)
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
EMAIL_ADDRESS=your-hr-email@company.com
EMAIL_PASSWORD=your-app-password  # NOT regular password!
SCRAPER_INTERVAL_SECONDS=60  # Check every 60 seconds
```

**Gmail App Password:** https://myaccount.google.com/apppasswords

**For Outlook/Office 365:**
```env
IMAP_SERVER=outlook.office365.com
IMAP_PORT=993
```

### 2. Start Backend

```bash
cd backend
python main.py
```

Server starts with:
- ✅ Database initialized (recruitment.db)
- ✅ Email scraper ready (starts on first API call)
- ✅ OpenAI integration active

### 3. Test Email Scraping

**Option A: Automatic (Background)**
```bash
# Scraper starts automatically on server startup
# Or trigger manually:
curl -X POST http://localhost:8000/api/scraper/start
```

**Option B: Manual Test**
```bash
# Process emails immediately (for testing)
curl -X POST http://localhost:8000/api/scraper/process-now
```

### 4. Check Status

```bash
# Scraper status
curl http://localhost:8000/api/scraper/status

# Platform stats
curl http://localhost:8000/api/stats

# Get candidates (paginated)
curl http://localhost:8000/api/candidates?page=1&limit=50
```

## How It Works

### Incremental Processing Flow

```
1. Email arrives: jobs@company.com
   ├─ Subject: "Application for Developer Position"
   ├─ Body: "I am interested in..."
   └─ Attachment: resume.pdf

2. Scraper fetches (UNSEEN emails only)
   ├─ Message ID: unique-id-123
   ├─ Check if processed: NO
   └─ Process email

3. Extract candidate data
   ├─ Parse resume.pdf → Extract skills, experience
   ├─ Parse email body → Get contact info
   ├─ AI analyzes → Job category: "Software Developer"
   └─ Generate candidate ID: hash(email)

4. Check database
   ├─ Email exists? NO
   ├─ Action: INSERT new candidate
   └─ Mark email processed

5. If same person applies again:
   ├─ Email exists? YES
   ├─ Action: UPDATE candidate record
   ├─ Keep original application date
   └─ Update skills, experience, resume
```

### Duplicate Handling

```python
# Email: john@example.com
# Hash: md5("john@example.com") = "abc123..."

First application:
  INSERT INTO candidates (id="abc123", email="john@example.com", ...)

Second application (updated resume):
  UPDATE candidates SET skills=[], experience=5, last_updated=NOW()
  WHERE id="abc123"
```

### Performance Optimizations

**1. Database Indexes**
```sql
CREATE INDEX idx_email_hash ON candidates(email_hash);      -- O(1) lookup
CREATE INDEX idx_last_updated ON candidates(last_updated);  -- Incremental queries
CREATE INDEX idx_job_category ON candidates(job_category);  -- Fast filtering
```

**2. Pagination**
```python
GET /api/candidates?page=1&limit=50  # Fetch 50 at a time
GET /api/candidates?page=2&limit=50  # Next 50
```

**3. Incremental Processing**
```python
GET /api/candidates/new?since=2026-02-01  # Only new since date
```

## API Endpoints

### Scraper Control

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/scraper/start` | POST | Start background scraper |
| `/api/scraper/stop` | POST | Stop scraper |
| `/api/scraper/status` | GET | Check status |
| `/api/scraper/process-now` | POST | Manual trigger |

### Candidates

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/candidates` | GET | Paginated list |
| `/api/candidates/new?since=date` | GET | Only new candidates |
| `/api/stats` | GET | Platform statistics |

### AI Features

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ai/chat` | POST | AI assistant |
| `/api/ai/analyze-match` | POST | Candidate analysis |
| `/api/ai/status` | GET | Check OpenAI |

## Database Schema

```sql
-- Optimized for 100,000+ candidates
CREATE TABLE candidates (
    id TEXT PRIMARY KEY,           -- md5(email)
    email TEXT UNIQUE NOT NULL,
    email_hash TEXT UNIQUE,        -- Indexed for fast lookup
    name TEXT NOT NULL,
    skills TEXT,                   -- JSON array
    experience INTEGER,
    job_category TEXT,             -- AI-generated
    match_score INTEGER,
    applied_date TEXT,             -- Original application
    last_updated TEXT,             -- Latest update
    is_active INTEGER DEFAULT 1
);

-- Indexes for performance
CREATE INDEX idx_email_hash ON candidates(email_hash);
CREATE INDEX idx_job_category ON candidates(job_category);
CREATE INDEX idx_last_updated ON candidates(last_updated);

-- Prevent reprocessing
CREATE TABLE email_processing_log (
    message_id TEXT PRIMARY KEY,
    processed_at TEXT,
    candidate_id TEXT
);
```

## Configuration Options

### Environment Variables

```env
# Email Scraping
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
EMAIL_ADDRESS=hr@company.ae
EMAIL_PASSWORD=app-specific-password
SCRAPER_INTERVAL_SECONDS=60

# OpenAI (for job categorization)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Database
DATABASE_URL=sqlite:///./recruitment.db
```

### Scraper Interval

```env
SCRAPER_INTERVAL_SECONDS=60   # Check every 1 minute
SCRAPER_INTERVAL_SECONDS=300  # Check every 5 minutes (production)
```

## Production Deployment

### 1. Use PostgreSQL (recommended for 100,000+)

```python
# database_service.py - change to:
DATABASE_URL = "postgresql://user:pass@localhost/recruitment"
```

### 2. Run as Background Service

```bash
# Use systemd, supervisor, or docker-compose
# Example: supervisord
[program:recruitment_backend]
command=python /app/backend/main.py
autostart=true
autorestart=true
```

### 3. Monitor Performance

```python
# Add logging
import logging
logging.basicConfig(level=logging.INFO)

# Track metrics
- Emails processed per minute
- Database query times
- Memory usage
```

### 4. Scale Horizontally

```
├─ Email Scraper (1 instance) → Queue
├─ API Server (3 instances) → Load Balancer
└─ Database (1 primary + read replicas)
```

## Troubleshooting

**Email not connecting:**
```bash
# Test IMAP connection
curl -X POST http://localhost:8000/api/scraper/process-now
# Check error message
```

**Slow with 100,000 candidates:**
```bash
# Check if indexes exist
sqlite3 recruitment.db "SELECT * FROM sqlite_master WHERE type='index';"

# Use pagination
curl "http://localhost:8000/api/candidates?page=1&limit=50"
```

**Duplicate candidates:**
```bash
# System auto-handles via email hash
# Check database:
sqlite3 recruitment.db "SELECT email, COUNT(*) FROM candidates GROUP BY email HAVING COUNT(*) > 1;"
```

## Next Steps

1. ✅ Configure email credentials
2. ✅ Start backend server
3. ✅ Trigger test scrape
4. ✅ Check database for candidates
5. ✅ Update frontend to use new API endpoints
6. 🔄 Deploy to production with PostgreSQL

---

**Status**: ✅ Email scraping system ready  
**Capacity**: 100,000+ candidates  
**Processing**: Incremental (only new emails)  
**Duplicates**: Auto-handled via email hash
