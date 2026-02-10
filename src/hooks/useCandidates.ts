import { useEffect, useCallback, useState } from 'react'
import { useCandidateStore, type Candidate } from '@/store/candidateStore'
import { useAuthStore } from '@/store/authStore'
import config from '@/config'

interface UseCandidatesOptions {
  autoFetch?: boolean
  refreshInterval?: number | null // in milliseconds, null to disable
}

interface UseCandidatesReturn {
  candidates: Candidate[]
  loading: boolean
  error: string | null
  totalCount: number
  refetch: () => Promise<void>
  stats: {
    total: number
    strong: number
    partial: number
    reject: number
    avgScore: number
    recentCount: number
  }
}

// Parse JSON string safely
const parseJSON = (value: any, fallback: any[] = []): any => {
  if (!value) return fallback
  if (Array.isArray(value)) return value
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed : fallback
    } catch {
      return fallback
    }
  }
  return fallback
}

// Transform API candidate to store format
const transformCandidate = (c: any): Candidate => {
  // Parse skills (can be JSON string or array)
  const skills = parseJSON(c.skills, [])
  
  // Parse education (stored as JSON string in DB)
  const rawEducation = parseJSON(c.education, [])
  const education = rawEducation.map((edu: any) => ({
    degree: edu.degree || edu.title || '',
    field: edu.field || '',  // Include field of study
    institution: edu.institution || edu.school || '',
    year: edu.year || edu.graduation_year || ''
  }))
  
  // Parse work history (stored as workHistory or work_history)
  const rawWorkHistory = parseJSON(c.workHistory || c.work_history, [])
  const workHistory = rawWorkHistory.map((job: any) => ({
    title: job.title || job.position || '',
    company: job.company || job.organization || '',
    duration: job.duration || job.period || job.years || '',
    description: job.description || job.responsibilities || ''
  }))
  
  // Get experience (can be experience or experience_years)
  const experience = c.experience || c.experience_years || 0
  
  // Get match score (matchScore from DB, or ai_score, or match_score) - ENSURE IT'S A NUMBER
  const rawScore = c.matchScore ?? c.ai_score ?? c.match_score ?? 50
  const matchScore = typeof rawScore === 'number' ? rawScore : parseFloat(rawScore) || 50
  
  // Determine status - use backend status if available, otherwise derive from score
  const backendStatus = c.status || c.candidate_status || ''
  const validStatuses = ['New', 'Reviewed', 'Shortlisted', 'Interviewing', 'Offered', 'Hired', 'Rejected', 'Withdrawn', 'Strong', 'Partial', 'Reject']
  const status = validStatuses.includes(backendStatus) ? backendStatus as any :
                 (matchScore >= 70 ? 'Strong' as const : 
                 matchScore >= 40 ? 'Partial' as const : 'Reject' as const)
  
  return {
    id: c.id,
    name: c.name || 'Unknown',
    email: c.email || '',
    phone: c.phone || '',
    location: c.location || '',
    experience,
    matchScore,
    status,
    skills,
    resumeUrl: '',
    appliedDate: c.appliedDate || c.applied_date || c.created_at || new Date().toISOString(),
    summary: c.summary || c.raw_text?.substring(0, 300) || '',
    education,
    workHistory,
    hasResume: c.hasResume || false,
    jobCategory: c.job_category || 'General',
    linkedin: c.linkedin || '',
    evaluation: {
      strengths: parseJSON(c.strengths, []),
      gaps: parseJSON(c.gaps, []),
      recommendation: c.job_category || 'General'
    },
    certifications: parseJSON(c.certifications, []),
    languages: parseJSON(c.languages, []),
    resumeText: c.resume_text || c.resumeText || '',
    aiAnalysis: c.ai_analysis || c.aiAnalysis || null,
  }
}

export function useCandidates(options: UseCandidatesOptions = {}): UseCandidatesReturn {
  const { autoFetch = true, refreshInterval = null } = options
  
  const candidates = useCandidateStore((state) => state.candidates)
  const setCandidates = useCandidateStore((state) => state.setCandidates)
  
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [totalCount, setTotalCount] = useState(0)

  const fetchCandidates = useCallback(async () => {
    setLoading(true)
    setError(null)
    
    try {
      const token = useAuthStore.getState().token
      const response = await fetch(`${config.endpoints.candidates}?limit=10000`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      
      if (!response.ok) {
        throw new Error(`Failed to fetch candidates: ${response.statusText}`)
      }
      
      const data = await response.json()
      const transformedCandidates = (data.candidates || []).map(transformCandidate)
      
      setCandidates(transformedCandidates)
      setTotalCount(data.total || transformedCandidates.length)
      
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch candidates'
      setError(message)
      console.error('Error fetching candidates:', err)
    } finally {
      setLoading(false)
    }
  }, [setCandidates])

  // Initial fetch
  useEffect(() => {
    if (autoFetch && candidates.length === 0) {
      fetchCandidates()
    }
  }, [autoFetch, fetchCandidates, candidates.length])

  // Refresh interval
  useEffect(() => {
    if (!refreshInterval) return
    
    const interval = setInterval(fetchCandidates, refreshInterval)
    return () => clearInterval(interval)
  }, [refreshInterval, fetchCandidates])

  // Calculate stats
  const stats = {
    total: candidates.length,
    totalCandidates: candidates.length,  // Alias for Dashboard compatibility
    strong: candidates.filter(c => c.status === 'Strong').length,
    strongMatches: candidates.filter(c => c.status === 'Strong').length,  // Alias
    partial: candidates.filter(c => c.status === 'Partial').length,
    reject: candidates.filter(c => c.status === 'Reject').length,
    avgScore: candidates.length > 0 
      ? Math.round(candidates.reduce((sum, c) => sum + c.matchScore, 0) / candidates.length)
      : 0,
    averageScore: candidates.length > 0 
      ? Math.round(candidates.reduce((sum, c) => sum + c.matchScore, 0) / candidates.length)
      : 0,  // Alias
    recentCount: candidates.filter(c => {
      const date = new Date(c.appliedDate)
      const weekAgo = new Date()
      weekAgo.setDate(weekAgo.getDate() - 7)
      return date >= weekAgo
    }).length,
    recentUploads: candidates.filter(c => {
      const date = new Date(c.appliedDate)
      const weekAgo = new Date()
      weekAgo.setDate(weekAgo.getDate() - 7)
      return date >= weekAgo
    }).length  // Alias
  }

  return {
    candidates,
    loading,
    error,
    totalCount,
    refetch: fetchCandidates,
    stats
  }
}

export default useCandidates
