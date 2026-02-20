/**
 * AI Recruiter — Branded PDF Generator
 * Generates professional candidate assessment PDFs with AI summary + profile
 * Blue gradient candidate header design with circular score indicator
 */

import { jsPDF } from 'jspdf'
import { useAuthStore } from '@/store/authStore'
import { config } from '@/config'

// AI Recruiter brand colors
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
  id?: string
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
  resumeText?: string
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
  source?: string
  isFallback?: boolean
}

// Cache for logo image data URL + natural dimensions
let cachedLogoDataUrl: string | null = null
let cachedLogoAspect: number = 1

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
      const canvas = document.createElement('canvas')
      canvas.width = 120
      canvas.height = 120
      const ctx = canvas.getContext('2d')!
      ctx.fillStyle = '#1d4ed8'
      ctx.beginPath()
      ctx.roundRect(0, 0, 120, 120, 20)
      ctx.fill()
      ctx.fillStyle = '#ffffff'
      ctx.font = 'bold 60px Arial'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText('AI', 60, 65)
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
 * Derive consistent rating/recommendation from matchScore when AI analysis is fallback
 */
function deriveRatingFromScore(score: number): { rating: string; recommendation: string; confidence: number } {
  if (score >= 85) return { rating: 'A', recommendation: 'STRONGLY_RECOMMEND', confidence: 85 }
  if (score >= 75) return { rating: 'A-', recommendation: 'RECOMMEND', confidence: 78 }
  if (score >= 65) return { rating: 'B+', recommendation: 'RECOMMEND', confidence: 72 }
  if (score >= 55) return { rating: 'B', recommendation: 'CONSIDER', confidence: 65 }
  if (score >= 45) return { rating: 'B-', recommendation: 'CONSIDER', confidence: 58 }
  if (score >= 35) return { rating: 'C+', recommendation: 'REVIEW', confidence: 50 }
  if (score >= 25) return { rating: 'C', recommendation: 'REVIEW', confidence: 42 }
  return { rating: 'C-', recommendation: 'NOT_RECOMMENDED', confidence: 35 }
}

/**
 * Draw compact navy top bar with logo + company name (reduced height)
 */
async function drawTopBar(doc: jsPDF, pageWidth: number): Promise<number> {
  const barH = 14
  drawRect(doc, 0, 0, pageWidth, barH, BRAND.primaryDark)
  
  let textStartX = 24
  try {
    const { dataUrl: logoDataUrl, aspect } = await getLogoDataUrl()
    const logoH = 8
    // Properly preserve aspect ratio — calculate width from height
    let logoW = logoH * aspect
    // Cap max width to prevent stretching
    if (logoW > 20) {
      logoW = 20
    }
    const logoX = 8
    const logoY = (barH - logoH) / 2
    doc.addImage(logoDataUrl, 'PNG', logoX, logoY, logoW, logoH)
    textStartX = logoX + logoW + 3
  } catch {
    textStartX = 10
  }
  
  doc.setFontSize(11)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.white)
  doc.text('AI Recruiter', textStartX, barH / 2 - 1)
  
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.primaryLight)
  doc.text('Smart Hiring Platform', textStartX, barH / 2 + 4)
  
  doc.setFontSize(7)
  setColor(doc, BRAND.primaryLight)
  const dateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  doc.text(dateStr, pageWidth - 10, barH / 2 - 1, { align: 'right' })
  doc.text('CONFIDENTIAL', pageWidth - 10, barH / 2 + 4, { align: 'right' })
  
  drawRect(doc, 0, barH, pageWidth, 1, BRAND.accent)
  
  return barH + 1
}

/**
 * Draw blue gradient candidate header with circular score indicator
 */
function drawCandidateHeader(doc: jsPDF, candidate: CandidateData, y: number, pageWidth: number): number {
  const headerH = 28
  const margin = 14
  
  // Blue gradient background (simulate with overlapping rects)
  drawRect(doc, 0, y, pageWidth, headerH, BRAND.primary)
  // Lighter overlay on right for gradient effect
  doc.setFillColor(59, 130, 246)
  doc.rect(pageWidth * 0.55, y, pageWidth * 0.45, headerH, 'F')
  
  // Candidate name
  doc.setFontSize(16)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.white)
  doc.text(candidate.name, margin, y + 9)
  
  // Job title / category
  const titleLine = candidate.jobSubcategory || candidate.jobCategory || 'Professional'
  doc.setFontSize(9)
  doc.setFont('helvetica', 'normal')
  doc.setTextColor(200, 220, 255)
  doc.text(titleLine, margin, y + 15)
  
  // Contact info row
  doc.setFontSize(7)
  doc.setTextColor(180, 200, 240)
  const contactItems: string[] = []
  if (candidate.location && candidate.location !== 'Not Specified' && candidate.location !== 'Unknown') {
    contactItems.push(candidate.location)
  }
  if (candidate.email) contactItems.push(candidate.email)
  if (candidate.phone) contactItems.push(candidate.phone)
  if (candidate.experience > 0) contactItems.push(`${candidate.experience} yrs exp`)
  doc.text(contactItems.join('  |  '), margin, y + 20)
  
  // Category badge at bottom
  if (candidate.jobCategory) {
    const catText = candidate.jobCategory
    const catW = doc.getTextWidth(catText) + 6
    drawRect(doc, margin, y + 22, catW, 4.5, [50, 100, 220] as [number, number, number], 2)
    doc.setFontSize(6.5)
    setColor(doc, BRAND.white)
    doc.text(catText, margin + 3, y + 25.2)
  }
  
  // Score circle on right
  const score = candidate.matchScore ?? 50
  const circleX = pageWidth - 26
  const circleY = y + headerH / 2
  const circleR = 10
  
  // White circle
  doc.setFillColor(255, 255, 255)
  doc.circle(circleX, circleY, circleR, 'F')
  
  // Score number
  const scoreColor = score >= 70 ? BRAND.green : score >= 50 ? BRAND.amber : BRAND.red
  doc.setFontSize(14)
  doc.setFont('helvetica', 'bold')
  setColor(doc, scoreColor)
  doc.text(`${score.toFixed(0)}%`, circleX, circleY + 1, { align: 'center' })
  
  // "MATCH" label
  doc.setFontSize(5)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.gray)
  doc.text('MATCH', circleX, circleY + 5.5, { align: 'center' })
  
  return y + headerH + 2
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
  doc.text('AI Recruiter  |  Smart Hiring Platform  |  Confidential Assessment Report', 14, footerY)
  doc.text(`Page ${pageNum}`, pageWidth - 14, footerY, { align: 'right' })
}

function drawSectionHeading(doc: jsPDF, title: string, y: number, pageWidth: number): number {
  drawRect(doc, 14, y, 3, 8, BRAND.primary)
  doc.setFontSize(11)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.primaryDark)
  doc.text(title, 20, y + 6)
  drawLine(doc, 20, y + 9, pageWidth - 14, y + 9, BRAND.primaryLight, 0.3)
  return y + 14
}

function checkPageBreak(doc: jsPDF, currentY: number, neededSpace: number, pageWidth: number, pageHeight: number, pageNum: { value: number }): number {
  if (currentY + neededSpace > pageHeight - 20) {
    drawFooter(doc, pageWidth, pageHeight, pageNum.value)
    doc.addPage()
    pageNum.value++
    return 16
  }
  return currentY
}

function writeWrappedText(doc: jsPDF, text: string, x: number, y: number, maxWidth: number, fontSize: number, color: [number, number, number], lineHeight = 4.5): number {
  doc.setFontSize(fontSize)
  doc.setFont('helvetica', 'normal')
  setColor(doc, color)
  const lines = doc.splitTextToSize(text, maxWidth)
  doc.text(lines, x, y)
  return y + (lines.length * lineHeight)
}

/**
 * Main PDF generation — AI Summary page, then Original CV on subsequent pages
 */
export async function generateCandidatePDF(
  candidate: CandidateData,
  aiAnalysis?: AIAnalysisData | null
): Promise<void> {
  const doc = new jsPDF('p', 'mm', 'a4')
  const pageWidth = doc.internal.pageSize.getWidth()
  const pageHeight = doc.internal.pageSize.getHeight()
  const margin = 14
  const contentWidth = pageWidth - margin * 2
  const pageNum = { value: 1 }
  
  // Normalize AI analysis: if fallback or grossly mismatched, derive rating/recommendation from matchScore
  let normalizedAnalysis = aiAnalysis ? { ...aiAnalysis } : null
  if (normalizedAnalysis) {
    const score = candidate.matchScore ?? 50
    const rating = normalizedAnalysis.overall_rating || ''
    const conf = normalizedAnalysis.confidence_score || 0
    // Detect fallback: explicit flag, hardcoded fallback values, or score-rating mismatch
    const isFallback = normalizedAnalysis.source === 'fallback' ||
      normalizedAnalysis.isFallback === true ||
      (rating === 'C+' && conf <= 42) ||
      (score >= 75 && (rating.startsWith('C') || rating === 'B-')) ||
      (score >= 60 && rating.startsWith('C')) ||
      (conf > 0 && conf < 45 && score >= 70)
    if (isFallback) {
      const derived = deriveRatingFromScore(candidate.matchScore ?? 50)
      normalizedAnalysis.overall_rating = derived.rating
      normalizedAnalysis.hiring_recommendation = derived.recommendation
      normalizedAnalysis.confidence_score = derived.confidence
      
      // Replace generic cons with profile-relevant observations
      const skills = candidate.skills || []
      const exp = candidate.experience || 0
      const relevantCons: string[] = []
      if (skills.length < 5) relevantCons.push('Limited skills breadth — expanding technical portfolio recommended')
      if (exp < 3) relevantCons.push('Early career stage — may need mentorship and onboarding support')
      if (!candidate.linkedin) relevantCons.push('No LinkedIn profile provided for background verification')
      if (!candidate.education || candidate.education.length === 0) relevantCons.push('Education details not specified — verification recommended')
      if (!candidate.workHistory || candidate.workHistory.length === 0) relevantCons.push('Work history not detailed — explore experience depth in interview')
      if (relevantCons.length === 0) relevantCons.push('Profile appears strong — detailed AI analysis recommended for deeper insights')
      normalizedAnalysis.cons = relevantCons
    }
  }
  
  // ===== TOP BAR =====
  let y = await drawTopBar(doc, pageWidth)
  
  // ===== CANDIDATE HEADER — Blue gradient =====
  y = drawCandidateHeader(doc, candidate, y, pageWidth)
  y += 2

  // ===== SECTION 1 — AI ASSESSMENT =====

  // Rating, Recommendation & Confidence row
  if (normalizedAnalysis?.overall_rating || normalizedAnalysis?.hiring_recommendation) {
    const boxY = y
    
    if (normalizedAnalysis.overall_rating) {
      drawRect(doc, margin, boxY, 35, 12, BRAND.primaryLight, 3)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.gray)
      doc.text('RATING', margin + 17.5, boxY + 4, { align: 'center' })
      doc.setFontSize(11)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.primaryDark)
      doc.text(normalizedAnalysis.overall_rating, margin + 17.5, boxY + 10, { align: 'center' })
    }
    
    if (normalizedAnalysis.hiring_recommendation) {
      const rec = normalizedAnalysis.hiring_recommendation.replace(/_/g, ' ')
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
    
    if (normalizedAnalysis.confidence_score) {
      drawRect(doc, margin + 96, boxY, 32, 12, BRAND.grayLight, 3)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.gray)
      doc.text('CONFIDENCE', margin + 112, boxY + 4, { align: 'center' })
      doc.setFontSize(11)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.primaryDark)
      doc.text(`${normalizedAnalysis.confidence_score}%`, margin + 112, boxY + 10, { align: 'center' })
    }

    if (normalizedAnalysis?.ideal_roles && normalizedAnalysis.ideal_roles.length > 0) {
      doc.setFontSize(7)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.gray)
      doc.text('IDEAL ROLES', margin + 134, boxY + 4)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.black)
      const rolesText = normalizedAnalysis.ideal_roles.slice(0, 3).join(', ')
      const roleLines = doc.splitTextToSize(rolesText, 46)
      doc.text(roleLines, margin + 134, boxY + 8)
    }

    y = boxY + 15
  }

  // Executive Summary
  if (normalizedAnalysis?.executive_summary) {
    y = drawSectionHeading(doc, 'AI Executive Summary', y, pageWidth)
    y = writeWrappedText(doc, normalizedAnalysis.executive_summary, margin, y, contentWidth, 8.5, BRAND.black, 3.8)
    y += 3
  }

  // Pros & Cons
  if ((normalizedAnalysis?.pros && normalizedAnalysis.pros.length > 0) || (normalizedAnalysis?.cons && normalizedAnalysis.cons.length > 0)) {
    const maxItems = Math.max(normalizedAnalysis?.pros?.length || 0, normalizedAnalysis?.cons?.length || 0)
    const prosConsHeight = maxItems * 4 + 10
    y = checkPageBreak(doc, y, prosConsHeight, pageWidth, pageHeight, pageNum)
    
    const colWidth = (contentWidth - 4) / 2

    if (normalizedAnalysis?.pros && normalizedAnalysis.pros.length > 0) {
      drawRect(doc, margin, y, colWidth, 6, [220, 252, 231] as [number, number, number], 2)
      doc.setFontSize(8)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.green)
      doc.text('+ Strengths', margin + 3, y + 4)
      
      let prosY = y + 9
      normalizedAnalysis.pros.slice(0, 5).forEach((pro) => {
        doc.setFontSize(7.5)
        doc.setFont('helvetica', 'normal')
        setColor(doc, BRAND.black)
        const lines = doc.splitTextToSize(`• ${pro}`, colWidth - 6)
        doc.text(lines, margin + 3, prosY)
        prosY += lines.length * 3.5
      })
    }
    
    if (normalizedAnalysis?.cons && normalizedAnalysis.cons.length > 0) {
      const consX = margin + colWidth + 4
      drawRect(doc, consX, y, colWidth, 6, [254, 226, 226] as [number, number, number], 2)
      doc.setFontSize(8)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.red)
      doc.text('- Areas for Improvement', consX + 3, y + 4)
      
      let consY = y + 9
      normalizedAnalysis.cons.slice(0, 5).forEach((con) => {
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

  // Technical & Experience Assessment
  if (normalizedAnalysis?.technical_assessment || normalizedAnalysis?.experience_assessment) {
    y = checkPageBreak(doc, y, 18, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Technical & Experience Assessment', y, pageWidth)
    if (normalizedAnalysis.technical_assessment) {
      const techText = normalizedAnalysis.technical_assessment.length > 300 ? normalizedAnalysis.technical_assessment.substring(0, 300) + '...' : normalizedAnalysis.technical_assessment
      y = writeWrappedText(doc, techText, margin, y, contentWidth, 8, BRAND.black, 3.6)
      y += 2
    }
    if (normalizedAnalysis.experience_assessment) {
      const expText = normalizedAnalysis.experience_assessment.length > 300 ? normalizedAnalysis.experience_assessment.substring(0, 300) + '...' : normalizedAnalysis.experience_assessment
      y = writeWrappedText(doc, expText, margin, y, contentWidth, 8, BRAND.black, 3.6)
      y += 2
    }
    y += 2
  }

  // Hiring Recommendation Rationale
  if (normalizedAnalysis?.hiring_recommendation_rationale) {
    y = checkPageBreak(doc, y, 14, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Hiring Recommendation', y, pageWidth)
    const ratText = normalizedAnalysis.hiring_recommendation_rationale.length > 250 ? normalizedAnalysis.hiring_recommendation_rationale.substring(0, 250) + '...' : normalizedAnalysis.hiring_recommendation_rationale
    y = writeWrappedText(doc, ratText, margin, y, contentWidth, 8, BRAND.black, 3.6)
    y += 3
  }

  // Interview Focus Areas
  if (normalizedAnalysis?.interview_focus_areas && normalizedAnalysis.interview_focus_areas.length > 0) {
    y = checkPageBreak(doc, y, 10, pageWidth, pageHeight, pageNum)
    doc.setFontSize(8)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.primaryDark)
    doc.text('Interview Focus:', margin, y + 3)
    doc.setFont('helvetica', 'normal')
    setColor(doc, BRAND.black)
    const focusText = normalizedAnalysis.interview_focus_areas.slice(0, 5).join('  •  ')
    const focusLines = doc.splitTextToSize(focusText, contentWidth - 30)
    doc.text(focusLines, margin + 28, y + 3)
    y += focusLines.length * 3.5 + 5
  }

  // ===== DIVIDER =====
  y = checkPageBreak(doc, y, 6, pageWidth, pageHeight, pageNum)
  drawLine(doc, margin, y, pageWidth - margin, y, BRAND.accent, 0.8)
  y += 4

  // ===== SECTION 2 — CANDIDATE PROFILE =====

  // Contact Info
  y = checkPageBreak(doc, y, 20, pageWidth, pageHeight, pageNum)
  y = drawSectionHeading(doc, 'Contact & Profile', y, pageWidth)
  
  const contactPairs: [string, string][] = [
    ['Email', candidate.email],
    ['Phone', candidate.phone || 'N/A'],
  ]
  if (candidate.location && candidate.location !== 'Not Specified' && candidate.location !== 'Unknown') {
    contactPairs.push(['Location', candidate.location])
  }
  contactPairs.push(['LinkedIn', candidate.linkedin || 'N/A'])
  contactPairs.push(['Experience', `${candidate.experience} years`])
  contactPairs.push(['Category', candidate.jobCategory || 'General'])
  
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

  // Professional Summary
  if (candidate.summary) {
    y = checkPageBreak(doc, y, 14, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Professional Summary', y, pageWidth)
    const sumText = candidate.summary.length > 350 ? candidate.summary.substring(0, 350) + '...' : candidate.summary
    y = writeWrappedText(doc, sumText, margin, y, contentWidth, 8, BRAND.black, 3.6)
    y += 3
  }

  // Skills badges
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

  // Work Experience
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

  // Education
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

  // Certifications & Languages
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

  // Salary Estimate
  if (normalizedAnalysis?.salary_range_estimate) {
    y = checkPageBreak(doc, y, 6, pageWidth, pageHeight, pageNum)
    doc.setFontSize(7.5)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.primaryDark)
    doc.text('Est. Salary Range:', margin, y)
    doc.setFont('helvetica', 'normal')
    setColor(doc, BRAND.black)
    doc.text(normalizedAnalysis.salary_range_estimate, margin + 32, y)
    y += 5
  }

  // ===== SECTION 3 — ORIGINAL CV / RESUME TEXT =====
  // Sanitize resume text: detect and skip garbled/mojibake content
  let cleanResumeText = candidate.resumeText?.trim() || ''
  // Use simple string-based mojibake detection (regex character classes cause build issues with non-ASCII ranges)
  const mojibakeMarkers = ['Ã\u0082', 'Ã\u0083', 'Ã\u00A9', 'Ã\u00A8', 'Ã\u00BC', 'Ã\u00B6', '\u00C3\u0082', '\u00C3\u0083', '\u00C2\u00A0']
  let mojibakeHits = 0
  for (const marker of mojibakeMarkers) {
    let idx = -1
    let startPos = 0
    while ((idx = cleanResumeText.indexOf(marker, startPos)) !== -1) {
      mojibakeHits++
      startPos = idx + marker.length
    }
  }
  const isMojibake = mojibakeHits > 5 && (mojibakeHits * 3) / cleanResumeText.length > 0.05

  if (cleanResumeText.length > 20 && !isMojibake) {
    // Footer on current page, then new page for CV
    drawFooter(doc, pageWidth, pageHeight, pageNum.value)
    doc.addPage()
    pageNum.value++
    
    // CV header bar
    drawRect(doc, 0, 0, pageWidth, 10, BRAND.primaryDark)
    doc.setFontSize(9)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.white)
    doc.text('Original CV / Resume', margin, 7)
    doc.setFontSize(7)
    doc.setFont('helvetica', 'normal')
    setColor(doc, BRAND.primaryLight)
    doc.text(candidate.name, pageWidth - 14, 7, { align: 'right' })
    drawRect(doc, 0, 10, pageWidth, 0.5, BRAND.accent)
    y = 16
    
    // Write resume text with page breaks
    doc.setFontSize(8)
    doc.setFont('helvetica', 'normal')
    setColor(doc, BRAND.black)
    
    const resumeLines = doc.splitTextToSize(cleanResumeText, contentWidth)
    const lineH = 3.5
    
    for (let i = 0; i < resumeLines.length; i++) {
      if (y + lineH > pageHeight - 20) {
        drawFooter(doc, pageWidth, pageHeight, pageNum.value)
        doc.addPage()
        pageNum.value++
        
        // Continuation header
        drawRect(doc, 0, 0, pageWidth, 8, BRAND.primaryDark)
        doc.setFontSize(7)
        doc.setFont('helvetica', 'bold')
        setColor(doc, BRAND.white)
        doc.text('Original CV / Resume (continued)', margin, 5.5)
        doc.setFont('helvetica', 'normal')
        setColor(doc, BRAND.primaryLight)
        doc.text(candidate.name, pageWidth - 14, 5.5, { align: 'right' })
        drawRect(doc, 0, 8, pageWidth, 0.5, BRAND.accent)
        y = 14
        
        doc.setFontSize(8)
        doc.setFont('helvetica', 'normal')
        setColor(doc, BRAND.black)
      }
      doc.text(resumeLines[i], margin, y)
      y += lineH
    }
  }

  // Final footer
  drawFooter(doc, pageWidth, pageHeight, pageNum.value)
  
  const safeName = candidate.name.replace(/[^a-zA-Z0-9\s]/g, '').replace(/\s+/g, '_')
  doc.save(`AI_Recruiter_Assessment_${safeName}.pdf`)
}

/**
 * Quick PDF without AI analysis — fetches full candidate data first if light version detected
 */
export async function generateQuickProfilePDF(candidate: CandidateData): Promise<void> {
  // If candidate has no education/workHistory (light version from list), fetch full data first
  const isLight = (!candidate.education || candidate.education.length === 0) &&
                  (!candidate.workHistory || candidate.workHistory.length === 0) &&
                  !candidate.summary
  if (isLight && candidate.id) {
    try {
      const token = useAuthStore.getState().token
      const res = await fetch(`${config.endpoints.candidates}/${candidate.id}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (res.ok) {
        const full = await res.json()
        const enriched: CandidateData = {
          ...candidate,
          summary: full.summary || candidate.summary || '',
          education: full.education || [],
          workHistory: (full.workHistory || []).map((j: any) => ({
            title: j.title || j.position || '',
            company: j.company || j.organization || '',
            duration: j.duration || j.period || '',
            description: j.description || j.responsibilities || '',
          })),
          resumeText: full.resume_text || full.resumeText || '',
          certifications: full.certifications || [],
          languages: full.languages || [],
          linkedin: full.linkedin || candidate.linkedin || '',
        }
        return generateCandidatePDF(enriched, full.ai_analysis || null)
      }
    } catch (err) {
      console.error('Failed to fetch full data for PDF:', err)
    }
  }
  return generateCandidatePDF(candidate, null)
}
