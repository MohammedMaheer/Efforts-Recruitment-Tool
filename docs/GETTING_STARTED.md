# 🎯 Getting Started - AI Recruiter Platform

Welcome to the AI Recruiter Platform! This guide will get you up and running in **under 5 minutes**.

---

## ⚡ Quick Start (Choose Your Path)

### 🚀 Path A: Just Want to See It? (2 minutes)

```powershell
# 1. Install dependencies
npm install

# 2. Start the app
npm run dev

# 3. Open browser to http://localhost:3000

# 4. Login with ANY email and password

# 5. Explore! 🎉
```

**That's it!** The app runs with mock data - no backend needed to explore the UI.

---

### 🔧 Path B: Full Stack Experience (10 minutes)

```powershell
# Frontend
npm install
npm run dev

# Backend (in new terminal)
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Now you have:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

---

## 🎨 What to Try First

### 1️⃣ Dashboard (Home)
- See 4 stat cards with recruitment metrics
- Click any recent candidate to view details
- Notice smooth animations throughout

### 2️⃣ Candidates Page
- Browse 5 mock candidates
- Use the **Match Score slider** - drag to 80% to see filtering
- Click "Sarah Johnson" (95% match) to see detail view
- Try the search bar - type "React" to filter by skill

### 3️⃣ Candidate Detail
- See two-column layout
- Check the **AI Evaluation** card (right side)
- Click **⭐ Add to Shortlist** button
- Notice the smooth animations

### 4️⃣ Shortlist
- Go to Shortlist in sidebar
- See your starred candidate ranked #1
- Click **Export CSV** to download
- Try "Remove" to unstar

### 5️⃣ Job Descriptions
- Click "Load sample JD" button
- Click **Analyze Job Description**
- Watch AI extract requirements (2 second animation)
- See required vs preferred skills in different colors

### 6️⃣ Settings
- Update profile information
- Toggle notification preferences
- Explore security settings

### 7️⃣ Setup (New!)
- Navigate to `/setup` in sidebar
- View **Configuration Status** for all components
- Follow **Step-by-step Instructions** for each feature
- Use **Test Connection** buttons to verify integrations
- Check production readiness status

---

## 🎯 Key Features to Explore

| Feature | Location | What to Do |
|---------|----------|-----------|
| **Filtering** | Candidates page | Adjust match score slider, try filters |
| **Search** | Top bar | Search for "React" or "Python" |
| **Match Scores** | Candidates page | See visual progress bars |
| **AI Evaluation** | Candidate detail | View strengths/gaps analysis |
| **Shortlist** | Star icon | Add/remove candidates |
| **Export** | Shortlist page | Download CSV |
| **Animations** | Everywhere | Notice smooth transitions |

---

## 🎨 UI Design Highlights

### Premium Features You'll Notice

✨ **Smooth Animations**
- Page transitions fade in
- Cards slide into view
- Hover effects on buttons
- Progress bars animate

✨ **Visual Hierarchy**
- Clear headings and sections
- Consistent spacing
- Color-coded match scores
- Status badges

✨ **Professional Polish**
- Rounded corners everywhere
- Soft drop shadows
- Subtle gradients
- Clean typography (Inter font)

✨ **Intuitive UX**
- Helpful empty states
- Clear action buttons
- Visual feedback on interactions
- Breadcrumb navigation

---

## 🔍 Technical Deep Dive

### Frontend Stack
```
React 18.2        → UI library
TypeScript        → Type safety
Tailwind CSS      → Styling
Framer Motion     → Animations
Zustand           → State management
React Router      → Navigation
Vite              → Build tool
```

### Key Files
```
src/
├── pages/
│   ├── Dashboard.tsx      → Main dashboard
│   ├── Candidates.tsx     → Candidate list
│   ├── CandidateDetail.tsx→ Detail view
│   └── Shortlist.tsx      → Shortlist
├── components/ui/
│   ├── Button.tsx         → Reusable button
│   ├── Card.tsx           → Card component
│   └── ...more
└── store/
    ├── authStore.ts       → Authentication
    └── candidateStore.ts  → Candidate data
```

---

## 🎓 Learning Resources

### Understanding the Code

**State Management (Zustand)**
```typescript
// src/store/candidateStore.ts
const candidates = useCandidateStore((state) => state.candidates)
```

**Component Styling (Tailwind)**
```typescript
className="bg-white rounded-xl shadow-soft hover:shadow-medium"
```

**Animations (Framer Motion)**
```typescript
<motion.div
  initial={{ opacity: 0, y: 20 }}
  animate={{ opacity: 1, y: 0 }}
/>
```

### Customization Tips

**Change Colors**: Edit `tailwind.config.js`
```javascript
primary: {
  600: '#006dcc', // Your brand color
}
```

**Add New Page**: 
1. Create file in `src/pages/`
2. Add route in `src/App.tsx`
3. Add navigation in `src/components/layout/Sidebar.tsx`

**Add Mock Data**: Edit `src/store/candidateStore.ts`

---

## 🐛 Common Issues & Solutions

### ❌ "Port 3000 already in use"

**Windows:**
```powershell
netstat -ano | findstr :3000
taskkill /PID <PID_NUMBER> /F
```

### ❌ "Module not found"

```powershell
# Clear cache and reinstall
rm -rf node_modules package-lock.json
npm install
```

### ❌ TypeScript errors in editor

```
Press Ctrl+Shift+P → TypeScript: Restart TS Server
```

### ❌ Styles not loading

```powershell
# Restart dev server
# Press Ctrl+C then run again
npm run dev
```

---

## 📚 Documentation Links

- **Full README**: [README.md](README.md) - Complete feature documentation
- **Setup Guide**: [SETUP_GUIDE.md](SETUP_GUIDE.md) - Detailed setup instructions
- **Project Summary**: [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - Architecture overview

---

## 🌟 Next Steps

### Customize It
1. Change brand colors in Tailwind config
2. Update mock data in candidate store
3. Add your own company logo
4. Modify text and copy

### Extend It
1. Connect to real database (PostgreSQL)
2. Integrate email services
3. Add Microsoft 365 OAuth
4. Implement real AI models
5. Add team collaboration features

### Deploy It
1. Build: `npm run build`
2. Deploy frontend to Vercel/Netlify
3. Deploy backend to Heroku/Railway
4. Set up environment variables
5. Configure domain

---

## 💡 Pro Tips

1. **Use Keyboard Shortcuts**
   - `Ctrl + K` - Focus search (not implemented but easy to add)
   - Click anywhere to navigate

2. **Explore Filters**
   - Combine multiple filters
   - Try extreme values (0% or 100%)
   - Search while filters are active

3. **Check Animations**
   - Watch page transitions
   - Hover over cards
   - Click buttons to see active states

4. **Mobile View**
   - Resize browser window
   - See responsive design in action

5. **Inspect Code**
   - Open DevTools (F12)
   - Check React components
   - See state in React DevTools

---

## 🎉 You're Ready!

Everything is set up and ready to go. Explore the features, check out the code, and customize to your needs!

**Questions?** Check [README.md](README.md) for comprehensive documentation.

**Found a bug?** It's a demo with mock data - perfect for learning and customization!

---

<div align="center">

**Happy Recruiting! 🚀**

Made with ❤️ using React, TypeScript, and Tailwind CSS

[⬆ Back to Top](#-getting-started---ai-recruiter-platform)

</div>
