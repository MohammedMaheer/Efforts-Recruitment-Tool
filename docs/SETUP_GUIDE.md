# 🚀 Quick Setup Guide - AI Recruiter Platform

## Prerequisites Check

Before starting, ensure you have:
- ✅ Node.js 18+ installed (`node --version`)
- ✅ npm installed (`npm --version`)
- ✅ Python 3.9+ installed (for backend) (`python --version`)
- ✅ Git installed

---

## 🎯 Frontend Setup (5 minutes)

### Step 1: Install Dependencies

Open terminal in the project root directory:

```powershell
npm install
```

This will install all required packages including:
- React & React Router
- TypeScript
- Tailwind CSS
- Framer Motion
- Radix UI components
- Zustand for state management
- And more...

### Step 2: Start Development Server

```powershell
npm run dev
```

You should see:
```
  VITE v5.0.11  ready in 500 ms

  ➜  Local:   http://localhost:3000/
  ➜  Network: use --host to expose
```

### Step 3: Open in Browser

Navigate to: **http://localhost:3000**

You'll see the login page! 🎉

**Demo Login**: Use any email and password to enter (authentication is mocked for demo)

---

## 🐍 Backend Setup (Optional - 10 minutes)

The frontend works standalone with mock data. For full functionality:

### Step 1: Create Virtual Environment

```powershell
cd backend
python -m venv venv
```

### Step 2: Activate Virtual Environment

**Windows:**
```powershell
venv\Scripts\activate
```

**macOS/Linux:**
```bash
source venv/bin/activate
```

### Step 3: Install Python Dependencies

```powershell
pip install -r requirements.txt
```

### Step 4: Setup Environment Variables

```powershell
copy .env.example .env
```

Edit `.env` if needed (defaults work fine for development)

### Step 5: Start Backend Server

```powershell
uvicorn main:app --reload
```

Backend will run at: **http://localhost:8000**

API Documentation: **http://localhost:8000/docs**

---

## 🎨 What You'll See

### 1. Login Page
- Modern, clean design
- Enterprise branding
- Any credentials work in demo mode

### 2. Dashboard
- **4 Stat Cards**: Total Candidates, Strong Matches, Avg Score, Recent Uploads
- **Recent Candidates List**: Click to view details
- Real-time metrics

### 3. Job Descriptions
- Upload PDF/DOCX or paste text
- Click "Load sample JD" for quick test
- AI analysis extracts:
  - Required skills (blue tags)
  - Preferred skills (outline tags)
  - Experience level
  - Key responsibilities

### 4. Candidates
- **Table View** with 5 mock candidates
- **Filters**:
  - Search by name/skills
  - Match score slider (0-100%)
  - Status filter
  - Experience range
- Click any row to see details

### 5. Candidate Detail
- **Left Column**:
  - Professional summary
  - Skills matrix
  - Work experience timeline
  - Education
- **Right Column**:
  - Large match score
  - Quick info card
  - AI evaluation with strengths/gaps
  - Action buttons

### 6. Shortlist
- Star candidates from detail view
- See ranked shortlist
- Export to CSV

### 7. Setup Wizard (New!)
- **Configuration Status**: See all components at a glance
- **Step-by-step Instructions**: Detailed guides for each feature
- **Connection Testing**: Verify database, AI, email integrations
- **Production Readiness**: Check if ready for deployment
- Schedule interviews

### 7. Settings
- Profile management
- Notification preferences
- Security settings

---

## 🎯 Key Features to Test

1. **Login** → Use any credentials
2. **Dashboard** → See overview stats
3. **Candidates** → Filter by match score >80%
4. **Click "Sarah Johnson"** → View top candidate detail
5. **Click ⭐ Add to Shortlist** → Star the candidate
6. **Go to Shortlist** → See starred candidate
7. **Export CSV** → Download shortlist data
8. **Job Descriptions** → Click "Load sample JD" → Analyze
9. **Settings** → Explore configuration options

---

## 🐛 Troubleshooting

### Port Already in Use

**Frontend (3000):**
```powershell
# Find process
netstat -ano | findstr :3000
# Kill process (use PID from above)
taskkill /PID <PID> /F
```

**Backend (8000):**
```powershell
# Find and kill similarly
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

### Dependencies Issues

```powershell
# Clear npm cache
npm cache clean --force
# Remove node_modules and reinstall
rm -rf node_modules package-lock.json
npm install
```

### TypeScript Errors

```powershell
# Restart TypeScript server in VS Code
# Press Ctrl+Shift+P → TypeScript: Restart TS Server
```

---

## 📦 Build for Production

### Frontend

```powershell
npm run build
```

Creates optimized build in `dist/` folder.

### Preview Production Build

```powershell
npm run preview
```

---

## 🎓 Learn More

### Project Structure
```
src/
├── components/ui/     # Reusable components (Button, Card, etc.)
├── components/layout/ # Layout components (Sidebar, TopBar)
├── pages/            # Page components (Dashboard, Candidates, etc.)
├── store/            # Zustand state management
└── lib/              # Utilities (cn, formatters, etc.)
```

### State Management
- **authStore**: User authentication state
- **candidateStore**: Candidate data and shortlist

### Styling
- **Tailwind CSS**: Utility-first styling
- **Custom Classes**: premium-card, glass effect
- **Colors**: Primary blue, success green, warning orange

---

## 🌟 Key Technologies

- **React 18** - UI library
- **TypeScript** - Type safety
- **Vite** - Lightning-fast dev server
- **Tailwind CSS** - Utility styling
- **Framer Motion** - Smooth animations
- **Radix UI** - Accessible primitives
- **Zustand** - State management
- **FastAPI** - Backend API

---

## 🎉 You're All Set!

The application is now running. Explore the features and see the premium, modern UI in action!

**Need Help?** Check the main [README.md](README.md) for detailed documentation.

---

## 🔥 Pro Tips

1. **Use the search** - Quick find candidates by skills
2. **Filter smartly** - Adjust match score slider for precision
3. **Shortlist liberally** - Star candidates to track top talent  
4. **Try animations** - Smooth transitions throughout the UI
5. **Responsive** - Resize browser to see responsive design

---

<div align="center">

**Happy Recruiting! 🎯**

Built with ❤️ using modern web technologies

</div>
