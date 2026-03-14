import { motion } from 'framer-motion'
import { useState, useRef, useCallback, useEffect } from 'react'
import { authFetch } from '@/lib/authFetch'
import config from '@/config'
import { useCandidates } from '@/hooks/useCandidates'
import {
  Upload, CheckCircle, AlertCircle, Loader2,
  Files, CheckCircle2, XCircle, Mail, Folder,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { toast } from '@/components/ui/Toast'

export default function UploadFiles() {
  const { refetch } = useCandidates({ autoFetch: false })
  const [activeTab, setActiveTab] = useState<'upload' | 'email'>('upload')
  const [isUploading, setIsUploading] = useState(false)
  const [uploadResults, setUploadResults] = useState<{ status: string; message?: string; filename?: string; candidate_name?: string; job_category?: string; ai_score?: number }[]>([])
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadStats, setUploadStats] = useState({ today: 0, success: 0, failed: 0 })

  // Email scraping state
  const [scrapeStatus, setScrapeStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [scrapeResults, setScrapeResults] = useState<{ resumes_found?: number; count?: number; emails_scanned?: number; error?: string } | null>(null)
  const [scrapeMaxEmails, setScrapeMaxEmails] = useState(50)
  const [scrapeDays, setScrapeDays] = useState(30)

  useEffect(() => {
    const s = uploadResults.filter(r => r.status === 'success').length
    const f = uploadResults.filter(r => r.status !== 'success').length
    if (uploadResults.length) setUploadStats({ today: uploadResults.length, success: s, failed: f })
  }, [uploadResults])

  const handleFileUpload = useCallback(async (files: FileList | File[]) => {
    const validFiles = Array.from(files).filter(f => /\.(pdf|docx?)$/i.test(f.name))
    if (!validFiles.length) { toast.warning('Invalid file type', 'Please upload PDF or DOCX files only.'); return }
    const oversized = validFiles.filter(f => f.size > 10 * 1024 * 1024)
    if (oversized.length) { toast.error('File too large', `${oversized.length} file(s) exceed the 10MB limit.`); return }
    setIsUploading(true); setUploadResults([])
    try {
      const formData = new FormData()
      validFiles.forEach(file => formData.append('files', file))
      const response = await authFetch(`${config.apiUrl}/api/resumes/upload-multiple`, { method: 'POST', body: formData })
      if (response.ok) {
        const data = await response.json()
        setUploadResults(data.results || [])
        setTimeout(() => refetch(), 1000)
      } else {
        const err = await response.json().catch(() => ({ detail: 'Upload failed' }))
        setUploadResults([{ status: 'error', message: err.detail || 'Upload failed' }])
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Network error'
      setUploadResults([{ status: 'error', message }])
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

  const handleEmailScrape = async () => {
    setScrapeStatus('running'); setScrapeResults(null)
    try {
      const params = new URLSearchParams({ process_all: 'true' })
      if (scrapeMaxEmails) params.set('max_emails', String(scrapeMaxEmails))
      if (scrapeDays) params.set('days_back', String(scrapeDays))
      const response = await authFetch(`${config.apiUrl}/api/scraper/process-now?${params.toString()}`, {
        method: 'POST',
      })
      if (response.ok) {
        const data = await response.json()
        setScrapeResults(data); setScrapeStatus('done')
        setTimeout(() => refetch(), 2000)
      } else {
        setScrapeResults({ error: (await response.json().catch(() => ({}))).detail || 'Scrape failed' })
        setScrapeStatus('error')
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Network error'
      setScrapeResults({ error: message }); setScrapeStatus('error')
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Upload Files</h1>
            <p className="text-sm text-gray-500 mt-1">Bulk upload resumes and import from email</p>
          </div>
        </div>
      </motion.div>

      {/* Stats Row */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { icon: Files, label: 'Files Uploaded Today', count: uploadStats.today, bg: 'bg-sky-50', color: 'text-sky-600' },
          { icon: CheckCircle2, label: 'Successful Uploads', count: uploadStats.success, bg: 'bg-green-50', color: 'text-green-600' },
          { icon: XCircle, label: 'Failed Uploads', count: uploadStats.failed, bg: 'bg-red-50', color: 'text-red-600' },
        ].map((s) => (
          <Card key={s.label} className="border border-gray-100 rounded-xl">
            <CardContent className="p-4 flex items-center gap-4">
              <div className={`w-10 h-10 ${s.bg} rounded-xl flex items-center justify-center`}>
                <s.icon className={`w-5 h-5 ${s.color}`} />
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900">{s.count}</p>
                <p className="text-xs text-gray-500">{s.label}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        {(['upload', 'email'] as const).map((tab) => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`px-5 py-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${activeTab === tab ? 'border-sky-500 text-sky-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
          >
            {tab === 'upload' ? <><Folder className="w-4 h-4" /> File Upload</> : <><Mail className="w-4 h-4" /> Email Scraping</>}
          </button>
        ))}
      </div>

      {/* File Upload Tab */}
      {activeTab === 'upload' && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
          <Card className="border border-gray-100 rounded-xl">
            <CardContent className="p-6">
              <h3 className="text-lg font-semibold text-gray-900 mb-1">Smart Batch Upload</h3>
              <p className="text-sm text-gray-500 mb-5">Upload multiple resumes at once. AI will automatically parse and categorize each file.</p>

              {isUploading ? (
                <div className="flex flex-col items-center py-16">
                  <Loader2 className="w-14 h-14 text-sky-500 animate-spin mb-4" />
                  <p className="text-lg font-medium text-gray-900">Processing resumes...</p>
                  <p className="text-sm text-gray-500 mt-1">AI is parsing and extracting information</p>
                </div>
              ) : uploadResults.length > 0 ? (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <CheckCircle className="w-5 h-5 text-green-600" />
                      <span className="font-semibold text-gray-900">{uploadResults.filter(r => r.status === 'success').length} of {uploadResults.length} uploaded successfully</span>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => setUploadResults([])}>Upload More</Button>
                  </div>
                  <div className="max-h-64 overflow-y-auto space-y-2">
                    {uploadResults.map((r, i) => (
                      <div key={i} className={`p-3 rounded-lg border flex items-start gap-3 ${r.status === 'success' ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                        {r.status === 'success' ? <CheckCircle className="w-4 h-4 text-green-600 mt-0.5" /> : <AlertCircle className="w-4 h-4 text-red-600 mt-0.5" />}
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-gray-900 truncate">{r.filename || r.candidate_name || 'Unknown'}</p>
                          {r.candidate_name && <p className="text-xs text-gray-500">{r.candidate_name} — {r.job_category || 'Uncategorized'}</p>}
                          {r.message && r.status !== 'success' && <p className="text-xs text-red-600">{r.message}</p>}
                        </div>
                        {r.ai_score && <Badge className="bg-sky-100 text-sky-700 border-0 text-xs">{r.ai_score}%</Badge>}
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div
                  onDragEnter={handleDrag} onDragLeave={handleDrag} onDragOver={handleDrag} onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                  className={`border-2 border-dashed rounded-xl p-16 text-center cursor-pointer transition-all ${dragActive ? 'border-sky-500 bg-sky-50' : 'border-gray-300 hover:border-sky-400 hover:bg-gray-50'}`}
                >
                  <div className="w-16 h-16 bg-sky-100 rounded-full flex items-center justify-center mx-auto mb-4">
                    <Upload className="w-8 h-8 text-sky-600" />
                  </div>
                  <p className="text-lg font-medium text-gray-900 mb-2">{dragActive ? 'Drop files here' : 'Drop files here or click to browse'}</p>
                  <p className="text-sm text-gray-500">Supports PDF, DOC, DOCX files. Max 10MB per file.</p>
                  <div className="flex items-center justify-center gap-3 mt-4">
                    <Badge className="bg-gray-100 text-gray-600 border-0">.pdf</Badge>
                    <Badge className="bg-gray-100 text-gray-600 border-0">.doc</Badge>
                    <Badge className="bg-gray-100 text-gray-600 border-0">.docx</Badge>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
          <input ref={fileInputRef} type="file" accept=".pdf,.doc,.docx" multiple className="hidden" onChange={(e) => e.target.files && handleFileUpload(e.target.files)} />
        </motion.div>
      )}

      {/* Email Scraping Tab */}
      {activeTab === 'email' && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
          <Card className="border border-gray-100 rounded-xl">
            <CardContent className="p-6">
              <h3 className="text-lg font-semibold text-gray-900 mb-1">Email Resume Scraping</h3>
              <p className="text-sm text-gray-500 mb-5">Automatically scan your inbox for resume attachments and import them.</p>

              <div className="grid grid-cols-2 gap-4 mb-5">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Max Emails to Scan</label>
                  <input type="number" value={scrapeMaxEmails} onChange={(e) => setScrapeMaxEmails(Number(e.target.value))} min={1} max={200}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Days to Look Back</label>
                  <input type="number" value={scrapeDays} onChange={(e) => setScrapeDays(Number(e.target.value))} min={1} max={365}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                  />
                </div>
              </div>

              <Button onClick={handleEmailScrape} disabled={scrapeStatus === 'running'} className="w-full flex items-center justify-center gap-2">
                {scrapeStatus === 'running' ? <><Loader2 className="w-4 h-4 animate-spin" /> Scanning Emails...</> : <><Mail className="w-4 h-4" /> Start Email Scraping</>}
              </Button>

              {scrapeStatus === 'done' && scrapeResults && !scrapeResults.error && (
                <div className="mt-5 p-4 bg-green-50 border border-green-200 rounded-lg">
                  <div className="flex items-center gap-2 mb-2"><CheckCircle className="w-5 h-5 text-green-600" /><span className="font-semibold text-green-900">Scraping Complete</span></div>
                  <p className="text-sm text-green-700">Found {scrapeResults.resumes_found ?? scrapeResults.count ?? 0} resumes from {scrapeResults.emails_scanned ?? 0} emails.</p>
                </div>
              )}
              {scrapeStatus === 'error' && scrapeResults?.error && (
                <div className="mt-5 p-4 bg-red-50 border border-red-200 rounded-lg">
                  <div className="flex items-center gap-2 mb-2"><AlertCircle className="w-5 h-5 text-red-600" /><span className="font-semibold text-red-900">Error</span></div>
                  <p className="text-sm text-red-700">{scrapeResults.error}</p>
                </div>
              )}
            </CardContent>
          </Card>
        </motion.div>
      )}
    </div>
  )
}
