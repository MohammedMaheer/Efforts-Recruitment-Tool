import { useEffect, useCallback, useState, useRef, useMemo } from 'react'
import { useCandidateStore } from '@/store/candidateStore'
import type { Candidate } from '@/types'
import { useAuthStore } from '@/store/authStore'
import config from '@/config'
import { cleanLocation } from '@/lib/utils'

/** Timeout per-fetch request (ms). Prevents hanging when backend is under load. */
const FETCH_TIMEOUT_MS = 30_000

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
const parseJSON = <T = unknown>(value: unknown, fallback: T[] = []): T[] => {
  if (!value) return fallback
  if (Array.isArray(value)) return value as T[]
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed as T[] : fallback
    } catch {
      return fallback
    }
  }
  return fallback
}

/** Raw candidate shape from the backend API */
interface RawCandidate {
  id: string
  name?: string
  email?: string
  phone?: string
  location?: string
  experience?: number
  experience_years?: number
  matchScore?: number
  ai_score?: number
  match_score?: number
  status?: string
  candidate_status?: string
  skills?: string | string[]
  appliedDate?: string
  applied_date?: string
  created_at?: string
  summary?: string
  raw_text?: string
  education?: string | unknown[]
  workHistory?: string | unknown[]
  work_history?: string | unknown[]
  hasResume?: boolean
  job_category?: string
  linkedin?: string
  strengths?: string | string[]
  gaps?: string | string[]
  certifications?: string | string[]
  languages?: string | string[]
  resume_text?: string
  resumeText?: string
  ai_analysis?: Record<string, unknown>
  aiAnalysis?: Record<string, unknown>
  isShortlisted?: boolean
  [key: string]: unknown
}

// Transform API candidate to store format
const transformCandidate = (c: RawCandidate): Candidate => {
  // Parse skills (can be JSON string or array)
  const skills = parseJSON(c.skills, [])
  
  // Parse education (stored as JSON string in DB)
  const rawEducation = parseJSON(c.education, []) as Record<string, string>[]
  const education = rawEducation.map((edu) => ({
    degree: edu.degree || edu.title || '',
    field: edu.field || '',  // Include field of study
    institution: edu.institution || edu.school || '',
    year: edu.year || edu.graduation_year || ''
  }))
  
  // Parse work history (stored as workHistory or work_history)
  const rawWorkHistory = parseJSON(c.workHistory || c.work_history, []) as Record<string, string>[]
  const workHistory = rawWorkHistory.map((job) => ({
    title: job.title || job.position || '',
    company: job.company || job.organization || '',
    duration: job.duration || job.period || job.years || '',
    description: job.description || job.responsibilities || ''
  }))
  
  // Get experience (can be experience or experience_years)
  const experience = c.experience || c.experience_years || 0
  
  // Get match score (matchScore from DB, or ai_score, or match_score) - ENSURE IT'S A NUMBER
  const rawScore = c.matchScore ?? c.ai_score ?? c.match_score ?? 0
  const matchScore = typeof rawScore === 'number' ? rawScore : parseFloat(rawScore) ?? 0
  
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
    location: cleanLocation(c.location),
    experience,
    matchScore,
    status,
    skills,
    resumeUrl: '',
    appliedDate: c.appliedDate || c.applied_date || c.created_at || new Date().toISOString(),
    summary: c.summary || c.raw_text?.substring(0, 300) || '',
    education,
    workHistory,
    hasResume: c.hasResume ?? (c as any).has_resume ?? false,
    jobCategory: c.job_category || 'General',
    jobSubcategory: (c as any).job_subcategory || (c as any).jobSubcategory || '',
    linkedin: c.linkedin || '',
    evaluation: {
      strengths: parseJSON<string>(c.strengths, []),
      gaps: parseJSON<string>(c.gaps, []),
      recommendation: (c as any).recommendation || (c as any).hiring_recommendation || c.job_category || 'General'
    },
    certifications: parseJSON<string>(c.certifications, []),
    languages: parseJSON<string>(c.languages, []),
    resumeText: c.resume_text || c.resumeText || '',
    aiAnalysis: (c.ai_analysis || c.aiAnalysis || undefined) as import('@/types').AIAnalysisResult | undefined,
  }
}

export function useCandidates(options: UseCandidatesOptions = {}): UseCandidatesReturn {
  const { autoFetch = true, refreshInterval = null } = options
  
  const candidates = useCandidateStore((state) => state.candidates)
  const setCandidates = useCandidateStore((state) => state.setCandidates)
  
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [totalCount, setTotalCount] = useState(0)
  const abortControllerRef = useRef<AbortController | null>(null)

  /** Create a combined AbortSignal from the controller + a timeout */
  const fetchWithTimeout = useCallback(
    (url: string, opts: RequestInit, timeoutMs = FETCH_TIMEOUT_MS): Promise<Response> => {
      // Use AbortSignal.any if available, otherwise manual timeout
      if (typeof AbortSignal !== 'undefined' && 'any' in AbortSignal) {
        const timeoutSignal = AbortSignal.timeout(timeoutMs)
        const combined = AbortSignal.any([opts.signal as AbortSignal, timeoutSignal])
        return fetch(url, { ...opts, signal: combined })
      }
      // Fallback: manual timeout via setTimeout
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          reject(new Error('Request timed out'))
        }, timeoutMs)
        fetch(url, opts)
          .then(resolve, reject)
          .finally(() => clearTimeout(timer))
      })
    },
    [],
  )

  const fetchCandidates = useCallback(async (_skipCache = false) => {
    // Cancel any in-flight fetch to prevent race conditions
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    const controller = new AbortController()
    abortControllerRef.current = controller

    setLoading(true)
    setError(null)
    
    try {
      const token = useAuthStore.getState().token
      const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {}
      
      // Page size matches backend hard cap (min(500, limit)) — requesting more is silently truncated
      const pageSize = 500
      let allCandidates: Candidate[] = []
      let totalFromServer = 0
      
      // First page — renders immediately, sets initial data
      const firstResponse = await fetchWithTimeout(
        `${config.endpoints.candidates}?limit=${pageSize}&page=1&fields=light`,
        { headers, signal: controller.signal, cache: 'no-store' as RequestCache },
      )
      
      // 401 is now handled centrally by authFetch — no duplicate handling needed
      if (!firstResponse.ok) {
        throw new Error(`Failed to fetch candidates: ${firstResponse.statusText}`)
      }
      
      const firstData = await firstResponse.json()
      const firstBatch = (firstData.candidates || []).map(transformCandidate)
      totalFromServer = firstData.total || firstBatch.length
      allCandidates = firstBatch
      
      // Show first page immediately
      if (!controller.signal.aborted) {
        setCandidates(allCandidates)
        setTotalCount(totalFromServer)
      }
      
      // Fetch remaining pages (if any) with Promise.allSettled to handle partial failures
      if (totalFromServer > pageSize && !controller.signal.aborted) {
        const totalPages = Math.ceil(totalFromServer / pageSize)
        const pagePromises = []
        for (let p = 2; p <= totalPages; p++) {
          pagePromises.push(
            fetchWithTimeout(
              `${config.endpoints.candidates}?limit=${pageSize}&page=${p}&fields=light`,
              { headers, signal: controller.signal, cache: 'no-store' as RequestCache },
            ).then(r => (r.ok ? r.json() : null)),
          )
        }

        // allSettled never rejects — partial data is better than no data
        const results = await Promise.allSettled(pagePromises)
        let failedPages = 0
        for (const result of results) {
          if (result.status === 'fulfilled' && result.value) {
            const batch = (result.value.candidates || []).map(transformCandidate)
            if (batch.length > 0) allCandidates = [...allCandidates, ...batch]
          } else {
            failedPages++
          }
        }
        if (!controller.signal.aborted) {
          setCandidates(allCandidates)
          if (failedPages > 0) {
            console.warn(`${failedPages} page(s) failed to load — showing partial data`)
          }
        }
      }
      
      if (!controller.signal.aborted) {
        setTotalCount(totalFromServer)
      }
      
    } catch (err) {
      // Ignore abort errors — they're expected when a newer fetch supersedes or on unmount
      if (err instanceof DOMException && (err.name === 'AbortError' || err.name === 'TimeoutError')) return
      const message = err instanceof Error ? err.message : 'Failed to fetch candidates'
      setError(message)
      console.error('Error fetching candidates:', err)
    } finally {
      setLoading(false)
    }
  }, [setCandidates, fetchWithTimeout])

  // Initial fetch — always fetch if empty, and also refetch on mount
  useEffect(() => {
    if (autoFetch) {
      // If we have cached candidates, show them immediately but refresh in background
      if (candidates.length > 0) {
        fetchCandidates() // Will use session cache for instant render, then background fetch
      } else {
        fetchCandidates()
      }
    }
    return () => {
      // Cleanup: abort in-flight requests on unmount
      if (abortControllerRef.current) abortControllerRef.current.abort()
    }
  }, [autoFetch, fetchCandidates])

  // Refresh interval
  useEffect(() => {
    if (!refreshInterval) return
    
    const interval = setInterval(fetchCandidates, refreshInterval)
    return () => clearInterval(interval)
  }, [refreshInterval, fetchCandidates])

  // Calculate stats (memoized to avoid recomputing on every render)
  const stats = useMemo(() => ({
    total: candidates.length,
    totalCandidates: candidates.length,
    strong: candidates.filter(c => c.status === 'Strong').length,
    strongMatches: candidates.filter(c => c.status === 'Strong').length,
    partial: candidates.filter(c => c.status === 'Partial').length,
    reject: candidates.filter(c => c.status === 'Reject').length,
    avgScore: candidates.length > 0 
      ? Math.round(candidates.reduce((sum, c) => sum + c.matchScore, 0) / candidates.length)
      : 0,
    averageScore: candidates.length > 0 
      ? Math.round(candidates.reduce((sum, c) => sum + c.matchScore, 0) / candidates.length)
      : 0,
    recentCount: candidates.filter(c => {
      const date = new Date(c.appliedDate)
      const oneDayAgo = new Date()
      oneDayAgo.setDate(oneDayAgo.getDate() - 1)
      return date >= oneDayAgo
    }).length,
    recentUploads: candidates.filter(c => {
      const date = new Date(c.appliedDate)
      const oneDayAgo = new Date()
      oneDayAgo.setDate(oneDayAgo.getDate() - 1)
      return date >= oneDayAgo
    }).length,
  }), [candidates])

  return {
    candidates,
    loading,
    error,
    totalCount,
    refetch: () => fetchCandidates(),
    stats
  }
}

export default useCandidates
