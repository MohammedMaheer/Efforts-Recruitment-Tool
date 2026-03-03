import { useEffect, useRef, useCallback, useState } from 'react'
import { authFetch } from '@/lib/authFetch'
import config from '@/config'

interface SyncStatus {
  lastSyncTime: string | null
  candidateCount: number
  syncIntervalMinutes: number
  status: 'active' | 'error' | 'unknown'
  isNewDataAvailable: boolean
  nextSyncTime: string | null
}

/**
 * Hook that polls the email sync status endpoint and triggers a callback
 * whenever new candidates are detected. This enables near-real-time
 * auto-refresh when the email scraper picks up new emails.
 * 
 * Clears sessionStorage cache on new data so useCandidates fetches fresh data.
 * 
 * @param onNewCandidates - Called when the candidate count increases
 * @param pollIntervalMs - How often to check for new candidates (default: 20s)
 */
export function useEmailSync(
  onNewCandidates?: () => void,
  pollIntervalMs: number = 20000
) {
  const [syncStatus, setSyncStatus] = useState<SyncStatus>({
    lastSyncTime: null,
    candidateCount: 0,
    syncIntervalMinutes: 2,
    status: 'unknown',
    isNewDataAvailable: false,
    nextSyncTime: null,
  })
  
  const lastKnownCount = useRef<number>(0)
  const isFirstCheck = useRef(true)
  const consecutiveErrors = useRef<number>(0)
  const currentInterval = useRef<number>(pollIntervalMs)

  // Use ref for callback to keep checkSyncStatus stable
  const callbackRef = useRef(onNewCandidates)
  callbackRef.current = onNewCandidates

  const checkSyncStatus = useCallback(async () => {
    try {
      const response = await authFetch(`${config.apiUrl}/api/email/sync-status`, {
        signal: AbortSignal.timeout(8000),
      })
      
      if (!response.ok) {
        consecutiveErrors.current++
        return
      }
      
      consecutiveErrors.current = 0
      currentInterval.current = pollIntervalMs  // Reset interval on success
      const data = await response.json()
      const newCount = data.candidate_count || 0
      
      // Detect if new candidates were added since last check
      const hasNewData = !isFirstCheck.current && newCount > lastKnownCount.current
      
      setSyncStatus({
        lastSyncTime: data.last_sync_time || null,
        candidateCount: newCount,
        syncIntervalMinutes: data.sync_interval_minutes || 2,
        status: data.status || 'unknown',
        isNewDataAvailable: hasNewData,
        nextSyncTime: data.next_sync_time || null,
      })
      
      // If new candidates detected, clear cache and trigger callback
      if (hasNewData) {
        // Clear sessionStorage cache so useCandidates fetches fresh data
        try {
          sessionStorage.removeItem('candidates_cache')
          sessionStorage.removeItem('candidates_cache_ts')
          sessionStorage.removeItem('candidates_cache_total')
        } catch { /* ignore */ }
        
        if (callbackRef.current) {
          callbackRef.current()
        }
      }
      
      lastKnownCount.current = newCount
      isFirstCheck.current = false
    } catch {
      consecutiveErrors.current++
      // Exponential backoff: double interval after 3+ consecutive errors (max 5 min)
      if (consecutiveErrors.current > 3) {
        currentInterval.current = Math.min(currentInterval.current * 2, 300000)
      }
    }
  }, [pollIntervalMs])

  useEffect(() => {
    // Initial check
    checkSyncStatus()
    
    // Poll periodically — use adaptive interval with exponential backoff
    let timerId: ReturnType<typeof setTimeout>
    const scheduleNext = () => {
      timerId = setTimeout(() => {
        checkSyncStatus().finally(scheduleNext)
      }, currentInterval.current)
    }
    scheduleNext()
    return () => clearTimeout(timerId)
  }, [checkSyncStatus, pollIntervalMs])

  const syncTimersRef = useRef<ReturnType<typeof setTimeout>[]>([])

  const triggerSync = useCallback(async () => {
    try {
      await authFetch(`${config.apiUrl}/api/email/sync-now`, {
        method: 'POST',
      })
      // Check more frequently right after triggering sync
      // Clear any previous sync timers first
      syncTimersRef.current.forEach(t => clearTimeout(t))
      syncTimersRef.current = [
        setTimeout(checkSyncStatus, 3000),
        setTimeout(checkSyncStatus, 8000),
        setTimeout(checkSyncStatus, 15000),
      ]
    } catch {
      // Silently ignore
    }
  }, [checkSyncStatus])

  // Clean up sync timers on unmount
  useEffect(() => {
    return () => {
      syncTimersRef.current.forEach(t => clearTimeout(t))
    }
  }, [])

  return { syncStatus, triggerSync, checkSyncStatus }
}

export default useEmailSync
