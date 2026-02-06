import { motion } from 'framer-motion'
import { Users, TrendingUp, Award, Clock, ArrowUpRight, ArrowDownRight, Sparkles, Target, Zap, CheckCircle2, Calendar, Mail, Info, RefreshCw, Loader2, Briefcase, Upload, X, FileText, CheckCircle, AlertCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/Avatar'
import { Badge } from '@/components/ui/Badge'
import { Progress } from '@/components/ui/Progress'
import { Button } from '@/components/ui/Button'
import { getMatchScoreColor } from '@/lib/utils'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/store/authStore'
import { useState, useMemo, useRef, useCallback } from 'react'
import { useAIStatus } from '@/hooks/useAIStatus'
import { useCandidates } from '@/hooks/useCandidates'
import config from '@/config'

// Category colors for visual distinction
const categoryColors: Record<string, { bg: string; text: string; border: string }> = {
  'Software Engineer': { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  'DevOps Engineer': { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' },
  'Data Scientist': { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
  'Marketing': { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200' },
  'Sales': { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
  'Product Manager': { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' },
  'HR': { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200' },
  'Finance': { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' },
  'Customer Support': { bg: 'bg-cyan-50', text: 'text-cyan-700', border: 'border-cyan-200' },
  'Design': { bg: 'bg-rose-50', text: 'text-rose-700', border: 'border-rose-200' },
  'General': { bg: 'bg-gray-50', text: 'text-gray-700', border: 'border-gray-200' },
}

const getCategoryColor = (category: string) => {
  return categoryColors[category] || categoryColors['General']
}

export default function Dashboard() {
  const { candidates, loading: _loading, error: _error, refetch, stats } = useCandidates({ 
    autoFetch: true,
    refreshInterval: 60000 // Refresh every minute
  })
  const user = useAuthStore((state) => state.user)
  const navigate = useNavigate()
  const [showTips, setShowTips] = useState(true)
  const [isSyncing, setIsSyncing] = useState(false)
  const aiStatus = useAIStatus()

  // Upload state
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadResults, setUploadResults] = useState<any[]>([])
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Handle file upload
  const handleFileUpload = useCallback(async (files: FileList | File[]) => {
    const fileArray = Array.from(files)
    if (fileArray.length === 0) return

    // Filter valid files
    const validFiles = fileArray.filter(f => 
      f.name.toLowerCase().endsWith('.pdf') || f.name.toLowerCase().endsWith('.docx')
    )
    
    if (validFiles.length === 0) {
      alert('Please upload PDF or DOCX files only.')
      return
    }

    setIsUploading(true)
    setUploadResults([])
    setShowUploadModal(true)

    try {
      const formData = new FormData()
      validFiles.forEach(file => formData.append('files', file))

      const response = await fetch(`${config.apiUrl}/api/resumes/upload-multiple`, {
        method: 'POST',
        body: formData,
      })

      if (response.ok) {
        const data = await response.json()
        setUploadResults(data.results || [])
        // Refresh candidates list after upload
        setTimeout(() => refetch(), 1000)
      } else {
        const error = await response.json()
        setUploadResults([{ status: 'error', message: error.detail || 'Upload failed' }])
      }
    } catch (error: any) {
      setUploadResults([{ status: 'error', message: error.message || 'Network error' }])
    } finally {
      setIsUploading(false)
    }
  }, [refetch])

  // Drag and drop handlers
  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileUpload(e.dataTransfer.files)
    }
  }, [handleFileUpload])

  const openFileDialog = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      handleFileUpload(e.target.files)
    }
  }, [handleFileUpload])

  // Instant sync - triggers immediate email check
  const handleInstantSync = async () => {
    setIsSyncing(true)
    try {
      const response = await fetch(`${config.apiUrl}/api/email/sync-now`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      })
      
      if (response.ok) {
        // Wait a moment for processing then refresh candidates
        setTimeout(() => {
          refetch()
        }, 2000)
      }
    } catch (error) {
      console.error('Instant sync error:', error)
    } finally {
      setIsSyncing(false)
    }
  }

  // Calculate previous week stats for comparison
  const twoWeeksAgo = new Date()
  twoWeeksAgo.setDate(twoWeeksAgo.getDate() - 14)
  const oneWeekAgo = new Date()
  oneWeekAgo.setDate(oneWeekAgo.getDate() - 7)
  
  const previousWeekUploads = candidates.filter((c) => {
    const date = new Date(c.appliedDate)
    return date >= twoWeeksAgo && date < oneWeekAgo
  }).length

  const uploadTrend = previousWeekUploads > 0 
    ? Math.round(((stats.recentCount - previousWeekUploads) / previousWeekUploads) * 100)
    : 0

  const recentCandidates = candidates.slice(0, 5)

  // Calculate category stats
  const categoryStats = useMemo(() => {
    const groups: Record<string, { count: number; avgScore: number; topScore: number }> = {}
    candidates.forEach((candidate) => {
      const category = candidate.jobCategory || 'General'
      if (!groups[category]) {
        groups[category] = { count: 0, avgScore: 0, topScore: 0 }
      }
      groups[category].count++
      groups[category].avgScore += candidate.matchScore
      if (candidate.matchScore > groups[category].topScore) {
        groups[category].topScore = candidate.matchScore
      }
    })
    
    // Calculate averages
    Object.keys(groups).forEach((category) => {
      groups[category].avgScore = Math.round(groups[category].avgScore / groups[category].count)
    })
    
    return groups
  }, [candidates])

  // Quick actions
  const quickActions = [
    {
      icon: Sparkles,
      title: 'AI Assistant',
      description: 'Smart candidate search',
      color: 'primary',
      action: () => navigate('/ai-assistant')
    },
    {
      icon: Zap,
      title: 'Upload Resumes',
      description: 'Add new candidates',
      color: 'purple',
      action: () => navigate('/job-descriptions')
    },
    {
      icon: Target,
      title: 'View Shortlist',
      description: 'Top candidates',
      color: 'success',
      action: () => navigate('/shortlist')
    },
    {
      icon: Mail,
      title: 'Email Sync',
      description: 'Connect inbox',
      color: 'warning',
      action: () => navigate('/email-integration')
    },
    {
      icon: Users,
      title: 'All Candidates',
      description: 'Browse pipeline',
      color: 'primary',
      action: () => navigate('/candidates')
    },
    {
      icon: CheckCircle2,
      title: 'Settings',
      description: 'Account & profile',
      color: 'purple',
      action: () => navigate('/settings')
    },
  ]

  const getCurrentGreeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 18) return 'Good afternoon'
    return 'Good evening'
  }

  return (
    <div className="space-y-6">
      {/* Header with Greeting */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="relative overflow-hidden"
      >
        <div className="absolute inset-0 bg-gradient-to-r from-primary-50 via-purple-50 to-pink-50 rounded-2xl opacity-50" />
        <div className="relative p-8 rounded-2xl border border-gray-200 bg-white/50 backdrop-blur-sm">
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Sparkles className="w-5 h-5 text-primary-600" />
                <p className="text-sm font-medium text-primary-600">Dashboard Overview</p>
              </div>
              <h1 className="text-4xl font-bold text-gray-900 mb-2">
                {getCurrentGreeting()}, {user?.firstName || 'Recruiter'}!
              </h1>
              <p className="text-gray-600 mb-4">Here's what's happening with your recruitment today.</p>
              <div className="flex items-center gap-3">
                <motion.button
                  onClick={() => navigate('/ai-assistant')}
                  className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-primary-600 to-purple-600 text-white rounded-lg hover:shadow-lg transition-all text-sm font-medium"
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.95 }}
                >
                  <Sparkles className="w-4 h-4" />
                  Try AI Search
                </motion.button>
                <motion.button
                  onClick={handleInstantSync}
                  disabled={isSyncing}
                  className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-green-500 to-emerald-500 text-white rounded-lg hover:shadow-lg transition-all text-sm font-medium disabled:opacity-50"
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.95 }}
                >
                  {isSyncing ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <RefreshCw className="w-4 h-4" />
                  )}
                  Sync Emails
                </motion.button>
                <motion.button
                  onClick={openFileDialog}
                  className="flex items-center gap-2 px-4 py-2 border-2 border-gray-200 bg-white text-gray-700 rounded-lg hover:border-primary-300 hover:bg-primary-50 transition-all text-sm font-medium"
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.95 }}
                >
                  <Upload className="w-4 h-4" />
                  Upload Resumes
                </motion.button>
                {/* Hidden file input */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx"
                  multiple
                  className="hidden"
                  onChange={handleFileInputChange}
                />
              </div>
            </div>
            <motion.div
              animate={{ 
                scale: [1, 1.05, 1],
                rotate: [0, 5, 0, -5, 0]
              }}
              transition={{ 
                duration: 3,
                repeat: Infinity,
                repeatDelay: 2
              }}
              className="hidden lg:block cursor-pointer"
              onClick={() => navigate('/candidates')}
              whileHover={{ scale: 1.1 }}
            >
              <div className="w-24 h-24 bg-gradient-to-br from-primary-500 to-purple-600 rounded-3xl flex items-center justify-center shadow-lg">
                <Users className="w-12 h-12 text-white" />
              </div>
            </motion.div>
          </div>
        </div>
      </motion.div>

      {/* Interactive Tips Banner */}
      {showTips && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, height: 0 }}
          className="relative"
        >
          <Card className="border-2 border-primary-200 bg-gradient-to-r from-primary-50 to-purple-50">
            <CardContent className="p-4">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 bg-primary-600 rounded-lg flex items-center justify-center flex-shrink-0">
                  <Info className="w-5 h-5 text-white" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="font-semibold text-gray-900">💡 Dashboard Tips</h3>
                    {/* AI Status Indicator */}
                    {!aiStatus.isLoading && (
                      <Badge 
                        variant={aiStatus.available ? "success" : "outline"}
                        className="text-xs"
                      >
                        {aiStatus.available ? (
                          <>
                            <Sparkles className="w-3 h-3 mr-1" />
                            AI: {aiStatus.model}
                          </>
                        ) : (
                          'AI: Local Mode'
                        )}
                      </Badge>
                    )}
                  </div>
                  <ul className="text-sm text-gray-700 space-y-1">
                    <li>• Click any <strong>stat card</strong> to navigate to that section</li>
                    <li>• Click <strong>skill badges</strong> to search candidates with that skill</li>
                    <li>• Use <strong>Quick Actions</strong> for instant navigation to key features</li>
                    <li>• Click candidate <strong>names</strong> or <strong>match scores</strong> to view details</li>
                    {aiStatus.available && (
                      <li className="text-primary-600">• 🎉 <strong>OpenAI is active</strong> - Enhanced AI features available!</li>
                    )}
                  </ul>
                </div>
                <button
                  onClick={() => setShowTips(false)}
                  className="text-gray-400 hover:text-gray-600 transition-colors"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* Stats Grid - Enhanced */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <div
          className="hover:-translate-y-1 transition-transform cursor-pointer"
          onClick={() => navigate('/candidates')}
        >
          <Card className="hover:shadow-large transition-all border-2 hover:border-primary-200 relative overflow-hidden group">
            <div className="absolute inset-0 bg-gradient-to-br from-primary-500/5 to-purple-500/5 opacity-0 group-hover:opacity-100 transition-opacity" />
            <CardContent className="p-6 relative">
              <div className="flex items-center justify-between mb-4">
                <div className="w-14 h-14 bg-gradient-to-br from-primary-500 to-primary-600 rounded-2xl flex items-center justify-center shadow-md">
                  <Users className="w-7 h-7 text-white" />
                </div>
                <ArrowUpRight className="w-5 h-5 text-gray-400 group-hover:text-primary-600 transition-colors" />
              </div>
              <div>
                <p className="text-sm font-medium text-gray-600 mb-1">Total Candidates</p>
                <p className="text-4xl font-bold text-gray-900">{stats.total}</p>
                <p className="text-xs text-gray-500 mt-2">Click to view all</p>
              </div>
            </CardContent>
          </Card>
        </div>

        <div
          className="hover:-translate-y-1 transition-transform cursor-pointer"
          onClick={() => navigate('/shortlist')}
        >
          <Card className="hover:shadow-large transition-all border-2 hover:border-success/30 relative overflow-hidden group">
            <div className="absolute inset-0 bg-gradient-to-br from-success/5 to-emerald-500/5 opacity-0 group-hover:opacity-100 transition-opacity" />
            <CardContent className="p-6 relative">
              <div className="flex items-center justify-between mb-4">
                <div className="w-14 h-14 bg-gradient-to-br from-success to-emerald-600 rounded-2xl flex items-center justify-center shadow-md">
                  <Award className="w-7 h-7 text-white" />
                </div>
                <CheckCircle2 className="w-5 h-5 text-success" />
              </div>
              <div>
                <p className="text-sm font-medium text-gray-600 mb-1">Strong Matches</p>
                <p className="text-4xl font-bold text-gray-900">{stats.strong}</p>
                <div className="mt-2 flex items-center gap-2">
                  <Progress 
                    value={stats.total > 0 ? (stats.strong / stats.total) * 100 : 0} 
                    className="h-1.5 flex-1"
                  />
                  <span className="text-xs font-semibold text-success">
                    {stats.total > 0 ? Math.round((stats.strong / stats.total) * 100) : 0}%
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        <div
          className="hover:-translate-y-1 transition-transform cursor-pointer"
          onClick={() => navigate('/ai-assistant')}
        >
          <Card className="hover:shadow-large transition-all border-2 hover:border-purple-200 relative overflow-hidden group">
            <div className="absolute inset-0 bg-gradient-to-br from-purple-500/5 to-pink-500/5 opacity-0 group-hover:opacity-100 transition-opacity" />
            <CardContent className="p-6 relative">
              <div className="flex items-center justify-between mb-4">
                <div className="w-14 h-14 bg-gradient-to-br from-purple-500 to-purple-600 rounded-2xl flex items-center justify-center shadow-md">
                  <TrendingUp className="w-7 h-7 text-white" />
                </div>
                <Target className="w-5 h-5 text-purple-600" />
              </div>
              <div>
                <p className="text-sm font-medium text-gray-600 mb-1">Avg Match Score</p>
                <p className="text-4xl font-bold text-gray-900">{stats.avgScore}%</p>
                <div className="mt-2">
                  <Progress value={stats.avgScore} className="h-1.5" />
                </div>
                <p className="text-xs text-gray-500 mt-2">Search with AI</p>
              </div>
            </CardContent>
          </Card>
        </div>

        <div
          className="hover:-translate-y-1 transition-transform cursor-pointer"
          onClick={() => navigate('/job-descriptions')}
        >
          <Card className="hover:shadow-large transition-all border-2 hover:border-warning/30 relative overflow-hidden group">
            <div className="absolute inset-0 bg-gradient-to-br from-warning/5 to-orange-500/5 opacity-0 group-hover:opacity-100 transition-opacity" />
            <CardContent className="p-6 relative">
              <div className="flex items-center justify-between mb-4">
                <div className="w-14 h-14 bg-gradient-to-br from-warning to-orange-600 rounded-2xl flex items-center justify-center shadow-md">
                  <Clock className="w-7 h-7 text-white" />
                </div>
                {uploadTrend !== 0 && (
                  uploadTrend > 0 ? (
                    <div className="flex items-center gap-1 text-success">
                      <ArrowUpRight className="w-4 h-4" />
                      <span className="text-xs font-semibold">+{uploadTrend}%</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 text-danger">
                      <ArrowDownRight className="w-4 h-4" />
                      <span className="text-xs font-semibold">{uploadTrend}%</span>
                    </div>
                  )
                )}
              </div>
              <div>
                <p className="text-sm font-medium text-gray-600 mb-1">Recent Uploads</p>
                <p className="text-4xl font-bold text-gray-900">{stats.recentCount}</p>
                <p className="text-xs text-gray-500 mt-2">Upload more resumes</p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Quick Actions */}
      <div>
        <div className="flex items-center gap-2 mb-4">
          <Zap className="w-5 h-5 text-primary-600" />
          <h2 className="text-xl font-semibold text-gray-900">Quick Actions</h2>
          <span className="text-sm text-gray-500 ml-2">Click to navigate</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {quickActions.map((action, index) => {
            const Icon = action.icon
            return (
              <div key={index}>
                <Card 
                  className="cursor-pointer hover:shadow-medium transition-all border-2 hover:border-primary-200 group"
                  onClick={action.action}
                >
                  <CardContent className="p-6">
                    <div className="flex items-center gap-4">
                      <div className={`w-12 h-12 bg-${action.color}-100 rounded-xl flex items-center justify-center group-hover:scale-110 transition-transform`}>
                        <Icon className={`w-6 h-6 text-${action.color}-600`} />
                      </div>
                      <div className="flex-1">
                        <h3 className="font-semibold text-gray-900 mb-0.5">{action.title}</h3>
                        <p className="text-sm text-gray-600">{action.description}</p>
                      </div>
                      <ArrowUpRight className="w-5 h-5 text-gray-400 group-hover:text-primary-600 group-hover:translate-x-1 group-hover:-translate-y-1 transition-all" />
                    </div>
                  </CardContent>
                </Card>
              </div>
            )
          })}
        </div>
      </div>

      {/* Category Breakdown */}
      {Object.keys(categoryStats).length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-4">
            <Briefcase className="w-5 h-5 text-primary-600" />
            <h2 className="text-xl font-semibold text-gray-900">Candidates by Category</h2>
            <span className="text-sm text-gray-500 ml-2">{Object.keys(categoryStats).length} categories</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {Object.entries(categoryStats)
              .sort(([, a], [, b]) => b.count - a.count)
              .map(([category, stats]) => {
                const colors = getCategoryColor(category)
                return (
                  <div key={category}>
                    <Card 
                      className={`cursor-pointer hover:shadow-medium transition-all border-2 ${colors.border} ${colors.bg} group`}
                      onClick={() => navigate(`/candidates?category=${encodeURIComponent(category)}`)}
                    >
                      <CardContent className="p-4">
                        <div className="flex items-center justify-between mb-3">
                          <div className={`w-10 h-10 bg-white rounded-xl flex items-center justify-center border ${colors.border}`}>
                            <Briefcase className={`w-5 h-5 ${colors.text}`} />
                          </div>
                          <Badge className={`${colors.bg} ${colors.text} border ${colors.border}`}>
                            {stats.count}
                          </Badge>
                        </div>
                        <h3 className={`font-semibold ${colors.text} mb-2 truncate`}>{category}</h3>
                        <div className="space-y-2">
                          <div className="flex items-center justify-between text-sm">
                            <span className="text-gray-600">Avg Score</span>
                            <span className={`font-bold ${getMatchScoreColor(stats.avgScore)}`}>{stats.avgScore}%</span>
                          </div>
                          <Progress 
                            value={stats.avgScore} 
                            className="h-1.5"
                            indicatorClassName={
                              stats.avgScore >= 70 ? 'bg-green-500' :
                              stats.avgScore >= 50 ? 'bg-yellow-500' : 'bg-red-500'
                            }
                          />
                          <div className="flex items-center justify-between text-xs text-gray-500">
                            <span>Top: {stats.topScore}%</span>
                            <ArrowUpRight className="w-4 h-4 opacity-0 group-hover:opacity-100 transition-opacity" />
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                )
              })}
          </div>
        </div>
      )}

      {/* Recent Candidates - Enhanced */}
      <div>
        <Card className="border-2">
          <CardHeader className="border-b border-gray-200 bg-gradient-to-r from-gray-50 to-white">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-primary-100 rounded-xl flex items-center justify-center">
                  <Users className="w-5 h-5 text-primary-600" />
                </div>
                <div>
                  <CardTitle>Recent Candidates</CardTitle>
                  <p className="text-sm text-gray-600 font-normal mt-0.5">Latest additions to your pipeline</p>
                </div>
              </div>
              <button
                onClick={() => navigate('/candidates')}
                className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors text-sm font-medium hover:scale-105 active:scale-95"
              >
                View all
                <ArrowUpRight className="w-4 h-4" />
              </button>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {recentCandidates.length === 0 ? (
              <div className="p-12 text-center">
                <div className="w-20 h-20 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Users className="w-10 h-10 text-gray-400" />
                </div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">No candidates yet</h3>
                <p className="text-gray-600 mb-4">Start by uploading resumes or connecting your email</p>
                <button
                  onClick={openFileDialog}
                  className="px-6 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors text-sm font-medium hover:scale-105 active:scale-95"
                >
                  Upload Resumes
                </button>
              </div>
            ) : (
              <div className="divide-y divide-gray-100">
                {recentCandidates.map((candidate) => (
                  <div
                    key={candidate.id}
                    className="p-6 hover:bg-gradient-to-r hover:from-gray-50 hover:to-white cursor-pointer transition-all group hover:translate-x-1"
                    onClick={() => navigate(`/candidates/${candidate.id}`)}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-4 flex-1">
                        <div>
                          <Avatar className="w-14 h-14 border-2 border-white shadow-md">
                            <AvatarImage
                              src={`https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}`}
                            />
                            <AvatarFallback className="text-lg font-semibold">{candidate.name.charAt(0)}</AvatarFallback>
                          </Avatar>
                        </div>
                        <div className="flex-1">
                          <h4 className="font-semibold text-gray-900 text-lg group-hover:text-primary-600 transition-colors">
                            {candidate.name}
                          </h4>
                          <div className="flex items-center gap-2 mt-1">
                            <p className="text-sm text-gray-600 flex items-center gap-2">
                              <Calendar className="w-3 h-3" />
                              {candidate.location}
                            </p>
                            {candidate.jobCategory && (
                              <Badge 
                                className={`text-xs ${getCategoryColor(candidate.jobCategory).bg} ${getCategoryColor(candidate.jobCategory).text} border ${getCategoryColor(candidate.jobCategory).border}`}
                              >
                                {candidate.jobCategory}
                              </Badge>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-6">
                        <div className="text-right">
                          <p className={`text-3xl font-bold ${getMatchScoreColor(candidate.matchScore)}`}>
                            {(candidate.matchScore ?? 50).toFixed(1)}%
                          </p>
                          <p className="text-xs text-gray-500 mt-1">Match Score</p>
                        </div>
                        <Badge
                          variant={
                            candidate.status === 'Strong'
                              ? 'success'
                              : candidate.status === 'Partial'
                              ? 'warning'
                              : 'danger'
                          }
                          className="px-3 py-1"
                        >
                          {candidate.status}
                        </Badge>
                        <ArrowUpRight className="w-5 h-5 text-gray-400 group-hover:text-primary-600 group-hover:translate-x-1 group-hover:-translate-y-1 transition-all" />
                      </div>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {candidate.skills.slice(0, 5).map((skill) => (
                        <Badge 
                          key={skill} 
                          variant="outline" 
                          className="text-xs hover:bg-primary-50 hover:border-primary-300 transition-colors cursor-pointer"
                          onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/candidates?search=${skill}`)
                          }}
                        >
                          {skill}
                        </Badge>
                      ))}
                      {candidate.skills.length > 5 && (
                        <Badge 
                          variant="outline" 
                          className="text-xs bg-gray-50 cursor-pointer hover:bg-gray-100"
                          onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/candidates/${candidate.id}`)
                          }}
                        >
                          +{candidate.skills.length - 5} more
                        </Badge>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Upload Modal */}
      {showUploadModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div 
            className="bg-white rounded-2xl shadow-2xl max-w-lg w-full max-h-[80vh] overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between p-6 border-b border-gray-200">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-primary-100 rounded-xl flex items-center justify-center">
                  <Upload className="w-5 h-5 text-primary-600" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-gray-900">Upload Resumes</h2>
                  <p className="text-sm text-gray-500">PDF or DOCX files</p>
                </div>
              </div>
              <button
                onClick={() => {
                  setShowUploadModal(false)
                  setUploadResults([])
                }}
                className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>

            {/* Modal Content */}
            <div className="p-6">
              {isUploading ? (
                <div className="flex flex-col items-center justify-center py-12">
                  <Loader2 className="w-12 h-12 text-primary-600 animate-spin mb-4" />
                  <p className="text-lg font-medium text-gray-900">Processing resumes...</p>
                  <p className="text-sm text-gray-500">AI is analyzing the candidates</p>
                </div>
              ) : uploadResults.length > 0 ? (
                <div className="space-y-4">
                  <div className="flex items-center gap-2 mb-4">
                    <CheckCircle className="w-5 h-5 text-green-600" />
                    <span className="font-medium text-gray-900">
                      {uploadResults.filter(r => r.status === 'success').length} of {uploadResults.length} uploaded successfully
                    </span>
                  </div>
                  <div className="max-h-60 overflow-y-auto space-y-2">
                    {uploadResults.map((result, idx) => (
                      <div 
                        key={idx}
                        className={`p-3 rounded-lg border ${
                          result.status === 'success' 
                            ? 'bg-green-50 border-green-200' 
                            : 'bg-red-50 border-red-200'
                        }`}
                      >
                        <div className="flex items-start gap-3">
                          {result.status === 'success' ? (
                            <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
                          ) : (
                            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                          )}
                          <div className="flex-1 min-w-0">
                            <p className="font-medium text-gray-900 truncate">
                              {result.filename || 'Unknown file'}
                            </p>
                            {result.status === 'success' && result.candidate && (
                              <div className="mt-1 text-sm text-gray-600">
                                <span className="font-medium">{result.candidate.name}</span>
                                <span className="mx-2">•</span>
                                <span className={getMatchScoreColor(result.candidate.matchScore)}>
                                  {result.candidate.matchScore?.toFixed(1)}% match
                                </span>
                                <span className="mx-2">•</span>
                                <span>{result.candidate.jobCategory}</span>
                              </div>
                            )}
                            {result.status === 'error' && (
                              <p className="text-sm text-red-600">{result.message}</p>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-3 pt-4 border-t border-gray-200">
                    <Button
                      onClick={() => {
                        setUploadResults([])
                        openFileDialog()
                      }}
                      variant="outline"
                      className="flex-1"
                    >
                      <Upload className="w-4 h-4 mr-2" />
                      Upload More
                    </Button>
                    <Button
                      onClick={() => {
                        setShowUploadModal(false)
                        setUploadResults([])
                        navigate('/candidates')
                      }}
                      className="flex-1"
                    >
                      View Candidates
                    </Button>
                  </div>
                </div>
              ) : (
                <div
                  onDragEnter={handleDrag}
                  onDragLeave={handleDrag}
                  onDragOver={handleDrag}
                  onDrop={handleDrop}
                  onClick={openFileDialog}
                  className={`
                    border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all
                    ${dragActive 
                      ? 'border-primary-500 bg-primary-50' 
                      : 'border-gray-300 hover:border-primary-400 hover:bg-gray-50'
                    }
                  `}
                >
                  <div className="w-16 h-16 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-4">
                    <FileText className="w-8 h-8 text-primary-600" />
                  </div>
                  <p className="text-lg font-medium text-gray-900 mb-2">
                    {dragActive ? 'Drop files here' : 'Drag & drop resumes here'}
                  </p>
                  <p className="text-sm text-gray-500 mb-4">
                    or click to browse your files
                  </p>
                  <p className="text-xs text-gray-400">
                    Supports PDF and DOCX • Multiple files allowed
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
