import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { 
  Send, 
  Sparkles, 
  Bot, 
  User, 
  Loader2, 
  TrendingUp, 
  MapPin,
  Star,
  Zap,
  Search,
  Brain,
  Users,
  Mail,
  Calendar,
  MessageSquare,
  Target,
  AlertTriangle,
  FileText,
  BarChart3,
  Clock,
  CheckCircle2,
  Copy,
  Briefcase,
  X,
  Upload,
  Square,
  CheckSquare,
  Eye
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Avatar, AvatarFallback } from '@/components/ui/Avatar'
import { useCandidates } from '@/hooks/useCandidates'
import { useCandidateStore } from '@/store/candidateStore'
import type { Candidate } from '@/store/candidateStore'
import { useNavigate } from 'react-router-dom'
import { getMatchScoreColor, getStatusBadgeColor } from '@/lib/utils'
import { useAIStatus } from '@/hooks/useAIStatus'
import { advancedApi, aiApi, candidateApi } from '@/services/api'
import config from '@/config'
import { authFetch } from '@/lib/authFetch'

interface Message {
  id: string
  type: 'user' | 'ai'
  content: string
  timestamp: Date
  candidates?: Candidate[]
  intent?: string
  actions?: ActionButton[]
  insights?: InsightCard[]
  loading?: boolean
}

interface ActionButton {
  label: string
  icon: React.ComponentType<{ className?: string }>
  action: () => void
  variant?: 'primary' | 'secondary' | 'success' | 'warning'
}

interface InsightCard {
  title: string
  value: string | number
  icon: React.ComponentType<{ className?: string }>
  color: string
  trend?: 'up' | 'down' | 'neutral'
}

const suggestedPrompts = [
  {
    icon: Brain,
    text: 'Rank candidates using ML for software engineer',
    color: 'text-primary-600',
    bgColor: 'bg-primary-50 hover:bg-primary-100',
    category: 'ml'
  },
  {
    icon: Target,
    text: 'Find best matches for our open positions',
    color: 'text-blue-600',
    bgColor: 'bg-blue-50 hover:bg-blue-100',
    category: 'matching'
  },
  {
    icon: TrendingUp,
    text: 'Show hiring predictions and analytics',
    color: 'text-green-600',
    bgColor: 'bg-green-50 hover:bg-green-100',
    category: 'analytics'
  },
  {
    icon: AlertTriangle,
    text: 'Check for duplicate candidates',
    color: 'text-orange-600',
    bgColor: 'bg-orange-50 hover:bg-orange-100',
    category: 'duplicates'
  },
  {
    icon: Star,
    text: 'Show top candidates with 70%+ score',
    color: 'text-yellow-600',
    bgColor: 'bg-yellow-50 hover:bg-yellow-100',
    category: 'top'
  },
  {
    icon: Briefcase,
    text: 'Match candidates to job description',
    color: 'text-emerald-600',
    bgColor: 'bg-emerald-50 hover:bg-emerald-100',
    category: 'job_match'
  },
  {
    icon: Mail,
    text: 'Draft outreach email for top candidates',
    color: 'text-blue-600',
    bgColor: 'bg-blue-50 hover:bg-blue-100',
    category: 'email'
  },
  {
    icon: Calendar,
    text: 'Schedule interviews for shortlisted candidates',
    color: 'text-teal-600',
    bgColor: 'bg-teal-50 hover:bg-teal-100',
    category: 'calendar'
  },
  {
    icon: FileText,
    text: 'Analyze resume quality of recent applicants',
    color: 'text-pink-600',
    bgColor: 'bg-pink-50 hover:bg-pink-100',
    category: 'quality'
  }
]

// Job Description Matching Modal Component
function JobMatchModal({ 
  isOpen, 
  onClose, 
  onMatch 
}: { 
  isOpen: boolean
  onClose: () => void
  onMatch: (jd: string, topN: number, file?: File) => void 
}) {
  const [jobDescription, setJobDescription] = useState('')
  const [topN, setTopN] = useState(10)
  const [isMatching, setIsMatching] = useState(false)
  const [activeTab, setActiveTab] = useState<'text' | 'file'>('file')
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleMatch = async () => {
    if (activeTab === 'text' && jobDescription.trim().length < 50) {
      alert('Please enter a job description with at least 50 characters')
      return
    }
    if (activeTab === 'file' && !uploadedFile) {
      alert('Please upload a job description file (PDF, DOCX, or TXT)')
      return
    }
    setIsMatching(true)
    await onMatch(
      activeTab === 'text' ? jobDescription : '',
      topN,
      activeTab === 'file' ? uploadedFile! : undefined
    )
    setIsMatching(false)
    onClose()
    setUploadedFile(null)
    setJobDescription('')
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) validateAndSetFile(file)
  }

  const validateAndSetFile = (file: File) => {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!['pdf', 'docx', 'doc', 'txt'].includes(ext || '')) {
      alert('Only PDF, DOCX, and TXT files are supported')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      alert('File too large. Max 10MB.')
      return
    }
    setUploadedFile(file)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) validateAndSetFile(file)
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="bg-white rounded-2xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-hidden"
      >
        <div className="p-6 border-b border-gray-200">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-emerald-100 rounded-xl flex items-center justify-center">
                <Briefcase className="w-5 h-5 text-emerald-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Match Candidates to Job</h2>
                <p className="text-sm text-gray-500">Upload a JD file or paste text to find best matches</p>
              </div>
            </div>
            <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
              <X className="w-5 h-5 text-gray-500" />
            </button>
          </div>
        </div>

        {/* Tab Switcher */}
        <div className="flex border-b border-gray-200">
          <button
            onClick={() => setActiveTab('file')}
            className={`flex-1 py-3 px-4 text-sm font-medium flex items-center justify-center gap-2 border-b-2 transition-colors ${
              activeTab === 'file'
                ? 'border-emerald-600 text-emerald-600 bg-emerald-50/50'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'
            }`}
          >
            <Upload className="w-4 h-4" />
            Upload File
          </button>
          <button
            onClick={() => setActiveTab('text')}
            className={`flex-1 py-3 px-4 text-sm font-medium flex items-center justify-center gap-2 border-b-2 transition-colors ${
              activeTab === 'text'
                ? 'border-emerald-600 text-emerald-600 bg-emerald-50/50'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'
            }`}
          >
            <FileText className="w-4 h-4" />
            Paste Text
          </button>
        </div>
        
        <div className="p-6 space-y-4 max-h-[60vh] overflow-y-auto">
          {/* File Upload Tab */}
          {activeTab === 'file' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Job Description File *
              </label>
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
                  dragOver
                    ? 'border-emerald-400 bg-emerald-50'
                    : uploadedFile
                    ? 'border-emerald-300 bg-emerald-50/50'
                    : 'border-gray-300 hover:border-emerald-400 hover:bg-gray-50'
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx,.doc,.txt"
                  onChange={handleFileChange}
                  className="hidden"
                />
                {uploadedFile ? (
                  <div className="flex flex-col items-center gap-2">
                    <div className="w-12 h-12 bg-emerald-100 rounded-xl flex items-center justify-center">
                      <FileText className="w-6 h-6 text-emerald-600" />
                    </div>
                    <p className="text-sm font-medium text-gray-900">{uploadedFile.name}</p>
                    <p className="text-xs text-gray-500">{(uploadedFile.size / 1024).toFixed(1)} KB</p>
                    <button
                      onClick={(e) => { e.stopPropagation(); setUploadedFile(null) }}
                      className="text-xs text-red-500 hover:text-red-700 mt-1"
                    >
                      Remove file
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2">
                    <div className="w-12 h-12 bg-gray-100 rounded-xl flex items-center justify-center">
                      <Upload className="w-6 h-6 text-gray-400" />
                    </div>
                    <p className="text-sm font-medium text-gray-700">
                      Drag & drop your JD file here
                    </p>
                    <p className="text-xs text-gray-500">
                      or click to browse — PDF, DOCX, TXT (max 10MB)
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Text Input Tab */}
          {activeTab === 'text' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Job Description *
              </label>
              <textarea
                value={jobDescription}
                onChange={(e) => setJobDescription(e.target.value)}
                placeholder="Paste the full job description here...

Example:
We are looking for a Senior Software Engineer with 5+ years of experience in React, Node.js, and AWS. The ideal candidate should have experience with microservices architecture, CI/CD pipelines, and agile methodologies..."
                className="w-full h-64 p-4 border border-gray-300 rounded-xl focus:ring-2 focus:ring-emerald-500 focus:border-emerald-500 resize-none text-sm"
              />
              <p className="text-xs text-gray-500 mt-1">
                {jobDescription.length} characters (minimum 50 required)
              </p>
            </div>
          )}
          
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Number of Top Candidates
            </label>
            <select
              value={topN}
              onChange={(e) => setTopN(Number(e.target.value))}
              className="w-full p-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-emerald-500"
            >
              <option value={5}>Top 5</option>
              <option value={10}>Top 10</option>
              <option value={15}>Top 15</option>
              <option value={20}>Top 20</option>
              <option value={50}>Top 50</option>
            </select>
          </div>
        </div>
        
        <div className="p-6 border-t border-gray-200 bg-gray-50">
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-3 border border-gray-300 rounded-xl text-gray-700 hover:bg-gray-100 font-medium"
            >
              Cancel
            </button>
            <button
              onClick={handleMatch}
              disabled={
                isMatching ||
                (activeTab === 'text' && jobDescription.length < 50) ||
                (activeTab === 'file' && !uploadedFile)
              }
              className="flex-1 px-4 py-3 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {isMatching ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Matching...
                </>
              ) : (
                <>
                  <Brain className="w-4 h-4" />
                  Find Best Matches
                </>
              )}
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  )
}

export default function AIAssistant() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [showJobMatchModal, setShowJobMatchModal] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const { candidates, totalCount } = useCandidates({ autoFetch: true })
  const toggleShortlist = useCandidateStore((s) => s.toggleShortlist)
  const isShortlisted = useCandidateStore((s) => s.isShortlisted)
  const navigate = useNavigate()
  const aiStatus = useAIStatus()

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleShortlistSelected = async (candidateList: Candidate[]) => {
    const toShortlist = candidateList.filter(c => selectedIds.has(c.id) && c.status !== 'Shortlisted')
    if (toShortlist.length === 0) return
    let count = 0
    for (const c of toShortlist) {
      try {
        await candidateApi.updateStatus(c.id, 'Shortlisted')
        if (!isShortlisted(c.id)) toggleShortlist(c.id)
        count++
      } catch (e) { console.error('Shortlist error:', e) }
    }
    setSelectedIds(new Set())
    const confirmMsg: Message = {
      id: Date.now().toString(),
      type: 'ai',
      content: `**${count} candidate${count !== 1 ? 's' : ''}** shortlisted successfully.`,
      timestamp: new Date(),
      intent: 'shortlist_confirm',
      actions: [{ label: 'View Shortlist', icon: Star, action: () => navigate('/shortlist'), variant: 'primary' }]
    }
    setMessages(prev => [...prev, confirmMsg])
  }

  // Track whether welcome has been generated to prevent re-rendering on each page load
  const welcomeGenerated = useRef(false)
  
  useEffect(() => {
    // Only generate welcome message once when we have data
    // Use totalCount (server-side accurate count) to avoid the 500→1000→5000 flicker
    if (welcomeGenerated.current) return
    if (candidates.length === 0) return
    welcomeGenerated.current = true
    
    // Build smart welcome with actual candidate insights
    const topSkills = new Map<string, number>()
    const categories = new Map<string, number>()
    const locations = new Map<string, number>()
    let strongCount = 0
    let recentCount = 0
    const today = new Date()
    
    // Use the candidates we have so far for insights
    candidates.forEach(c => {
      c.skills?.forEach(s => topSkills.set(s, (topSkills.get(s) || 0) + 1))
      if (c.jobCategory) categories.set(c.jobCategory, (categories.get(c.jobCategory) || 0) + 1)
      if (c.location) locations.set(c.location, (locations.get(c.location) || 0) + 1)
      if (c.matchScore >= 70) strongCount++
      const d = new Date(c.appliedDate)
      if (today.getTime() - d.getTime() < 86400000) recentCount++
    })

    // Use server totalCount for the headline number (accurate from first API call)
    const displayTotal = totalCount > 0 ? totalCount : candidates.length
    const topSkillsList = [...topSkills.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([s]) => s)
    const topCategories = [...categories.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([c, n]) => `${c} (${n})`)
    const avgScore = candidates.length > 0 ? (candidates.reduce((a, c) => a + (c.matchScore || 0), 0) / candidates.length).toFixed(0) : '0'

    const welcomeMessage: Message = {
      id: '0',
      type: 'ai',
      content: `Welcome! I'm your **AI Recruitment Assistant** with **${displayTotal} candidates** ready to analyze.

${topCategories.length > 0 ? `**Top Categories:** ${topCategories.join(' · ')}` : ''}
${topSkillsList.length > 0 ? `**Trending Skills:** ${topSkillsList.join(', ')}` : ''}

**What I can do:**
• **Smart Search** — Find candidates by role, skill, location, or any criteria
• **Job Matching** — Upload a JD and I'll rank your best fits
• **ML Ranking** — Predict hiring success with machine learning
• **Analytics** — Pipeline insights, quality breakdown, hiring forecasts
• **Actions** — Shortlist, email, schedule interviews directly from chat

Try a suggestion below or ask anything!`,
      timestamp: new Date(),
      insights: displayTotal > 0 ? [
        { title: 'Candidates', value: displayTotal, icon: Users, color: 'blue' },
        { title: 'Avg Score', value: `${avgScore}%`, icon: TrendingUp, color: 'green' },
        { title: 'Strong (70%+)', value: strongCount, icon: Star, color: 'yellow' },
        { title: 'New Today', value: recentCount, icon: Clock, color: 'indigo' }
      ] : undefined
    }
    setMessages([welcomeMessage])
  }, [candidates.length, totalCount])

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  // Enhanced query parser with advanced features
  const parseQuery = async (query: string): Promise<{ 
    candidates: Candidate[], 
    response: string, 
    intent: string,
    actions?: ActionButton[],
    insights?: InsightCard[]
  }> => {
    const lowerQuery = query.toLowerCase()
    let filteredCandidates = [...candidates]
    let response = ''
    let intent = 'search'
    let actions: ActionButton[] = []
    let insights: InsightCard[] | undefined

    // ML RANKING
    if (lowerQuery.includes('rank') || lowerQuery.includes('ml rank') || lowerQuery.includes('machine learning')) {
      intent = 'ml_ranking'
      try {
        const candidateIds = candidates.slice(0, 20).map(c => c.id)
        const result = await advancedApi.ml.rankCandidates(candidateIds, undefined, 10) as { data?: { rankings?: Array<{ candidate_id: string; score: number }> } }
        
        if (result.data?.rankings) {
          const rankings = result.data.rankings
          const rankedIds = rankings.map((r) => r.candidate_id)
          filteredCandidates = rankedIds
            .map((id: string) => candidates.find(c => c.id === id))
            .filter(Boolean) as Candidate[]
          
          response = `**ML-Powered Ranking Complete**\n\nI've analyzed ${candidateIds.length} candidates using machine learning. Here are the top matches ranked by predicted success:`
          
          insights = [
            { title: 'Analyzed', value: candidateIds.length, icon: Brain, color: 'indigo' },
            { title: 'Top Score', value: `${(rankings[0]?.score * 100 || 0).toFixed(0)}%`, icon: Star, color: 'yellow' },
            { title: 'Avg Score', value: `${(rankings.reduce((a, r) => a + r.score, 0) / rankings.length * 100).toFixed(0)}%`, icon: TrendingUp, color: 'green' }
          ]
        }
      } catch (error) {
        console.error('ML ranking error:', error)
        // Fallback to score-based ranking
        filteredCandidates = filteredCandidates.sort((a, b) => b.matchScore - a.matchScore).slice(0, 10)
        response = `Here are the top candidates ranked by match score (ML service unavailable):`
      }
      
      actions = [
        { label: 'Email Top 5', icon: Mail, action: () => navigate('/email-integration'), variant: 'primary' },
        { label: 'Schedule Interviews', icon: Calendar, action: () => navigate('/candidates'), variant: 'secondary' }
      ]
    }
    
    // PREDICTIVE ANALYTICS
    else if (lowerQuery.includes('predict') || lowerQuery.includes('analytics') || lowerQuery.includes('forecast')) {
      intent = 'predictive_analytics'
      try {
        const topCandidates = [...candidates].sort((a, b) => b.matchScore - a.matchScore).slice(0, 5)
        const predictions = await Promise.all(
          topCandidates.map(c => advancedApi.analytics.predict(c.id).catch(() => null))
        )
        
        filteredCandidates = topCandidates
response = `**Predictive Analytics Report**\n\nI've analyzed your top candidates to predict hiring outcomes:`
        
        const avgProbability = predictions.filter(Boolean).reduce((acc, p) => acc + ((p as { data?: { probability?: number } })?.data?.probability || 0.5), 0) / predictions.length
        
        insights = [
          { title: 'Candidates Analyzed', value: topCandidates.length, icon: Users, color: 'blue' },
          { title: 'Avg Hiring Probability', value: `${(avgProbability * 100).toFixed(0)}%`, icon: Target, color: 'green' },
          { title: 'High Potential', value: predictions.filter((p) => ((p as { data?: { probability?: number } })?.data?.probability || 0) > 0.7).length, icon: Star, color: 'yellow' }
        ]
      } catch (error) {
        response = `**Quick Analytics Summary**\n\n• Total Candidates: ${candidates.length}\n• Strong Matches: ${candidates.filter(c => c.status === 'Strong').length}\n• Average Score: ${(candidates.reduce((acc, c) => acc + c.matchScore, 0) / candidates.length).toFixed(1)}%`
        filteredCandidates = []
      }
      
      actions = [
        { label: 'View Full Analytics', icon: BarChart3, action: () => navigate('/analytics'), variant: 'primary' },
        { label: 'Export Report', icon: FileText, action: () => {}, variant: 'secondary' }
      ]
    }
    
    // DUPLICATE DETECTION
    else if (lowerQuery.includes('duplicate') || lowerQuery.includes('duplicates') || lowerQuery.includes('clean')) {
      intent = 'duplicate_detection'
      try {
        // Check duplicates for each candidate
        const duplicateResults = await Promise.all(
          candidates.slice(0, 20).map(c => 
            advancedApi.duplicates.check({ candidateId: c.id }).catch(() => null)
          )
        )
        
        const duplicatesFound = duplicateResults.filter((r) => (r as { data?: { duplicates?: unknown[] } })?.data?.duplicates?.length).length
        
        if (duplicatesFound > 0) {
          filteredCandidates = candidates.filter((_c, idx) => {
            const result = duplicateResults[idx] as { data?: { duplicates?: unknown[] } } | null
            return result?.data?.duplicates && result.data.duplicates.length > 0
          })
          
          response = `**Duplicate Detection Results**\n\nI found **${duplicatesFound} candidates** with potential duplicates that may need attention:`
          
          insights = [
            { title: 'Candidates Checked', value: Math.min(20, candidates.length), icon: Search, color: 'blue' },
            { title: 'With Duplicates', value: duplicatesFound, icon: Copy, color: 'orange' },
            { title: 'Clean Records', value: Math.min(20, candidates.length) - duplicatesFound, icon: CheckCircle2, color: 'green' }
          ]
        } else {
          response = `**No Duplicates Found**\n\nYour candidate database is clean. No duplicate entries detected.`
          filteredCandidates = []
        }
      } catch (error) {
        response = `Checking for duplicates... (Service temporarily unavailable)`
        filteredCandidates = []
      }
      
      actions = [
        { label: 'Merge Duplicates', icon: Users, action: () => {}, variant: 'warning' }
      ]
    }
    
    // EMAIL TEMPLATES
    else if (lowerQuery.includes('email') || lowerQuery.includes('template') || lowerQuery.includes('outreach') || lowerQuery.includes('draft')) {
      intent = 'email_templates'
      filteredCandidates = candidates.filter(c => c.status === 'Strong' || c.matchScore >= 70).slice(0, 5)
      
      response = `**Email Outreach Ready**\n\nI've identified **${filteredCandidates.length} candidates** perfect for outreach. You can use our pre-built templates or create custom ones:`
      
      actions = [
        { label: 'Browse Templates', icon: FileText, action: () => navigate('/email-integration'), variant: 'primary' },
        { label: 'Create Campaign', icon: Mail, action: () => navigate('/email-integration'), variant: 'secondary' },
        { label: 'Quick Email', icon: Send, action: () => {
          if (filteredCandidates[0]?.email) {
            window.location.href = `mailto:${filteredCandidates[0].email}`
          }
        }, variant: 'success' }
      ]
    }
    
    // CALENDAR / SCHEDULING
    else if (lowerQuery.includes('schedule') || lowerQuery.includes('interview') || lowerQuery.includes('calendar') || lowerQuery.includes('meeting')) {
      intent = 'calendar'
      filteredCandidates = candidates.filter(c => c.status === 'Strong' || c.isShortlisted).slice(0, 5)
      
      response = `**Interview Scheduling**\n\nI found **${filteredCandidates.length} candidates** ready for interviews. You can schedule through our calendar integration:`
      
      actions = [
        { label: 'Open Calendar', icon: Calendar, action: () => navigate('/candidates'), variant: 'primary' },
        { label: 'Bulk Schedule', icon: Users, action: () => {}, variant: 'secondary' }
      ]
    }
    
    // SMS / NOTIFICATIONS  
    else if (lowerQuery.includes('sms') || lowerQuery.includes('text') || lowerQuery.includes('notify') || lowerQuery.includes('message')) {
      intent = 'sms'
      filteredCandidates = candidates.filter(c => c.phone).slice(0, 5)
      
      response = `**SMS Notifications**\n\n**${filteredCandidates.length} candidates** have phone numbers available for SMS outreach:`
      
      actions = [
        { label: 'Send Bulk SMS', icon: MessageSquare, action: () => {}, variant: 'primary' },
        { label: 'View Templates', icon: FileText, action: () => navigate('/email-integration'), variant: 'secondary' }
      ]
    }
    
    // RESUME QUALITY
    else if (lowerQuery.includes('quality') || lowerQuery.includes('resume quality') || lowerQuery.includes('analyze resume')) {
      intent = 'resume_quality'
      filteredCandidates = [...candidates].sort((a, b) => b.matchScore - a.matchScore).slice(0, 10)
      
      const highQuality = filteredCandidates.filter(c => c.matchScore >= 70).length
      const mediumQuality = filteredCandidates.filter(c => c.matchScore >= 50 && c.matchScore < 70).length
      const lowQuality = filteredCandidates.filter(c => c.matchScore < 50).length
      
      response = `**Resume Quality Analysis**\n\nHere's a breakdown of your candidate pool quality:`
      
      insights = [
        { title: 'High Quality (70%+)', value: highQuality, icon: Star, color: 'green' },
        { title: 'Medium (50-70%)', value: mediumQuality, icon: TrendingUp, color: 'yellow' },
        { title: 'Needs Review (<50%)', value: lowQuality, icon: AlertTriangle, color: 'red' }
      ]
      
      actions = [
        { label: 'View All Candidates', icon: Users, action: () => navigate('/candidates'), variant: 'primary' }
      ]
    }
    
    // JOB MATCHING
    else if (lowerQuery.includes('match') || lowerQuery.includes('job') || lowerQuery.includes('position') || lowerQuery.includes('fit')) {
      intent = 'job_matching'
      filteredCandidates = [...candidates].sort((a, b) => b.matchScore - a.matchScore).slice(0, 10)
      response = `**Job Matching Results**\n\nTop candidates matched to your open positions:`
      
      actions = [
        { label: 'View Candidates', icon: Target, action: () => navigate('/candidates'), variant: 'primary' },
        { label: 'Upload JD', icon: FileText, action: () => navigate('/ai-assistant'), variant: 'secondary' }
      ]
    }

    // Match score filtering
    else if (lowerQuery.includes('top') || lowerQuery.includes('best') || lowerQuery.match(/\d+%?\+?\s*(match|score)/)) {
      const scoreMatch = lowerQuery.match(/(\d+)%?/)
      const minScore = scoreMatch ? parseInt(scoreMatch[1]) : 70
      filteredCandidates = filteredCandidates.filter(c => c.matchScore >= minScore)
      intent = 'top_candidates'
      response = `Found **${filteredCandidates.length} candidate${filteredCandidates.length !== 1 ? 's' : ''}** with ${minScore}%+ match score:`
      
      actions = [
        { label: 'Email All', icon: Mail, action: () => navigate('/email-integration'), variant: 'primary' },
        { label: 'Shortlist All', icon: Star, action: async () => {
          for (const c of filteredCandidates) {
            try {
              await candidateApi.updateStatus(c.id, 'Shortlisted')
            } catch (e) {
              console.error(`Failed to shortlist ${c.name}:`, e)
            }
          }
          alert(`Shortlisted ${filteredCandidates.length} candidates — notification emails sent!`)
        }, variant: 'secondary' }
      ]
    }
    
    // Status filtering
    else if (lowerQuery.includes('strong match') || lowerQuery.includes('strong candidate')) {
      filteredCandidates = filteredCandidates.filter(c => c.status === 'Strong')
      intent = 'strong_matches'
      response = `Here are **${filteredCandidates.length} strong match** candidate${filteredCandidates.length !== 1 ? 's' : ''}:`
    }
    
    // Recent/new candidates
    else if (lowerQuery.includes('recent') || lowerQuery.includes('new') || lowerQuery.includes('latest') || lowerQuery.includes('today')) {
      filteredCandidates = filteredCandidates
        .sort((a, b) => new Date(b.appliedDate).getTime() - new Date(a.appliedDate).getTime())
        .slice(0, 10)
      intent = 'recent'
      response = `Here are the **${filteredCandidates.length} most recent** applicants:`
    }
    
    // Skill-based search
    else if (lowerQuery.includes('skill') || lowerQuery.includes('developer') || 
             lowerQuery.includes('engineer') || lowerQuery.includes('with ') ||
             lowerQuery.includes('react') || lowerQuery.includes('python') ||
             lowerQuery.includes('java') || lowerQuery.includes('node')) {
      const skills = ['react', 'python', 'javascript', 'java', 'typescript', 'node', 'angular', 'vue', 'aws', 'docker', 'kubernetes', 'sql', 'mongodb', 'c#', '.net', 'go', 'rust', 'swift', 'kotlin']
      const foundSkills = skills.filter(skill => lowerQuery.includes(skill))
      
      if (foundSkills.length > 0) {
        filteredCandidates = filteredCandidates.filter(c => 
          foundSkills.some(skill => 
            c.skills.some(s => s.toLowerCase().includes(skill))
          )
        )
        intent = 'skill_search'
        response = `Found **${filteredCandidates.length} candidate${filteredCandidates.length !== 1 ? 's' : ''}** with **${foundSkills.join(', ')}** skills:`
      }
    }
    
    // Location-based search
    else if (lowerQuery.includes('in ') || lowerQuery.includes('from ') || lowerQuery.includes('location')) {
      // Extract location from query dynamically - look for text after 'in' or 'from'
      const locationMatch = lowerQuery.match(/(?:in|from|location[:\s]+)\s*([a-z\s]+?)(?:\s*$|\s+with|\s+who|\s+that)/i)
      const searchLocation = locationMatch ? locationMatch[1].trim() : ''
      
      if (searchLocation) {
        filteredCandidates = filteredCandidates.filter(c => 
          c.location.toLowerCase().includes(searchLocation)
        )
        intent = 'location_search'
        response = `Found **${filteredCandidates.length} candidate${filteredCandidates.length !== 1 ? 's' : ''}** in **${searchLocation}**:`
      }
    }
    
    // Shortlist
    else if (lowerQuery.includes('shortlist') || lowerQuery.includes('favorite') || lowerQuery.includes('saved')) {
      filteredCandidates = filteredCandidates.filter(c => c.isShortlisted)
      intent = 'shortlist'
      response = `Your shortlist has **${filteredCandidates.length} candidate${filteredCandidates.length !== 1 ? 's' : ''}**:`
      
      actions = [
        { label: 'View Shortlist', icon: Star, action: () => navigate('/shortlist'), variant: 'primary' },
        { label: 'Export PDF', icon: FileText, action: () => navigate('/shortlist'), variant: 'secondary' }
      ]
    }
    
    // Default: show all or search by name
    else {
      const searchTerm = query.trim()
      if (searchTerm.length > 0) {
        filteredCandidates = filteredCandidates.filter(c => 
          c.name.toLowerCase().includes(lowerQuery) ||
          c.skills.some(s => s.toLowerCase().includes(lowerQuery)) ||
          c.location.toLowerCase().includes(lowerQuery) ||
          c.email.toLowerCase().includes(lowerQuery)
        )
        intent = 'general_search'
        response = `Found **${filteredCandidates.length} result${filteredCandidates.length !== 1 ? 's' : ''}** for "${query}":`
      } else {
        filteredCandidates = filteredCandidates.slice(0, 10)
        intent = 'show_all'
        response = `Here are the first **${filteredCandidates.length} candidates** in your pipeline:`
      }
    }

    // Sort by match score
    filteredCandidates.sort((a, b) => b.matchScore - a.matchScore)

    if (filteredCandidates.length === 0 && !response.includes('No Duplicates') && !response.includes('Analytics')) {
      response = "I couldn't find any candidates matching that criteria. Try:\n\n• Different keywords or skills\n• Broader search terms\n• Check spelling"
      actions = [
        { label: 'View All Candidates', icon: Users, action: () => navigate('/candidates'), variant: 'primary' },
        { label: 'Import More', icon: Mail, action: () => navigate('/email-integration'), variant: 'secondary' }
      ]
    }

    return { candidates: filteredCandidates.slice(0, 8), response, intent, actions, insights }
  }

  const handleSend = async () => {
    if (!input.trim()) return

    const userMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: input,
      timestamp: new Date()
    }

    setMessages(prev => [...prev, userMessage])
    const userInput = input
    setInput('')
    setIsTyping(true)

    // Add loading message
    const loadingId = (Date.now() + 1).toString()
    setMessages(prev => [...prev, {
      id: loadingId,
      type: 'ai',
      content: 'Analyzing your request...',
      timestamp: new Date(),
      loading: true
    }])

    try {
      // Build conversation history for backend context
      const conversationHistory = messages
        .filter(m => !m.loading && m.content)
        .slice(-10)
        .map(m => ({
          role: m.type === 'user' ? 'user' : 'assistant',
          content: m.content.substring(0, 500)
        }))
      
      // Use AI chat (3-tier: Gemini → LLM → OpenAI → rule-based)
      // The backend now returns candidates_lookup alongside the text response
      const chatResult = await aiApi.chat(userInput, true, conversationHistory)
      
      // Remove loading message
      setMessages(prev => prev.filter(m => m.id !== loadingId))
      
      const aiText = chatResult.data?.response || 'AI service unavailable. Please try again.'
      const sourceInfo = chatResult.data?.source || ''
      const candidatesLookup = chatResult.data?.candidates_lookup || []
      
      // Extract candidate references from AI text (e.g. "#6." or "#25." or "**#6.")
      // and match them to the candidates_lookup by index
      let displayCandidates: Candidate[] = []
      
      if (candidatesLookup.length > 0) {
        // Find all #N. references in the AI text
        const refPattern = /[#*]*#(\d+)\.\s/g
        const mentionedIndices = new Set<number>()
        let match
        while ((match = refPattern.exec(aiText)) !== null) {
          mentionedIndices.add(parseInt(match[1]))
        }
        
        if (mentionedIndices.size > 0) {
          // Map mentioned indices to candidates from lookup
          for (const idx of mentionedIndices) {
            const lookupEntry = candidatesLookup.find(c => c.index === idx)
            if (lookupEntry) {
              // Try to find the full candidate in local data first
              const localCandidate = candidates.find(c => c.id === lookupEntry.id)
              if (localCandidate) {
                displayCandidates.push(localCandidate)
              } else {
                // Use lookup data as a lightweight candidate
                displayCandidates.push({
                  id: lookupEntry.id,
                  name: lookupEntry.name,
                  matchScore: lookupEntry.matchScore,
                  location: lookupEntry.location,
                  jobCategory: lookupEntry.jobCategory,
                  experience: lookupEntry.experience,
                  skills: lookupEntry.skills,
                  email: lookupEntry.email,
                  phone: lookupEntry.phone,
                  status: lookupEntry.status,
                  // Fill defaults for required Candidate fields
                  appliedDate: new Date().toISOString(),
                  summary: '',
                  isShortlisted: lookupEntry.status === 'Shortlisted',
                } as Candidate)
              }
            }
          }
          // Sort by match score descending
          displayCandidates.sort((a, b) => (b.matchScore ?? 0) - (a.matchScore ?? 0))
        }
      }
      
      // If no candidates extracted from AI text, fall back to local parseQuery
      if (displayCandidates.length === 0) {
        const { candidates: fallbackCandidates } = await parseQuery(userInput)
        displayCandidates = fallbackCandidates
      }
      
      const aiMessage: Message = {
        id: (Date.now() + 2).toString(),
        type: 'ai',
        content: aiText + (sourceInfo ? `\n\n_Source: ${sourceInfo}_` : ''),
        timestamp: new Date(),
        candidates: displayCandidates,
        intent: 'ai_response'
      }

      setMessages(prev => [...prev, aiMessage])
    } catch (error) {
      // Fallback to local NLP
      console.warn('AI service unavailable, using local NLP fallback:', error)
      
      const { candidates: foundCandidates, response, intent, actions, insights } = await parseQuery(userInput)

      // Remove loading and add real message
      setMessages(prev => prev.filter(m => m.id !== loadingId))

      const aiMessage: Message = {
        id: (Date.now() + 2).toString(),
        type: 'ai',
        content: response,
        timestamp: new Date(),
        candidates: foundCandidates,
        intent,
        actions,
        insights
      }

      setMessages(prev => [...prev, aiMessage])
    } finally {
      setIsTyping(false)
    }
  }

  const handleSuggestedPrompt = (prompt: string, category?: string) => {
    // Handle special categories
    if (category === 'job_match') {
      setShowJobMatchModal(true)
      return
    }
    setInput(prompt)
    inputRef.current?.focus()
  }

  // Handle job description matching
  const handleJobMatch = async (jobDescription: string, topN: number, file?: File) => {
    const isFileUpload = !!file
    const userMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: isFileUpload
        ? `**Job Description Matching Request**\n\nFind top ${topN} candidates for JD uploaded: **${file!.name}**`
        : `**Job Description Matching Request**\n\nFind top ${topN} candidates for:\n\n"${jobDescription.slice(0, 200)}${jobDescription.length > 200 ? '...' : ''}"`,
      timestamp: new Date()
    }

    setMessages(prev => [...prev, userMessage])
    setIsTyping(true)

    const loadingId = (Date.now() + 1).toString()
    setMessages(prev => [...prev, {
      id: loadingId,
      type: 'ai',
      content: isFileUpload
        ? `Parsing "${file!.name}" and matching candidates using AI...`
        : 'Analyzing job description and matching candidates using AI...',
      timestamp: new Date(),
      loading: true
    }])

    try {
      let data: any

      if (isFileUpload) {
        // File upload path — use FormData
        const formData = new FormData()
        formData.append('file', file!)
        formData.append('top_n', topN.toString())
        if (jobDescription) {
          formData.append('job_description', jobDescription)
        }

        const response = await authFetch(`${config.apiUrl}/api/ai/match-job-file`, {
          method: 'POST',
          body: formData
        })
        data = await response.json()
      } else {
        // Text-only path — use JSON
        const response = await authFetch(`${config.apiUrl}/api/ai/match-job`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            job_description: jobDescription,
            top_n: topN
          })
        })
        data = await response.json()
      }

      setMessages(prev => prev.filter(m => m.id !== loadingId))

      if (data.rankings && data.rankings.length > 0) {
        // Map ranked candidates to our candidate format
        const rankedCandidateIds = data.rankings.map((r: { candidate_id: string }) => r.candidate_id)
        const matchedCandidates = rankedCandidateIds
          .map((id: string) => candidates.find(c => c.id === id))
          .filter(Boolean) as Candidate[]

        // Build response with AI analysis
        let responseText = `## AI Job Matching Results\n\n`
        
        if (data.job_analysis) {
          responseText += `**Key Requirements Identified:**\n`
          responseText += data.job_analysis.key_requirements?.map((r: string) => `- ${r}`).join('\n') || 'Not specified'
          responseText += `\n\n**Experience Level:** ${data.job_analysis.experience_level || 'Not specified'}\n\n`
        }

        responseText += `**Top ${data.rankings.length} Matches** (from ${data.total_candidates_searched || 'all'} candidates):\n\n`
        
        data.rankings.forEach((r: { rank: number; candidate_name: string; job_fit_score: number; recommendation: string; match_reasons?: string[] }, _idx: number) => {
          const tier = r.job_fit_score >= 80 ? '★' : r.job_fit_score >= 60 ? '●' : '○'
          responseText += `${tier} **#${r.rank} ${r.candidate_name}** — ${r.job_fit_score}% match\n`
          responseText += `   ${r.recommendation}\n`
          if (r.match_reasons?.length) {
            responseText += `   _${r.match_reasons.slice(0, 2).join(', ')}_\n`
          }
          responseText += '\n'
        })

        if (data.summary) {
          responseText += `\n**Summary:** ${data.summary.recommendation || ''}`
        }

        responseText += `\n\nUse the **Shortlist Top Matches** button below to add them to your shortlist and automatically send notification emails.`

        const aiMessage: Message = {
          id: (Date.now() + 2).toString(),
          type: 'ai',
          content: responseText,
          timestamp: new Date(),
          candidates: matchedCandidates,
          intent: 'job_match',
          insights: [
            { title: 'Evaluated', value: data.total_candidates_searched || candidates.length, icon: Users, color: 'blue' },
            { title: 'Strong Matches', value: data.summary?.strong_matches || data.rankings.filter((r: { job_fit_score: number }) => r.job_fit_score >= 70).length, icon: Star, color: 'yellow' },
            { title: 'Top Score', value: `${data.rankings[0]?.job_fit_score || 0}%`, icon: Target, color: 'green' }
          ],
          actions: [
            {
              label: 'Shortlist Top Matches',
              icon: CheckCircle2,
              action: async () => {
                let shortlisted = 0
                for (const c of matchedCandidates) {
                  try {
                    await candidateApi.updateStatus(c.id, 'Shortlisted')
                    if (!isShortlisted(c.id)) toggleShortlist(c.id)
                    shortlisted++
                  } catch (e) {
                    console.error(`Failed to shortlist ${c.name}:`, e)
                  }
                }
                const confirmMsg: Message = {
                  id: Date.now().toString(),
                  type: 'ai',
                  content: `Successfully shortlisted **${shortlisted} candidate${shortlisted !== 1 ? 's' : ''}**. Automated notification emails have been sent to all shortlisted candidates informing them they've been selected for the next process.`,
                  timestamp: new Date(),
                  intent: 'shortlist_confirm',
                  insights: [
                    { title: 'Shortlisted', value: shortlisted, icon: CheckCircle2, color: 'green' },
                    { title: 'Emails Sent', value: shortlisted, icon: Mail, color: 'blue' }
                  ],
                  actions: [
                    { label: 'View Shortlist', icon: Star, action: () => navigate('/shortlist'), variant: 'primary' }
                  ]
                }
                setMessages(prev => [...prev, confirmMsg])
              },
              variant: 'success'
            },
            { label: 'View All Candidates', icon: Users, action: () => navigate('/candidates'), variant: 'secondary' },
            { label: 'View Shortlist', icon: Star, action: () => navigate('/shortlist'), variant: 'primary' }
          ]
        }

        setMessages(prev => [...prev, aiMessage])
      } else {
        const aiMessage: Message = {
          id: (Date.now() + 2).toString(),
          type: 'ai',
          content: data.message || 'No candidates found matching this job description. Try importing more candidates or broadening your search.',
          timestamp: new Date(),
          intent: 'job_match_empty'
        }
        setMessages(prev => [...prev, aiMessage])
      }
    } catch (error) {
      console.error('Job matching error:', error)
      setMessages(prev => prev.filter(m => m.id !== loadingId))
      
      const aiMessage: Message = {
        id: (Date.now() + 2).toString(),
        type: 'ai',
        content: 'Error matching candidates. Please ensure the backend is running and try again.',
        timestamp: new Date()
      }
      setMessages(prev => [...prev, aiMessage])
    } finally {
      setIsTyping(false)
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  /**
   * Split AI response into header + per-candidate sections.
   * E.g. "#1. Name | Score..." blocks get separated so each can render above its card.
   */
  const splitContentByCandidates = (content: string, count: number): { header: string; sections: string[] } => {
    // Match "#N." patterns at start of line (with optional bold **)
    const first = content.search(/(?:^|\n)\s*\*{0,2}#1\./)
    if (first === -1 || count === 0) {
      return { header: content, sections: [] }
    }
    const header = content.slice(0, first).trim()
    const body = content.slice(first)
    
    const sections: string[] = []
    for (let i = 1; i <= count; i++) {
      const current = new RegExp(`(?:^|\\n)\\s*\\*{0,2}#${i}\\.`)
      const next = i < count ? new RegExp(`(?:^|\\n)\\s*\\*{0,2}#${i + 1}\\.`) : null
      const startMatch = body.search(current)
      if (startMatch === -1) { sections.push(''); continue }
      const endMatch = next ? body.slice(startMatch + 1).search(next) : -1
      if (endMatch === -1) {
        sections.push(body.slice(startMatch).trim())
      } else {
        sections.push(body.slice(startMatch, startMatch + 1 + endMatch).trim())
      }
    }
    return { header, sections }
  }

  /**
   * Format AI response content with enhanced markdown rendering and clickable candidate refs.
   * Converts #N. Name patterns into clickable links that navigate to candidate profile.
   */
  const formatAIContent = (content: string, messageCandidates?: Candidate[]): string => {
    let html = content
    
    // Make candidate #N. references clickable if we have matching candidates
    if (messageCandidates && messageCandidates.length > 0) {
      // Match patterns like "#6. ANJALI J S" or "**#6. ANJALI J S**"
      html = html.replace(/(\*{0,2})#(\d+)\.\s+([A-Z][A-Za-z\s.]+?)(?=\s*\||\s*\*{0,2}\s*\|)/g, (match, bold, num, name) => {
        const trimmedName = name.trim()
        // Find matching candidate by name
        const cand = messageCandidates.find(c => {
          const cn = c.name.toLowerCase().trim()
          const rn = trimmedName.toLowerCase().trim()
          return cn === rn || cn.includes(rn) || rn.includes(cn)
        })
        if (cand) {
          return `${bold}<a href="/candidates/${cand.id}" class="ai-candidate-link" data-candidate-id="${cand.id}">#${num}. ${trimmedName}</a>${bold} `
        }
        return match
      })
    }
    
    // Horizontal rules --- or ***
    html = html.replace(/^[\s]*[-*]{3,}[\s]*$/gm, '<hr class="my-3 border-gray-200"/>')
    
    // Bold **text**
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    
    // Italic _text_ (single-line only to avoid matching across paragraphs)
    html = html.replace(/(?<!\w)_([^_\n]+)_(?!\w)/g, '<em class="text-gray-500">$1</em>')
    
    // Bullet points (- item)
    html = html.replace(/^(\s*)[-•]\s+(.+)$/gm, '$1<span class="flex gap-2"><span class="text-primary-400">•</span><span>$2</span></span>')
    
    // Newlines
    html = html.replace(/\n/g, '<br/>')
    
    return html
  }

  return (
    <div className="h-full flex flex-col bg-gradient-to-br from-primary-50/30 via-white to-primary-50/20">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex-shrink-0 bg-white border-b border-gray-200 px-6 py-4"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <motion.div
              animate={{ 
                rotate: [0, 10, -10, 0],
                scale: [1, 1.1, 1]
              }}
              transition={{ 
                duration: 2,
                repeat: Infinity,
                repeatDelay: 3
              }}
              className="w-12 h-12 bg-gradient-to-br from-primary-600 to-primary-800 rounded-2xl flex items-center justify-center shadow-lg"
            >
              <Brain className="w-6 h-6 text-white" />
            </motion.div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">AI Assistant</h1>
              <p className="text-sm text-gray-600 flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${aiStatus.available ? 'bg-success' : 'bg-warning'} opacity-75`}></span>
                  <span className={`relative inline-flex rounded-full h-2 w-2 ${aiStatus.available ? 'bg-success' : 'bg-warning'}`}></span>
                </span>
                {aiStatus.available ? 'AI Connected' : 'Local AI Mode'} • ML Features Active
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="flex items-center gap-1 bg-primary-50 text-primary-700 border-primary-200">
              <Brain className="w-3 h-3" />
              ML Ranking
            </Badge>
            <Badge variant="outline" className="flex items-center gap-1 bg-blue-50 text-blue-700 border-blue-200">
              <Target className="w-3 h-3" />
              Job Matching
            </Badge>
            <Badge 
              variant={aiStatus.available ? "success" : "outline"} 
              className="flex items-center gap-1"
            >
              {aiStatus.available ? (
                <>
                  <Sparkles className="w-3 h-3" />
                  {aiStatus.model}
                </>
              ) : (
                <>
                  <Zap className="w-3 h-3" />
                  Local NLP
                </>
              )}
            </Badge>
          </div>
        </div>
      </motion.div>

      {/* Messages Container */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        <AnimatePresence>
          {messages.map((message) => (
            <motion.div
              key={message.id}
              initial={{ opacity: 0, y: 20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.3 }}
              className={`flex gap-3 ${message.type === 'user' ? 'flex-row-reverse' : 'flex-row'}`}
            >
              {/* Avatar */}
              <motion.div
                whileHover={{ scale: 1.1 }}
                className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${
                  message.type === 'user'
                    ? 'bg-gradient-to-br from-gray-600 to-gray-700'
                    : 'bg-gradient-to-br from-primary-600 to-primary-800'
                } shadow-md`}
              >
                {message.type === 'user' ? (
                  <User className="w-5 h-5 text-white" />
                ) : (
                  <Bot className="w-5 h-5 text-white" />
                )}
              </motion.div>

              {/* Message Content */}
              <div className={`flex-1 max-w-3xl ${message.type === 'user' ? 'flex justify-end' : ''}`}>
                <motion.div
                  whileHover={{ scale: 1.01 }}
                  className={`rounded-2xl px-4 py-3 ${
                    message.type === 'user'
                      ? 'bg-gradient-to-br from-primary-600 to-primary-700 text-white'
                      : 'bg-white border border-gray-200 shadow-sm'
                  }`}
                >
                  {message.loading ? (
                    <div className="flex items-center gap-2">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span className="text-sm text-gray-500">Processing...</span>
                    </div>
                  ) : (
                    (() => {
                      const hasCandidates = message.candidates && message.candidates.length > 0
                      const split = hasCandidates
                        ? splitContentByCandidates(message.content, message.candidates!.length)
                        : null

                      return (
                        <>
                          {/* Header / full text (if no per-candidate split, show everything) */}
                          <div className="text-sm leading-relaxed whitespace-pre-wrap ai-response-content" 
                            onClick={(e) => {
                              const target = e.target as HTMLElement
                              const link = target.closest('a.ai-candidate-link')
                              if (link) {
                                e.preventDefault()
                                const candidateId = link.getAttribute('data-candidate-id')
                                if (candidateId) navigate(`/candidates/${candidateId}`)
                              }
                            }}
                            dangerouslySetInnerHTML={{ 
                              __html: formatAIContent(
                                split && split.sections.length > 0 ? split.header : message.content,
                                message.candidates
                              )
                            }} 
                          />

                          {/* Interleaved: Per-candidate AI section → candidate card */}
                          {hasCandidates && split && split.sections.length > 0 && (
                            <motion.div
                              initial={{ opacity: 0, height: 0 }}
                              animate={{ opacity: 1, height: 'auto' }}
                              transition={{ delay: 0.3 }}
                              className="mt-4 space-y-3"
                            >
                              {/* Bulk actions bar */}
                              <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2">
                                <button
                                  onClick={() => {
                                    const all = message.candidates!.map(c => c.id)
                                    setSelectedIds(prev => {
                                      const allSelected = all.every(id => prev.has(id))
                                      return allSelected ? new Set() : new Set(all)
                                    })
                                  }}
                                  className="text-xs text-gray-600 hover:text-blue-600 flex items-center gap-1"
                                >
                                  {message.candidates!.every(c => selectedIds.has(c.id))
                                    ? <CheckSquare className="w-3.5 h-3.5 text-blue-600" />
                                    : <Square className="w-3.5 h-3.5" />}
                                  {selectedIds.size > 0 ? `${[...selectedIds].filter(id => message.candidates!.some(c => c.id === id)).length} selected` : 'Select all'}
                                </button>
                                {selectedIds.size > 0 && (
                                  <div className="flex items-center gap-2">
                                    <motion.button
                                      whileHover={{ scale: 1.05 }}
                                      whileTap={{ scale: 0.95 }}
                                      onClick={() => handleShortlistSelected(message.candidates!)}
                                      className="flex items-center gap-1 px-2.5 py-1 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium"
                                    >
                                      <CheckCircle2 className="w-3 h-3" />
                                      Shortlist Selected
                                    </motion.button>
                                  </div>
                                )}
                              </div>

                              {message.candidates!.map((candidate, idx) => (
                                <motion.div
                                  key={candidate.id}
                                  initial={{ opacity: 0, x: -20 }}
                                  animate={{ opacity: 1, x: 0 }}
                                  transition={{ delay: 0.4 + idx * 0.05 }}
                                >
                                  {/* Per-candidate AI detail section */}
                                  {split.sections[idx] && (
                                    <div 
                                      className="text-xs leading-relaxed whitespace-pre-wrap ai-response-content bg-gray-50 border border-gray-100 rounded-t-xl px-3 py-2 text-gray-700"
                                      onClick={(e) => {
                                        const target = e.target as HTMLElement
                                        const link = target.closest('a.ai-candidate-link')
                                        if (link) {
                                          e.preventDefault()
                                          const candidateId = link.getAttribute('data-candidate-id')
                                          if (candidateId) navigate(`/candidates/${candidateId}`)
                                        }
                                      }}
                                      dangerouslySetInnerHTML={{ 
                                        __html: formatAIContent(split.sections[idx], message.candidates)
                                      }} 
                                    />
                                  )}
                                  {/* Candidate card */}
                                  <Card 
                                    className={`hover:shadow-md transition-all border-2 overflow-hidden ${split.sections[idx] ? 'rounded-t-none border-t-0' : ''} ${
                                      selectedIds.has(candidate.id) ? 'border-blue-400 bg-blue-50/30' : 'hover:border-primary-200'
                                    }`}
                                  >
                                    <CardContent className="p-3">
                                      {/* Row 1: Checkbox + Rank + Avatar + Name ... Score + Status */}
                                      <div className="flex items-center gap-2">
                                        <button
                                          onClick={(e) => { e.stopPropagation(); toggleSelect(candidate.id) }}
                                          className="flex-shrink-0"
                                        >
                                          {selectedIds.has(candidate.id)
                                            ? <CheckSquare className="w-4.5 h-4.5 text-blue-600" />
                                            : <Square className="w-4.5 h-4.5 text-gray-400 hover:text-gray-600" />}
                                        </button>
                                        <div className="w-7 h-7 flex-shrink-0 rounded-full bg-gradient-to-br from-primary-100 to-primary-200 flex items-center justify-center text-primary-700 font-bold text-xs">
                                          {idx + 1}
                                        </div>
                                        <Avatar className="w-8 h-8 flex-shrink-0 border-2 border-white shadow">
                                          <AvatarFallback className="text-xs font-semibold bg-gradient-to-br from-primary-100 to-primary-200 text-primary-700">
                                            {candidate.name.charAt(0)}
                                          </AvatarFallback>
                                        </Avatar>
                                        <div className="min-w-0 flex-1 cursor-pointer" onClick={() => navigate(`/candidates/${candidate.id}`)}>
                                          <h4 className="font-semibold text-gray-900 text-sm truncate hover:text-blue-600">{candidate.name}</h4>
                                          <p className="text-xs text-gray-500 flex items-center gap-1 truncate">
                                            <MapPin className="w-3 h-3 flex-shrink-0" />
                                            <span className="truncate">{candidate.location || 'N/A'}</span>
                                          </p>
                                        </div>
                                        <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
                                          <p className={`text-lg font-bold ${getMatchScoreColor(candidate.matchScore)}`}>
                                            {(candidate.matchScore ?? 50).toFixed(0)}%
                                          </p>
                                          <Badge
                                            className={`text-xs whitespace-nowrap ${getStatusBadgeColor(candidate.status).bg} ${getStatusBadgeColor(candidate.status).text} border ${getStatusBadgeColor(candidate.status).border}`}
                                          >
                                            {candidate.status}
                                          </Badge>
                                        </div>
                                      </div>
                                      {/* Row 2: Skills ... Action buttons */}
                                      <div className="flex items-center justify-between mt-2 pl-[4.5rem]">
                                        <div className="flex items-center gap-1 flex-wrap min-w-0 overflow-hidden max-w-[60%]">
                                          {candidate.skills.slice(0, 3).map((skill: string) => (
                                            <Badge key={skill} variant="outline" className="text-[10px] px-1.5 py-0 whitespace-nowrap">
                                              {skill.length > 12 ? skill.slice(0, 12) + '..' : skill}
                                            </Badge>
                                          ))}
                                          {(candidate as any).jobCategory && (candidate as any).jobCategory !== 'General' && (
                                            <Badge className="text-[10px] px-1.5 py-0 bg-blue-50 text-blue-700 border border-blue-200 whitespace-nowrap">
                                              {((candidate as any).jobCategory || '').length > 16 ? ((candidate as any).jobCategory || '').slice(0, 16) + '..' : (candidate as any).jobCategory}
                                            </Badge>
                                          )}
                                        </div>
                                        <div className="flex items-center gap-1.5 flex-shrink-0">
                                          <motion.button
                                            whileHover={{ scale: 1.1 }}
                                            whileTap={{ scale: 0.9 }}
                                            onClick={(e) => { e.stopPropagation(); navigate(`/candidates/${candidate.id}`) }}
                                            className="px-2 py-1 bg-blue-100 hover:bg-blue-200 text-blue-700 rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"
                                            title="View profile"
                                          >
                                            <Eye className="w-3 h-3" />
                                            View
                                          </motion.button>
                                          {candidate.status !== 'Shortlisted' && (
                                            <motion.button
                                              whileHover={{ scale: 1.1 }}
                                              whileTap={{ scale: 0.9 }}
                                              onClick={async (e) => {
                                                e.stopPropagation()
                                                try {
                                                  await candidateApi.updateStatus(candidate.id, 'Shortlisted')
                                                  if (!isShortlisted(candidate.id)) toggleShortlist(candidate.id)
                                                  const confirmMsg: Message = {
                                                    id: Date.now().toString(),
                                                    type: 'ai',
                                                    content: `**${candidate.name}** has been shortlisted.`,
                                                    timestamp: new Date(),
                                                    intent: 'shortlist_single',
                                                  }
                                                  setMessages(prev => [...prev, confirmMsg])
                                                } catch (err) {
                                                  console.error('Shortlist error:', err)
                                                }
                                              }}
                                              className="px-2 py-1 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"
                                              title="Shortlist this candidate"
                                            >
                                              <CheckCircle2 className="w-3 h-3" />
                                              Shortlist
                                            </motion.button>
                                          )}
                                          {candidate.status === 'Shortlisted' && (
                                            <Badge className="bg-green-100 text-green-700 text-xs whitespace-nowrap">Shortlisted</Badge>
                                          )}
                                        </div>
                                      </div>
                                    </CardContent>
                                  </Card>
                                </motion.div>
                              ))}
                            </motion.div>
                          )}
                        </>
                      )
                    })()
                  )}
                  
                  {/* Insight Cards */}
                  {message.insights && message.insights.length > 0 && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      transition={{ delay: 0.2 }}
                      className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-2"
                    >
                      {message.insights.map((insight, idx) => {
                        const Icon = insight.icon
                        const colorClasses: Record<string, string> = {
                          blue: 'bg-blue-50 text-blue-700 border-blue-200',
                          green: 'bg-green-50 text-green-700 border-green-200',
                          yellow: 'bg-yellow-50 text-yellow-700 border-yellow-200',
                          purple: 'bg-primary-50 text-primary-700 border-primary-200',
                          indigo: 'bg-primary-50 text-primary-700 border-primary-200',
                          orange: 'bg-orange-50 text-orange-700 border-orange-200',
                          red: 'bg-red-50 text-red-700 border-red-200',
                        }
                        return (
                          <motion.div
                            key={idx}
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            transition={{ delay: 0.3 + idx * 0.1 }}
                            className={`rounded-lg p-3 border ${colorClasses[insight.color] || colorClasses.blue}`}
                          >
                            <div className="flex items-center gap-2 mb-1">
                              <Icon className="w-4 h-4" />
                              <span className="text-xs font-medium">{insight.title}</span>
                            </div>
                            <p className="text-lg font-bold">{insight.value}</p>
                          </motion.div>
                        )
                      })}
                    </motion.div>
                  )}
                  
                  {/* Candidate Results (non-split fallback: when AI text has no per-candidate sections) */}
                  {message.candidates && message.candidates.length > 0 && (() => {
                    const split = splitContentByCandidates(message.content, message.candidates!.length)
                    // Only render old-style list if we didn't already render interleaved above
                    return split.sections.length > 0 ? null : (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      transition={{ delay: 0.3 }}
                      className="mt-4 space-y-2"
                    >
                      <div className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2">
                        <button
                          onClick={() => {
                            const all = message.candidates!.map(c => c.id)
                            setSelectedIds(prev => {
                              const allSelected = all.every(id => prev.has(id))
                              return allSelected ? new Set() : new Set(all)
                            })
                          }}
                          className="text-xs text-gray-600 hover:text-blue-600 flex items-center gap-1"
                        >
                          {message.candidates!.every(c => selectedIds.has(c.id))
                            ? <CheckSquare className="w-3.5 h-3.5 text-blue-600" />
                            : <Square className="w-3.5 h-3.5" />}
                          {selectedIds.size > 0 ? `${[...selectedIds].filter(id => message.candidates!.some(c => c.id === id)).length} selected` : 'Select all'}
                        </button>
                        {selectedIds.size > 0 && (
                          <div className="flex items-center gap-2">
                            <motion.button
                              whileHover={{ scale: 1.05 }}
                              whileTap={{ scale: 0.95 }}
                              onClick={() => handleShortlistSelected(message.candidates!)}
                              className="flex items-center gap-1 px-2.5 py-1 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium"
                            >
                              <CheckCircle2 className="w-3 h-3" />
                              Shortlist Selected
                            </motion.button>
                          </div>
                        )}
                      </div>

                      {message.candidates!.map((candidate, idx) => (
                        <motion.div
                          key={candidate.id}
                          initial={{ opacity: 0, x: -20 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: 0.4 + idx * 0.05 }}
                        >
                          <Card 
                            className={`hover:shadow-md transition-all border-2 overflow-hidden ${
                              selectedIds.has(candidate.id) ? 'border-blue-400 bg-blue-50/30' : 'hover:border-primary-200'
                            }`}
                          >
                            <CardContent className="p-3">
                              <div className="flex items-center gap-2">
                                <button onClick={(e) => { e.stopPropagation(); toggleSelect(candidate.id) }} className="flex-shrink-0">
                                  {selectedIds.has(candidate.id) ? <CheckSquare className="w-4.5 h-4.5 text-blue-600" /> : <Square className="w-4.5 h-4.5 text-gray-400 hover:text-gray-600" />}
                                </button>
                                <div className="w-7 h-7 flex-shrink-0 rounded-full bg-gradient-to-br from-primary-100 to-primary-200 flex items-center justify-center text-primary-700 font-bold text-xs">{idx + 1}</div>
                                <Avatar className="w-8 h-8 flex-shrink-0 border-2 border-white shadow">
                                  <AvatarFallback className="text-xs font-semibold bg-gradient-to-br from-primary-100 to-primary-200 text-primary-700">{candidate.name.charAt(0)}</AvatarFallback>
                                </Avatar>
                                <div className="min-w-0 flex-1 cursor-pointer" onClick={() => navigate(`/candidates/${candidate.id}`)}>
                                  <h4 className="font-semibold text-gray-900 text-sm truncate hover:text-blue-600">{candidate.name}</h4>
                                  <p className="text-xs text-gray-500 flex items-center gap-1 truncate"><MapPin className="w-3 h-3 flex-shrink-0" /><span className="truncate">{candidate.location || 'N/A'}</span></p>
                                </div>
                                <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
                                  <p className={`text-lg font-bold ${getMatchScoreColor(candidate.matchScore)}`}>{(candidate.matchScore ?? 50).toFixed(0)}%</p>
                                  <Badge className={`text-xs whitespace-nowrap ${getStatusBadgeColor(candidate.status).bg} ${getStatusBadgeColor(candidate.status).text} border ${getStatusBadgeColor(candidate.status).border}`}>{candidate.status}</Badge>
                                </div>
                              </div>
                              <div className="flex items-center justify-between mt-2 pl-[4.5rem]">
                                <div className="flex items-center gap-1 flex-wrap min-w-0 overflow-hidden max-w-[60%]">
                                  {candidate.skills.slice(0, 3).map((skill: string) => (
                                    <Badge key={skill} variant="outline" className="text-[10px] px-1.5 py-0 whitespace-nowrap">{skill.length > 12 ? skill.slice(0, 12) + '..' : skill}</Badge>
                                  ))}
                                </div>
                                <div className="flex items-center gap-1.5 flex-shrink-0">
                                  <motion.button whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} onClick={(e) => { e.stopPropagation(); navigate(`/candidates/${candidate.id}`) }} className="px-2 py-1 bg-blue-100 hover:bg-blue-200 text-blue-700 rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"><Eye className="w-3 h-3" />View</motion.button>
                                  {candidate.status !== 'Shortlisted' ? (
                                    <motion.button whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} onClick={async (e) => { e.stopPropagation(); try { await candidateApi.updateStatus(candidate.id, 'Shortlisted'); if (!isShortlisted(candidate.id)) toggleShortlist(candidate.id); setMessages(prev => [...prev, { id: Date.now().toString(), type: 'ai', content: `**${candidate.name}** has been shortlisted.`, timestamp: new Date(), intent: 'shortlist_single' }]) } catch (err) { console.error('Shortlist error:', err) } }} className="px-2 py-1 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"><CheckCircle2 className="w-3 h-3" />Shortlist</motion.button>
                                  ) : <Badge className="bg-green-100 text-green-700 text-xs whitespace-nowrap">Shortlisted</Badge>}
                                </div>
                              </div>
                            </CardContent>
                          </Card>
                        </motion.div>
                      ))}
                    </motion.div>
                    )
                  })()}
                  
                  {/* Action Buttons */}
                  {message.actions && message.actions.length > 0 && (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: 0.6 }}
                      className="mt-4 flex flex-wrap gap-2"
                    >
                      {message.actions.map((action, idx) => {
                        const Icon = action.icon
                        const variants: Record<string, string> = {
                          primary: 'bg-primary-600 hover:bg-primary-700 text-white',
                          secondary: 'bg-gray-100 hover:bg-gray-200 text-gray-700',
                          success: 'bg-green-600 hover:bg-green-700 text-white',
                          warning: 'bg-orange-500 hover:bg-orange-600 text-white'
                        }
                        return (
                          <motion.button
                            key={idx}
                            whileHover={{ scale: 1.05 }}
                            whileTap={{ scale: 0.95 }}
                            onClick={action.action}
                            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${variants[action.variant || 'secondary']}`}
                          >
                            <Icon className="w-4 h-4" />
                            {action.label}
                          </motion.button>
                        )
                      })}
                    </motion.div>
                  )}
                  
                  <p className="text-xs mt-2 opacity-60">
                    {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </p>
                </motion.div>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {/* Typing Indicator */}
        {isTyping && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="flex gap-3"
          >
            <div className="w-10 h-10 bg-gradient-to-br from-primary-600 to-primary-800 rounded-full flex items-center justify-center shadow-md">
              <Bot className="w-5 h-5 text-white" />
            </div>
            <div className="bg-white border border-gray-200 rounded-2xl px-4 py-3 shadow-sm">
              <div className="flex gap-1.5">
                <motion.div
                  animate={{ scale: [1, 1.2, 1], opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1, repeat: Infinity, delay: 0 }}
                  className="w-2 h-2 bg-gray-400 rounded-full"
                />
                <motion.div
                  animate={{ scale: [1, 1.2, 1], opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1, repeat: Infinity, delay: 0.2 }}
                  className="w-2 h-2 bg-gray-400 rounded-full"
                />
                <motion.div
                  animate={{ scale: [1, 1.2, 1], opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1, repeat: Infinity, delay: 0.4 }}
                  className="w-2 h-2 bg-gray-400 rounded-full"
                />
              </div>
            </div>
          </motion.div>
        )}

        {/* Suggested Prompts (shown when only welcome message) */}
        {messages.length === 1 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5 }}
            className="max-w-4xl mx-auto"
          >
            <p className="text-sm font-medium text-gray-700 mb-3 text-center">Try asking:</p>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
              {suggestedPrompts.map((prompt, index) => {
                const Icon = prompt.icon
                return (
                  <motion.button
                    key={index}
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: 0.6 + index * 0.05 }}
                    whileHover={{ scale: 1.03, y: -2 }}
                    whileTap={{ scale: 0.98 }}
                    onClick={() => handleSuggestedPrompt(prompt.text, prompt.category)}
                    className={`${prompt.bgColor} ${prompt.color} rounded-xl p-3 text-left transition-all border-2 border-transparent hover:border-current`}
                  >
                    <div className="flex items-start gap-2">
                      <Icon className="w-4 h-4 flex-shrink-0 mt-0.5" />
                      <p className="text-xs font-medium leading-tight">{prompt.text}</p>
                    </div>
                  </motion.button>
                )
              })}
            </div>
          </motion.div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex-shrink-0 bg-white border-t border-gray-200 px-6 py-4"
      >
        <div className="max-w-4xl mx-auto">
          <div className="flex gap-3">
            <div className="flex-1 relative">
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyPress}
                placeholder="Ask about ML ranking, duplicates, analytics, templates, scheduling..."
                className="w-full px-4 py-3 pr-12 border-2 border-gray-200 rounded-xl focus:outline-none focus:border-primary-400 transition-colors text-sm"
                disabled={isTyping}
              />
              <div className="absolute right-3 top-1/2 -translate-y-1/2">
                <Brain className="w-5 h-5 text-gray-400" />
              </div>
            </div>
            <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}>
              <Button
                onClick={handleSend}
                disabled={!input.trim() || isTyping}
                size="lg"
                className="px-6 shadow-md"
              >
                {isTyping ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <>
                    Send
                    <Send className="w-4 h-4 ml-2" />
                  </>
                )}
              </Button>
            </motion.div>
          </div>
          <p className="text-xs text-gray-500 mt-2 text-center">
            ML Ranking · Job Matching · Predictive Analytics · Duplicates · Email Templates · Calendar · SMS
          </p>
        </div>
      </motion.div>

      {/* Job Description Matching Modal */}
      <JobMatchModal
        isOpen={showJobMatchModal}
        onClose={() => setShowJobMatchModal(false)}
        onMatch={handleJobMatch}
      />
    </div>
  )
}