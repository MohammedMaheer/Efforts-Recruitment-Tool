import { useState, useMemo, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Download,
  FileText,
  FileDown,
  Star,
  MapPin,
  Search,
  Eye,
  Trash2,
  Calendar,
  CheckSquare,
  Square,
  Users,
  TrendingUp,
  Briefcase,
  Loader2,
  Mail,
  Phone,
  Linkedin,
  X,
  Award,
  ExternalLink,
  RefreshCw,
  Sparkles,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { jsPDF } from 'jspdf'
import { useCandidates } from '@/hooks/useCandidates'
import { useCandidateStore } from '@/store/candidateStore'
import type { Candidate } from '@/types'
import { useNotificationStore } from '@/store/notificationStore'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/Avatar'
import { generateQuickProfilePDF, downloadOriginalResume } from '@/lib/pdfGenerator'
import { toast } from '@/components/ui/Toast'
import { candidateApi } from '@/services/api'
import { getScoreColor, getFitLabel } from '@/lib/utils'
import { ScoreRing } from '@/components/ui/ScoreRing'
import { useAuthStore } from '@/store/authStore'

type SortKey = 'score' | 'name' | 'experience' | 'date'
type SortDir = 'asc' | 'desc'

export default function Shortlist() {
  const navigate = useNavigate()
  const { candidates, loading, error, refetch } = useCandidates({ autoFetch: true })
  const shortlistedIds = useCandidateStore((s) => s.shortlistedIds)
  const toggleShortlist = useCandidateStore((s) => s.toggleShortlist)
  const addNotification = useNotificationStore((s) => s.addNotification)
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [selectedCandidate, setSelectedCandidate] = useState<Candidate | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [removing, setRemoving] = useState<string | null>(null)
  const [bulkRemoving, setBulkRemoving] = useState(false)

  // ONLY use backend status 'Shortlisted' — NOT stale localStorage shortlistedIds
  // This prevents phantom auto-shortlisting from old localStorage data
  const shortlistedCandidates = useMemo(() => {
    const results = candidates.filter((c) => c.status === 'Shortlisted')

    const filtered = searchQuery
      ? results.filter((c) => {
          const q = searchQuery.toLowerCase()
          return (
            c.name.toLowerCase().includes(q) ||
            c.location?.toLowerCase().includes(q) ||
            c.skills.some((s) => s.toLowerCase().includes(q)) ||
            c.email?.toLowerCase().includes(q) ||
            c.jobCategory?.toLowerCase().includes(q)
          )
        })
      : results

    return [...filtered].sort((a, b) => {
      let cmp = 0
      switch (sortKey) {
        case 'score': cmp = (a.matchScore ?? 0) - (b.matchScore ?? 0); break
        case 'name': cmp = a.name.localeCompare(b.name); break
        case 'experience': cmp = (a.experience ?? 0) - (b.experience ?? 0); break
        case 'date': cmp = new Date(a.appliedDate).getTime() - new Date(b.appliedDate).getTime(); break
      }
      return sortDir === 'desc' ? -cmp : cmp
    })
  }, [candidates, searchQuery, sortKey, sortDir])

  // Active candidate for right panel — auto-select first if current is gone
  const activeCandidate = selectedCandidate && shortlistedCandidates.some(c => c.id === selectedCandidate.id)
    ? selectedCandidate
    : shortlistedCandidates[0] || null

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  const selectAll = useCallback(() => {
    const allIds = shortlistedCandidates.map((c) => c.id)
    setSelectedIds((prev) => {
      const allSelected = allIds.length > 0 && allIds.every((id) => prev.has(id))
      return allSelected ? new Set() : new Set(allIds)
    })
  }, [shortlistedCandidates])

  const handleRemove = useCallback(async (candidate: Candidate) => {
    setRemoving(candidate.id)
    try {
      await candidateApi.updateStatus(candidate.id, 'Reviewed')
      if (shortlistedIds.includes(candidate.id)) toggleShortlist(candidate.id)
      setSelectedIds((prev) => { const n = new Set(prev); n.delete(candidate.id); return n })
      if (selectedCandidate?.id === candidate.id) setSelectedCandidate(null)
      await refetch()
      addNotification({ type: 'info', title: 'Removed', message: `${candidate.name} removed from shortlist` })
    } catch {
      addNotification({ type: 'error', title: 'Error', message: 'Failed to remove candidate' })
    } finally {
      setRemoving(null)
    }
  }, [shortlistedIds, toggleShortlist, refetch, addNotification, selectedCandidate])

  const handleBulkRemove = useCallback(async () => {
    if (selectedIds.size === 0) return
    setBulkRemoving(true)
    const idsToRemove = [...selectedIds]
    const results = await Promise.allSettled(
      idsToRemove.map(async (id) => {
        await candidateApi.updateStatus(id, 'Reviewed')
        if (shortlistedIds.includes(id)) toggleShortlist(id)
      })
    )
    const removed = results.filter(r => r.status === 'fulfilled').length
    setSelectedIds(new Set())
    if (selectedCandidate && idsToRemove.includes(selectedCandidate.id)) setSelectedCandidate(null)
    await refetch()
    setBulkRemoving(false)
    addNotification({ type: 'info', title: 'Removed', message: `${removed} candidate(s) removed` })
  }, [selectedIds, shortlistedIds, toggleShortlist, refetch, addNotification, selectedCandidate])

  const handleScheduleInterview = useCallback((candidate: Candidate) => {
    const start = new Date()
    start.setDate(start.getDate() + 3)
    start.setHours(10, 0, 0, 0)
    const end = new Date(start)
    end.setHours(11, 0, 0, 0)
    const fmt = (d: Date) => d.toISOString().replace(/-|:|\.\d+/g, '')
    const title = encodeURIComponent(`Interview: ${candidate.name}`)
    const details = encodeURIComponent(`Interview for ${candidate.name}\nEmail: ${candidate.email}\nPhone: ${candidate.phone || 'N/A'}`)
    window.open(`https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&details=${details}&location=${encodeURIComponent('Video Call')}&dates=${fmt(start)}/${fmt(end)}`, '_blank')
  }, [])

  const handleExportCSV = useCallback(() => {
    if (shortlistedCandidates.length === 0) return
    const escape = (v: string | number | boolean | null | undefined) => {
      const s = String(v ?? '')
      return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s
    }
    const rows = [
      ['Name', 'Email', 'Match Score', 'Status', 'Experience', 'Location', 'Job Category', 'Skills'],
      ...shortlistedCandidates.map((c) => [
        escape(c.name), escape(c.email), escape(c.matchScore), escape(c.status),
        escape(c.experience), escape(c.location), escape(c.jobCategory), escape(c.skills.join('; '))
      ]),
    ].map((r) => r.join(',')).join('\n')
    const blob = new Blob([rows], { type: 'text/csv' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `shortlist-${new Date().toISOString().split('T')[0]}.csv`; a.click()
    window.URL.revokeObjectURL(url)
    addNotification({ type: 'success', title: 'Exported', message: `${shortlistedCandidates.length} candidates exported` })
  }, [shortlistedCandidates, addNotification])

  const handleExportPDF = useCallback(() => {
    if (shortlistedCandidates.length === 0) return
    try {
      const doc = new jsPDF()
      const pw = doc.internal.pageSize.getWidth()
      const ph = doc.internal.pageSize.getHeight()
      doc.setFontSize(20); doc.text('Shortlisted Candidates', pw / 2, 20, { align: 'center' })
      doc.setFontSize(10); doc.text(`Generated: ${new Date().toLocaleDateString()} | Total: ${shortlistedCandidates.length}`, pw / 2, 28, { align: 'center' })
      let y = 40
      shortlistedCandidates.forEach((c, i) => {
        if (y > ph - 40) { doc.addPage(); y = 20 }
        doc.setFontSize(13); doc.setFont('helvetica', 'bold')
        doc.text(`${i + 1}. ${c.name}  (${(c.matchScore ?? 0).toFixed(1)}%)`, 15, y); y += 6
        doc.setFontSize(10); doc.setFont('helvetica', 'normal')
        doc.text(`${c.email} | ${c.location} | ${c.experience}yr exp | ${c.jobCategory}`, 20, y); y += 5
        const sl = doc.splitTextToSize(`Skills: ${c.skills.join(', ')}`, pw - 40)
        doc.text(sl, 20, y); y += sl.length * 5
        const su = doc.splitTextToSize(c.summary || '', pw - 40)
        doc.text(su, 20, y); y += su.length * 5 + 8
      })
      doc.save(`shortlist-${new Date().toISOString().split('T')[0]}.pdf`)
      addNotification({ type: 'success', title: 'PDF Exported', message: `${shortlistedCandidates.length} candidates exported` })
    } catch { addNotification({ type: 'error', title: 'Error', message: 'Failed to generate PDF' }) }
  }, [shortlistedCandidates, addNotification])

  const handleResetAll = useCallback(async () => {
    if (shortlistedCandidates.length === 0) return
    const typed = prompt(`⚠️ This will remove ALL ${shortlistedCandidates.length} candidates from the shortlist (reset to Strong).\n\nType "${shortlistedCandidates.length}" to confirm:`)
    if (typed !== String(shortlistedCandidates.length)) return
    try {
      await candidateApi.resetShortlist()
      setSelectedIds(new Set())
      setSelectedCandidate(null)
      await refetch()
      addNotification({ type: 'success', title: 'Reset Complete', message: `All candidates removed from shortlist` })
    } catch {
      addNotification({ type: 'error', title: 'Error', message: 'Failed to reset shortlist' })
    }
  }, [shortlistedCandidates, refetch, addNotification])

  // Stats
  const stats = useMemo(() => {
    const total = shortlistedCandidates.length
    const avgScore = total > 0
      ? Math.round(shortlistedCandidates.reduce((a, c) => a + (c.matchScore ?? 0), 0) / total)
      : 0
    const strongMatches = shortlistedCandidates.filter((c) => c.matchScore >= 90).length
    const categories = [...new Set(shortlistedCandidates.map((c) => c.jobCategory).filter(Boolean))]
    return { total, avgScore, strongMatches, categories }
  }, [shortlistedCandidates])

  const sortOptions: { key: SortKey; label: string }[] = [
    { key: 'score', label: 'Score' },
    { key: 'name', label: 'Name' },
    { key: 'experience', label: 'Exp' },
    { key: 'date', label: 'Date' },
  ]

  return (
    <div className="h-[calc(100vh-80px)] flex flex-col overflow-hidden">
      {/* Header Bar */}
      <div className="flex-shrink-0 bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-xl flex items-center justify-center shadow-lg shadow-indigo-200">
              <Star className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900">Shortlisted Candidates</h1>
              <p className="text-xs text-gray-500">{stats.total} candidates · Avg {stats.avgScore}% match</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {selectedIds.size > 0 && (
              <Button variant="outline" size="sm" onClick={handleBulkRemove} disabled={bulkRemoving}
                className="text-red-600 border-red-200 hover:bg-red-50">
                {bulkRemoving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Trash2 className="w-3.5 h-3.5 mr-1" />}
                Remove {selectedIds.size}
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={handleExportCSV} disabled={stats.total === 0}>
              <Download className="w-3.5 h-3.5 mr-1.5" />CSV
            </Button>
            <Button variant="outline" size="sm" onClick={handleExportPDF} disabled={stats.total === 0}>
              <FileText className="w-3.5 h-3.5 mr-1.5" />PDF
            </Button>
            {isAdmin && (
            <Button variant="outline" size="sm" onClick={handleResetAll} disabled={stats.total === 0}
              className="text-red-600 border-red-200 hover:bg-red-50">
              <Trash2 className="w-3.5 h-3.5 mr-1.5" />Reset All
            </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={loading}>
              <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />Refresh
            </Button>
          </div>
        </div>

        {/* Stats Row */}
        {stats.total > 0 && (
          <div className="flex items-center gap-4 mt-3">
            {[
              { icon: Users, label: 'Total', value: stats.total, color: 'text-indigo-600' },
              { icon: TrendingUp, label: 'Avg Score', value: `${stats.avgScore}%`, color: 'text-green-600' },
              { icon: Award, label: '90%+', value: stats.strongMatches, color: 'text-amber-600' },
              { icon: Briefcase, label: 'Categories', value: stats.categories.length, color: 'text-purple-600' },
            ].map((s) => (
              <div key={s.label} className="flex items-center gap-1.5 text-sm">
                <s.icon className={`w-3.5 h-3.5 ${s.color}`} />
                <span className="text-gray-500">{s.label}:</span>
                <span className={`font-semibold ${s.color}`}>{s.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Empty state */}
      {stats.total === 0 && !loading && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center max-w-md">
            <div className="w-20 h-20 bg-gradient-to-br from-indigo-50 to-purple-50 rounded-2xl flex items-center justify-center mx-auto mb-5">
              <Star className="w-10 h-10 text-indigo-300" />
            </div>
            <h3 className="text-lg font-semibold text-gray-900 mb-2">No candidates shortlisted</h3>
            <p className="text-sm text-gray-500 mb-6">
              Use the AI Search to find and shortlist top candidates, or browse the candidate list.
            </p>
            <div className="flex justify-center gap-3">
              <Button onClick={() => navigate('/ai-assistant')}>
                <Sparkles className="w-4 h-4 mr-1.5" />AI Search
              </Button>
              <Button variant="outline" onClick={() => navigate('/candidates')}>
                <Users className="w-4 h-4 mr-1.5" />Browse Candidates
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Error state with retry */}
      {error && !loading && stats.total === 0 && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center max-w-md">
            <div className="w-20 h-20 bg-gradient-to-br from-red-50 to-orange-50 rounded-2xl flex items-center justify-center mx-auto mb-5">
              <RefreshCw className="w-10 h-10 text-red-300" />
            </div>
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Failed to load candidates</h3>
            <p className="text-sm text-gray-500 mb-6">
              {error.includes('Session expired') ? 'Your session has expired. Please log in again.' : 'The server may be busy. Please try again.'}
            </p>
            <div className="flex justify-center gap-3">
              <Button onClick={() => refetch()}>
                <RefreshCw className="w-4 h-4 mr-1.5" />Retry
              </Button>
              <Button variant="outline" onClick={() => navigate('/candidates')}>
                <Users className="w-4 h-4 mr-1.5" />Browse Candidates
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Loading */}
      {loading && stats.total === 0 && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
        </div>
      )}

      {/* Main Split Panel */}
      {stats.total > 0 && (
        <div className="flex-1 flex overflow-hidden">
          {/* Left Panel: Candidate List */}
          <div className="w-[340px] flex-shrink-0 border-r border-gray-200 bg-gray-50/50 flex flex-col">
            {/* Search + Controls */}
            <div className="p-3 space-y-2 border-b border-gray-200 bg-white">
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search shortlisted..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-8 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400"
                />
                {searchQuery && (
                  <button onClick={() => setSearchQuery('')} className="absolute right-3 top-1/2 -translate-y-1/2">
                    <X className="w-3.5 h-3.5 text-gray-400 hover:text-gray-600" />
                  </button>
                )}
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1">
                  {sortOptions.map((o) => (
                    <button
                      key={o.key}
                      onClick={() => { sortKey === o.key ? setSortDir(d => d === 'asc' ? 'desc' : 'asc') : (setSortKey(o.key), setSortDir('desc')) }}
                      className={`px-2 py-1 text-[11px] font-medium rounded transition-all ${
                        sortKey === o.key ? 'bg-indigo-600 text-white shadow-sm' : 'text-gray-500 hover:bg-gray-100'
                      }`}
                    >
                      {o.label}{sortKey === o.key && (sortDir === 'desc' ? ' ↓' : ' ↑')}
                    </button>
                  ))}
                </div>
                <button
                  onClick={selectAll}
                  className="text-[11px] text-gray-500 hover:text-indigo-600 flex items-center gap-1 px-1.5 py-1 rounded hover:bg-indigo-50 transition-colors"
                >
                  {shortlistedCandidates.length > 0 && shortlistedCandidates.every((c) => selectedIds.has(c.id))
                    ? <CheckSquare className="w-3.5 h-3.5 text-indigo-600" />
                    : <Square className="w-3.5 h-3.5" />}
                  {selectedIds.size > 0 ? `${selectedIds.size} sel.` : 'All'}
                </button>
              </div>
            </div>

            {/* Scrollable list */}
            <div className="flex-1 overflow-y-auto">
              <AnimatePresence mode="popLayout">
                {shortlistedCandidates.map((candidate, index) => {
                  const isActive = activeCandidate?.id === candidate.id
                  const isChecked = selectedIds.has(candidate.id)
                  const fit = getFitLabel(candidate.matchScore)
                  return (
                    <motion.div
                      key={candidate.id}
                      initial={{ opacity: 0, x: -20 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: -40 }}
                      transition={{ delay: index * 0.02 }}
                      layout
                      onClick={() => setSelectedCandidate(candidate)}
                      className={`relative cursor-pointer border-b border-gray-100 transition-all ${
                        isActive
                          ? 'bg-indigo-50 border-l-[3px] border-l-indigo-500'
                          : 'hover:bg-white border-l-[3px] border-l-transparent'
                      }`}
                    >
                      <div className="p-3 pl-2">
                        <div className="flex items-start gap-2.5">
                          <button
                            onClick={(e) => { e.stopPropagation(); toggleSelect(candidate.id) }}
                            className="mt-1 flex-shrink-0"
                          >
                            {isChecked
                              ? <CheckSquare className="w-4 h-4 text-indigo-600" />
                              : <Square className="w-4 h-4 text-gray-300 hover:text-gray-500" />}
                          </button>
                          <Avatar className="w-9 h-9 flex-shrink-0">
                            <AvatarImage src={`https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}&backgroundColor=6366f1,8b5cf6,a855f7,d946ef`} />
                            <AvatarFallback className="text-xs bg-indigo-100 text-indigo-700">{candidate.name.charAt(0)}</AvatarFallback>
                          </Avatar>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-start justify-between">
                              <div className="min-w-0">
                                <h4 className="text-sm font-semibold text-gray-900 truncate">{candidate.name}</h4>
                                <p className="text-[11px] text-gray-500 truncate">{candidate.jobCategory || 'General'}</p>
                              </div>
                              <div className="flex flex-col items-end flex-shrink-0 ml-2">
                                <span className={`text-base font-bold ${getScoreColor(candidate.matchScore)}`}>
                                  {Math.round(candidate.matchScore)}%
                                </span>
                                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${fit.cls}`}>
                                  {fit.text}
                                </span>
                              </div>
                            </div>
                            {candidate.location && (
                              <div className="flex items-center gap-1 mt-0.5">
                                <MapPin className="w-3 h-3 text-gray-400" />
                                <span className="text-[11px] text-gray-500 truncate">{candidate.location}</span>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </motion.div>
                  )
                })}
              </AnimatePresence>
              {shortlistedCandidates.length === 0 && searchQuery && (
                <div className="p-8 text-center">
                  <Search className="w-6 h-6 text-gray-300 mx-auto mb-2" />
                  <p className="text-sm text-gray-500">No results for "{searchQuery}"</p>
                </div>
              )}
            </div>
            <div className="p-2 border-t border-gray-200 bg-white text-center">
              <span className="text-[11px] text-gray-400">{shortlistedCandidates.length} shortlisted</span>
            </div>
          </div>

          {/* Right Panel: Detail */}
          <div className="flex-1 overflow-y-auto bg-white">
            {activeCandidate ? (
              <CandidateDetailPanel
                candidate={activeCandidate}
                onRemove={handleRemove}
                onScheduleInterview={handleScheduleInterview}
                removing={removing}
                navigate={navigate}
              />
            ) : (
              <div className="h-full flex items-center justify-center text-gray-400">
                <div className="text-center">
                  <Eye className="w-8 h-8 mx-auto mb-2 text-gray-300" />
                  <p className="text-sm">Select a candidate to view details</p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/* =============== Candidate Detail Panel =============== */
function CandidateDetailPanel({
  candidate,
  onRemove,
  onScheduleInterview,
  removing,
  navigate,
}: {
  candidate: Candidate
  onRemove: (c: Candidate) => void
  onScheduleInterview: (c: Candidate) => void
  removing: string | null
  navigate: (path: string) => void
}) {
  const fit = getFitLabel(candidate.matchScore)

  return (
    <motion.div
      key={candidate.id}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
    >
      {/* Hero Header */}
      <div className="bg-gradient-to-r from-indigo-600 via-purple-600 to-indigo-700 text-white p-6">
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-4">
            <Avatar className="w-14 h-14 ring-2 ring-white/30">
              <AvatarImage src={`https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}&backgroundColor=6366f1`} />
              <AvatarFallback className="text-lg bg-white/20 text-white">{candidate.name.charAt(0)}</AvatarFallback>
            </Avatar>
            <div>
              <h2 className="text-xl font-bold">{candidate.name}</h2>
              <p className="text-indigo-200 text-sm">
                {candidate.jobCategory || 'General'} · {candidate.experience || 0}+ Years experience
              </p>
              <div className="flex items-center gap-3 mt-2 text-sm text-indigo-100">
                {candidate.email && (
                  <a href={`mailto:${candidate.email}`} className="flex items-center gap-1 hover:text-white transition-colors">
                    <Mail className="w-3.5 h-3.5" />{candidate.email}
                  </a>
                )}
                {candidate.phone && (
                  <a href={`tel:${candidate.phone}`} className="flex items-center gap-1 hover:text-white transition-colors">
                    <Phone className="w-3.5 h-3.5" />{candidate.phone}
                  </a>
                )}
              </div>
              {candidate.location && (
                <div className="flex items-center gap-1 mt-1 text-sm text-indigo-200">
                  <MapPin className="w-3.5 h-3.5" />{candidate.location}
                </div>
              )}
            </div>
          </div>
          <div className="text-right">
            <ScoreRing score={candidate.matchScore} size={72} />
            <span className={`text-[11px] font-medium px-2 py-0.5 rounded mt-1 inline-block ${fit.cls}`}>
              {fit.text}
            </span>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 px-6 py-3 border-b border-gray-100 bg-gray-50/50">
        <Button size="sm" onClick={() => navigate(`/candidates/${candidate.id}`)}>
          <Eye className="w-3.5 h-3.5 mr-1.5" />Full Profile
        </Button>
        <Button size="sm" variant="outline" onClick={() => onScheduleInterview(candidate)}>
          <Calendar className="w-3.5 h-3.5 mr-1.5" />Schedule Interview
        </Button>
        <Button size="sm" variant="outline" onClick={() => generateQuickProfilePDF(candidate)} className="text-xs">
          <Download className="w-3.5 h-3.5 mr-1.5" />PDF Report
        </Button>
        <Button size="sm" variant="outline" onClick={async () => {
          try { await downloadOriginalResume(candidate) } catch (err) { console.error('Resume download failed:', err); toast.error('Download failed', 'No resume available for this candidate') }
        }} className="text-xs">
          <FileDown className="w-3.5 h-3.5 mr-1.5" />Resume
        </Button>
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={() => onRemove(candidate)} disabled={removing === candidate.id}
          className="text-red-500 hover:text-red-700 hover:bg-red-50">
          {removing === candidate.id
            ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
            : <Trash2 className="w-3.5 h-3.5 mr-1" />}
          Remove
        </Button>
      </div>

      {/* Content */}
      <div className="p-6 space-y-6">
        {/* AI Analysis */}
        {candidate.aiAnalysis && (
          <section>
            <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 mb-3">
              <Sparkles className="w-4 h-4 text-indigo-500" />AI Analysis
            </h3>
            <div className="bg-gradient-to-br from-indigo-50/50 to-purple-50/50 rounded-xl p-4 border border-indigo-100">
              <p className="text-sm text-gray-700 leading-relaxed">
                {String(candidate.aiAnalysis.executive_summary || candidate.summary || 'No AI analysis available. View full profile to generate.')}
              </p>
            </div>
          </section>
        )}

        {!candidate.aiAnalysis && candidate.summary && (
          <section>
            <h3 className="text-sm font-semibold text-gray-900 mb-2">Professional Summary</h3>
            <p className="text-sm text-gray-600 leading-relaxed bg-gray-50 rounded-lg p-4">{candidate.summary}</p>
          </section>
        )}

        {/* Skills */}
        {candidate.skills.length > 0 && (
          <section>
            <h3 className="text-sm font-semibold text-gray-900 mb-2">Skills</h3>
            <div className="flex flex-wrap gap-1.5">
              {candidate.skills.map((skill) => (
                <Badge key={skill} variant="outline" className="text-xs bg-indigo-50/50 text-indigo-700 border-indigo-200">
                  {skill}
                </Badge>
              ))}
            </div>
          </section>
        )}

        {/* Work History */}
        {candidate.workHistory && candidate.workHistory.length > 0 && (
          <section>
            <h3 className="text-sm font-semibold text-gray-900 mb-2">Experience</h3>
            <div className="space-y-3">
              {candidate.workHistory.slice(0, 5).map((job, i) => (
                <div key={i} className="border-l-2 border-indigo-200 pl-3 py-0.5">
                  <p className="text-sm font-medium text-gray-900">{job.title}</p>
                  <p className="text-xs text-gray-500">{job.company}{job.duration && ` · ${job.duration}`}</p>
                  {job.description && <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{job.description}</p>}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Education */}
        {candidate.education && candidate.education.length > 0 && (
          <section>
            <h3 className="text-sm font-semibold text-gray-900 mb-2">Education</h3>
            <div className="space-y-2">
              {candidate.education.map((edu, i) => (
                <div key={i} className="flex items-start gap-2">
                  <Award className="w-4 h-4 text-indigo-400 mt-0.5 flex-shrink-0" />
                  <div>
                    <p className="text-sm font-medium text-gray-900">{edu.degree}{edu.field && ` in ${edu.field}`}</p>
                    <p className="text-xs text-gray-500">{edu.institution}{edu.year && ` · ${edu.year}`}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Contact */}
        <section>
          <h3 className="text-sm font-semibold text-gray-900 mb-2">Contact</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {candidate.email && (
              <a href={`mailto:${candidate.email}`} className="flex items-center gap-2 text-sm text-gray-600 hover:text-indigo-600 p-2 rounded-lg hover:bg-indigo-50 transition-colors">
                <Mail className="w-4 h-4 text-gray-400" />{candidate.email}
              </a>
            )}
            {candidate.phone && (
              <a href={`tel:${candidate.phone}`} className="flex items-center gap-2 text-sm text-gray-600 hover:text-indigo-600 p-2 rounded-lg hover:bg-indigo-50 transition-colors">
                <Phone className="w-4 h-4 text-gray-400" />{candidate.phone}
              </a>
            )}
            {candidate.linkedin && (
              <a href={candidate.linkedin} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2 text-sm text-gray-600 hover:text-indigo-600 p-2 rounded-lg hover:bg-indigo-50 transition-colors">
                <Linkedin className="w-4 h-4 text-gray-400" />LinkedIn
                <ExternalLink className="w-3 h-3 ml-auto text-gray-400" />
              </a>
            )}
            {candidate.location && (
              <div className="flex items-center gap-2 text-sm text-gray-600 p-2">
                <MapPin className="w-4 h-4 text-gray-400" />{candidate.location}
              </div>
            )}
          </div>
        </section>
      </div>
    </motion.div>
  )
}
