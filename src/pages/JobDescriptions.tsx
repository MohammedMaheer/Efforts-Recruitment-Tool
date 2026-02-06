import { useState } from 'react'
import { motion } from 'framer-motion'
import { Upload, FileText, Sparkles, Check } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { useCandidateStore } from '@/store/candidateStore'
import { useNotificationStore } from '@/store/notificationStore'
import config from '@/config'

export default function JobDescriptions() {
  const navigate = useNavigate()
  const candidates = useCandidateStore((state) => state.candidates)
  const addNotification = useNotificationStore((state) => state.addNotification)
  const [jdText, setJdText] = useState('')
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [analysis, setAnalysis] = useState<any>(null)
  const [isUploading, setIsUploading] = useState(false)

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setIsUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch(`${config.endpoints.jobs}/upload`, {
        method: 'POST',
        body: formData,
      })

      if (response.ok) {
        const data = await response.json()
        setJdText(data.text || '')
      } else {
        throw new Error('Upload failed')
      }
    } catch (error) {
      console.error('Upload error:', error)
      alert('Upload failed. Please try again or paste the text manually.')
    } finally {
      setIsUploading(false)
    }
  }

  const handleAnalyze = async () => {
    if (!jdText.trim()) {
      alert('Please enter a job description')
      return
    }

    setIsAnalyzing(true)
    try {
      const response = await fetch(`${config.endpoints.jobs}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: jdText }),
      })
      
      if (response.ok) {
        const data = await response.json()
        setAnalysis(data)
      } else {
        throw new Error('Analysis failed')
      }
    } catch (error) {
      console.error('Analysis error:', error)
      alert('Analysis failed. Please check if the backend server is running.')
    } finally {
      setIsAnalyzing(false)
    }
  }

  const handleMatchCandidates = () => {
    if (candidates.length === 0) {
      alert('No candidates found. Please upload resumes or sync emails first.')
      return
    }
    addNotification({
      type: 'info',
      title: 'Matching Started',
      message: `Analyzing ${candidates.length} candidates against job requirements`,
      actionUrl: '/candidates'
    })
    navigate('/candidates')
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <h1 className="text-3xl font-bold text-gray-900">Job Descriptions</h1>
        <p className="text-gray-600 mt-1">Upload or paste job descriptions for AI-powered analysis</p>
      </motion.div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Upload Section */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="lg:col-span-2"
        >
          <Card>
            <CardHeader>
              <CardTitle>Job Description Input</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* File Upload */}
              <label className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-primary-400 transition-colors cursor-pointer block">
                <input
                  type="file"
                  accept=".pdf,.docx,.txt"
                  onChange={handleFileUpload}
                  className="hidden"
                  disabled={isUploading}
                />
                <Upload className="w-12 h-12 text-gray-400 mx-auto mb-3" />
                <p className="text-sm font-medium text-gray-900 mb-1">
                  {isUploading ? 'Uploading...' : 'Upload Job Description'}
                </p>
                <p className="text-xs text-gray-600 mb-3">PDF, DOCX, TXT up to 10MB</p>
                <Button variant="outline" size="sm" type="button" disabled={isUploading}>
                  {isUploading ? 'Processing...' : 'Choose File'}
                </Button>
              </label>

              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-gray-300"></div>
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-2 bg-white text-gray-500">or paste text</span>
                </div>
              </div>

              {/* Text Area */}
              <div>
                <textarea
                  value={jdText}
                  onChange={(e) => setJdText(e.target.value)}
                  placeholder="Paste job description here..."
                  className="w-full h-64 p-4 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none text-sm"
                />
                <div className="flex items-center justify-end mt-3">
                  <p className="text-xs text-gray-500">{jdText.length} characters</p>
                </div>
              </div>

              <Button
                onClick={handleAnalyze}
                disabled={!jdText || isAnalyzing}
                className="w-full"
                size="lg"
              >
                {isAnalyzing ? (
                  <>
                    <Sparkles className="w-4 h-4 mr-2 animate-spin" />
                    Analyzing with AI...
                  </>
                ) : (
                  <>
                    <Sparkles className="w-4 h-4 mr-2" />
                    Analyze Job Description
                  </>
                )}
              </Button>
            </CardContent>
          </Card>
        </motion.div>

        {/* Analysis Results */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <Card className="sticky top-6">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileText className="w-5 h-5" />
                AI Analysis
              </CardTitle>
            </CardHeader>
            <CardContent>
              {!analysis ? (
                <div className="text-center py-8">
                  <Sparkles className="w-12 h-12 text-gray-300 mx-auto mb-3" />
                  <p className="text-sm text-gray-600">
                    Upload or paste a job description to see AI-powered insights
                  </p>
                </div>
              ) : (
                <div className="space-y-4">
                  <div>
                    <p className="text-xs font-semibold text-gray-700 mb-2 uppercase tracking-wider">
                      Required Skills
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {analysis.requiredSkills.map((skill: string) => (
                        <Badge key={skill} variant="primary">
                          {skill}
                        </Badge>
                      ))}
                    </div>
                  </div>

                  <div>
                    <p className="text-xs font-semibold text-gray-700 mb-2 uppercase tracking-wider">
                      Preferred Skills
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {analysis.preferredSkills.map((skill: string) => (
                        <Badge key={skill} variant="outline">
                          {skill}
                        </Badge>
                      ))}
                    </div>
                  </div>

                  <div>
                    <p className="text-xs font-semibold text-gray-700 mb-1 uppercase tracking-wider">
                      Experience Level
                    </p>
                    <p className="text-sm text-gray-900">{analysis.experienceLevel}</p>
                  </div>

                  <div>
                    <p className="text-xs font-semibold text-gray-700 mb-1 uppercase tracking-wider">
                      Key Responsibilities
                    </p>
                    <ul className="space-y-2">
                      {analysis.responsibilities.map((resp: string, idx: number) => (
                        <li key={idx} className="flex items-start gap-2 text-sm text-gray-700">
                          <Check className="w-4 h-4 text-success mt-0.5 flex-shrink-0" />
                          <span>{resp}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <Button variant="success" className="w-full mt-4" onClick={handleMatchCandidates}>
                    Run Candidate Matching
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </div>
  )
}
