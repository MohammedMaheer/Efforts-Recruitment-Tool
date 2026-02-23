import { useEffect, useCallback, useState, useRef } from 'react'
import { useCandidateStore } from '@/store/candidateStore'
import type { Candidate } from '@/types'
import { useAuthStore } from '@/store/authStore'
import config from '@/config'

// Clean up bad location values extracted from email body parsing
const cleanLocation = (loc: string | undefined | null): string => {
  if (!loc) return ''
  let cleaned = loc.trim()
  // Strip Arabic/non-Latin text in parentheses (e.g. UAE Arabic name)
  cleaned = cleaned.replace(/\s*\([^)]*[\u0600-\u06FF][^)]*\)\s*/g, '').trim()
  // Remove locations that are just common pronouns / noise words
  const noise = /^(you|me|us|we|they|them|him|her|i|my|your|our|here|there|null|undefined|n\/a|none|na|unknown|test|email|sir|madam|dear|hi|hello|the|a|an|from|to|for)$/i
  if (noise.test(cleaned)) return ''
  if (cleaned.length <= 1) return ''
  return cleaned
}

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

// Session-storage key & TTL for instant restores
const CACHE_KEY = 'candidates_cache'
const CACHE_TS_KEY = 'candidates_cache_ts'
const CACHE_TOTAL_KEY = 'candidates_cache_total'
const CACHE_TTL = 5 * 60 * 1000 // 5 minutes

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
  const abortControllerRef = useRef<AbortController | null>(null)

  const fetchCandidates = useCallback(async (skipCache = false) => {
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
      
      // --- Instant restore from sessionStorage (< 1ms) ---
      if (!skipCache) {
        try {
          const cachedTs = sessionStorage.getItem(CACHE_TS_KEY)
          if (cachedTs && Date.now() - Number(cachedTs) < CACHE_TTL) {
            const cached = sessionStorage.getItem(CACHE_KEY)
            if (cached) {
              const parsed = JSON.parse(cached) as Candidate[]
              if (parsed.length > 0) {
                setCandidates(parsed)
                const cachedTotal = Number(sessionStorage.getItem(CACHE_TOTAL_KEY)) || parsed.length
                setTotalCount(cachedTotal)
                setLoading(false)
                // Background refresh after instant render
                setTimeout(() => fetchCandidates(true), 500)
                return
              }
            }
          }
        } catch { /* ignore cache errors */ }
      }
      
      // Fetch with fields=light for ~3x smaller payload
      const pageSize = 500
      let allCandidates: Candidate[] = []
      let totalFromServer = 0
      
      // First page - fast initial render
      const firstResponse = await fetch(`${config.endpoints.candidates}?limit=${pageSize}&page=1&fields=light`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: controller.signal,
      })
      
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
      
      // Fetch remaining pages in parallel if there are more
      if (totalFromServer > pageSize && !controller.signal.aborted) {
        const totalPages = Math.ceil(totalFromServer / pageSize)
        const pagePromises = []
        for (let p = 2; p <= totalPages; p++) {
          pagePromises.push(
            fetch(`${config.endpoints.candidates}?limit=${pageSize}&page=${p}&fields=light`, {
              headers: token ? { Authorization: `Bearer ${token}` } : {},
              signal: controller.signal,
            }).then(r => r.ok ? r.json() : null)
          )
        }
        const results = await Promise.all(pagePromises)
        for (const data of results) {
          if (data) {
            const batch = (data.candidates || []).map(transformCandidate)
            if (batch.length > 0) allCandidates = [...allCandidates, ...batch]
          }
        }
        if (!controller.signal.aborted) {
          setCandidates(allCandidates)
        }
      }
      
      if (!controller.signal.aborted) {
        setTotalCount(totalFromServer)
        // Persist to sessionStorage for instant next load
        try {
          sessionStorage.setItem(CACHE_KEY, JSON.stringify(allCandidates))
          sessionStorage.setItem(CACHE_TS_KEY, String(Date.now()))
          sessionStorage.setItem(CACHE_TOTAL_KEY, String(totalFromServer))
        } catch { /* storage full — ignore */ }
      }
      
    } catch (err) {
      // Ignore abort errors — they're expected when a newer fetch supersedes
      if (err instanceof DOMException && err.name === 'AbortError') return
      const message = err instanceof Error ? err.message : 'Failed to fetch candidates'
      setError(message)
      console.error('Error fetching candidates:', err)
    } finally {
      setLoading(false)
    }
  }, [setCandidates])

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
  }, [autoFetch]) // eslint-disable-line react-hooks/exhaustive-deps

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
      const oneDayAgo = new Date()
      oneDayAgo.setDate(oneDayAgo.getDate() - 1)
      return date >= oneDayAgo
    }).length,
    recentUploads: candidates.filter(c => {
      const date = new Date(c.appliedDate)
      const oneDayAgo = new Date()
      oneDayAgo.setDate(oneDayAgo.getDate() - 1)
      return date >= oneDayAgo
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
