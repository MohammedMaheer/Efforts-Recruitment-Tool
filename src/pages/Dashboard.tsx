import { motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useCandidates } from '@/hooks/useCandidates'
import { useRealTimeStats } from '@/hooks/useRealTimeStats'
import config from '@/config'
import { authFetch } from '@/lib/authFetch'
import {
  CheckCircle2, XCircle, Star, Video,
  Search, Eye, Calendar, Plus,
  Upload, Loader2, X, FileText, CheckCircle, AlertCircle,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { toast } from '@/components/ui/Toast'

interface SearchHistoryEntry {
  id: string
  _id?: string
  query: string
  description?: string
  searched_at: string
  result_count: number
  top_results?: Array<{ name: string; score?: number; matchScore?: number }> | string
}

interface UploadResult {
  status: 'success' | 'error'
  filename?: string
  message?: string
}

export default function Dashboard() {
  const navigate = useNavigate()
  const { candidates, refetch, stats } = useCandidates({ autoFetch: true, refreshInterval: 60000 })
  const { stats: liveStats } = useRealTimeStats({ interval: 30000, enabled: true })

  const [pipeline, setPipeline] = useState({ selected: 0, rejected: 0, shortlisted: 0, interviewed: 0, total: 0 })
  const [searches, setSearches] = useState<SearchHistoryEntry[]>([])
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadResults, setUploadResults] = useState<UploadResult[]>([])
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const totalCandidates = liveStats?.total_candidates ?? stats.total
  const storagePercent = Math.min(Math.round((totalCandidates / 10000) * 100), 100)
  const searchQueryCount = searches.length

  // CVs added today / this week (memoized to avoid recomputing on every render)
  const { cvsToday, cvsThisWeek } = useMemo(() => {
    const now = new Date()
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const weekStart = new Date(todayStart)
    weekStart.setDate(weekStart.getDate() - 7)
    return {
      cvsToday: candidates.filter(c => new Date(c.appliedDate) >= todayStart).length,
      cvsThisWeek: candidates.filter(c => new Date(c.appliedDate) >= weekStart).length,
    }
  }, [candidates])

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [pRes, sRes] = await Promise.all([
          authFetch(`${config.apiUrl}/api/stats/pipeline`),
          authFetch(`${config.apiUrl}/api/search-history?limit=10`),
        ])
        if (pRes.ok) setPipeline(await pRes.json())
        if (sRes.ok) { const data = await sRes.json(); setSearches(data.history || []) }
      } catch (err) {
        console.error('Dashboard fetch error:', err)
        toast.error('Load failed', 'Could not load dashboard data')
      }
    }
    fetchData()
  }, [])

  const handleFileUpload = useCallback(async (files: FileList | File[]) => {
    const validFiles = Array.from(files).filter(f => /\.(pdf|docx?)$/i.test(f.name))
    if (!validFiles.length) { toast.warning('Invalid file type', 'Please upload PDF or DOCX files only.'); return }
    setIsUploading(true); setUploadResults([]); setShowUploadModal(true)
    try {
      const formData = new FormData()
      validFiles.forEach(file => formData.append('files', file))
      const response = await authFetch(`${config.apiUrl}/api/resumes/upload-multiple`, { method: 'POST', body: formData })
      if (response.ok) {
        setUploadResults((await response.json()).results || [])
        setTimeout(() => refetch(), 1000)
      } else {
        setUploadResults([{ status: 'error', message: (await response.json().catch(() => ({}))).detail || 'Upload failed' }])
      }
    } catch (error: unknown) {
      setUploadResults([{ status: 'error', message: error instanceof Error ? error.message : 'Network error' }])
    } finally { setIsUploading(false) }
  }, [refetch])

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation()
    setDragActive(e.type === 'dragenter' || e.type === 'dragover')
  }, [])
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setDragActive(false)
    if (e.dataTransfer.files?.length) handleFileUpload(e.dataTransfer.files)
  }, [handleFileUpload])

  const fmtDate = (iso: string) => {
    try {
      const d = new Date(iso)
      return d.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', year: 'numeric', timeZone: 'Asia/Dubai' }) +
        ' ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'Asia/Dubai' })
    } catch { return iso }
  }

  return (
    <div className="space-y-6">
      {/* ── Purple Gradient Top Card ── */}
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-600 via-purple-600 to-violet-700 p-6 text-white shadow-xl">
          <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNDAiIGhlaWdodD0iNDAiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PGNpcmNsZSBjeD0iMjAiIGN5PSIyMCIgcj0iMSIgZmlsbD0icmdiYSgyNTUsMjU1LDI1NSwwLjA1KSIvPjwvc3ZnPg==')] opacity-40" />
          <div className="relative z-10">
            <div className="flex items-center gap-2 mb-5">
              <div className="w-7 h-7 bg-white/20 rounded-lg flex items-center justify-center">
                <FileText className="w-4 h-4" />
              </div>
              <h2 className="text-lg font-bold">All Recruitment Details</h2>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="cursor-pointer hover:opacity-90 transition-opacity" onClick={() => navigate('/candidates')}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-white/80">Candidates Storage</span>
                  <span className="text-xs font-semibold bg-white/20 px-2 py-0.5 rounded-full">{storagePercent}%</span>
                </div>
                <div className="flex items-end gap-3 mb-2">
                  <span className="text-3xl font-bold">{totalCandidates.toLocaleString()}</span>
                  <span className="text-sm text-white/60 pb-1">of 10,000</span>
                </div>
                <div className="w-full h-2 bg-white/20 rounded-full overflow-hidden">
                  <div className="h-full bg-white/80 rounded-full transition-all" style={{ width: `${storagePercent}%` }} />
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-white/80">Search Queries</span>
                  <span className="text-xs font-semibold bg-white/20 px-2 py-0.5 rounded-full">{Math.min(Math.round((searchQueryCount / 1000) * 100), 100)}%</span>
                </div>
                <div className="flex items-end gap-3 mb-2">
                  <span className="text-3xl font-bold">{searchQueryCount}</span>
                  <span className="text-sm text-white/60 pb-1">of 1,000</span>
                </div>
                <div className="w-full h-2 bg-white/20 rounded-full overflow-hidden">
                  <div className="h-full bg-white/80 rounded-full transition-all" style={{ width: `${Math.min((searchQueryCount / 1000) * 100, 100)}%` }} />
                </div>
              </div>
            </div>

            <div className="flex items-center justify-between mt-4 pt-3 border-t border-white/10">
              <Badge className="bg-white/20 text-white border-0 text-xs px-3 py-1 font-semibold">ENTERPRISE</Badge>
              <span className="text-xs text-white/50">
                Last updated: {new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })}
              </span>
            </div>
          </div>
        </div>
      </motion.div>

      {/* ── My Recruitment Dashboard ── */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-xl font-bold text-gray-900">My Recruitment Dashboard</h2>
            <p className="text-sm text-gray-500">Track candidates and manage your recruitment pipeline</p>
          </div>
          <span className="text-xs text-gray-400">{new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}</span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
          {[
            { icon: CheckCircle2, label: 'Selected', sublabel: 'Ready to hire', count: pipeline.selected, color: 'text-green-500', bg: 'bg-green-50', status: 'Offered' },
            { icon: XCircle, label: 'Rejected', sublabel: 'This month', count: pipeline.rejected, color: 'text-red-500', bg: 'bg-red-50', status: 'Rejected' },
            { icon: Star, label: 'Shortlisted', sublabel: 'Awaiting review', count: pipeline.shortlisted, color: 'text-amber-500', bg: 'bg-amber-50', status: 'Shortlisted' },
            { icon: Video, label: 'Interviewed', sublabel: 'Completed interviews', count: pipeline.interviewed, color: 'text-blue-500', bg: 'bg-blue-50', status: 'Interviewing' },
            { icon: Upload, label: 'CVs Today', sublabel: 'Added today', count: cvsToday, color: 'text-teal-500', bg: 'bg-teal-50', status: '' },
            { icon: FileText, label: 'CVs This Week', sublabel: 'Last 7 days', count: cvsThisWeek, color: 'text-purple-500', bg: 'bg-purple-50', status: '' },
          ].map((item) => (
            <Card key={item.label} className="border border-gray-100 hover:shadow-md transition-shadow rounded-xl cursor-pointer" onClick={() => item.status ? navigate(`/candidates?status=${item.status}`) : navigate('/candidates')}>
              <CardContent className="p-5 text-center">
                <div className={`w-10 h-10 ${item.bg} rounded-full flex items-center justify-center mx-auto mb-3`}>
                  <item.icon className={`w-5 h-5 ${item.color}`} />
                </div>
                <p className="text-3xl font-bold text-gray-900 mb-1">{item.count}</p>
                <p className="text-sm font-semibold text-gray-700">{item.label}</p>
                <p className="text-xs text-gray-400">{item.sublabel}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* ── Bottom Row: Recent Searches + Schedule ── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <Card className="lg:col-span-3 border border-gray-100 rounded-xl">
          <div className="p-5 border-b border-gray-100 flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-gray-900">Recent Resume Searches</h3>
              <p className="text-xs text-gray-500">Your latest search activities and results</p>
            </div>
            <button onClick={() => navigate('/search-reports')} className="text-xs text-sky-600 hover:text-sky-700 font-medium flex items-center gap-1">
              <Eye className="w-3.5 h-3.5" /> View All
            </button>
          </div>
          <CardContent className="p-0">
            {searches.length === 0 ? (
              <div className="p-8 text-center">
                <Search className="w-10 h-10 text-gray-300 mx-auto mb-3" />
                <p className="text-sm text-gray-500 mb-2">No searches yet</p>
                <Button size="sm" onClick={() => navigate('/ai-assistant')}>Start Searching</Button>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 border-b border-gray-100">
                      <th className="text-left px-5 py-3 font-medium">SEARCH QUERY</th>
                      <th className="text-center px-3 py-3 font-medium">RESULTS</th>
                      <th className="text-left px-3 py-3 font-medium">DATE & TIME</th>
                      <th className="text-center px-3 py-3 font-medium">ACTIONS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {searches.slice(0, 5).map((s) => (
                      <tr key={s.id} className="border-b border-gray-50 hover:bg-gray-50/50 transition-colors">
                        <td className="px-5 py-3">
                          <div className="flex items-start gap-2">
                            <Search className="w-3.5 h-3.5 text-gray-400 mt-0.5 flex-shrink-0" />
                            <div className="min-w-0">
                              <p className="font-medium text-gray-900 truncate max-w-[260px]">{s.query}</p>
                              <p className="text-xs text-gray-400 truncate max-w-[260px]">{s.description}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-3 py-3 text-center">
                          <Badge className="bg-green-50 text-green-700 border-green-200 text-xs">{s.result_count} matches</Badge>
                        </td>
                        <td className="px-3 py-3 text-xs text-gray-500">{fmtDate(s.searched_at)}</td>
                        <td className="px-3 py-3 text-center">
                          <button onClick={() => navigate('/ai-assistant', { state: { prefillQuery: s.query } })} className="text-xs text-sky-600 hover:text-sky-700 font-medium">View Results</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2 border border-gray-100 rounded-xl">
          <div className="p-5 border-b border-gray-100 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Calendar className="w-4 h-4 text-sky-500" />
              <h3 className="font-semibold text-gray-900">Schedule</h3>
            </div>
            <button onClick={() => navigate('/ai-assistant')} title="Schedule via AI Search" className="w-7 h-7 rounded-lg bg-gray-100 flex items-center justify-center hover:bg-gray-200 transition-colors">
              <Plus className="w-4 h-4 text-gray-500" />
            </button>
          </div>
          <CardContent className="p-4">
            <p className="text-xs font-medium text-gray-500 mb-3">Upcoming Events</p>
            {candidates.filter(c => c.status === 'Shortlisted' || c.status === 'Interviewing').length === 0 ? (
              <div className="text-center py-6">
                <Calendar className="w-8 h-8 text-gray-300 mx-auto mb-2" />
                <p className="text-sm text-gray-400">No upcoming events</p>
                <p className="text-xs text-gray-400 mt-1">Schedule interviews from AI Search</p>
              </div>
            ) : (
              <div className="space-y-3">
                {candidates
                  .filter(c => c.status === 'Shortlisted' || c.status === 'Interviewing')
                  .slice(0, 4)
                  .map((c) => (
                    <div key={c.id} className="flex items-start gap-3 p-3 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors cursor-pointer" onClick={() => navigate(`/candidates/${c.id}`)}>
                      <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${c.status === 'Shortlisted' ? 'bg-amber-400' : 'bg-blue-400'}`} />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-gray-900 truncate">{c.status === 'Interviewing' ? 'Decision' : 'Follow-up'}: {c.name}</p>
                        <p className="text-xs text-gray-500 truncate">{c.jobCategory}</p>
                        <p className="text-xs text-gray-400 mt-0.5">{c.appliedDate ? new Date(c.appliedDate).toLocaleDateString() : ''}</p>
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
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => { setShowUploadModal(false); setUploadResults([]) }}>
          <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full max-h-[80vh] overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-6 border-b border-gray-200">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-sky-100 rounded-xl flex items-center justify-center"><Upload className="w-5 h-5 text-sky-600" /></div>
                <div><h2 className="text-xl font-bold text-gray-900">Upload Resumes</h2><p className="text-sm text-gray-500">PDF or DOCX files</p></div>
              </div>
              <button onClick={() => { setShowUploadModal(false); setUploadResults([]) }} className="p-2 hover:bg-gray-100 rounded-lg"><X className="w-5 h-5 text-gray-500" /></button>
            </div>
            <div className="p-6">
              {isUploading ? (
                <div className="flex flex-col items-center py-12">
                  <Loader2 className="w-12 h-12 text-sky-500 animate-spin mb-4" />
                  <p className="text-lg font-medium text-gray-900">Processing resumes...</p>
                </div>
              ) : uploadResults.length > 0 ? (
                <div className="space-y-4">
                  <div className="flex items-center gap-2"><CheckCircle className="w-5 h-5 text-green-600" /><span className="font-medium">{uploadResults.filter(r => r.status === 'success').length} uploaded</span></div>
                  <div className="max-h-60 overflow-y-auto space-y-2">
                    {uploadResults.map((r, i) => (
                      <div key={i} className={`p-3 rounded-lg border ${r.status === 'success' ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                        <div className="flex items-start gap-3">
                          {r.status === 'success' ? <CheckCircle className="w-5 h-5 text-green-600" /> : <AlertCircle className="w-5 h-5 text-red-600" />}
                          <p className="font-medium truncate">{r.filename || 'Unknown'}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                  <Button onClick={() => { setUploadResults([]); setShowUploadModal(false) }} className="w-full">Done</Button>
                </div>
              ) : (
                <div onDragEnter={handleDrag} onDragLeave={handleDrag} onDragOver={handleDrag} onDrop={handleDrop} onClick={() => fileInputRef.current?.click()}
                  className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all ${dragActive ? 'border-sky-500 bg-sky-50' : 'border-gray-300 hover:border-sky-400'}`}
                >
                  <div className="w-16 h-16 bg-sky-100 rounded-full flex items-center justify-center mx-auto mb-4"><Upload className="w-8 h-8 text-sky-600" /></div>
                  <p className="text-lg font-medium text-gray-900 mb-2">{dragActive ? 'Drop files here' : 'Drop files here or click to browse'}</p>
                  <p className="text-sm text-gray-500">Support for PDF, DOC, DOCX files (max 10MB per file)</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      <input ref={fileInputRef} type="file" accept=".pdf,.doc,.docx" multiple className="hidden" onChange={(e) => e.target.files && handleFileUpload(e.target.files)} />
    </div>
  )
}
