import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  CheckCircle,
  XCircle,
  AlertCircle,
  Loader,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Copy,
  RefreshCw,
  Server,
  Database,
  Mail,
  Brain,
  MessageSquare,
  Calendar,
  Settings,
  Zap,
  Shield,
  Clock,
  AlertTriangle,
  Wrench,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { useNotificationStore } from '@/store/notificationStore'
import config from '@/config'
import { authFetch } from '@/lib/authFetch'

/* ────────────────────────────────────────────
   Types
   ──────────────────────────────────────────── */

interface SetupCheck {
  name: string
  status: 'configured' | 'not_configured' | 'error' | 'optional'
  message: string
  required: boolean
  instructions: string
  docs_url: string
}

interface SetupReport {
  overall_status: string
  ready_for_production: boolean
  checks: SetupCheck[]
  warnings: string[]
  errors: string[]
  summary: {
    total: number
    configured: number
    not_configured: number
    errors: number
    optional: number
  }
}

interface SetupInstruction {
  id: string
  title: string
  description: string
  required?: boolean
  steps: string[]
  env_vars?: string[]
  docs_url?: string
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

interface EmailProvider {
  id: string
  name: string
  requires_app_password: boolean
  supports_oauth: boolean
  enterprise_ready?: boolean
  instructions: string
}

/* ────────────────────────────────────────────
   Tabs
   ──────────────────────────────────────────── */

const TABS = [
  { id: 'overview', label: 'System Overview', icon: Server },
  { id: 'email', label: 'Email & Sync', icon: Mail },
  { id: 'guides', label: 'Setup Guides', icon: Wrench },
] as const

type TabId = (typeof TABS)[number]['id']

/* ────────────────────────────────────────────
   Component
   ──────────────────────────────────────────── */

export default function SetupWizard() {
  const navigate = useNavigate()
  const addNotification = useNotificationStore((state) => state.addNotification)

  const [activeTab, setActiveTab] = useState<TabId>('overview')

  // Setup status
  const [setupReport, setSetupReport] = useState<SetupReport | null>(null)
  const [instructions, setInstructions] = useState<{ sections: SetupInstruction[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['quick_start']))
  const [testingService, setTestingService] = useState<string | null>(null)
  const [platformStats, setPlatformStats] = useState<{ total_candidates?: number; avg_score?: number; categories?: Record<string, number>; job_categories?: number; strong_matches?: number } | null>(null)

  // Email integration
  const [selectedProvider, setSelectedProvider] = useState<string>('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isConnecting, setIsConnecting] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [connectionStatus, setConnectionStatus] = useState<'idle' | 'connected' | 'error'>('idle')
  const [syncResult, setSyncResult] = useState<{ candidates_found?: number; new_applications?: number; resumes_parsed?: number; updated_profiles?: number } | null>(null)
  const [oauthStatus, setOauthStatus] = useState<OAuthStatus | null>(null)
  const [isRefreshingToken, setIsRefreshingToken] = useState(false)
  const [isManualSyncing, setIsManualSyncing] = useState(false)

  /* ── Data fetching ── */

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchOAuthStatus, 30000)
    return () => clearInterval(interval)
  }, [])

  const fetchAll = async () => {
    setLoading(true)
    try {
      await Promise.all([fetchSetupData(), fetchOAuthStatus(), fetchPlatformStats()])
    } finally {
      setLoading(false)
    }
  }

  const fetchSetupData = async () => {
    try {
      const [reportRes, instructionsRes] = await Promise.all([
        authFetch(`${config.apiUrl}/api/setup/verify`),
        authFetch(`${config.apiUrl}/api/setup/instructions`),
      ])
      if (reportRes.ok) setSetupReport(await reportRes.json())
      if (instructionsRes.ok) setInstructions(await instructionsRes.json())
    } catch (error) {
      console.error('Failed to fetch setup data:', error)
    }
  }

  const fetchPlatformStats = async () => {
    try {
      const res = await authFetch(`${config.apiUrl}/api/stats`)
      if (res.ok) setPlatformStats(await res.json())
    } catch (error) {
      console.error('Failed to fetch platform stats:', error)
    }
  }

  const fetchOAuthStatus = async () => {
    try {
      const response = await authFetch(`${config.apiUrl}/api/oauth/status`)
      if (response.ok) setOauthStatus(await response.json())
    } catch (error) {
      console.error('Failed to fetch OAuth status:', error)
    }
  }

  /* ── Setup actions ── */

  const testConnection = async (service: string) => {
    setTestingService(service)
    try {
      const response = await authFetch(`${config.apiUrl}/api/setup/test-connection/${service}`, { method: 'POST' })
      const data = await response.json()
      const ok = data.status === 'connected' || data.status === 'working' || data.status === 'configured'
      addNotification({
        type: ok ? 'success' : 'warning',
        title: ok ? 'Connection Successful' : 'Connection Issue',
        message: data.error || `${service} ${ok ? 'is working correctly' : 'is not configured'}`,
      })
    } catch {
      addNotification({ type: 'error', title: 'Test Failed', message: `Failed to test ${service}` })
    } finally {
      setTestingService(null)
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    addNotification({ type: 'success', title: 'Copied', message: 'Copied to clipboard' })
  }

  const toggleSection = (id: string) => {
    const s = new Set(expandedSections)
    s.has(id) ? s.delete(id) : s.add(id)
    setExpandedSections(s)
  }

  /* ── Email actions ── */

  const handleForceRefresh = async () => {
    setIsRefreshingToken(true)
    try {
      const response = await authFetch(`${config.apiUrl}/api/oauth/refresh`, { method: 'POST' })
      const data = await response.json()
      if (data.status === 'success') {
        addNotification({ type: 'success', title: 'Token Refreshed', message: 'OAuth2 token refreshed successfully' })
        await fetchOAuthStatus()
      } else if (data.needs_manual_auth && data.auth_url) {
        window.open(data.auth_url, '_blank')
      } else {
        addNotification({ type: 'error', title: 'Refresh Failed', message: data.message || 'Failed to refresh token' })
      }
    } catch {
      addNotification({ type: 'error', title: 'Error', message: 'Failed to refresh token' })
    } finally {
      setIsRefreshingToken(false)
    }
  }

  const handleManualSync = async () => {
    setIsManualSyncing(true)
    try {
      const response = await authFetch(`${config.apiUrl}/api/email/manual-sync`, { method: 'POST' })
      const data = await response.json()
      if (data.status === 'syncing') {
        addNotification({ type: 'success', title: 'Sync Started', message: 'Manual email sync started in background' })
      } else if (data.status === 'needs_auth' && data.auth_url) {
        addNotification({ type: 'warning', title: 'Authentication Required', message: 'Please authenticate to sync emails' })
        window.open(data.auth_url, '_blank')
      } else {
        addNotification({ type: 'error', title: 'Sync Failed', message: data.message || 'Failed to start sync' })
      }
      await fetchOAuthStatus()
    } catch (err) {
      console.error('Manual sync failed:', err)
      addNotification({ type: 'error', title: 'Error', message: 'Failed to trigger manual sync' })
    } finally {
      setIsManualSyncing(false)
    }
  }

  const handleConnect = async () => {
    setIsConnecting(true)
    try {
      const response = await authFetch(`${config.apiUrl}/api/email/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: selectedProvider, email, password }),
      })
      if (response.ok) {
        setConnectionStatus('connected')
        addNotification({ type: 'success', title: 'Email Connected', message: `Connected to ${selectedProvider}` })
      } else {
        setConnectionStatus('error')
      }
    } catch (err) {
      console.error('Email connect failed:', err)
      setConnectionStatus('error')
      addNotification({ type: 'error', title: 'Connection Failed', message: 'Could not connect to email provider. Check your credentials.' })
    } finally {
      setIsConnecting(false)
    }
  }

  const handleSync = async () => {
    setIsSyncing(true)
    try {
      const response = await authFetch(`${config.apiUrl}/api/email/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: selectedProvider, email, password, max_emails: 50 }),
      })
      if (response.ok) {
        const data = await response.json()
        setSyncResult(data)
        addNotification({ type: 'success', title: 'Sync Complete', message: `Found ${data.candidates_found || 0} candidates` })
      } else throw new Error('Sync failed')
    } catch (err) {
      console.error('Email sync failed:', err)
      addNotification({ type: 'error', title: 'Sync Failed', message: 'Check your credentials and try again.' })
    } finally {
      setIsSyncing(false)
    }
  }

  const handleSetupAutoSync = async () => {
    try {
      const response = await authFetch(`${config.apiUrl}/api/email/setup-auto-sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: selectedProvider, email, password, sync_interval_minutes: 1 }),
      })
      if (response.ok) addNotification({ type: 'info', title: 'Auto-Sync Enabled', message: 'New applications imported every minute' })
      else throw new Error()
    } catch (err) {
      console.error('Auto-sync setup failed:', err)
      addNotification({ type: 'error', title: 'Error', message: 'Failed to setup auto-sync.' })
    }
  }

  /* ── Helpers ── */

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'configured': return <CheckCircle className="w-4 h-4 text-green-500" />
      case 'error': return <XCircle className="w-4 h-4 text-red-500" />
      case 'not_configured': return <AlertCircle className="w-4 h-4 text-yellow-500" />
      default: return <AlertCircle className="w-4 h-4 text-gray-400" />
    }
  }

  const getStatusBadge = (status: string) => {
    const cls: Record<string, string> = {
      configured: 'bg-green-100 text-green-800',
      error: 'bg-red-100 text-red-800',
      not_configured: 'bg-yellow-100 text-yellow-800',
    }
    const labels: Record<string, string> = { configured: 'OK', error: 'Error', not_configured: 'Pending' }
    return <Badge className={`${cls[status] || 'bg-gray-100 text-gray-600'} text-[10px] px-1.5`}>{labels[status] || 'Optional'}</Badge>
  }

  const getAuthColor = (s: string) => {
    switch (s) {
      case 'valid': return 'bg-green-100 text-green-800'
      case 'expired': return 'bg-yellow-100 text-yellow-800'
      case 'needs_reauth': return 'bg-red-100 text-red-800'
      default: return 'bg-gray-100 text-gray-800'
    }
  }

  const getSyncColor = (s: string) => {
    switch (s) {
      case 'success': return 'bg-green-100 text-green-800'
      case 'syncing': return 'bg-sky-100 text-sky-800'
      case 'failed': return 'bg-red-100 text-red-800'
      default: return 'bg-gray-100 text-gray-800'
    }
  }

  const fmtDate = (iso?: string) => (iso ? new Date(iso).toLocaleString() : 'Never')

  const getSectionIcon = (id: string) => {
    const map: Record<string, JSX.Element> = {
      quick_start: <Zap className="w-4 h-4" />,
      email_oauth: <Mail className="w-4 h-4" />,
      ai_models: <Brain className="w-4 h-4" />,
      production: <Server className="w-4 h-4" />,
      twilio: <MessageSquare className="w-4 h-4" />,
      google_calendar: <Calendar className="w-4 h-4" />,
      calendly: <Calendar className="w-4 h-4" />,
    }
    return map[id] || <Settings className="w-4 h-4" />
  }

  const providers: EmailProvider[] = [
    { id: 'outlook', name: 'Outlook / Office 365', requires_app_password: false, supports_oauth: true, enterprise_ready: true, instructions: 'Enterprise OAuth2 — secure & recommended' },
    { id: 'gmail', name: 'Gmail', requires_app_password: true, supports_oauth: true, instructions: 'Enable 2FA → create app password at myaccount.google.com' },
    { id: 'yahoo', name: 'Yahoo Mail', requires_app_password: true, supports_oauth: false, instructions: 'Create app password in Yahoo security settings' },
    { id: 'icloud', name: 'iCloud Mail', requires_app_password: true, supports_oauth: false, instructions: 'Generate app-specific password at appleid.apple.com' },
    { id: 'custom', name: 'Custom IMAP', requires_app_password: false, supports_oauth: false, instructions: 'Connect any IMAP-compatible email server' },
  ]

  /* ── Loading ── */

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader className="w-8 h-8 animate-spin text-sky-600" />
        <span className="ml-3 text-gray-500">Loading configuration…</span>
      </div>
    )
  }

  /* ──────────────────────────────────────────
     RENDER
     ────────────────────────────────────────── */

  return (
    <div className="space-y-5 max-w-6xl mx-auto">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center">
              <Wrench className="w-5 h-5 text-white" />
            </div>
            <div>
              <h2 className="text-2xl font-bold text-gray-900">Platform Setup</h2>
              <p className="text-sm text-gray-500">System health, email sync & configuration guides</p>
            </div>
          </div>
          <Button onClick={fetchAll} variant="outline" size="sm">
            <RefreshCw className="w-4 h-4 mr-1.5" />Refresh
          </Button>
        </div>
      </motion.div>

      {/* Quick status row */}
      {setupReport && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'Configured', value: setupReport.summary.configured, color: 'text-green-600', bg: 'bg-green-50 border-green-100' },
            { label: 'Pending', value: setupReport.summary.not_configured, color: 'text-yellow-600', bg: 'bg-yellow-50 border-yellow-100' },
            { label: 'Errors', value: setupReport.summary.errors, color: 'text-red-600', bg: 'bg-red-50 border-red-100' },
            { label: 'Optional', value: setupReport.summary.optional, color: 'text-gray-500', bg: 'bg-gray-50 border-gray-100' },
          ].map((s) => (
            <div key={s.label} className={`rounded-lg border p-3 text-center ${s.bg}`}>
              <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
              <p className="text-[11px] text-gray-500">{s.label}</p>
            </div>
          ))}
        </motion.div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-1 -mb-px" role="tablist" aria-label="Setup sections">
          {TABS.map((tab) => {
            const Icon = tab.icon
            const active = activeTab === tab.id
            return (
              <button
                key={tab.id}
                id={`tab-${tab.id}`}
                role="tab"
                type="button"
                aria-selected={active}
                aria-controls={`panel-${tab.id}`}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  active ? 'border-sky-600 text-sky-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
                {tab.id === 'email' && oauthStatus?.is_configured && oauthStatus.auth_status === 'valid' && (
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                )}
              </button>
            )
          })}
        </nav>
      </div>

      {/* Tab content */}
      <AnimatePresence mode="wait">
        <motion.div
          key={activeTab}
          id={`panel-${activeTab}`}
          role="tabpanel"
          aria-labelledby={`tab-${activeTab}`}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.15 }}
        >
          {activeTab === 'overview' && renderOverview()}
          {activeTab === 'email' && renderEmail()}
          {activeTab === 'guides' && renderGuides()}
        </motion.div>
      </AnimatePresence>
    </div>
  )

  /* ════════════════════════════════════════════
     TAB: System Overview
     ════════════════════════════════════════════ */

  function renderOverview() {
    if (!setupReport) return null
    return (
      <div className="space-y-4">
        {/* Platform Intelligence */}
        <Card className="border-sky-200 bg-gradient-to-r from-sky-50/60 to-white">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Brain className="w-5 h-5 text-sky-600" />
              <CardTitle className="text-base">Platform Intelligence</CardTitle>
              <Badge className="bg-sky-100 text-sky-700 text-[10px]">v18</Badge>
            </div>
            <CardDescription className="text-xs">AI engine status, database health & sync pipeline</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="text-center p-3 bg-white rounded-lg shadow-sm border">
                <Database className="w-5 h-5 text-sky-600 mx-auto mb-1" />
                <p className="text-xl font-bold text-gray-900">{platformStats?.total_candidates?.toLocaleString() || '—'}</p>
                <p className="text-[10px] text-gray-500">Total Candidates</p>
              </div>
              <div className="text-center p-3 bg-white rounded-lg shadow-sm border">
                <Brain className="w-5 h-5 text-green-600 mx-auto mb-1" />
                <p className="text-sm font-bold text-gray-900">Gemini 2.0</p>
                <p className="text-[10px] text-gray-500">Flash-Lite AI Model</p>
              </div>
              <div className="text-center p-3 bg-white rounded-lg shadow-sm border">
                <Zap className="w-5 h-5 text-amber-600 mx-auto mb-1" />
                <p className="text-xl font-bold text-gray-900">{platformStats?.avg_score ? `${Math.round(platformStats.avg_score)}%` : '—'}</p>
                <p className="text-[10px] text-gray-500">Avg Match Score</p>
              </div>
              <div className="text-center p-3 bg-white rounded-lg shadow-sm border">
                <Mail className="w-5 h-5 text-teal-600 mx-auto mb-1" />
                <p className="text-sm font-bold text-gray-900">{oauthStatus?.auth_status === 'valid' ? 'Active' : 'Inactive'}</p>
                <p className="text-[10px] text-gray-500">Email Auto-Sync</p>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2">
              {[
                { l: 'Job Categories', v: platformStats?.job_categories?.toLocaleString() || '—', c: 'text-sky-600' },
                { l: 'Sync Interval', v: `${oauthStatus?.sync_interval_minutes || 60} min`, c: 'text-green-600' },
                { l: 'Strong Matches', v: platformStats?.strong_matches?.toLocaleString() || '—', c: 'text-amber-600' },
              ].map((s, i) => (
                <div key={i} className="flex items-center justify-between p-2 bg-gray-50 rounded text-xs">
                  <span className="text-gray-500">{s.l}</span>
                  <span className={`font-semibold ${s.c}`}>{s.v}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {setupReport.errors.length > 0 && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
            <h4 className="font-medium text-red-800 mb-1 text-sm">Critical Issues</h4>
            <ul className="text-sm text-red-700 space-y-0.5">{setupReport.errors.map((e, i) => <li key={i}>• {e}</li>)}</ul>
          </div>
        )}
        {setupReport.warnings.length > 0 && (
          <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
            <h4 className="font-medium text-yellow-800 mb-1 text-sm">Warnings</h4>
            <ul className="text-sm text-yellow-700 space-y-0.5">{setupReport.warnings.map((w, i) => <li key={i}>• {w}</li>)}</ul>
          </div>
        )}

        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-base">Configuration Checks</CardTitle>
                <CardDescription className="text-xs">{setupReport.summary.configured}/{setupReport.summary.total} components ready</CardDescription>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => testConnection('database')} disabled={testingService === 'database'}>
                  {testingService === 'database' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Database className="w-3.5 h-3.5" />}
                  <span className="ml-1 text-xs">Test DB</span>
                </Button>
                <Button size="sm" variant="outline" onClick={() => testConnection('ai')} disabled={testingService === 'ai'}>
                  {testingService === 'ai' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Brain className="w-3.5 h-3.5" />}
                  <span className="ml-1 text-xs">Test AI</span>
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="space-y-1.5">
              {setupReport.checks.map((check, i) => (
                <div key={i} className={`flex items-center justify-between p-2.5 rounded-lg text-sm ${
                  check.status === 'configured' ? 'bg-green-50/60' : check.status === 'error' ? 'bg-red-50/60' : check.status === 'not_configured' ? 'bg-yellow-50/60' : 'bg-gray-50/60'
                }`}>
                  <div className="flex items-center gap-2 min-w-0">
                    {getStatusIcon(check.status)}
                    <div className="min-w-0">
                      <p className="font-medium text-gray-900 text-[13px]">{check.name}</p>
                      <p className="text-[11px] text-gray-500 truncate">{check.message}</p>
                      {check.instructions && check.status !== 'configured' && (
                        <p className="text-[11px] text-sky-600 mt-0.5 truncate">{check.instructions}</p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 flex-shrink-0 ml-2">
                    {check.required && <Badge variant="outline" className="text-[9px] px-1 py-0">Required</Badge>}
                    {getStatusBadge(check.status)}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  /* ════════════════════════════════════════════
     TAB: Email & Sync
     ════════════════════════════════════════════ */

  function renderEmail() {
    return (
      <div className="space-y-4">
        {/* OAuth automation */}
        {oauthStatus?.is_configured && (
          <Card className={`border-2 ${
            oauthStatus.auth_status === 'valid' ? 'border-green-200 bg-green-50/30' :
            oauthStatus.auth_status === 'needs_reauth' ? 'border-red-200 bg-red-50/30' : 'border-sky-200 bg-sky-50/30'
          }`}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Shield className="w-5 h-5 text-sky-600" />
                  <CardTitle className="text-base">OAuth2 Automation</CardTitle>
                  {oauthStatus.auth_status === 'valid' && <Badge className="bg-green-100 text-green-800 text-[11px]">Active</Badge>}
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" onClick={handleForceRefresh} disabled={isRefreshingToken}>
                    {isRefreshingToken ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                    <span className="ml-1 text-xs">Refresh Token</span>
                  </Button>
                  <Button size="sm" onClick={handleManualSync} disabled={isManualSyncing}>
                    {isManualSyncing ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
                    <span className="ml-1 text-xs">Sync Now</span>
                  </Button>
                </div>
              </div>
              <CardDescription className="text-xs">Auto token refresh & email sync for {oauthStatus.primary_email}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                {[
                  { icon: Shield, label: 'Auth', badge: oauthStatus.auth_status, color: getAuthColor(oauthStatus.auth_status) },
                  { icon: RefreshCw, label: 'Sync', badge: oauthStatus.sync_status, color: getSyncColor(oauthStatus.sync_status) },
                  { icon: Clock, label: 'Last Sync', text: fmtDate(oauthStatus.last_sync) },
                  { icon: Clock, label: 'Next Sync', text: fmtDate(oauthStatus.next_sync) },
                ].map((item, i) => (
                  <div key={i} className="text-center p-2.5 bg-white rounded-lg shadow-sm">
                    <div className="flex items-center justify-center gap-1 mb-1">
                      <item.icon className="w-3.5 h-3.5 text-sky-600" />
                      <span className="text-[11px] text-gray-500">{item.label}</span>
                    </div>
                    {item.badge ? (
                      <Badge className={`${item.color} text-[11px]`}>{item.badge}</Badge>
                    ) : (
                      <p className="text-[11px] font-medium text-gray-700 truncate">{item.text}</p>
                    )}
                  </div>
                ))}
              </div>

              {oauthStatus.stats && (
                <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-center">
                  {[
                    { v: oauthStatus.stats.total_syncs, l: 'Total', c: 'text-sky-600 bg-sky-50' },
                    { v: oauthStatus.stats.successful_syncs, l: 'Success', c: 'text-green-600 bg-green-50' },
                    { v: oauthStatus.stats.failed_syncs, l: 'Failed', c: 'text-red-600 bg-red-50' },
                    { v: oauthStatus.stats.token_refreshes, l: 'Refreshes', c: 'text-sky-600 bg-sky-50' },
                    { v: oauthStatus.stats.emails_processed, l: 'Emails', c: 'text-orange-600 bg-orange-50' },
                    { v: oauthStatus.stats.candidates_added, l: 'Candidates', c: 'text-teal-600 bg-teal-50' },
                  ].map((s, i) => (
                    <div key={i} className={`p-2 rounded ${s.c.split(' ')[1]}`}>
                      <p className={`text-lg font-bold ${s.c.split(' ')[0]}`}>{s.v}</p>
                      <p className="text-[10px] text-gray-500">{s.l}</p>
                    </div>
                  ))}
                </div>
              )}

              {oauthStatus.needs_manual_auth && oauthStatus.auth_url && (
                <div className="mt-3 p-2.5 bg-yellow-50 border border-yellow-200 rounded-lg flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-yellow-600" />
                    <span className="text-sm text-yellow-800">Manual authentication required</span>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => window.open(oauthStatus.auth_url, '_blank')}>
                    <ExternalLink className="w-3.5 h-3.5 mr-1" />Authenticate
                  </Button>
                </div>
              )}

              {oauthStatus.auth_status === 'valid' && !oauthStatus.needs_manual_auth && (
                <div className="mt-3 p-2.5 bg-green-50 border border-green-200 rounded-lg flex items-center gap-2">
                  <CheckCircle className="w-4 h-4 text-green-600" />
                  <span className="text-sm text-green-800">Auto-sync active — emails synced every {oauthStatus.sync_interval_minutes} min</span>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Not connected */}
        {(!oauthStatus || !oauthStatus.is_configured) && (
          <div className="p-4 bg-sky-50 border border-sky-200 rounded-lg flex items-start gap-3">
            <Mail className="w-5 h-5 text-sky-600 mt-0.5" />
            <div>
              <h4 className="font-medium text-sky-900 text-sm">Email Not Connected</h4>
              <p className="text-xs text-sky-700 mt-0.5">Connect your email below to enable automatic candidate import.</p>
            </div>
          </div>
        )}

        {/* Provider selection */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Connect Email Provider</CardTitle>
            <CardDescription className="text-xs">Choose a provider to enable automatic inbox sync</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {providers.map((p) => (
                <div key={p.id} onClick={() => setSelectedProvider(p.id)} className={`p-3 border-2 rounded-lg cursor-pointer transition-all ${
                  selectedProvider === p.id ? 'border-sky-500 bg-sky-50/50 ring-1 ring-sky-200' : 'border-gray-200 hover:border-gray-300'
                }`}>
                  <div className="flex items-start justify-between mb-1.5">
                    <h4 className="font-semibold text-gray-900 text-sm">{p.name}</h4>
                    {p.enterprise_ready && <Badge className="bg-sky-100 text-sky-700 text-[10px]">Enterprise</Badge>}
                  </div>
                  <p className="text-[11px] text-gray-500 mb-2">{p.instructions}</p>
                  <div className="flex gap-1">
                    {p.supports_oauth && <Badge variant="outline" className="text-[10px]">OAuth2</Badge>}
                    {p.requires_app_password && <Badge variant="outline" className="text-[10px]">App Password</Badge>}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Connection form */}
        {selectedProvider && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Connect Account</CardTitle>
              <CardDescription className="text-xs">{providers.find((p) => p.id === selectedProvider)?.instructions}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">Email Address</label>
                <Input type="email" placeholder="your-email@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
              </div>
              {selectedProvider !== 'outlook' && (
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">
                    {providers.find((p) => p.id === selectedProvider)?.requires_app_password ? 'App Password' : 'Password'}
                  </label>
                  <Input type="password" placeholder="••••••••••••" value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
              )}

              {connectionStatus === 'connected' && (
                <div className="flex items-center gap-2 p-2.5 bg-green-50 border border-green-200 rounded-lg">
                  <CheckCircle className="w-4 h-4 text-green-600" />
                  <span className="text-sm text-green-800">Connected to {email}</span>
                </div>
              )}
              {connectionStatus === 'error' && (
                <div className="flex items-center gap-2 p-2.5 bg-red-50 border border-red-200 rounded-lg">
                  <AlertCircle className="w-4 h-4 text-red-600" />
                  <span className="text-sm text-red-800">Connection failed. Check credentials.</span>
                </div>
              )}

              <div className="flex gap-2">
                <Button onClick={handleConnect} disabled={!email || (!password && selectedProvider !== 'outlook') || isConnecting} className="flex-1">
                  {isConnecting ? <><Loader className="w-4 h-4 mr-1.5 animate-spin" />Connecting…</> : <><Mail className="w-4 h-4 mr-1.5" />Connect</>}
                </Button>
                {connectionStatus === 'connected' && (
                  <Button onClick={handleSync} disabled={isSyncing} variant="outline">
                    {isSyncing ? <><Loader className="w-4 h-4 mr-1.5 animate-spin" />Syncing…</> : <><RefreshCw className="w-4 h-4 mr-1.5" />Sync Now</>}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Sync results */}
        {syncResult && (
          <Card className="border-green-200">
            <CardHeader className="pb-2">
              <CardTitle className="text-base text-green-700 flex items-center gap-2">
                <CheckCircle className="w-5 h-5" />Sync Complete
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                {[
                  { v: syncResult.candidates_found, l: 'Candidates Found', c: 'text-sky-600 bg-sky-50' },
                  { v: syncResult.new_applications, l: 'New Applications', c: 'text-green-600 bg-green-50' },
                  { v: syncResult.resumes_parsed, l: 'Resumes Parsed', c: 'text-sky-600 bg-sky-50' },
                  { v: syncResult.updated_profiles, l: 'Profiles Updated', c: 'text-yellow-600 bg-yellow-50' },
                ].map((s, i) => (
                  <div key={i} className={`text-center p-3 rounded-lg ${s.c.split(' ')[1]}`}>
                    <p className={`text-2xl font-bold ${s.c.split(' ')[0]}`}>{s.v}</p>
                    <p className="text-xs text-gray-600 mt-0.5">{s.l}</p>
                  </div>
                ))}
              </div>
              <div className="flex gap-2">
                <Button onClick={() => navigate('/candidates')} className="flex-1" size="sm">View Candidates</Button>
                <Button onClick={handleSetupAutoSync} variant="outline" size="sm">
                  <Settings className="w-3.5 h-3.5 mr-1" />Auto-Sync
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Features */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Email Parsing Features</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {[
                { title: 'Resume Extraction', desc: 'Auto-detects and parses PDF, DOCX attachments', color: 'bg-sky-100 text-sky-600' },
                { title: 'Email Content Parsing', desc: 'Extracts phone, skills, experience from email body', color: 'bg-green-100 text-green-600' },
                { title: 'Smart Detection', desc: 'Identifies job applications from subject lines & keywords', color: 'bg-sky-100 text-sky-600' },
                { title: 'Auto-Sync', desc: 'Continuously monitors inbox for new applications', color: 'bg-orange-100 text-orange-600' },
              ].map((f, i) => (
                <div key={i} className="flex items-start gap-2.5">
                  <div className={`w-8 h-8 ${f.color} rounded-lg flex items-center justify-center flex-shrink-0`}>
                    <CheckCircle className="w-4 h-4" />
                  </div>
                  <div>
                    <h4 className="font-medium text-gray-900 text-sm">{f.title}</h4>
                    <p className="text-[11px] text-gray-500">{f.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  /* ════════════════════════════════════════════
     TAB: Setup Guides
     ════════════════════════════════════════════ */

  function renderGuides() {
    if (!instructions) return <p className="text-sm text-gray-500">No setup instructions available.</p>
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Step-by-Step Guides</CardTitle>
          <CardDescription className="text-xs">Setup instructions for each component</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {instructions.sections.map((section) => (
              <div key={section.id} className="border rounded-lg overflow-hidden">
                <button onClick={() => toggleSection(section.id)} className="w-full flex items-center justify-between p-3 bg-gray-50 hover:bg-gray-100 transition-colors">
                  <div className="flex items-center gap-2.5">
                    <div className={`p-1.5 rounded-lg ${section.required ? 'bg-sky-100 text-sky-600' : 'bg-gray-100 text-gray-500'}`}>
                      {getSectionIcon(section.id)}
                    </div>
                    <div className="text-left">
                      <h4 className="font-medium text-gray-900 text-sm">{section.title}</h4>
                      <p className="text-[11px] text-gray-500">{section.description}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {section.required && <Badge className="bg-sky-100 text-sky-800 text-[10px]">Required</Badge>}
                    {expandedSections.has(section.id) ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
                  </div>
                </button>

                {expandedSections.has(section.id) && (
                  <div className="p-3 bg-white border-t space-y-3">
                    <ol className="space-y-1.5">
                      {section.steps.map((step, i) => (
                        <li key={i} className="flex items-start gap-2 text-sm text-gray-600">
                          <span className="flex-shrink-0 w-5 h-5 rounded-full bg-sky-100 text-sky-600 flex items-center justify-center text-[11px] font-medium">{i + 1}</span>
                          <span className="text-xs">{step.replace(/^\d+\.\s*/, '')}</span>
                        </li>
                      ))}
                    </ol>

                    {section.env_vars && section.env_vars.length > 0 && (
                      <div className="bg-gray-900 rounded-lg p-2.5 overflow-x-auto">
                        <code className="text-xs text-gray-100">
                          {section.env_vars.map((envVar, i) => (
                            <div key={i} className="flex items-center justify-between group">
                              <span>{envVar}</span>
                              <button onClick={() => copyToClipboard(envVar)} className="opacity-0 group-hover:opacity-100 p-0.5 hover:bg-gray-700 rounded">
                                <Copy className="w-3 h-3 text-gray-400" />
                              </button>
                            </div>
                          ))}
                        </code>
                      </div>
                    )}

                    {section.docs_url && (
                      <a href={section.docs_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-sky-600 hover:text-sky-800">
                        <ExternalLink className="w-3.5 h-3.5" />View documentation
                      </a>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    )
  }
}
