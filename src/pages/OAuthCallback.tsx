import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'
import config from '@/config'

export default function OAuthCallback() {
  const navigate = useNavigate()
  const token = useAuthStore((s) => s.token)
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
      window.history.replaceState({}, document.title, '/oauth/callback')

      try {
        // Exchange code for token
        const response = await fetch(`${config.apiUrl}/api/email/oauth2/callback`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: code,
            redirect_uri: `${window.location.origin}/oauth/callback`
          })
        })

        const data = await response.json()

        if (response.ok) {
          console.log('OAuth2 authentication successful!', data)
          
          // Trigger email sync in background (with auth header)
          try {
            await fetch(`${config.apiUrl}/api/email/sync-now`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
              },
            })
            console.log('Email sync triggered')
          } catch (syncError) {
            console.warn('Email sync trigger failed, will sync on next interval:', syncError)
          }
          
          console.log(`Successfully connected ${data.email}! Email sync started.`)
          navigate('/candidates')
        } else {
          console.error('OAuth2 error:', data)
          navigate('/settings')
        }
      } catch (error) {
        console.error('Error during OAuth callback:', error)
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
