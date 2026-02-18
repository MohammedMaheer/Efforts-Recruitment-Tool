/**
 * Efforts Solutions AI Recruiter — Branded PDF Generator
 * Generates professional candidate assessment PDFs with AI summary + profile
 */

import { jsPDF } from 'jspdf'

// Efforts Solutions brand colors
const BRAND = {
  primary: [29, 78, 216] as [number, number, number],     // #1d4ed8 — deep royal blue
  primaryDark: [23, 37, 84] as [number, number, number],   // #172554 — navy
  primaryLight: [219, 234, 254] as [number, number, number],// #dbeafe — light blue
  accent: [59, 130, 246] as [number, number, number],      // #3b82f6 — bright blue
  white: [255, 255, 255] as [number, number, number],
  black: [15, 23, 42] as [number, number, number],         // slate-900
  gray: [100, 116, 139] as [number, number, number],       // slate-500
  grayLight: [241, 245, 249] as [number, number, number],  // slate-100
  green: [22, 163, 74] as [number, number, number],        // green-600
  red: [220, 38, 38] as [number, number, number],          // red-600
  amber: [217, 119, 6] as [number, number, number],        // amber-600
}

interface CandidateData {
  name: string
  email: string
  phone?: string
  location: string
  experience: number
  matchScore: number
  status: string
  skills: string[]
  summary?: string
  jobCategory?: string
  jobSubcategory?: string
  linkedin?: string
  education?: Array<{ degree: string; institution: string; year: string; field?: string }>
  workHistory?: Array<{ title: string; company: string; duration: string; description?: string }>
  certifications?: string[]
  languages?: string[]
}

interface AIAnalysisData {
  executive_summary?: string
  technical_assessment?: string
  experience_assessment?: string
  education_assessment?: string
  pros?: string[]
  cons?: string[]
  career_trajectory?: string
  hiring_recommendation?: string
  hiring_recommendation_rationale?: string
  overall_rating?: string
  confidence_score?: number
  interview_focus_areas?: string[]
  ideal_roles?: string[]
  salary_range_estimate?: string
  culture_fit_notes?: string
}

// Cache for logo image data URL + natural dimensions
let cachedLogoDataUrl: string | null = null
let cachedLogoAspect: number = 1 // width / height

async function getLogoDataUrl(): Promise<{ dataUrl: string; aspect: number }> {
  if (cachedLogoDataUrl) return { dataUrl: cachedLogoDataUrl, aspect: cachedLogoAspect }
  
  return new Promise((resolve) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = img.naturalWidth
      canvas.height = img.naturalHeight
      const ctx = canvas.getContext('2d')!
      ctx.drawImage(img, 0, 0)
      cachedLogoDataUrl = canvas.toDataURL('image/png')
      cachedLogoAspect = img.naturalWidth / img.naturalHeight
      resolve({ dataUrl: cachedLogoDataUrl, aspect: cachedLogoAspect })
    }
    img.onerror = () => {
      // Fallback: generate a simple "E" logo
      const canvas = document.createElement('canvas')
      canvas.width = 120
      canvas.height = 120
      const ctx = canvas.getContext('2d')!
      
      // Blue rounded rect
      ctx.fillStyle = '#1d4ed8'
      ctx.beginPath()
      ctx.roundRect(0, 0, 120, 120, 20)
      ctx.fill()
      
      // White "E"
      ctx.fillStyle = '#ffffff'
      ctx.font = 'bold 80px Arial'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText('E', 60, 65)
      
      cachedLogoDataUrl = canvas.toDataURL('image/png')
      cachedLogoAspect = 1
      resolve({ dataUrl: cachedLogoDataUrl, aspect: cachedLogoAspect })
    }
    img.src = '/effortz-logo.png'
  })
}

function setColor(doc: jsPDF, color: [number, number, number]) {
  doc.setTextColor(color[0], color[1], color[2])
}

function drawRect(doc: jsPDF, x: number, y: number, w: number, h: number, color: [number, number, number], radius?: number) {
  doc.setFillColor(color[0], color[1], color[2])
  if (radius) {
    // Rounded rectangle
    doc.roundedRect(x, y, w, h, radius, radius, 'F')
  } else {
    doc.rect(x, y, w, h, 'F')
  }
}

function drawLine(doc: jsPDF, x1: number, y1: number, x2: number, y2: number, color: [number, number, number], width = 0.5) {
  doc.setDrawColor(color[0], color[1], color[2])
  doc.setLineWidth(width)
  doc.line(x1, y1, x2, y2)
}

/**
 * Draw branded header with logo, company name, and candidate name
 */
async function drawHeader(doc: jsPDF, candidate: CandidateData, pageWidth: number): Promise<number> {
  // Navy header bar
  drawRect(doc, 0, 0, pageWidth, 42, BRAND.primaryDark)
  
  // Logo — constrain to max width so it never overlaps with text
  let textStartX = 48 // default if logo fails
  try {
    const { dataUrl: logoDataUrl, aspect } = await getLogoDataUrl()
    const logoH = 14 // fixed height in mm
    const maxLogoW = 32 // maximum width to prevent overlap
    let logoW = logoH * aspect
    // If the logo is very wide (text-logo), cap it and shrink height proportionally
    if (logoW > maxLogoW) {
      logoW = maxLogoW
    }
    const logoX = 12
    const logoY = (42 - logoH) / 2
    doc.addImage(logoDataUrl, 'PNG', logoX, logoY, logoW, logoH)
    textStartX = logoX + logoW + 4 // position text right after logo with 4mm gap
  } catch {
    // Fallback text logo
    doc.setFontSize(18)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.white)
    doc.text('E', 22, 24)
    textStartX = 32
  }
  
  // Company name — positioned after logo
  doc.setFontSize(16)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.white)
  doc.text('Efforts Solutions', textStartX, 18)
  
  doc.setFontSize(9)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.primaryLight)
  doc.text('AI Recruiter — Candidate Assessment Report', textStartX, 26)
  
  // Date on right
  doc.setFontSize(8)
  setColor(doc, BRAND.primaryLight)
  const dateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  doc.text(dateStr, pageWidth - 14, 18, { align: 'right' })
  doc.text('CONFIDENTIAL', pageWidth - 14, 26, { align: 'right' })
  
  // Accent line under header
  drawRect(doc, 0, 42, pageWidth, 1.5, BRAND.accent)
  
  // Candidate name banner
  const bannerY = 48
  drawRect(doc, 0, bannerY, pageWidth, 22, BRAND.grayLight)
  
  doc.setFontSize(14)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.primaryDark)
  doc.text(candidate.name, 14, bannerY + 10)
  
  // Category & match score badges
  doc.setFontSize(9)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.gray)
  const infoLine = [
    candidate.jobCategory || 'General',
    candidate.jobSubcategory,
    `${candidate.experience} yrs experience`,
    candidate.location,
  ].filter(Boolean).join('  |  ')
  doc.text(infoLine, 14, bannerY + 17)
  
  // Match Score badge on right
  const score = candidate.matchScore ?? 50
  const scoreColor = score >= 70 ? BRAND.green : score >= 50 ? BRAND.amber : BRAND.red
  drawRect(doc, pageWidth - 50, bannerY + 3, 36, 16, scoreColor, 3)
  doc.setFontSize(11)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.white)
  doc.text(`${score.toFixed(0)}%`, pageWidth - 32, bannerY + 13, { align: 'center' })
  
  return bannerY + 26 // return Y position after header
}

/**
 * Draw the branded footer
 */
function drawFooter(doc: jsPDF, pageWidth: number, pageHeight: number, pageNum: number) {
  const footerY = pageHeight - 12
  drawLine(doc, 14, footerY - 3, pageWidth - 14, footerY - 3, BRAND.primaryLight, 0.3)
  
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.gray)
  doc.text('Efforts Solutions  |  www.effortz.com  |  Confidential AI Assessment Report', 14, footerY)
  doc.text(`Page ${pageNum}`, pageWidth - 14, footerY, { align: 'right' })
}

/**
 * Section heading with accent line
 */
function drawSectionHeading(doc: jsPDF, title: string, y: number, pageWidth: number): number {
  drawRect(doc, 14, y, 3, 8, BRAND.primary)
  doc.setFontSize(11)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.primaryDark)
  doc.text(title, 20, y + 6)
  drawLine(doc, 20, y + 9, pageWidth - 14, y + 9, BRAND.primaryLight, 0.3)
  return y + 14
}

/**
 * Check if we need a new page, and if so, add one with footer on previous
 */
function checkPageBreak(doc: jsPDF, currentY: number, neededSpace: number, pageWidth: number, pageHeight: number, pageNum: { value: number }): number {
  if (currentY + neededSpace > pageHeight - 20) {
    drawFooter(doc, pageWidth, pageHeight, pageNum.value)
    doc.addPage()
    pageNum.value++
    return 16 // top margin on new page
  }
  return currentY
}

/**
 * Write wrapped text and return new Y position
 */
function writeWrappedText(doc: jsPDF, text: string, x: number, y: number, maxWidth: number, fontSize: number, color: [number, number, number], lineHeight = 4.5): number {
  doc.setFontSize(fontSize)
  doc.setFont('helvetica', 'normal')
  setColor(doc, color)
  const lines = doc.splitTextToSize(text, maxWidth)
  doc.text(lines, x, y)
  return y + (lines.length * lineHeight)
}

/**
 * Main PDF generation function — Compact 1-2 page layout
 * AI Summary first, then Candidate Profile flows naturally
 */
export async function generateCandidatePDF(
  candidate: CandidateData,
  aiAnalysis?: AIAnalysisData | null
): Promise<void> {
  const doc = new jsPDF('p', 'mm', 'a4')
  const pageWidth = doc.internal.pageSize.getWidth()  // 210
  const pageHeight = doc.internal.pageSize.getHeight() // 297
  const margin = 14
  const contentWidth = pageWidth - margin * 2
  const pageNum = { value: 1 }
  
  // =========================================================================
  // HEADER — Compact branded header with candidate info
  // =========================================================================
  let y = await drawHeader(doc, candidate, pageWidth)
  y += 3

  // =========================================================================
  // SECTION 1 — AI ASSESSMENT (compact)
  // =========================================================================

  // --- Rating, Recommendation & Confidence in a single compact row ---
  if (aiAnalysis?.overall_rating || aiAnalysis?.hiring_recommendation) {
    const boxY = y
    
    if (aiAnalysis.overall_rating) {
      drawRect(doc, margin, boxY, 35, 12, BRAND.primaryLight, 3)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.gray)
      doc.text('RATING', margin + 17.5, boxY + 4, { align: 'center' })
      doc.setFontSize(11)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.primaryDark)
      doc.text(aiAnalysis.overall_rating, margin + 17.5, boxY + 10, { align: 'center' })
    }
    
    if (aiAnalysis.hiring_recommendation) {
      const rec = aiAnalysis.hiring_recommendation.replace(/_/g, ' ')
      const recColor = rec.includes('STRONGLY') || rec.includes('RECOMMEND') ? BRAND.green : rec.includes('CONSIDER') ? BRAND.amber : BRAND.red
      drawRect(doc, margin + 38, boxY, 55, 12, recColor, 3)
      doc.setFontSize(7)
      setColor(doc, BRAND.white)
      doc.setFont('helvetica', 'normal')
      doc.text('RECOMMENDATION', margin + 65.5, boxY + 4, { align: 'center' })
      doc.setFontSize(9)
      doc.setFont('helvetica', 'bold')
      doc.text(rec, margin + 65.5, boxY + 10, { align: 'center' })
    }
    
    if (aiAnalysis.confidence_score) {
      drawRect(doc, margin + 96, boxY, 32, 12, BRAND.grayLight, 3)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.gray)
      doc.text('CONFIDENCE', margin + 112, boxY + 4, { align: 'center' })
      doc.setFontSize(11)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.primaryDark)
      doc.text(`${aiAnalysis.confidence_score}%`, margin + 112, boxY + 10, { align: 'center' })
    }

    // Ideal Roles on right side
    if (aiAnalysis?.ideal_roles && aiAnalysis.ideal_roles.length > 0) {
      doc.setFontSize(7)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.gray)
      doc.text('IDEAL ROLES', margin + 134, boxY + 4)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.black)
      const rolesText = aiAnalysis.ideal_roles.slice(0, 3).join(', ')
      const roleLines = doc.splitTextToSize(rolesText, 46)
      doc.text(roleLines, margin + 134, boxY + 8)
    }

    y = boxY + 15
  }

  // --- AI EXECUTIVE SUMMARY ---
  if (aiAnalysis?.executive_summary) {
    y = drawSectionHeading(doc, 'AI Executive Summary', y, pageWidth)
    y = writeWrappedText(doc, aiAnalysis.executive_summary, margin, y, contentWidth, 8.5, BRAND.black, 3.8)
    y += 3
  }

  // --- PROS & CONS (compact two columns) ---
  if ((aiAnalysis?.pros && aiAnalysis.pros.length > 0) || (aiAnalysis?.cons && aiAnalysis.cons.length > 0)) {
    const maxItems = Math.max(aiAnalysis?.pros?.length || 0, aiAnalysis?.cons?.length || 0)
    const prosConsHeight = maxItems * 4 + 10
    y = checkPageBreak(doc, y, prosConsHeight, pageWidth, pageHeight, pageNum)
    
    const colWidth = (contentWidth - 4) / 2

    // Strengths
    if (aiAnalysis?.pros && aiAnalysis.pros.length > 0) {
      drawRect(doc, margin, y, colWidth, 6, [220, 252, 231], 2)
      doc.setFontSize(8)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.green)
      doc.text('+ Strengths', margin + 3, y + 4)
      
      let prosY = y + 9
      aiAnalysis.pros.slice(0, 5).forEach((pro) => {
        doc.setFontSize(7.5)
        doc.setFont('helvetica', 'normal')
        setColor(doc, BRAND.black)
        const lines = doc.splitTextToSize(`• ${pro}`, colWidth - 6)
        doc.text(lines, margin + 3, prosY)
        prosY += lines.length * 3.5
      })
    }
    
    // Areas for Improvement
    if (aiAnalysis?.cons && aiAnalysis.cons.length > 0) {
      const consX = margin + colWidth + 4
      drawRect(doc, consX, y, colWidth, 6, [254, 226, 226], 2)
      doc.setFontSize(8)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.red)
      doc.text('- Areas for Improvement', consX + 3, y + 4)
      
      let consY = y + 9
      aiAnalysis.cons.slice(0, 5).forEach((con) => {
        doc.setFontSize(7.5)
        doc.setFont('helvetica', 'normal')
        setColor(doc, BRAND.black)
        const lines = doc.splitTextToSize(`• ${con}`, colWidth - 6)
        doc.text(lines, consX + 3, consY)
        consY += lines.length * 3.5
      })
    }
    
    y += 9 + Math.min(maxItems, 5) * 4.5
    y += 3
  }

  // --- TECHNICAL + EXPERIENCE ASSESSMENT (combined, compact) ---
  if (aiAnalysis?.technical_assessment || aiAnalysis?.experience_assessment) {
    y = checkPageBreak(doc, y, 18, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Technical & Experience Assessment', y, pageWidth)
    if (aiAnalysis.technical_assessment) {
      const techText = aiAnalysis.technical_assessment.length > 300 ? aiAnalysis.technical_assessment.substring(0, 300) + '...' : aiAnalysis.technical_assessment
      y = writeWrappedText(doc, techText, margin, y, contentWidth, 8, BRAND.black, 3.6)
      y += 2
    }
    if (aiAnalysis.experience_assessment) {
      const expText = aiAnalysis.experience_assessment.length > 300 ? aiAnalysis.experience_assessment.substring(0, 300) + '...' : aiAnalysis.experience_assessment
      y = writeWrappedText(doc, expText, margin, y, contentWidth, 8, BRAND.black, 3.6)
      y += 2
    }
    y += 2
  }

  // --- HIRING RECOMMENDATION RATIONALE (compact) ---
  if (aiAnalysis?.hiring_recommendation_rationale) {
    y = checkPageBreak(doc, y, 14, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Hiring Recommendation', y, pageWidth)
    const ratText = aiAnalysis.hiring_recommendation_rationale.length > 250 ? aiAnalysis.hiring_recommendation_rationale.substring(0, 250) + '...' : aiAnalysis.hiring_recommendation_rationale
    y = writeWrappedText(doc, ratText, margin, y, contentWidth, 8, BRAND.black, 3.6)
    y += 3
  }

  // --- INTERVIEW FOCUS AREAS (inline, compact) ---
  if (aiAnalysis?.interview_focus_areas && aiAnalysis.interview_focus_areas.length > 0) {
    y = checkPageBreak(doc, y, 10, pageWidth, pageHeight, pageNum)
    doc.setFontSize(8)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.primaryDark)
    doc.text('Interview Focus:', margin, y + 3)
    doc.setFont('helvetica', 'normal')
    setColor(doc, BRAND.black)
    const focusText = aiAnalysis.interview_focus_areas.slice(0, 5).join('  •  ')
    const focusLines = doc.splitTextToSize(focusText, contentWidth - 30)
    doc.text(focusLines, margin + 28, y + 3)
    y += focusLines.length * 3.5 + 5
  }

  // =========================================================================
  // DIVIDER — Thin accent line between AI and Profile sections
  // =========================================================================
  y = checkPageBreak(doc, y, 6, pageWidth, pageHeight, pageNum)
  drawLine(doc, margin, y, pageWidth - margin, y, BRAND.accent, 0.8)
  y += 4

  // =========================================================================
  // SECTION 2 — CANDIDATE PROFILE (flows naturally, no forced page break)
  // =========================================================================

  // --- CONTACT INFORMATION (compact inline) ---
  y = checkPageBreak(doc, y, 20, pageWidth, pageHeight, pageNum)
  y = drawSectionHeading(doc, 'Contact & Profile', y, pageWidth)
  
  const contactPairs = [
    ['Email', candidate.email],
    ['Phone', candidate.phone || 'N/A'],
    ['Location', candidate.location],
    ['LinkedIn', candidate.linkedin || 'N/A'],
    ['Experience', `${candidate.experience} years`],
    ['Category', candidate.jobCategory || 'General'],
  ]
  
  const colW = contentWidth / 2
  contactPairs.forEach((info, idx) => {
    const col = idx % 2
    const row = Math.floor(idx / 2)
    const xPos = margin + col * colW
    const yPos = y + row * 5
    
    doc.setFontSize(7.5)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.gray)
    doc.text(`${info[0]}:`, xPos, yPos)
    doc.setFont('helvetica', 'normal')
    setColor(doc, BRAND.black)
    doc.text(info[1], xPos + 20, yPos)
  })
  y += Math.ceil(contactPairs.length / 2) * 5 + 3

  // --- PROFESSIONAL SUMMARY (compact) ---
  if (candidate.summary) {
    y = checkPageBreak(doc, y, 14, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Professional Summary', y, pageWidth)
    const sumText = candidate.summary.length > 350 ? candidate.summary.substring(0, 350) + '...' : candidate.summary
    y = writeWrappedText(doc, sumText, margin, y, contentWidth, 8, BRAND.black, 3.6)
    y += 3
  }

  // --- SKILLS (compact badges) ---
  if (candidate.skills && candidate.skills.length > 0) {
    y = checkPageBreak(doc, y, 14, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Skills & Expertise', y, pageWidth)
    
    let badgeX = margin
    const badgeH = 5
    candidate.skills.slice(0, 20).forEach((skill) => {
      const textWidth = doc.getTextWidth(skill) + 3
      const badgeW = Math.max(textWidth, 10)
      
      if (badgeX + badgeW > pageWidth - margin) {
        badgeX = margin
        y += badgeH + 1.5
        y = checkPageBreak(doc, y, badgeH + 3, pageWidth, pageHeight, pageNum)
      }
      
      drawRect(doc, badgeX, y - 1, badgeW, badgeH, BRAND.primaryLight, 2)
      doc.setFontSize(6.5)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.primaryDark)
      doc.text(skill, badgeX + 1.5, y + 2.5)
      badgeX += badgeW + 1.5
    })
    y += badgeH + 4
  }

  // --- WORK EXPERIENCE (compact — limit to 3 most recent) ---
  if (candidate.workHistory && candidate.workHistory.length > 0) {
    y = checkPageBreak(doc, y, 16, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Work Experience', y, pageWidth)
    
    candidate.workHistory.slice(0, 3).forEach((job) => {
      y = checkPageBreak(doc, y, 12, pageWidth, pageHeight, pageNum)
      
      drawRect(doc, margin, y, 2, 2, BRAND.primary, 1)
      
      doc.setFontSize(8)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.black)
      doc.text(job.title, margin + 4, y + 1.5)
      
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.gray)
      const companyLine = [job.company, job.duration].filter(Boolean).join('  ·  ')
      doc.text(companyLine, margin + 4, y + 5.5)
      y += 8
      
      if (job.description) {
        const desc = job.description.length > 150 ? job.description.substring(0, 150) + '...' : job.description
        y = writeWrappedText(doc, desc, margin + 4, y, contentWidth - 4, 7, BRAND.black, 3.2)
        y += 1
      }
      y += 2
    })
    if (candidate.workHistory.length > 3) {
      doc.setFontSize(7)
      doc.setFont('helvetica', 'italic')
      setColor(doc, BRAND.gray)
      doc.text(`+ ${candidate.workHistory.length - 3} more positions on file`, margin + 4, y)
      y += 5
    }
  }

  // --- EDUCATION (compact) ---
  if (candidate.education && candidate.education.length > 0) {
    y = checkPageBreak(doc, y, 12, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Education', y, pageWidth)
    
    candidate.education.slice(0, 3).forEach((edu) => {
      y = checkPageBreak(doc, y, 8, pageWidth, pageHeight, pageNum)
      
      doc.setFontSize(8)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.black)
      doc.text(`${edu.degree}${edu.field ? ` in ${edu.field}` : ''}`, margin, y)
      
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.gray)
      doc.text(`${edu.institution || 'N/A'}${edu.year ? `  ·  ${edu.year}` : ''}`, margin, y + 4)
      y += 8
    })
    y += 2
  }

  // --- CERTIFICATIONS & LANGUAGES (combined, single line each) ---
  if ((candidate.certifications && candidate.certifications.length > 0) || (candidate.languages && candidate.languages.length > 0)) {
    y = checkPageBreak(doc, y, 10, pageWidth, pageHeight, pageNum)
    
    if (candidate.certifications && candidate.certifications.length > 0) {
      doc.setFontSize(7.5)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.primaryDark)
      doc.text('Certifications:', margin, y)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.black)
      const certText = candidate.certifications.slice(0, 4).join('  |  ')
      doc.text(certText, margin + 24, y)
      y += 4.5
    }
    
    if (candidate.languages && candidate.languages.length > 0) {
      doc.setFontSize(7.5)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.primaryDark)
      doc.text('Languages:', margin, y)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.black)
      doc.text(candidate.languages.join('  |  '), margin + 24, y)
      y += 4.5
    }
  }

  // --- SALARY ESTIMATE (if available) ---
  if (aiAnalysis?.salary_range_estimate) {
    y = checkPageBreak(doc, y, 6, pageWidth, pageHeight, pageNum)
    doc.setFontSize(7.5)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.primaryDark)
    doc.text('Est. Salary Range:', margin, y)
    doc.setFont('helvetica', 'normal')
    setColor(doc, BRAND.black)
    doc.text(aiAnalysis.salary_range_estimate, margin + 32, y)
    y += 5
  }

  // Final footer
  drawFooter(doc, pageWidth, pageHeight, pageNum.value)
  
  // Save
  const safeName = candidate.name.replace(/[^a-zA-Z0-9\s]/g, '').replace(/\s+/g, '_')
  doc.save(`Efforts_Solutions_AI_Assessment_${safeName}.pdf`)
}

/**
 * Quick PDF without AI analysis — just candidate profile
 */
export async function generateQuickProfilePDF(candidate: CandidateData): Promise<void> {
  return generateCandidatePDF(candidate, null)
}
