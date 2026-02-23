/**
 * Main Application Component
 * Root component with routing, error handling, and global providers
 */
import { useEffect, useState, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { ToastContainer } from '@/components/ui/Toast'
import config from '@/config'
import LoginPage from '@/pages/LoginPage'
import OAuthCallback from '@/pages/OAuthCallback'
import DashboardLayout from '@/components/layout/DashboardLayout'

// Lazy-load route components for code splitting
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Candidates = lazy(() => import('@/pages/Candidates'))
const CandidateDetail = lazy(() => import('@/pages/CandidateDetail'))
const Shortlist = lazy(() => import('@/pages/Shortlist'))
const Settings = lazy(() => import('@/pages/Settings'))
const AIAssistant = lazy(() => import('@/pages/AIAssistant'))
const UploadFiles = lazy(() => import('@/pages/UploadFiles'))
const SearchReports = lazy(() => import('@/pages/SearchReports'))
const JDBuilder = lazy(() => import('@/pages/JDBuilder'))
const SetupWizard = lazy(() => import('@/pages/SetupWizard'))

/** Route loading fallback */
function RouteFallback() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="w-8 h-8 border-4 border-sky-600 border-t-transparent rounded-full animate-spin"></div>
    </div>
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
  }, [])

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
          <Route path="dashboard" element={<Suspense fallback={<RouteFallback />}><Dashboard /></Suspense>} />
          <Route path="ai-assistant" element={<Suspense fallback={<RouteFallback />}><AIAssistant /></Suspense>} />
          <Route path="upload" element={<Suspense fallback={<RouteFallback />}><UploadFiles /></Suspense>} />
          <Route path="search-reports" element={<Suspense fallback={<RouteFallback />}><SearchReports /></Suspense>} />
          <Route path="jd-builder" element={<Suspense fallback={<RouteFallback />}><JDBuilder /></Suspense>} />
          <Route path="candidates" element={<Suspense fallback={<RouteFallback />}><Candidates /></Suspense>} />
          <Route path="candidates/:id" element={<Suspense fallback={<RouteFallback />}><CandidateDetail /></Suspense>} />
          <Route path="shortlist" element={<Suspense fallback={<RouteFallback />}><Shortlist /></Suspense>} />
          <Route path="email-integration" element={<Navigate to="/setup" replace />} />
          <Route path="settings" element={<Suspense fallback={<RouteFallback />}><Settings /></Suspense>} />
          <Route path="setup" element={<Suspense fallback={<RouteFallback />}><SetupWizard /></Suspense>} />
        </Route>

        {/* Fallback Route */}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </ErrorBoundary>
  )
}

export default App
