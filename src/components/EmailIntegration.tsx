import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { Mail, CheckCircle, AlertCircle, Loader, RefreshCw, Settings as SettingsIcon, Shield, Clock, Zap, AlertTriangle, ExternalLink } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { useNotificationStore } from '@/store/notificationStore'
import config from '@/config'

interface EmailProvider {
  id: string
  name: string
  requires_app_password: boolean
  supports_oauth: boolean
  enterprise_ready?: boolean
  instructions: string
}

interface OAuthStatus {
  is_configured: boolean
  auth_status: string
  sync_status: string
  primary_email?: string
  last_sync?: string
  next_sync?: string
  sync_interval_minutes?: number
  needs_manual_auth?: boolean
  auth_url?: string
  stats?: {
    total_syncs: number
    successful_syncs: number
    failed_syncs: number
    token_refreshes: number
    emails_processed: number
    candidates_added: number
  }
}

export default function EmailIntegration() {
  const navigate = useNavigate()
  const addNotification = useNotificationStore((state) => state.addNotification)
  const [selectedProvider, setSelectedProvider] = useState<string>('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isConnecting, setIsConnecting] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState<'idle' | 'connected' | 'error'>('idle')
  const [syncResult, setSyncResult] = useState<any>(null)
  
  // OAuth Automation Status
  const [oauthStatus, setOauthStatus] = useState<OAuthStatus | null>(null)
  const [isRefreshingToken, setIsRefreshingToken] = useState(false)
  const [isManualSyncing, setIsManualSyncing] = useState(false)

  // Fetch OAuth automation status on mount and periodically
  useEffect(() => {
    fetchOAuthStatus()
    const interval = setInterval(fetchOAuthStatus, 30000) // Update every 30 seconds
    return () => clearInterval(interval)
  }, [])

  const fetchOAuthStatus = async () => {
    try {
      const response = await fetch(`${config.apiUrl}/api/oauth/status`)
      if (response.ok) {
        const data = await response.json()
        setOauthStatus(data)
      }
    } catch (error) {
      console.error('Failed to fetch OAuth status:', error)
    }
  }

  const handleForceRefresh = async () => {
    setIsRefreshingToken(true)
    try {
      const response = await fetch(`${config.apiUrl}/api/oauth/refresh`, { method: 'POST' })
      const data = await response.json()
      
      if (data.status === 'success') {
        addNotification({
          type: 'success',
          title: 'Token Refreshed',
          message: 'OAuth2 token refreshed successfully'
        })
        await fetchOAuthStatus()
      } else if (data.needs_manual_auth && data.auth_url) {
        window.open(data.auth_url, '_blank')
      } else {
        addNotification({
          type: 'error',
          title: 'Refresh Failed',
          message: data.message || 'Failed to refresh token'
        })
      }
    } catch (error) {
      addNotification({
        type: 'error',
        title: 'Error',
        message: 'Failed to refresh token'
      })
    } finally {
      setIsRefreshingToken(false)
    }
  }

  const handleManualSync = async () => {
    setIsManualSyncing(true)
    try {
      const response = await fetch(`${config.apiUrl}/api/email/manual-sync`, { method: 'POST' })
      const data = await response.json()
      
      if (data.status === 'syncing') {
        addNotification({
          type: 'success',
          title: 'Sync Started',
          message: 'Manual email sync started in background'
        })
      } else if (data.status === 'needs_auth' && data.auth_url) {
        addNotification({
          type: 'warning',
          title: 'Authentication Required',
          message: 'Please authenticate to sync emails'
        })
        window.open(data.auth_url, '_blank')
      } else {
        addNotification({
          type: 'error',
          title: 'Sync Failed',
          message: data.message || 'Failed to start sync'
        })
      }
      
      await fetchOAuthStatus()
    } catch (error) {
      addNotification({
        type: 'error',
        title: 'Error',
        message: 'Failed to trigger manual sync'
      })
    } finally {
      setIsManualSyncing(false)
    }
  }

  const getAuthStatusColor = (status: string) => {
    switch (status) {
      case 'valid': return 'bg-green-100 text-green-800'
      case 'expired': return 'bg-yellow-100 text-yellow-800'
      case 'refreshing': return 'bg-blue-100 text-blue-800'
      case 'needs_reauth': return 'bg-red-100 text-red-800'
      default: return 'bg-gray-100 text-gray-800'
    }
  }

  const getSyncStatusColor = (status: string) => {
    switch (status) {
      case 'success': return 'bg-green-100 text-green-800'
      case 'syncing': return 'bg-blue-100 text-blue-800'
      case 'failed': return 'bg-red-100 text-red-800'
      case 'waiting_auth': return 'bg-yellow-100 text-yellow-800'
      default: return 'bg-gray-100 text-gray-800'
    }
  }

  const formatDateTime = (isoString: string | undefined) => {
    if (!isoString) return 'Never'
    const date = new Date(isoString)
    return date.toLocaleString()
  }

  const providers: EmailProvider[] = [
    {
      id: 'outlook',
      name: 'Outlook / Office 365',
      requires_app_password: false,
      supports_oauth: true,
      enterprise_ready: true,
      instructions: 'Enterprise OAuth2 integration - secure and recommended'
    },
    {
      id: 'gmail',
      name: 'Gmail',
      requires_app_password: true,
      supports_oauth: true,
      instructions: 'Enable 2FA and create app password at myaccount.google.com/apppasswords'
    },
    {
      id: 'yahoo',
      name: 'Yahoo Mail',
      requires_app_password: true,
      supports_oauth: false,
      instructions: 'Create app password in Yahoo account security settings'
    },
    {
      id: 'icloud',
      name: 'iCloud Mail',
      requires_app_password: true,
      supports_oauth: false,
      instructions: 'Generate app-specific password at appleid.apple.com'
    },
    {
      id: 'custom',
      name: 'Custom IMAP',
      requires_app_password: false,
      supports_oauth: false,
      instructions: 'Connect any IMAP-compatible email server'
    }
  ]

  const handleConnect = async () => {
    setIsConnecting(true)
    try {
      const response = await fetch(`${config.apiUrl}/api/email/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: selectedProvider,
          email: email,
          password: password
        })
      })
      
      if (response.ok) {
        setConnectionStatus('connected')
        addNotification({
          type: 'success',
          title: 'Email Connected',
          message: `Successfully connected to ${selectedProvider}`,
        })
      } else {
        setConnectionStatus('error')
      }
    } catch (error) {
      console.error('Connection error:', error)
      setConnectionStatus('error')
    } finally {
      setIsConnecting(false)
    }
  }

  const handleSync = async () => {
    setIsSyncing(true)
    try {
      const response = await fetch(`${config.apiUrl}/api/email/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: selectedProvider,
          email: email,
          password: password,
          max_emails: 50
        })
      })
      
      if (response.ok) {
        const data = await response.json()
        setSyncResult(data)
        addNotification({
          type: 'success',
          title: 'Email Sync Complete',
          message: `Found ${data.candidates_found || 0} candidates, parsed ${data.resumes_parsed || 0} resumes`,
          actionUrl: '/candidates'
        })
      } else {
        throw new Error('Sync failed')
      }
    } catch (error) {
      console.error('Sync error:', error)
      alert('Sync failed. Please check your credentials and try again.')
    } finally {
      setIsSyncing(false)
    }
  }

  const handleSetupAutoSync = async () => {
    try {
      const response = await fetch(`${config.apiUrl}/api/email/setup-auto-sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: selectedProvider,
          email: email,
          password: password,
          sync_interval_minutes: 1  // Fast sync - check every minute
        })
      })
      
      if (response.ok) {
        addNotification({
          type: 'info',
          title: 'Auto-Sync Enabled',
          message: 'New applications will be imported every minute',
        })
        alert('Auto-sync configured! New applications will be imported every minute.')
      } else {
        throw new Error('Failed to setup auto-sync')
      }
    } catch (error) {
      console.error('Auto-sync error:', error)
      alert('Failed to setup auto-sync. Please ensure email is connected first.')
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Email Integration</h2>
        <p className="text-gray-600">
          Connect your email to automatically import and parse candidate applications
        </p>
      </motion.div>

      {/* OAuth Automation Status Card */}
      {oauthStatus?.is_configured && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05 }}
        >
          <Card className={`border-2 ${oauthStatus.auth_status === 'valid' ? 'border-green-200 bg-green-50/30' : oauthStatus.auth_status === 'needs_reauth' ? 'border-red-200 bg-red-50/30' : 'border-blue-200 bg-blue-50/30'}`}>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Shield className="w-5 h-5 text-blue-600" />
                  <CardTitle className="text-lg">OAuth2 Automation</CardTitle>
                  {oauthStatus.auth_status === 'valid' && (
                    <Badge className="bg-green-100 text-green-800">Active</Badge>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleForceRefresh}
                    disabled={isRefreshingToken}
                  >
                    {isRefreshingToken ? (
                      <Loader className="w-4 h-4 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4" />
                    )}
                    <span className="ml-1">Refresh Token</span>
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleManualSync}
                    disabled={isManualSyncing}
                    variant={oauthStatus.auth_status === 'valid' ? 'default' : 'outline'}
                  >
                    {isManualSyncing ? (
                      <Loader className="w-4 h-4 animate-spin" />
                    ) : (
                      <Zap className="w-4 h-4" />
                    )}
                    <span className="ml-1">Manual Sync</span>
                  </Button>
                </div>
              </div>
              <CardDescription>
                Automatic token refresh and email sync for {oauthStatus.primary_email}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                <div className="text-center p-3 bg-white rounded-lg shadow-sm">
                  <div className="flex items-center justify-center gap-1 mb-1">
                    <Shield className="w-4 h-4 text-blue-600" />
                    <span className="text-xs text-gray-500">Auth Status</span>
                  </div>
                  <Badge className={getAuthStatusColor(oauthStatus.auth_status)}>
                    {oauthStatus.auth_status}
                  </Badge>
                </div>
                <div className="text-center p-3 bg-white rounded-lg shadow-sm">
                  <div className="flex items-center justify-center gap-1 mb-1">
                    <RefreshCw className="w-4 h-4 text-purple-600" />
                    <span className="text-xs text-gray-500">Sync Status</span>
                  </div>
                  <Badge className={getSyncStatusColor(oauthStatus.sync_status)}>
                    {oauthStatus.sync_status}
                  </Badge>
                </div>
                <div className="text-center p-3 bg-white rounded-lg shadow-sm">
                  <div className="flex items-center justify-center gap-1 mb-1">
                    <Clock className="w-4 h-4 text-orange-600" />
                    <span className="text-xs text-gray-500">Last Sync</span>
                  </div>
                  <p className="text-xs font-medium text-gray-700 truncate">
                    {formatDateTime(oauthStatus.last_sync)}
                  </p>
                </div>
                <div className="text-center p-3 bg-white rounded-lg shadow-sm">
                  <div className="flex items-center justify-center gap-1 mb-1">
                    <Clock className="w-4 h-4 text-green-600" />
                    <span className="text-xs text-gray-500">Next Sync</span>
                  </div>
                  <p className="text-xs font-medium text-gray-700 truncate">
                    {formatDateTime(oauthStatus.next_sync)}
                  </p>
                </div>
              </div>

              {/* Stats */}
              {oauthStatus.stats && (
                <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-center">
                  <div className="p-2 bg-blue-50 rounded">
                    <p className="text-lg font-bold text-blue-600">{oauthStatus.stats.total_syncs}</p>
                    <p className="text-xs text-gray-500">Total Syncs</p>
                  </div>
                  <div className="p-2 bg-green-50 rounded">
                    <p className="text-lg font-bold text-green-600">{oauthStatus.stats.successful_syncs}</p>
                    <p className="text-xs text-gray-500">Successful</p>
                  </div>
                  <div className="p-2 bg-red-50 rounded">
                    <p className="text-lg font-bold text-red-600">{oauthStatus.stats.failed_syncs}</p>
                    <p className="text-xs text-gray-500">Failed</p>
                  </div>
                  <div className="p-2 bg-purple-50 rounded">
                    <p className="text-lg font-bold text-purple-600">{oauthStatus.stats.token_refreshes}</p>
                    <p className="text-xs text-gray-500">Refreshes</p>
                  </div>
                  <div className="p-2 bg-orange-50 rounded">
                    <p className="text-lg font-bold text-orange-600">{oauthStatus.stats.emails_processed}</p>
                    <p className="text-xs text-gray-500">Emails</p>
                  </div>
                  <div className="p-2 bg-teal-50 rounded">
                    <p className="text-lg font-bold text-teal-600">{oauthStatus.stats.candidates_added}</p>
                    <p className="text-xs text-gray-500">Candidates</p>
                  </div>
                </div>
              )}

              {/* Warning for re-auth needed */}
              {oauthStatus.needs_manual_auth && oauthStatus.auth_url && (
                <div className="mt-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="w-5 h-5 text-yellow-600" />
                    <span className="text-sm text-yellow-800">Manual authentication required</span>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => window.open(oauthStatus.auth_url, '_blank')}
                  >
                    <ExternalLink className="w-4 h-4 mr-1" />
                    Authenticate
                  </Button>
                </div>
              )}

              {/* Success indicator */}
              {oauthStatus.auth_status === 'valid' && !oauthStatus.needs_manual_auth && (
                <div className="mt-4 p-3 bg-green-50 border border-green-200 rounded-lg flex items-center gap-2">
                  <CheckCircle className="w-5 h-5 text-green-600" />
                  <span className="text-sm text-green-800">
                    Automatic sync active - emails synced every {oauthStatus.sync_interval_minutes} minutes
                  </span>
                </div>
              )}
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* Provider Selection */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
      >
        <Card>
          <CardHeader>
            <CardTitle>Select Email Provider</CardTitle>
            <CardDescription>Choose your email service to get started</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {providers.map((provider) => (
                <div
                  key={provider.id}
                  onClick={() => setSelectedProvider(provider.id)}
                  className={`
                    p-4 border-2 rounded-lg cursor-pointer transition-all
                    ${selectedProvider === provider.id
                      ? 'border-primary-600 bg-primary-50'
                      : 'border-gray-200 hover:border-gray-300'
                    }
                  `}
                >
                  <div className="flex items-start justify-between mb-2">
                    <h4 className="font-semibold text-gray-900">{provider.name}</h4>
                    {provider.enterprise_ready && (
                      <Badge variant="primary" className="text-xs">Enterprise</Badge>
                    )}
                  </div>
                  <p className="text-xs text-gray-600 mb-3">{provider.instructions}</p>
                  <div className="flex flex-wrap gap-1">
                    {provider.supports_oauth && (
                      <Badge variant="outline" className="text-xs">OAuth2</Badge>
                    )}
                    {provider.requires_app_password && (
                      <Badge variant="outline" className="text-xs">App Password</Badge>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Connection Form */}
      {selectedProvider && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <Card>
            <CardHeader>
              <CardTitle>Connect Your Account</CardTitle>
              <CardDescription>
                {providers.find(p => p.id === selectedProvider)?.instructions}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Email Address
                </label>
                <Input
                  type="email"
                  placeholder="your-email@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>

              {selectedProvider !== 'outlook' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {providers.find(p => p.id === selectedProvider)?.requires_app_password
                      ? 'App Password'
                      : 'Password'}
                  </label>
                  <Input
                    type="password"
                    placeholder="••••••••••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  {providers.find(p => p.id === selectedProvider)?.requires_app_password && (
                    <p className="text-xs text-gray-500 mt-1">
                      Use an app-specific password, not your regular password
                    </p>
                  )}
                </div>
              )}

              {connectionStatus === 'connected' && (
                <div className="flex items-center gap-2 p-3 bg-success/10 rounded-lg">
                  <CheckCircle className="w-5 h-5 text-success" />
                  <span className="text-sm font-medium text-success">
                    Successfully connected to {email}
                  </span>
                </div>
              )}

              {connectionStatus === 'error' && (
                <div className="flex items-center gap-2 p-3 bg-danger/10 rounded-lg">
                  <AlertCircle className="w-5 h-5 text-danger" />
                  <span className="text-sm font-medium text-danger">
                    Connection failed. Please check your credentials.
                  </span>
                </div>
              )}

              <div className="flex gap-3">
                <Button
                  onClick={handleConnect}
                  disabled={!email || (!password && selectedProvider !== 'outlook') || isConnecting}
                  className="flex-1"
                >
                  {isConnecting ? (
                    <>
                      <Loader className="w-4 h-4 mr-2 animate-spin" />
                      Connecting...
                    </>
                  ) : (
                    <>
                      <Mail className="w-4 h-4 mr-2" />
                      Connect Account
                    </>
                  )}
                </Button>

                {connectionStatus === 'connected' && (
                  <Button
                    onClick={handleSync}
                    disabled={isSyncing}
                    variant="success"
                  >
                    {isSyncing ? (
                      <>
                        <Loader className="w-4 h-4 mr-2 animate-spin" />
                        Syncing...
                      </>
                    ) : (
                      <>
                        <RefreshCw className="w-4 h-4 mr-2" />
                        Sync Now
                      </>
                    )}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* Sync Results */}
      {syncResult && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <Card className="border-success">
            <CardHeader>
              <CardTitle className="text-success flex items-center gap-2">
                <CheckCircle className="w-5 h-5" />
                Sync Complete!
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="text-center p-4 bg-primary-50 rounded-lg">
                  <p className="text-3xl font-bold text-primary-600">
                    {syncResult.candidates_found}
                  </p>
                  <p className="text-sm text-gray-600 mt-1">Candidates Found</p>
                </div>
                <div className="text-center p-4 bg-success/10 rounded-lg">
                  <p className="text-3xl font-bold text-success">
                    {syncResult.new_applications}
                  </p>
                  <p className="text-sm text-gray-600 mt-1">New Applications</p>
                </div>
                <div className="text-center p-4 bg-purple-50 rounded-lg">
                  <p className="text-3xl font-bold text-purple-600">
                    {syncResult.resumes_parsed}
                  </p>
                  <p className="text-sm text-gray-600 mt-1">Resumes Parsed</p>
                </div>
                <div className="text-center p-4 bg-warning/10 rounded-lg">
                  <p className="text-3xl font-bold text-warning">
                    {syncResult.updated_profiles}
                  </p>
                  <p className="text-sm text-gray-600 mt-1">Profiles Updated</p>
                </div>
              </div>

              <div className="mt-6 flex gap-3">
                <Button onClick={() => navigate('/candidates')} className="flex-1">
                  View Candidates
                </Button>
                <Button onClick={handleSetupAutoSync} variant="outline">
                  <SettingsIcon className="w-4 h-4 mr-2" />
                  Setup Auto-Sync
                </Button>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* Features */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
      >
        <Card>
          <CardHeader>
            <CardTitle>Automatic Email Parsing Features</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 bg-primary-100 rounded-lg flex items-center justify-center flex-shrink-0">
                  <CheckCircle className="w-5 h-5 text-primary-600" />
                </div>
                <div>
                  <h4 className="font-semibold text-gray-900 mb-1">Resume Extraction</h4>
                  <p className="text-sm text-gray-600">
                    Automatically detects and parses PDF, DOCX resume attachments
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-3">
                <div className="w-10 h-10 bg-success/10 rounded-lg flex items-center justify-center flex-shrink-0">
                  <CheckCircle className="w-5 h-5 text-success" />
                </div>
                <div>
                  <h4 className="font-semibold text-gray-900 mb-1">Email Content Parsing</h4>
                  <p className="text-sm text-gray-600">
                    Extracts candidate info from email body (phone, skills, experience)
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-3">
                <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center flex-shrink-0">
                  <CheckCircle className="w-5 h-5 text-purple-600" />
                </div>
                <div>
                  <h4 className="font-semibold text-gray-900 mb-1">Smart Detection</h4>
                  <p className="text-sm text-gray-600">
                    Identifies job applications from subject lines and keywords
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-3">
                <div className="w-10 h-10 bg-warning/10 rounded-lg flex items-center justify-center flex-shrink-0">
                  <CheckCircle className="w-5 h-5 text-warning" />
                </div>
                <div>
                  <h4 className="font-semibold text-gray-900 mb-1">Auto-Sync</h4>
                  <p className="text-sm text-gray-600">
                    Continuously monitors inbox for new applications (every 15 min)
                  </p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  )
}
