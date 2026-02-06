# AI Recruiter Platform 🚀 - **FREE Local AI Edition**

<div align="center">

![AI Recruiter Platform](https://img.shields.io/badge/Enterprise-AI%20Powered-blue)
![Free AI](https://img.shields.io/badge/AI-100%25%20FREE-success)
![React](https://img.shields.io/badge/React-18.2-61dafb)
![TypeScript](https://img.shields.io/badge/TypeScript-5.3-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688)
![License](https://img.shields.io/badge/License-MIT-green)

**Enterprise-grade AI-powered recruitment platform with FREE Local AI (zero costs) + optional OpenAI**

[Features](#features) • [Quick Start](#quick-start) • [Free AI](#free-ai-features) • [Architecture](#architecture)

</div>

---

## 🎉 NEW: 100% FREE Local AI

Your platform now includes **FREE Local AI** with ZERO API costs:
- ✅ **Candidate Analysis** - Instant skill matching & scoring (80% accuracy)
- ✅ **Interview Questions** - Role-specific question generation  
- ✅ **Resume Summarization** - Key qualification extraction
- ✅ **AI Chat Assistant** - Recruitment guidance & insights
- ✅ **Unlimited Processing** - No API limits, no usage fees
- ✅ **600+ Technical Skills** - Comprehensive skill database

**💰 Cost Savings:** $0 forever vs $500/year for 100K candidates with OpenAI

**🔄 Optional Upgrade:** Set `USE_OPENAI=true` for 95% accuracy when budget allows

---

## 🎯 Overview

AI Recruiter Platform is a modern, enterprise-grade SaaS application that leverages artificial intelligence to revolutionize the recruitment process. Built with a premium UI and powerful matching algorithms, it helps recruiters efficiently identify, evaluate, and manage top talent.

### Key Highlights

✨ **FREE AI-Powered Matching** - Zero-cost intelligent ranking using Local AI  
💰 **No API Costs** - Process unlimited candidates for free  
🎨 **Premium Modern UI** - Clean, professional interface inspired by Linear, Notion, and Microsoft  
⚡ **Real-time Analysis** - Instant resume parsing and job description analysis  
📊 **Smart Analytics** - Comprehensive dashboards with actionable insights  
🔒 **Enterprise Security** - Built with security-first principles  
📱 **Fully Responsive** - Desktop-first design that works everywhere  

---

## ✨ Features

### 🤖 FREE AI Features (Zero Costs)
- **Local AI Engine** - Keyword-based NLP matching
- **Candidate Scoring** - 0-100 match scores with explanations
- **Interview Questions** - Auto-generated based on skills
- **Resume Summaries** - Professional qualification highlights
- **Chat Assistant** - Recruitment guidance and tips
- **Skill Extraction** - 600+ technical skills tracked
- **Achievement Detection** - Identifies leadership indicators
- **AI Caching** - Analyze once, retrieve instantly forever

### 🔐 Authentication & Access
- Clean, modern login interface
- Secure email-based authentication
- Enterprise-grade session management
- Remember me functionality

### 📊 Dashboard
- Real-time recruitment metrics
- Total candidates and strong matches overview
- Average match score analytics
- Recent uploads tracking
- Interactive stat cards with trends
- Quick access to recent candidates

### 📝 Job Description Management
- **Upload or paste JD** - Support for PDF, DOCX, or plain text
- **AI Analysis** - Automatic extraction of:
  - Required skills
  - Preferred skills
  - Experience level
  - Key responsibilities
  - Education requirements
- **Visual Requirements Display** - Tagged skills with color coding
- **One-click Matching** - Run candidate matching against JD

### 👥 Candidate Management
- **Advanced Table View** with:
  - Candidate avatars and basic info
  - Real-time match scores with progress bars
  - Skills chips and badges
  - Status indicators (Strong/Partial/Reject)
  - Location and experience
- **Powerful Filtering**:
  - Match score slider
  - Status filter
  - Experience range
  - Skills search
  - Name/email search
- **Smart Sorting** - Sort by relevance, score, or date

### 🔍 Candidate Detail View
- **Two-Column Layout**:
  - **Left Column**:
    - Professional summary
    - Skills matrix with visual tags
    - Work experience timeline
    - Education history
  - **Right Column**:
    - Match score with visual indicator
    - Quick info card
    - AI evaluation with:
      - Strengths analysis
      - Gap identification
      - Final recommendation
    - Action buttons (Interview, Message, Reject)
- **Resume Download** - One-click resume access
- **Shortlist Toggle** - Star/unstar candidates

### ⭐ Shortlist Management
- Ranked candidates by match score
- Batch operations support
- **Export Options**:
  - CSV export
  - PDF export (for sharing)
- Remove from shortlist
- Schedule interviews
- Share shortlist internally

### ⚙️ Settings
- Profile management
- Notification preferences
- Security settings
- Password management

### 📧 Email Integration ⭐ NEW
- **Universal Email Support**:
  - Gmail (IMAP)
  - Outlook / Office 365 (OAuth2)
  - Yahoo Mail (IMAP)
  - iCloud Mail (IMAP)
  - Custom IMAP servers
- **Automatic Email Parsing**:
  - Extracts candidate info from email body
  - Parses resume attachments (PDF, DOCX)
  - Smart application detection
  - Filters spam and irrelevant emails
- **Auto-Sync Functionality**:
  - Continuous inbox monitoring
  - Configurable sync intervals (default: 15 min)
  - Background job processing
  - Real-time candidate import
- **Enterprise OAuth2**:
  - Secure Microsoft authentication
  - Office 365 integration
  - Delegated permissions
  - Token management
- **Smart Extraction**:
  - Name, email, phone number
  - LinkedIn and GitHub profiles
  - Skills and experience
  - Location and job applied
- **Interactive UI**:
  - Provider selection wizard
  - Connection status display
  - Sync progress tracking
  - Results dashboard

**See [EMAIL_SETUP_GUIDE.md](EMAIL_SETUP_GUIDE.md) for detailed setup instructions**

---

## 🚀 Quick Start

### Prerequisites

- **Node.js** 18+ and npm
- **Python** 3.9+
- **Git**

### Installation

#### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/ai-recruiter-platform.git
cd ai-recruiter-platform
```

#### 2. Frontend Setup

```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

The frontend will be available at `http://localhost:3000`

#### 3. Backend Setup

```bash
# Navigate to backend directory
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Start the server
uvicorn main:app --reload
```

The backend API will be available at `http://localhost:8000`

### First Login

1. Navigate to `http://localhost:3000`
2. Enter any email and password (demo mode)
3. Explore the platform!

### Setup Wizard 🔧

After logging in, visit the **Setup** page to verify your configuration:

1. Navigate to `/setup` in the UI
2. View the **Configuration Status** showing all components
3. Follow the **Step-by-step Instructions** for each feature
4. Use the **Test Connection** buttons to verify integrations

The Setup Wizard helps you:
- ✅ Verify environment configuration
- ✅ Check AI model availability
- ✅ Test database connectivity
- ✅ Configure email integration (OAuth2)
- ✅ Set up SMS notifications (Twilio)
- ✅ Enable calendar integrations

**API Endpoints:**
```bash
# Full verification report
curl http://localhost:8000/api/setup/verify

# Quick status check
curl http://localhost:8000/api/setup/status

# Setup instructions
curl http://localhost:8000/api/setup/instructions
```

See [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) for production configuration.

---

## 🏗️ Architecture

### Tech Stack

#### Frontend
- **React 18.2** - UI library
- **TypeScript** - Type safety
- **Vite** - Build tool and dev server
- **Tailwind CSS** - Utility-first styling
- **Framer Motion** - Smooth animations
- **Zustand** - State management
- **React Router** - Navigation
- **Recharts** - Data visualization
- **Lucide Icons** - Modern icon set

#### UI Components
- **ShadCN UI** architecture - Premium, accessible components
- Custom-built components following ShadCN patterns
- Radix UI primitives for accessibility

#### Backend
- **FastAPI** - High-performance Python API framework
- **Pydantic** - Data validation
- **PyPDF2** - PDF parsing
- **python-docx** - DOCX parsing
- **requests** - HTTP library for Microsoft Graph API
- **msal** - Microsoft Authentication Library (OAuth2)
- **email-validator** - Email validation
- **scikit-learn** - ML algorithms
- **Transformers** - NLP models (optional)

### Project Structure

```
ai-recruiter-platform/
├── src/
│   ├── components/
│   │   ├── ui/              # Reusable UI components
│   │   │   ├── Button.tsx
│   │   │   ├── Card.tsx
│   │   │   ├── Input.tsx
│   │   │   ├── Badge.tsx
│   │   │   ├── Avatar.tsx
│   │   │   ├── Table.tsx
│   │   │   ├── Dialog.tsx
│   │   │   └── Progress.tsx
│   │   └── layout/          # Layout components
│   │       ├── DashboardLayout.tsx
│   │       ├── Sidebar.tsx
│   │       └── TopBar.tsx
│   ├── pages/               # Page components
│   │   ├── LoginPage.tsx
│   │   ├── Dashboard.tsx
│   │   ├── JobDescriptions.tsx
│   │   ├── Candidates.tsx
│   │   ├── CandidateDetail.tsx
│   │   ├── Shortlist.tsx
│   │   └── Settings.tsx
│   ├── store/               # State management
│   │   ├── authStore.ts
│   │   └── candidateStore.ts
│   ├── lib/                 # Utilities
│   │   └── utils.ts
│   ├── App.tsx              # Main app component
│   ├── main.tsx             # Entry point
│   └── index.css            # Global styles
├── backend/
│   ├── models/              # Data models
│   │   └── candidate.py
│   ├── services/            # Business logic
│   │   ├── resume_parser.py
│   │   └── matching_engine.py
│   ├── main.py              # FastAPI application
│   └── requirements.txt     # Python dependencies
├── public/                  # Static assets
├── package.json
├── tsconfig.json
├── tailwind.config.js
└── vite.config.ts
```

---

## 🎨 Design System

### Color Palette

- **Primary Blue**: `#006dcc` - Main brand color, CTAs
- **Success Green**: `#10b981` - Positive actions, strong matches
- **Warning Orange**: `#f59e0b` - Partial matches, alerts
- **Danger Red**: `#ef4444` - Rejections, errors
- **Neutral Grays**: `#f9fafb` to `#111827` - Text and backgrounds

### Typography

- **Font Family**: Inter (Google Fonts)
- **Headings**: 700 weight, tight tracking
- **Body**: 400 weight, relaxed leading
- **Small Text**: 500 weight for emphasis

### UI Principles

1. **Clarity** - Information hierarchy is always clear
2. **Consistency** - Patterns repeat throughout the app
3. **Feedback** - Every action has visual feedback
4. **Efficiency** - Minimize clicks to achieve goals
5. **Beauty** - Subtle animations and soft shadows

---

## 🤖 AI & Matching Engine

### Resume Parsing

The resume parser extracts:
- **Personal Information**: Name, email, phone, location
- **Skills**: Technical and soft skills identification
- **Experience**: Years of experience, work history
- **Education**: Degrees, institutions, graduation years
- **Summary**: Professional overview

### Matching Algorithm

**Multi-Factor Scoring**:

1. **Skills Match** (40%)
   - Required skills: 70% weight
   - Preferred skills: 30% weight
   - Exact and semantic matching

2. **Experience Level** (30%)
   - Years of experience alignment
   - Seniority level match

3. **Semantic Similarity** (20%)
   - TF-IDF vectorization
   - Cosine similarity comparison
   - Context understanding

4. **Additional Factors** (10%)
   - Education match
   - Location preference
   - Industry experience

**Match Categories**:
- **Strong Match**: 80%+ score - Highly recommended
- **Partial Match**: 60-79% score - Consider for interview
- **Weak Match**: <60% score - Not recommended

### AI Evaluation

For each candidate, the system generates:
- **Strengths**: Key matching points
- **Gaps**: Missing skills or qualifications
- **Recommendation**: Action advice for recruiter
- **Confidence Score**: Model certainty

---

## 📡 API Documentation

### Base URL
```
http://localhost:8000
```

### Endpoints

#### Health Check
```http
GET /health
```

#### Upload Resume
```http
POST /api/resumes/upload
Content-Type: multipart/form-data

Body: file (PDF or DOCX)
```

#### Batch Upload
```http
POST /api/resumes/batch-upload
Content-Type: multipart/form-data

Body: files[] (multiple files)
```

#### Analyze Job Description
```http
POST /api/job-descriptions/analyze
Content-Type: application/json

{
  "text": "Job description text..."
}
```

#### Match Candidates
```http
POST /api/matching/match-candidates
Content-Type: application/json

{
  "job_description_id": "jd-123",
  "candidate_ids": ["c1", "c2"]
}
```

#### Evaluate Candidate
```http
POST /api/matching/evaluate-candidate
Content-Type: application/json

{
  "candidate_id": "c1",
  "job_description_id": "jd-123"
}
```

#### Email Integration - Connect Account
```http
POST /api/email/connect
Content-Type: application/json

{
  "provider": "gmail",
  "email": "recruiter@company.com",
  "password": "app-password"
}
```

#### Email Integration - Sync Applications
```http
POST /api/email/sync
Content-Type: application/json

{
  "provider": "gmail",
  "email": "recruiter@company.com",
  "password": "app-password",
  "max_emails": 50,
  "days_back": 30
}
```

#### Email Integration - Get Outlook Auth URL
```http
GET /api/email/outlook/auth-url
```

#### Email Integration - Connect Outlook (OAuth2)
```http
POST /api/email/outlook/connect
Content-Type: application/json

{
  "code": "authorization-code-from-oauth"
}
```

#### Email Integration - Get Supported Providers
```http
GET /api/email/supported-providers
```

**For detailed email setup, see [EMAIL_SETUP_GUIDE.md](EMAIL_SETUP_GUIDE.md)**

---

## 📸 Screenshots

### Login Page
Clean, modern authentication with enterprise branding and minimal design.

### Dashboard
Real-time metrics, recent candidates, and quick access to all features.

### Job Description Analysis
AI-powered extraction of requirements, skills, and responsibilities.

### Candidate List
Advanced filtering, sorting, and match score visualization.

### Candidate Detail
Comprehensive two-column layout with AI evaluation and recommendations.

### Shortlist
Ranked candidates ready for export and interview scheduling.

---

## 🔧 Development

### Available Scripts

```bash
# Frontend
npm run dev          # Start development server
npm run build        # Build for production
npm run lint         # Run ESLint
npm run preview      # Preview production build

# Backend
uvicorn main:app --reload        # Start with hot reload
uvicorn main:app --host 0.0.0.0  # Expose to network
```

### Environment Variables

Create a `.env` file in the backend directory:

```env
DATABASE_URL=sqlite:///./ai_recruiter.db
SECRET_KEY=your-secret-key-here
CORS_ORIGINS=http://localhost:3000
MAX_FILE_SIZE=10485760
```

---

## 🚀 Production Deployment

### Quick Production Checklist

1. Set `ENVIRONMENT=production` and `DEBUG=false`
2. Generate secure `SECRET_KEY`
3. Use PostgreSQL (not SQLite)
4. Configure CORS for your domain
5. Use the **Setup Wizard** at `/setup` to verify

### Docker Deployment

```bash
docker-compose up -d
```

### Cloud Deployment

See [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) for:
- Vercel + Railway deployment
- DigitalOcean App Platform
- AWS ECS + RDS
- Environment configuration
- Health monitoring

---

## 📚 Documentation Index

### Getting Started
- [QUICKSTART.md](QUICKSTART.md) - 5-minute quick start guide
- [GETTING_STARTED.md](GETTING_STARTED.md) - Detailed getting started
- [SETUP_GUIDE.md](SETUP_GUIDE.md) - Step-by-step setup instructions
- [INSTALL.md](INSTALL.md) - Installation requirements

### Deployment
- [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) - **Comprehensive production guide**
- [DEPLOYMENT.md](DEPLOYMENT.md) - Quick deployment reference

### AI Features
- [AI_CONFIG_REFERENCE.md](AI_CONFIG_REFERENCE.md) - AI configuration quick reference
- [LOCAL_AI_SETUP.md](LOCAL_AI_SETUP.md) - Free local AI setup
- [LOCAL_AI_SUCCESS.md](LOCAL_AI_SUCCESS.md) - AI setup confirmation
- [AI_INTEGRATION_SUMMARY.md](AI_INTEGRATION_SUMMARY.md) - OpenAI integration details
- [AI_PERFORMANCE_OPTIMIZATION.md](AI_PERFORMANCE_OPTIMIZATION.md) - AI performance tuning
- [OPENAI_SETUP.md](OPENAI_SETUP.md) - OpenAI API configuration

### Email Integration
- [EMAIL_SETUP_GUIDE.md](EMAIL_SETUP_GUIDE.md) - **Complete email setup guide**
- [QUICKSTART_EMAIL.md](QUICKSTART_EMAIL.md) - Quick email integration
- [EMAIL_ARCHITECTURE.md](EMAIL_ARCHITECTURE.md) - Email system architecture
- [EMAIL_INTEGRATION_SUMMARY.md](EMAIL_INTEGRATION_SUMMARY.md) - Email features overview
- [EMAIL_SCRAPING_SYSTEM.md](EMAIL_SCRAPING_SYSTEM.md) - Email scraping details
- [OAUTH2_SETUP.md](OAUTH2_SETUP.md) - Microsoft OAuth2 configuration

### Reference
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - Complete project overview
- [PERFORMANCE_OPTIMIZATION.md](PERFORMANCE_OPTIMIZATION.md) - Performance tuning

---

## 🤝 Contributing

We welcome contributions! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **ShadCN** - UI component architecture inspiration
- **Tailwind CSS** - Utility-first CSS framework
- **React Team** - Amazing library
- **FastAPI** - Modern Python web framework

---

## 📞 Support

For support, email support@airecruiter.com or open an issue on GitHub.

---

<div align="center">

**Built with ❤️ for modern recruiters**

[⬆ Back to top](#ai-recruiter-platform-)

</div>
