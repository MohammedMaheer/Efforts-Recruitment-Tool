import { motion } from 'framer-motion'
import { useState } from 'react'
import { authFetch } from '@/lib/authFetch'
import config from '@/config'
import {
  FileEdit, Sparkles, Copy, Download, Loader2, X, Plus,
  Briefcase, MapPin, Clock, Users,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { toast } from '@/components/ui/Toast'

const DEPARTMENTS = ['Engineering', 'Marketing', 'Sales', 'HR', 'Finance', 'Operations', 'Design', 'Product', 'Legal', 'Support']
const EXP_LEVELS = ['Entry Level', 'Mid Level', 'Senior Level', 'Lead', 'Director', 'VP', 'C-Level']
const EMP_TYPES = ['Full-time', 'Part-time', 'Contract', 'Freelance', 'Internship']

export default function JDBuilder() {
  const [form, setForm] = useState({
    title: '', department: '', experience_level: '', employment_type: 'Full-time',
    location: '', description: '', skills: [] as string[],
  })
  const [skillInput, setSkillInput] = useState('')
  const [generating, setGenerating] = useState(false)
  const [generatedJD, setGeneratedJD] = useState('')
  const [copied, setCopied] = useState(false)

  const addSkill = () => {
    const s = skillInput.trim()
    if (s && !form.skills.includes(s)) {
      setForm(prev => ({ ...prev, skills: [...prev.skills, s] }))
      setSkillInput('')
    }
  }
  const removeSkill = (skill: string) => {
    setForm(prev => ({ ...prev, skills: prev.skills.filter(s => s !== skill) }))
  }

  const handleGenerate = async () => {
    if (!form.title.trim()) { toast.warning('Missing title', 'Please enter a job title'); return }
    setGenerating(true); setGeneratedJD('')
    try {
      const res = await authFetch(`${config.apiUrl}/api/jd/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      if (res.ok) {
        const data = await res.json()
        setGeneratedJD(data.job_description || data.jd || '')
      } else {
        const err = await res.json().catch(() => ({ detail: 'Generation failed' }))
        toast.error('Generation failed', err.detail || 'Could not generate job description')
      }
    } catch (error: any) {
      toast.error('Network error', error.message || 'Please check your connection')
    } finally { setGenerating(false) }
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(generatedJD)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleDownload = () => {
    const blob = new Blob([generatedJD], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `${form.title || 'job-description'}.txt`; a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">JD Builder</h1>
            <p className="text-sm text-gray-500 mt-1">Create professional job descriptions with AI assistance</p>
          </div>
          <Badge className="bg-purple-100 text-purple-700 border-purple-200 flex items-center gap-1.5 px-3 py-1.5">
            <Sparkles className="w-3.5 h-3.5" /> AI Powered
          </Badge>
        </div>
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* LEFT — Form */}
        <Card className="border border-gray-100 rounded-xl">
          <div className="p-5 border-b border-gray-100">
            <h3 className="font-semibold text-gray-900">Job Details</h3>
            <p className="text-xs text-gray-500">Fill in the details and AI will generate a complete JD</p>
          </div>
          <CardContent className="p-5 space-y-4">
            {/* Title */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                <Briefcase className="w-3.5 h-3.5 inline mr-1.5" />Job Title *
              </label>
              <input value={form.title} onChange={(e) => setForm(p => ({ ...p, title: e.target.value }))}
                placeholder="e.g. Senior Frontend Developer"
                className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
              />
            </div>

            {/* Department & Experience */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Department</label>
                <select value={form.department} onChange={(e) => setForm(p => ({ ...p, department: e.target.value }))}
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500 bg-white"
                >
                  <option value="">Select...</option>
                  {DEPARTMENTS.map(d => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Experience Level</label>
                <select value={form.experience_level} onChange={(e) => setForm(p => ({ ...p, experience_level: e.target.value }))}
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500 bg-white"
                >
                  <option value="">Select...</option>
                  {EXP_LEVELS.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
            </div>

            {/* Employment Type & Location */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  <Clock className="w-3.5 h-3.5 inline mr-1.5" />Employment Type
                </label>
                <select value={form.employment_type} onChange={(e) => setForm(p => ({ ...p, employment_type: e.target.value }))}
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500 bg-white"
                >
                  {EMP_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  <MapPin className="w-3.5 h-3.5 inline mr-1.5" />Location
                </label>
                <input value={form.location} onChange={(e) => setForm(p => ({ ...p, location: e.target.value }))}
                  placeholder="e.g. Remote, New York"
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                />
              </div>
            </div>

            {/* Description */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Brief Description</label>
              <textarea value={form.description} onChange={(e) => setForm(p => ({ ...p, description: e.target.value }))}
                rows={3} placeholder="Briefly describe the role and responsibilities..."
                className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500 resize-none"
              />
            </div>

            {/* Skills */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                <Users className="w-3.5 h-3.5 inline mr-1.5" />Required Skills
              </label>
              <div className="flex gap-2">
                <input value={skillInput} onChange={(e) => setSkillInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addSkill() } }}
                  placeholder="Type a skill and press Enter"
                  className="flex-1 px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-sky-500 focus:border-sky-500"
                />
                <Button size="sm" variant="outline" onClick={addSkill} className="px-3"><Plus className="w-4 h-4" /></Button>
              </div>
              {form.skills.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {form.skills.map(s => (
                    <Badge key={s} className="bg-sky-50 text-sky-700 border-sky-200 flex items-center gap-1 pr-1">
                      {s}
                      <button onClick={() => removeSkill(s)} className="ml-0.5 hover:text-sky-900"><X className="w-3 h-3" /></button>
                    </Badge>
                  ))}
                </div>
              )}
            </div>

            <Button onClick={handleGenerate} disabled={generating} className="w-full flex items-center justify-center gap-2 py-3">
              {generating ? <><Loader2 className="w-4 h-4 animate-spin" /> Generating...</> : <><Sparkles className="w-4 h-4" /> Generate with AI</>}
            </Button>
          </CardContent>
        </Card>

        {/* RIGHT — Preview */}
        <Card className="border border-gray-100 rounded-xl">
          <div className="p-5 border-b border-gray-100 flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-gray-900">Job Description Preview</h3>
              <p className="text-xs text-gray-500">AI-generated professional JD</p>
            </div>
            {generatedJD && (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={handleCopy} className="flex items-center gap-1.5 text-xs">
                  <Copy className="w-3.5 h-3.5" /> {copied ? 'Copied!' : 'Copy'}
                </Button>
                <Button size="sm" variant="outline" onClick={handleDownload} className="flex items-center gap-1.5 text-xs">
                  <Download className="w-3.5 h-3.5" /> Download
                </Button>
              </div>
            )}
          </div>
          <CardContent className="p-5">
            {generating ? (
              <div className="flex flex-col items-center py-20">
                <Loader2 className="w-10 h-10 text-purple-500 animate-spin mb-4" />
                <p className="text-sm font-medium text-gray-700">AI is crafting your job description...</p>
                <p className="text-xs text-gray-500 mt-1">This may take a few seconds</p>
              </div>
            ) : generatedJD ? (
              <div className="prose prose-sm max-w-none">
                <div className="whitespace-pre-wrap text-sm text-gray-800 leading-relaxed">{generatedJD}</div>
              </div>
            ) : (
              <div className="text-center py-20">
                <FileEdit className="w-12 h-12 text-gray-300 mx-auto mb-3" />
                <p className="text-sm font-medium text-gray-500 mb-1">No JD generated yet</p>
                <p className="text-xs text-gray-400">Fill in job details and click &quot;Generate with AI&quot;</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
