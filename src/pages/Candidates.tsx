import { useState, useEffect, useMemo, useCallback } from 'react'
import { Search, SlidersHorizontal, RefreshCw, Loader2, Users, Briefcase, ChevronDown, ChevronRight, Calendar, ArrowUpDown, Mail, MessageCircle, Linkedin, Phone, Download, Star, FileText } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCandidates } from '@/hooks/useCandidates'
import { useEmailSync } from '@/hooks/useEmailSync'
import { Card } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/Avatar'
import { Progress } from '@/components/ui/Progress'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/Table'
import { getMatchScoreColor, getStatusBadgeColor, getCategoryColor } from '@/lib/utils'
import { generateQuickProfilePDF, downloadOriginalResume } from '@/lib/pdfGenerator'
import { candidateApi } from '@/services/api'
import { normalizeCategory } from '@/lib/categoryUtils'
import { toast } from '@/components/ui/Toast'
import type { Candidate } from '@/types'
import { useAuthStore } from '@/store/authStore'

// Quick contact helper - opens contact without navigating away
const openContact = (e: React.MouseEvent, type: 'email' | 'whatsapp' | 'linkedin' | 'phone', candidate: Pick<Candidate, 'email' | 'name' | 'phone' | 'linkedin'>) => {
  e.stopPropagation() // Prevent row click
  
  switch (type) {
    case 'email': {
      const safeEmail = encodeURIComponent(candidate.email || '')
      const safeName = encodeURIComponent(candidate.name || '')
      window.location.href = `mailto:${safeEmail}?subject=Regarding%20Your%20Application&body=Hi%20${safeName}%2C%0A%0A`
      break
    }
    case 'whatsapp': {
      const cleanPhone = candidate.phone?.replace(/[\s\-\(\)]/g, '').replace(/^\+/, '') || ''
      if (cleanPhone) {
        window.open(`https://wa.me/${cleanPhone}?text=Hi ${encodeURIComponent(candidate.name || '')}, I'm reaching out regarding your job application.`, '_blank')
      }
      break
    }
    case 'linkedin':
      if (candidate.linkedin) {
        window.open(candidate.linkedin, '_blank')
      }
      break
    case 'phone':
      window.location.href = `tel:${encodeURIComponent(candidate.phone || '')}`
      break
  }
}

// Validate if a string is a real phone number (not a year or random short number)
const isValidPhone = (phone: string | undefined | null): boolean => {
  if (!phone) return false
  // Remove all non-digit characters for validation
  const digitsOnly = phone.replace(/\D/g, '')
  // Phone numbers should have at least 7 digits and not be just a year (4 digits like 2024, 2025, 2026)
  if (digitsOnly.length < 7) return false
  // Check if it's just a year (4 digits starting with 19 or 20)
  if (digitsOnly.length === 4 && /^(19|20)\d{2}$/.test(digitsOnly)) return false
  return true
}

// ── Main category grouping ──
// Groups canonical sub-categories into broader main categories for the UI
const MAIN_CATEGORY_MAP: Record<string, string> = {
  'Software Engineering': 'Technology',
  'Data & Analytics': 'Technology',
  'IT & Systems': 'Technology',
  'QA & Testing': 'Technology',
  'Engineering': 'Engineering & Technical',
  'Project Management': 'Engineering & Technical',
  'Finance & Accounting': 'Business & Finance',
  'Business Analyst': 'Business & Finance',
  'Consulting': 'Business & Finance',
  'Sales': 'Sales & Marketing',
  'Marketing': 'Sales & Marketing',
  'HR & Admin': 'People & Administration',
  'Customer Service': 'People & Administration',
  'Education': 'People & Administration',
  'Operations': 'Operations & Logistics',
  'Healthcare': 'Specialist Services',
  'Legal': 'Specialist Services',
  'Insurance & Safety': 'Specialist Services',
  'Retail & Hospitality': 'Specialist Services',
  'Design & Creative': 'Creative & Media',
  'General': 'Other',
}

type SortOption = 'score-desc' | 'score-asc' | 'date-newest' | 'date-oldest' | 'name-asc' | 'name-desc'

export default function Candidates() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { candidates, loading, error, refetch, totalCount } = useCandidates({ autoFetch: true })
  // Auto-refresh when email sync detects new candidates
  useEmailSync(refetch, 30000)
  const { user } = useAuthStore()
  const isAdmin = user?.role === 'admin'
  const [searchQuery, setSearchQuery] = useState(searchParams.get('search') || '')
  const [showFilters, setShowFilters] = useState(false)
  const [viewMode, setViewMode] = useState<'grouped' | 'list'>('grouped')
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set())
  const [selectedCategory, setSelectedCategory] = useState<string>('all')
  const [sortBy, setSortBy] = useState<SortOption>('date-newest')  // Default to newest first within categories
  const [dateRange, setDateRange] = useState({ start: '', end: '' })
  const [displayLimit, setDisplayLimit] = useState(50)  // Show 50 at a time for performance
  const [shortlistingIds, setShortlistingIds] = useState<Set<string>>(new Set())
  const [shortlistedIds, setShortlistedIds] = useState<Set<string>>(new Set())
  const [emailSentIds, setEmailSentIds] = useState<Set<string>>(new Set())
  const [isReprocessing, setIsReprocessing] = useState(false)

  const showToast = useCallback((message: string, type: 'success' | 'error' | 'info' = 'success') => {
    toast[type](message)
  }, [])

  const handleReprocessWithGemini = useCallback(async () => {
    if (isReprocessing) return
    setIsReprocessing(true)
    showToast('Starting AI reprocessing... This may take a few minutes.', 'info')
    try {
      const res = await candidateApi.reprocessWithGemini()
      const data = (res as any)?.data || res
      const improved = data?.improved || 0
      const processed = data?.processed || 0
      showToast(`AI reprocessing complete: ${processed} analyzed, ${improved} scores improved`, 'success')
      setTimeout(() => refetch(), 2000)
    } catch (err: any) {
      showToast(`Reprocess failed: ${err?.message || 'Unknown error'}`, 'error')
    } finally {
      setIsReprocessing(false)
    }
  }, [isReprocessing, refetch, showToast])

  const [filters, setFilters] = useState({
    minScore: 0,
    status: 'all',
    minExperience: 0,
  })

  useEffect(() => {
    const searchFromUrl = searchParams.get('search')
    if (searchFromUrl) {
      setSearchQuery(searchFromUrl)
    }
    const categoryFromUrl = searchParams.get('category')
    if (categoryFromUrl) {
      setSelectedCategory(categoryFromUrl)
    }
    const statusFromUrl = searchParams.get('status')
    if (statusFromUrl) {
      setFilters(prev => ({ ...prev, status: statusFromUrl }))
    }
    setDisplayLimit(50) // Reset pagination on filter change
  }, [searchParams])

  // Sort function - memoized for performance
  const sortCandidates = useCallback((items: typeof candidates, sort: SortOption) => {
    const sorted = [...items]
    // Safe date parser — handles empty/invalid dates by treating them as epoch 0
    const safeDate = (d: string) => {
      if (!d) return 0
      const t = new Date(d).getTime()
      return isNaN(t) ? 0 : t
    }
    switch (sort) {
      case 'score-desc':
        return sorted.sort((a, b) => b.matchScore - a.matchScore)
      case 'score-asc':
        return sorted.sort((a, b) => a.matchScore - b.matchScore)
      case 'date-newest':
        return sorted.sort((a, b) => safeDate(b.appliedDate) - safeDate(a.appliedDate))
      case 'date-oldest':
        return sorted.sort((a, b) => safeDate(a.appliedDate) - safeDate(b.appliedDate))
      case 'name-asc':
        return sorted.sort((a, b) => a.name.localeCompare(b.name))
      case 'name-desc':
        return sorted.sort((a, b) => b.name.localeCompare(a.name))
      default:
        return sorted
    }
  }, [])

  // Filter candidates - optimized with single pass
  const filteredCandidates = useMemo(() => {
    const searchLower = searchQuery.toLowerCase()
    
    return candidates.filter((candidate) => {
      // Date range filter
      if (dateRange.start || dateRange.end) {
        const appliedDate = new Date(candidate.appliedDate).getTime()
        if (Number.isNaN(appliedDate)) return false  // Exclude candidates with invalid dates from date-filtered results
        if (dateRange.start) {
          const startDate = new Date(dateRange.start).getTime()
          if (appliedDate < startDate) return false
        }
        if (dateRange.end) {
          const endDate = new Date(dateRange.end).setHours(23, 59, 59, 999)
          if (appliedDate > endDate) return false
        }
      }
      
      // Quick filters first (faster checks)
      if (candidate.matchScore < filters.minScore) return false
      if (filters.status !== 'all' && candidate.status !== filters.status) return false
      if (candidate.experience < filters.minExperience) return false
      if (selectedCategory !== 'all' && normalizeCategory(candidate.jobCategory || 'General') !== selectedCategory) return false
      
      // Search filter (slower, do last)
      if (searchLower) {
        const matchesName = candidate.name.toLowerCase().includes(searchLower)
        const matchesCategory = candidate.jobCategory.toLowerCase().includes(searchLower)
        const matchesSkill = candidate.skills.some(skill => skill.toLowerCase().includes(searchLower))
        const matchesEmail = candidate.email?.toLowerCase().includes(searchLower)
        if (!matchesName && !matchesCategory && !matchesSkill && !matchesEmail) return false
      }
      
      return true
    })
  }, [candidates, searchQuery, filters, selectedCategory, dateRange])

  // Sorted candidates
  const sortedCandidates = useMemo(() => {
    return sortCandidates(filteredCandidates, sortBy)
  }, [filteredCandidates, sortBy, sortCandidates])

  // Paginated candidates for list view
  const paginatedCandidates = useMemo(() => {
    return sortedCandidates.slice(0, displayLimit)
  }, [sortedCandidates, displayLimit])

  // Group candidates by normalized category
  const groupedCandidates = useMemo(() => {
    const groups: Record<string, typeof candidates> = {}
    sortedCandidates.forEach((candidate) => {
      const category = normalizeCategory(candidate.jobCategory || 'General')
      if (!groups[category]) {
        groups[category] = []
      }
      groups[category].push(candidate)
    })
    return groups
  }, [sortedCandidates])

  // Get unique categories for filter dropdown (using normalized names)
  const categories = useMemo(() => {
    const cats = new Set(candidates.map(c => normalizeCategory(c.jobCategory || 'General')))
    return ['all', ...Array.from(cats).sort()]
  }, [candidates])

  // Category stats
  const categoryStats = useMemo(() => {
    const stats: Record<string, { total: number; avgScore: number; topScore: number }> = {}
    Object.entries(groupedCandidates).forEach(([category, catCandidates]) => {
      const scores = catCandidates.map(c => c.matchScore)
      stats[category] = {
        total: catCandidates.length,
        avgScore: Math.round(scores.reduce((a, b) => a + b, 0) / scores.length),
        topScore: Math.max(...scores)
      }
    })
    return stats
  }, [groupedCandidates])

  const toggleCategory = useCallback((category: string) => {
    setExpandedCategories(prev => {
      const next = new Set(prev)
      if (next.has(category)) {
        next.delete(category)
      } else {
        next.add(category)
      }
      return next
    })
  }, [])

  // Expand all categories by default
  useEffect(() => {
    if (Object.keys(groupedCandidates).length > 0 && expandedCategories.size === 0) {
      setExpandedCategories(new Set(Object.keys(groupedCandidates)))
    }
  }, [groupedCandidates, expandedCategories.size])

  // Clear date range
  const clearDateRange = useCallback(() => {
    setDateRange({ start: '', end: '' })
  }, [])

  // Format date for display
  const formatDate = (dateString: string) => {
    if (!dateString) return '-'
    return new Date(dateString).toLocaleDateString('en-US', { 
      month: 'short', 
      day: 'numeric',
      year: 'numeric'
    })
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-slate-800 rounded-xl flex items-center justify-center">
            <Users className="w-5 h-5 text-sky-300" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">
              {filters.status !== 'all' ? `${filters.status} Candidates` : 'Candidates'}
            </h1>
            <p className="text-sm text-gray-500">
              {loading ? 'Loading...' : (() => {
                const hasActiveFilters = searchQuery || selectedCategory !== 'all' || filters.minScore > 0 || filters.status !== 'all' || filters.minExperience > 0 || dateRange.start || dateRange.end
                const displayCount = hasActiveFilters ? filteredCandidates.length : (totalCount || filteredCandidates.length)
                return `${displayCount} candidates in ${Object.keys(groupedCandidates).length} categories`
              })()}
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button 
            variant={viewMode === 'grouped' ? 'default' : 'outline'} 
            size="sm"
            onClick={() => setViewMode('grouped')}
          >
            <Briefcase className="w-4 h-4 mr-1" />
            By Category
          </Button>
          <Button 
            variant={viewMode === 'list' ? 'default' : 'outline'} 
            size="sm"
            onClick={() => setViewMode('list')}
          >
            <Users className="w-4 h-4 mr-1" />
            List View
          </Button>
          <Button variant="outline" onClick={refetch} disabled={loading}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            <span className="ml-2">Refresh</span>
          </Button>
          {isAdmin && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleReprocessWithGemini}
            disabled={isReprocessing}
            className="border-indigo-200 text-indigo-600 hover:bg-indigo-50"
            title="Reprocess poorly-scored candidates with Gemini AI"
          >
            {isReprocessing ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <Star className="w-4 h-4 mr-1" />}
            {isReprocessing ? 'Reprocessing...' : 'AI Reprocess'}
          </Button>
          )}
        </div>
      </div>

      {/* Filters Bar */}
      <Card className="p-4">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex-1 min-w-[200px] relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
            <Input
              type="search"
              placeholder="Search by name, skills, or category..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>
          
          {/* Sort Dropdown */}
          <div className="flex items-center gap-2">
            <ArrowUpDown className="w-4 h-4 text-gray-500" />
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortOption)}
              className="h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-sky-500 focus:border-transparent bg-white text-sm"
            >
              <option value="score-desc">Highest Score</option>
              <option value="score-asc">Lowest Score</option>
              <option value="date-newest">Newest First</option>
              <option value="date-oldest">Oldest First</option>
              <option value="name-asc">Name A-Z</option>
              <option value="name-desc">Name Z-A</option>
            </select>
          </div>

          <select
            value={selectedCategory}
            onChange={(e) => setSelectedCategory(e.target.value)}
            className="h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-sky-500 focus:border-transparent bg-white"
          >
            {categories.map((cat) => (
              <option key={cat} value={cat}>
                {cat === 'all' ? 'All Categories' : cat}
              </option>
            ))}
          </select>
          <Button
            variant={showFilters ? 'default' : 'outline'}
            onClick={() => setShowFilters(!showFilters)}
          >
            <SlidersHorizontal className="w-4 h-4 mr-2" />
            Filters
          </Button>
        </div>

        {showFilters && (
          <div className="mt-4 pt-4 border-t border-gray-200 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Date Range Filter */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                <Calendar className="w-4 h-4 inline mr-1" />
                Date Applied (From)
              </label>
              <input
                type="date"
                value={dateRange.start}
                onChange={(e) => setDateRange(prev => ({ ...prev, start: e.target.value }))}
                className="w-full h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-sky-500 focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                <Calendar className="w-4 h-4 inline mr-1" />
                Date Applied (To)
              </label>
              <div className="flex gap-2">
                <input
                  type="date"
                  value={dateRange.end}
                  onChange={(e) => setDateRange(prev => ({ ...prev, end: e.target.value }))}
                  className="flex-1 h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-sky-500 focus:border-transparent"
                />
                {(dateRange.start || dateRange.end) && (
                  <Button variant="ghost" size="sm" onClick={clearDateRange} className="px-2">
                    ✕
                  </Button>
                )}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Min Match Score: {filters.minScore}%
              </label>
              <input
                type="range"
                min="0"
                max="100"
                value={filters.minScore}
                onChange={(e) =>
                  setFilters({ ...filters, minScore: Number(e.target.value) })
                }
                className="w-full"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Status
              </label>
              <select
                value={filters.status}
                onChange={(e) => setFilters({ ...filters, status: e.target.value })}
                className="w-full h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-sky-500 focus:border-transparent"
              >
                <option value="all">All Statuses</option>
                <option value="Strong">Strong Match (70%+)</option>
                <option value="Partial">Partial Match (40-69%)</option>
                <option value="Reject">Below Threshold</option>
                <option value="Shortlisted">Shortlisted</option>
                <option value="Interviewing">Interviewing</option>
                <option value="Offered">Offered</option>
                <option value="Hired">Hired</option>
                <option value="Rejected">Rejected</option>
              </select>
            </div>
          </div>
        )}
        
        {/* Active Filters Summary */}
        {(dateRange.start || dateRange.end || filters.minScore > 0 || filters.status !== 'all') && (
          <div className="mt-3 pt-3 border-t border-gray-100 flex items-center gap-2 flex-wrap">
            <span className="text-sm text-gray-500">Active filters:</span>
            {dateRange.start && (
              <Badge variant="secondary" className="text-xs">
                From: {formatDate(dateRange.start)}
              </Badge>
            )}
            {dateRange.end && (
              <Badge variant="secondary" className="text-xs">
                To: {formatDate(dateRange.end)}
              </Badge>
            )}
            {filters.minScore > 0 && (
              <Badge variant="secondary" className="text-xs">
                Min Score: {filters.minScore}%
              </Badge>
            )}
            {filters.status !== 'all' && (
              <Badge variant="secondary" className="text-xs">
                Status: {filters.status}
              </Badge>
            )}
          </div>
        )}
      </Card>

      {/* Category Overview Cards - Hierarchical main → sub grouping */}
      {viewMode === 'grouped' && Object.keys(categoryStats).length > 0 && (
        <div className="space-y-3">
          {(() => {
            // Group sub-categories under main categories
            const mainGroups: Record<string, { sub: string; stats: typeof categoryStats[string] }[]> = {}
            Object.entries(categoryStats).forEach(([sub, stats]) => {
              const main = MAIN_CATEGORY_MAP[sub] || 'Other'
              if (!mainGroups[main]) mainGroups[main] = []
              mainGroups[main].push({ sub, stats })
            })
            // Sort main groups by total count descending
            const mainOrder = Object.entries(mainGroups).sort(
              ([, a], [, b]) => b.reduce((s, x) => s + x.stats.total, 0) - a.reduce((s, x) => s + x.stats.total, 0)
            )
            return mainOrder.map(([mainCat, subs]) => {
              const mainTotal = subs.reduce((s, x) => s + x.stats.total, 0)
              const mainAvg = Math.round(subs.reduce((s, x) => s + x.stats.avgScore * x.stats.total, 0) / mainTotal)
              return (
                <div key={mainCat}>
                  <div className="flex items-center gap-2 mb-1.5">
                    <h3 className="text-sm font-bold text-gray-800">{mainCat}</h3>
                    <Badge variant="secondary" className="text-xs">{mainTotal}</Badge>
                    <span className="text-xs text-gray-400 ml-1">avg {mainAvg}%</span>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 ml-6">
                    {subs.map(({ sub, stats }) => {
                      const colors = getCategoryColor(sub)
                      return (
                        <Card
                          key={sub}
                          className={`p-2.5 cursor-pointer transition-all hover:shadow-md ${colors.bg} border ${colors.border} ${selectedCategory === sub ? 'ring-2 ring-sky-400 shadow-md' : ''}`}
                          onClick={() => setSelectedCategory(selectedCategory === sub ? 'all' : sub)}
                        >
                          <div className="flex items-center justify-between mb-1.5">
                            <span className={`text-xs font-semibold ${colors.text} truncate`}>{sub}</span>
                            <Badge variant="secondary" className="text-[10px] px-1.5">{stats.total}</Badge>
                          </div>
                          <div className="flex items-center gap-2">
                            <div className="flex-1">
                              <Progress
                                value={stats.avgScore}
                                className="h-1"
                                indicatorClassName={
                                  stats.avgScore >= 70 ? 'bg-emerald-500' :
                                  stats.avgScore >= 40 ? 'bg-amber-500' : 'bg-red-500'
                                }
                              />
                            </div>
                            <span className="text-[10px] font-medium text-gray-500">{stats.avgScore}%</span>
                          </div>
                        </Card>
                      )
                    })}
                  </div>
                </div>
              )
            })
          })()}
        </div>
      )}

      {/* Grouped View */}
      {viewMode === 'grouped' && (
        <div className="space-y-4">
          {Object.entries(groupedCandidates)
            .sort(([, a], [, b]) => b.length - a.length)
            .map(([category, categoryCandidates]) => {
              const colors = getCategoryColor(category)
              const isExpanded = expandedCategories.has(category)
              const stats = categoryStats[category]
              
              return (
                <Card key={category} className={`overflow-hidden border-2 ${colors.border}`}>
                  {/* Category Header */}
                  <div 
                    className={`p-4 ${colors.bg} cursor-pointer flex items-center justify-between`}
                    onClick={() => toggleCategory(category)}
                  >
                    <div className="flex items-center gap-3">
                      {isExpanded ? 
                        <ChevronDown className={`w-5 h-5 ${colors.text}`} /> : 
                        <ChevronRight className={`w-5 h-5 ${colors.text}`} />
                      }
                      <Briefcase className={`w-5 h-5 ${colors.text}`} />
                      <h3 className={`text-lg font-semibold ${colors.text}`}>{category}</h3>
                      <Badge className={`${colors.bg} ${colors.text} border ${colors.border}`}>
                        {categoryCandidates.length} candidates
                      </Badge>
                    </div>
                    <div className="flex items-center gap-4 text-sm">
                      <span className="text-gray-600">
                        Avg Score: <strong className={getMatchScoreColor(stats?.avgScore || 0)}>{stats?.avgScore || 0}%</strong>
                      </span>
                      <span className="text-gray-600">
                        Top: <strong className="text-green-600">{stats?.topScore || 0}%</strong>
                      </span>
                    </div>
                  </div>

                  {/* Candidates in Category - No animation for better performance */}
                  {isExpanded && (
                    <>
                    <div className="overflow-x-auto">
                      <Table className="min-w-[900px] w-full">
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-[50px]">SI No.</TableHead>
                            <TableHead className="w-[220px]">Candidate</TableHead>
                            <TableHead className="w-[90px]">Score</TableHead>
                            <TableHead className="w-[140px]">Skills</TableHead>
                            <TableHead className="w-[70px]">Exp</TableHead>
                            <TableHead className="w-[90px]">Applied</TableHead>
                            <TableHead className="w-[100px]">Contact</TableHead>
                            <TableHead className="w-[80px]">Status</TableHead>
                            <TableHead className="w-[80px]"></TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                        {categoryCandidates.slice(0, displayLimit).map((candidate, index) => {
                          const statusColors = getStatusBadgeColor(candidate.status)
                          return (
                            <TableRow
                              key={candidate.id}
                              className="cursor-pointer hover:bg-gray-50"
                              onClick={() => navigate(`/candidates/${candidate.id}`)}
                            >
                              <TableCell>
                                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                                  index === 0 ? 'bg-yellow-100 text-yellow-700' :
                                  index === 1 ? 'bg-gray-100 text-gray-700' :
                                  index === 2 ? 'bg-orange-100 text-orange-700' :
                                  'bg-gray-50 text-gray-500'
                                }`}>
                                  {index + 1}
                                </div>
                              </TableCell>
                              <TableCell className="max-w-[220px]">
                                <div className="flex items-center gap-2 overflow-hidden">
                                  <Avatar className="w-8 h-8 flex-shrink-0">
                                    <AvatarImage
                                      src={`https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(candidate.name || 'candidate')}`}
                                    />
                                    <AvatarFallback>{candidate.name.charAt(0)}</AvatarFallback>
                                  </Avatar>
                                  <div className="min-w-0 flex-1 overflow-hidden">
                                    <p className="font-medium text-gray-900 truncate text-sm" title={candidate.name}>{candidate.name}</p>
                                    <p className="text-xs text-gray-500 truncate" title={candidate.email}>{candidate.email}</p>
                                    <div className="flex items-center gap-1 text-xs text-gray-400 mt-0.5 overflow-hidden">
                                      {isValidPhone(candidate.phone) && (
                                        <span className="truncate flex-shrink-0" title={candidate.phone}>{candidate.phone!.slice(0, 12)}</span>
                                      )}
                                      {candidate.location && (
                                        <span className="truncate" title={candidate.location}>{candidate.location.length > 10 ? candidate.location.slice(0, 10) + '..' : candidate.location}</span>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              </TableCell>
                              <TableCell className="w-[90px]">
                                <div className="space-y-1">
                                  <p className={`text-sm font-bold ${getMatchScoreColor(candidate.matchScore)}`}>
                                    {(candidate.matchScore ?? 0).toFixed(0)}%
                                  </p>
                                  <Progress
                                    value={candidate.matchScore}
                                    className="w-14 h-1"
                                    indicatorClassName={
                                      candidate.matchScore >= 70 ? 'bg-emerald-500' :
                                      candidate.matchScore >= 40 ? 'bg-amber-500' : 'bg-red-500'
                                    }
                                  />
                                </div>
                              </TableCell>
                              <TableCell className="max-w-[140px]">
                                <div className="flex flex-wrap gap-0.5 overflow-hidden">
                                  {candidate.skills.length === 0 || (candidate.skills.length === 1 && candidate.skills[0].toLowerCase() === 'r') ? (
                                    <span className="text-xs text-gray-400 italic">Pending AI</span>
                                  ) : (
                                    <>
                                      {candidate.skills.slice(0, 2).map((skill) => (
                                        <Badge key={skill} variant="outline" className="text-xs px-1 py-0 whitespace-nowrap" title={skill}>
                                          {skill.length > 7 ? skill.slice(0, 7) + '..' : skill}
                                        </Badge>
                                      ))}
                                      {candidate.skills.length > 2 && (
                                        <Badge variant="secondary" className="text-xs px-1 py-0 whitespace-nowrap">
                                          +{candidate.skills.length - 2}
                                        </Badge>
                                      )}
                                    </>
                                  )}
                                </div>
                              </TableCell>
                              <TableCell className="w-[70px]">
                                <p className="text-xs font-medium whitespace-nowrap">
                                  {candidate.experience > 0 ? `${candidate.experience} yrs` : '-'}
                                </p>
                              </TableCell>
                              <TableCell className="w-[90px]">
                                <p className="text-xs text-gray-600 whitespace-nowrap">
                                  {formatDate(candidate.appliedDate)}
                                </p>
                              </TableCell>
                              <TableCell className="w-[100px]">
                                {/* Quick Contact Icons */}
                                <div className="flex items-center gap-0 flex-nowrap">
                                  {candidate.email && (
                                    <button
                                      onClick={(e) => openContact(e, 'email', candidate)}
                                      className="p-1 rounded-full hover:bg-sky-100 text-sky-600 transition-colors"
                                      title="Send Email"
                                    >
                                      <Mail className="w-3.5 h-3.5" />
                                    </button>
                                  )}
                                  {isValidPhone(candidate.phone) && (
                                    <button
                                      onClick={(e) => openContact(e, 'whatsapp', candidate)}
                                      className="p-1 rounded-full hover:bg-green-100 text-green-600 transition-colors"
                                      title="WhatsApp"
                                    >
                                      <MessageCircle className="w-3.5 h-3.5" />
                                    </button>
                                  )}
                                  {candidate.linkedin && (
                                    <button
                                      onClick={(e) => openContact(e, 'linkedin', candidate)}
                                      className="p-1 rounded-full hover:bg-[#0077B5]/10 text-[#0077B5] transition-colors"
                                      title="LinkedIn"
                                    >
                                      <Linkedin className="w-3.5 h-3.5" />
                                    </button>
                                  )}
                                  {isValidPhone(candidate.phone) && (
                                    <button
                                      onClick={(e) => openContact(e, 'phone', candidate)}
                                      className="p-1 rounded-full hover:bg-sky-100 text-sky-600 transition-colors"
                                      title="Call"
                                    >
                                      <Phone className="w-3.5 h-3.5" />
                                    </button>
                                  )}
                                </div>
                              </TableCell>
                              <TableCell className="w-[80px]">
                                <Badge className={`${statusColors.bg} ${statusColors.text} text-xs px-1.5 py-0.5 whitespace-nowrap`}>
                                  {candidate.status}
                                </Badge>
                              </TableCell>
                              <TableCell className="w-[80px]">
                                <div className="flex items-center gap-1">
                                  <button
                                    onClick={async (e) => {
                                      e.stopPropagation()
                                      if (!isAdmin) { showToast('Admin required to shortlist candidates', 'error'); return; }
                                      if (shortlistingIds.has(candidate.id)) return
                                      const alreadyShortlisted = candidate.isShortlisted || candidate.status === 'Shortlisted' || shortlistedIds.has(candidate.id)
                                      try {
                                        setShortlistingIds(prev => new Set(prev).add(candidate.id))
                                        if (alreadyShortlisted) {
                                          // Unshortlist
                                          const res = await candidateApi.updateStatus(candidate.id, 'Reviewed')
                                          if (res.error) throw new Error(res.error.message || 'Failed to update status')
                                          setShortlistedIds(prev => {
                                            const next = new Set(prev)
                                            next.delete(candidate.id)
                                            return next
                                          })
                                          setEmailSentIds(prev => {
                                            const next = new Set(prev)
                                            next.delete(candidate.id)
                                            return next
                                          })
                                          showToast(`${candidate.name} removed from shortlist`, 'info')
                                          // Refresh candidates to update status column
                                          refetch()
                                        } else {
                                          // Shortlist
                                          const res = await candidateApi.updateStatus(candidate.id, 'Shortlisted')
                                          if (res.error) throw new Error(res.error.message || 'Failed to shortlist')
                                          setShortlistedIds(prev => new Set(prev).add(candidate.id))
                                          const emailStatus = (res as any)?.data?.email_sent?.status || (res as any)?.email_sent?.status
                                          if (emailStatus === 'queued' || emailStatus === 'success') {
                                            setEmailSentIds(prev => new Set(prev).add(candidate.id))
                                            showToast(`✅ ${candidate.name} shortlisted — email sent to ${candidate.email || 'candidate'}`, 'success')
                                          } else if (emailStatus === 'error') {
                                            showToast(`⭐ ${candidate.name} shortlisted — email failed`, 'error')
                                          } else {
                                            showToast(`⭐ ${candidate.name} shortlisted`, 'info')
                                          }
                                          refetch()
                                        }
                                      } catch (err) {
                                        console.error('Shortlist failed:', err)
                                        showToast(`Failed to shortlist ${candidate.name}`, 'error')
                                      } finally {
                                        setShortlistingIds(prev => {
                                          const next = new Set(prev)
                                          next.delete(candidate.id)
                                          return next
                                        })
                                      }
                                    }}
                                    disabled={!isAdmin || shortlistingIds.has(candidate.id)}
                                    className={`p-1 rounded-full transition-colors ${
                                      candidate.status === 'Shortlisted' || shortlistedIds.has(candidate.id)
                                        ? 'text-yellow-500 bg-yellow-50'
                                        : shortlistingIds.has(candidate.id)
                                          ? 'text-gray-300 opacity-50'
                                          : 'text-gray-400 hover:text-yellow-500 hover:bg-yellow-50'
                                    }`}
                                    title={
                                      shortlistingIds.has(candidate.id) ? 'Shortlisting & sending email...' :
                                      candidate.status === 'Shortlisted' || shortlistedIds.has(candidate.id)
                                        ? (emailSentIds.has(candidate.id) ? 'Shortlisted — Email sent' : 'Shortlisted')
                                        : 'Shortlist & send email'
                                    }
                                  >
                                    <Star className="w-3.5 h-3.5" fill={
                                      candidate.status === 'Shortlisted' || shortlistedIds.has(candidate.id) ? 'currentColor' : 'none'
                                    } />
                                  </button>
                                  {emailSentIds.has(candidate.id) && (
                                    <span className="text-green-500" title="Shortlist email sent">
                                      <Mail className="w-3 h-3" />
                                    </span>
                                  )}
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      generateQuickProfilePDF(candidate).catch((err: Error) =>
                                        toast.error('PDF Failed', err.message || 'Could not generate PDF report')
                                      )
                                    }}
                                    className="p-1 rounded-full hover:bg-sky-100 text-sky-600 transition-colors"
                                    title="Download PDF Report"
                                  >
                                    <Download className="w-3.5 h-3.5" />
                                  </button>
                                  {candidate.hasResume && (
                                    <button
                                      onClick={(e) => { e.stopPropagation(); downloadOriginalResume(candidate).catch((err: Error) => toast.error('Download Failed', err.message || 'No resume available')) }}
                                      className="p-1 rounded-full hover:bg-emerald-100 text-emerald-600 transition-colors"
                                      title="Download Original Resume"
                                    >
                                      <FileText className="w-3.5 h-3.5" />
                                    </button>
                                  )}
                                </div>
                              </TableCell>
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                    </div>
                    {categoryCandidates.length > displayLimit && (
                      <div className="p-3 text-center border-t border-gray-100">
                        <Button variant="outline" size="sm" onClick={() => setDisplayLimit(prev => prev + 50)}>
                          Show more ({categoryCandidates.length - displayLimit} remaining)
                        </Button>
                      </div>
                    )}
                    </>
                  )}
                </Card>
              )
            })}
        </div>
      )}

      {/* List View - No per-row animations */}
      {viewMode === 'list' && (
        <Card>
          <div className="overflow-x-auto">
          <Table className="min-w-[1000px] w-full">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[60px]">SI No.</TableHead>
                <TableHead className="w-[250px]">Candidate</TableHead>
                <TableHead className="w-[130px]">Category</TableHead>
                <TableHead className="w-[100px]">Match Score</TableHead>
                <TableHead className="w-[180px]">Skills</TableHead>
                <TableHead className="w-[80px]">Experience</TableHead>
                <TableHead className="w-[90px]">Applied</TableHead>
                <TableHead className="w-[80px]">Status</TableHead>
                <TableHead className="w-[80px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {paginatedCandidates.map((candidate, index) => {
                const statusColors = getStatusBadgeColor(candidate.status)
                const catColors = getCategoryColor(candidate.jobCategory)
                return (
                  <TableRow
                    key={candidate.id}
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => navigate(`/candidates/${candidate.id}`)}
                  >
                    <TableCell>
                      <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                        index === 0 ? 'bg-yellow-100 text-yellow-700' :
                        index === 1 ? 'bg-gray-100 text-gray-700' :
                        index === 2 ? 'bg-orange-100 text-orange-700' :
                        'bg-gray-50 text-gray-500'
                      }`}>
                        {index + 1}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[250px]">
                      <div className="flex items-center gap-2 overflow-hidden">
                        <Avatar className="w-9 h-9 flex-shrink-0">
                          <AvatarImage
                            src={`https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(candidate.name || 'candidate')}`}
                          />
                          <AvatarFallback>{candidate.name.charAt(0)}</AvatarFallback>
                        </Avatar>
                        <div className="min-w-0 flex-1 overflow-hidden">
                          <p className="font-medium text-gray-900 truncate text-sm" title={candidate.name}>{candidate.name}</p>
                          <p className="text-xs text-gray-500 truncate" title={candidate.email}>{candidate.email}</p>
                          <div className="flex items-center gap-2 text-xs text-gray-400 mt-0.5 overflow-hidden">
                            {isValidPhone(candidate.phone) && (
                              <span className="truncate flex-shrink-0" title={candidate.phone}>
                                {candidate.phone!.slice(0, 12)}
                              </span>
                            )}
                            {candidate.location && (
                              <span className="truncate" title={candidate.location}>
                                {candidate.location.length > 12 ? candidate.location.slice(0, 12) + '..' : candidate.location}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[130px]">
                      <Badge className={`${catColors.bg} ${catColors.text} border ${catColors.border} text-xs whitespace-nowrap truncate max-w-full`} title={candidate.jobCategory}>
                        {candidate.jobCategory.length > 14 ? candidate.jobCategory.slice(0, 14) + '..' : candidate.jobCategory}
                      </Badge>
                    </TableCell>
                    <TableCell className="w-[100px]">
                      <div className="space-y-1">
                        <p className={`text-base font-bold ${getMatchScoreColor(candidate.matchScore)}`}>
                          {(candidate.matchScore ?? 0).toFixed(0)}%
                        </p>
                        <Progress
                          value={candidate.matchScore ?? 0}
                          className="w-16 h-1.5"
                          indicatorClassName={
                            (candidate.matchScore ?? 0) >= 70 ? 'bg-emerald-500' :
                            (candidate.matchScore ?? 0) >= 40 ? 'bg-amber-500' : 'bg-red-500'
                          }
                        />
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[180px]">
                      <div className="flex flex-wrap gap-0.5 overflow-hidden">
                        {candidate.skills.length === 0 || (candidate.skills.length === 1 && candidate.skills[0].toLowerCase() === 'r') ? (
                          <span className="text-xs text-gray-400 italic">Pending AI analysis</span>
                        ) : (
                          <>
                            {candidate.skills.slice(0, 3).map((skill) => (
                              <Badge key={skill} variant="outline" className="text-xs px-1.5 py-0 whitespace-nowrap" title={skill}>
                                {skill.length > 10 ? skill.slice(0, 10) + '..' : skill}
                              </Badge>
                            ))}
                          </>
                        )}
                        {candidate.skills.length > 3 && (
                          <Badge variant="secondary" className="text-xs px-1.5 py-0 whitespace-nowrap">
                            +{candidate.skills.length - 3}
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="w-[80px]">
                      <p className="text-sm text-gray-900 whitespace-nowrap">
                        {candidate.experience > 0 ? `${candidate.experience} yrs` : '-'}
                      </p>
                    </TableCell>
                    <TableCell className="w-[90px]">
                      <p className="text-xs text-gray-600 whitespace-nowrap">
                        {formatDate(candidate.appliedDate)}
                      </p>
                    </TableCell>
                    <TableCell className="w-[80px]">
                      <Badge className={`${statusColors.bg} ${statusColors.text} text-xs whitespace-nowrap`}>
                        {candidate.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="w-[80px]">
                      <div className="flex items-center gap-1">
                        <button
                          onClick={async (e) => {
                            e.stopPropagation()
                            if (!isAdmin) { showToast('Admin required to shortlist candidates', 'error'); return; }
                            if (shortlistingIds.has(candidate.id)) return
                            const alreadyShortlisted = candidate.isShortlisted || candidate.status === 'Shortlisted' || shortlistedIds.has(candidate.id)
                            try {
                              setShortlistingIds(prev => new Set(prev).add(candidate.id))
                              if (alreadyShortlisted) {
                                // Unshortlist
                                const res = await candidateApi.updateStatus(candidate.id, 'Reviewed')
                                if (res.error) throw new Error(res.error.message || 'Failed to update status')
                                setShortlistedIds(prev => {
                                  const next = new Set(prev)
                                  next.delete(candidate.id)
                                  return next
                                })
                                setEmailSentIds(prev => {
                                  const next = new Set(prev)
                                  next.delete(candidate.id)
                                  return next
                                })
                                showToast(`${candidate.name} removed from shortlist`, 'info')
                                refetch()
                              } else {
                                // Shortlist
                                const res = await candidateApi.updateStatus(candidate.id, 'Shortlisted')
                                if (res.error) throw new Error(res.error.message || 'Failed to shortlist')
                                setShortlistedIds(prev => new Set(prev).add(candidate.id))
                                const emailStatus = (res as any)?.data?.email_sent?.status || (res as any)?.email_sent?.status
                                if (emailStatus === 'queued' || emailStatus === 'success') {
                                  setEmailSentIds(prev => new Set(prev).add(candidate.id))
                                  showToast(`✅ ${candidate.name} shortlisted — email sent to ${candidate.email || 'candidate'}`, 'success')
                                } else if (emailStatus === 'error') {
                                  showToast(`⭐ ${candidate.name} shortlisted — email failed`, 'error')
                                } else {
                                  showToast(`⭐ ${candidate.name} shortlisted`, 'info')
                                }
                                refetch()
                              }
                            } catch (err) {
                              console.error('Shortlist failed:', err)
                              showToast(`Failed to shortlist ${candidate.name}`, 'error')
                            } finally {
                              setShortlistingIds(prev => {
                                const next = new Set(prev)
                                next.delete(candidate.id)
                                return next
                              })
                            }
                          }}
                          disabled={!isAdmin || shortlistingIds.has(candidate.id)}
                          className={`p-1 rounded-full transition-colors ${
                            candidate.isShortlisted || candidate.status === 'Shortlisted' || shortlistedIds.has(candidate.id)
                              ? 'text-yellow-500 bg-yellow-50'
                              : shortlistingIds.has(candidate.id)
                                ? 'text-gray-300 opacity-50'
                                : 'text-gray-400 hover:text-yellow-500 hover:bg-yellow-50'
                          }`}
                          title={
                            shortlistingIds.has(candidate.id) ? 'Shortlisting & sending email...' :
                            candidate.isShortlisted || candidate.status === 'Shortlisted' || shortlistedIds.has(candidate.id)
                              ? (emailSentIds.has(candidate.id) ? 'Shortlisted — Email sent' : 'Shortlisted')
                              : 'Shortlist & send email'
                          }
                        >
                          <Star className="w-3.5 h-3.5" fill={
                            candidate.isShortlisted || candidate.status === 'Shortlisted' || shortlistedIds.has(candidate.id) ? 'currentColor' : 'none'
                          } />
                        </button>
                        {emailSentIds.has(candidate.id) && (
                          <span className="text-green-500" title="Shortlist email sent">
                            <Mail className="w-3 h-3" />
                          </span>
                        )}
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            generateQuickProfilePDF(candidate).catch((err: Error) =>
                              toast.error('PDF Failed', err.message || 'Could not generate PDF report')
                            )
                          }}
                          className="p-1 rounded-full hover:bg-sky-100 text-sky-600 transition-colors"
                          title="Download PDF Report"
                        >
                          <Download className="w-3.5 h-3.5" />
                        </button>
                        {candidate.hasResume && (
                          <button
                            onClick={(e) => { e.stopPropagation(); downloadOriginalResume(candidate).catch((err: Error) => toast.error('Download Failed', err.message || 'No resume available')) }}
                            className="p-1 rounded-full hover:bg-emerald-100 text-emerald-600 transition-colors"
                            title="Download Original Resume"
                          >
                            <FileText className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          </div>
          {sortedCandidates.length > displayLimit && (
            <div className="p-4 text-center border-t border-gray-100">
              <Button variant="outline" onClick={() => setDisplayLimit(prev => prev + 50)}>
                Load More ({sortedCandidates.length - displayLimit} remaining)
              </Button>
            </div>
          )}
        </Card>
      )}

      {/* Error State */}
      {!loading && error && (
        <div className="text-center py-12">
          <p className="text-red-600 font-medium">Failed to load candidates</p>
          <p className="text-gray-500 text-sm mt-1">{error}</p>
          <button onClick={refetch} className="mt-3 text-sm text-indigo-600 hover:underline">Try again</button>
        </div>
      )}

      {/* Empty State */}
      {!loading && !error && filteredCandidates.length === 0 && (
        <div className="text-center py-12">
          <Users className="w-16 h-16 text-gray-300 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900">No candidates found</h3>
          <p className="text-gray-500 mt-1">
            Try adjusting your filters or search query
          </p>
        </div>
      )}

    </div>
  )
}
