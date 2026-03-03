/**
 * Main Application Component
 * Root component with routing, error handling, and global providers
 */
import { useEffect, useState, Suspense } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ToastContainer } from '@/components/ui/Toast'
import { lazyRetry } from '@/lib/lazyRetry'
import config from '@/config'
import LoginPage from '@/pages/LoginPage'
import OAuthCallback from '@/pages/OAuthCallback'
import DashboardLayout from '@/components/layout/DashboardLayout'

// Lazy-load route components for code splitting (with auto-retry on stale chunks)
const Dashboard = lazyRetry(() => import('@/pages/Dashboard'))
const Candidates = lazyRetry(() => import('@/pages/Candidates'))
const CandidateDetail = lazyRetry(() => import('@/pages/CandidateDetail'))
const Shortlist = lazyRetry(() => import('@/pages/Shortlist'))
const Settings = lazyRetry(() => import('@/pages/Settings'))
const AIAssistant = lazyRetry(() => import('@/pages/AIAssistant'))
const UploadFiles = lazyRetry(() => import('@/pages/UploadFiles'))
const SearchReports = lazyRetry(() => import('@/pages/SearchReports'))
const JDBuilder = lazyRetry(() => import('@/pages/JDBuilder'))
const SetupWizard = lazyRetry(() => import('@/pages/SetupWizard'))

/** Route loading fallback */
function RouteFallback() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="w-8 h-8 border-4 border-sky-600 border-t-transparent rounded-full animate-spin"></div>
    </div>
  )
}

/** Route with error isolation — wraps Suspense in its own ErrorBoundary */
function RouteWithErrorBoundary({ children }: { children: React.ReactNode }) {
  return (
    <ErrorBoundary>
      <Suspense fallback={<RouteFallback />}>
        {children}
      </Suspense>
    </ErrorBoundary>
  )
}

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
  const [isVerifying, setIsVerifying] = useState(true)
  const verifyToken = useAuthStore((state) => state.verifyToken)
  const token = useAuthStore((state) => state.token)
  const navigate = useNavigate()
  
  // Listen for session-expired events from the API layer (avoids hard reload)
  useEffect(() => {
    const handler = () => navigate('/login', { replace: true })
    window.addEventListener('auth:session-expired', handler)
    return () => window.removeEventListener('auth:session-expired', handler)
  }, [navigate])

  // Warm up Cloud Run (fire-and-forget) then verify token
  useEffect(() => {
    const verify = async () => {
      // Wake up Cloud Run backend with a lightweight health ping
      fetch(`${config.apiUrl}/health`, { method: 'GET' }).catch(() => {})
      if (token) {
        await verifyToken()
      }
      setIsVerifying(false)
    }
    verify()
  }, [token, verifyToken])

  // Show loading state while verifying token
  if (isVerifying) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-sky-600 border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-gray-600">Loading...</p>
        </div>
      </div>
    )
  }
  
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
          <Route path="dashboard" element={<RouteWithErrorBoundary><Dashboard /></RouteWithErrorBoundary>} />
          <Route path="ai-assistant" element={<RouteWithErrorBoundary><AIAssistant /></RouteWithErrorBoundary>} />
          <Route path="upload" element={<RouteWithErrorBoundary><UploadFiles /></RouteWithErrorBoundary>} />
          <Route path="search-reports" element={<RouteWithErrorBoundary><SearchReports /></RouteWithErrorBoundary>} />
          <Route path="jd-builder" element={<RouteWithErrorBoundary><JDBuilder /></RouteWithErrorBoundary>} />
          <Route path="candidates" element={<RouteWithErrorBoundary><Candidates /></RouteWithErrorBoundary>} />
          <Route path="candidates/:id" element={<RouteWithErrorBoundary><CandidateDetail /></RouteWithErrorBoundary>} />
          <Route path="shortlist" element={<RouteWithErrorBoundary><Shortlist /></RouteWithErrorBoundary>} />
          <Route path="email-integration" element={<Navigate to="/setup" replace />} />
          <Route path="settings" element={<RouteWithErrorBoundary><Settings /></RouteWithErrorBoundary>} />
          <Route path="setup" element={<RouteWithErrorBoundary><SetupWizard /></RouteWithErrorBoundary>} />
        </Route>

        {/* Fallback Route */}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </ErrorBoundary>
  )
}

export default App
