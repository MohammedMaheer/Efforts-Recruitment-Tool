import { motion } from 'framer-motion'
import { useState } from 'react'
import { User, Bell, Lock, Mail, Loader2, CheckCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useAuthStore } from '@/store/authStore'
import { useNotificationStore } from '@/store/notificationStore'
import config from '@/config'

export default function Settings() {
  const user = useAuthStore((state) => state.user)
  const addNotification = useNotificationStore((state) => state.addNotification)
  const [firstName, setFirstName] = useState(user?.name?.split(' ')[0] || '')
  const [lastName, setLastName] = useState(user?.name?.split(' ')[1] || '')
  const [email, setEmail] = useState(user?.email || '')
  const [company, setCompany] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isAuthenticating, setIsAuthenticating] = useState(false)
  const [authStatus, setAuthStatus] = useState<'idle' | 'authenticating' | 'authenticated' | 'error'>('idle')
  const [authMessage, setAuthMessage] = useState('')

  const handleSaveProfile = async () => {
    setIsSaving(true)
    try {
      const response = await fetch(`${config.apiUrl}/api/users/profile`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
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
      addNotification({
        type: 'success',
        title: 'Profile Updated',
        message: 'Your profile has been updated successfully',
      })
      alert('Profile updated successfully!')
    } catch (error) {
      console.error('Save error:', error)
      alert('Failed to save profile')
    } finally {
      setIsSaving(false)
    }
  }

  const handleUpdatePassword = async () => {
    if (!currentPassword || !newPassword || !confirmPassword) {
      alert('Please fill in all password fields')
      return
    }
    
    if (newPassword !== confirmPassword) {
      alert('New passwords do not match')
      return
    }
    
    try {
      const response = await fetch(`${config.apiUrl}/api/users/password`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
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
      alert('Password updated successfully!')
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (error) {
      console.error('Password update error:', error)
      alert('Failed to update password')
    }
  }

  const handleAutoAuthenticate = async () => {
    setIsAuthenticating(true)
    setAuthStatus('authenticating')
    setAuthMessage('Redirecting to Microsoft login...')
    
    try {
      // Get OAuth URL from backend (uses delegated flow - works with your Azure AD app)
      const response = await fetch(`${config.apiUrl}/api/email/oauth2/url`)
      
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
      setAuthMessage(`❌ Authentication failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
      
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
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium text-gray-900">Email Notifications</p>
                <p className="text-sm text-gray-600">Receive email updates about new candidates</p>
              </div>
              <input type="checkbox" className="w-5 h-5 text-primary-600 rounded" defaultChecked />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium text-gray-900">Match Alerts</p>
                <p className="text-sm text-gray-600">Get notified about high-match candidates</p>
              </div>
              <input type="checkbox" className="w-5 h-5 text-primary-600 rounded" defaultChecked />
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium text-gray-900">Weekly Summary</p>
                <p className="text-sm text-gray-600">Weekly recruitment metrics summary</p>
              </div>
              <input type="checkbox" className="w-5 h-5 text-primary-600 rounded" />
            </div>
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
