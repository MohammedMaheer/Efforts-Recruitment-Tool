import { motion } from 'framer-motion'
import { Download, FileText, Star } from 'lucide-react'
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

export default function Shortlist() {
  const navigate = useNavigate()
  const { candidates } = useCandidates({ autoFetch: true })
  const shortlistedIds = useCandidateStore((state) => state.shortlistedIds)
  const toggleShortlist = useCandidateStore((state) => state.toggleShortlist)
  const addNotification = useNotificationStore((state) => state.addNotification)

  const shortlistedCandidates = candidates
    .filter((c) => shortlistedIds.includes(c.id))
    .sort((a, b) => b.matchScore - a.matchScore)

  const handleExportCSV = () => {
    if (shortlistedCandidates.length === 0) {
      alert('No candidates to export')
      return
    }

    const csvContent = [
      ['Name', 'Email', 'Match Score', 'Status', 'Experience', 'Location'],
      ...shortlistedCandidates.map((c) => [
        c.name,
        c.email,
        c.matchScore,
        c.status,
        c.experience,
        c.location,
      ]),
    ]
      .map((row) => row.join(','))
      .join('\n')

    const blob = new Blob([csvContent], { type: 'text/csv' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `shortlisted-candidates-${new Date().toISOString().split('T')[0]}.csv`
    a.click()
    window.URL.revokeObjectURL(url)
    
    addNotification({
      type: 'success',
      title: 'CSV Exported',
      message: `Exported ${shortlistedCandidates.length} candidates to CSV`,
    })
  }

  const handleExportPDF = () => {
    if (shortlistedCandidates.length === 0) {
      alert('No candidates to export')
      return
    }
    
    try {
      const doc = new jsPDF()
      const pageWidth = doc.internal.pageSize.getWidth()
      const pageHeight = doc.internal.pageSize.getHeight()
      
      // Title
      doc.setFontSize(20)
      doc.text('Shortlisted Candidates', pageWidth / 2, 20, { align: 'center' })
      
      doc.setFontSize(10)
      doc.text(`Generated on: ${new Date().toLocaleDateString()}`, pageWidth / 2, 28, { align: 'center' })
      
      let yPos = 40
      
      shortlistedCandidates.forEach((candidate, index) => {
        // Check if we need a new page
        if (yPos > pageHeight - 40) {
          doc.addPage()
          yPos = 20
        }
        
        // Candidate header
        doc.setFontSize(14)
        doc.setFont('helvetica', 'bold')
        doc.text(`${index + 1}. ${candidate.name}`, 15, yPos)
        yPos += 7
        
        // Match score
        doc.setFontSize(10)
        doc.setFont('helvetica', 'normal')
        doc.text(`Match Score: ${(candidate.matchScore ?? 50).toFixed(1)}% | Status: ${candidate.status}`, 20, yPos)
        yPos += 5
        
        // Contact info
        doc.text(`Email: ${candidate.email} | Phone: ${candidate.phone}`, 20, yPos)
        yPos += 5
        doc.text(`Location: ${candidate.location} | Experience: ${candidate.experience} years`, 20, yPos)
        yPos += 7
        
        // Skills
        doc.setFont('helvetica', 'bold')
        doc.text('Skills:', 20, yPos)
        yPos += 5
        doc.setFont('helvetica', 'normal')
        const skillsText = candidate.skills.join(', ')
        const skillsLines = doc.splitTextToSize(skillsText, pageWidth - 40)
        doc.text(skillsLines, 20, yPos)
        yPos += (skillsLines.length * 5) + 5
        
        // Summary
        doc.setFont('helvetica', 'bold')
        doc.text('Summary:', 20, yPos)
        yPos += 5
        doc.setFont('helvetica', 'normal')
        const summaryLines = doc.splitTextToSize(candidate.summary, pageWidth - 40)
        doc.text(summaryLines, 20, yPos)
        yPos += (summaryLines.length * 5) + 10
      })
      
      // Save the PDF
      doc.save(`shortlisted-candidates-${new Date().toISOString().split('T')[0]}.pdf`)
      
      addNotification({
        type: 'success',
        title: 'PDF Exported',
        message: `Exported ${shortlistedCandidates.length} candidates to PDF`,
      })
    } catch (error) {
      console.error('PDF generation error:', error)
      alert('Error generating PDF. Please try again.')
    }
  }

  const handleScheduleInterview = (candidate: any) => {
    // Create calendar event using native browser calendar
    const startDate = new Date()
    startDate.setDate(startDate.getDate() + 3) // 3 days from now
    startDate.setHours(10, 0, 0, 0) // 10:00 AM
    
    const endDate = new Date(startDate)
    endDate.setHours(11, 0, 0, 0) // 11:00 AM
    
    const formatDate = (date: Date) => {
      return date.toISOString().replace(/-|:|\.\d+/g, '')
    }
    
    const title = encodeURIComponent(`Interview: ${candidate.name}`)
    const details = encodeURIComponent(`Interview for ${candidate.name}\nEmail: ${candidate.email}\nPhone: ${candidate.phone || 'N/A'}`)
    const location = encodeURIComponent('Video Call')
    
    // Google Calendar URL
    const googleCalUrl = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${title}&details=${details}&location=${location}&dates=${formatDate(startDate)}/${formatDate(endDate)}`
    
    window.open(googleCalUrl, '_blank')
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="flex items-center justify-between"
      >
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Shortlist</h1>
          <p className="text-gray-600 mt-1">
            {shortlistedCandidates.length} candidates shortlisted
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="outline" onClick={handleExportCSV}>
            <Download className="w-4 h-4 mr-2" />
            Export CSV
          </Button>
          <Button variant="outline" onClick={handleExportPDF}>
            <FileText className="w-4 h-4 mr-2" />
            Export PDF
          </Button>
        </div>
      </motion.div>

      {shortlistedCandidates.length === 0 ? (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <Card>
            <CardContent className="py-16">
              <div className="text-center">
                <Star className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                <h3 className="text-xl font-semibold text-gray-900 mb-2">
                  No candidates shortlisted yet
                </h3>
                <p className="text-gray-600 mb-6">
                  Start adding candidates to your shortlist to keep track of top talent
                </p>
                <Button onClick={() => navigate('/candidates')}>
                  Browse Candidates
                </Button>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      ) : (
        <div className="space-y-4">
          {shortlistedCandidates.map((candidate, index) => (
            <motion.div
              key={candidate.id}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 + index * 0.05 }}
            >
              <Card className="hover:shadow-medium transition-shadow">
                <CardContent className="p-6">
                  <div className="flex items-start justify-between">
                    <div className="flex items-start gap-4 flex-1">
                      <div className="flex items-center gap-4">
                        <div className="text-2xl font-bold text-gray-400 w-8">
                          #{index + 1}
                        </div>
                        <Avatar className="w-16 h-16">
                          <AvatarImage
                            src={`https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}`}
                          />
                          <AvatarFallback className="text-lg">
                            {candidate.name.charAt(0)}
                          </AvatarFallback>
                        </Avatar>
                      </div>
                      <div className="flex-1">
                        <div className="flex items-start justify-between mb-2">
                          <div>
                            <h3 className="text-xl font-semibold text-gray-900">
                              {candidate.name}
                            </h3>
                            <p className="text-sm text-gray-600 mt-1">
                              {candidate.email} · {candidate.location}
                            </p>
                          </div>
                          <div className="text-right">
                            <div
                              className={`text-3xl font-bold ${getMatchScoreColor(
                                candidate.matchScore
                              )}`}
                            >
                              {(candidate.matchScore ?? 50).toFixed(1)}%
                            </div>
                            <Progress
                              value={candidate.matchScore}
                              className="w-24 h-2 mt-2"
                              indicatorClassName={
                                candidate.matchScore >= 80
                                  ? 'bg-success'
                                  : candidate.matchScore >= 60
                                  ? 'bg-warning'
                                  : 'bg-danger'
                              }
                            />
                          </div>
                        </div>
                        <p className="text-sm text-gray-700 mb-3 line-clamp-2">
                          {candidate.summary}
                        </p>
                        <div className="flex flex-wrap gap-2 mb-4">
                          {candidate.skills.map((skill) => (
                            <Badge key={skill} variant="outline" className="text-xs">
                              {skill}
                            </Badge>
                          ))}
                        </div>
                        <div className="flex items-center gap-3">
                          <Button
                            size="sm"
                            onClick={() => navigate(`/candidates/${candidate.id}`)}
                          >
                            View Profile
                          </Button>
                          <Button variant="outline" size="sm" onClick={() => handleScheduleInterview(candidate)}>
                            Schedule Interview
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation()
                              toggleShortlist(candidate.id)
                            }}
                          >
                            <Star className="w-4 h-4 fill-yellow-400 text-yellow-400 mr-1" />
                            Remove
                          </Button>
                        </div>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  )
}
