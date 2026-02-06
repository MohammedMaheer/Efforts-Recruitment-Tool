import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  CheckCircle,
  XCircle,
  AlertCircle,
  Loader,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Copy,
  RefreshCw,
  Server,
  Database,
  Mail,
  Brain,
  MessageSquare,
  Calendar,
  Settings,
  Zap
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { useNotificationStore } from '@/store/notificationStore'
import config from '@/config'

interface SetupCheck {
  name: string
  status: 'configured' | 'not_configured' | 'error' | 'optional'
  message: string
  required: boolean
  instructions: string
  docs_url: string
}

interface SetupReport {
  overall_status: string
  ready_for_production: boolean
  checks: SetupCheck[]
  warnings: string[]
  errors: string[]
  summary: {
    total: number
    configured: number
    not_configured: number
    errors: number
    optional: number
  }
}

interface SetupInstruction {
  id: string
  title: string
  description: string
  required?: boolean
  steps: string[]
  env_vars?: string[]
  docs_url?: string
}

export default function SetupWizard() {
  const addNotification = useNotificationStore((state) => state.addNotification)
  const [setupReport, setSetupReport] = useState<SetupReport | null>(null)
  const [instructions, setInstructions] = useState<{ sections: SetupInstruction[] } | null>(null)
  const [loading, setLoading] = useState(true)
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['quick_start']))
  const [testingService, setTestingService] = useState<string | null>(null)

  useEffect(() => {
    fetchSetupData()
  }, [])

  const fetchSetupData = async () => {
    setLoading(true)
    try {
      const [reportRes, instructionsRes] = await Promise.all([
        fetch(`${config.apiUrl}/api/setup/verify`),
        fetch(`${config.apiUrl}/api/setup/instructions`)
      ])

      if (reportRes.ok) {
        setSetupReport(await reportRes.json())
      }
      if (instructionsRes.ok) {
        setInstructions(await instructionsRes.json())
      }
    } catch (error) {
      console.error('Failed to fetch setup data:', error)
    } finally {
      setLoading(false)
    }
  }

  const testConnection = async (service: string) => {
    setTestingService(service)
    try {
      const response = await fetch(`${config.apiUrl}/api/setup/test-connection/${service}`, {
        method: 'POST'
      })
      const data = await response.json()
      
      if (data.status === 'connected' || data.status === 'working' || data.status === 'configured') {
        addNotification({
          type: 'success',
          title: 'Connection Successful',
          message: `${service} is working correctly`
        })
      } else {
        addNotification({
          type: 'warning',
          title: 'Connection Issue',
          message: data.error || `${service} is not configured`
        })
      }
    } catch (error) {
      addNotification({
        type: 'error',
        title: 'Test Failed',
        message: `Failed to test ${service} connection`
      })
    } finally {
      setTestingService(null)
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    addNotification({
      type: 'success',
      title: 'Copied',
      message: 'Copied to clipboard'
    })
  }

  const toggleSection = (id: string) => {
    const newExpanded = new Set(expandedSections)
    if (newExpanded.has(id)) {
      newExpanded.delete(id)
    } else {
      newExpanded.add(id)
    }
    setExpandedSections(newExpanded)
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'configured':
        return <CheckCircle className="w-5 h-5 text-green-500" />
      case 'error':
        return <XCircle className="w-5 h-5 text-red-500" />
      case 'not_configured':
        return <AlertCircle className="w-5 h-5 text-yellow-500" />
      case 'optional':
        return <AlertCircle className="w-5 h-5 text-gray-400" />
      default:
        return <AlertCircle className="w-5 h-5 text-gray-400" />
    }
  }

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'configured':
        return <Badge className="bg-green-100 text-green-800">Configured</Badge>
      case 'error':
        return <Badge className="bg-red-100 text-red-800">Error</Badge>
      case 'not_configured':
        return <Badge className="bg-yellow-100 text-yellow-800">Not Configured</Badge>
      case 'optional':
        return <Badge className="bg-gray-100 text-gray-600">Optional</Badge>
      default:
        return <Badge className="bg-gray-100 text-gray-600">Unknown</Badge>
    }
  }

  const getSectionIcon = (id: string) => {
    switch (id) {
      case 'quick_start':
        return <Zap className="w-5 h-5" />
      case 'email_oauth':
        return <Mail className="w-5 h-5" />
      case 'ai_models':
        return <Brain className="w-5 h-5" />
      case 'production':
        return <Server className="w-5 h-5" />
      case 'twilio':
        return <MessageSquare className="w-5 h-5" />
      case 'google_calendar':
      case 'calendly':
        return <Calendar className="w-5 h-5" />
      default:
        return <Settings className="w-5 h-5" />
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader className="w-8 h-8 animate-spin text-blue-600" />
        <span className="ml-2 text-gray-600">Loading setup status...</span>
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">Setup & Configuration</h2>
            <p className="text-gray-600 mt-1">
              Configure your AI Recruiter platform for production deployment
            </p>
          </div>
          <Button onClick={fetchSetupData} variant="outline">
            <RefreshCw className="w-4 h-4 mr-2" />
            Refresh Status
          </Button>
        </div>
      </motion.div>

      {/* Overall Status Card */}
      {setupReport && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <Card className={`border-2 ${
            setupReport.ready_for_production 
              ? 'border-green-200 bg-green-50/50' 
              : setupReport.overall_status === 'error'
              ? 'border-red-200 bg-red-50/50'
              : 'border-yellow-200 bg-yellow-50/50'
          }`}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {setupReport.ready_for_production ? (
                    <CheckCircle className="w-8 h-8 text-green-500" />
                  ) : setupReport.overall_status === 'error' ? (
                    <XCircle className="w-8 h-8 text-red-500" />
                  ) : (
                    <AlertCircle className="w-8 h-8 text-yellow-500" />
                  )}
                  <div>
                    <CardTitle className="text-xl">
                      {setupReport.ready_for_production 
                        ? 'Ready for Production' 
                        : setupReport.overall_status === 'error'
                        ? 'Configuration Errors Found'
                        : 'Setup Incomplete'}
                    </CardTitle>
                    <CardDescription>
                      {setupReport.summary.configured}/{setupReport.summary.total} components configured
                    </CardDescription>
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => testConnection('database')}
                    disabled={testingService === 'database'}
                  >
                    {testingService === 'database' ? (
                      <Loader className="w-4 h-4 animate-spin" />
                    ) : (
                      <Database className="w-4 h-4" />
                    )}
                    <span className="ml-1">Test DB</span>
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => testConnection('ai')}
                    disabled={testingService === 'ai'}
                  >
                    {testingService === 'ai' ? (
                      <Loader className="w-4 h-4 animate-spin" />
                    ) : (
                      <Brain className="w-4 h-4" />
                    )}
                    <span className="ml-1">Test AI</span>
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {/* Summary Stats */}
              <div className="grid grid-cols-4 gap-4 mb-4">
                <div className="text-center p-3 bg-white rounded-lg shadow-sm">
                  <p className="text-2xl font-bold text-green-600">{setupReport.summary.configured}</p>
                  <p className="text-xs text-gray-500">Configured</p>
                </div>
                <div className="text-center p-3 bg-white rounded-lg shadow-sm">
                  <p className="text-2xl font-bold text-yellow-600">{setupReport.summary.not_configured}</p>
                  <p className="text-xs text-gray-500">Pending</p>
                </div>
                <div className="text-center p-3 bg-white rounded-lg shadow-sm">
                  <p className="text-2xl font-bold text-red-600">{setupReport.summary.errors}</p>
                  <p className="text-xs text-gray-500">Errors</p>
                </div>
                <div className="text-center p-3 bg-white rounded-lg shadow-sm">
                  <p className="text-2xl font-bold text-gray-600">{setupReport.summary.optional}</p>
                  <p className="text-xs text-gray-500">Optional</p>
                </div>
              </div>

              {/* Errors */}
              {setupReport.errors.length > 0 && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg">
                  <h4 className="font-medium text-red-800 mb-2">⚠️ Critical Issues</h4>
                  <ul className="text-sm text-red-700 space-y-1">
                    {setupReport.errors.map((error, i) => (
                      <li key={i}>• {error}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Warnings */}
              {setupReport.warnings.length > 0 && (
                <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                  <h4 className="font-medium text-yellow-800 mb-2">ℹ️ Optional Components</h4>
                  <ul className="text-sm text-yellow-700 space-y-1">
                    {setupReport.warnings.slice(0, 3).map((warning, i) => (
                      <li key={i}>• {warning}</li>
                    ))}
                    {setupReport.warnings.length > 3 && (
                      <li className="text-yellow-600">+ {setupReport.warnings.length - 3} more...</li>
                    )}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* Detailed Checks */}
      {setupReport && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <Card>
            <CardHeader>
              <CardTitle>Configuration Checks</CardTitle>
              <CardDescription>Detailed status of all configuration components</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {setupReport.checks.map((check, index) => (
                  <div
                    key={index}
                    className={`flex items-center justify-between p-3 rounded-lg ${
                      check.status === 'configured' ? 'bg-green-50' :
                      check.status === 'error' ? 'bg-red-50' :
                      check.status === 'not_configured' ? 'bg-yellow-50' :
                      'bg-gray-50'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      {getStatusIcon(check.status)}
                      <div>
                        <p className="font-medium text-gray-900">{check.name}</p>
                        <p className="text-sm text-gray-600">{check.message}</p>
                        {check.instructions && check.status !== 'configured' && (
                          <p className="text-xs text-blue-600 mt-1">{check.instructions}</p>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {check.required && <Badge variant="outline" className="text-xs">Required</Badge>}
                      {getStatusBadge(check.status)}
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* Setup Instructions */}
      {instructions && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <Card>
            <CardHeader>
              <CardTitle>Setup Instructions</CardTitle>
              <CardDescription>Step-by-step guides for configuring each component</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {instructions.sections.map((section) => (
                  <div key={section.id} className="border rounded-lg overflow-hidden">
                    <button
                      onClick={() => toggleSection(section.id)}
                      className="w-full flex items-center justify-between p-4 bg-gray-50 hover:bg-gray-100 transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <div className={`p-2 rounded-lg ${
                          section.id === 'quick_start' ? 'bg-blue-100 text-blue-600' :
                          section.required ? 'bg-purple-100 text-purple-600' :
                          'bg-gray-100 text-gray-600'
                        }`}>
                          {getSectionIcon(section.id)}
                        </div>
                        <div className="text-left">
                          <h4 className="font-medium text-gray-900">{section.title}</h4>
                          <p className="text-sm text-gray-500">{section.description}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {section.required && (
                          <Badge className="bg-purple-100 text-purple-800">Required</Badge>
                        )}
                        {expandedSections.has(section.id) ? (
                          <ChevronDown className="w-5 h-5 text-gray-400" />
                        ) : (
                          <ChevronRight className="w-5 h-5 text-gray-400" />
                        )}
                      </div>
                    </button>
                    
                    {expandedSections.has(section.id) && (
                      <div className="p-4 bg-white border-t">
                        {/* Steps */}
                        <div className="mb-4">
                          <h5 className="font-medium text-gray-700 mb-2">Steps:</h5>
                          <ol className="space-y-2">
                            {section.steps.map((step, i) => (
                              <li key={i} className="flex items-start gap-2 text-sm text-gray-600">
                                <span className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-xs font-medium">
                                  {i + 1}
                                </span>
                                <span>{step.replace(/^\d+\.\s*/, '')}</span>
                              </li>
                            ))}
                          </ol>
                        </div>

                        {/* Environment Variables */}
                        {section.env_vars && section.env_vars.length > 0 && (
                          <div className="mb-4">
                            <h5 className="font-medium text-gray-700 mb-2">Environment Variables:</h5>
                            <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
                              <code className="text-sm text-gray-100">
                                {section.env_vars.map((envVar, i) => (
                                  <div key={i} className="flex items-center justify-between group">
                                    <span>{envVar}</span>
                                    <button
                                      onClick={() => copyToClipboard(envVar)}
                                      className="opacity-0 group-hover:opacity-100 p-1 hover:bg-gray-700 rounded"
                                    >
                                      <Copy className="w-3 h-3 text-gray-400" />
                                    </button>
                                  </div>
                                ))}
                              </code>
                            </div>
                          </div>
                        )}

                        {/* Docs Link */}
                        {section.docs_url && (
                          <a
                            href={section.docs_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-800"
                          >
                            <ExternalLink className="w-4 h-4" />
                            View detailed documentation
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}
    </div>
  )
}
