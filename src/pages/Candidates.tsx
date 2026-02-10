import { useState, useEffect, useMemo, useCallback } from 'react'
import { Search, SlidersHorizontal, RefreshCw, Loader2, Users, Briefcase, ChevronDown, ChevronRight, Calendar, ArrowUpDown, Mail, MessageCircle, Linkedin, Phone } from 'lucide-react'
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
import { getMatchScoreColor, getStatusBadgeColor } from '@/lib/utils'

// Quick contact helper - opens contact without navigating away
const openContact = (e: React.MouseEvent, type: 'email' | 'whatsapp' | 'linkedin' | 'phone', candidate: any) => {
  e.stopPropagation() // Prevent row click
  
  switch (type) {
    case 'email':
      window.location.href = `mailto:${candidate.email}?subject=Regarding Your Application&body=Hi ${candidate.name},%0A%0A`
      break
    case 'whatsapp':
      const cleanPhone = candidate.phone?.replace(/[\s\-\(\)]/g, '').replace(/^\+/, '') || ''
      if (cleanPhone) {
        window.open(`https://wa.me/${cleanPhone}?text=Hi ${encodeURIComponent(candidate.name)}, I'm reaching out regarding your job application.`, '_blank')
      }
      break
    case 'linkedin':
      if (candidate.linkedin) {
        window.open(candidate.linkedin, '_blank')
      }
      break
    case 'phone':
      window.location.href = `tel:${candidate.phone}`
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

// Category colors for visual distinction
const categoryColors: Record<string, { bg: string; text: string; border: string }> = {
  'Software Engineer': { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  'DevOps Engineer': { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' },
  'Data Scientist': { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
  'Cybersecurity': { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-200' },
  'QA / Testing': { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' },
  'IT & Systems': { bg: 'bg-slate-50', text: 'text-slate-700', border: 'border-slate-200' },
  'Product Manager': { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' },
  'Design': { bg: 'bg-rose-50', text: 'text-rose-700', border: 'border-rose-200' },
  'Project Management': { bg: 'bg-violet-50', text: 'text-violet-700', border: 'border-violet-200' },
  'Business Analyst': { bg: 'bg-sky-50', text: 'text-sky-700', border: 'border-sky-200' },
  'Consulting': { bg: 'bg-fuchsia-50', text: 'text-fuchsia-700', border: 'border-fuchsia-200' },
  'Marketing': { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200' },
  'Content & Communications': { bg: 'bg-lime-50', text: 'text-lime-700', border: 'border-lime-200' },
  'Sales': { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
  'Finance': { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' },
  'HR': { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200' },
  'Legal': { bg: 'bg-stone-50', text: 'text-stone-700', border: 'border-stone-200' },
  'Operations': { bg: 'bg-zinc-50', text: 'text-zinc-700', border: 'border-zinc-200' },
  'Healthcare': { bg: 'bg-cyan-50', text: 'text-cyan-700', border: 'border-cyan-200' },
  'Education': { bg: 'bg-yellow-50', text: 'text-yellow-700', border: 'border-yellow-200' },
  'Engineering': { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  'Customer Support': { bg: 'bg-cyan-50', text: 'text-cyan-700', border: 'border-cyan-200' },
  'Media & Creative': { bg: 'bg-fuchsia-50', text: 'text-fuchsia-700', border: 'border-fuchsia-200' },
  'Real Estate': { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' },
  'Hospitality': { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
  'General': { bg: 'bg-gray-50', text: 'text-gray-700', border: 'border-gray-200' },
}

const getCategoryColor = (category: string) => {
  return categoryColors[category] || categoryColors['General']
}

type SortOption = 'score-desc' | 'score-asc' | 'date-newest' | 'date-oldest' | 'name-asc' | 'name-desc'

export default function Candidates() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { candidates, loading, refetch } = useCandidates({ autoFetch: true })
  // Auto-refresh when email sync detects new candidates
  useEmailSync(refetch, 30000)
  const [searchQuery, setSearchQuery] = useState(searchParams.get('search') || '')
  const [showFilters, setShowFilters] = useState(false)
  const [viewMode, setViewMode] = useState<'grouped' | 'list'>('grouped')
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set())
  const [selectedCategory, setSelectedCategory] = useState<string>('all')
  const [sortBy, setSortBy] = useState<SortOption>('date-newest')  // Default to newest first within categories
  const [dateRange, setDateRange] = useState({ start: '', end: '' })
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
  }, [searchParams])

  // Sort function - memoized for performance
  const sortCandidates = useCallback((items: typeof candidates, sort: SortOption) => {
    const sorted = [...items]
    switch (sort) {
      case 'score-desc':
        return sorted.sort((a, b) => b.matchScore - a.matchScore)
      case 'score-asc':
        return sorted.sort((a, b) => a.matchScore - b.matchScore)
      case 'date-newest':
        return sorted.sort((a, b) => new Date(b.appliedDate).getTime() - new Date(a.appliedDate).getTime())
      case 'date-oldest':
        return sorted.sort((a, b) => new Date(a.appliedDate).getTime() - new Date(b.appliedDate).getTime())
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
      if (selectedCategory !== 'all' && candidate.jobCategory !== selectedCategory) return false
      
      // Search filter (slower, do last)
      if (searchLower) {
        const matchesName = candidate.name.toLowerCase().includes(searchLower)
        const matchesCategory = candidate.jobCategory.toLowerCase().includes(searchLower)
        const matchesSkill = candidate.skills.some(skill => skill.toLowerCase().includes(searchLower))
        if (!matchesName && !matchesCategory && !matchesSkill) return false
      }
      
      return true
    })
  }, [candidates, searchQuery, filters, selectedCategory, dateRange])

  // Sorted candidates
  const sortedCandidates = useMemo(() => {
    return sortCandidates(filteredCandidates, sortBy)
  }, [filteredCandidates, sortBy, sortCandidates])

  // Group candidates by job category
  const groupedCandidates = useMemo(() => {
    const groups: Record<string, typeof candidates> = {}
    sortedCandidates.forEach((candidate) => {
      const category = candidate.jobCategory || 'General'
      if (!groups[category]) {
        groups[category] = []
      }
      groups[category].push(candidate)
    })
    return groups
  }, [sortedCandidates])

  // Get unique categories for filter dropdown
  const categories = useMemo(() => {
    const cats = new Set(candidates.map(c => c.jobCategory || 'General'))
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
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Candidates</h1>
          <p className="text-gray-600 mt-1">
            {loading ? 'Loading...' : `${filteredCandidates.length} candidates in ${Object.keys(groupedCandidates).length} categories`}
          </p>
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
              className="h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent bg-white text-sm"
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
            className="h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent bg-white"
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
                className="w-full h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
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
                  className="flex-1 h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
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
                className="w-full h-10 px-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
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

      {/* Category Overview Cards - Only show in grouped view */}
      {viewMode === 'grouped' && Object.keys(categoryStats).length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {Object.entries(categoryStats).map(([category, stats]) => {
            const colors = getCategoryColor(category)
            return (
              <Card 
                key={category} 
                className={`p-3 cursor-pointer transition-all hover:shadow-md ${colors.bg} border ${colors.border}`}
                onClick={() => setSelectedCategory(selectedCategory === category ? 'all' : category)}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className={`text-xs font-semibold ${colors.text} truncate`}>{category}</span>
                  <Badge variant="secondary" className="text-xs">{stats.total}</Badge>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex-1">
                    <Progress 
                      value={stats.avgScore} 
                      className="h-1.5"
                      indicatorClassName={
                        stats.avgScore >= 70 ? 'bg-green-500' :
                        stats.avgScore >= 50 ? 'bg-yellow-500' : 'bg-red-500'
                      }
                    />
                  </div>
                  <span className="text-xs font-medium text-gray-600">{stats.avgScore}%</span>
                </div>
              </Card>
            )
          })}
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
                    <div className="overflow-x-auto">
                      <Table className="min-w-[900px] w-full">
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-[50px]">Rank</TableHead>
                            <TableHead className="w-[220px]">Candidate</TableHead>
                            <TableHead className="w-[90px]">Score</TableHead>
                            <TableHead className="w-[140px]">Skills</TableHead>
                            <TableHead className="w-[70px]">Exp</TableHead>
                            <TableHead className="w-[90px]">Applied</TableHead>
                            <TableHead className="w-[100px]">Contact</TableHead>
                            <TableHead className="w-[80px]">Status</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                        {categoryCandidates.map((candidate, index) => {
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
                                  #{index + 1}
                                </div>
                              </TableCell>
                              <TableCell className="max-w-[220px]">
                                <div className="flex items-center gap-2 overflow-hidden">
                                  <Avatar className="w-8 h-8 flex-shrink-0">
                                    <AvatarImage
                                      src={`https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}`}
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
                                    {(candidate.matchScore ?? 50).toFixed(0)}%
                                  </p>
                                  <Progress
                                    value={candidate.matchScore}
                                    className="w-14 h-1"
                                    indicatorClassName={
                                      candidate.matchScore >= 70 ? 'bg-green-500' :
                                      candidate.matchScore >= 50 ? 'bg-yellow-500' : 'bg-red-500'
                                    }
                                  />
                                </div>
                              </TableCell>
                              <TableCell className="max-w-[140px]">
                                <div className="flex flex-wrap gap-0.5 overflow-hidden">
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
                                      className="p-1 rounded-full hover:bg-blue-100 text-blue-600 transition-colors"
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
                                      className="p-1 rounded-full hover:bg-purple-100 text-purple-600 transition-colors"
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
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                    </div>
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
                <TableHead className="w-[60px]">Rank</TableHead>
                <TableHead className="w-[250px]">Candidate</TableHead>
                <TableHead className="w-[130px]">Category</TableHead>
                <TableHead className="w-[100px]">Match Score</TableHead>
                <TableHead className="w-[180px]">Skills</TableHead>
                <TableHead className="w-[80px]">Experience</TableHead>
                <TableHead className="w-[90px]">Applied</TableHead>
                <TableHead className="w-[80px]">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedCandidates.map((candidate, index) => {
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
                        #{index + 1}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[250px]">
                      <div className="flex items-center gap-2 overflow-hidden">
                        <Avatar className="w-9 h-9 flex-shrink-0">
                          <AvatarImage
                            src={`https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}`}
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
                          {(candidate.matchScore ?? 50).toFixed(0)}%
                        </p>
                        <Progress
                          value={candidate.matchScore ?? 50}
                          className="w-16 h-1.5"
                          indicatorClassName={
                            (candidate.matchScore ?? 50) >= 70 ? 'bg-green-500' :
                            (candidate.matchScore ?? 50) >= 50 ? 'bg-yellow-500' : 'bg-red-500'
                          }
                        />
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[180px]">
                      <div className="flex flex-wrap gap-0.5 overflow-hidden">
                        {candidate.skills.slice(0, 3).map((skill) => (
                          <Badge key={skill} variant="outline" className="text-xs px-1.5 py-0 whitespace-nowrap" title={skill}>
                            {skill.length > 10 ? skill.slice(0, 10) + '..' : skill}
                          </Badge>
                        ))}
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
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
          </div>
        </Card>
      )}

      {/* Empty State */}
      {!loading && filteredCandidates.length === 0 && (
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
