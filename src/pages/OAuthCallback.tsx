import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authFetch } from '@/lib/authFetch'
import config from '@/config'
import { toast } from '@/components/ui/Toast'
import { useAuthStore } from '@/store/authStore'

export default function OAuthCallback() {
  const navigate = useNavigate()
  const { user, verifyToken } = useAuthStore()
  const isAdmin = user?.role === 'admin'
  const hasProcessed = useRef(false)  // Prevent duplicate requests
  const [hydrated, setHydrated] = useState(
    // Zustand persist: check if already hydrated (synchronous on first call if storage is sync)
    () => useAuthStore.persist?.hasHydrated?.() ?? true
  )

  // Wait for Zustand to finish rehydrating from sessionStorage before processing.
  // On a fresh page load (after Microsoft OAuth redirect) the store may not be
  // hydrated yet when the component first mounts — causing a false "no token" read.
  useEffect(() => {
    if (hydrated) return
    return useAuthStore.persist.onFinishHydration(() => setHydrated(true))
  }, [hydrated])

  useEffect(() => {
    if (!hydrated) return  // Wait for sessionStorage hydration first

    const handleCallback = async () => {
      // Prevent duplicate calls (React StrictMode calls useEffect twice)
      if (hasProcessed.current) {
        return
      }
      hasProcessed.current = true

      // Get the authorization code from URL
      const urlParams = new URLSearchParams(window.location.search)
      const code = urlParams.get('code')

      if (!code) {
        console.error('No authorization code found')
        navigate('/settings')
        return
      }

      // Clear the URL to prevent re-use of the code
      window.history.replaceState({}, document.title, '/auth/callback')

      // Now that the store is hydrated, read the token directly.
      let activeToken = useAuthStore.getState().token
      if (!activeToken) {
        // Still no token after hydration — try a full verify round-trip
        const verified = await verifyToken()
        activeToken = useAuthStore.getState().token
        if (!verified || !activeToken) {
          toast.error('Session Expired', 'Please log in again and reconnect Microsoft.')
          navigate('/login')
          return
        }
      }

      try {
        // Exchange code for token — redirect_uri MUST match the one sent during authorization
        const response = await authFetch(`${config.apiUrl}/api/email/oauth2/callback`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code,
            redirect_uri: `${window.location.origin}/auth/callback`
          })
        })

        if (response.ok) {
          // Trigger email sync in background — admin only (sync-now requires admin)
          if (isAdmin) {
            try {
              await authFetch(`${config.apiUrl}/api/email/sync-now`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
              })
            } catch (syncError) {
              console.warn('Email sync trigger failed, will sync on next interval:', syncError)
            }
          }

          navigate('/candidates')
        } else {
          const errorBody = await response.json().catch(() => ({}))
          const errorMsg = errorBody?.detail || `Authentication failed (HTTP ${response.status})`
          console.error('OAuth2 callback failed:', response.status, errorMsg)
          toast.error('Microsoft Login Failed', errorMsg)
          navigate('/settings')
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : 'Network error during authentication'
        console.error('Error during OAuth callback:', error)
        toast.error('Microsoft Login Failed', msg)
        navigate('/settings')
      }
    }

    handleCallback()
  }, [navigate, isAdmin, hydrated, verifyToken])

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-sky-600 mx-auto"></div>
        <p className="mt-4 text-gray-600">Completing authentication...</p>
        <p className="mt-2 text-sm text-gray-500">Setting up email sync...</p>
      </div>
    </div>
  )
}

