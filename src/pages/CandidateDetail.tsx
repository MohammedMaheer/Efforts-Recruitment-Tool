import { useParams, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { useState, useEffect, useMemo, useCallback } from 'react'
import {
  ArrowLeft,
  Download,
  FileDown,
  Star,
  Mail,
  Phone,
  MapPin,
  Briefcase,
  GraduationCap,
  CheckCircle,
  XCircle,
  Sparkles,
  Loader2,
  TrendingUp,
  AlertCircle,
  Tag,
  Linkedin,
  ExternalLink,
  MessageCircle,
  Award,
  Globe,
  Calendar,
  XOctagon,
} from 'lucide-react'
import { useCandidates } from '@/hooks/useCandidates'
import { useCandidateStore } from '@/store/candidateStore'
import { useNotificationStore } from '@/store/notificationStore'
import { candidateApi } from '@/services/api'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Avatar, AvatarFallback } from '@/components/ui/Avatar'
import { Progress } from '@/components/ui/Progress'
import { getMatchScoreColor } from '@/lib/utils'
import config from '@/config'
import { authFetch } from '@/lib/authFetch'
import { generateCandidatePDF, downloadOriginalResume } from '@/lib/pdfGenerator'
import { isTextGarbled } from '@/lib/textUtils'
import { toast } from '@/components/ui/Toast'

// Category colors for visual distinction
const categoryColors: Record<string, { bg: string; text: string; border: string }> = {
  'Software Engineer': { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  'DevOps Engineer': { bg: 'bg-cyan-50', text: 'text-cyan-700', border: 'border-cyan-200' },
  'Data Scientist': { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
  'Cybersecurity': { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-200' },
  'QA / Testing': { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' },
  'IT & Systems': { bg: 'bg-slate-50', text: 'text-slate-700', border: 'border-slate-200' },
  'Marketing': { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200' },
  'Sales': { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
  'Product Manager': { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' },
  'Project Management': { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200' },
  'Business Analyst': { bg: 'bg-sky-50', text: 'text-sky-700', border: 'border-sky-200' },
  'Consulting': { bg: 'bg-fuchsia-50', text: 'text-fuchsia-700', border: 'border-fuchsia-200' },
  'HR': { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200' },
  'Finance': { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' },
  'Legal': { bg: 'bg-stone-50', text: 'text-stone-700', border: 'border-stone-200' },
  'Operations': { bg: 'bg-zinc-50', text: 'text-zinc-700', border: 'border-zinc-200' },
  'Customer Support': { bg: 'bg-cyan-50', text: 'text-cyan-700', border: 'border-cyan-200' },
  'Design': { bg: 'bg-rose-50', text: 'text-rose-700', border: 'border-rose-200' },
  'Content & Communications': { bg: 'bg-lime-50', text: 'text-lime-700', border: 'border-lime-200' },
  'Healthcare': { bg: 'bg-red-50', text: 'text-red-600', border: 'border-red-200' },
  'Education': { bg: 'bg-blue-50', text: 'text-blue-600', border: 'border-blue-200' },
  'Engineering': { bg: 'bg-yellow-50', text: 'text-yellow-700', border: 'border-yellow-200' },
  'Media & Creative': { bg: 'bg-pink-50', text: 'text-pink-600', border: 'border-pink-200' },
  'Real Estate': { bg: 'bg-amber-50', text: 'text-amber-600', border: 'border-amber-200' },
  'Hospitality': { bg: 'bg-orange-50', text: 'text-orange-600', border: 'border-orange-200' },
  'General': { bg: 'bg-gray-50', text: 'text-gray-700', border: 'border-gray-200' },
}

const getCategoryColor = (category: string) => {
  return categoryColors[category] || categoryColors['General']
}

export default function CandidateDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { candidates, loading } = useCandidates({ autoFetch: true })
  const isShortlisted = useCandidateStore((state) => state.isShortlisted)
  const toggleShortlist = useCandidateStore((state) => state.toggleShortlist)
  const addNotification = useNotificationStore((state) => state.addNotification)

  const [aiAnalysis, setAiAnalysis] = useState<Record<string, any> | null>(null)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [isShortlisting, setIsShortlisting] = useState(false)
  const [fullCandidateData, setFullCandidateData] = useState<Record<string, any> | null>(null)
  const [fullDataLoading, setFullDataLoading] = useState(true)
  const [showCalendarPicker, setShowCalendarPicker] = useState(false)
  const [interviewDate, setInterviewDate] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() + 3); return d.toISOString().slice(0, 16)
  })

  const lightCandidate = useMemo(() => candidates.find((c) => c.id === id), [candidates, id])

  // Fetch full candidate data (light endpoint omits workHistory/education/summary)
  useEffect(() => {
    if (id) {
      setFullDataLoading(true)
      authFetch(`${config.endpoints.candidates}/${id}`)
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data) setFullCandidateData(data) })
        .catch((err) => console.error('Full candidate fetch failed:', err))
        .finally(() => setFullDataLoading(false))
    }
  }, [id])

  // Merge full data over light store data — works even when lightCandidate is null (direct URL nav)
  const candidate = (() => {
    const base = lightCandidate || (fullCandidateData ? {
      id: fullCandidateData.id,
      name: fullCandidateData.name || 'Unknown',
      email: fullCandidateData.email || '',
      phone: fullCandidateData.phone || '',
      location: fullCandidateData.location || '',
      skills: fullCandidateData.skills || [],
      experience: fullCandidateData.experience || 0,
      matchScore: fullCandidateData.matchScore || 50,
      status: fullCandidateData.status || 'New',
      jobCategory: fullCandidateData.jobCategory || fullCandidateData.job_category || 'General',
      jobSubcategory: fullCandidateData.jobSubcategory || fullCandidateData.job_subcategory || '',
      appliedDate: fullCandidateData.appliedDate || fullCandidateData.applied_date || '',
      linkedin: fullCandidateData.linkedin || '',
      isShortlisted: false,
      hasResume: fullCandidateData.hasResume || false,
      summary: '',
      education: [],
      workHistory: [],
      resumeText: '',
      certifications: [],
      languages: [],
    } as any : null)
    if (!base) return null
    return {
      ...base,
      ...(fullCandidateData ? {
        summary: fullCandidateData.summary || base.summary || '',
        workHistory: (fullCandidateData.workHistory || []).map((job: any) => ({
          title: job.title || job.position || '',
          company: job.company || job.organization || '',
          duration: job.duration || job.period || job.years || '',
          description: job.description || job.responsibilities || '',
        })),
        education: (fullCandidateData.education || []).map((edu: any) => ({
          degree: edu.degree || edu.title || '',
          field: edu.field || '',
          institution: edu.institution || edu.school || '',
          year: edu.year || edu.graduation_year || '',
        })),
        resumeText: fullCandidateData.resume_text || fullCandidateData.resumeText || base.resumeText || '',
        certifications: fullCandidateData.certifications || base.certifications || [],
        languages: fullCandidateData.languages || base.languages || [],
        aiAnalysis: fullCandidateData.ai_analysis || fullCandidateData.aiAnalysis || base.aiAnalysis || null,
      } : {}),
    }
  })()

  const handleAIAnalysis = useCallback(async () => {
    if (!candidate) return
    
    setIsAnalyzing(true)
    setAnalysisError(null)
    
    try {
      // Use the new detailed AI analysis endpoint
      const response = await authFetch(`${config.endpoints.candidates}/${candidate.id}/ai-analysis${aiAnalysis ? '?refresh=true' : ''}`)

      if (!response.ok) {
        if (response.status === 503) {
          throw new Error('AI service not configured. Please ensure Ollama is running.')
        }
        throw new Error('Failed to analyze candidate')
      }

      const analysis = await response.json()
      setAiAnalysis(analysis)
      
      addNotification({
        type: 'success',
        title: 'AI Analysis Complete',
        message: `Detailed assessment generated for ${candidate.name}`,
      })
    } catch (error: any) {
      console.error('AI Analysis error:', error)
      setAnalysisError(error.message || 'Failed to analyze candidate')
      
      // Fallback to simple analysis — derive grade AND recommendation from score
      const fallbackScore = candidate.matchScore
      // Consistent grade derivation
      const deriveGrade = (s: number) => {
        if (s >= 90) return { rating: 'A+', rec: 'STRONGLY_RECOMMEND' }
        if (s >= 80) return { rating: 'A', rec: 'STRONGLY_RECOMMEND' }
        if (s >= 70) return { rating: 'A-', rec: 'RECOMMEND' }
        if (s >= 60) return { rating: 'B+', rec: 'RECOMMEND' }
        if (s >= 50) return { rating: 'B', rec: 'CONSIDER' }
        if (s >= 40) return { rating: 'B-', rec: 'CONSIDER' }
        return { rating: 'C+', rec: 'REVIEW' }
      }
      const { rating: fbRating, rec: fbRec } = deriveGrade(fallbackScore)
      setAiAnalysis({
        executive_summary: `${candidate.name} has a ${fallbackScore}% match score with ${candidate.experience || 0} years of experience. Their skill set includes ${candidate.skills.slice(0, 5).join(', ')}. A more detailed AI assessment is recommended when the AI service becomes available.`,
        technical_assessment: `The candidate lists ${candidate.skills.length} skills: ${candidate.skills.slice(0, 8).join(', ')}. These should be validated through technical assessment.`,
        experience_assessment: `${candidate.name} reports ${candidate.experience || 0} years of professional experience. Career progression details should be explored during the interview process.`,
        pros: ['Profile submitted and in active pipeline', `Lists ${candidate.skills.length} relevant skills`, `${candidate.experience || 0} years of experience`],
        cons: ['Detailed AI analysis unavailable - manual review recommended'],
        hiring_recommendation: fbRec,
        hiring_recommendation_rationale: fallbackScore >= 80 ? 'Strong candidate profile. Interview recommended to confirm fit.' : fallbackScore >= 60 ? 'Promising candidate. Further assessment recommended.' : 'Automated analysis was limited. A manual review is recommended.',
        confidence_score: fallbackScore || 50,
        overall_rating: fbRating,
        source: 'profile-based',
        isFallback: true
      })
    } finally {
      setIsAnalyzing(false)
    }
  }, [candidate, aiAnalysis, addNotification])

  // Auto-load cached AI analysis on page load
  const [autoTriggered, setAutoTriggered] = useState(false)
  useEffect(() => {
    if (candidate?.id) {
      authFetch(`${config.endpoints.candidates}/${candidate.id}/ai-analysis`)
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data?.executive_summary) {
            setAiAnalysis(data)
          } else {
            // Mark for auto-trigger — handled by a separate effect to avoid stale closure
            setAutoTriggered(true)
          }
        })
        .catch(() => {})
    }
  }, [candidate?.id])

  // Separate effect for auto-triggering AI analysis — avoids stale closure on handleAIAnalysis
  useEffect(() => {
    if (autoTriggered && candidate && !aiAnalysis && !isAnalyzing) {
      setAutoTriggered(false)
      handleAIAnalysis()
    }
  }, [autoTriggered, candidate, aiAnalysis, isAnalyzing, handleAIAnalysis])

  const handleToggleShortlist = async () => {
    if (!candidate) return
    const wasShortlisted = isShortlisted(candidate.id)
    setIsShortlisting(true)
    
    try {
      if (!wasShortlisted) {
        // Shortlisting — call API which persists status AND auto-sends email
        const result = await candidateApi.updateStatus(candidate.id, 'Shortlisted')
        toggleShortlist(candidate.id)
        
        const emailStatus = result?.data?.email_sent?.status
        const emailSent = emailStatus === 'success' || emailStatus === 'queued'
        addNotification({
          type: 'success',
          title: 'Added to Shortlist',
          message: emailSent 
            ? `${candidate.name} shortlisted — notification email sent!`
            : emailStatus === 'error'
              ? `${candidate.name} shortlisted — email failed: ${result?.data?.email_sent?.message || 'unknown error'}`
              : `${candidate.name} added to your shortlist`,
          actionUrl: '/shortlist'
        })
      } else {
        // Un-shortlisting — revert to Reviewed status
        await candidateApi.updateStatus(candidate.id, 'Reviewed')
        toggleShortlist(candidate.id)
        addNotification({
          type: 'info',
          title: 'Removed from Shortlist',
          message: `${candidate.name} removed from your shortlist`,
          actionUrl: `/candidates/${candidate.id}`
        })
      }
    } catch (error) {
      console.error('Shortlist error:', error)
      addNotification({
        type: 'error',
        title: 'Shortlist Failed',
        message: `Could not update shortlist status for ${candidate.name}`,
      })
    } finally {
      setIsShortlisting(false)
    }
  }

  const handleScheduleInterview = () => {
    if (!candidate) return
    setShowCalendarPicker(true)
  }

  const openCalendar = (provider: 'google' | 'outlook') => {
    if (!candidate) return
    const startDate = new Date(interviewDate)
    const endDate = new Date(startDate)
    endDate.setHours(endDate.getHours() + 1)

    const title = encodeURIComponent(`Interview: ${candidate.name}`)
    const details = encodeURIComponent(`Interview for ${candidate.name}\nEmail: ${candidate.email}\nPhone: ${candidate.phone}`)
    const location = encodeURIComponent('Video Call')

    let calendarUrl: string

    if (provider === 'outlook') {
      const startIso = startDate.toISOString()
      const endIso = endDate.toISOString()
      calendarUrl = `https://outlook.office.com/calendar/0/deeplink/compose?subject=${title}&body=${details}&location=${location}&startdt=${encodeURIComponent(startIso)}&enddt=${encodeURIComponent(endIso)}`
    } else {
      const formatDate = (date: Date) => date.toISOString().replace(/-|:|\.\d+/g, '')
      calendarUrl = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&details=${details}&location=${location}&dates=${formatDate(startDate)}/${formatDate(endDate)}`
    }

    addNotification({
      type: 'success',
      title: 'Interview Scheduled',
      message: `Interview with ${candidate.name} scheduled for ${startDate.toLocaleDateString()} at ${startDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`,
      actionUrl: `/candidates/${candidate.id}`
    })

    setShowCalendarPicker(false)
    window.open(calendarUrl, '_blank')
  }

  const handleSendMessage = () => {
    if (!candidate) return
    const subject = `Application Follow-up`
    const body = `Hello ${candidate.name},`
    window.location.href = `mailto:${candidate.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
  }

  const [showRejectConfirm, setShowRejectConfirm] = useState(false)

  const handleRejectCandidate = async () => {
    if (!candidate) return
    try {
      await candidateApi.updateStatus(candidate.id, 'Rejected')
      addNotification({
        type: 'info',
        title: 'Candidate Rejected',
        message: `${candidate.name} has been marked as rejected`,
        actionUrl: '/candidates'
      })
      setShowRejectConfirm(false)
      navigate('/candidates')
    } catch (error) {
      console.error('Update error:', error)
      addNotification({
        type: 'error',
        title: 'Update Failed',
        message: 'Failed to update candidate status'
      })
    }
  }

  if (!candidate && (loading || fullDataLoading)) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-sky-500 mx-auto mb-4"></div>
          <p className="text-gray-500">Loading candidate...</p>
        </div>
      </div>
    )
  }

  if (!candidate) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <p className="text-gray-600">Candidate not found</p>
          <Button onClick={() => navigate('/candidates')} className="mt-4">
            Back to Candidates
          </Button>
        </div>
      </div>
    )
  }

  // Use backend status as source of truth (survives page refresh), with in-memory store as fallback
  const shortlisted = candidate.status === 'Shortlisted' || isShortlisted(candidate.id)

  const scoreColor = candidate.matchScore >= 80 ? 'text-emerald-600' : candidate.matchScore >= 60 ? 'text-blue-600' : candidate.matchScore >= 40 ? 'text-amber-600' : 'text-red-500'
  const catColor = getCategoryColor(candidate.jobCategory || 'General')

  return (
    <div className="max-w-6xl mx-auto space-y-5 pb-10">
      {/* Breadcrumb */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
        <button
          onClick={() => navigate('/candidates')}
          className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-sky-600 transition-colors font-medium"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Candidates
        </button>
      </motion.div>

      {/* Hero Card */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
      >
        <Card className="overflow-hidden border border-gray-100/80 shadow-sm bg-white">
          {/* Colored accent bar */}
          <div className="h-1 bg-gradient-to-r from-sky-500 to-indigo-500" />
          <CardContent className="p-6">
            <div className="flex flex-col lg:flex-row lg:items-start gap-5">
              {/* Left: Avatar + Info */}
              <div className="flex items-start gap-4 flex-1 min-w-0">
                <Avatar className="w-14 h-14 ring-2 ring-sky-100 flex-shrink-0">
                  <AvatarFallback className="text-lg font-semibold bg-gradient-to-br from-sky-100 to-sky-200 text-sky-700">{candidate.name.charAt(0)}</AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap mb-1.5">
                    <h1 className="text-xl font-bold text-gray-900 truncate">{candidate.name}</h1>
                    {candidate.jobCategory && (
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${catColor.bg} ${catColor.text} border ${catColor.border}`}>
                        <Tag className="w-3 h-3" />
                        {candidate.jobCategory}
                      </span>
                    )}
                    {candidate.jobSubcategory && (
                      <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">{candidate.jobSubcategory}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-sm text-gray-500 flex-wrap">
                    {candidate.email && (
                      <span className="inline-flex items-center gap-1 truncate">
                        <Mail className="w-3.5 h-3.5 text-gray-400" />{candidate.email}
                      </span>
                    )}
                    {candidate.phone && candidate.phone.replace(/\D/g, '').length >= 7 && (
                      <span className="inline-flex items-center gap-1">
                        <Phone className="w-3.5 h-3.5 text-gray-400" />{candidate.phone}
                      </span>
                    )}
                    {candidate.location && (
                      <span className="inline-flex items-center gap-1">
                        <MapPin className="w-3.5 h-3.5 text-gray-400" />{candidate.location}
                      </span>
                    )}
                    {candidate.experience > 0 && (
                      <span className="inline-flex items-center gap-1">
                        <Briefcase className="w-3.5 h-3.5 text-gray-400" />{candidate.experience} yrs
                      </span>
                    )}
                  </div>
                  {/* Quick Contact Row */}
                  <div className="flex items-center gap-1.5 mt-3 flex-wrap">
                    {candidate.email && (
                      <button onClick={() => window.location.href = `mailto:${candidate.email}?subject=Regarding Your Application&body=Hi ${candidate.name},%0A%0A`} className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-md border border-sky-200 text-sky-600 bg-sky-50/50 hover:bg-sky-100 transition-colors">
                        <Mail className="w-3 h-3" />Email
                      </button>
                    )}
                    {candidate.phone && (
                      <button onClick={() => { const p = candidate.phone.replace(/[\s\-\(\)]/g, '').replace(/^\+/, ''); window.open(`https://wa.me/${p}?text=Hi ${encodeURIComponent(candidate.name)}, I'm reaching out regarding your job application.`, '_blank') }} className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-md border border-green-200 text-green-600 bg-green-50/50 hover:bg-green-100 transition-colors">
                        <MessageCircle className="w-3 h-3" />WhatsApp
                      </button>
                    )}
                    {candidate.linkedin && (
                      <button onClick={() => window.open(candidate.linkedin, '_blank')} className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-md border border-sky-200 text-sky-600 bg-sky-50/50 hover:bg-sky-100 transition-colors">
                        <Linkedin className="w-3 h-3" />LinkedIn<ExternalLink className="w-2.5 h-2.5" />
                      </button>
                    )}
                    {candidate.phone && (
                      <button onClick={() => window.location.href = `tel:${candidate.phone}`} className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-md border border-gray-200 text-gray-600 hover:bg-gray-100 transition-colors">
                        <Phone className="w-3 h-3" />Call
                      </button>
                    )}
                  </div>
                </div>
              </div>

              {/* Right: Score + Actions */}
              <div className="flex items-center gap-3 flex-shrink-0">
                {/* Score badge */}
                <div className="text-center px-4">
                  <div className={`text-2xl font-bold ${scoreColor}`}>{(candidate.matchScore ?? 50).toFixed(0)}%</div>
                  <div className="text-[10px] font-medium text-gray-400 uppercase tracking-wider mt-0.5">Match</div>
                </div>
                <div className="h-10 w-px bg-gray-200" />
                {/* Action buttons */}
                <div className="flex items-center gap-2">
                  <Button 
                    size="sm"
                    onClick={handleAIAnalysis}
                    disabled={isAnalyzing}
                    className="bg-gradient-to-r from-slate-800 to-slate-700 text-white hover:from-slate-700 hover:to-slate-600 shadow-sm text-xs h-8"
                  >
                    {isAnalyzing ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Sparkles className="w-3.5 h-3.5 mr-1" />}
                    {isAnalyzing ? 'Analyzing...' : aiAnalysis ? 'Refresh' : 'AI Analysis'}
                  </Button>
                  <Button 
                    size="sm"
                    variant="outline" 
                    onClick={() => generateCandidatePDF(candidate, aiAnalysis)}
                    className="text-xs h-8"
                  >
                    <Download className="w-3.5 h-3.5 mr-1" />PDF
                  </Button>
                  <Button 
                    size="sm"
                    variant="outline" 
                    onClick={handleToggleShortlist} 
                    disabled={isShortlisting}
                    className={`text-xs h-8 ${shortlisted ? 'border-yellow-300 bg-yellow-50 text-yellow-700 hover:bg-red-50 hover:border-red-300 hover:text-red-700' : ''}`}
                  >
                    {isShortlisting ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Star className={`w-3.5 h-3.5 mr-1 ${shortlisted ? 'fill-yellow-400 text-yellow-500' : ''}`} />}
                    {isShortlisting ? '...' : shortlisted ? 'Unshortlist' : 'Shortlist'}
                  </Button>
                </div>
              </div>
            </div>

            {/* Quick Actions Row — Schedule / Message / Reject inline */}
            <div className="flex items-center gap-2 px-6 pb-4 pt-2.5 border-t border-gray-100 mt-3">
              <Button variant="success" size="sm" className="text-xs h-8" onClick={handleScheduleInterview}>
                <Calendar className="w-3.5 h-3.5 mr-1" />Schedule Interview
              </Button>
              <Button variant="outline" size="sm" className="text-xs h-8" onClick={handleSendMessage}>
                <Mail className="w-3.5 h-3.5 mr-1" />Send Message
              </Button>
              <Button variant="destructive" size="sm" className="text-xs h-8" onClick={() => setShowRejectConfirm(true)}>
                <XOctagon className="w-3.5 h-3.5 mr-1" />Reject Candidate
              </Button>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* AI Analysis — Compact Assessment Card */}
      {aiAnalysis && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
          <div className="rounded-xl border border-sky-100 bg-white overflow-hidden shadow-sm">
            <div className="bg-gradient-to-r from-sky-50 to-indigo-50 px-5 py-3 flex items-center justify-between border-b border-sky-100/60">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-sky-500" />
                <span className="text-sm font-semibold text-gray-900">AI Assessment</span>
                  {aiAnalysis.isFallback && <span className="text-[10px] text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">Basic</span>}
                  {aiAnalysis.from_cache && <span className="text-[10px] text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">Cached</span>}
                </div>
                <div className="flex items-center gap-2">
                  {aiAnalysis.overall_rating && (
                    <span className={`text-sm font-bold px-2 py-0.5 rounded ${
                      aiAnalysis.overall_rating?.startsWith('A') ? 'bg-emerald-100 text-emerald-700' :
                      aiAnalysis.overall_rating?.startsWith('B') ? 'bg-blue-100 text-blue-700' :
                      aiAnalysis.overall_rating?.startsWith('C') ? 'bg-amber-100 text-amber-700' :
                      'bg-red-100 text-red-700'
                    }`}>{aiAnalysis.overall_rating}</span>
                  )}
                  {aiAnalysis.hiring_recommendation && (
                    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded ${
                      aiAnalysis.hiring_recommendation === 'STRONGLY_RECOMMEND' ? 'bg-emerald-600 text-white' :
                      aiAnalysis.hiring_recommendation === 'RECOMMEND' ? 'bg-emerald-500 text-white' :
                      aiAnalysis.hiring_recommendation === 'CONSIDER' ? 'bg-amber-500 text-white' :
                      'bg-red-500 text-white'
                    }`}>{aiAnalysis.hiring_recommendation.replace('_', ' ')}</span>
                  )}
                </div>
              </div>
            <div className="p-5 space-y-4">
              {/* Executive Summary */}
              {aiAnalysis.executive_summary && (
                <p className="text-sm text-gray-700 leading-relaxed bg-slate-50 rounded-lg p-4">{aiAnalysis.executive_summary}</p>
              )}

              {/* Assessment grid */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {aiAnalysis.technical_assessment && (
                  <div className="rounded-lg p-3 bg-gray-50/70 border border-gray-100">
                    <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Technical</h4>
                    <p className="text-sm text-gray-700 leading-relaxed">{aiAnalysis.technical_assessment}</p>
                  </div>
                )}
                {aiAnalysis.experience_assessment && (
                  <div className="rounded-lg p-3 bg-gray-50/70 border border-gray-100">
                    <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Experience</h4>
                    <p className="text-sm text-gray-700 leading-relaxed">{aiAnalysis.experience_assessment}</p>
                  </div>
                )}
                {aiAnalysis.education_assessment && (
                  <div className="rounded-lg p-3 bg-gray-50/70 border border-gray-100">
                    <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Education</h4>
                    <p className="text-sm text-gray-700 leading-relaxed">{aiAnalysis.education_assessment}</p>
                  </div>
                )}
                {aiAnalysis.career_trajectory && (
                  <div className="rounded-lg p-3 bg-gray-50/70 border border-gray-100">
                    <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5 flex items-center gap-1"><TrendingUp className="w-3 h-3" />Career</h4>
                    <p className="text-sm text-gray-700 leading-relaxed">{aiAnalysis.career_trajectory}</p>
                  </div>
                )}
              </div>

              {/* Pros & Cons — compact side-by-side */}
              <div className="grid grid-cols-2 gap-3">
                {aiAnalysis.pros?.length > 0 && (
                  <div className="rounded-lg p-3 border border-emerald-100 bg-emerald-50/30">
                    <h4 className="text-xs font-semibold text-emerald-700 mb-2 flex items-center gap-1"><CheckCircle className="w-3 h-3" />Strengths</h4>
                    <ul className="space-y-1">
                      {aiAnalysis.pros.map((pro: string, i: number) => (
                        <li key={i} className="text-xs text-gray-700 flex items-start gap-1.5"><span className="text-emerald-500 mt-px font-bold">+</span><span>{pro}</span></li>
                      ))}
                    </ul>
                  </div>
                )}
                {aiAnalysis.cons?.length > 0 && (
                  <div className="rounded-lg p-3 border border-red-100 bg-red-50/30">
                    <h4 className="text-xs font-semibold text-red-600 mb-2 flex items-center gap-1"><AlertCircle className="w-3 h-3" />Gaps</h4>
                    <ul className="space-y-1">
                      {aiAnalysis.cons.map((con: string, i: number) => (
                        <li key={i} className="text-xs text-gray-700 flex items-start gap-1.5"><span className="text-red-400 mt-px font-bold">-</span><span>{con}</span></li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* Interview Focus & Ideal Roles */}
              {(aiAnalysis.interview_focus_areas?.length > 0 || aiAnalysis.ideal_roles?.length > 0) && (
                <div className="grid grid-cols-2 gap-3">
                  {aiAnalysis.interview_focus_areas?.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Interview Focus</h4>
                      <ul className="space-y-0.5">{aiAnalysis.interview_focus_areas.map((a: string, i: number) => <li key={i} className="text-xs text-gray-600">• {a}</li>)}</ul>
                    </div>
                  )}
                  {aiAnalysis.ideal_roles?.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Ideal Roles</h4>
                      <div className="flex flex-wrap gap-1">{aiAnalysis.ideal_roles.map((r: string, i: number) => <span key={i} className="text-xs bg-sky-50 text-sky-700 px-2 py-0.5 rounded-full border border-sky-100">{r}</span>)}</div>
                      {aiAnalysis.salary_range_estimate && <p className="text-[11px] text-gray-400 mt-1.5">Est. Salary: {aiAnalysis.salary_range_estimate}</p>}
                    </div>
                  )}
                </div>
              )}

              {/* Recommendation + Confidence */}
              {aiAnalysis.hiring_recommendation_rationale && (
                <div className="bg-sky-50/60 rounded-lg p-3 border border-sky-100">
                  <p className="text-sm text-gray-800 leading-relaxed">{aiAnalysis.hiring_recommendation_rationale}</p>
                  {aiAnalysis.culture_fit_notes && <p className="text-xs text-gray-500 mt-2">Culture: {aiAnalysis.culture_fit_notes}</p>}
                </div>
              )}
              {aiAnalysis.confidence_score && (
                <div className="flex items-center gap-2 text-xs text-gray-400">
                  <span>Confidence: {aiAnalysis.confidence_score}%</span>
                  <Progress value={aiAnalysis.confidence_score} className="h-1.5 flex-1 max-w-32" />
                  {aiAnalysis.source && <span className="capitalize">{aiAnalysis.source === 'fallback' ? 'profile-based' : aiAnalysis.source}</span>}
                </div>
              )}
            </div>
          </div>
        </motion.div>
      )}

      {analysisError && !aiAnalysis && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
          <div className="flex items-start gap-2.5 bg-amber-50 border border-amber-200 rounded-lg p-3">
            <AlertCircle className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-amber-800">AI Analysis Unavailable</p>
              <p className="text-xs text-amber-600 mt-0.5">{analysisError}</p>
            </div>
          </div>
        </motion.div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Left Column — Main Content */}
        <div className="lg:col-span-2 space-y-4">
          {/* Summary */}
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}>
            <div className="rounded-xl border border-gray-100/80 bg-white shadow-sm">
              <div className="p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Professional Summary</h3>
                {fullDataLoading && !fullCandidateData ? (
                  <div className="space-y-2 animate-pulse">
                    <div className="h-3 bg-gray-200 rounded w-full" />
                    <div className="h-3 bg-gray-200 rounded w-5/6" />
                    <div className="h-3 bg-gray-200 rounded w-4/6" />
                  </div>
                ) : candidate.summary ? (
                  <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{candidate.summary}</p>
                ) : (
                  <p className="text-sm text-gray-400 italic">No summary available. Run AI Analysis to generate one.</p>
                )}
              </div>
            </div>
          </motion.div>

          {/* Skills */}
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
            <div className="rounded-xl border border-gray-100/80 bg-white shadow-sm">
              <div className="p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">Skills & Expertise</h3>
                <div className="flex flex-wrap gap-1.5">
                  {candidate.skills.map((skill: string) => (
                    <span key={skill} className="text-[11px] px-2 py-0.5 rounded-md font-medium bg-sky-50 text-sky-700 border border-sky-100">
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </motion.div>

          {/* Work Experience */}
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}>
            <div className="rounded-xl border border-gray-100/80 bg-white shadow-sm">
              <div className="p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-4 flex items-center gap-1.5">
                  <Briefcase className="w-3.5 h-3.5" />Work Experience
                </h3>
                <div className="space-y-4">
                  {fullDataLoading && !fullCandidateData ? (
                    <div className="space-y-3 animate-pulse">
                      {[1,2,3].map(i => <div key={i} className="pl-5"><div className="h-3 bg-gray-200 rounded w-3/4 mb-1.5" /><div className="h-2.5 bg-gray-200 rounded w-1/2" /></div>)}
                    </div>
                  ) : candidate.workHistory && candidate.workHistory.length > 0 ? (
                    candidate.workHistory.map((job: any, index: number) => (
                      <div key={index} className="relative pl-5 pb-4 last:pb-0 border-l border-gray-200 last:border-l-transparent">
                        <div className="absolute left-0 top-1 w-2 h-2 -translate-x-[5px] rounded-full bg-slate-800 ring-2 ring-white" />
                        <h4 className="text-sm font-semibold text-gray-900">{job.title}</h4>
                        {(job.company || job.duration) && (
                          <p className="text-xs text-gray-500 mt-0.5">
                            {job.company}{job.company && job.duration ? ' · ' : ''}{job.duration}
                          </p>
                        )}
                        {job.description && (
                          <p className="text-xs text-gray-600 mt-1.5 leading-relaxed">{job.description}</p>
                        )}
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-gray-400 italic">No work experience data available</p>
                  )}
                </div>
              </div>
            </div>
          </motion.div>

          {/* Education */}
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
            <div className="rounded-xl border border-gray-100/80 bg-white shadow-sm">
              <div className="p-5">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3 flex items-center gap-1.5">
                  <GraduationCap className="w-3.5 h-3.5" />Education
                </h3>
                <div className="space-y-3">
                  {fullDataLoading && !fullCandidateData ? (
                    <div className="space-y-2 animate-pulse">
                      {[1,2].map(i => <div key={i}><div className="h-3 bg-gray-200 rounded w-2/3 mb-1" /><div className="h-2.5 bg-gray-200 rounded w-1/2" /></div>)}
                    </div>
                  ) : candidate.education && candidate.education.length > 0 ? (
                    candidate.education.map((edu: any, index: number) => (
                      <div key={index}>
                        <h4 className="text-sm font-semibold text-gray-900">
                          {edu.degree}{edu.field ? ` in ${edu.field}` : ''}
                        </h4>
                        <p className="text-xs text-gray-500">
                          {edu.institution || 'Institution not specified'}{edu.year ? ` · ${edu.year}` : ''}
                        </p>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-gray-400 italic">No education data available</p>
                  )}
                </div>
              </div>
            </div>
          </motion.div>

          {/* Certifications & Languages — combined row */}
          {((candidate.certifications && candidate.certifications.length > 0) || (candidate.languages && candidate.languages.length > 0)) && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }}>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {candidate.certifications && candidate.certifications.length > 0 && (
                  <div className="rounded-xl border border-gray-100/80 bg-white shadow-sm">
                    <div className="p-5">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2 flex items-center gap-1.5">
                        <Award className="w-3.5 h-3.5" />Certifications
                      </h3>
                      <div className="space-y-1.5">
                        {candidate.certifications.map((cert: string, i: number) => (
                          <div key={i} className="flex items-start gap-1.5">
                            <Award className="w-3 h-3 text-amber-500 mt-0.5 flex-shrink-0" />
                            <span className="text-xs text-gray-700">{cert}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
                {candidate.languages && candidate.languages.length > 0 && (
                  <div className="rounded-xl border border-gray-100/80 bg-white shadow-sm">
                    <div className="p-5">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2 flex items-center gap-1.5">
                        <Globe className="w-3.5 h-3.5" />Languages
                      </h3>
                      <div className="flex flex-wrap gap-1.5">
                        {candidate.languages.map((lang: string, i: number) => (
                          <span key={i} className="text-xs px-2 py-0.5 rounded-full border border-sky-100 bg-sky-50/50 text-sky-700">{lang}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </motion.div>
          )}

          {/* Original Resume Text — shown as-is from email/upload */}
          {candidate.resumeText && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
              <div className="rounded-xl border border-gray-100/80 bg-white shadow-sm">
                <div className="p-5">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400 flex items-center gap-1.5">
                      <Download className="w-3.5 h-3.5" />Original Resume Content
                    </h3>
                    <button
                      onClick={async () => {
                        try { await downloadOriginalResume(candidate as any) } catch { toast.error('Download failed', 'No resume file available') }
                      }}
                      className="flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 px-2.5 py-1 rounded-lg transition-colors"
                    >
                      <FileDown className="w-3.5 h-3.5" />Download Resume
                    </button>
                  </div>
                  {isTextGarbled(candidate.resumeText) ? (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 flex items-center gap-3">
                      <FileDown className="w-5 h-5 text-amber-500 flex-shrink-0" />
                      <div>
                        <p className="text-sm font-medium text-amber-800">Resume text could not be displayed</p>
                        <p className="text-xs text-amber-600 mt-0.5">The extracted text contains encoding errors. Use <strong>Download Resume</strong> to view the original file.</p>
                      </div>
                    </div>
                  ) : (
                    <div className="bg-slate-50 rounded-lg p-4 max-h-96 overflow-y-auto">
                      <p className="text-xs text-gray-700 leading-relaxed whitespace-pre-line font-mono">{candidate.resumeText}</p>
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          )}
        </div>

        {/* Right Column — Sidebar */}
        <div className="space-y-4">
          {/* Match Score — compact */}
          <motion.div initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.05 }}>
            <div className="rounded-xl border border-sky-100 bg-white shadow-sm overflow-hidden">
              <div className="h-1 bg-gradient-to-r from-sky-500 to-indigo-500" />
              <div className="p-4 text-center">
                <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Match Score</p>
                <div className={`text-4xl font-bold ${getMatchScoreColor(candidate.matchScore)}`}>
                  {(candidate.matchScore ?? 50).toFixed(0)}%
                </div>
                <Progress
                  value={candidate.matchScore}
                  className="h-1.5 mt-3 mb-2"
                  indicatorClassName={
                    candidate.matchScore >= 80
                      ? 'bg-emerald-500'
                      : candidate.matchScore >= 60
                      ? 'bg-sky-500'
                      : candidate.matchScore >= 40
                      ? 'bg-amber-500'
                      : 'bg-red-500'
                  }
                />
                <span className={`inline-block text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full ${
                  candidate.matchScore >= 80
                    ? 'bg-emerald-50 text-emerald-700'
                    : candidate.matchScore >= 60
                    ? 'bg-sky-50 text-sky-700'
                    : candidate.matchScore >= 40
                    ? 'bg-amber-50 text-amber-700'
                    : 'bg-red-50 text-red-700'
                }`}>
                  {candidate.matchScore >= 80 ? 'Strong' : candidate.matchScore >= 60 ? 'Medium' : candidate.matchScore >= 40 ? 'Weak' : 'Low'} Match
                </span>
              </div>
            </div>
          </motion.div>

          {/* Quick Info — compact rows */}
          <motion.div initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 }}>
            <div className="rounded-xl border border-gray-100/80 bg-white shadow-sm">
              <div className="p-4 space-y-2.5">
                <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Quick Info</p>
                {[
                  { label: 'Experience', value: `${candidate.experience} years` },
                  { label: 'Applied', value: candidate.appliedDate ? new Date(candidate.appliedDate).toLocaleDateString() : '—' },
                  { label: 'Location', value: candidate.location },
                ].map((row) => (
                  <div key={row.label} className="flex items-center justify-between text-xs">
                    <span className="text-gray-500">{row.label}</span>
                    <span className="font-medium text-gray-900">{row.value}</span>
                  </div>
                ))}
              </div>
            </div>
          </motion.div>

          {/* AI Evaluation — populated from AI analysis or candidate data */}
          {(aiAnalysis || (candidate.evaluation && (candidate.evaluation.strengths?.length > 0 || candidate.evaluation.gaps?.length > 0))) && (
            <motion.div initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 }}>
              <div className="rounded-xl border border-sky-100 bg-white shadow-sm overflow-hidden">
                <div className="h-0.5 bg-gradient-to-r from-sky-500 to-indigo-500" />
                <div className="p-4 space-y-3">
                  <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">AI Evaluation</p>

                  {(() => {
                    const strengths = aiAnalysis?.pros || candidate.evaluation?.strengths || []
                    const gaps = aiAnalysis?.cons || candidate.evaluation?.gaps || []
                    const recommendation = aiAnalysis?.hiring_recommendation_rationale || aiAnalysis?.hiring_recommendation?.replace('_', ' ') || candidate.evaluation?.recommendation || candidate.jobCategory || 'General'
                    return (
                      <>
                        {strengths.length > 0 && (
                          <div>
                            <h4 className="text-[11px] font-semibold text-emerald-700 uppercase tracking-wide mb-1 flex items-center gap-1">
                              <CheckCircle className="w-3 h-3" />Strengths
                            </h4>
                            <ul className="space-y-1">
                              {strengths.slice(0, 5).map((s: string, i: number) => (
                                <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                  <span className="w-1 h-1 rounded-full bg-emerald-400 mt-1.5 flex-shrink-0" />
                                  {s}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {gaps.length > 0 && (
                          <div>
                            <h4 className="text-[11px] font-semibold text-red-600 uppercase tracking-wide mb-1 flex items-center gap-1">
                              <XCircle className="w-3 h-3" />Gaps
                            </h4>
                            <ul className="space-y-1">
                              {gaps.slice(0, 5).map((g: string, i: number) => (
                                <li key={i} className="text-xs text-gray-600 flex items-start gap-1.5">
                                  <span className="w-1 h-1 rounded-full bg-red-400 mt-1.5 flex-shrink-0" />
                                  {g}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        <div className="pt-2 border-t border-gray-100">
                          <h4 className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">Recommendation</h4>
                          <p className="text-xs text-gray-700 leading-relaxed">{recommendation}</p>
                        </div>
                      </>
                    )
                  })()}
                </div>
              </div>
            </motion.div>
          )}

          {/* Actions moved to hero card — inline row at top */}
        </div>
      </div>

      {/* Calendar Picker Modal */}
      {showCalendarPicker && candidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowCalendarPicker(false)}>
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            onClick={(e) => e.stopPropagation()}
            className="bg-white rounded-xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden"
          >
            <div className="bg-gradient-to-r from-slate-800 to-slate-700 px-5 py-3.5">
              <h3 className="text-white font-semibold text-sm">Schedule Interview</h3>
              <p className="text-gray-300 text-xs mt-0.5">with {candidate.name}</p>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1.5">Date & Time</label>
                <input
                  type="datetime-local"
                  value={interviewDate}
                  onChange={(e) => setInterviewDate(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400"
                />
              </div>
              <div className="space-y-2">
                <p className="text-xs font-medium text-gray-600">Open in Calendar</p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => openCalendar('outlook')}
                    className="flex items-center justify-center gap-2 px-3 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    <Calendar className="w-3.5 h-3.5" />
                    Microsoft Outlook
                  </button>
                  <button
                    onClick={() => openCalendar('google')}
                    className="flex items-center justify-center gap-2 px-3 py-2.5 bg-red-500 hover:bg-red-600 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    <Calendar className="w-3.5 h-3.5" />
                    Google Calendar
                  </button>
                </div>
              </div>
              <button
                onClick={() => setShowCalendarPicker(false)}
                className="w-full text-center text-xs text-gray-500 hover:text-gray-700 py-1"
              >
                Cancel
              </button>
            </div>
          </motion.div>
        </div>
      )}

      {/* Reject Confirmation Modal */}
      {showRejectConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowRejectConfirm(false)}>
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="bg-white rounded-xl shadow-xl p-6 max-w-sm mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Reject Candidate</h3>
            <p className="text-sm text-gray-600 mb-5">
              Are you sure you want to reject <span className="font-medium">{candidate?.name}</span>? This action will update their status.
            </p>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" size="sm" onClick={() => setShowRejectConfirm(false)}>Cancel</Button>
              <Button variant="destructive" size="sm" onClick={handleRejectCandidate}>Reject</Button>
            </div>
          </motion.div>
        </div>
      )}
    </div>
  )
}
