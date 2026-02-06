import { useParams, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { useState } from 'react'
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
} from 'lucide-react'
import { useCandidates } from '@/hooks/useCandidates'
import { useCandidateStore } from '@/store/candidateStore'
import { useNotificationStore } from '@/store/notificationStore'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/Avatar'
import { Progress } from '@/components/ui/Progress'
import { getMatchScoreColor } from '@/lib/utils'
import config from '@/config'

// Category colors for visual distinction
const categoryColors: Record<string, { bg: string; text: string; border: string }> = {
  'Software Engineer': { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  'DevOps Engineer': { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' },
  'Data Scientist': { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
  'Marketing': { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200' },
  'Sales': { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
  'Product Manager': { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' },
  'HR': { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200' },
  'Finance': { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' },
  'Customer Support': { bg: 'bg-cyan-50', text: 'text-cyan-700', border: 'border-cyan-200' },
  'Design': { bg: 'bg-rose-50', text: 'text-rose-700', border: 'border-rose-200' },
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

  const candidate = candidates.find((c) => c.id === id)

  const handleToggleShortlist = () => {
    if (!candidate) return
    const wasShortlisted = isShortlisted(candidate.id)
    toggleShortlist(candidate.id)
    
    addNotification({
      type: wasShortlisted ? 'info' : 'success',
      title: wasShortlisted ? 'Removed from Shortlist' : 'Added to Shortlist',
      message: `${candidate.name} ${wasShortlisted ? 'removed from' : 'added to'} your shortlist`,
      actionUrl: wasShortlisted ? `/candidates/${candidate.id}` : '/shortlist'
    })
  }

  const handleDownloadResume = async () => {
    if (!candidate) return
    try {
      const response = await fetch(`${config.endpoints.candidates}/${candidate.id}/resume`)
      
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
      const response = await fetch(`${config.endpoints.ai}/analyze-match`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          candidate: {
            name: candidate.name,
            experience: candidate.experience,
            skills: candidate.skills,
            education: candidate.education,
            summary: candidate.summary,
            location: candidate.location
          },
          job_description: {
            title: "Senior Developer Position",
            required_skills: ["React", "TypeScript", "Python", "AWS", "PostgreSQL"],
            experience_required: "3-5 years",
            description: "Looking for experienced full-stack developer with strong frontend and backend skills"
          }
        })
      })

      if (!response.ok) {
        if (response.status === 503) {
          throw new Error('AI service not configured. Please add OpenAI API key.')
        }
        throw new Error('Failed to analyze candidate')
      }

      const analysis = await response.json()
      setAiAnalysis(analysis)
      
      addNotification({
        type: 'success',
        title: 'AI Analysis Complete',
        message: `Match score: ${analysis.score}% for ${candidate.name}`,
      })
    } catch (error: any) {
      console.error('AI Analysis error:', error)
      setAnalysisError(error.message || 'Failed to analyze candidate')
      
      // Fallback to simple analysis
      const fallbackScore = candidate.matchScore
      setAiAnalysis({
        score: fallbackScore,
        strengths: ['Strong skill set', 'Relevant experience', 'Good cultural fit'],
        gaps: ['Verify specific technical skills', 'Check availability'],
        recommendation: `Candidate shows ${fallbackScore}% match. Recommend proceeding with technical interview.`,
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
        const response = await fetch(`${config.endpoints.candidates}/${candidate.id}/status`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'Reject' })
        })
        
        if (!response.ok) {
          throw new Error('Failed to update candidate status')
        }
        
        addNotification({
          type: 'info',
          title: 'Candidate Rejected',
          message: `${candidate.name} has been marked as rejected`,
          actionUrl: '/candidates'
        })
        alert(`${candidate.name} has been marked as rejected.`)
        // In production, update local state or refresh candidate list
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
              </div>
              <div className="flex items-center gap-4 mt-2 text-gray-600">
                <span className="flex items-center gap-1">
                  <Mail className="w-4 h-4" />
                  {candidate.email}
                </span>
                <span className="flex items-center gap-1">
                  <Phone className="w-4 h-4" />
                  {candidate.phone}
                </span>
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
                  AI Analysis
                </>
              )}
            </Button>
            <Button variant="outline" onClick={handleToggleShortlist}>
              <Star className={`w-4 h-4 mr-2 ${shortlisted ? 'fill-yellow-400 text-yellow-400' : ''}`} />
              {shortlisted ? 'Shortlisted' : 'Add to Shortlist'}
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

      {/* AI Analysis Results */}
      {aiAnalysis && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-6"
        >
          <Card className="border-2 border-primary-200 bg-gradient-to-r from-primary-50 to-purple-50">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-primary-600" />
                AI Match Analysis
                {aiAnalysis.isFallback && (
                  <Badge variant="outline" className="ml-2 text-xs">Local AI</Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Match Score */}
              <div className="flex items-center justify-between p-4 bg-white rounded-lg">
                <div>
                  <p className="text-sm text-gray-600">Overall Match Score</p>
                  <p className="text-3xl font-bold text-primary-600">{aiAnalysis.score}%</p>
                </div>
                <TrendingUp className={`w-12 h-12 ${getMatchScoreColor(aiAnalysis.score)}`} />
              </div>

              {/* Strengths */}
              <div className="bg-white rounded-lg p-4">
                <h3 className="font-semibold text-gray-900 mb-2 flex items-center gap-2">
                  <CheckCircle className="w-5 h-5 text-green-600" />
                  Key Strengths
                </h3>
                <ul className="space-y-1.5">
                  {aiAnalysis.strengths.map((strength: string, idx: number) => (
                    <li key={idx} className="text-gray-700 flex items-start gap-2">
                      <span className="text-green-600 mt-1">•</span>
                      {strength}
                    </li>
                  ))}
                </ul>
              </div>

              {/* Gaps */}
              <div className="bg-white rounded-lg p-4">
                <h3 className="font-semibold text-gray-900 mb-2 flex items-center gap-2">
                  <AlertCircle className="w-5 h-5 text-amber-600" />
                  Areas to Address
                </h3>
                <ul className="space-y-1.5">
                  {aiAnalysis.gaps.map((gap: string, idx: number) => (
                    <li key={idx} className="text-gray-700 flex items-start gap-2">
                      <span className="text-amber-600 mt-1">•</span>
                      {gap}
                    </li>
                  ))}
                </ul>
              </div>

              {/* Recommendation */}
              <div className="bg-gradient-to-r from-primary-100 to-purple-100 rounded-lg p-4">
                <h3 className="font-semibold text-gray-900 mb-2">Recommendation</h3>
                <p className="text-gray-800">{aiAnalysis.recommendation}</p>
              </div>
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
                <p className="text-gray-700 leading-relaxed">{candidate.summary}</p>
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
                        <p className="text-sm text-gray-600 mt-1">
                          {job.company} · {job.duration}
                        </p>
                        <p className="text-sm text-gray-700 mt-2">{job.description}</p>
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
