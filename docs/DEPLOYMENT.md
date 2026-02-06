# Deployment Guide

## 🚀 Quick Deploy

### Local Development

**1. Backend (Python/FastAPI)**
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your settings (OpenAI key optional)
python main.py
```

**2. Frontend (React/Vite)**
```bash
npm install
cp .env.example .env.local
# Edit .env.local if needed (defaults to localhost:8000)
npm run dev
```

Visit: http://localhost:3001

---

## ☁️ Production Deployment

### Option 1: Docker (Recommended)

**Build & Run:**
```bash
docker-compose up -d
```

**Environment variables:**
- Create `.env` files from `.env.example` templates
- Set `VITE_API_URL` to your production API domain
- Set `DATABASE_URL` to PostgreSQL (not SQLite)
- Add `OPENAI_API_KEY` for AI features
- Configure email accounts (optional)

### Option 2: Platform-Specific

#### **Vercel (Frontend)**
```bash
npm run build
vercel --prod
```

Environment:
- `VITE_API_URL=https://your-backend.railway.app`

#### **Railway/Render (Backend)**
```bash
# Push to GitHub
# Connect repository to Railway/Render
```

Environment:
- `DATABASE_URL=postgresql://...`
- `CORS_ORIGINS=https://your-frontend.vercel.app`
- `OPENAI_API_KEY=sk-...`
- `EMAIL_ADDRESS=your@email.com` (optional)

#### **Heroku (Full Stack)**
```bash
git push heroku main
heroku config:set OPENAI_API_KEY=sk-...
heroku config:set DATABASE_URL=postgresql://...
```

---

## 🔐 Environment Configuration

### Frontend (.env.local)
```env
VITE_API_URL=http://localhost:8000  # Local
# VITE_API_URL=https://api.yoursite.com  # Production
VITE_ENV=development
```

### Backend (.env)
```env
# Required
DATABASE_URL=sqlite:///./ai_recruiter.db  # Local
# DATABASE_URL=postgresql://user:pass@host/db  # Production
SECRET_KEY=your-random-secret-key-here
CORS_ORIGINS=http://localhost:3001

# Optional (graceful fallback if missing)
OPENAI_API_KEY=sk-...
EMAIL_ADDRESS=your@gmail.com
EMAIL_PASSWORD=your-app-password
```

---

## 📊 Database Migration

### Local (SQLite) - Default
No setup needed! Database auto-creates on first run.

### Production (PostgreSQL) - Recommended for 100,000+ records

**1. Create PostgreSQL database:**
```bash
# Railway
railway add postgresql

# Heroku
heroku addons:create heroku-postgresql:hobby-dev

# Manual
createdb ai_recruiter
```

**2. Update DATABASE_URL:**
```env
DATABASE_URL=postgresql://username:password@host:5432/ai_recruiter
```

**3. Database auto-initializes on first request**

---

## 🔧 Configuration Checklist

### Minimal (No AI, No Email)
```env
# Backend
DATABASE_URL=sqlite:///./ai_recruiter.db
SECRET_KEY=random-secret-key
CORS_ORIGINS=http://localhost:3001

# Frontend
VITE_API_URL=http://localhost:8000
```

✅ Login works (mock mode)  
✅ Manual candidate entry  
✅ Basic matching (local NLP)  

### With OpenAI
Add to backend:
```env
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini
```

✅ AI-powered matching  
✅ Interview questions  
✅ Resume analysis  

### With Email Scraping
Add to backend:
```env
EMAIL_ADDRESS=your@gmail.com
EMAIL_PASSWORD=your-app-password  # Gmail app password
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
AUTO_SYNC_ENABLED=true
```

✅ Automatic candidate extraction  
✅ 10,000+ email processing  
✅ Multi-account support  

---

## 🧪 Testing Deployment

### Local Test
```bash
# Terminal 1 - Backend
cd backend && python main.py

# Terminal 2 - Frontend  
npm run dev

# Terminal 3 - Test API
curl http://localhost:8000/api/stats
```

### Production Test
```bash
# Test backend health
curl https://your-api-domain.com/api/stats

# Test frontend
open https://your-frontend-domain.com
```

---

## 🎯 Feature Availability

| Feature | Local (Minimal) | Local (Full) | Production |
|---------|----------------|--------------|------------|
| Login | ✅ Mock | ✅ Mock | ✅ Real JWT |
| Candidate CRUD | ✅ | ✅ | ✅ |
| AI Matching | ✅ Local AI | ✅ Local + OpenAI | ✅ Full |
| Email Scraping | ❌ | ✅ | ✅ |
| OAuth2 Automation | ❌ | ✅ | ✅ |
| Multi-Account | ❌ | ✅ | ✅ |
| Score Caching | ✅ | ✅ | ✅ Redis |
| Setup Wizard | ✅ | ✅ | ✅ |
| Database | SQLite | SQLite | PostgreSQL |

---

## 🔧 Setup Wizard

After deployment, use the Setup Wizard to verify configuration:

1. **Access**: Navigate to `/setup` in the UI
2. **Verify**: Check configuration status for all components
3. **Test**: Use connection test buttons for each service
4. **Follow**: Step-by-step instructions for missing components

**API Endpoints:**
```bash
# Full verification
curl http://localhost:8000/api/setup/verify

# Quick status
curl http://localhost:8000/api/setup/status

# Setup instructions
curl http://localhost:8000/api/setup/instructions
```

---

## 🚨 Troubleshooting

### Frontend can't reach backend
- Check `VITE_API_URL` in `.env.local`
- Check CORS_ORIGINS in backend `.env`
- Verify backend is running: `curl http://localhost:8000/api/stats`

### Email scraping not working
- Gmail: Use [App Password](https://myaccount.google.com/apppasswords), not regular password
- Outlook: Enable IMAP in settings
- Check logs: `python main.py` will show connection errors

### OpenAI errors
- Verify API key is valid: `curl https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"`
- System works without OpenAI (falls back to local NLP)

### Database errors
- SQLite: Check file permissions
- PostgreSQL: Verify connection string format

---

## 📦 Production Optimization

### Backend
```python
# main.py - Remove debug mode
DEBUG=false
HOST=0.0.0.0
PORT=8000

# Use PostgreSQL
DATABASE_URL=postgresql://...

# Use Redis for caching (optional)
REDIS_URL=redis://...
```

### Frontend
```bash
# Build optimized bundle
npm run build

# Serve with nginx/caddy
npx serve -s dist
```

### Email Scraper
```bash
# Run as background service
nohup python -c "from backend.services.email_scraper import EmailScraperService; EmailScraperService().run_continuous_scraper()" &

# Or use systemd/supervisor
```

---

## ✅ Ready to Deploy!

1. **Copy environment templates:** `.env.example` → `.env`
2. **Configure minimal settings** (DATABASE_URL, SECRET_KEY, CORS_ORIGINS)
3. **Add optional features** (OpenAI, Email as needed)
4. **Test locally** with `npm run dev` + `python main.py`
5. **Use Setup Wizard** at `/setup` to verify configuration
6. **Deploy to platform** (Vercel + Railway recommended)

For comprehensive production deployment, see [PRODUCTION_DEPLOYMENT.md](./PRODUCTION_DEPLOYMENT.md)

Questions? Check [QUICKSTART_EMAIL.md](./QUICKSTART_EMAIL.md) or [EMAIL_SETUP_GUIDE.md](./EMAIL_SETUP_GUIDE.md)
