/**
 * Main Application Component
 * Root component with routing, error handling, and global providers
 */
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ToastContainer } from '@/components/ui/Toast'
import LoginPage from '@/pages/LoginPage'
import OAuthCallback from '@/pages/OAuthCallback'
import DashboardLayout from '@/components/layout/DashboardLayout'
import Dashboard from '@/pages/Dashboard'
import Candidates from '@/pages/Candidates'
import CandidateDetail from '@/pages/CandidateDetail'
import Shortlist from '@/pages/Shortlist'
import Settings from '@/pages/Settings'
import EmailIntegration from '@/components/EmailIntegration'
import AIAssistant from '@/pages/AIAssistant'
import AnalyticsDashboard from '@/components/AnalyticsDashboard'
import JobDescriptions from '@/pages/JobDescriptions'
import SetupWizard from '@/pages/SetupWizard'

/**
 * Protected Route Wrapper
 * Redirects to login if not authenticated
 */
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }
  
  return <>{children}</>
}

/**
 * Public Route Wrapper
 * Redirects to dashboard if already authenticated
 */
function PublicRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  
  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />
  }
  
  return <>{children}</>
}

/**
 * Main Application Component
 */
function App() {
  return (
    <ErrorBoundary>
      {/* Global Toast Notifications */}
      <ToastContainer />
      
      {/* Application Routes */}
      <Routes>
        {/* Public Routes */}
        <Route 
          path="/login" 
          element={
            <PublicRoute>
              <LoginPage />
            </PublicRoute>
          } 
        />
        <Route path="/auth/callback" element={<OAuthCallback />} />
        
        {/* Protected Routes */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <DashboardLayout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="ai-assistant" element={<AIAssistant />} />
          <Route path="analytics" element={<AnalyticsDashboard />} />
          <Route path="candidates" element={<Candidates />} />
          <Route path="candidates/:id" element={<CandidateDetail />} />
          <Route path="jobs" element={<JobDescriptions />} />
          <Route path="shortlist" element={<Shortlist />} />
          <Route path="email-integration" element={<EmailIntegration />} />
          <Route path="settings" element={<Settings />} />
          <Route path="setup" element={<SetupWizard />} />
        </Route>

        {/* Fallback Route */}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </ErrorBoundary>
  )
}

export default App
