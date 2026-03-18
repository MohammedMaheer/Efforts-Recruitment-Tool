import { motion } from 'framer-motion'
import { useState, useEffect } from 'react'
import { User, Bell, Lock, Mail, Loader2, CheckCircle, Database, RefreshCw, Trash2, Zap, Search, Shield, FileSearch } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useAuthStore } from '@/store/authStore'
import { useNotificationStore } from '@/store/notificationStore'
import { candidateApi } from '@/services/api'
import config from '@/config'
import { toast } from '@/components/ui/Toast'
import { authFetch } from '@/lib/authFetch'

export default function Settings() {
  const user = useAuthStore((state) => state.user)
  const isAdmin = user?.role === 'admin'
  const addNotification = useNotificationStore((state) => state.addNotification)
  const [firstName, setFirstName] = useState(user?.name?.split(' ')[0] || '')
  const [lastName, setLastName] = useState(user?.name?.split(' ').slice(1).join(' ') || '')
  const [email, setEmail] = useState(user?.email || '')
  const [company, setCompany] = useState(user?.company || '')
  const [isSaving, setIsSaving] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isAuthenticating, setIsAuthenticating] = useState(false)
  const [authStatus, setAuthStatus] = useState<'idle' | 'authenticating' | 'authenticated' | 'error'>('idle')
  const [authMessage, setAuthMessage] = useState('')

  // Notification toggle state — persisted to localStorage
  const [emailNotifications, setEmailNotifications] = useState(() => {
    try { return JSON.parse(localStorage.getItem('pref_emailNotifications') ?? 'true') } catch { return true }
  })
  const [matchAlerts, setMatchAlerts] = useState(() => {
    try { return JSON.parse(localStorage.getItem('pref_matchAlerts') ?? 'true') } catch { return true }
  })
  const [weeklySummary, setWeeklySummary] = useState(() => {
    try { return JSON.parse(localStorage.getItem('pref_weeklySummary') ?? 'false') } catch { return false }
  })

  // Persist notification prefs
  useEffect(() => { localStorage.setItem('pref_emailNotifications', JSON.stringify(emailNotifications)) }, [emailNotifications])
  useEffect(() => { localStorage.setItem('pref_matchAlerts', JSON.stringify(matchAlerts)) }, [matchAlerts])
  useEffect(() => { localStorage.setItem('pref_weeklySummary', JSON.stringify(weeklySummary)) }, [weeklySummary])

  // Data management state
  const [isReprocessing, setIsReprocessing] = useState(false)
  const [isRescoring, setIsRescoring] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [isCleaningUp, setIsCleaningUp] = useState(false)
  const [isAuditing, setIsAuditing] = useState(false)
  const [isFullRepairing, setIsFullRepairing] = useState(false)
  const [isRelooking, setIsRelooking] = useState(false)

  const handleReprocessGarbled = async () => {
    setIsReprocessing(true)
    try {
      const result = await candidateApi.reprocessGarbled()
      const data = (result?.data ?? result) as { cleaned?: number; rescored?: number; encoding_fixed?: number }
      addNotification({
        type: 'success',
        title: 'Reprocessing Complete',
        message: `Cleaned: ${data?.cleaned || 0}, Rescored: ${data?.rescored || 0}, Encoding fixed: ${data?.encoding_fixed || 0}`,
      })
      // Clear cache so candidates page shows fresh data
      try {
        sessionStorage.removeItem('candidates_cache')
        sessionStorage.removeItem('candidates_cache_ts')
      } catch {}
    } catch (error) {
      addNotification({ type: 'error', title: 'Reprocess Failed', message: 'Could not reprocess candidates' })
    } finally {
      setIsReprocessing(false)
    }
  }

  const handleRescoreAll = async () => {
    setIsRescoring(true)
    try {
      const result = await candidateApi.reprocessScores()
      const data = (result?.data ?? result) as { processed?: number }
      addNotification({
        type: 'success',
        title: 'Rescoring Complete',
        message: `Processed: ${data?.processed || 0} candidates`,
      })
      try { sessionStorage.removeItem('candidates_cache'); sessionStorage.removeItem('candidates_cache_ts') } catch {}
    } catch (error) {
      addNotification({ type: 'error', title: 'Rescore Failed', message: 'Could not rescore candidates' })
    } finally {
      setIsRescoring(false)
    }
  }

  const handleSyncNow = async () => {
    setIsSyncing(true)
    try {
      const response = await authFetch(`${config.apiUrl}/api/email/sync-now`, { method: 'POST' })
      if (!response.ok) throw new Error(`Sync failed: ${response.status}`)
      addNotification({ type: 'success', title: 'Sync Started', message: 'Email sync triggered. New candidates will appear shortly.' })
    } catch (error) {
      addNotification({ type: 'error', title: 'Sync Failed', message: 'Could not start email sync' })
    } finally {
      setTimeout(() => setIsSyncing(false), 3000)
    }
  }

  const handleCleanupGibberish = async () => {
    setIsCleaningUp(true)
    try {
      const result = await candidateApi.cleanupGibberish()
      const data = (result?.data ?? result) as { deleted_count?: number; reprocessed_count?: number; encoding_fixed_count?: number }
      addNotification({
        type: 'success',
        title: 'Cleanup Complete',
        message: `Deleted: ${data?.deleted_count || 0}, Reprocessed: ${data?.reprocessed_count || 0}, Fixed: ${data?.encoding_fixed_count || 0}`,
      })
      try { sessionStorage.removeItem('candidates_cache'); sessionStorage.removeItem('candidates_cache_ts') } catch {}
    } catch (error) {
      addNotification({ type: 'error', title: 'Cleanup Failed', message: 'Could not cleanup profiles' })
    } finally {
      setIsCleaningUp(false)
    }
  }

  const handleDatabaseAudit = async () => {
    setIsAuditing(true)
    try {
      const result = await candidateApi.databaseAudit()
      const data = (result?.data ?? result) as { total_candidates?: number; active_candidates?: number; issues?: Record<string, number> }
      const issues = data?.issues || {}
      const totalIssues = Object.values(issues).reduce((s: number, v: unknown) => s + (typeof v === 'number' ? v : 0), 0)
      addNotification({
        type: totalIssues > 0 ? 'warning' : 'success',
        title: 'Database Audit Complete',
        message: `${data?.total_candidates || 0} candidates, ${data?.active_candidates || 0} active. ${totalIssues} issue${totalIssues !== 1 ? 's' : ''} found${totalIssues > 0 ? `: bad names(${issues.invalid_names || 0}), zero score(${issues.zero_score || 0}), mojibake(${issues.mojibake || 0}), system emails(${issues.system_emails || 0})` : ''}.`,
      })
    } catch (error) {
      addNotification({ type: 'error', title: 'Audit Failed', message: 'Could not audit database' })
    } finally {
      setIsAuditing(false)
    }
  }

  const handleFullRepair = async () => {
    setIsFullRepairing(true)
    try {
      const result = await candidateApi.fullDatabaseRepair()
      const data = (result?.data ?? result) as { repair?: { summary?: Record<string, number> }; rescore?: { rescored?: number } }
      const repair = data?.repair?.summary || {}
      const rescore = data?.rescore || {}
      addNotification({
        type: 'success',
        title: 'Full Repair Complete',
        message: `Deleted: ${repair.deleted || 0}, Encoding fixed: ${repair.encoding_fixed || 0}, Names recovered: ${repair.names_recovered || 0}, Rescored: ${rescore.rescored || 0}`,
      })
      try { sessionStorage.removeItem('candidates_cache'); sessionStorage.removeItem('candidates_cache_ts') } catch {}
    } catch (error) {
      addNotification({ type: 'error', title: 'Full Repair Failed', message: 'Could not repair database' })
    } finally {
      setIsFullRepairing(false)
    }
  }

  const handleRelookupFromEmail = async () => {
    setIsRelooking(true)
    try {
      const result = await candidateApi.relookupFromEmail()
      const data = (result?.data ?? result) as { checked?: number; improved?: number; errors?: number }
      addNotification({
        type: 'success',
        title: 'Email Re-lookup Complete',
        message: `Checked: ${data?.checked || 0}, Improved: ${data?.improved || 0}, Errors: ${data?.errors || 0}`,
      })
      try { sessionStorage.removeItem('candidates_cache'); sessionStorage.removeItem('candidates_cache_ts') } catch {}
    } catch (error) {
      addNotification({ type: 'error', title: 'Re-lookup Failed', message: 'Could not re-lookup emails. Ensure Microsoft Graph / OAuth is configured.' })
    } finally {
      setIsRelooking(false)
    }
  }

  const handleSaveProfile = async () => {
    setIsSaving(true)
    try {
      const response = await authFetch(`${config.apiUrl}/api/users/profile`, {
        method: 'PUT',
        headers: { 
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          firstName,
          lastName,
          email,
          company
        })
      })
      
      if (!response.ok) {
        throw new Error('Failed to update profile')
      }
      
      await response.json()
      // Update the auth store so the UI reflects the change immediately (local only, no extra API call)
      const currentUser = useAuthStore.getState().user
      if (currentUser) {
        useAuthStore.setState({ user: { ...currentUser, name: `${firstName} ${lastName}`.trim(), email, company } })
      }
      addNotification({
        type: 'success',
        title: 'Profile Updated',
        message: 'Your profile has been updated successfully',
      })
    } catch (error) {
      console.error('Save error:', error)
      toast.error('Failed to save profile')
      addNotification({
        type: 'error',
        title: 'Error',
        message: 'Failed to save profile',
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleUpdatePassword = async () => {
    if (!currentPassword || !newPassword || !confirmPassword) {
      toast.warning('Missing fields', 'Please fill in all password fields')
      return
    }
    
    if (newPassword !== confirmPassword) {
      toast.warning('Password mismatch', 'New passwords do not match')
      return
    }
    
    try {
      const response = await authFetch(`${config.apiUrl}/api/users/password`, {
        method: 'PUT',
        headers: { 
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          currentPassword,
          newPassword
        })
      })
      
      if (!response.ok) {
        throw new Error('Failed to update password')
      }
      
      addNotification({
        type: 'success',
        title: 'Password Changed',
        message: 'Your password has been updated successfully',
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (error) {
      console.error('Password update error:', error)
      toast.error('Failed to update password')
      addNotification({
        type: 'error',
        title: 'Error',
        message: 'Failed to update password',
      })
    }
  }

  const handleAutoAuthenticate = async () => {
    setIsAuthenticating(true)
    setAuthStatus('authenticating')
    setAuthMessage('Redirecting to Microsoft login...')
    
    try {
      // Get OAuth URL from backend (uses delegated flow - works with your Azure AD app)
      const response = await authFetch(`${config.apiUrl}/api/email/oauth2/url`)
      
      if (!response.ok) {
        throw new Error('Failed to get authentication URL')
      }
      
      const data = await response.json()
      
      // Redirect to Microsoft login page
      // After login, Microsoft redirects back to /auth/callback which handles the token
      window.location.href = data.auth_url
    } catch (error) {
      console.error('Authentication error:', error)
      setAuthStatus('error')
      setAuthMessage(`Authentication failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
      
      addNotification({
        type: 'error',
        title: 'Authentication Failed',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
      
      // Reset after 5 seconds
      setTimeout(() => {
        setAuthStatus('idle')
        setAuthMessage('')
      }, 5000)
    } finally {
      setIsAuthenticating(false)
    }
  }
  
  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <h1 className="text-3xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-600 mt-1">Manage your account and preferences</p>
      </motion.div>

      {/* Profile Settings */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
      >
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="w-5 h-5" />
              Profile Information
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  First Name
                </label>
                <Input
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  placeholder="John"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Last Name
                </label>
                <Input
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  placeholder="Doe"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Email Address
              </label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="john.doe@company.com"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Company
              </label>
              <Input
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="Company Name"
              />
            </div>
            <Button onClick={handleSaveProfile} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save Changes'}
            </Button>
          </CardContent>
        </Card>
      </motion.div>

      {/* Email Integration Settings */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mail className="w-5 h-5" />
              Email & Authentication
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <p className="font-medium text-gray-900 mb-2">Microsoft Outlook Authentication</p>
              <p className="text-sm text-gray-600 mb-4">
                Authenticate once to sync all emails and candidates. You won't need to authenticate again.
              </p>
              <Button 
                onClick={handleAutoAuthenticate} 
                disabled={isAuthenticating}
                variant={authStatus === 'authenticated' ? 'outline' : 'default'}
                className="w-full"
              >
                {isAuthenticating ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Authenticating...
                  </>
                ) : authStatus === 'authenticated' ? (
                  <>
                    <CheckCircle className="w-4 h-4 mr-2" />
                    Authenticated
                  </>
                ) : (
                  'Authenticate & Sync Emails'
                )}
              </Button>
              {authMessage && (
                <p className={`text-sm mt-3 ${authStatus === 'authenticated' ? 'text-green-700' : authStatus === 'error' ? 'text-red-700' : 'text-blue-700'}`}>
                  {authMessage}
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Notification Settings */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
      >
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Bell className="w-5 h-5" />
              Notifications
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <label htmlFor="email-notifications" className="flex items-center justify-between cursor-pointer">
              <div>
                <p className="font-medium text-gray-900">Email Notifications</p>
                <p id="email-notifications-desc" className="text-sm text-gray-600">Receive email updates about new candidates</p>
              </div>
              <input
                id="email-notifications"
                type="checkbox"
                className="w-5 h-5 text-sky-600 rounded"
                aria-describedby="email-notifications-desc"
                checked={emailNotifications}
                onChange={(e) => setEmailNotifications(e.target.checked)}
              />
            </label>
            <label htmlFor="match-alerts" className="flex items-center justify-between cursor-pointer">
              <div>
                <p className="font-medium text-gray-900">Match Alerts</p>
                <p id="match-alerts-desc" className="text-sm text-gray-600">Get notified about high-match candidates</p>
              </div>
              <input
                id="match-alerts"
                type="checkbox"
                className="w-5 h-5 text-sky-600 rounded"
                aria-describedby="match-alerts-desc"
                checked={matchAlerts}
                onChange={(e) => setMatchAlerts(e.target.checked)}
              />
            </label>
            <label htmlFor="weekly-summary" className="flex items-center justify-between cursor-pointer">
              <div>
                <p className="font-medium text-gray-900">Weekly Summary</p>
                <p id="weekly-summary-desc" className="text-sm text-gray-600">Weekly recruitment metrics summary</p>
              </div>
              <input
                id="weekly-summary"
                type="checkbox"
                className="w-5 h-5 text-sky-600 rounded"
                aria-describedby="weekly-summary-desc"
                checked={weeklySummary}
                onChange={(e) => setWeeklySummary(e.target.checked)}
              />
            </label>
          </CardContent>
        </Card>
      </motion.div>

      {/* Security Settings */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
      >
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="w-5 h-5" />
              Data Management
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {isAdmin && (<>
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <p className="font-medium text-gray-900">Sync Emails Now</p>
                <p className="text-sm text-gray-600">Trigger an immediate sync to fetch new candidates from your inbox</p>
              </div>
              <Button onClick={handleSyncNow} disabled={isSyncing} size="sm" className="flex-shrink-0">
                {isSyncing ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-1.5" />}
                {isSyncing ? 'Syncing...' : 'Sync Now'}
              </Button>
            </div>
            <div className="border-t border-gray-100" />
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <p className="font-medium text-gray-900">Re-score Unscored Candidates</p>
                <p className="text-sm text-gray-600">Recalculate AI match scores for candidates with 0% score</p>
              </div>
              <Button onClick={handleRescoreAll} disabled={isRescoring} size="sm" variant="outline" className="flex-shrink-0">
                {isRescoring ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-1.5" />}
                {isRescoring ? 'Scoring...' : 'Re-score'}
              </Button>
            </div>
            <div className="border-t border-gray-100" />
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <p className="font-medium text-gray-900">Reprocess Garbled Candidates</p>
                <p className="text-sm text-gray-600">Fix encoding issues, remove system/bot profiles, and re-score candidates with missing or default scores</p>
              </div>
              <Button onClick={handleReprocessGarbled} disabled={isReprocessing} size="sm" variant="outline" className="flex-shrink-0">
                {isReprocessing ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Zap className="w-4 h-4 mr-1.5" />}
                {isReprocessing ? 'Processing...' : 'Reprocess'}
              </Button>
            </div>
            <div className="border-t border-gray-100" />
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-gray-900">Cleanup Gibberish Profiles</p>
                  <span className="text-[10px] text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded-full font-medium">Destructive</span>
                </div>
                <p className="text-sm text-gray-600">Remove bot/system profiles, chat transcripts, and unfixable garbled entries</p>
              </div>
              <Button onClick={handleCleanupGibberish} disabled={isCleaningUp} size="sm" variant="outline" className="flex-shrink-0 border-amber-200 text-amber-700 hover:bg-amber-50">
                {isCleaningUp ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Trash2 className="w-4 h-4 mr-1.5" />}
                {isCleaningUp ? 'Cleaning...' : 'Cleanup'}
              </Button>
            </div>
            <div className="border-t border-gray-100" />
            {/* Database Audit */}
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <p className="font-medium text-gray-900">Database Audit</p>
                <p className="text-sm text-gray-600">Run a read-only health check to see how many gibberish, zero-score, or corrupted profiles exist</p>
              </div>
              <Button onClick={handleDatabaseAudit} disabled={isAuditing} size="sm" variant="outline" className="flex-shrink-0">
                {isAuditing ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}
                {isAuditing ? 'Auditing...' : 'Audit'}
              </Button>
            </div>
            <div className="border-t border-gray-100" />
            {/* Full Database Repair */}
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-gray-900">Full Database Repair</p>
                  <span className="text-[10px] text-red-600 bg-red-50 px-1.5 py-0.5 rounded-full font-medium">Heavy</span>
                </div>
                <p className="text-sm text-gray-600">Nuclear option: cleanup + fix encoding + recover names + deduplicate + re-score all candidates</p>
              </div>
              <Button onClick={handleFullRepair} disabled={isFullRepairing} size="sm" variant="outline" className="flex-shrink-0 border-red-200 text-red-700 hover:bg-red-50">
                {isFullRepairing ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Shield className="w-4 h-4 mr-1.5" />}
                {isFullRepairing ? 'Repairing...' : 'Full Repair'}
              </Button>
            </div>
            <div className="border-t border-gray-100" />
            {/* Re-lookup from Email */}
            <div className="flex items-start justify-between gap-4">
              <div className="flex-1">
                <p className="font-medium text-gray-900">Re-lookup from Email</p>
                <p className="text-sm text-gray-600">Search original emails via Microsoft Graph to recover data for candidates with bad/empty profiles</p>
              </div>
              <Button onClick={handleRelookupFromEmail} disabled={isRelooking} size="sm" variant="outline" className="flex-shrink-0">
                {isRelooking ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <FileSearch className="w-4 h-4 mr-1.5" />}
                {isRelooking ? 'Looking up...' : 'Re-lookup'}
              </Button>
            </div>
            </>)}
          </CardContent>
        </Card>
      </motion.div>

      {/* Security Settings */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
      >
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lock className="w-5 h-5" />
              Security
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Current Password
              </label>
              <Input type="password" placeholder="••••••••" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                New Password
              </label>
              <Input type="password" placeholder="••••••••" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Confirm New Password
              </label>
              <Input type="password" placeholder="••••••••" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} />
            </div>
            <Button onClick={handleUpdatePassword}>Update Password</Button>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  )
}
