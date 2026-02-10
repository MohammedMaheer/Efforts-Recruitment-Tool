import { useEffect, useRef, useCallback, useState } from 'react'
import { useAuthStore } from '@/store/authStore'
import config from '@/config'

interface SyncStatus {
  lastSyncTime: string | null
  candidateCount: number
  syncIntervalMinutes: number
  status: 'active' | 'error' | 'unknown'
  isNewDataAvailable: boolean
}

/**
 * Hook that polls the email sync status endpoint and triggers a callback
 * whenever new candidates are detected. This enables near-real-time
 * auto-refresh when the email scraper picks up new emails.
 * 
 * @param onNewCandidates - Called when the candidate count increases
 * @param pollIntervalMs - How often to check for new candidates (default: 30s)
 */
export function useEmailSync(
  onNewCandidates?: () => void,
  pollIntervalMs: number = 30000
) {
  const [syncStatus, setSyncStatus] = useState<SyncStatus>({
    lastSyncTime: null,
    candidateCount: 0,
    syncIntervalMinutes: 2,
    status: 'unknown',
    isNewDataAvailable: false,
  })
  
  const lastKnownCount = useRef<number>(0)
  const isFirstCheck = useRef(true)

  const checkSyncStatus = useCallback(async () => {
    try {
      const token = useAuthStore.getState().token
      const response = await fetch(`${config.apiUrl}/api/email/sync-status`, {
        signal: AbortSignal.timeout(5000),
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      
      if (!response.ok) return
      
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
      })
      
      // Trigger callback if new candidates detected
      if (hasNewData && onNewCandidates) {
        onNewCandidates()
      }
      
      lastKnownCount.current = newCount
      isFirstCheck.current = false
    } catch {
      // Silently ignore - sync status is optional
    }
  }, [onNewCandidates])

  useEffect(() => {
    // Initial check
    checkSyncStatus()
    
    // Poll periodically
    const interval = setInterval(checkSyncStatus, pollIntervalMs)
    return () => clearInterval(interval)
  }, [checkSyncStatus, pollIntervalMs])

  const triggerSync = useCallback(async () => {
    try {
      const token = useAuthStore.getState().token
      await fetch(`${config.apiUrl}/api/email/sync-now`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      // Check again after a short delay
      setTimeout(checkSyncStatus, 3000)
    } catch {
      // Silently ignore
    }
  }, [checkSyncStatus])

  return { syncStatus, triggerSync, checkSyncStatus }
}

export default useEmailSync
