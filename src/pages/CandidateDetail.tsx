import { useParams, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { useState, useEffect } from 'react'
import {
  ArrowLeft,
  Download,
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
} from 'lucide-react'
import { useCandidates } from '@/hooks/useCandidates'
import { useCandidateStore } from '@/store/candidateStore'
import { useNotificationStore } from '@/store/notificationStore'
import { candidateApi } from '@/services/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/Avatar'
import { Progress } from '@/components/ui/Progress'
import { getMatchScoreColor } from '@/lib/utils'
import config from '@/config'
import { authFetch } from '@/lib/authFetch'

// Category colors for visual distinction
const categoryColors: Record<string, { bg: string; text: string; border: string }> = {
  'Software Engineer': { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  'DevOps Engineer': { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' },
  'Data Scientist': { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
  'Cybersecurity': { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-200' },
  'QA/Testing': { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' },
  'IT & Systems': { bg: 'bg-slate-50', text: 'text-slate-700', border: 'border-slate-200' },
  'Marketing': { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200' },
  'Sales': { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
  'Product Manager': { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' },
  'Project Management': { bg: 'bg-violet-50', text: 'text-violet-700', border: 'border-violet-200' },
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
  const { candidates } = useCandidates({ autoFetch: true })
  const isShortlisted = useCandidateStore((state) => state.isShortlisted)
  const toggleShortlist = useCandidateStore((state) => state.toggleShortlist)
  const addNotification = useNotificationStore((state) => state.addNotification)

  const [aiAnalysis, setAiAnalysis] = useState<any>(null)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [isShortlisting, setIsShortlisting] = useState(false)

  const candidate = candidates.find((c) => c.id === id)

  // Auto-load cached AI analysis on page load
  useEffect(() => {
    if (candidate?.id) {
      authFetch(`${config.endpoints.candidates}/${candidate.id}/ai-analysis`)
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data?.executive_summary) setAiAnalysis(data) })
        .catch(() => {})
    }
  }, [candidate?.id])

  const handleToggleShortlist = async () => {
    if (!candidate) return
    const wasShortlisted = isShortlisted(candidate.id)
    setIsShortlisting(true)
    
    try {
      if (!wasShortlisted) {
        // Shortlisting — call API which persists status AND auto-sends email
        const result = await candidateApi.updateStatus(candidate.id, 'Shortlisted')
        toggleShortlist(candidate.id)
        
        const emailSent = result?.data?.email_sent?.status === 'success'
        addNotification({
          type: 'success',
          title: 'Added to Shortlist',
          message: emailSent 
            ? `${candidate.name} shortlisted — notification email sent!`
            : `${candidate.name} added to your shortlist`,
          actionUrl: '/shortlist'
        })
      } else {
        // Un-shortlisting — revert status
        await candidateApi.updateStatus(candidate.id, candidate.status || 'Strong')
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

  const handleDownloadResume = async () => {
    if (!candidate) return
    try {
      const response = await authFetch(`${config.endpoints.candidates}/${candidate.id}/resume`)
      
      if (!response.ok) {
        alert('Resume file not available. Please upload the resume first.')
        return
      }
      
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${candidate.name.replace(/\s+/g, '_')}_resume.pdf`
      a.click()
      window.URL.revokeObjectURL(url)
    } catch (error) {
      console.error('Download error:', error)
      alert('Resume download not available yet. Feature coming soon!')
    }
  }

  const handleScheduleInterview = () => {
    if (!candidate) return
    // Create calendar event using native browser calendar
    const startDate = new Date()
    startDate.setDate(startDate.getDate() + 3) // 3 days from now
    startDate.setHours(10, 0, 0, 0) // 10:00 AM
    
    const endDate = new Date(startDate)
    endDate.setHours(11, 0, 0, 0) // 11:00 AM
    
    const formatDate = (date: Date) => {
      return date.toISOString().replace(/-|:|\.\d+/g, '')
    }
    
    const title = encodeURIComponent(`Interview: ${candidate.name}`)
    const details = encodeURIComponent(`Interview for ${candidate.name}\nEmail: ${candidate.email}\nPhone: ${candidate.phone}`)
    const location = encodeURIComponent('Video Call')
    
    // Google Calendar URL
    const googleCalUrl = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&details=${details}&location=${location}&dates=${formatDate(startDate)}/${formatDate(endDate)}`
    
    addNotification({
      type: 'success',
      title: 'Interview Scheduled',
      message: `Interview with ${candidate.name} scheduled for ${startDate.toLocaleDateString()}`,
      actionUrl: `/candidates/${candidate.id}`
    })
    
    window.open(googleCalUrl, '_blank')
  }

  const handleSendMessage = () => {
    if (!candidate) return
    const subject = `Application Follow-up`
    const body = `Hello ${candidate.name},`
    window.location.href = `mailto:${candidate.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
  }

  const handleAIAnalysis = async () => {
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
      
      // Fallback to simple analysis
      const fallbackScore = candidate.matchScore
      setAiAnalysis({
        executive_summary: `${candidate.name} has a ${fallbackScore}% match score with ${candidate.experience || 0} years of experience. Their skill set includes ${candidate.skills.slice(0, 5).join(', ')}. A more detailed AI assessment is recommended when the AI service becomes available.`,
        technical_assessment: `The candidate lists ${candidate.skills.length} skills: ${candidate.skills.slice(0, 8).join(', ')}. These should be validated through technical assessment.`,
        experience_assessment: `${candidate.name} reports ${candidate.experience || 0} years of professional experience. Career progression details should be explored during the interview process.`,
        pros: ['Profile submitted and in active pipeline', `Lists ${candidate.skills.length} relevant skills`, `${candidate.experience || 0} years of experience`],
        cons: ['Detailed AI analysis unavailable - manual review recommended'],
        hiring_recommendation: 'CONSIDER',
        hiring_recommendation_rationale: 'Automated analysis was limited. A manual review and interview is recommended to fully assess fit.',
        confidence_score: 40,
        overall_rating: 'C+',
        source: 'fallback',
        isFallback: true
      })
    } finally {
      setIsAnalyzing(false)
    }
  }

  const handleRejectCandidate = async () => {
    if (!candidate) return
    if (confirm(`Are you sure you want to reject ${candidate.name}?`)) {
      try {
        await candidateApi.updateStatus(candidate.id, 'Rejected')
        
        addNotification({
          type: 'info',
          title: 'Candidate Rejected',
          message: `${candidate.name} has been marked as rejected`,
          actionUrl: '/candidates'
        })
        alert(`${candidate.name} has been marked as rejected.`)
        navigate('/candidates')
      } catch (error) {
        console.error('Update error:', error)
        alert('Failed to update candidate status')
      }
    }
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

  const shortlisted = isShortlisted(candidate.id)

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <Button
          variant="ghost"
          onClick={() => navigate('/candidates')}
          className="mb-4"
        >
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Candidates
        </Button>

        <div className="flex items-start justify-between">
          <div className="flex items-center gap-4">
            <Avatar className="w-20 h-20">
              <AvatarImage
                src={`https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}`}
              />
              <AvatarFallback className="text-2xl">
                {candidate.name.charAt(0)}
              </AvatarFallback>
            </Avatar>
            <div>
              <div className="flex items-center gap-3 mb-1">
                <h1 className="text-3xl font-bold text-gray-900">{candidate.name}</h1>
                {candidate.jobCategory && (
                  <Badge 
                    className={`${getCategoryColor(candidate.jobCategory).bg} ${getCategoryColor(candidate.jobCategory).text} border ${getCategoryColor(candidate.jobCategory).border} text-sm`}
                  >
                    <Tag className="w-3 h-3 mr-1" />
                    {candidate.jobCategory}
                  </Badge>
                )}
                {candidate.jobSubcategory && (
                  <Badge variant="outline" className="text-sm">
                    {candidate.jobSubcategory}
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-4 mt-2 text-gray-600">
                <span className="flex items-center gap-1">
                  <Mail className="w-4 h-4" />
                  {candidate.email}
                </span>
                {candidate.phone && candidate.phone.replace(/\D/g, '').length >= 7 && (
                  <span className="flex items-center gap-1">
                    <Phone className="w-4 h-4" />
                    {candidate.phone}
                  </span>
                )}
                <span className="flex items-center gap-1">
                  <MapPin className="w-4 h-4" />
                  {candidate.location}
                </span>
              </div>
              {/* Quick Contact Buttons */}
              <div className="flex items-center gap-2 mt-3 flex-wrap">
                {/* Email Button */}
                {candidate.email && (
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={() => window.location.href = `mailto:${candidate.email}?subject=Regarding Your Application&body=Hi ${candidate.name},%0A%0A`}
                    className="text-blue-600 border-blue-200 hover:bg-blue-50"
                  >
                    <Mail className="w-4 h-4 mr-1" />
                    Email
                  </Button>
                )}
                
                {/* WhatsApp Button - uses phone number */}
                {candidate.phone && (
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={() => {
                      // Clean phone number for WhatsApp (remove spaces, dashes, parentheses)
                      const cleanPhone = candidate.phone.replace(/[\s\-\(\)]/g, '').replace(/^\+/, '')
                      window.open(`https://wa.me/${cleanPhone}?text=Hi ${encodeURIComponent(candidate.name)}, I'm reaching out regarding your job application.`, '_blank')
                    }}
                    className="text-green-600 border-green-200 hover:bg-green-50"
                  >
                    <MessageCircle className="w-4 h-4 mr-1" />
                    WhatsApp
                  </Button>
                )}
                
                {/* LinkedIn Button */}
                {candidate.linkedin && (
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={() => window.open(candidate.linkedin, '_blank')}
                    className="text-[#0077B5] border-[#0077B5]/30 hover:bg-[#0077B5]/10"
                  >
                    <Linkedin className="w-4 h-4 mr-1" />
                    LinkedIn
                    <ExternalLink className="w-3 h-3 ml-1" />
                  </Button>
                )}
                
                {/* Phone Call Button */}
                {candidate.phone && (
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={() => window.location.href = `tel:${candidate.phone}`}
                    className="text-purple-600 border-purple-200 hover:bg-purple-50"
                  >
                    <Phone className="w-4 h-4 mr-1" />
                    Call
                  </Button>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Button 
              variant="outline" 
              onClick={handleAIAnalysis}
              disabled={isAnalyzing}
              className="bg-gradient-to-r from-primary-500 to-purple-600 text-white border-0 hover:from-primary-600 hover:to-purple-700"
            >
              {isAnalyzing ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Analyzing...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4 mr-2" />
                  {aiAnalysis ? 'Refresh AI Analysis' : 'AI Analysis'}
                </>
              )}
            </Button>
            <Button variant="outline" onClick={handleToggleShortlist} disabled={isShortlisting}>
              {isShortlisting ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <Star className={`w-4 h-4 mr-2 ${shortlisted ? 'fill-yellow-400 text-yellow-400' : ''}`} />
              )}
              {isShortlisting ? 'Updating...' : shortlisted ? 'Shortlisted' : 'Add to Shortlist'}
            </Button>
            {candidate.hasResume && (
              <Button variant="outline" onClick={handleDownloadResume}>
                <Download className="w-4 h-4 mr-2" />
                Download Resume
              </Button>
            )}
          </div>
        </div>
      </motion.div>

      {/* AI Analysis Results — Detailed Assessment */}
      {aiAnalysis && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-6"
        >
          <Card className="border-2 border-primary-200 bg-gradient-to-r from-primary-50 to-purple-50">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="w-5 h-5 text-primary-600" />
                  AI Candidate Assessment
                  {aiAnalysis.isFallback && (
                    <Badge variant="outline" className="ml-2 text-xs">Basic</Badge>
                  )}
                  {aiAnalysis.from_cache && (
                    <Badge variant="outline" className="ml-2 text-xs text-gray-500">Cached</Badge>
                  )}
                </CardTitle>
                <div className="flex items-center gap-3">
                  {aiAnalysis.overall_rating && (
                    <div className="flex items-center gap-1">
                      <span className="text-sm text-gray-500">Rating:</span>
                      <Badge className={`text-lg px-3 py-1 ${
                        aiAnalysis.overall_rating?.startsWith('A') ? 'bg-green-100 text-green-800' :
                        aiAnalysis.overall_rating?.startsWith('B') ? 'bg-blue-100 text-blue-800' :
                        aiAnalysis.overall_rating?.startsWith('C') ? 'bg-yellow-100 text-yellow-800' :
                        'bg-red-100 text-red-800'
                      }`}>
                        {aiAnalysis.overall_rating}
                      </Badge>
                    </div>
                  )}
                  {aiAnalysis.hiring_recommendation && (
                    <Badge className={`text-sm px-3 py-1 ${
                      aiAnalysis.hiring_recommendation === 'STRONGLY_RECOMMEND' ? 'bg-green-600 text-white' :
                      aiAnalysis.hiring_recommendation === 'RECOMMEND' ? 'bg-green-500 text-white' :
                      aiAnalysis.hiring_recommendation === 'CONSIDER' ? 'bg-yellow-500 text-white' :
                      'bg-red-500 text-white'
                    }`}>
                      {aiAnalysis.hiring_recommendation.replace('_', ' ')}
                    </Badge>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              {/* Executive Summary */}
              {aiAnalysis.executive_summary && (
                <div className="bg-white rounded-lg p-5 border border-gray-100">
                  <h3 className="font-semibold text-gray-900 mb-3 text-base">Executive Summary</h3>
                  <p className="text-gray-700 leading-relaxed">{aiAnalysis.executive_summary}</p>
                </div>
              )}

              {/* Technical Assessment */}
              {aiAnalysis.technical_assessment && (
                <div className="bg-white rounded-lg p-5 border border-gray-100">
                  <h3 className="font-semibold text-gray-900 mb-3 text-base">Technical Assessment</h3>
                  <p className="text-gray-700 leading-relaxed">{aiAnalysis.technical_assessment}</p>
                </div>
              )}

              {/* Experience Assessment */}
              {aiAnalysis.experience_assessment && (
                <div className="bg-white rounded-lg p-5 border border-gray-100">
                  <h3 className="font-semibold text-gray-900 mb-3 text-base">Experience Assessment</h3>
                  <p className="text-gray-700 leading-relaxed">{aiAnalysis.experience_assessment}</p>
                </div>
              )}

              {/* Education Assessment */}
              {aiAnalysis.education_assessment && (
                <div className="bg-white rounded-lg p-5 border border-gray-100">
                  <h3 className="font-semibold text-gray-900 mb-3 text-base">Education Assessment</h3>
                  <p className="text-gray-700 leading-relaxed">{aiAnalysis.education_assessment}</p>
                </div>
              )}

              {/* Pros & Cons Side by Side */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Pros */}
                {aiAnalysis.pros && aiAnalysis.pros.length > 0 && (
                  <div className="bg-white rounded-lg p-5 border border-green-100">
                    <h3 className="font-semibold text-gray-900 mb-3 flex items-center gap-2">
                      <CheckCircle className="w-5 h-5 text-green-600" />
                      Pros
                    </h3>
                    <ul className="space-y-2">
                      {aiAnalysis.pros.map((pro: string, idx: number) => (
                        <li key={idx} className="text-gray-700 flex items-start gap-2 text-sm">
                          <span className="text-green-600 mt-0.5 font-bold">+</span>
                          <span>{pro}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Cons */}
                {aiAnalysis.cons && aiAnalysis.cons.length > 0 && (
                  <div className="bg-white rounded-lg p-5 border border-red-100">
                    <h3 className="font-semibold text-gray-900 mb-3 flex items-center gap-2">
                      <AlertCircle className="w-5 h-5 text-red-500" />
                      Cons
                    </h3>
                    <ul className="space-y-2">
                      {aiAnalysis.cons.map((con: string, idx: number) => (
                        <li key={idx} className="text-gray-700 flex items-start gap-2 text-sm">
                          <span className="text-red-500 mt-0.5 font-bold">-</span>
                          <span>{con}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* Career Trajectory */}
              {aiAnalysis.career_trajectory && (
                <div className="bg-white rounded-lg p-5 border border-gray-100">
                  <h3 className="font-semibold text-gray-900 mb-3 flex items-center gap-2">
                    <TrendingUp className="w-5 h-5 text-blue-600" />
                    Career Trajectory
                  </h3>
                  <p className="text-gray-700 leading-relaxed">{aiAnalysis.career_trajectory}</p>
                </div>
              )}

              {/* Interview Focus Areas & Ideal Roles */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {aiAnalysis.interview_focus_areas && aiAnalysis.interview_focus_areas.length > 0 && (
                  <div className="bg-white rounded-lg p-5 border border-gray-100">
                    <h3 className="font-semibold text-gray-900 mb-3">Interview Focus Areas</h3>
                    <ul className="space-y-2">
                      {aiAnalysis.interview_focus_areas.map((area: string, idx: number) => (
                        <li key={idx} className="text-gray-700 flex items-start gap-2 text-sm">
                          <span className="text-primary-600 mt-0.5">&#8226;</span>
                          <span>{area}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {aiAnalysis.ideal_roles && aiAnalysis.ideal_roles.length > 0 && (
                  <div className="bg-white rounded-lg p-5 border border-gray-100">
                    <h3 className="font-semibold text-gray-900 mb-3">Ideal Roles</h3>
                    <div className="flex flex-wrap gap-2">
                      {aiAnalysis.ideal_roles.map((role: string, idx: number) => (
                        <Badge key={idx} variant="primary" className="py-1.5 px-3">{role}</Badge>
                      ))}
                    </div>
                    {aiAnalysis.salary_range_estimate && (
                      <p className="text-sm text-gray-500 mt-3">
                        <span className="font-medium">Est. Salary:</span> {aiAnalysis.salary_range_estimate}
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* Culture Fit & Hiring Recommendation Rationale */}
              {aiAnalysis.hiring_recommendation_rationale && (
                <div className="bg-gradient-to-r from-primary-100 to-purple-100 rounded-lg p-5">
                  <h3 className="font-semibold text-gray-900 mb-3">Hiring Recommendation</h3>
                  <p className="text-gray-800 leading-relaxed">{aiAnalysis.hiring_recommendation_rationale}</p>
                  {aiAnalysis.culture_fit_notes && (
                    <p className="text-gray-700 mt-3 text-sm"><span className="font-medium">Culture Fit:</span> {aiAnalysis.culture_fit_notes}</p>
                  )}
                </div>
              )}

              {/* Confidence */}
              {aiAnalysis.confidence_score && (
                <div className="flex items-center gap-3 text-sm text-gray-500 pt-2">
                  <span>AI Confidence: {aiAnalysis.confidence_score}%</span>
                  <Progress value={aiAnalysis.confidence_score} className="h-2 flex-1 max-w-48" />
                  {aiAnalysis.source && <span className="text-xs">Source: {aiAnalysis.source}</span>}
                </div>
              )}
            </CardContent>
          </Card>
        </motion.div>
      )}

      {analysisError && !aiAnalysis && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-6"
        >
          <Card className="border-2 border-amber-200 bg-amber-50">
            <CardContent className="pt-6">
              <div className="flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="font-medium text-amber-900">AI Analysis Unavailable</p>
                  <p className="text-sm text-amber-700 mt-1">{analysisError}</p>
                  <p className="text-xs text-amber-600 mt-2">Using local matching algorithm as fallback.</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column - Candidate Info */}
        <div className="lg:col-span-2 space-y-6">
          {/* Summary */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            <Card>
              <CardHeader>
                <CardTitle>Professional Summary</CardTitle>
              </CardHeader>
              <CardContent>
                {candidate.summary ? (
                  <p className="text-gray-700 leading-relaxed whitespace-pre-line">{candidate.summary}</p>
                ) : (
                  <p className="text-gray-500 text-sm italic">No professional summary available. Run AI Analysis to generate one.</p>
                )}
              </CardContent>
            </Card>
          </motion.div>

          {/* Skills Matrix */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
          >
            <Card>
              <CardHeader>
                <CardTitle>Skills & Expertise</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {candidate.skills.map((skill) => (
                    <Badge key={skill} variant="primary" className="text-sm py-1.5 px-3">
                      {skill}
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>
          </motion.div>

          {/* Work Experience */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
          >
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Briefcase className="w-5 h-5" />
                  Work Experience
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-6">
                  {candidate.workHistory && candidate.workHistory.length > 0 ? (
                    candidate.workHistory.map((job, index) => (
                      <div
                        key={index}
                        className="relative pl-6 pb-6 border-l-2 border-gray-200 last:pb-0 last:border-l-0"
                      >
                        <div className="absolute left-0 top-0 w-3 h-3 -translate-x-[7px] rounded-full bg-primary-600 border-2 border-white"></div>
                        <h4 className="font-semibold text-gray-900">{job.title}</h4>
                        {(job.company || job.duration) && (
                          <p className="text-sm text-gray-600 mt-1">
                            {job.company}{job.company && job.duration ? ' · ' : ''}{job.duration}
                          </p>
                        )}
                        {job.description && (
                          <p className="text-sm text-gray-700 mt-2 leading-relaxed">{job.description}</p>
                        )}
                      </div>
                    ))
                  ) : (
                    <p className="text-gray-500 text-sm italic">No work experience data available from resume</p>
                  )}
                </div>
              </CardContent>
            </Card>
          </motion.div>

          {/* Education */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
          >
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <GraduationCap className="w-5 h-5" />
                  Education
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {candidate.education && candidate.education.length > 0 ? (
                    candidate.education.map((edu, index) => (
                      <div key={index}>
                        <h4 className="font-semibold text-gray-900">
                          {edu.degree}{edu.field ? ` in ${edu.field}` : ''}
                        </h4>
                        <p className="text-sm text-gray-600">
                          {edu.institution || 'Institution not specified'}{edu.year ? ` · ${edu.year}` : ''}
                        </p>
                      </div>
                    ))
                  ) : (
                    <p className="text-gray-500 text-sm italic">No education data available from resume</p>
                  )}
                </div>
              </CardContent>
            </Card>
          </motion.div>

          {/* Certifications */}
          {candidate.certifications && candidate.certifications.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.45 }}
            >
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Award className="w-5 h-5" />
                    Certifications
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {candidate.certifications.map((cert, index) => (
                      <div key={index} className="flex items-start gap-2">
                        <Award className="w-4 h-4 text-amber-500 mt-0.5 flex-shrink-0" />
                        <span className="text-sm text-gray-800">{cert}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          )}

          {/* Languages */}
          {candidate.languages && candidate.languages.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.5 }}
            >
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Globe className="w-5 h-5" />
                    Languages
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-2">
                    {candidate.languages.map((lang, index) => (
                      <Badge key={index} variant="outline" className="text-sm px-3 py-1 border-blue-200 bg-blue-50 text-blue-700">
                        <Globe className="w-3 h-3 mr-1" />
                        {lang}
                      </Badge>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          )}
        </div>

        {/* Right Column - AI Evaluation */}
        <div className="space-y-6">
          {/* Match Score */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.1 }}
          >
            <Card>
              <CardHeader>
                <CardTitle>Match Score</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-center">
                  <div
                    className={`text-6xl font-bold mb-2 ${getMatchScoreColor(
                      candidate.matchScore
                    )}`}
                  >
                    {(candidate.matchScore ?? 50).toFixed(1)}%
                  </div>
                  <Progress
                    value={candidate.matchScore}
                    className="h-3 mb-4"
                    indicatorClassName={
                      candidate.matchScore >= 80
                        ? 'bg-success'
                        : candidate.matchScore >= 60
                        ? 'bg-warning'
                        : 'bg-danger'
                    }
                  />
                  <Badge
                    variant={
                      candidate.status === 'Strong'
                        ? 'success'
                        : candidate.status === 'Partial'
                        ? 'warning'
                        : 'danger'
                    }
                    className="text-sm py-1.5 px-4"
                  >
                    {candidate.status} Match
                  </Badge>
                </div>
              </CardContent>
            </Card>
          </motion.div>

          {/* Quick Info */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.2 }}
          >
            <Card>
              <CardHeader>
                <CardTitle>Quick Info</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-600">Experience</span>
                  <span className="font-semibold text-gray-900">
                    {candidate.experience} years
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-600">Applied</span>
                  <span className="font-semibold text-gray-900">
                    {new Date(candidate.appliedDate).toLocaleDateString()}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-600">Location</span>
                  <span className="font-semibold text-gray-900">{candidate.location}</span>
                </div>
              </CardContent>
            </Card>
          </motion.div>

          {/* AI Evaluation */}
          {candidate.evaluation && (
            <motion.div
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.3 }}
            >
              <Card className="border-primary-200 bg-primary-50/30">
                <CardHeader>
                  <CardTitle className="text-primary-900">AI Evaluation</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <h4 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-2">
                      <CheckCircle className="w-4 h-4 text-success" />
                      Strengths
                    </h4>
                    <ul className="space-y-2">
                      {candidate.evaluation.strengths.map((strength, index) => (
                        <li key={index} className="text-sm text-gray-700 flex items-start gap-2">
                          <span className="w-1 h-1 bg-success rounded-full mt-2 flex-shrink-0"></span>
                          {strength}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div>
                    <h4 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-2">
                      <XCircle className="w-4 h-4 text-warning" />
                      Gaps
                    </h4>
                    <ul className="space-y-2">
                      {candidate.evaluation.gaps.map((gap, index) => (
                        <li key={index} className="text-sm text-gray-700 flex items-start gap-2">
                          <span className="w-1 h-1 bg-warning rounded-full mt-2 flex-shrink-0"></span>
                          {gap}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="pt-4 border-t border-primary-200">
                    <h4 className="text-sm font-semibold text-gray-900 mb-2">
                      Recommendation
                    </h4>
                    <p className="text-sm text-gray-700">{candidate.evaluation.recommendation}</p>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          )}

          {/* Actions */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.4 }}
          >
            <Card>
              <CardContent className="p-4 space-y-2">
                <Button variant="success" className="w-full" onClick={handleScheduleInterview}>
                  Schedule Interview
                </Button>
                <Button variant="outline" className="w-full" onClick={handleSendMessage}>
                  Send Message
                </Button>
                <Button variant="destructive" className="w-full" onClick={handleRejectCandidate}>
                  Reject Candidate
                </Button>
              </CardContent>
            </Card>
          </motion.div>
        </div>
      </div>
    </div>
  )
}
