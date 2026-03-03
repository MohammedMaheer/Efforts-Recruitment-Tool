import { useState, useEffect, useCallback } from 'react';
import config from '@/config';
import { useAuthStore } from '@/store/authStore';

interface AIStatus {
  available: boolean
  model: string | null
  message: string
  isLoading: boolean
}

export function useAIStatus() {
  const [status, setStatus] = useState<AIStatus>({
    available: false,
    model: null,
    message: 'Checking AI service...',
    isLoading: true
  })

  const checkAIStatus = useCallback(async () => {
    try {
      const token = useAuthStore.getState().token
      const response = await fetch(`${config.endpoints.ai}/status`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (response.ok) {
        const data = await response.json()
        setStatus({
          ...data,
          isLoading: false
        })
      } else {
        setStatus({
          available: false,
          model: null,
          message: 'AI service unavailable',
          isLoading: false
        })
      }
    } catch (error) {
      console.error('AI status check failed:', error)
      setStatus({
        available: false,
        model: null,
        message: 'Backend not connected',
        isLoading: false
      })
    }
  }, [])

  useEffect(() => {
    checkAIStatus()
  }, [checkAIStatus])

  return { ...status, refresh: checkAIStatus }
}
