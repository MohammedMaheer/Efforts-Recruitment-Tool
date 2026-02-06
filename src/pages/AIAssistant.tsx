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
  Copy
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Avatar, AvatarFallback } from '@/components/ui/Avatar'
import { useCandidates } from '@/hooks/useCandidates'
import type { Candidate } from '@/store/candidateStore'
import { useNavigate } from 'react-router-dom'
import { getMatchScoreColor } from '@/lib/utils'
import { useAIStatus } from '@/hooks/useAIStatus'
import { advancedApi } from '@/services/api'
import config from '@/config'

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
    color: 'text-purple-600',
    bgColor: 'bg-purple-50 hover:bg-purple-100',
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
    icon: Mail,
    text: 'Draft outreach email for top candidates',
    color: 'text-indigo-600',
    bgColor: 'bg-indigo-50 hover:bg-indigo-100',
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

export default function AIAssistant() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const { candidates } = useCandidates({ autoFetch: true })
  const navigate = useNavigate()
  const aiStatus = useAIStatus()

  useEffect(() => {
    // Welcome message with enhanced capabilities
    const welcomeMessage: Message = {
      id: '0',
      type: 'ai',
      content: `👋 Hi! I'm your **AI Recruitment Assistant** powered by advanced ML features.

${candidates.length > 0 ? `📊 I can see **${candidates.length} candidates** in your database.` : ''}

**Here's what I can help you with:**
• 🧠 **ML Ranking** - Intelligently rank candidates for any role
• 🎯 **Job Matching** - Find best fits for open positions
• 📈 **Predictive Analytics** - Forecast hiring success
• 🔍 **Duplicate Detection** - Clean up your candidate pool
• ✉️ **Email Templates** - Draft professional outreach
• 📅 **Calendar** - Schedule interviews
• 📱 **SMS Notifications** - Send quick updates

Try one of the suggestions below or ask me anything!`,
      timestamp: new Date(),
      insights: candidates.length > 0 ? [
        {
          title: 'Total Candidates',
          value: candidates.length,
          icon: Users,
          color: 'blue'
        },
        {
          title: 'Avg Score',
          value: `${(candidates.reduce((acc, c) => acc + (c.matchScore || 0), 0) / candidates.length).toFixed(0)}%`,
          icon: TrendingUp,
          color: 'green'
        },
        {
          title: 'Strong Matches',
          value: candidates.filter(c => c.status === 'Strong').length,
          icon: Star,
          color: 'yellow'
        },
        {
          title: 'New Today',
          value: candidates.filter(c => {
            const today = new Date().toDateString()
            return new Date(c.appliedDate).toDateString() === today
          }).length,
          icon: Clock,
          color: 'purple'
        }
      ] : undefined
    }
    setMessages([welcomeMessage])
  }, [candidates.length])

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
        const result = await advancedApi.ml.rankCandidates(candidateIds, undefined, 10)
        
        if (result.data?.rankings) {
          const rankedIds = result.data.rankings.map((r: { candidate_id: string }) => r.candidate_id)
          filteredCandidates = rankedIds
            .map((id: string) => candidates.find(c => c.id === id))
            .filter(Boolean) as Candidate[]
          
          response = `🧠 **ML-Powered Ranking Complete!**\n\nI've analyzed ${candidateIds.length} candidates using machine learning. Here are the top matches ranked by predicted success:`
          
          insights = [
            { title: 'Analyzed', value: candidateIds.length, icon: Brain, color: 'purple' },
            { title: 'Top Score', value: `${(result.data.rankings[0]?.score * 100 || 0).toFixed(0)}%`, icon: Star, color: 'yellow' },
            { title: 'Avg Score', value: `${(result.data.rankings.reduce((a: number, r: { score: number }) => a + r.score, 0) / result.data.rankings.length * 100).toFixed(0)}%`, icon: TrendingUp, color: 'green' }
          ]
        }
      } catch (error) {
        console.error('ML ranking error:', error)
        // Fallback to score-based ranking
        filteredCandidates = filteredCandidates.sort((a, b) => b.matchScore - a.matchScore).slice(0, 10)
        response = `📊 Here are the top candidates ranked by match score (ML service unavailable):`
      }
      
      actions = [
        { label: 'Email Top 5', icon: Mail, action: () => navigate('/templates'), variant: 'primary' },
        { label: 'Schedule Interviews', icon: Calendar, action: () => navigate('/campaigns'), variant: 'secondary' }
      ]
    }
    
    // PREDICTIVE ANALYTICS
    else if (lowerQuery.includes('predict') || lowerQuery.includes('analytics') || lowerQuery.includes('forecast')) {
      intent = 'predictive_analytics'
      try {
        const topCandidates = candidates.sort((a, b) => b.matchScore - a.matchScore).slice(0, 5)
        const predictions = await Promise.all(
          topCandidates.map(c => advancedApi.analytics.predict(c.id).catch(() => null))
        )
        
        filteredCandidates = topCandidates
        response = `📈 **Predictive Analytics Report**\n\nI've analyzed your top candidates to predict hiring outcomes:`
        
        const avgProbability = predictions.filter(Boolean).reduce((acc, p) => acc + ((p as { data?: { probability?: number } })?.data?.probability || 0.5), 0) / predictions.length
        
        insights = [
          { title: 'Candidates Analyzed', value: topCandidates.length, icon: Users, color: 'blue' },
          { title: 'Avg Hiring Probability', value: `${(avgProbability * 100).toFixed(0)}%`, icon: Target, color: 'green' },
          { title: 'High Potential', value: predictions.filter((p) => ((p as { data?: { probability?: number } })?.data?.probability || 0) > 0.7).length, icon: Star, color: 'yellow' }
        ]
      } catch (error) {
        response = `📈 **Quick Analytics Summary:**\n\n• Total Candidates: ${candidates.length}\n• Strong Matches: ${candidates.filter(c => c.status === 'Strong').length}\n• Average Score: ${(candidates.reduce((acc, c) => acc + c.matchScore, 0) / candidates.length).toFixed(1)}%`
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
          filteredCandidates = candidates.filter((c, idx) => {
            const result = duplicateResults[idx] as { data?: { duplicates?: unknown[] } } | null
            return result?.data?.duplicates && result.data.duplicates.length > 0
          })
          
          response = `🔍 **Duplicate Detection Results**\n\nI found **${duplicatesFound} candidates** with potential duplicates that may need attention:`
          
          insights = [
            { title: 'Candidates Checked', value: Math.min(20, candidates.length), icon: Search, color: 'blue' },
            { title: 'With Duplicates', value: duplicatesFound, icon: Copy, color: 'orange' },
            { title: 'Clean Records', value: Math.min(20, candidates.length) - duplicatesFound, icon: CheckCircle2, color: 'green' }
          ]
        } else {
          response = `✅ **No Duplicates Found!**\n\nYour candidate database is clean. No duplicate entries detected.`
          filteredCandidates = []
        }
      } catch (error) {
        response = `🔍 Checking for duplicates... (Service temporarily unavailable)`
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
      
      response = `✉️ **Email Outreach Ready!**\n\nI've identified **${filteredCandidates.length} candidates** perfect for outreach. You can use our pre-built templates or create custom ones:`
      
      actions = [
        { label: 'Browse Templates', icon: FileText, action: () => navigate('/templates'), variant: 'primary' },
        { label: 'Create Campaign', icon: Mail, action: () => navigate('/campaigns'), variant: 'secondary' },
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
      
      response = `📅 **Interview Scheduling**\n\nI found **${filteredCandidates.length} candidates** ready for interviews. You can schedule through our calendar integration:`
      
      actions = [
        { label: 'Open Calendar', icon: Calendar, action: () => navigate('/campaigns'), variant: 'primary' },
        { label: 'Bulk Schedule', icon: Users, action: () => {}, variant: 'secondary' }
      ]
    }
    
    // SMS / NOTIFICATIONS  
    else if (lowerQuery.includes('sms') || lowerQuery.includes('text') || lowerQuery.includes('notify') || lowerQuery.includes('message')) {
      intent = 'sms'
      filteredCandidates = candidates.filter(c => c.phone).slice(0, 5)
      
      response = `📱 **SMS Notifications**\n\n**${filteredCandidates.length} candidates** have phone numbers available for SMS outreach:`
      
      actions = [
        { label: 'Send Bulk SMS', icon: MessageSquare, action: () => {}, variant: 'primary' },
        { label: 'View Templates', icon: FileText, action: () => navigate('/templates'), variant: 'secondary' }
      ]
    }
    
    // RESUME QUALITY
    else if (lowerQuery.includes('quality') || lowerQuery.includes('resume quality') || lowerQuery.includes('analyze resume')) {
      intent = 'resume_quality'
      filteredCandidates = candidates.sort((a, b) => b.matchScore - a.matchScore).slice(0, 10)
      
      const highQuality = filteredCandidates.filter(c => c.matchScore >= 70).length
      const mediumQuality = filteredCandidates.filter(c => c.matchScore >= 50 && c.matchScore < 70).length
      const lowQuality = filteredCandidates.filter(c => c.matchScore < 50).length
      
      response = `📋 **Resume Quality Analysis**\n\nHere's a breakdown of your candidate pool quality:`
      
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
      filteredCandidates = candidates.sort((a, b) => b.matchScore - a.matchScore).slice(0, 10)
      response = `🎯 **Job Matching Results**\n\nTop candidates matched to your open positions:`
      
      actions = [
        { label: 'View Jobs', icon: Target, action: () => navigate('/jobs'), variant: 'primary' },
        { label: 'Create Job', icon: FileText, action: () => navigate('/jobs'), variant: 'secondary' }
      ]
    }

    // Match score filtering
    else if (lowerQuery.includes('top') || lowerQuery.includes('best') || lowerQuery.match(/\d+%?\+?\s*(match|score)/)) {
      const scoreMatch = lowerQuery.match(/(\d+)%?/)
      const minScore = scoreMatch ? parseInt(scoreMatch[1]) : 70
      filteredCandidates = filteredCandidates.filter(c => c.matchScore >= minScore)
      intent = 'top_candidates'
      response = `⭐ Found **${filteredCandidates.length} candidate${filteredCandidates.length !== 1 ? 's' : ''}** with ${minScore}%+ match score:`
      
      actions = [
        { label: 'Email All', icon: Mail, action: () => navigate('/templates'), variant: 'primary' },
        { label: 'Shortlist All', icon: Star, action: () => {}, variant: 'secondary' }
      ]
    }
    
    // Status filtering
    else if (lowerQuery.includes('strong match') || lowerQuery.includes('strong candidate')) {
      filteredCandidates = filteredCandidates.filter(c => c.status === 'Strong')
      intent = 'strong_matches'
      response = `💪 Here are **${filteredCandidates.length} strong match** candidate${filteredCandidates.length !== 1 ? 's' : ''}:`
    }
    
    // Recent/new candidates
    else if (lowerQuery.includes('recent') || lowerQuery.includes('new') || lowerQuery.includes('latest') || lowerQuery.includes('today')) {
      filteredCandidates = filteredCandidates
        .sort((a, b) => new Date(b.appliedDate).getTime() - new Date(a.appliedDate).getTime())
        .slice(0, 10)
      intent = 'recent'
      response = `🕐 Here are the **${filteredCandidates.length} most recent** applicants:`
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
        response = `🔧 Found **${filteredCandidates.length} candidate${filteredCandidates.length !== 1 ? 's' : ''}** with **${foundSkills.join(', ')}** skills:`
      }
    }
    
    // Location-based search
    else if (lowerQuery.includes('in ') || lowerQuery.includes('from ') || lowerQuery.includes('location')) {
      const cities = ['dubai', 'abu dhabi', 'sharjah', 'ajman', 'ras al khaimah', 'fujairah', 'umm al quwain', 'remote', 'mumbai', 'bangalore', 'delhi', 'chennai', 'hyderabad', 'india', 'pakistan', 'uae']
      const foundCity = cities.find(city => lowerQuery.includes(city))
      
      if (foundCity) {
        filteredCandidates = filteredCandidates.filter(c => 
          c.location.toLowerCase().includes(foundCity)
        )
        intent = 'location_search'
        response = `📍 Found **${filteredCandidates.length} candidate${filteredCandidates.length !== 1 ? 's' : ''}** in **${foundCity}**:`
      }
    }
    
    // Shortlist
    else if (lowerQuery.includes('shortlist') || lowerQuery.includes('favorite') || lowerQuery.includes('saved')) {
      filteredCandidates = filteredCandidates.filter(c => c.isShortlisted)
      intent = 'shortlist'
      response = `⭐ Your shortlist has **${filteredCandidates.length} candidate${filteredCandidates.length !== 1 ? 's' : ''}**:`
      
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
        response = `🔍 Found **${filteredCandidates.length} result${filteredCandidates.length !== 1 ? 's' : ''}** for "${query}":`
      } else {
        filteredCandidates = filteredCandidates.slice(0, 10)
        intent = 'show_all'
        response = `📋 Here are the first **${filteredCandidates.length} candidates** in your pipeline:`
      }
    }

    // Sort by match score
    filteredCandidates.sort((a, b) => b.matchScore - a.matchScore)

    if (filteredCandidates.length === 0 && !response.includes('No Duplicates') && !response.includes('Analytics')) {
      response = "😕 I couldn't find any candidates matching that criteria. Try:\n\n• Different keywords or skills\n• Broader search terms\n• Check spelling"
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
      // Try OpenAI first (preferred)
      const response = await fetch(`${config.endpoints.ai}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userInput,
          context: JSON.stringify({
            totalCandidates: candidates.length,
            availableSkills: [...new Set(candidates.flatMap(c => c.skills))].slice(0, 30),
            locations: [...new Set(candidates.map(c => c.location))].slice(0, 10),
            avgMatchScore: candidates.reduce((acc, c) => acc + c.matchScore, 0) / candidates.length || 0,
            strongMatches: candidates.filter(c => c.status === 'Strong').length,
            recentCount: candidates.filter(c => {
              const weekAgo = new Date()
              weekAgo.setDate(weekAgo.getDate() - 7)
              return new Date(c.appliedDate) >= weekAgo
            }).length
          })
        })
      })

      if (response.ok) {
        const data = await response.json()
        
        // Parse candidates from local data based on query
        const { candidates: foundCandidates, actions, insights } = await parseQuery(userInput)
        
        // Remove loading and add real message
        setMessages(prev => prev.filter(m => m.id !== loadingId))
        
        const aiMessage: Message = {
          id: (Date.now() + 2).toString(),
          type: 'ai',
          content: data.response,
          timestamp: new Date(),
          candidates: foundCandidates,
          intent: 'ai_response',
          actions,
          insights
        }

        setMessages(prev => [...prev, aiMessage])
      } else {
        throw new Error('OpenAI API unavailable')
      }
    } catch (error) {
      // Fallback to local NLP
      console.warn('OpenAI unavailable, using local NLP fallback:', error)
      
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

  const handleSuggestedPrompt = (prompt: string) => {
    setInput(prompt)
    inputRef.current?.focus()
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="h-full flex flex-col bg-gradient-to-br from-primary-50/30 via-white to-purple-50/30">
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
              className="w-12 h-12 bg-gradient-to-br from-primary-500 to-purple-600 rounded-2xl flex items-center justify-center shadow-lg"
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
            <Badge variant="outline" className="flex items-center gap-1 bg-purple-50 text-purple-700 border-purple-200">
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
          {messages.map((message, index) => (
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
                    : 'bg-gradient-to-br from-primary-500 to-purple-600'
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
                    <div className="text-sm leading-relaxed whitespace-pre-wrap" 
                      dangerouslySetInnerHTML={{ 
                        __html: message.content
                          .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                          .replace(/\n/g, '<br/>') 
                      }} 
                    />
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
                          purple: 'bg-purple-50 text-purple-700 border-purple-200',
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
                  
                  {/* Candidate Results */}
                  {message.candidates && message.candidates.length > 0 && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      transition={{ delay: 0.3 }}
                      className="mt-4 space-y-3"
                    >
                      {message.candidates.map((candidate, idx) => (
                        <motion.div
                          key={candidate.id}
                          initial={{ opacity: 0, x: -20 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: 0.4 + idx * 0.1 }}
                          whileHover={{ scale: 1.02, x: 4 }}
                        >
                          <Card 
                            className="cursor-pointer hover:shadow-md transition-all border-2 hover:border-primary-200"
                            onClick={() => navigate(`/candidates/${candidate.id}`)}
                          >
                            <CardContent className="p-3">
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                  <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-100 to-purple-100 flex items-center justify-center text-primary-700 font-bold text-sm">
                                    {idx + 1}
                                  </div>
                                  <Avatar className="w-9 h-9 border-2 border-white shadow">
                                    <AvatarFallback className="text-sm font-semibold bg-gradient-to-br from-primary-100 to-purple-100 text-primary-700">
                                      {candidate.name.charAt(0)}
                                    </AvatarFallback>
                                  </Avatar>
                                  <div className="min-w-0">
                                    <h4 className="font-semibold text-gray-900 text-sm truncate">{candidate.name}</h4>
                                    <p className="text-xs text-gray-500 flex items-center gap-1">
                                      <MapPin className="w-3 h-3" />
                                      {candidate.location}
                                    </p>
                                  </div>
                                </div>
                                <div className="flex items-center gap-3">
                                  <div className="flex gap-1">
                                    {candidate.skills.slice(0, 2).map((skill: string) => (
                                      <Badge key={skill} variant="outline" className="text-xs px-1.5 py-0">
                                        {skill.length > 10 ? skill.slice(0, 10) + '..' : skill}
                                      </Badge>
                                    ))}
                                  </div>
                                  <div className="text-right">
                                    <p className={`text-lg font-bold ${getMatchScoreColor(candidate.matchScore)}`}>
                                      {(candidate.matchScore ?? 50).toFixed(0)}%
                                    </p>
                                  </div>
                                  <Badge
                                    variant={
                                      candidate.status === 'Strong'
                                        ? 'success'
                                        : candidate.status === 'Partial'
                                        ? 'warning'
                                        : 'danger'
                                    }
                                    className="text-xs"
                                  >
                                    {candidate.status}
                                  </Badge>
                                </div>
                              </div>
                            </CardContent>
                          </Card>
                        </motion.div>
                      ))}
                    </motion.div>
                  )}
                  
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
            <div className="w-10 h-10 bg-gradient-to-br from-primary-500 to-purple-600 rounded-full flex items-center justify-center shadow-md">
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
                    onClick={() => handleSuggestedPrompt(prompt.text)}
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
                onKeyPress={handleKeyPress}
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
            🧠 ML Ranking • 🎯 Job Matching • 📈 Predictive Analytics • 🔍 Duplicates • ✉️ Email Templates • 📅 Calendar • 📱 SMS
          </p>
        </div>
      </motion.div>
    </div>
  )
}