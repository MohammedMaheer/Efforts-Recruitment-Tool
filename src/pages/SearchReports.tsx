import { motion } from 'framer-motion'
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { authFetch } from '@/lib/authFetch'
import config from '@/config'
import {
  Search, Eye, Trash2, Loader2,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'

interface SearchHistoryEntry {
  id: string
  _id?: string
  query: string
  description?: string
  searched_at: string
  result_count: number
  top_results?: Array<{ name: string; score?: number; matchScore?: number }> | string
}

export default function SearchReports() {
  const navigate = useNavigate()
  const [searches, setSearches] = useState<SearchHistoryEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [clearing, setClearing] = useState(false)
  const [filterText, setFilterText] = useState('')

  const fetchHistory = async () => {
    setLoading(true)
    try {
      const res = await authFetch(`${config.apiUrl}/api/search-history?limit=50`)
      if (res.ok) {
        const data = await res.json()
        setSearches(data.history || [])
      }
    } catch (err) { console.error('Failed to fetch search history:', err) }
    finally { setLoading(false) }
  }

  useEffect(() => { fetchHistory() }, [])

  const [showClearConfirm, setShowClearConfirm] = useState(false)

  const handleClear = async () => {
    setShowClearConfirm(false)
    setClearing(true)
    try {
      const res = await authFetch(`${config.apiUrl}/api/search-history`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Server error')
      setSearches([])
    } catch (err) { console.error('Failed to clear search history:', err) }
    finally { setClearing(false) }
  }

  const handleDeleteOne = async (id: string) => {
    try {
      const res = await authFetch(`${config.apiUrl}/api/search-history/${id}`, { method: 'DELETE' })
      if (res.ok) {
        setSearches(prev => prev.filter(s => (s.id || s._id) !== id))
      }
    } catch (err) { console.error('Failed to delete search entry:', err) }
  }

  const fmtDate = (iso: string) => {
    try {
      const d = new Date(iso)
      return d.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', year: 'numeric', timeZone: 'Asia/Dubai' }) +
        '\n' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'Asia/Dubai' })
    } catch { return iso }
  }

  const filtered = filterText
    ? searches.filter(s => s.query?.toLowerCase().includes(filterText.toLowerCase()))
    : searches

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Search Reports</h1>
            <p className="text-sm text-gray-500 mt-1">Review and re-run your previous searches</p>
          </div>
          <div className="flex gap-2 items-center">
            <div className="relative">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                placeholder="Search history..."
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                className="pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-300 w-48"
              />
            </div>
            {searches.length > 0 && !showClearConfirm && (
              <Button variant="outline" size="sm" onClick={() => setShowClearConfirm(true)} disabled={clearing} className="flex items-center gap-1.5 text-red-600 hover:text-red-700 hover:border-red-300">
                {clearing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />} Clear All
              </Button>
            )}
            {showClearConfirm && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-gray-600">Clear all history?</span>
                <Button variant="outline" size="sm" onClick={handleClear} className="text-red-600 hover:text-red-700 border-red-300 px-3 py-1 text-xs">Yes, clear</Button>
                <Button variant="outline" size="sm" onClick={() => setShowClearConfirm(false)} className="px-3 py-1 text-xs">Cancel</Button>
              </div>
            )}
          </div>
        </div>
      </motion.div>

      {/* Table */}
      <Card className="border border-gray-100 rounded-xl">
        <CardContent className="p-0">
          {loading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-8 h-8 text-sky-500 animate-spin" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16">
              <Search className="w-12 h-12 text-gray-300 mx-auto mb-3" />
              <p className="text-lg font-medium text-gray-700 mb-1">{filterText ? 'No matching searches' : 'No search history'}</p>
              <p className="text-sm text-gray-500 mb-4">{filterText ? 'Try a different filter' : 'Your AI-powered searches will appear here'}</p>
              {!filterText && <Button onClick={() => navigate('/ai-assistant')}>Start Searching</Button>}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-gray-500 border-b border-gray-200 bg-gray-50/50">
                    <th className="text-left px-5 py-3.5 font-medium">JOB DESCRIPTION</th>
                    <th className="text-left px-4 py-3.5 font-medium">DATE & TIME</th>
                    <th className="text-center px-4 py-3.5 font-medium">CANDIDATES</th>
                    <th className="text-left px-4 py-3.5 font-medium">TOP RESULTS</th>
                    <th className="text-center px-4 py-3.5 font-medium">ACTIONS</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((s) => {
                    let topResults: (string | { name?: string; score?: number; matchScore?: number })[] = []
                    try { topResults = typeof s.top_results === 'string' ? JSON.parse(s.top_results) : (s.top_results || []) } catch { topResults = [] }
                    const colors = ['bg-teal-500', 'bg-sky-500', 'bg-purple-500', 'bg-amber-500', 'bg-rose-500', 'bg-emerald-500']
                    return (
                      <tr key={s.id} className="border-b border-gray-50 hover:bg-gray-50/50 transition-colors">
                        <td className="px-5 py-4">
                          <div className="min-w-0 max-w-[350px]">
                            <p className="font-medium text-gray-900 truncate">{s.query}</p>
                            {s.description && <p className="text-xs text-gray-400 truncate mt-0.5">{s.description}</p>}
                          </div>
                        </td>
                        <td className="px-4 py-4 text-xs text-gray-500 whitespace-pre-line">{fmtDate(s.searched_at)}</td>
                        <td className="px-4 py-4 text-center">
                          <span className="text-sm font-semibold text-gray-700">{s.result_count}</span>
                          <span className="text-xs text-gray-400 ml-1">candidates found</span>
                        </td>
                        <td className="px-4 py-4">
                          <div className="space-y-1.5">
                            {topResults.slice(0, 3).map((r, j: number) => {
                              const name = typeof r === 'string' ? r : r.name || 'Unknown'
                              const score = typeof r === 'string' ? undefined : (r.score || r.matchScore)
                              const initial = typeof name === 'string' ? name.charAt(0).toUpperCase() : '?'
                              const bgColor = colors[j % colors.length]
                              return (
                                <div key={j} className="flex items-center gap-2">
                                  <div className={`w-5 h-5 ${bgColor} rounded-full flex items-center justify-center text-white text-[9px] font-bold flex-shrink-0`}>
                                    {initial}
                                  </div>
                                  <span className="text-xs text-gray-700 truncate max-w-[100px]">{typeof name === 'string' ? name.split(' ').slice(0, 2).join(' ') : name}</span>
                                  {score && <span className="text-[10px] font-semibold text-sky-600 ml-auto">{Math.round(score)}%</span>}
                                </div>
                              )
                            })}
                          </div>
                        </td>
                        <td className="px-4 py-4 text-center">
                          <div className="flex items-center justify-center gap-1">
                            <button onClick={() => navigate('/ai-assistant', { state: { restoreSessionQuery: s.query } })} className="p-1.5 rounded-lg hover:bg-sky-50 text-sky-600 transition-colors" title="View Results in AI Search">
                              <Eye className="w-4 h-4" />
                            </button>
                            <button onClick={() => { const id = s.id || s._id; if (id) handleDeleteOne(id) }} className="p-1.5 rounded-lg hover:bg-red-50 text-red-400 hover:text-red-600 transition-colors" title="Delete">
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
