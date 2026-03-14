import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import DOMPurify from 'dompurify'
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
  CheckCircle2,
  Copy,
  Briefcase,
  X,
  Upload,
  Square,
  CheckSquare,
  Eye,
  History,
  Plus,
  Trash2,
  PanelLeftClose,
  MessageCircle,
  Phone,
  ExternalLink,
  Download,
  FileDown,
  Linkedin,
  Award,
  Video,
  XCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Avatar, AvatarFallback } from '@/components/ui/Avatar'
import { useCandidates } from '@/hooks/useCandidates'
import { useCandidateStore } from '@/store/candidateStore'
import type { Candidate } from '@/types'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAIStatus } from '@/hooks/useAIStatus'
import { useAuthStore } from '@/store/authStore'
import { advancedApi, aiApi, candidateApi } from '@/services/api'
import config from '@/config'
import { authFetch } from '@/lib/authFetch'
import { toast } from '@/components/ui/Toast'
import { generateQuickProfilePDF, downloadOriginalResume } from '@/lib/pdfGenerator'
import { isTextGarbled } from '@/lib/textUtils'
import { cleanLocation, getScoreColor, getFitLabel } from '@/lib/utils'
import { normalizeCategory } from '@/lib/categoryUtils'
import { ScoreRing } from '@/components/ui/ScoreRing'

/** Shape of the detailed AI candidate analysis returned by the backend */
interface CandidateAnalysis {
  executive_summary?: string
  hiring_recommendation?: string
  overall_rating?: string
  confidence_score?: number
  technical_assessment?: string
  experience_assessment?: string
  pros?: string[]
  cons?: string[]
  career_trajectory?: string
  interview_focus_areas?: string[]
  ideal_roles?: string[]
  hiring_recommendation_rationale?: string
  [key: string]: unknown
}

/** Shape of the job match response from the backend */
interface JobMatchData {
  rankings: {
    rank: number
    candidate_id: string
    candidate_name: string
    job_fit_score: number
    recommendation: string
    match_reasons?: string[]
  }[]
  job_analysis?: {
    key_requirements?: string[]
    experience_level?: string
  }
  total_candidates_searched?: number
  summary?: {
    recommendation?: string
    strong_matches?: number
  }
  message?: string  // Error/empty response message
}

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
    color: 'text-sky-600',
    bgColor: 'bg-sky-50 hover:bg-sky-100',
    category: 'ml'
  },
  {
    icon: Target,
    text: 'Find best matches for our open positions',
    color: 'text-sky-600',
    bgColor: 'bg-sky-50 hover:bg-sky-100',
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
    color: 'text-sky-600',
    bgColor: 'bg-sky-50 hover:bg-sky-100',
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
  const [topN, setTopN] = useState(25)
  const [isMatching, setIsMatching] = useState(false)
  const [activeTab, setActiveTab] = useState<'text' | 'file'>('file')
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleMatch = async () => {
    if (activeTab === 'text' && jobDescription.trim().length < 50) {
      toast.warning('Too short', 'Please enter a job description with at least 50 characters')
      return
    }
    if (activeTab === 'file' && !uploadedFile) {
      toast.warning('No file selected', 'Please upload a job description file (PDF, DOCX, or TXT)')
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
      toast.warning('Unsupported file', 'Only PDF, DOCX, and TXT files are supported')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      toast.warning('File too large', 'Maximum file size is 10MB')
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
              <option value={25}>Top 25</option>
              <option value={30}>Top 30</option>
              <option value={50}>Top 50</option>
              <option value={100}>Top 100</option>
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

// Restore chat history from sessionStorage so navigation doesn't lose it
const restoreChatHistory = (): Message[] => {
  try {
    const raw = sessionStorage.getItem('ai_chat_history')
    if (!raw) return []
    const parsed = JSON.parse(raw) as any[]
    // Filter out old welcome messages (id '0') — will be regenerated fresh
    return parsed.filter(m => m.id !== '0').map(m => ({
      ...m,
      timestamp: new Date(m.timestamp),
      // Re-hydrate action functions (they can't be serialized)
      actions: undefined,
      // Insights contain React component refs (icon) — not serializable
      insights: undefined,
      // Candidates are stored as data, not references
      candidates: m.candidates || undefined,
    }))
  } catch { return [] }
}

const saveChatHistory = (msgs: Message[]) => {
  try {
    // Save last 50 messages to keep storage reasonable
    const toSave = msgs.slice(-50).map(m => ({
      id: m.id,
      type: m.type,
      content: m.content,
      timestamp: m.timestamp.toISOString(),
      candidates: m.candidates,
      intent: m.intent,
      // Skip actions/insights/loading — contain non-serializable React component refs
    }))
    sessionStorage.setItem('ai_chat_history', JSON.stringify(toSave))
  } catch { /* storage full — ignore */ }
}

/** Serialized message shape stored in localStorage/sessionStorage */
interface SerializedMessage {
  id: string
  type: 'user' | 'ai'
  content: string
  timestamp: string
  candidates?: Candidate[]
  intent?: string
  [key: string]: unknown
}

// ── Chat Session Management (persisted to localStorage) ──
interface ChatSession {
  id: string
  title: string
  preview: string
  messages: SerializedMessage[]  // serialized messages
  createdAt: string
  updatedAt: string
}

const SESSIONS_KEY = 'ai_chat_sessions'
const ACTIVE_SESSION_KEY = 'ai_active_session_id'

const loadChatSessions = (): ChatSession[] => {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

const persistChatSessions = (sessions: ChatSession[]) => {
  try {
    // Keep max 50 sessions
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions.slice(0, 50)))
  } catch { /* storage full */ }
}

const getSessionTitle = (msgs: Message[]): string => {
  const firstUser = msgs.find(m => m.type === 'user')
  if (firstUser) {
    const text = firstUser.content.replace(/\*+/g, '').trim()
    return text.length > 50 ? text.slice(0, 50) + '...' : text
  }
  return 'New Chat'
}

const getSessionPreview = (msgs: Message[]): string => {
  const lastAi = [...msgs].reverse().find(m => m.type === 'ai' && !m.loading)
  if (lastAi) {
    const text = lastAi.content.replace(/\*+/g, '').replace(/<[^>]+>/g, '').trim()
    return text.length > 80 ? text.slice(0, 80) + '...' : text
  }
  return 'No messages yet'
}

export default function AIAssistant() {
  const [messages, setMessages] = useState<Message[]>(() => restoreChatHistory())
  const [input, setInput] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [showJobMatchModal, setShowJobMatchModal] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showHistory, setShowHistory] = useState(false)
  const [chatSessions, setChatSessions] = useState<ChatSession[]>(() => loadChatSessions())
  const [activeSessionId, setActiveSessionId] = useState<string>(() => {
    return localStorage.getItem(ACTIVE_SESSION_KEY) || Date.now().toString()
  })
  const [newChatTrigger, setNewChatTrigger] = useState(0)
  const [previewCandidate, setPreviewCandidate] = useState<Candidate | null>(null)
  const [previewAnalysis, setPreviewAnalysis] = useState<CandidateAnalysis | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  // ── Results Split-Panel State ──
  const [resultsView, setResultsView] = useState(false)
  const [resultsCandidates, setResultsCandidates] = useState<Candidate[]>([])
  const [selectedResultIdx, setSelectedResultIdx] = useState(0)
  const [, setShortlistingId] = useState<string | null>(null)
  const [resultDetailCandidate, setResultDetailCandidate] = useState<Candidate | null>(null)
  const [resultDetailAnalysis, setResultDetailAnalysis] = useState<CandidateAnalysis | null>(null)
  const [resultDetailLoading, setResultDetailLoading] = useState(false)
  const [hrNotes, setHrNotes] = useState<Record<string, string>>({})
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const { candidates, totalCount } = useCandidates({ autoFetch: true })
  const toggleShortlist = useCandidateStore((s) => s.toggleShortlist)
  const isShortlisted = useCandidateStore((s) => s.isShortlisted)
  const navigate = useNavigate()
  const location = useLocation()
  const aiStatus = useAIStatus()

  // Load HR notes from localStorage on mount
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('hr_candidate_notes') || '{}')
      if (saved && typeof saved === 'object') setHrNotes(saved)
    } catch { /* ignore */ }
  }, [])

  // ── Handle prefilled query from Dashboard/SearchReports "View Results" ──
  const prefillHandled = useRef(false)
  const pendingAutoSend = useRef(false)
  useEffect(() => {
    const state = location.state as { prefillQuery?: string; restoreSessionQuery?: string } | null
    if (!state || prefillHandled.current) return

    // restoreSessionQuery: try to find an existing session with this query and restore it
    if (state.restoreSessionQuery) {
      prefillHandled.current = true
      window.history.replaceState({}, '')
      const queryNorm = state.restoreSessionQuery.trim().toLowerCase()
      // Search chatSessions for a session whose first user message matches
      const existingSessions = loadChatSessions()
      const match = existingSessions.find(s => {
        const firstUserMsg = s.messages.find(m => m.type === 'user')
        if (firstUserMsg) {
          return firstUserMsg.content.trim().toLowerCase() === queryNorm
        }
        return s.title.trim().toLowerCase().replace(/\.{3}$/, '') === queryNorm.slice(0, 50)
      })
      if (match) {
        // Restore existing session — no new API call
        loadSession(match)
        return
      }
      // No matching session found — fall through to new search
      const newId = Date.now().toString()
      setActiveSessionId(newId)
      setMessages([])
      setResultsView(false)
      setResultsCandidates([])
      setResultDetailCandidate(null)
      setSelectedIds(new Set())
      setInput(state.restoreSessionQuery)
      pendingAutoSend.current = true
      return
    }

    // prefillQuery: always start a new search (from Dashboard quick actions etc.)
    if (state.prefillQuery) {
      prefillHandled.current = true
      // Clear navigation state so refreshing doesn't re-trigger
      window.history.replaceState({}, '')
      // Start a new chat session and auto-send the query
      const newId = Date.now().toString()
      setActiveSessionId(newId)
      setMessages([])
      setResultsView(false)
      setResultsCandidates([])
      setResultDetailCandidate(null)
      setSelectedIds(new Set())
      setInput(state.prefillQuery)
      pendingAutoSend.current = true
    }
  }, [location.state])

  // Auto-send once input is populated from prefill
  useEffect(() => {
    if (pendingAutoSend.current && input.trim()) {
      pendingAutoSend.current = false
      // Allow React to finish rendering, then call handleSend
      setTimeout(() => handleSend(), 200)
    }
  }, [input])

  // ── Candidate Preview Panel Logic ──
  const handlePreviewCandidate = async (candidate: Candidate) => {
    setPreviewCandidate(candidate)
    setPreviewAnalysis(null)
    setPreviewLoading(true)
    try {
      // Fetch full candidate data
      const fullRes = await authFetch(`${config.endpoints.candidates}/${candidate.id}`)
      if (fullRes.ok) {
        const fullData = await fullRes.json()
        // Merge full data with the summary card data
        const merged: Candidate = {
          ...candidate,
          location: cleanLocation(fullData.location || candidate.location),
          summary: fullData.summary || candidate.summary || '',
          workHistory: (fullData.workHistory || (fullData as any).work_history || []).map((job: Record<string, string>) => ({
            title: job.title || job.position || '',
            company: job.company || job.organization || '',
            duration: job.duration || job.period || job.years || '',
            description: job.description || job.responsibilities || '',
          })),
          education: (fullData.education || []).map((edu: Record<string, string>) => ({
            degree: edu.degree || edu.title || '',
            field: edu.field || '',
            institution: edu.institution || edu.school || '',
            year: edu.year || edu.graduation_year || '',
          })),
          resumeText: fullData.resume_text || fullData.resumeText || '',
          certifications: fullData.certifications || [],
          languages: fullData.languages || [],
        }
        setPreviewCandidate(merged)
      }
      // Fetch AI analysis
      const analysisRes = await authFetch(`${config.endpoints.candidates}/${candidate.id}/ai-analysis`)
      if (analysisRes.ok) {
        const analysis = await analysisRes.json()
        if (analysis?.executive_summary) setPreviewAnalysis(analysis)
      }
    } catch (err) {
      console.error('Preview fetch error:', err)
      toast.error('Preview Error', 'Could not load candidate details. Please try again.')
    }
    setPreviewLoading(false)
  }

  // ── Results Panel helpers ──
  const loadResultDetail = async (candidate: Candidate, index: number) => {
    setSelectedResultIdx(index)
    setResultDetailCandidate(candidate)
    setResultDetailAnalysis(null)
    setResultDetailLoading(true)
    try {
      // Fetch candidate data and AI analysis in parallel to avoid getting stuck
      const [fullRes, analysisRes] = await Promise.all([
        authFetch(`${config.endpoints.candidates}/${candidate.id}`).catch(() => null),
        authFetch(`${config.endpoints.candidates}/${candidate.id}/ai-analysis`).catch(() => null),
      ])
      if (fullRes?.ok) {
        const fullData = await fullRes.json()
        const merged: Candidate = {
          ...candidate,
          // Pull latest status from DB so shortlist badge is accurate
          status: fullData.status || candidate.status || 'New',
          location: cleanLocation(fullData.location || candidate.location),
          summary: fullData.summary || candidate.summary || '',
          workHistory: (fullData.workHistory || (fullData as any).work_history || []).map((job: Record<string, string>) => ({
            title: job.title || job.position || '',
            company: job.company || job.organization || '',
            duration: job.duration || job.period || job.years || '',
            description: job.description || job.responsibilities || '',
          })),
          education: (fullData.education || []).map((edu: Record<string, string>) => ({
            degree: edu.degree || edu.title || '',
            field: edu.field || '',
            institution: edu.institution || edu.school || '',
            year: edu.year || edu.graduation_year || '',
          })),
          resumeText: fullData.resume_text || fullData.resumeText || '',
          certifications: fullData.certifications || [],
          languages: fullData.languages || [],
        }
        setResultDetailCandidate(merged)
        // Also update the left-panel list entry with the latest status from DB
        setResultsCandidates(prev => prev.map((c, i) => i === index ? { ...c, status: merged.status } : c))
      }
      if (analysisRes?.ok) {
        const analysis = await analysisRes.json()
        if (analysis?.executive_summary) setResultDetailAnalysis(analysis)
      }
    } catch { /* ignore */ } finally {
      setResultDetailLoading(false)
    }
  }

  const handleShortlistInResults = async (candidate: Candidate, idx: number) => {
    if (!confirm(`Shortlist ${candidate.name}? A notification email will be sent.`)) return
    setShortlistingId(candidate.id)
    try {
      const result = await candidateApi.updateStatus(candidate.id, 'Shortlisted')
      if (!isShortlisted(candidate.id)) toggleShortlist(candidate.id)
      setResultsCandidates(prev => prev.map((c, i) => i === idx ? { ...c, status: 'Shortlisted' } : c))
      if (resultDetailCandidate?.id === candidate.id) {
        setResultDetailCandidate(prev => prev ? { ...prev, status: 'Shortlisted' } : prev)
      }
      const emailStatus = result?.data?.email_sent?.status
      const emailSent = emailStatus === 'success' || emailStatus === 'queued'
      setMessages(prev => [...prev, {
        id: Date.now().toString(),
        type: 'ai',
        content: emailSent
          ? `**${candidate.name}** has been shortlisted. Notification email sent!`
          : `**${candidate.name}** has been shortlisted.`,
        timestamp: new Date(),
        intent: 'shortlist_single'
      }])
    } catch (err) {
      console.error('Shortlist error:', err)
      toast.error('Error', `Failed to shortlist ${candidate.name}`)
    }
    setShortlistingId(null)
  }

  // Auto-select first candidate when results arrive
  useEffect(() => {
    if (resultsView && resultsCandidates.length > 0 && !resultDetailCandidate) {
      loadResultDetail(resultsCandidates[0], 0)
    }
  }, [resultsView, resultsCandidates.length])

  // On initial page load, check if restored messages have candidates → activate results view
  const initialResultsChecked = useRef(false)
  useEffect(() => {
    if (initialResultsChecked.current) return
    if (messages.length === 0) return
    initialResultsChecked.current = true
    const lastAiWithCandidates = [...messages].reverse().find(
      m => m.type === 'ai' && m.candidates && m.candidates.length > 0
    )
    if (lastAiWithCandidates?.candidates) {
      setResultsCandidates(lastAiWithCandidates.candidates)
      setResultDetailCandidate(null)
      setSelectedResultIdx(0)
      setResultsView(true)
    }
  }, [messages.length])

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
    if (!confirm(`Shortlist ${toShortlist.length} selected candidate${toShortlist.length !== 1 ? 's' : ''} and send notification emails?`)) return
    try {
      const ids = toShortlist.map(c => c.id)
      const result = await candidateApi.bulkShortlist(ids)
      const data = result.data
      const count = data?.shortlisted || 0
      const emailsSent = data?.emails_sent || 0
      toShortlist.forEach(c => { if (!isShortlisted(c.id)) toggleShortlist(c.id) })
      setSelectedIds(new Set())
      const confirmMsg: Message = {
        id: Date.now().toString(),
        type: 'ai',
        content: `**${count} candidate${count !== 1 ? 's' : ''}** shortlisted successfully. **${emailsSent}** personalized email${emailsSent !== 1 ? 's' : ''} sent.`,
        timestamp: new Date(),
        intent: 'shortlist_confirm',
        actions: [{ label: 'View Shortlist', icon: Star, action: () => navigate('/shortlist'), variant: 'primary' }]
      }
      setMessages(prev => [...prev, confirmMsg])
    } catch (e) {
      console.error('Bulk shortlist error:', e)
      // Fallback: try one-by-one
      let count = 0
      for (const c of toShortlist) {
        try {
          await candidateApi.updateStatus(c.id, 'Shortlisted')
          if (!isShortlisted(c.id)) toggleShortlist(c.id)
          count++
        } catch (err) { console.error('Shortlist error:', err) }
      }
      setSelectedIds(new Set())
      setMessages(prev => [...prev, { id: Date.now().toString(), type: 'ai', content: `**${count} candidate${count !== 1 ? 's' : ''}** shortlisted. Personalized emails queued.`, timestamp: new Date(), intent: 'shortlist_confirm', actions: [{ label: 'View Shortlist', icon: Star, action: () => navigate('/shortlist'), variant: 'primary' }] }])
    }
  }

  // ── Chat Session Persistence ──
  // Save current session to localStorage whenever messages change
  useEffect(() => {
    if (messages.length <= 1) return // Don't save if only welcome msg
    const hasUserMsg = messages.some(m => m.type === 'user')
    if (!hasUserMsg) return

    const serialized = messages.slice(-50).map(m => ({
      id: m.id,
      type: m.type,
      content: m.content,
      timestamp: m.timestamp instanceof Date ? m.timestamp.toISOString() : m.timestamp,
      candidates: m.candidates,
      intent: m.intent,
      // Skip insights — they contain non-serializable React component refs (icon)
    }))

    setChatSessions(prev => {
      const existing = prev.find(s => s.id === activeSessionId)
      const session: ChatSession = {
        id: activeSessionId,
        title: getSessionTitle(messages),
        preview: getSessionPreview(messages),
        messages: serialized,
        createdAt: existing?.createdAt || new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      }
      const updated = [session, ...prev.filter(s => s.id !== activeSessionId)]
      persistChatSessions(updated)
      return updated
    })
  }, [messages, activeSessionId])

  const startNewChat = () => {
    const newId = Date.now().toString()
    setActiveSessionId(newId)
    localStorage.setItem(ACTIVE_SESSION_KEY, newId)
    sessionStorage.removeItem('ai_chat_history')
    setSelectedIds(new Set())
    setShowHistory(false)
    setResultsView(false)
    setResultsCandidates([])
    setResultDetailCandidate(null)
    setResultDetailAnalysis(null)
    welcomeGenerated.current = false
    setMessages([])
    setNewChatTrigger(t => t + 1) // Force welcome useEffect re-run
  }

  const loadSession = (session: ChatSession) => {
    setActiveSessionId(session.id)
    localStorage.setItem(ACTIVE_SESSION_KEY, session.id)
    try {
      const restored: Message[] = session.messages.map((m) => ({
        id: m.id,
        type: m.type,
        content: m.content,
        timestamp: new Date(m.timestamp),
        candidates: m.candidates || undefined,
        intent: m.intent,
      }))
      setMessages(restored)
      saveChatHistory(restored)
      welcomeGenerated.current = true

      // Activate split-panel results view if last AI message has candidates
      const lastAiWithCandidates = [...restored].reverse().find(
        (m: Message) => m.type === 'ai' && m.candidates && m.candidates.length > 0
      )
      if (lastAiWithCandidates?.candidates) {
        setResultsCandidates(lastAiWithCandidates.candidates)
        // Pre-set first candidate to avoid auto-select useEffect triggering API calls
        setResultDetailCandidate(lastAiWithCandidates.candidates[0])
        setResultDetailAnalysis(null)
        setSelectedResultIdx(0)
        setResultsView(true)
      } else {
        setResultsView(false)
        setResultsCandidates([])
        setResultDetailCandidate(null)
      }
    } catch {
      setMessages([])
      setResultsView(false)
    }
    setSelectedIds(new Set())
    setShowHistory(false)
  }

  const deleteSession = (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setChatSessions(prev => {
      const updated = prev.filter(s => s.id !== sessionId)
      persistChatSessions(updated)
      return updated
    })
    if (sessionId === activeSessionId) {
      startNewChat()
    }
  }

  // Persist messages to sessionStorage whenever they change
  useEffect(() => {
    if (messages.length > 0) saveChatHistory(messages)
  }, [messages])

  // Track whether welcome has been generated to prevent re-rendering on each page load
  const welcomeGenerated = useRef(messages.length > 0)
  
  useEffect(() => {
    // Show a simple personalized welcome message
    if (welcomeGenerated.current) return
    if (candidates.length === 0) return
    welcomeGenerated.current = true

    // Clear any cached old welcome from sessionStorage
    sessionStorage.removeItem('ai_chat_history')

    const user = useAuthStore.getState().user
    const firstName = user?.name?.split(' ')[0] || 'there'

    const welcomeMessage: Message = {
      id: '0',
      type: 'ai',
      content: `Hi ${firstName}! How can I help you today?`,
      timestamp: new Date(),
    }
    setMessages([welcomeMessage])
  }, [candidates.length, totalCount, newChatTrigger])

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  // Enhanced query parser with advanced features
  // Extract requested number of candidates from query (e.g. "show me 15 candidates", "top 20", "give me 5")
  const extractRequestedCount = (query: string): number => {
    const patterns = [
      /(?:show|give|list|find|get|display|return|fetch|bring)\s+(?:me\s+)?(?:the\s+)?(?:top\s+)?(\d+)/i,
      /(?:top|best|first)\s+(\d+)/i,
      /(\d+)\s+(?:candidates|results|people|profiles|matches|applicants)/i,
    ]
    for (const p of patterns) {
      const m = query.match(p)
      if (m) return Math.min(Math.max(parseInt(m[1]), 1), 100)
    }
    return 25 // default — show more results
  }

  const parseQuery = async (query: string): Promise<{ 
    candidates: Candidate[], 
    response: string, 
    intent: string,
    actions?: ActionButton[],
    insights?: InsightCard[]
  }> => {
    const lowerQuery = query.toLowerCase()
    const requestedCount = extractRequestedCount(query)
    let filteredCandidates = [...candidates]
    let response = ''
    let intent = 'search'
    let actions: ActionButton[] = []
    let insights: InsightCard[] | undefined

    // ML RANKING
    if (lowerQuery.includes('rank') || lowerQuery.includes('ml rank') || lowerQuery.includes('machine learning')) {
      intent = 'ml_ranking'
      try {
        const candidateIds = candidates.slice(0, Math.max(20, requestedCount * 2)).map(c => c.id)
        const result = await advancedApi.ml.rankCandidates(candidateIds, undefined, requestedCount) as { data?: { rankings?: Array<{ candidate_id: string; score: number }> } }
        
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
        filteredCandidates = filteredCandidates.sort((a, b) => b.matchScore - a.matchScore).slice(0, requestedCount)
        response = `Here are the top **${requestedCount}** candidates ranked by match score (ML service unavailable):`
      }
      
      actions = [
        { label: 'Email Top 5', icon: Mail, action: () => {
          const topEmails = filteredCandidates.slice(0, 5).map(c => c.email).filter(Boolean).join(',')
          if (topEmails) window.open(`mailto:${topEmails}`)
          else toast.info('No Emails', 'No email addresses found for top candidates.')
        }, variant: 'primary' },
        { label: 'View Candidates', icon: Calendar, action: () => navigate('/candidates'), variant: 'secondary' }
      ]
    }
    
    // PREDICTIVE ANALYTICS
    else if (lowerQuery.includes('predict') || lowerQuery.includes('analytics') || lowerQuery.includes('forecast')) {
      intent = 'predictive_analytics'
      try {
        const topCandidates = [...candidates].sort((a, b) => b.matchScore - a.matchScore).slice(0, requestedCount)
        const predictions = await Promise.all(
          topCandidates.map(c => advancedApi.analytics.predict(c.id).catch(() => null))
        )
        
        filteredCandidates = topCandidates
response = `**Predictive Analytics Report**\n\nI've analyzed your top candidates to predict hiring outcomes:`
        
        const avgProbability = predictions.filter(Boolean).reduce((acc, p) => {
          const pred = (p as any)?.data || p
          return acc + (pred?.response_rate || pred?.interview_success || pred?.probability || 0.5)
        }, 0) / Math.max(predictions.filter(Boolean).length, 1)
        
        insights = [
          { title: 'Candidates Analyzed', value: topCandidates.length, icon: Users, color: 'blue' },
          { title: 'Avg Success Rate', value: `${(avgProbability * 100).toFixed(0)}%`, icon: Target, color: 'green' },
          { title: 'High Potential', value: predictions.filter((p) => { const pred = (p as any)?.data || p; return (pred?.response_rate || pred?.interview_success || pred?.probability || 0) > 0.7 }).length, icon: Star, color: 'yellow' }
        ]
      } catch (error) {
        response = `**Quick Analytics Summary**\n\n• Total Candidates: ${candidates.length}\n• Strong Matches: ${candidates.filter(c => c.status === 'Strong').length}\n• Average Score: ${candidates.length > 0 ? (candidates.reduce((acc, c) => acc + c.matchScore, 0) / candidates.length).toFixed(1) : '0.0'}%`
        filteredCandidates = []
      }
      
      actions = [
        { label: 'View Dashboard', icon: BarChart3, action: () => navigate('/dashboard'), variant: 'primary' },
        { label: 'Export Report', icon: FileText, action: () => toast.info('Export', 'Report export will be available in the next update.'), variant: 'secondary' }
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
        { label: 'Merge Duplicates', icon: Users, action: async () => {
          try {
            const result = await candidateApi.deduplicate()
            toast.success('Deduplication', `Processed ${(result as any)?.data?.checked || 0} candidates. ${(result as any)?.data?.merged || 0} duplicates merged.`)
          } catch { toast.error('Error', 'Could not run deduplication. Please try again.') }
        }, variant: 'warning' }
      ]
    }
    
    // EMAIL TEMPLATES
    else if (lowerQuery.includes('email') || lowerQuery.includes('template') || lowerQuery.includes('outreach') || lowerQuery.includes('draft')) {
      intent = 'email_templates'
      filteredCandidates = candidates.filter(c => c.status === 'Strong' || c.matchScore >= 70).slice(0, requestedCount)
      
      response = `**Email Outreach Ready**\n\nI've identified **${filteredCandidates.length} candidates** perfect for outreach. You can use our pre-built templates or create custom ones:`
      
      actions = [
        { label: 'Browse Templates', icon: FileText, action: () => navigate('/setup'), variant: 'primary' },
        { label: 'Create Campaign', icon: Mail, action: () => {
          const emails = filteredCandidates.slice(0, 5).map(c => c.email).filter(Boolean).join(',')
          if (emails) window.open(`mailto:${emails}?subject=Exciting Opportunity`)
          else toast.info('No Emails', 'No email addresses found for selected candidates.')
        }, variant: 'secondary' },
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
      filteredCandidates = candidates.filter(c => c.status === 'Strong' || c.status === 'Shortlisted').slice(0, requestedCount)
      
      response = `**Interview Scheduling**\n\nI found **${filteredCandidates.length} candidates** ready for interviews. You can schedule through our calendar integration:`
      
      actions = [
        { label: 'View Candidates', icon: Calendar, action: () => navigate('/candidates'), variant: 'primary' },
        { label: 'View Shortlist', icon: Star, action: () => navigate('/shortlist'), variant: 'secondary' }
      ]
    }
    
    // SMS / NOTIFICATIONS  
    else if (lowerQuery.includes('sms') || lowerQuery.includes('text') || lowerQuery.includes('notify') || lowerQuery.includes('message')) {
      intent = 'sms'
      filteredCandidates = candidates.filter(c => c.phone).slice(0, requestedCount)
      
      response = `**SMS Notifications**\n\n**${filteredCandidates.length} candidates** have phone numbers available for SMS outreach:`
      
      actions = [
        { label: 'Send Email Instead', icon: Mail, action: () => {
          if (filteredCandidates[0]?.email) window.open(`mailto:${filteredCandidates[0].email}`)
          else toast.info('No Email', 'No email address available for this candidate.')
        }, variant: 'primary' },
        { label: 'View Templates', icon: FileText, action: () => navigate('/setup'), variant: 'secondary' }
      ]
    }
    
    // RESUME QUALITY
    else if (lowerQuery.includes('quality') || lowerQuery.includes('resume quality') || lowerQuery.includes('analyze resume')) {
      intent = 'resume_quality'
      filteredCandidates = [...candidates].sort((a, b) => b.matchScore - a.matchScore).slice(0, requestedCount)
      
      const highQuality = filteredCandidates.filter(c => c.matchScore >= 70).length
      const mediumQuality = filteredCandidates.filter(c => c.matchScore >= 50 && c.matchScore < 70).length
      const lowQuality = filteredCandidates.filter(c => c.matchScore < 50).length
      
      response = `**Resume Quality Analysis** (Top ${requestedCount})\n\nHere's a breakdown of your candidate pool quality:`
      
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
      filteredCandidates = [...candidates].sort((a, b) => b.matchScore - a.matchScore).slice(0, requestedCount)
      response = `**Job Matching Results** (Top ${requestedCount})\n\nTop candidates matched to your open positions:`
      
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
        { label: 'Email All', icon: Mail, action: () => {
          const emails = filteredCandidates.map(c => c.email).filter(Boolean).join(',')
          if (emails) window.open(`mailto:${emails}`)
          else toast.info('No Emails', 'No email addresses found.')
        }, variant: 'primary' },
        { label: 'Shortlist All', icon: Star, action: async () => {
          const count = filteredCandidates.filter(c => c.status !== 'Shortlisted').length
          if (count === 0) { toast.info('Already Shortlisted', 'All candidates are already shortlisted.'); return }
          const typed = prompt(`⚠️ This will shortlist ${count} candidates and send notification emails.\n\nType "${count}" to confirm:`)
          if (typed !== String(count)) { if (typed !== null) toast.info('Cancelled', 'Confirmation did not match.'); return }
          try {
            const ids = filteredCandidates.map(c => c.id)
            const result = await candidateApi.bulkShortlist(ids)
            const data = result.data
            toast.success('Bulk shortlist complete', `Shortlisted ${data?.shortlisted || 0} candidates — ${data?.emails_sent || 0} personalized emails sent!`)
          } catch (e) {
            console.error('Bulk shortlist error:', e)
            toast.error('Error', 'Failed to shortlist candidates. Please try again.')
          }
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
        .slice(0, requestedCount)
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
      filteredCandidates = filteredCandidates.filter(c => c.status === 'Shortlisted')
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
        filteredCandidates = filteredCandidates.slice(0, requestedCount)
        intent = 'show_all'
        response = `Here are the top **${filteredCandidates.length} candidates** in your pipeline:`
      }
    }

    // Sort by match score
    filteredCandidates.sort((a, b) => b.matchScore - a.matchScore)

    if (filteredCandidates.length === 0 && !response.includes('No Duplicates') && !response.includes('Analytics')) {
      response = "I couldn't find any candidates matching that criteria. Try:\n\n• Different keywords or skills\n• Broader search terms\n• Check spelling"
      actions = [
        { label: 'View All Candidates', icon: Users, action: () => navigate('/candidates'), variant: 'primary' },
        { label: 'Import More', icon: Mail, action: () => navigate('/setup'), variant: 'secondary' }
      ]
    }

    return { candidates: filteredCandidates.slice(0, requestedCount), response, intent, actions, insights }
  }

  const handleSend = async () => {
    if (!input.trim() || isTyping) return

    const userMessage: Message = {
      id: crypto.randomUUID(),
      type: 'user',
      content: input,
      timestamp: new Date()
    }

    setMessages(prev => [...prev, userMessage])
    const userInput = input
    setInput('')
    setIsTyping(true)

    // Add loading message
    const loadingId = crypto.randomUUID()
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
      
      // Determine how many candidate cards to show
      const requestedCount = extractRequestedCount(userInput)
      
      // Use AI chat (3-tier: Gemini → LLM → Rule-based)
      // The backend now returns candidates_lookup alongside the text response
      const chatResult = await aiApi.chat(userInput, true, conversationHistory, requestedCount)
      
      // Remove loading message
      setMessages(prev => prev.filter(m => m.id !== loadingId))
      
      const aiText = chatResult.data?.response || 'AI service unavailable. Please try again.'
      const candidatesLookup: Record<string, unknown>[] = chatResult.data?.candidates_lookup || []
      
      // Build candidate cards from lookup data — don't depend on local store
      let displayCandidates: Candidate[] = []
      
      if (candidatesLookup.length > 0) {
        // Backend now returns candidates_lookup in the EXACT order Gemini
        // mentioned them (parsed by name from AI text). So we just use
        // them directly — no fragile index matching needed.
        const makeCandidateFromLookup = (entry: Record<string, unknown>): Candidate => {
          const local = candidates.find(c => c.id === entry.id)
          return local ? { ...local, location: cleanLocation(local.location) } : {
            id: entry.id as string,
            name: (entry.name as string) || 'Unknown',
            matchScore: (entry.matchScore as number) ?? 0,
            location: cleanLocation(entry.location as string),
            jobCategory: normalizeCategory((entry.jobCategory as string) || 'General'),
            experience: (entry.experience as number) || 0,
            skills: (entry.skills as string[]) || [],
            email: (entry.email as string) || '',
            phone: (entry.phone as string) || '',
            status: (entry.status as string) || 'New',
            appliedDate: new Date().toISOString(),
            summary: '',
            education: [],
            workHistory: [],
            resumeUrl: '',
            hasResume: (entry.hasResume as boolean) || false,
          } as Candidate
        }

        // Use lookup entries in order (already matched to AI text by backend)
        // Deduplicate by candidate ID — keep first occurrence only
        const seenIds = new Set<string>()
        displayCandidates = candidatesLookup
          .slice(0, requestedCount)
          .map(makeCandidateFromLookup)
          .filter(c => {
            if (seenIds.has(c.id)) return false
            seenIds.add(c.id)
            return true
          })
      }
      
      // If no candidates extracted from AI text, fall back to local parseQuery
      if (displayCandidates.length === 0) {
        const { candidates: fallbackCandidates } = await parseQuery(userInput)
        displayCandidates = fallbackCandidates
      }
      
      const aiMessage: Message = {
        id: crypto.randomUUID(),
        type: 'ai',
        content: aiText,
        timestamp: new Date(),
        candidates: displayCandidates,
        intent: 'ai_response'
      }

      setMessages(prev => [...prev, aiMessage])

      // Switch to split-panel results view when candidates are returned
      if (displayCandidates.length > 0) {
        setResultsCandidates(displayCandidates)
        setResultDetailCandidate(null)
        setResultDetailAnalysis(null)
        setSelectedResultIdx(0)
        setResultsView(true)
      }
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

      // Switch to split-panel results view when candidates are returned
      if (foundCandidates.length > 0) {
        setResultsCandidates(foundCandidates)
        setResultDetailCandidate(null)
        setResultDetailAnalysis(null)
        setSelectedResultIdx(0)
        setResultsView(true)
      }
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
      let data: JobMatchData

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
        if (!response.ok) {
          const err = await response.json().catch(() => ({}))
          throw new Error((err as { detail?: string }).detail || `Matching failed (${response.status})`)
        }
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
        if (!response.ok) {
          const err = await response.json().catch(() => ({}))
          throw new Error((err as { detail?: string }).detail || `Matching failed (${response.status})`)
        }
        data = await response.json()
      }

      setMessages(prev => prev.filter(m => m.id !== loadingId))

      if (data.rankings && data.rankings.length > 0) {
        // Map ranked candidates to our candidate format; fall back to ranking data for candidates not yet in local store
        type RankEntry = { candidate_id: string; job_fit_score?: number; candidate_name?: string; matched_skills?: string[]; recommendation?: string }
        const matchedCandidates = (data.rankings as RankEntry[])
          .map((ranking) => {
            const id = ranking.candidate_id
            const c = candidates.find(cand => cand.id === id)
            const score = ranking.job_fit_score ?? 0
            if (c) return { ...c, matchScore: score }
            // Candidate not yet in local store — build lightweight object from ranking data
            return {
              id,
              name: ranking.candidate_name || 'Unknown',
              email: '',
              matchScore: score,
              skills: ranking.matched_skills || [],
              jobCategory: 'General',
              status: 'New',
              experience: 0,
              education: [],
              location: '',
              summary: ranking.recommendation || '',
            } as unknown as Candidate
          })
          .filter((c): c is Candidate => c !== null)

        // Build response with AI analysis
        let responseText = `## AI Job Matching Results\n\n`
        
        if (data.job_analysis) {
          responseText += `**Key Requirements Identified:**\n`
          responseText += data.job_analysis.key_requirements?.map((r) => `- ${r}`).join('\n') || 'Not specified'
          responseText += `\n\n**Experience Level:** ${data.job_analysis.experience_level || 'Not specified'}\n\n`
        }

        responseText += `**Top ${data.rankings.length} Matches** (from ${data.total_candidates_searched || 'all'} candidates):\n\n`
        
        data.rankings.forEach((r, _idx) => {
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
            { title: 'Strong Matches', value: data.summary?.strong_matches || data.rankings.filter((r) => r.job_fit_score >= 70).length, icon: Star, color: 'yellow' },
            { title: 'Top Score', value: `${data.rankings[0]?.job_fit_score || 0}%`, icon: Target, color: 'green' }
          ],
          actions: [
            {
              label: 'Shortlist Top Matches',
              icon: CheckCircle2,
              action: async () => {
                if (!confirm(`Are you sure you want to shortlist ${matchedCandidates.length} top matches? This will also send notification emails.`)) return
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
                  content: `Successfully shortlisted **${shortlisted} candidate${shortlisted !== 1 ? 's' : ''}**. Notification emails have been sent.`,
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

        // Switch to split-panel results view for job match results
        if (matchedCandidates.length > 0) {
          setResultsCandidates(matchedCandidates)
          setResultDetailCandidate(null)
          setResultDetailAnalysis(null)
          setSelectedResultIdx(0)
          setResultsView(true)
        }
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

  /**
   * Split AI response into header + per-candidate sections.
   * E.g. "#1. Name | Score..." blocks get separated so each can render above its card.
   * Returns sections with extracted candidate names for matching.
   */
  const splitContentByCandidates = (content: string, candidates: Candidate[]): { header: string; sections: { text: string; candidateId: string | null }[] } => {
    if (!candidates.length) return { header: content, sections: [] }

    // Find ALL "#N." patterns (any number, not just sequential from 1)
    const sectionPattern = /(?:^|\n)\s*\*{0,2}#\d+\./g
    const matches: { index: number }[] = []
    let m: RegExpExecArray | null
    while ((m = sectionPattern.exec(content)) !== null) {
      matches.push({ index: m.index })
    }
    if (matches.length === 0) return { header: content, sections: [] }

    // Header is everything before the first #N.
    const firstIdx = matches[0].index
    const header = content.slice(0, firstIdx).trim()

    // Split into raw sections between each #N. boundary
    const rawSections: string[] = []
    for (let i = 0; i < matches.length; i++) {
      const start = matches[i].index
      const end = i + 1 < matches.length ? matches[i + 1].index : content.length
      rawSections.push(content.slice(start, end).trim())
    }

    // Match each section to the correct candidate by name (primary) or position (fallback)
    const usedCandidateIds = new Set<string>()
    const sections = rawSections.map((text, sectionIdx) => {
      if (!text) return { text, candidateId: null }
      
      // Extract name from section: "#N. Name | Score..." or "#N. Name\n"
      const nameMatch = text.match(/^[\s*]*#\d+\.\s*(.+?)(?:\s*\*{0,2}\s*\||\s*\*{0,2}\s*\n|\s*\*{2,})/)
      if (nameMatch) {
        const sectionName = nameMatch[1].replace(/\*+/g, '').trim().toLowerCase()
        // Try exact/fuzzy name match first
        const nameMatched = candidates.find(c => {
          if (usedCandidateIds.has(c.id)) return false
          const cn = c.name.toLowerCase().trim()
          const cnBase = cn.split(/[–\-—]/)[0].trim()
          const snBase = sectionName.split(/[–\-—]/)[0].trim()
          if (cn === sectionName || cnBase === snBase || cn.includes(snBase) || snBase.includes(cnBase)) return true
          const cnWords = cnBase.split(/\s+/).filter(w => w.length > 1)
          const snWords = snBase.split(/\s+/).filter(w => w.length > 1)
          const overlap = cnWords.filter(w => snWords.includes(w)).length
          return overlap >= 2 || (overlap >= 1 && (cnWords.length <= 2 || snWords.length <= 2))
        })
        if (nameMatched) {
          usedCandidateIds.add(nameMatched.id)
          return { text, candidateId: nameMatched.id }
        }
      }
      
      // Positional fallback: since backend now sends candidates_lookup in the
      // same order as the AI text, section N maps to candidate N
      if (sectionIdx < candidates.length && !usedCandidateIds.has(candidates[sectionIdx].id)) {
        usedCandidateIds.add(candidates[sectionIdx].id)
        return { text, candidateId: candidates[sectionIdx].id }
      }
      // Last resort: first unused candidate
      const unused = candidates.find(c => !usedCandidateIds.has(c.id))
      if (unused) {
        usedCandidateIds.add(unused.id)
        return { text, candidateId: unused.id }
      }
      return { text, candidateId: null }
    })

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
    html = html.replace(/^(\s*)[-•]\s+(.+)$/gm, '$1<span class="flex gap-2"><span class="text-sky-400">•</span><span>$2</span></span>')
    
    // Newlines
    html = html.replace(/\n/g, '<br/>')
    
    return html
  }

  return (
    <div className="h-[calc(100vh-7rem)] flex flex-col bg-gradient-to-br from-sky-50/30 via-white to-sky-50/20 -m-6 p-6">
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
              className="w-12 h-12 bg-gradient-to-br from-slate-800 to-slate-700 rounded-2xl flex items-center justify-center shadow-lg"
            >
              <Brain className="w-6 h-6 text-white" />
            </motion.div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">AI Search</h1>
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
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={startNewChat}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-white rounded-lg text-xs font-medium shadow-sm"
              title="Start new chat"
            >
              <Plus className="w-3.5 h-3.5" />
              New Chat
            </motion.button>
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={() => setShowHistory(!showHistory)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium shadow-sm border transition-colors ${
                showHistory
                  ? 'bg-sky-100 text-sky-700 border-sky-300'
                  : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
              }`}
              title="Chat history"
            >
              <History className="w-3.5 h-3.5" />
              History
              {chatSessions.length > 0 && (
                <span className="ml-1 bg-slate-800 text-white text-[10px] rounded-full w-4 h-4 flex items-center justify-center">
                  {chatSessions.length > 9 ? '9+' : chatSessions.length}
                </span>
              )}
            </motion.button>
            <Badge variant="outline" className="flex items-center gap-1 bg-sky-50 text-sky-700 border-sky-200">
              <Brain className="w-3 h-3" />
              ML Ranking
            </Badge>
            <Badge variant="outline" className="flex items-center gap-1 bg-sky-50 text-sky-700 border-sky-200">
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

      {/* Main Content Area — Sidebar + Chat */}
      <div className="flex-1 flex overflow-hidden">
        {/* Chat History Sidebar */}
        <AnimatePresence>
          {showHistory && (
            <motion.div
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 320, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.25, ease: 'easeInOut' }}
              className="flex-shrink-0 bg-white border-r border-gray-200 overflow-hidden"
            >
              <div className="w-80 h-full flex flex-col">
                {/* Sidebar Header */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-gray-50/50">
                  <div className="flex items-center gap-2">
                    <MessageCircle className="w-4 h-4 text-sky-600" />
                    <h3 className="text-sm font-semibold text-gray-900">Chat History</h3>
                  </div>
                  <motion.button
                    whileHover={{ scale: 1.1 }}
                    whileTap={{ scale: 0.9 }}
                    onClick={() => setShowHistory(false)}
                    className="p-1 hover:bg-gray-200 rounded-md transition-colors"
                  >
                    <PanelLeftClose className="w-4 h-4 text-gray-500" />
                  </motion.button>
                </div>

                {/* New Chat Button in sidebar */}
                <div className="px-3 py-2 border-b border-gray-100">
                  <motion.button
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.98 }}
                    onClick={startNewChat}
                    className="w-full flex items-center gap-2 px-3 py-2 bg-sky-50 hover:bg-sky-100 text-sky-700 rounded-lg text-sm font-medium transition-colors border border-sky-200"
                  >
                    <Plus className="w-4 h-4" />
                    New Chat
                  </motion.button>
                </div>

                {/* Sessions List */}
                <div className="flex-1 overflow-y-auto">
                  {chatSessions.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-gray-400 px-4">
                      <History className="w-10 h-10 mb-2 opacity-50" />
                      <p className="text-sm text-center">No chat history yet</p>
                      <p className="text-xs text-center mt-1">Start a conversation and it will appear here</p>
                    </div>
                  ) : (
                    <div className="py-1">
                      {chatSessions.map((session) => {
                        const isActive = session.id === activeSessionId
                        const date = new Date(session.updatedAt)
                        const now = new Date()
                        const diffMs = now.getTime() - date.getTime()
                        const diffMins = Math.floor(diffMs / 60000)
                        const diffHrs = Math.floor(diffMs / 3600000)
                        const diffDays = Math.floor(diffMs / 86400000)
                        let timeLabel = ''
                        if (diffMins < 1) timeLabel = 'Just now'
                        else if (diffMins < 60) timeLabel = `${diffMins}m ago`
                        else if (diffHrs < 24) timeLabel = `${diffHrs}h ago`
                        else if (diffDays < 7) timeLabel = `${diffDays}d ago`
                        else timeLabel = date.toLocaleDateString()

                        return (
                          <motion.button
                            key={session.id}
                            whileHover={{ x: 2 }}
                            onClick={() => loadSession(session)}
                            className={`w-full text-left px-3 py-2.5 mx-1 my-0.5 rounded-lg transition-all group ${
                              isActive
                                ? 'bg-sky-50 border border-sky-200'
                                : 'hover:bg-gray-50 border border-transparent'
                            }`}
                            style={{ width: 'calc(100% - 8px)' }}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-1.5">
                                  <MessageSquare className={`w-3.5 h-3.5 flex-shrink-0 ${isActive ? 'text-sky-600' : 'text-gray-400'}`} />
                                  <p className={`text-sm font-medium truncate ${isActive ? 'text-sky-700' : 'text-gray-800'}`}>
                                    {session.title}
                                  </p>
                                </div>
                                <p className="text-xs text-gray-500 truncate mt-0.5 pl-5">
                                  {session.preview}
                                </p>
                              </div>
                              <div className="flex items-center gap-1 flex-shrink-0">
                                <span className="text-[10px] text-gray-400 whitespace-nowrap">{timeLabel}</span>
                                <motion.button
                                  whileHover={{ scale: 1.2 }}
                                  whileTap={{ scale: 0.9 }}
                                  onClick={(e) => deleteSession(session.id, e)}
                                  className="p-0.5 rounded hover:bg-red-100 opacity-0 group-hover:opacity-100 transition-opacity"
                                  title="Delete chat"
                                >
                                  <Trash2 className="w-3 h-3 text-gray-400 hover:text-red-500" />
                                </motion.button>
                              </div>
                            </div>
                          </motion.button>
                        )
                      })}
                    </div>
                  )}
                </div>

                {/* Sidebar Footer */}
                {chatSessions.length > 0 && (
                  <div className="px-3 py-2 border-t border-gray-100 bg-gray-50/50">
                    <p className="text-[10px] text-gray-400 text-center">
                      {chatSessions.length} conversation{chatSessions.length !== 1 ? 's' : ''} saved
                    </p>
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Chat Column (messages + input) */}
        <div className="flex-1 flex flex-col overflow-hidden">

      {/* Results Split-Panel View OR Messages Container */}
      {resultsView ? (
        <div className="flex-1 flex overflow-hidden">
          {/* Left: Candidate List */}
          <div className="w-80 flex-shrink-0 border-r border-gray-200 flex flex-col bg-white">
            {/* Header */}
            <div className="p-4 border-b border-gray-100 bg-gradient-to-r from-gray-50 to-white">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 bg-sky-100 rounded-lg flex items-center justify-center">
                    <Users className="w-4 h-4 text-sky-600" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-gray-900 text-sm">Candidates</h3>
                    <p className="text-[10px] text-gray-500">{resultsCandidates.length} results</p>
                  </div>
                </div>
                <button
                  onClick={() => { setResultsView(false); setResultDetailCandidate(null); setPreviewCandidate(null) }}
                  className="text-xs text-gray-500 hover:text-sky-600 flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-sky-50 transition-colors"
                >
                  <MessageCircle className="w-3.5 h-3.5" />Chat
                </button>
              </div>
              {/* Select All / Shortlist Selected bar */}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    const allIds = resultsCandidates.map(c => c.id)
                    setSelectedIds(prev => {
                      const allSelected = allIds.every(id => prev.has(id))
                      return allSelected ? new Set() : new Set(allIds)
                    })
                  }}
                  className="flex items-center gap-1.5 text-xs text-gray-600 hover:text-sky-600 flex-shrink-0"
                >
                  {resultsCandidates.length > 0 && resultsCandidates.every(c => selectedIds.has(c.id))
                    ? <CheckSquare className="w-4 h-4 text-sky-600" />
                    : <Square className="w-4 h-4" />}
                  Select All
                </button>
                {selectedIds.size > 0 && [...selectedIds].some(id => resultsCandidates.some(c => c.id === id)) ? (
                  <button
                    onClick={async () => {
                      const toShortlist = resultsCandidates.filter(c => selectedIds.has(c.id) && c.status !== 'Shortlisted')
                      if (toShortlist.length === 0) return
                      if (!confirm(`Are you sure you want to shortlist ${toShortlist.length} selected candidates? This will also send notification emails.`)) return
                      try {
                        const ids = toShortlist.map(c => c.id)
                        const result = await candidateApi.bulkShortlist(ids)
                        const data = result.data
                        toShortlist.forEach(c => { if (!isShortlisted(c.id)) toggleShortlist(c.id) })
                        setResultsCandidates(prev => prev.map(c => selectedIds.has(c.id) ? { ...c, status: 'Shortlisted' } : c))
                        if (resultDetailCandidate && selectedIds.has(resultDetailCandidate.id)) setResultDetailCandidate(prev => prev ? { ...prev, status: 'Shortlisted' } : prev)
                        setSelectedIds(new Set())
                        setMessages(prev => [...prev, { id: Date.now().toString(), type: 'ai', content: `**${data?.shortlisted || toShortlist.length} candidates** shortlisted. **${data?.emails_sent || 0}** notification emails sent.`, timestamp: new Date(), intent: 'shortlist_confirm' }])
                      } catch (e) {
                        console.error('Bulk shortlist error:', e)
                      }
                    }}
                    className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Shortlist {[...selectedIds].filter(id => resultsCandidates.some(c => c.id === id)).length} & Send Emails
                  </button>
                ) : (
                  <button
                    onClick={async () => {
                      const toShortlist = resultsCandidates.filter(c => c.status !== 'Shortlisted')
                      if (toShortlist.length === 0) return
                      const typed = prompt(`⚠️ This will shortlist ALL ${toShortlist.length} candidates and send notification emails.\n\nType "${toShortlist.length}" to confirm:`)
                      if (typed !== String(toShortlist.length)) return
                      try {
                        const ids = toShortlist.map(c => c.id)
                        const result = await candidateApi.bulkShortlist(ids)
                        const data = result.data
                        toShortlist.forEach(c => { if (!isShortlisted(c.id)) toggleShortlist(c.id) })
                        setResultsCandidates(prev => prev.map(c => ({ ...c, status: 'Shortlisted' })))
                        if (resultDetailCandidate) setResultDetailCandidate(prev => prev ? { ...prev, status: 'Shortlisted' } : prev)
                        setMessages(prev => [...prev, { id: Date.now().toString(), type: 'ai', content: `**${data?.shortlisted || toShortlist.length} candidates** shortlisted. **${data?.emails_sent || 0}** notification emails sent.`, timestamp: new Date(), intent: 'shortlist_confirm' }])
                      } catch (e) {
                        console.error('Bulk shortlist error:', e)
                      }
                    }}
                    className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Shortlist All & Send Emails
                  </button>
                )}
              </div>
            </div>
            {/* Scrollable Candidate List */}
            <div className="flex-1 overflow-y-auto">
              {resultsCandidates.map((candidate, idx) => (
                <motion.div
                  key={candidate.id}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: idx * 0.03 }}
                  onClick={() => loadResultDetail(candidate, idx)}
                  className={`p-3 cursor-pointer border-b border-gray-100 hover:bg-sky-50/50 transition-all ${
                    selectedResultIdx === idx ? 'bg-sky-50 border-l-4 border-l-sky-500' : 'border-l-4 border-l-transparent'
                  } ${selectedIds.has(candidate.id) ? 'bg-sky-50/40' : ''}`}
                >
                  <div className="flex items-center gap-3">
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleSelect(candidate.id) }}
                      className="flex-shrink-0"
                    >
                      {selectedIds.has(candidate.id)
                        ? <CheckSquare className="w-4 h-4 text-sky-600" />
                        : <Square className="w-4 h-4 text-gray-300 hover:text-gray-500" />}
                    </button>
                    {/* Single-color initial avatar */}
                    <div className="w-10 h-10 bg-teal-600 rounded-full flex items-center justify-center text-white font-bold text-sm flex-shrink-0">
                      {candidate.name.charAt(0).toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <h4 className="font-semibold text-gray-900 text-sm truncate">{candidate.name}</h4>
                      <p className="text-xs text-gray-500 truncate">
                        {candidate.jobCategory && candidate.jobCategory !== 'General' ? candidate.jobCategory : (candidate.skills?.[0] || 'Candidate')}
                      </p>
                      <div className="flex items-center gap-1 text-xs text-gray-400 mt-0.5">
                        <MapPin className="w-3 h-3 flex-shrink-0" />
                        <span className="truncate">{cleanLocation(candidate.location) || 'N/A'}</span>
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
                      <span className="text-base font-bold text-gray-900">{(candidate.matchScore ?? 0).toFixed(0)}%</span>
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${getFitLabel(candidate.matchScore ?? 0).cls}`}>
                        {getFitLabel(candidate.matchScore ?? 0).text}
                      </span>
                      {candidate.status === 'Shortlisted' ? (
                        <span className="text-[10px] text-green-600 font-medium">✓ Shortlisted</span>
                      ) : (
                        <button
                          onClick={(e) => { e.stopPropagation(); handleShortlistInResults(candidate, idx) }}
                          className="text-[10px] text-green-600 hover:text-green-700 font-medium flex items-center gap-0.5 hover:bg-green-50 px-1.5 py-0.5 rounded transition-colors"
                        >
                          <CheckCircle2 className="w-3 h-3" />Shortlist
                        </button>
                      )}
                    </div>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>

          {/* Right: Detail Panel */}
          <div className="flex-1 flex flex-col overflow-hidden bg-white">
            {resultDetailCandidate ? (
              <div className="flex-1 overflow-y-auto">
                {/* Animated Gradient Header */}
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, ease: 'easeOut' }}
                  className="relative overflow-hidden bg-gradient-to-r from-slate-800 via-slate-700 to-teal-700 text-white p-6"
                >
                  {/* Subtle animated background shimmer */}
                  <motion.div
                    animate={{ x: ['-100%', '200%'] }}
                    transition={{ duration: 3, repeat: Infinity, repeatDelay: 2, ease: 'linear' }}
                    className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent skew-x-12"
                  />
                  <div className="relative flex items-start justify-between">
                    <div className="flex items-start gap-4">
                      <motion.div
                        initial={{ scale: 0.8, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        transition={{ delay: 0.15, duration: 0.3 }}
                        className="w-14 h-14 rounded-full bg-teal-600 flex items-center justify-center text-2xl font-bold ring-2 ring-white/30 flex-shrink-0"
                      >
                        {resultDetailCandidate.name.charAt(0)}
                      </motion.div>
                      <div>
                        <h2 className="text-xl font-bold">{resultDetailCandidate.name}</h2>
                        <p className="text-teal-200 text-sm">
                          {resultDetailCandidate.jobCategory && resultDetailCandidate.jobCategory !== 'General' ? resultDetailCandidate.jobCategory : 'Candidate'}
                        </p>
                        <div className="flex items-center gap-3 mt-2 text-sm text-slate-300 flex-wrap">
                          {cleanLocation(resultDetailCandidate.location) && (
                            <span className="flex items-center gap-1"><MapPin className="w-3.5 h-3.5" />{cleanLocation(resultDetailCandidate.location)}</span>
                          )}
                          {resultDetailCandidate.experience > 0 && (
                            <span className="flex items-center gap-1">• {resultDetailCandidate.experience}+ Years experience</span>
                          )}
                        </div>
                        <div className="flex items-center gap-4 mt-2 text-sm text-slate-300 flex-wrap">
                          {resultDetailCandidate.email && (
                            <a href={`mailto:${resultDetailCandidate.email}`} className="flex items-center gap-1 hover:text-white transition-colors">
                              <Mail className="w-3.5 h-3.5" />{resultDetailCandidate.email}
                            </a>
                          )}
                          {resultDetailCandidate.phone && (
                            <a href={`tel:${resultDetailCandidate.phone}`} className="flex items-center gap-1 hover:text-white transition-colors">
                              <Phone className="w-3.5 h-3.5" />{resultDetailCandidate.phone}
                            </a>
                          )}
                        </div>
                      </div>
                    </div>
                    <motion.div
                      initial={{ scale: 0.7, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      transition={{ delay: 0.2, duration: 0.4, type: 'spring', stiffness: 200 }}
                      className="flex-shrink-0"
                    >
                      <ScoreRing score={resultDetailCandidate.matchScore ?? 0} size={72} />
                      <div className="text-center mt-1">
                        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded ${getFitLabel(resultDetailCandidate.matchScore ?? 0).cls}`}>
                          {getFitLabel(resultDetailCandidate.matchScore ?? 0).text}
                        </span>
                      </div>
                    </motion.div>
                  </div>
                </motion.div>

                {/* Content Sections */}
                <div className="p-6 space-y-6">

                  {/* ── Quick Action Buttons ── */}
                  <div className="flex items-center gap-2 flex-wrap">
                    <Button
                      size="sm"
                      className="bg-sky-600 hover:bg-sky-700 text-white shadow-sm"
                      onClick={() => navigate(`/candidates/${resultDetailCandidate!.id}`)}
                    >
                      <Calendar className="w-3.5 h-3.5 mr-1.5" />Schedule Interview
                    </Button>
                    <Button
                      size="sm"
                      className={resultDetailCandidate.status === 'Shortlisted'
                        ? 'bg-amber-100 text-amber-700 border border-amber-300 cursor-default'
                        : 'bg-amber-500 hover:bg-amber-600 text-white shadow-sm'}
                      disabled={resultDetailCandidate.status === 'Shortlisted'}
                      onClick={async () => {
                        if (!confirm(`Shortlist ${resultDetailCandidate!.name}? A notification email will be sent.`)) return
                        try {
                          const result = await candidateApi.updateStatus(resultDetailCandidate!.id, 'Shortlisted')
                          if (!isShortlisted(resultDetailCandidate!.id)) toggleShortlist(resultDetailCandidate!.id)
                          setResultDetailCandidate(prev => prev ? { ...prev, status: 'Shortlisted' } : prev)
                          setResultsCandidates(prev => prev.map((c, i) => i === selectedResultIdx ? { ...c, status: 'Shortlisted' } : c))
                          const emailStatus = result?.data?.email_sent?.status
                          const emailSent = emailStatus === 'success' || emailStatus === 'queued'
                          if (emailSent) {
                            toast.success('Shortlisted', `${resultDetailCandidate!.name} shortlisted — email sent!`)
                          } else {
                            toast.success('Shortlisted', `${resultDetailCandidate!.name} has been shortlisted.`)
                          }
                        } catch { toast.error('Error', 'Failed to shortlist candidate.') }
                      }}
                    >
                      <Star className="w-3.5 h-3.5 mr-1.5" />
                      {resultDetailCandidate.status === 'Shortlisted' ? 'Shortlisted' : 'Shortlist'}
                    </Button>
                    <Button
                      size="sm"
                      className={resultDetailCandidate.status === 'Rejected'
                        ? 'bg-red-100 text-red-600 border border-red-300 cursor-default'
                        : 'bg-red-500 hover:bg-red-600 text-white shadow-sm'}
                      disabled={resultDetailCandidate.status === 'Rejected'}
                      onClick={async () => {
                        if (!confirm(`Reject ${resultDetailCandidate!.name}? A rejection email will be sent.`)) return
                        try {
                          const result = await candidateApi.updateStatus(resultDetailCandidate!.id, 'Rejected')
                          setResultDetailCandidate(prev => prev ? { ...prev, status: 'Rejected' } : prev)
                          setResultsCandidates(prev => prev.map((c, i) => i === selectedResultIdx ? { ...c, status: 'Rejected' } : c))
                          const emailStatus = result?.data?.email_sent?.status
                          const emailSent = emailStatus === 'success' || emailStatus === 'queued'
                          if (emailSent) {
                            toast.success('Rejected', `${resultDetailCandidate!.name} rejected — rejection email sent.`)
                          } else {
                            toast.success('Rejected', `${resultDetailCandidate!.name} has been rejected.`)
                          }
                        } catch { toast.error('Error', 'Failed to reject candidate.') }
                      }}
                    >
                      <XCircle className="w-3.5 h-3.5 mr-1.5" />
                      {resultDetailCandidate.status === 'Rejected' ? 'Rejected' : 'Reject'}
                    </Button>

                    <div className="w-px h-6 bg-gray-200 mx-1" />

                    <Button
                      size="sm"
                      variant="outline"
                      className="border-green-300 text-green-700 hover:bg-green-50"
                      onClick={() => {
                        const phone = resultDetailCandidate!.phone?.replace(/[^0-9+]/g, '') || ''
                        const name = resultDetailCandidate!.name || 'Candidate'
                        const msg = encodeURIComponent(`Hi ${name}, we reviewed your profile and would like to discuss a potential opportunity with you. Please let us know your availability.`)
                        window.open(`https://wa.me/${phone.replace('+', '')}?text=${msg}`, '_blank')
                      }}
                    >
                      <MessageCircle className="w-3.5 h-3.5 mr-1.5" />WhatsApp
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="border-sky-300 text-sky-700 hover:bg-sky-50"
                      onClick={() => {
                        const email = resultDetailCandidate!.email || ''
                        const name = resultDetailCandidate!.name || 'Candidate'
                        const subject = encodeURIComponent(`Regarding Your Application - ${name}`)
                        const body = encodeURIComponent(`Hi ${name},\n\nThank you for your application. We have reviewed your profile and would like to discuss a potential opportunity with you.\n\nPlease let us know your availability for a brief call or interview.\n\nBest regards`)
                        window.open(`mailto:${email}?subject=${subject}&body=${body}`)
                      }}
                    >
                      <Mail className="w-3.5 h-3.5 mr-1.5" />Email
                    </Button>

                    <div className="w-px h-6 bg-gray-200 mx-1" />

                    <Button size="sm" variant="outline" onClick={async () => {
                      try { await downloadOriginalResume(resultDetailCandidate!) } catch {
                        toast.error('No Resume', 'No resume file available for this candidate. Use Export Report for a profile summary.')
                      }
                    }}>
                      <Download className="w-3.5 h-3.5 mr-1.5" />Download Resume
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => generateQuickProfilePDF(resultDetailCandidate!)}>
                      <FileDown className="w-3.5 h-3.5 mr-1.5" />Export Report
                    </Button>
                  </div>

                  {/* AI Analysis */}
                  <section>
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-3">
                      <Sparkles className="w-4 h-4 text-sky-500" />AI Analysis
                    </h3>
                    <div className="bg-gradient-to-br from-sky-50/50 to-indigo-50/50 rounded-xl p-4 border border-sky-100 space-y-4">
                      {resultDetailLoading ? (
                        <div className="flex items-center gap-2 py-2">
                          <Loader2 className="w-4 h-4 animate-spin text-sky-400" />
                          <span className="text-sm text-gray-500">Loading AI analysis...</span>
                        </div>
                      ) : resultDetailAnalysis?.executive_summary ? (
                        <>
                          {/* Executive Summary */}
                          <p className="text-sm text-gray-700 leading-relaxed">{resultDetailAnalysis.executive_summary}</p>

                          {/* Hiring Recommendation Badge */}
                          {resultDetailAnalysis.hiring_recommendation && (
                            <div className="flex items-center gap-3 flex-wrap">
                              <span className={`text-xs font-bold px-3 py-1 rounded-full ${
                                resultDetailAnalysis.hiring_recommendation === 'STRONG_HIRE' ? 'bg-emerald-100 text-emerald-700' :
                                resultDetailAnalysis.hiring_recommendation === 'HIRE' ? 'bg-green-100 text-green-700' :
                                resultDetailAnalysis.hiring_recommendation === 'CONSIDER' ? 'bg-amber-100 text-amber-700' :
                                'bg-red-100 text-red-700'
                              }`}>
                                {resultDetailAnalysis.hiring_recommendation.replace(/_/g, ' ')}
                              </span>
                              {resultDetailAnalysis.overall_rating && (
                                <span className="text-xs font-semibold text-gray-500">Rating: {resultDetailAnalysis.overall_rating}</span>
                              )}
                              {resultDetailAnalysis.confidence_score && (
                                <span className="text-xs text-gray-400">Confidence: {resultDetailAnalysis.confidence_score}%</span>
                              )}
                            </div>
                          )}

                          {/* Technical Assessment */}
                          {resultDetailAnalysis.technical_assessment && (
                            <div>
                              <h4 className="text-xs font-semibold text-gray-600 mb-1 flex items-center gap-1"><Zap className="w-3 h-3 text-sky-500" />Technical Assessment</h4>
                              <p className="text-xs text-gray-600 leading-relaxed">{resultDetailAnalysis.technical_assessment}</p>
                            </div>
                          )}

                          {/* Experience Assessment */}
                          {resultDetailAnalysis.experience_assessment && (
                            <div>
                              <h4 className="text-xs font-semibold text-gray-600 mb-1 flex items-center gap-1"><Briefcase className="w-3 h-3 text-sky-500" />Experience Assessment</h4>
                              <p className="text-xs text-gray-600 leading-relaxed">{resultDetailAnalysis.experience_assessment}</p>
                            </div>
                          )}

                          {/* Pros & Cons */}
                          {((resultDetailAnalysis.pros?.length ?? 0) > 0 || (resultDetailAnalysis.cons?.length ?? 0) > 0) && (
                            <div className="grid grid-cols-2 gap-3">
                              {(resultDetailAnalysis.pros?.length ?? 0) > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-green-700 mb-1.5 flex items-center gap-1"><CheckCircle2 className="w-3 h-3" />Strengths</h4>
                                  <ul className="space-y-1">
                                    {resultDetailAnalysis.pros!.map((p: string, i: number) => (
                                      <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                        <span className="w-1.5 h-1.5 bg-green-400 rounded-full mt-1 flex-shrink-0" />{p}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                              {(resultDetailAnalysis.cons?.length ?? 0) > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-amber-700 mb-1.5 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />Areas to Explore</h4>
                                  <ul className="space-y-1">
                                    {resultDetailAnalysis.cons!.map((c: string, i: number) => (
                                      <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                        <span className="w-1.5 h-1.5 bg-amber-400 rounded-full mt-1 flex-shrink-0" />{c}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          )}

                          {/* Career Trajectory */}
                          {resultDetailAnalysis.career_trajectory && (
                            <div>
                              <h4 className="text-xs font-semibold text-gray-600 mb-1 flex items-center gap-1"><TrendingUp className="w-3 h-3 text-sky-500" />Career Trajectory</h4>
                              <p className="text-xs text-gray-600 leading-relaxed">{resultDetailAnalysis.career_trajectory}</p>
                            </div>
                          )}

                          {/* Interview Focus Areas */}
                          {(resultDetailAnalysis.interview_focus_areas?.length ?? 0) > 0 && (
                            <div>
                              <h4 className="text-xs font-semibold text-gray-600 mb-1.5 flex items-center gap-1"><Target className="w-3 h-3 text-sky-500" />Interview Focus Areas</h4>
                              <div className="flex flex-wrap gap-1.5">
                                {resultDetailAnalysis.interview_focus_areas!.map((area: string, i: number) => (
                                  <span key={i} className="text-[10px] px-2 py-0.5 bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-full">{area}</span>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Ideal Roles */}
                          {(resultDetailAnalysis.ideal_roles?.length ?? 0) > 0 && resultDetailAnalysis.ideal_roles![0] !== 'General' && (
                            <div>
                              <h4 className="text-xs font-semibold text-gray-600 mb-1.5 flex items-center gap-1"><Star className="w-3 h-3 text-sky-500" />Ideal Roles</h4>
                              <div className="flex flex-wrap gap-1.5">
                                {resultDetailAnalysis.ideal_roles!.map((role: string, i: number) => (
                                  <span key={i} className="text-[10px] px-2 py-0.5 bg-sky-50 text-sky-700 border border-sky-200 rounded-full">{role}</span>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Recommendation Rationale */}
                          {resultDetailAnalysis.hiring_recommendation_rationale && (
                            <div className="pt-2 border-t border-sky-100">
                              <p className="text-[11px] text-gray-500 italic leading-relaxed">{resultDetailAnalysis.hiring_recommendation_rationale}</p>
                            </div>
                          )}
                        </>
                      ) : resultDetailCandidate.summary ? (
                        <p className="text-sm text-gray-700 leading-relaxed">{resultDetailCandidate.summary}</p>
                      ) : (
                        <p className="text-sm text-gray-400 italic">No AI analysis available for this candidate.</p>
                      )}
                    </div>
                  </section>

                  {/* Resume Highlights — hide garbled/mojibake text */}
                  {resultDetailCandidate.resumeText && (() => {
                    const txt = resultDetailCandidate.resumeText || ''
                    if (isTextGarbled(txt)) {
                      return (
                        <section>
                          <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-3">
                            <FileText className="w-4 h-4 text-sky-400" />Resume
                          </h3>
                          <div className="text-sm text-gray-500 italic bg-gray-50 rounded-xl p-4 border border-gray-200 flex items-center gap-2">
                            <FileDown className="w-4 h-4 text-gray-400" />
                            Resume text could not be displayed. Use <strong className="text-sky-600 mx-1">Download Resume</strong> below.
                          </div>
                        </section>
                      )
                    }
                    const lines = txt.split('\n').filter(l => l.trim().length > 10).slice(0, 8)
                    return (
                      <section>
                        <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-3">
                          <FileText className="w-4 h-4 text-sky-400" />Resume Highlights
                        </h3>
                        <div className="space-y-2">
                          {lines.map((line, i) => (
                            <div key={i} className="flex items-start gap-2 p-2.5 bg-sky-50/40 rounded-lg border border-sky-100">
                              <div className="w-1.5 h-1.5 bg-sky-400 rounded-full mt-1.5 flex-shrink-0" />
                              <p className="text-sm text-gray-700 leading-relaxed">{line.trim()}</p>
                            </div>
                          ))}
                        </div>
                      </section>
                    )
                  })()}

                  {/* Career Timeline */}
                  {resultDetailCandidate.workHistory && resultDetailCandidate.workHistory.length > 0 && (
                    <section>
                      <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-3">
                        <Briefcase className="w-4 h-4 text-sky-500" />Career Timeline
                      </h3>
                      <div className="relative">
                        {resultDetailCandidate.workHistory.slice(0, 5).map((job, i) => (
                          <div key={i} className="flex gap-4 mb-4 last:mb-0">
                            {/* Timeline dot and line */}
                            <div className="flex flex-col items-center">
                              <div className="w-3 h-3 bg-sky-500 rounded-full border-2 border-white shadow-sm flex-shrink-0" />
                              {i < Math.min(resultDetailCandidate.workHistory!.length, 5) - 1 && (
                                <div className="w-0.5 flex-1 bg-sky-200 mt-1" />
                              )}
                            </div>
                            {/* Content */}
                            <div className="flex-1 pb-2">
                              <div className="flex items-start justify-between">
                                <div>
                                  <h4 className="text-sm font-semibold text-gray-900">{job.title}</h4>
                                  <p className="text-xs text-gray-500">{job.company}</p>
                                </div>
                                {job.duration && (
                                  <span className="text-[10px] font-medium text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full whitespace-nowrap flex-shrink-0 ml-2">{job.duration}</span>
                                )}
                              </div>
                              {/* Skill tags per job — derive from job title/description */}
                              <div className="flex flex-wrap gap-1 mt-2">
                                {(resultDetailCandidate.skills || []).slice(i * 3, i * 3 + 5).map((skill: string, si: number) => (
                                  <span key={si} className="text-[10px] px-2 py-0.5 bg-sky-50 text-sky-700 border border-sky-200 rounded-full">{skill}</span>
                                ))}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {/* Skills Overview */}
                  {resultDetailCandidate.skills && resultDetailCandidate.skills.length > 0 && (
                    <section>
                      <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-3">
                        <Target className="w-4 h-4 text-sky-500" />Skills ({resultDetailCandidate.skills.length})
                      </h3>
                      <div className="flex flex-wrap gap-1.5">
                        {resultDetailCandidate.skills.map((s: string) => (
                          <span key={s} className="text-[11px] px-2.5 py-1 bg-sky-50 text-sky-700 border border-sky-200 rounded-full font-medium">{s}</span>
                        ))}
                      </div>
                    </section>
                  )}

                  {/* HR Actions */}
                  <section>
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-3">
                      <CheckCircle2 className="w-4 h-4 text-sky-500" />HR Actions
                    </h3>
                    <div className="flex items-center gap-4 mb-4">
                      {[
                        { label: 'Selected', icon: CheckCircle2, status: 'Hired' as const, color: 'text-green-600' },
                        { label: 'Shortlisted', icon: Star, status: 'Shortlisted' as const, color: 'text-amber-600' },
                        { label: 'Interviewed', icon: Video, status: 'Interviewing' as const, color: 'text-blue-600' },
                        { label: 'Rejected', icon: XCircle, status: 'Rejected' as const, color: 'text-red-600' },
                      ].map((action) => {
                        const isActive = resultDetailCandidate.status === action.status
                        return (
                          <label key={action.label} className="flex items-center gap-2 cursor-pointer group">
                            <button
                              onClick={async () => {
                                try {
                                  await candidateApi.updateStatus(resultDetailCandidate!.id, action.status)
                                  setResultDetailCandidate(prev => prev ? { ...prev, status: action.status } : prev)
                                  setResultsCandidates(prev => prev.map((c, i) => i === selectedResultIdx ? { ...c, status: action.status } : c))
                                } catch { /* ignore */ }
                              }}
                              className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-all ${
                                isActive ? `${action.color} border-current bg-current/10` : 'border-gray-300 hover:border-gray-400'
                              }`}
                            >
                              {isActive && <CheckCircle2 className="w-3 h-3" />}
                            </button>
                            <span className={`text-sm ${isActive ? `font-medium ${action.color}` : 'text-gray-600 group-hover:text-gray-900'}`}>{action.label}</span>
                          </label>
                        )
                      })}
                    </div>

                    {/* HR Comments & Notes */}
                    <div>
                      <h4 className="text-xs font-semibold text-gray-700 mb-2 flex items-center gap-1.5">
                        <MessageSquare className="w-3.5 h-3.5 text-gray-400" />HR Comments & Notes
                      </h4>
                      <textarea
                        aria-label="HR Comments and Notes"
                        value={hrNotes[resultDetailCandidate.id] || ''}
                        onChange={(e) => {
                          const id = resultDetailCandidate.id
                          setHrNotes(prev => ({ ...prev, [id]: e.target.value }))
                          // Auto-save to localStorage
                          try {
                            const saved = JSON.parse(localStorage.getItem('hr_candidate_notes') || '{}')
                            saved[id] = e.target.value
                            localStorage.setItem('hr_candidate_notes', JSON.stringify(saved))
                          } catch { /* storage full */ }
                        }}
                        placeholder="Add your comments, interview notes, or feedback about this candidate..."
                        className="w-full h-24 text-sm border border-gray-200 rounded-xl p-3 resize-none focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-300 bg-gray-50/50"
                      />
                    </div>
                  </section>


                </div>
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-gray-400">
                <Users className="w-12 h-12 mb-3 opacity-50" />
                <p className="text-sm">Select a candidate to view details</p>
              </div>
            )}
          </div>
        </div>
      ) : (
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
                    : 'bg-gradient-to-br from-slate-800 to-slate-700'
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
                      ? 'bg-gradient-to-br from-slate-800 to-slate-700 text-white'
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
                        ? splitContentByCandidates(message.content, message.candidates!)
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
                              __html: DOMPurify.sanitize(formatAIContent(
                                split && split.sections.length > 0 ? split.header : message.content,
                                message.candidates
                              ), { ADD_ATTR: ['data-candidate-id'] })
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
                                  className="text-xs text-gray-600 hover:text-sky-600 flex items-center gap-1"
                                >
                                  {message.candidates!.every(c => selectedIds.has(c.id))
                                    ? <CheckSquare className="w-3.5 h-3.5 text-sky-600" />
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

                              {message.candidates!.map((candidate, idx) => {
                                return (
                                <motion.div
                                  key={candidate.id}
                                  initial={{ opacity: 0, x: -20 }}
                                  animate={{ opacity: 1, x: 0 }}
                                  transition={{ delay: 0.4 + idx * 0.05 }}
                                >
                                  {/* Separator line between candidates */}
                                  {idx > 0 && (
                                    <div className="my-3 border-t-2 border-dashed border-gray-200" />
                                  )}
                                  {/* Unified candidate card — entire card clickable */}
                                  <Card 
                                    className={`overflow-hidden border-2 hover:shadow-md transition-all cursor-pointer ${
                                      selectedIds.has(candidate.id) ? 'border-sky-400 bg-sky-50/30' : 'hover:border-sky-200'
                                    }`}
                                    onClick={() => handlePreviewCandidate(candidate)}
                                  >
                                    {/* Header: Checkbox + Rank + Avatar + Name + Score + Actions */}
                                    <div className="flex items-center gap-2 px-3 py-2.5 bg-gradient-to-r from-gray-50 to-white border-b border-gray-100">
                                      <button
                                        onClick={(e) => { e.stopPropagation(); toggleSelect(candidate.id) }}
                                        className="flex-shrink-0"
                                      >
                                        {selectedIds.has(candidate.id)
                                          ? <CheckSquare className="w-4.5 h-4.5 text-sky-600" />
                                          : <Square className="w-4.5 h-4.5 text-gray-400 hover:text-gray-600" />}
                                      </button>
                                      <div className="w-7 h-7 flex-shrink-0 rounded-full bg-gradient-to-br from-sky-100 to-sky-200 flex items-center justify-center text-sky-700 font-bold text-xs">
                                        {idx + 1}
                                      </div>
                                      <Avatar className="w-8 h-8 flex-shrink-0 border-2 border-white shadow">
                                        <AvatarFallback className="text-xs font-semibold bg-gradient-to-br from-sky-100 to-sky-200 text-sky-700">
                                          {candidate.name.charAt(0)}
                                        </AvatarFallback>
                                      </Avatar>
                                      <div className="min-w-0 flex-1">
                                        <h4 className="font-semibold text-gray-900 text-sm truncate hover:text-sky-600">{candidate.name}</h4>
                                        <p className="text-xs text-gray-500 flex items-center gap-1 truncate">
                                          <MapPin className="w-3 h-3 flex-shrink-0" />
                                          <span className="truncate">{cleanLocation(candidate.location) || 'N/A'}</span>
                                        </p>
                                      </div>
                                      <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
                                        <p className={`text-lg font-bold ${getScoreColor(candidate.matchScore ?? 0)}`}>
                                          {(candidate.matchScore ?? 0).toFixed(0)}%
                                        </p>
                                        <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap ${getFitLabel(candidate.matchScore ?? 0).cls}`}>
                                          {getFitLabel(candidate.matchScore ?? 0).text}
                                        </span>
                                        <motion.button
                                          whileHover={{ scale: 1.1 }}
                                          whileTap={{ scale: 0.9 }}
                                          onClick={(e) => { e.stopPropagation(); handlePreviewCandidate(candidate) }}
                                          className="px-2 py-1 bg-sky-100 hover:bg-sky-200 text-sky-700 rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"
                                        >
                                          <Eye className="w-3 h-3" />Preview
                                        </motion.button>
                                        <motion.button
                                          whileHover={{ scale: 1.1 }}
                                          whileTap={{ scale: 0.9 }}
                                          onClick={(e) => { e.stopPropagation(); navigate(`/candidates/${candidate.id}`) }}
                                          className="px-2 py-1 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"
                                        >
                                          <ExternalLink className="w-3 h-3" />Open
                                        </motion.button>
                                        {candidate.status !== 'Shortlisted' ? (
                                          <motion.button
                                            whileHover={{ scale: 1.1 }}
                                            whileTap={{ scale: 0.9 }}
                                            onClick={async (e) => {
                                              e.stopPropagation()
                                              try {
                                                await candidateApi.updateStatus(candidate.id, 'Shortlisted')
                                                if (!isShortlisted(candidate.id)) toggleShortlist(candidate.id)
                                                setMessages(prev => [...prev, { id: Date.now().toString(), type: 'ai', content: `**${candidate.name}** has been shortlisted. Notification email queued.`, timestamp: new Date(), intent: 'shortlist_single' }])
                                              } catch (err) { console.error('Shortlist error:', err) }
                                            }}
                                            className="px-2 py-1 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"
                                          >
                                            <CheckCircle2 className="w-3 h-3" />Shortlist
                                          </motion.button>
                                        ) : <Badge className="bg-green-100 text-green-700 text-xs whitespace-nowrap">Shortlisted ✓</Badge>}
                                      </div>
                                    </div>
                                    {/* Skills row */}
                                    <div className="flex items-center gap-1 flex-wrap px-3 py-1.5 bg-white border-b border-gray-100">
                                      {candidate.skills.slice(0, 4).map((skill: string) => (
                                        <Badge key={skill} variant="outline" className="text-[10px] px-1.5 py-0 whitespace-nowrap">
                                          {skill.length > 14 ? skill.slice(0, 14) + '..' : skill}
                                        </Badge>
                                      ))}
                                      {(candidate as any).jobCategory && normalizeCategory((candidate as any).jobCategory) !== 'General' && (
                                        <Badge className="text-[10px] px-1.5 py-0 bg-sky-50 text-sky-700 border border-sky-200 whitespace-nowrap">
                                          {(() => { const c = normalizeCategory((candidate as any).jobCategory); return c.length > 16 ? c.slice(0, 16) + '..' : c })()}
                                        </Badge>
                                      )}
                                    </div>
                                    {/* Details shown in preview panel on click */}
                                  </Card>
                                </motion.div>
                              )})})
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
                          blue: 'bg-sky-50 text-sky-700 border-sky-200',
                          green: 'bg-green-50 text-green-700 border-green-200',
                          yellow: 'bg-yellow-50 text-yellow-700 border-yellow-200',
                          purple: 'bg-sky-50 text-sky-700 border-sky-200',
                          indigo: 'bg-sky-50 text-sky-700 border-sky-200',
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
                    const split = splitContentByCandidates(message.content, message.candidates!)
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
                          className="text-xs text-gray-600 hover:text-sky-600 flex items-center gap-1"
                        >
                          {message.candidates!.every(c => selectedIds.has(c.id))
                            ? <CheckSquare className="w-3.5 h-3.5 text-sky-600" />
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
                          {/* Separator line between candidates */}
                          {idx > 0 && (
                            <div className="my-3 border-t-2 border-dashed border-gray-200" />
                          )}
                          <Card 
                            className={`hover:shadow-md transition-all border-2 overflow-hidden cursor-pointer ${
                              selectedIds.has(candidate.id) ? 'border-sky-400 bg-sky-50/30' : 'hover:border-sky-200'
                            }`}
                            onClick={() => handlePreviewCandidate(candidate)}
                          >
                            {/* Header: Checkbox + Rank + Avatar + Name + Score + Actions */}
                            <div className="flex items-center gap-2 px-3 py-2.5 bg-gradient-to-r from-gray-50 to-white border-b border-gray-100">
                              <button onClick={(e) => { e.stopPropagation(); toggleSelect(candidate.id) }} className="flex-shrink-0">
                                {selectedIds.has(candidate.id) ? <CheckSquare className="w-4 h-4 text-sky-600" /> : <Square className="w-4 h-4 text-gray-400 hover:text-gray-600" />}
                              </button>
                              <div className="w-7 h-7 flex-shrink-0 rounded-full bg-gradient-to-br from-sky-100 to-sky-200 flex items-center justify-center text-sky-700 font-bold text-xs">{idx + 1}</div>
                              <Avatar className="w-8 h-8 flex-shrink-0 border-2 border-white shadow">
                                <AvatarFallback className="text-xs font-semibold bg-gradient-to-br from-sky-100 to-sky-200 text-sky-700">{candidate.name.charAt(0)}</AvatarFallback>
                              </Avatar>
                              <div className="min-w-0 flex-1">
                                <h4 className="text-sm font-semibold text-gray-900 truncate hover:text-sky-600">{candidate.name}</h4>
                                <p className="text-xs text-gray-500 flex items-center gap-1 truncate"><MapPin className="w-3 h-3 flex-shrink-0" /><span className="truncate">{cleanLocation(candidate.location) || 'N/A'}</span></p>
                              </div>
                              <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
                                <p className={`text-lg font-bold ${getScoreColor(candidate.matchScore ?? 0)}`}>{(candidate.matchScore ?? 0).toFixed(0)}%</p>
                                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap ${getFitLabel(candidate.matchScore ?? 0).cls}`}>{getFitLabel(candidate.matchScore ?? 0).text}</span>
                                <motion.button whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} onClick={(e) => { e.stopPropagation(); handlePreviewCandidate(candidate) }} className="px-2 py-1 bg-sky-100 hover:bg-sky-200 text-sky-700 rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"><Eye className="w-3 h-3" />Preview</motion.button>
                                <motion.button whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} onClick={(e) => { e.stopPropagation(); navigate(`/candidates/${candidate.id}`) }} className="px-2 py-1 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"><ExternalLink className="w-3 h-3" />Open</motion.button>
                                {candidate.status !== 'Shortlisted' ? (
                                  <motion.button whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} onClick={async (e) => { e.stopPropagation(); try { await candidateApi.updateStatus(candidate.id, 'Shortlisted'); if (!isShortlisted(candidate.id)) toggleShortlist(candidate.id); setMessages(prev => [...prev, { id: Date.now().toString(), type: 'ai', content: `**${candidate.name}** has been shortlisted. Notification email queued.`, timestamp: new Date(), intent: 'shortlist_single' }]) } catch (err) { console.error('Shortlist error:', err) } }} className="px-2 py-1 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium flex items-center gap-1 whitespace-nowrap"><CheckCircle2 className="w-3 h-3" />Shortlist</motion.button>
                                ) : <Badge className="bg-green-100 text-green-700 text-xs whitespace-nowrap">Shortlisted ✓</Badge>}
                              </div>
                            </div>
                            {/* Skills row */}
                            <div className="flex items-center gap-1 flex-wrap px-3 py-1.5 bg-white">
                              {candidate.skills.slice(0, 4).map((skill: string) => (
                                <Badge key={skill} variant="outline" className="text-[10px] px-1.5 py-0 whitespace-nowrap">{skill.length > 14 ? skill.slice(0, 14) + '..' : skill}</Badge>
                              ))}
                              {(candidate as any).jobCategory && normalizeCategory((candidate as any).jobCategory) !== 'General' && (
                                <Badge className="text-[10px] px-1.5 py-0 bg-sky-50 text-sky-700 border border-sky-200 whitespace-nowrap">
                                  {(() => { const c = normalizeCategory((candidate as any).jobCategory); return c.length > 16 ? c.slice(0, 16) + '..' : c })()}
                                </Badge>
                              )}
                            </div>
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
                          primary: 'bg-slate-800 hover:bg-slate-700 text-white',
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
            <div className="w-10 h-10 bg-gradient-to-br from-slate-800 to-slate-700 rounded-full flex items-center justify-center shadow-md">
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
        {messages.length <= 1 && (
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
      )}

      {/* Input Area */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className={`flex-shrink-0 bg-white border-t border-gray-200 ${resultsView ? 'px-4 py-2' : 'px-6 py-4'}`}
      >
        <div className={resultsView ? '' : 'max-w-4xl mx-auto'}>
          <div className="flex gap-3">
            <div className="flex-1 relative">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value)
                  // Auto-resize
                  e.target.style.height = 'auto'
                  e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !isTyping) {
                    e.preventDefault()
                    handleSend()
                  }
                }}
                placeholder="Ask about ML ranking, duplicates, analytics, templates, scheduling... (Shift+Enter for new line)"
                className="w-full px-4 py-3 pr-12 border-2 border-gray-200 rounded-xl focus:outline-none focus:border-sky-400 transition-colors text-sm resize-none overflow-hidden"
                disabled={isTyping}
                rows={1}
                style={{ minHeight: '48px', maxHeight: '120px' }}
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
          {!resultsView && (
            <p className="text-xs text-gray-500 mt-2 text-center">
              ML Ranking · Job Matching · Predictive Analytics · Duplicates · Email Templates · Calendar · SMS
            </p>
          )}
        </div>
      </motion.div>

      </div>{/* /Chat Column */}

      {/* ── Candidate Preview Panel (right side) — Matching Shortlist Design ── */}
      <AnimatePresence>
        {previewCandidate && !resultsView && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 580, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
            className="flex-shrink-0 bg-white border-l border-gray-200 overflow-y-auto"
          >
            <div className="w-[580px] min-h-0 flex flex-col">

              {/* Hero Header */}
              <div className="bg-gradient-to-r from-slate-800 via-slate-700 to-teal-700 text-white p-5 relative flex-shrink-0">
                <button
                  onClick={() => { setPreviewCandidate(null); setPreviewAnalysis(null) }}
                  className="absolute top-3 right-3 p-1 hover:bg-white/20 rounded-md transition-colors"
                >
                  <X className="w-4 h-4 text-white/80" />
                </button>
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-3">
                    {(() => {
                      const colors = ['bg-teal-500', 'bg-sky-500', 'bg-purple-500', 'bg-amber-500', 'bg-rose-500', 'bg-emerald-500', 'bg-indigo-500', 'bg-orange-500']
                      const colorIdx = previewCandidate.name.charCodeAt(0) % colors.length
                      return (
                        <div className={`w-12 h-12 ${colors[colorIdx]} rounded-full flex items-center justify-center text-white text-lg font-bold ring-2 ring-white/30 flex-shrink-0`}>
                          {previewCandidate.name.charAt(0).toUpperCase()}{previewCandidate.name.split(' ')[1]?.charAt(0).toUpperCase() || ''}
                        </div>
                      )
                    })()}
                    <div>
                      <h2 className="text-lg font-bold">{previewCandidate.name}</h2>
                      <p className="text-teal-200 text-sm">
                        {normalizeCategory(previewCandidate.jobCategory) !== 'General' ? normalizeCategory(previewCandidate.jobCategory) : 'Candidate'}
                        {previewCandidate.experience > 0 ? ` · ${previewCandidate.experience}+ Years experience` : ''}
                      </p>
                      <div className="flex items-center gap-3 mt-1.5 text-sm text-slate-200">
                        {previewCandidate.email && (
                          <a href={`mailto:${previewCandidate.email}`} className="flex items-center gap-1 hover:text-white transition-colors">
                            <Mail className="w-3.5 h-3.5" />{previewCandidate.email}
                          </a>
                        )}
                        {previewCandidate.phone && (
                          <a href={`tel:${previewCandidate.phone}`} className="flex items-center gap-1 hover:text-white transition-colors">
                            <Phone className="w-3.5 h-3.5" />{previewCandidate.phone}
                          </a>
                        )}
                      </div>
                      {cleanLocation(previewCandidate.location) && (
                        <div className="flex items-center gap-1 mt-1 text-sm text-teal-200">
                          <MapPin className="w-3.5 h-3.5" />{cleanLocation(previewCandidate.location)}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <div className="text-3xl font-bold text-white">{Math.round(previewCandidate.matchScore ?? 0)}%</div>
                    <span className={`text-[11px] font-medium px-2 py-0.5 rounded mt-1 inline-block ${getFitLabel(previewCandidate.matchScore ?? 0).cls}`}>
                      {getFitLabel(previewCandidate.matchScore ?? 0).text}
                    </span>
                  </div>
                </div>
              </div>

              {/* Actions Bar */}
              <div className="flex items-center gap-2 px-5 py-3 border-b border-gray-100 bg-gray-50/50 flex-wrap">
                <Button size="sm" onClick={() => navigate(`/candidates/${previewCandidate.id}`)}>
                  <Eye className="w-3.5 h-3.5 mr-1.5" />Full Profile
                </Button>
                <Button size="sm" variant="outline" onClick={() => navigate(`/candidates/${previewCandidate.id}`)}>
                  <Calendar className="w-3.5 h-3.5 mr-1.5" />Schedule Interview
                </Button>
                <Button size="sm" variant="outline" onClick={() => generateQuickProfilePDF(previewCandidate)} className="text-xs">
                  <Download className="w-3.5 h-3.5 mr-1.5" />PDF Report
                </Button>
                <Button size="sm" variant="outline" onClick={async () => {
                  try { await downloadOriginalResume(previewCandidate) } catch { toast.error('Download failed', 'No resume available for this candidate') }
                }} className="text-xs">
                  <FileDown className="w-3.5 h-3.5 mr-1.5" />Resume
                </Button>
              </div>

              {/* Section Nav Bar */}
              <div className="flex items-center gap-0.5 px-3 py-1.5 border-b border-gray-200 bg-gradient-to-r from-gray-50 to-white sticky top-0 z-10 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
                {[
                  { id: 'panel-analysis', label: 'Analysis', icon: Sparkles },
                  { id: 'panel-skills', label: 'Skills', icon: Zap },
                  { id: 'panel-resume', label: 'Resume', icon: FileText },
                  { id: 'panel-experience', label: 'Experience', icon: Briefcase },
                  { id: 'panel-education', label: 'Education', icon: Award },
                  { id: 'panel-contact', label: 'Contact', icon: Mail },
                ].map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    onClick={() => {
                      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    }}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-600 hover:text-teal-700 hover:bg-teal-50 border border-transparent hover:border-teal-200 transition-all whitespace-nowrap flex-shrink-0"
                  >
                    <Icon className="w-3.5 h-3.5" />{label}
                  </button>
                ))}
              </div>

              {previewLoading && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-5 h-5 animate-spin text-teal-500" />
                  <span className="text-xs text-gray-500 ml-2">Loading details...</span>
                </div>
              )}

              <div className="p-5 space-y-5">

                {/* AI Analysis (matching Shortlist gradient box) */}
                {previewAnalysis && (
                  <section id="panel-analysis">
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-3">
                      <Sparkles className="w-4 h-4 text-teal-500" />AI Analysis
                    </h3>
                    <div className="bg-gradient-to-br from-teal-50/50 to-slate-50/50 rounded-xl p-4 border border-teal-100 space-y-3">
                      <p className="text-sm text-gray-700 leading-relaxed">
                        {previewAnalysis.executive_summary || previewCandidate.summary || 'No AI analysis available yet.'}
                      </p>

                      {/* Technical & Experience Assessments */}
                      {previewAnalysis.technical_assessment && (
                        <div>
                          <h4 className="text-xs font-semibold text-gray-600 mb-1 flex items-center gap-1"><Zap className="w-3 h-3 text-teal-500" />Technical Assessment</h4>
                          <p className="text-xs text-gray-600 leading-relaxed">{previewAnalysis.technical_assessment}</p>
                        </div>
                      )}
                      {previewAnalysis.experience_assessment && (
                        <div>
                          <h4 className="text-xs font-semibold text-gray-600 mb-1 flex items-center gap-1"><Briefcase className="w-3 h-3 text-teal-500" />Experience Assessment</h4>
                          <p className="text-xs text-gray-600 leading-relaxed">{previewAnalysis.experience_assessment}</p>
                        </div>
                      )}

                      {/* Pros & Cons */}
                      {((previewAnalysis.pros?.length ?? 0) > 0 || (previewAnalysis.cons?.length ?? 0) > 0) && (
                        <div className="grid grid-cols-2 gap-3">
                          {(previewAnalysis.pros?.length ?? 0) > 0 && (
                            <div>
                              <h4 className="text-xs font-semibold text-green-700 mb-1 flex items-center gap-1"><CheckCircle2 className="w-3 h-3" />Strengths</h4>
                              <ul className="space-y-1">
                                {previewAnalysis.pros!.map((p: string, i: number) => (
                                  <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                    <span className="w-1.5 h-1.5 bg-green-400 rounded-full mt-1 flex-shrink-0" />{p}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                          {(previewAnalysis.cons?.length ?? 0) > 0 && (
                            <div>
                              <h4 className="text-xs font-semibold text-amber-700 mb-1 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />Areas to Explore</h4>
                              <ul className="space-y-1">
                                {previewAnalysis.cons!.map((c: string, i: number) => (
                                  <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                    <span className="w-1.5 h-1.5 bg-amber-400 rounded-full mt-1 flex-shrink-0" />{c}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Interview Focus Areas */}
                      {(previewAnalysis.interview_focus_areas?.length ?? 0) > 0 && (
                        <div>
                          <h4 className="text-xs font-semibold text-gray-600 mb-1 flex items-center gap-1"><Target className="w-3 h-3 text-teal-500" />Interview Focus</h4>
                          <div className="flex flex-wrap gap-1.5">
                            {previewAnalysis.interview_focus_areas!.map((area: string, i: number) => (
                              <span key={i} className="text-[10px] px-2 py-0.5 bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-full">{area}</span>
                            ))}
                          </div>
                        </div>
                      )}

                      {(previewAnalysis.overall_rating || previewAnalysis.hiring_recommendation) && (
                        <div className="flex items-center gap-2 pt-2 border-t border-teal-100">
                          {previewAnalysis.overall_rating && (
                            <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                              previewAnalysis.overall_rating?.startsWith('A') ? 'bg-emerald-100 text-emerald-700' :
                              previewAnalysis.overall_rating?.startsWith('B') ? 'bg-blue-100 text-blue-700' :
                              'bg-amber-100 text-amber-700'
                            }`}>{previewAnalysis.overall_rating}</span>
                          )}
                          {previewAnalysis.hiring_recommendation && (
                            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${
                              previewAnalysis.hiring_recommendation === 'STRONGLY_RECOMMEND' ? 'bg-emerald-600 text-white' :
                              previewAnalysis.hiring_recommendation === 'RECOMMEND' ? 'bg-emerald-500 text-white' :
                              previewAnalysis.hiring_recommendation === 'CONSIDER' ? 'bg-amber-500 text-white' :
                              'bg-red-500 text-white'
                            }`}>{previewAnalysis.hiring_recommendation.replace('_', ' ')}</span>
                          )}
                          {previewAnalysis.confidence_score && (
                            <span className="text-[10px] text-gray-400">Confidence: {previewAnalysis.confidence_score}%</span>
                          )}
                        </div>
                      )}
                    </div>
                  </section>
                )}

                {!previewAnalysis && previewCandidate.summary && (
                  <section id="panel-analysis">
                    <h3 className="text-sm font-semibold text-gray-900 mb-2">Professional Summary</h3>
                    <p className="text-sm text-gray-600 leading-relaxed bg-gray-50 rounded-lg p-4">{previewCandidate.summary}</p>
                  </section>
                )}

                {/* Skills (matching Shortlist indigo style) */}
                {previewCandidate.skills.length > 0 && (
                  <section id="panel-skills">
                    <h3 className="text-sm font-semibold text-gray-900 mb-2">Skills</h3>
                    <div className="flex flex-wrap gap-1.5">
                      {previewCandidate.skills.map((skill: string) => (
                        <Badge key={skill} variant="outline" className="text-xs bg-teal-50/50 text-teal-700 border-teal-200">
                          {skill}
                        </Badge>
                      ))}
                    </div>
                  </section>
                )}

                {/* Resume Highlights — skip garbled text */}
                {previewCandidate.resumeText && !isTextGarbled(previewCandidate.resumeText) ? (
                  <section id="panel-resume">
                    <h3 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-1.5">
                      <FileText className="w-4 h-4 text-teal-400" />Resume Highlights
                    </h3>
                    <div className="text-sm text-gray-600 leading-relaxed bg-teal-50/30 rounded-lg p-4 border border-teal-100 max-h-48 overflow-y-auto whitespace-pre-line">
                      {previewCandidate.resumeText.substring(0, 1000)}{previewCandidate.resumeText.length > 1000 ? '...' : ''}
                    </div>
                  </section>
                ) : previewCandidate.resumeText ? (
                  <section id="panel-resume">
                    <h3 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-1.5">
                      <FileText className="w-4 h-4 text-teal-400" />Resume
                    </h3>
                    <div className="text-sm text-gray-500 italic bg-gray-50 rounded-lg p-4 border border-gray-200 flex items-center gap-2">
                      <FileDown className="w-4 h-4 text-gray-400" />
                      Resume text could not be displayed. Download the original file instead.
                    </div>
                  </section>
                ) : null}

                {/* Work History (matching Shortlist) */}
                {previewCandidate.workHistory && previewCandidate.workHistory.length > 0 && (
                  <section id="panel-experience">
                    <h3 className="text-sm font-semibold text-gray-900 mb-2">Experience</h3>
                    <div className="space-y-3">
                      {previewCandidate.workHistory.slice(0, 5).map((job, i) => (
                        <div key={i} className="border-l-2 border-teal-200 pl-3 py-0.5">
                          <p className="text-sm font-medium text-gray-900">{job.title}</p>
                          <p className="text-xs text-gray-500">{job.company}{job.duration && ` · ${job.duration}`}</p>
                          {job.description && <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{job.description}</p>}
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {/* Education */}
                {previewCandidate.education && previewCandidate.education.length > 0 && (
                  <section id="panel-education">
                    <h3 className="text-sm font-semibold text-gray-900 mb-2">Education</h3>
                    <div className="space-y-2">
                      {previewCandidate.education.map((edu, i) => (
                        <div key={i} className="flex items-start gap-2">
                          <Award className="w-4 h-4 text-teal-400 mt-0.5 flex-shrink-0" />
                          <div>
                            <p className="text-sm font-medium text-gray-900">{edu.degree}{edu.field && ` in ${edu.field}`}</p>
                            <p className="text-xs text-gray-500">{edu.institution}{edu.year && ` · ${edu.year}`}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {/* Contact */}
                <section id="panel-contact">
                  <h3 className="text-sm font-semibold text-gray-900 mb-2">Contact</h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {previewCandidate.email && (
                      <a href={`mailto:${previewCandidate.email}`} className="flex items-center gap-2 text-sm text-gray-600 hover:text-teal-600 p-2 rounded-lg hover:bg-teal-50 transition-colors">
                        <Mail className="w-4 h-4 text-gray-400" />{previewCandidate.email}
                      </a>
                    )}
                    {previewCandidate.phone && (
                      <a href={`tel:${previewCandidate.phone}`} className="flex items-center gap-2 text-sm text-gray-600 hover:text-teal-600 p-2 rounded-lg hover:bg-teal-50 transition-colors">
                        <Phone className="w-4 h-4 text-gray-400" />{previewCandidate.phone}
                      </a>
                    )}
                    {previewCandidate.linkedin && (
                      <a href={previewCandidate.linkedin} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 text-sm text-gray-600 hover:text-teal-600 p-2 rounded-lg hover:bg-teal-50 transition-colors">
                        <Linkedin className="w-4 h-4 text-gray-400" />LinkedIn
                        <ExternalLink className="w-3 h-3 ml-auto text-gray-400" />
                      </a>
                    )}
                    {previewCandidate.location && cleanLocation(previewCandidate.location) && (
                      <div className="flex items-center gap-2 text-sm text-gray-600 p-2">
                        <MapPin className="w-4 h-4 text-gray-400" />{cleanLocation(previewCandidate.location)}
                      </div>
                    )}
                  </div>
                </section>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      </div>{/* /Main Content Area */}

      {/* Job Description Matching Modal */}
      <JobMatchModal
        isOpen={showJobMatchModal}
        onClose={() => setShowJobMatchModal(false)}
        onMatch={handleJobMatch}
      />
    </div>
  )
}