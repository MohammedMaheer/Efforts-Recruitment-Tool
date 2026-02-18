import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Download,
  FileText,
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
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { jsPDF } from 'jspdf'
import { useCandidates } from '@/hooks/useCandidates'
import { useCandidateStore } from '@/store/candidateStore'
import { useNotificationStore } from '@/store/notificationStore'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/Avatar'
import { Progress } from '@/components/ui/Progress'
import { getMatchScoreColor } from '@/lib/utils'
import { candidateApi } from '@/services/api'

type SortKey = 'score' | 'name' | 'experience' | 'date'
type SortDir = 'asc' | 'desc'

export default function Shortlist() {
  const navigate = useNavigate()
  const { candidates, refetch } = useCandidates({ autoFetch: true })
  const shortlistedIds = useCandidateStore((s) => s.shortlistedIds)
  const toggleShortlist = useCandidateStore((s) => s.toggleShortlist)
  const addNotification = useNotificationStore((s) => s.addNotification)

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState('')
  const [sortKey, setSortKey] = useState<SortKey>('score')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [removing, setRemoving] = useState<string | null>(null)
  const [bulkRemoving, setBulkRemoving] = useState(false)

  // Merge: Zustand shortlistedIds OR backend status === 'Shortlisted'
  const shortlistedCandidates = useMemo(() => {
    const results = candidates.filter(
      (c) => shortlistedIds.includes(c.id) || c.status === 'Shortlisted'
    )

    // Search filter
    const filtered = searchQuery
      ? results.filter((c) => {
          const q = searchQuery.toLowerCase()
          return (
            c.name.toLowerCase().includes(q) ||
            c.location.toLowerCase().includes(q) ||
            c.skills.some((s) => s.toLowerCase().includes(q)) ||
            c.jobCategory?.toLowerCase().includes(q)
          )
        })
      : results

    // Sort
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
  }, [candidates, shortlistedIds, searchQuery, sortKey, sortDir])

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const selectAll = () => {
    const allIds = shortlistedCandidates.map((c) => c.id)
    setSelectedIds((prev) => {
      const allSelected = allIds.every((id) => prev.has(id))
      return allSelected ? new Set() : new Set(allIds)
    })
  }

  const handleRemove = async (candidate: (typeof candidates)[0]) => {
    setRemoving(candidate.id)
    try {
      await candidateApi.updateStatus(candidate.id, 'Reviewed')
      if (shortlistedIds.includes(candidate.id)) toggleShortlist(candidate.id)
      setSelectedIds((prev) => { const n = new Set(prev); n.delete(candidate.id); return n })
      // Refetch candidates so status updates reflect in the filter
      await refetch()
      addNotification({ type: 'info', title: 'Removed', message: `${candidate.name} removed from shortlist` })
    } catch {
      addNotification({ type: 'error', title: 'Error', message: 'Failed to remove candidate' })
    } finally {
      setRemoving(null)
    }
  }

  const handleBulkRemove = async () => {
    if (selectedIds.size === 0) return
    setBulkRemoving(true)
    let removed = 0
    for (const id of selectedIds) {
      const c = candidates.find((x) => x.id === id)
      if (!c) continue
      try {
        await candidateApi.updateStatus(id, 'Reviewed')
        if (shortlistedIds.includes(id)) toggleShortlist(id)
        removed++
      } catch { /* skip */ }
    }
    setSelectedIds(new Set())
    setBulkRemoving(false)
    addNotification({ type: 'info', title: 'Removed', message: `${removed} candidate(s) removed from shortlist` })
  }

  const handleScheduleInterview = (candidate: (typeof candidates)[0]) => {
    const start = new Date()
    start.setDate(start.getDate() + 3)
    start.setHours(10, 0, 0, 0)
    const end = new Date(start)
    end.setHours(11, 0, 0, 0)
    const fmt = (d: Date) => d.toISOString().replace(/-|:|\.\d+/g, '')
    const title = encodeURIComponent(`Interview: ${candidate.name}`)
    const details = encodeURIComponent(`Interview for ${candidate.name}\nEmail: ${candidate.email}\nPhone: ${candidate.phone || 'N/A'}`)
    window.open(`https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&details=${details}&location=${encodeURIComponent('Video Call')}&dates=${fmt(start)}/${fmt(end)}`, '_blank')
  }

  const handleExportCSV = () => {
    if (shortlistedCandidates.length === 0) return alert('No candidates to export')
    const escape = (v: any) => {
      const s = String(v ?? '')
      return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s
    }
    const rows = [
      ['Name', 'Email', 'Match Score', 'Status', 'Experience', 'Location', 'Job Category', 'Skills'],
      ...shortlistedCandidates.map((c) => [
        escape(c.name), escape(c.email), escape(c.matchScore), escape(c.status), escape(c.experience), escape(c.location), escape(c.jobCategory), escape(c.skills.join('; '))
      ]),
    ].map((r) => r.join(',')).join('\n')
    const blob = new Blob([rows], { type: 'text/csv' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `shortlist-${new Date().toISOString().split('T')[0]}.csv`; a.click()
    window.URL.revokeObjectURL(url)
    addNotification({ type: 'success', title: 'Exported', message: `${shortlistedCandidates.length} candidates exported to CSV` })
  }

  const handleExportPDF = () => {
    if (shortlistedCandidates.length === 0) return alert('No candidates to export')
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
    } catch { alert('Error generating PDF') }
  }

  // Stats
  const avgScore = shortlistedCandidates.length > 0
    ? (shortlistedCandidates.reduce((a, c) => a + (c.matchScore ?? 0), 0) / shortlistedCandidates.length).toFixed(0)
    : '0'
  const topCategories = [...new Set(shortlistedCandidates.map((c) => c.jobCategory).filter(Boolean))].slice(0, 3)

  const sortOptions: { key: SortKey; label: string }[] = [
    { key: 'score', label: 'Match Score' },
    { key: 'name', label: 'Name' },
    { key: 'experience', label: 'Experience' },
    { key: 'date', label: 'Applied Date' },
  ]

  return (
    <div className="space-y-5 max-w-6xl mx-auto">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-blue-100 rounded-xl flex items-center justify-center">
            <Star className="w-5 h-5 text-blue-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Shortlist</h1>
            <p className="text-sm text-gray-500">{shortlistedCandidates.length} candidates shortlisted</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleExportCSV}>
            <Download className="w-3.5 h-3.5 mr-1.5" />CSV
          </Button>
          <Button variant="outline" size="sm" onClick={handleExportPDF}>
            <FileText className="w-3.5 h-3.5 mr-1.5" />PDF
          </Button>
        </div>
      </motion.div>

      {/* Stats */}
      {shortlistedCandidates.length > 0 && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { icon: Users, label: 'Total', value: shortlistedCandidates.length, color: 'text-blue-600', bg: 'bg-blue-50 border-blue-100' },
            { icon: TrendingUp, label: 'Avg Score', value: `${avgScore}%`, color: 'text-green-600', bg: 'bg-green-50 border-green-100' },
            { icon: Star, label: '90%+ Matches', value: shortlistedCandidates.filter((c) => c.matchScore >= 90).length, color: 'text-yellow-600', bg: 'bg-yellow-50 border-yellow-100' },
            { icon: Briefcase, label: 'Categories', value: topCategories.length, color: 'text-indigo-600', bg: 'bg-indigo-50 border-indigo-100' },
          ].map((s) => (
            <div key={s.label} className={`rounded-lg border p-3 text-center ${s.bg}`}>
              <s.icon className={`w-4 h-4 mx-auto mb-1 ${s.color}`} />
              <p className={`text-xl font-bold ${s.color}`}>{s.value}</p>
              <p className="text-[11px] text-gray-500">{s.label}</p>
            </div>
          ))}
        </motion.div>
      )}

      {/* Search + Sort + Bulk actions */}
      {shortlistedCandidates.length > 0 && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }} className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2 flex-1 w-full sm:w-auto">
            <div className="relative flex-1 max-w-xs">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Search shortlist..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400"
              />
            </div>
            <div className="flex items-center border border-gray-200 rounded-lg overflow-hidden">
              {sortOptions.map((o) => (
                <button
                  key={o.key}
                  onClick={() => { sortKey === o.key ? setSortDir(d => d === 'asc' ? 'desc' : 'asc') : (setSortKey(o.key), setSortDir('desc')) }}
                  className={`px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    sortKey === o.key ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  {o.label}
                  {sortKey === o.key && (sortDir === 'desc' ? ' ↓' : ' ↑')}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={selectAll} className="text-xs text-gray-600 hover:text-blue-600 flex items-center gap-1">
              {shortlistedCandidates.every((c) => selectedIds.has(c.id))
                ? <CheckSquare className="w-3.5 h-3.5 text-blue-600" />
                : <Square className="w-3.5 h-3.5" />}
              {selectedIds.size > 0 ? `${selectedIds.size} selected` : 'Select all'}
            </button>
            {selectedIds.size > 0 && (
              <Button variant="outline" size="sm" onClick={handleBulkRemove} disabled={bulkRemoving} className="text-red-600 border-red-200 hover:bg-red-50">
                {bulkRemoving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Trash2 className="w-3.5 h-3.5 mr-1" />}
                Remove ({selectedIds.size})
              </Button>
            )}
          </div>
        </motion.div>
      )}

      {/* Empty state */}
      {shortlistedCandidates.length === 0 && !searchQuery && (
        <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
          <Card>
            <CardContent className="py-16">
              <div className="text-center">
                <div className="w-16 h-16 bg-blue-50 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Star className="w-8 h-8 text-blue-400" />
                </div>
                <h3 className="text-lg font-semibold text-gray-900 mb-1">No candidates shortlisted yet</h3>
                <p className="text-sm text-gray-500 mb-4">Use AI Assistant to search and shortlist top talent</p>
                <div className="flex justify-center gap-3">
                  <Button size="sm" onClick={() => navigate('/ai-assistant')}>
                    <Star className="w-4 h-4 mr-1.5" />AI Assistant
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => navigate('/candidates')}>
                    Browse Candidates
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* No search results */}
      {shortlistedCandidates.length === 0 && searchQuery && (
        <Card>
          <CardContent className="py-8 text-center">
            <Search className="w-8 h-8 text-gray-300 mx-auto mb-2" />
            <p className="text-sm text-gray-500">No shortlisted candidates match "{searchQuery}"</p>
          </CardContent>
        </Card>
      )}

      {/* Candidate list */}
      <AnimatePresence>
        <div className="space-y-3">
          {shortlistedCandidates.map((candidate, index) => (
            <motion.div
              key={candidate.id}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, x: -100 }}
              transition={{ delay: 0.05 + index * 0.03 }}
              layout
            >
              <Card className={`hover:shadow-md transition-all border-2 ${
                selectedIds.has(candidate.id) ? 'border-blue-400 bg-blue-50/20' : 'border-transparent shadow-sm'
              }`}>
                <CardContent className="p-4">
                  <div className="flex items-start gap-3">
                    {/* Checkbox */}
                    <button onClick={() => toggleSelect(candidate.id)} className="mt-1 flex-shrink-0">
                      {selectedIds.has(candidate.id)
                        ? <CheckSquare className="w-5 h-5 text-blue-600" />
                        : <Square className="w-5 h-5 text-gray-300 hover:text-gray-500" />}
                    </button>

                    {/* Rank */}
                    <div className="text-lg font-bold text-blue-400 w-6 mt-0.5 flex-shrink-0">
                      #{index + 1}
                    </div>

                    {/* Avatar */}
                    <Avatar className="w-11 h-11 ring-2 ring-blue-100 flex-shrink-0">
                      <AvatarImage src={`https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}`} />
                      <AvatarFallback className="text-base">{candidate.name.charAt(0)}</AvatarFallback>
                    </Avatar>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between mb-1.5">
                        <div className="min-w-0">
                          <h3 className="text-base font-semibold text-gray-900 truncate">{candidate.name}</h3>
                          <div className="flex items-center gap-2 text-sm text-gray-500 mt-0.5">
                            {candidate.jobCategory && (
                              <span className="flex items-center gap-1">
                                <Briefcase className="w-3 h-3" />{candidate.jobCategory}
                              </span>
                            )}
                            <span className="flex items-center gap-1">
                              <MapPin className="w-3 h-3" />{candidate.location}
                            </span>
                            {candidate.experience > 0 && <span>{candidate.experience}yr exp</span>}
                          </div>
                          <p className="text-xs text-gray-500 mt-0.5">{candidate.email}</p>
                        </div>
                        <div className="text-right flex-shrink-0 ml-3">
                          <div className={`text-2xl font-bold ${getMatchScoreColor(candidate.matchScore)}`}>
                            {(candidate.matchScore ?? 50).toFixed(1)}%
                          </div>
                          <Progress
                            value={candidate.matchScore}
                            className="w-20 h-1.5 mt-1"
                            indicatorClassName={
                              candidate.matchScore >= 80 ? 'bg-green-500' : candidate.matchScore >= 60 ? 'bg-yellow-500' : 'bg-red-500'
                            }
                          />
                        </div>
                      </div>

                      {candidate.summary && (
                        <p className="text-sm text-gray-600 line-clamp-2 mb-2">{candidate.summary}</p>
                      )}

                      <div className="flex flex-wrap gap-1.5 mb-3">
                        {candidate.skills.slice(0, 8).map((skill) => (
                          <Badge key={skill} variant="outline" className="text-xs">
                            {skill}
                          </Badge>
                        ))}
                        {candidate.skills.length > 8 && (
                          <Badge variant="outline" className="text-xs text-gray-400">+{candidate.skills.length - 8}</Badge>
                        )}
                      </div>

                      <div className="flex items-center gap-2 flex-wrap">
                        <Button size="sm" onClick={() => navigate(`/candidates/${candidate.id}`)}>
                          <Eye className="w-3.5 h-3.5 mr-1" />View Profile
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => handleScheduleInterview(candidate)}>
                          <Calendar className="w-3.5 h-3.5 mr-1" />Interview
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRemove(candidate)}
                          disabled={removing === candidate.id}
                          className="text-red-500 hover:text-red-700 hover:bg-red-50"
                        >
                          {removing === candidate.id
                            ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                            : <Trash2 className="w-3.5 h-3.5 mr-1" />}
                          Remove
                        </Button>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      </AnimatePresence>
    </div>
  )
}
