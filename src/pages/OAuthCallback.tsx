import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { authFetch } from '@/lib/authFetch'
import config from '@/config'
import { toast } from '@/components/ui/Toast'

export default function OAuthCallback() {
  const navigate = useNavigate()
  const hasProcessed = useRef(false)  // Prevent duplicate requests

  useEffect(() => {
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
          // Trigger email sync in background using authFetch (auto-injects auth header)
          try {
            await authFetch(`${config.apiUrl}/api/email/sync-now`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
            })
          } catch (syncError) {
            console.warn('Email sync trigger failed, will sync on next interval:', syncError)
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
  }, [navigate])

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
