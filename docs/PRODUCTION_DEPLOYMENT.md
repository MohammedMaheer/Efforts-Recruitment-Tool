# Production Deployment Guide

Complete guide for deploying the AI Recruiter platform to production.

## 🚀 Quick Start

### Prerequisites
- **Node.js** 18+ (for frontend)
- **Python** 3.10+ (for backend)
- **PostgreSQL** 14+ (production database)
- **Redis** (optional, for caching)
- **Docker** (optional, for containerized deployment)

### One-Liner Deploy (Docker)
```bash
docker-compose -f docker-compose.yml up -d
```

---

## 📋 Production Checklist

Before deploying to production, ensure all items are checked:

### Required Configuration
- [ ] Set `ENVIRONMENT=production`
- [ ] Set `DEBUG=false`
- [ ] Generate secure `SECRET_KEY` (32+ characters)
- [ ] Configure `DATABASE_URL` (PostgreSQL, not SQLite)
- [ ] Set `CORS_ORIGINS` to your frontend domain

### Security
- [ ] Change default SECRET_KEY
- [ ] Enable HTTPS on frontend
- [ ] Configure proper firewall rules
- [ ] Set up rate limiting

### Optional Features
- [ ] Microsoft OAuth2 for email integration
- [ ] Twilio for SMS notifications
- [ ] Google Calendar / Calendly integration
- [ ] OpenAI API for enhanced AI features

---

## 🔧 Step-by-Step Deployment

### 1. Backend Setup

**Clone and configure:**
```bash
cd backend
cp .env.production .env
# Edit .env with your production settings
```

**Install dependencies:**
```bash
pip install -r requirements.production.txt
```

**Download AI models (first time):**
```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"
python -m spacy download en_core_web_sm
```

**Run with gunicorn:**
```bash
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### 2. Frontend Setup

**Build for production:**
```bash
npm install
npm run build
```

**Environment variables (.env):**
```env
VITE_API_URL=https://api.your-domain.com
VITE_ENV=production
```

### 3. Database Setup (PostgreSQL)

```sql
CREATE DATABASE ai_recruiter;
CREATE USER recruiter WITH PASSWORD 'secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE ai_recruiter TO recruiter;
```

Update `.env`:
```env
DATABASE_URL=postgresql://recruiter:secure_password_here@localhost:5432/ai_recruiter
```

---

## 🐳 Docker Deployment (Recommended)

### Using Docker Compose

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Environment Setup

Create a `.env` file in the project root:
```env
# Required
SECRET_KEY=your-secure-secret-key-min-32-chars
DATABASE_URL=postgresql://postgres:password@db:5432/ai_recruiter
CORS_ORIGINS=https://your-domain.com
VITE_API_URL=https://api.your-domain.com

# Optional
MICROSOFT_CLIENT_ID=your-azure-client-id
MICROSOFT_CLIENT_SECRET=your-azure-client-secret
OPENAI_API_KEY=sk-your-openai-key
```

---

## ☁️ Cloud Platform Deployment

### Option 1: Vercel (Frontend) + Railway (Backend)

**Frontend (Vercel):**
```bash
npm run build
vercel --prod
```

Set environment variables in Vercel:
- `VITE_API_URL=https://your-backend.railway.app`

**Backend (Railway):**
1. Connect GitHub repository
2. Set root directory to `backend`
3. Add environment variables:
   - `ENVIRONMENT=production`
   - `DEBUG=false`
   - `DATABASE_URL` (Railway PostgreSQL addon)
   - `SECRET_KEY` (generate with `openssl rand -hex 32`)
   - `CORS_ORIGINS=https://your-frontend.vercel.app`

### Option 2: DigitalOcean App Platform

1. Create new App from GitHub
2. Configure services:
   - Backend: Python runtime, `backend` directory
   - Frontend: Static Site, `dist` output
   - Database: PostgreSQL addon
3. Set environment variables

### Option 3: AWS (ECS + RDS)

**Prerequisites:**
- ECR repository for Docker images
- RDS PostgreSQL instance
- ECS cluster

**Deploy:**
```bash
# Build and push Docker image
aws ecr get-login-password | docker login --username AWS --password-stdin YOUR_ECR_URL
docker build -t ai-recruiter-backend ./backend
docker tag ai-recruiter-backend:latest YOUR_ECR_URL/ai-recruiter-backend:latest
docker push YOUR_ECR_URL/ai-recruiter-backend:latest

# Update ECS service
aws ecs update-service --cluster your-cluster --service ai-recruiter --force-new-deployment
```

---

## 🔐 Environment Variables Reference

### Backend (.env)

```env
# ===========================================
# REQUIRED - Must be configured
# ===========================================
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=your-secure-secret-key-min-32-chars

# Database (PostgreSQL for production)
DATABASE_URL=postgresql://user:pass@host:5432/ai_recruiter

# CORS (your frontend domain)
CORS_ORIGINS=https://your-domain.com

# ===========================================
# AI MODELS - Local Models (No API needed)
# ===========================================
# Models download automatically on first run:
# - all-mpnet-base-v2 (~420MB) - semantic matching
# - en_core_web_sm (~12MB) - entity extraction

# ===========================================
# OPTIONAL - Email Integration (Microsoft OAuth2)
# ===========================================
MICROSOFT_CLIENT_ID=your-azure-app-client-id
MICROSOFT_CLIENT_SECRET=your-azure-app-client-secret
MICROSOFT_TENANT_ID=your-azure-tenant-id
OAUTH_REDIRECT_URI=https://api.your-domain.com/api/oauth2/callback

# ===========================================
# OPTIONAL - SMS Notifications (Twilio)
# ===========================================
TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_PHONE_NUMBER=+1234567890

# ===========================================
# OPTIONAL - Calendar Integration
# ===========================================
GOOGLE_CALENDAR_CREDENTIALS_PATH=/path/to/credentials.json
CALENDLY_API_KEY=your-calendly-api-key
CALENDLY_USER_URI=https://calendly.com/your-user

# ===========================================
# OPTIONAL - Enhanced AI (OpenAI)
# ===========================================
OPENAI_API_KEY=sk-your-openai-api-key
OPENAI_MODEL=gpt-4o-mini

# ===========================================
# OPTIONAL - Redis Cache
# ===========================================
REDIS_URL=redis://localhost:6379
CACHE_TYPE=redis
```

### Frontend (.env)

```env
VITE_API_URL=https://api.your-domain.com
VITE_ENV=production
```

---

## 📊 Monitoring & Health Checks

### Health Check Endpoints

```bash
# Basic health check
curl https://api.your-domain.com/health

# Detailed setup verification
curl https://api.your-domain.com/api/setup/verify

# Quick status summary
curl https://api.your-domain.com/api/setup/status
```

### Setup Wizard

Access the Setup Wizard in the UI at `/setup` to:
- View configuration status
- Get step-by-step setup instructions
- Test individual service connections
- Identify missing configurations

### Logging

Enable structured logging for production:
```env
LOG_LEVEL=INFO
LOG_FORMAT=json
```

---

## 🚨 Troubleshooting

### Backend won't start
```bash
# Check Docker logs
docker-compose logs backend

# Test database connection
cd backend
python -c "from core.database import engine; engine.connect(); print('DB OK')"
```

### Frontend can't reach backend
1. Verify `CORS_ORIGINS` includes your frontend domain
2. Check `VITE_API_URL` is correct
3. Ensure backend is accessible: `curl https://api.your-domain.com/health`

### AI models not loading
```bash
# Manually download models
pip install sentence-transformers spacy
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"
python -m spacy download en_core_web_sm
```

### Memory issues
AI models require ~2GB RAM minimum. Ensure your server has sufficient memory.

### OAuth2 not working
1. Verify Azure App registration is correct
2. Check redirect URI matches exactly
3. Ensure API permissions are granted
4. See [OAUTH2_SETUP.md](./OAUTH2_SETUP.md) for detailed setup

---

## ✅ Production Verification

### Using Setup Wizard
Navigate to `/setup` in the UI to see a comprehensive verification dashboard.

### Using API
```bash
curl https://api.your-domain.com/api/setup/verify | jq .
```

Expected output for production-ready deployment:
```json
{
  "overall_status": "ready",
  "ready_for_production": true,
  "summary": {
    "total": 12,
    "configured": 8,
    "not_configured": 0,
    "errors": 0,
    "optional": 4
  }
}
```

---

## 📚 Additional Resources

- [OAuth2 Setup Guide](./OAUTH2_SETUP.md) - Microsoft OAuth2 configuration
- [Email Setup Guide](./EMAIL_SETUP_GUIDE.md) - Email integration details
- [AI Configuration](./AI_CONFIG_REFERENCE.md) - AI model settings
- [Local AI Setup](./LOCAL_AI_SETUP.md) - Running AI locally
- [Quick Start](./QUICKSTART.md) - Getting started quickly
