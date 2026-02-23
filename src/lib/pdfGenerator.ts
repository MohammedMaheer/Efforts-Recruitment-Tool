/**
 * AI Recruiter — Branded PDF Generator
 * Generates professional candidate assessment PDFs with AI summary + profile
 * Blue gradient candidate header design with circular score indicator
 */

import { jsPDF } from 'jspdf'
import { PDFDocument } from 'pdf-lib'
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
  hasResume?: boolean
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
  if (score >= 90) return { rating: 'A+', recommendation: 'STRONGLY_RECOMMEND', confidence: 92 }
  if (score >= 80) return { rating: 'A', recommendation: 'STRONGLY_RECOMMEND', confidence: 85 }
  if (score >= 70) return { rating: 'A-', recommendation: 'RECOMMEND', confidence: 78 }
  if (score >= 60) return { rating: 'B+', recommendation: 'RECOMMEND', confidence: 72 }
  if (score >= 50) return { rating: 'B', recommendation: 'CONSIDER', confidence: 65 }
  if (score >= 40) return { rating: 'B-', recommendation: 'CONSIDER', confidence: 58 }
  if (score >= 30) return { rating: 'C+', recommendation: 'REVIEW', confidence: 50 }
  if (score >= 20) return { rating: 'C', recommendation: 'REVIEW', confidence: 42 }
  return { rating: 'C-', recommendation: 'NOT_RECOMMENDED', confidence: 35 }
}

/**
 * Draw clean white top bar with logo + company name (matching AI Summary design)
 */
async function drawTopBar(doc: jsPDF, pageWidth: number): Promise<number> {
  const barH = 18
  // White background
  drawRect(doc, 0, 0, pageWidth, barH, BRAND.white)
  
  let textStartX = 28
  try {
    const { dataUrl: logoDataUrl, aspect } = await getLogoDataUrl()
    const logoH = 12
    let logoW = logoH * aspect
    if (logoW > 28) logoW = 28
    const logoX = 10
    const logoY = (barH - logoH) / 2
    doc.addImage(logoDataUrl, 'PNG', logoX, logoY, logoW, logoH)
    textStartX = logoX + logoW + 4
  } catch {
    textStartX = 12
  }
  
  doc.setFontSize(13)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.primaryDark)
  doc.text('AI Recruiter', textStartX, barH / 2 + 0.5)
  
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.gray)
  doc.text('Smart Hiring Platform', textStartX, barH / 2 + 5)
  
  doc.setFontSize(8)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.primaryDark)
  doc.text('AI SUMMARY', pageWidth - 14, barH / 2 - 2, { align: 'right' })
  
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.gray)
  const dateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  doc.text(dateStr, pageWidth - 14, barH / 2 + 3, { align: 'right' })
  
  // Accent line at bottom of header
  drawRect(doc, 0, barH - 1, pageWidth, 1, BRAND.accent)
  
  return barH
}

/**
 * Draw white-background candidate header with score circle and clean layout
 */
function drawCandidateHeader(doc: jsPDF, candidate: CandidateData, y: number, pageWidth: number): number {
  const headerH = 32
  const margin = 14
  
  // White background
  drawRect(doc, 0, y, pageWidth, headerH, BRAND.white)
  // Subtle bottom border
  drawLine(doc, margin, y + headerH - 1, pageWidth - margin, y + headerH - 1, BRAND.primaryLight, 0.5)
  
  // Score circle on right
  const score = candidate.matchScore ?? 50
  const circleX = pageWidth - 28
  const circleY = y + headerH / 2 - 1
  const circleR = 12
  
  // Colored ring background
  const scoreColor = score >= 70 ? BRAND.green : score >= 50 ? BRAND.amber : BRAND.red
  doc.setFillColor(scoreColor[0], scoreColor[1], scoreColor[2])
  doc.circle(circleX, circleY, circleR, 'F')
  // White inner circle
  doc.setFillColor(255, 255, 255)
  doc.circle(circleX, circleY, circleR - 2, 'F')
  
  // Score number
  doc.setFontSize(15)
  doc.setFont('helvetica', 'bold')
  setColor(doc, scoreColor)
  doc.text(`${score.toFixed(0)}%`, circleX, circleY + 1.5, { align: 'center' })
  
  // "MATCH" label
  doc.setFontSize(5)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.gray)
  doc.text('MATCH', circleX, circleY + 6, { align: 'center' })
  
  // Candidate name (large, dark)
  doc.setFontSize(18)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.primaryDark)
  doc.text(candidate.name, margin, y + 10)
  
  // Job title / category
  const titleLine = candidate.jobSubcategory || candidate.jobCategory || 'Professional'
  doc.setFontSize(10)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.accent)
  doc.text(titleLine, margin, y + 17)
  
  // Contact info row
  doc.setFontSize(7.5)
  setColor(doc, BRAND.gray)
  const contactItems: string[] = []
  if (candidate.location && candidate.location !== 'Not Specified' && candidate.location !== 'Unknown') {
    contactItems.push(candidate.location)
  }
  if (candidate.email) contactItems.push(candidate.email)
  if (candidate.phone) contactItems.push(candidate.phone)
  if (candidate.experience > 0) contactItems.push(`${candidate.experience} yrs exp`)
  doc.text(contactItems.join('  |  '), margin, y + 23)
  
  // Category badge
  if (candidate.jobCategory) {
    const catText = candidate.jobCategory
    doc.setFontSize(6.5)
    const catW = doc.getTextWidth(catText) + 6
    drawRect(doc, margin, y + 26, catW, 4.5, BRAND.primaryLight, 2)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.primaryDark)
    doc.text(catText, margin + 3, y + 29.2)
  }
  
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

function writeWrappedText(doc: jsPDF, text: string, x: number, y: number, maxWidth: number, fontSize: number, color: [number, number, number], lineHeight = 4.5, pageWidth?: number, pageHeight?: number, pageNum?: { value: number }): number {
  doc.setFontSize(fontSize)
  doc.setFont('helvetica', 'normal')
  setColor(doc, color)
  const lines = doc.splitTextToSize(text, maxWidth)
  // If page dimensions provided, handle page breaks per line
  if (pageWidth && pageHeight && pageNum) {
    for (const line of lines) {
      if (y + lineHeight > pageHeight - 20) {
        drawFooter(doc, pageWidth, pageHeight, pageNum.value)
        doc.addPage()
        pageNum.value++
        y = 16
        doc.setFontSize(fontSize)
        doc.setFont('helvetica', 'normal')
        setColor(doc, color)
      }
      doc.text(line, x, y)
      y += lineHeight
    }
    return y
  }
  // Fallback: write all at once (old behavior for callers that don't pass page info)
  doc.text(lines, x, y)
  return y + (lines.length * lineHeight)
}

/**
 * Main PDF generation — AI Summary page, then Original CV on subsequent pages
 */
/**
 * Detect mojibake / garbled encoding in text.
 * Returns true if text appears to be corrupted.
 */
function detectMojibake(text: string): boolean {
  if (!text || text.length < 30) return false
  // Common mojibake marker sequences (UTF-8 misinterpreted as Latin-1/CP1252)
  const markers = [
    '\u00C3\u0082', '\u00C3\u0083', '\u00C3\u00A9', '\u00C3\u00A8',
    '\u00C3\u00BC', '\u00C3\u00B6', '\u00C2\u00A0', '\u00C2\u00AE',
    '\u00C2\u00AB', '\u00C2\u00BB', '\u00C3\u00A2', '\u00C3\u0089',
    'Ã\u0082', 'Ã\u0083', 'Ã\u00A9', 'Ã\u00A8', 'Ã\u00BC', 'Ã\u00B6',
    'Ã\u00A2', 'Ã\u0089', 'Â\u00AE', 'Â\u00AB', 'Â\u00BB', 'Â\u00A0',
  ]
  let hits = 0
  for (const m of markers) {
    let pos = 0
    while ((pos = text.indexOf(m, pos)) !== -1) { hits++; pos += m.length }
  }
  // Also count runs of high-codepoint chars (U+00C0–U+00FF repeated)
  let highRun = 0
  for (let i = 0; i < Math.min(text.length, 2000); i++) {
    const c = text.charCodeAt(i)
    if (c >= 0xC0 && c <= 0xFF) highRun++
  }
  const sample = Math.min(text.length, 2000)
  // Mojibake if: many marker hits OR >15% high-codepoint chars in first 2k
  return hits >= 3 || (highRun / sample > 0.15)
}

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

  // Compact single-line assessment bar (Rating + Recommendation + Confidence inline)
  if (normalizedAnalysis?.overall_rating || normalizedAnalysis?.hiring_recommendation) {
    const barY = y
    
    // Light background strip
    drawRect(doc, margin, barY, contentWidth, 8, BRAND.grayLight, 3)
    
    let xPos = margin + 4
    
    if (normalizedAnalysis.overall_rating) {
      doc.setFontSize(7)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.gray)
      doc.text('Rating:', xPos, barY + 5)
      xPos += 13
      doc.setFontSize(9)
      setColor(doc, BRAND.primaryDark)
      doc.text(normalizedAnalysis.overall_rating, xPos, barY + 5)
      xPos += doc.getTextWidth(normalizedAnalysis.overall_rating) + 6
    }
    
    if (normalizedAnalysis.hiring_recommendation) {
      const rec = normalizedAnalysis.hiring_recommendation.replace(/_/g, ' ')
      const recColor = rec.includes('STRONGLY') || rec.includes('RECOMMEND') ? BRAND.green : rec.includes('CONSIDER') ? BRAND.amber : BRAND.red
      
      // Recommendation pill
      const recW = doc.getTextWidth(rec) + 8
      drawRect(doc, xPos, barY + 1, recW, 6, recColor, 2)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.white)
      doc.text(rec, xPos + 4, barY + 5)
      xPos += recW + 6
    }
    
    if (normalizedAnalysis.confidence_score) {
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.gray)
      doc.text(`Confidence: ${normalizedAnalysis.confidence_score}%`, xPos, barY + 5)
      xPos += 30
    }

    if (normalizedAnalysis?.ideal_roles && normalizedAnalysis.ideal_roles.length > 0) {
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.gray)
      const rolesText = 'Ideal: ' + normalizedAnalysis.ideal_roles.slice(0, 2).join(', ')
      const maxRolesW = pageWidth - margin - xPos - 4
      if (maxRolesW > 20) {
        const rolesTrunc = rolesText.length > 50 ? rolesText.substring(0, 48) + '...' : rolesText
        doc.text(rolesTrunc, xPos, barY + 5)
      }
    }

    y = barY + 11
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
      y = writeWrappedText(doc, normalizedAnalysis.technical_assessment, margin, y, contentWidth, 8, BRAND.black, 3.6, pageWidth, pageHeight, pageNum)
      y += 2
    }
    if (normalizedAnalysis.experience_assessment) {
      y = writeWrappedText(doc, normalizedAnalysis.experience_assessment, margin, y, contentWidth, 8, BRAND.black, 3.6, pageWidth, pageHeight, pageNum)
      y += 2
    }
    y += 2
  }

  // Hiring Recommendation Rationale
  if (normalizedAnalysis?.hiring_recommendation_rationale) {
    y = checkPageBreak(doc, y, 14, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Hiring Recommendation', y, pageWidth)
    y = writeWrappedText(doc, normalizedAnalysis.hiring_recommendation_rationale, margin, y, contentWidth, 8, BRAND.black, 3.6, pageWidth, pageHeight, pageNum)
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

  // LinkedIn (only show if available — other contact info already in header)
  if (candidate.linkedin) {
    y = checkPageBreak(doc, y, 8, pageWidth, pageHeight, pageNum)
    doc.setFontSize(7.5)
    doc.setFont('helvetica', 'bold')
    setColor(doc, BRAND.primaryDark)
    doc.text('LinkedIn:', margin, y + 3)
    doc.setFont('helvetica', 'normal')
    setColor(doc, BRAND.accent)
    doc.text(candidate.linkedin, margin + 18, y + 3)
    y += 6
  }

  // Professional Summary — full text, no truncation (page breaks handle overflow)
  if (candidate.summary) {
    y = checkPageBreak(doc, y, 14, pageWidth, pageHeight, pageNum)
    y = drawSectionHeading(doc, 'Professional Summary', y, pageWidth)
    y = writeWrappedText(doc, candidate.summary, margin, y, contentWidth, 8, BRAND.black, 3.6, pageWidth, pageHeight, pageNum)
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
    
    candidate.workHistory.slice(0, 18).forEach((job) => {
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
        const desc = job.description.length > 400 ? job.description.substring(0, 400) + '...' : job.description
        y = writeWrappedText(doc, desc, margin + 4, y, contentWidth - 4, 7, BRAND.black, 3.2, pageWidth, pageHeight, pageNum)
        y += 1
      }
      y += 2
    })
    if (candidate.workHistory.length > 18) {
      doc.setFontSize(7)
      doc.setFont('helvetica', 'italic')
      setColor(doc, BRAND.gray)
      doc.text(`+ ${candidate.workHistory.length - 18} more positions on file`, margin + 4, y)
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

  // ===== SECTION 3 — ORIGINAL CV / RESUME (merged PDF or text fallback) =====
  // Try to fetch the original resume PDF from the backend and merge it
  let resumeMerged = false
  // Always attempt to fetch resume if we have a candidate ID — the API will 404 if none exists
  if (candidate.id) {
    try {
      const token = useAuthStore.getState().token
      const resumeRes = await fetch(`${config.endpoints.candidates}/${candidate.id}/resume`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (resumeRes.ok) {
        const contentType = resumeRes.headers.get('content-type') || ''
        if (contentType.includes('pdf')) {
          const resumeBytes = await resumeRes.arrayBuffer()
          if (resumeBytes.byteLength > 100) {
            // Finalize assessment PDF footer on last page
            drawFooter(doc, pageWidth, pageHeight, pageNum.value)

            // Convert jsPDF output to ArrayBuffer and merge with original resume
            const assessmentBytes = doc.output('arraybuffer')
            const mergedPdf = await PDFDocument.create()

            // Copy assessment pages
            const assessmentDoc = await PDFDocument.load(assessmentBytes)
            const assessmentPages = await mergedPdf.copyPages(assessmentDoc, assessmentDoc.getPageIndices())
            assessmentPages.forEach((p) => mergedPdf.addPage(p))

            // Add separator page before original resume
            const sepDoc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
            const sepW = sepDoc.internal.pageSize.getWidth()
            const sepH = sepDoc.internal.pageSize.getHeight()
            drawRect(sepDoc, 0, 0, sepW, sepH, BRAND.primaryDark)
            // White center box
            const boxW = 140, boxH = 60
            const boxX = (sepW - boxW) / 2, boxY = (sepH - boxH) / 2 - 10
            drawRect(sepDoc, boxX, boxY, boxW, boxH, BRAND.white, 8)
            drawRect(sepDoc, boxX, boxY + boxH - 4, boxW, 4, BRAND.accent, 2)
            sepDoc.setFontSize(22)
            sepDoc.setFont('helvetica', 'bold')
            setColor(sepDoc, BRAND.primaryDark)
            sepDoc.text('ORIGINAL RESUME', sepW / 2, boxY + 22, { align: 'center' })
            sepDoc.setFontSize(11)
            sepDoc.setFont('helvetica', 'normal')
            setColor(sepDoc, BRAND.gray)
            sepDoc.text(candidate.name, sepW / 2, boxY + 34, { align: 'center' })
            sepDoc.setFontSize(9)
            sepDoc.text('Attached untouched from submission', sepW / 2, boxY + 44, { align: 'center' })
            const sepBytes = sepDoc.output('arraybuffer')
            const sepPdfDoc = await PDFDocument.load(sepBytes)
            const sepPages = await mergedPdf.copyPages(sepPdfDoc, [0])
            sepPages.forEach((p) => mergedPdf.addPage(p))

            // Copy original resume pages (untouched)
            try {
              const resumeDoc = await PDFDocument.load(resumeBytes, { ignoreEncryption: true })
              const resumePages = await mergedPdf.copyPages(resumeDoc, resumeDoc.getPageIndices())
              resumePages.forEach((p) => mergedPdf.addPage(p))
              resumeMerged = true
            } catch (mergeErr) {
              console.warn('Could not merge resume PDF (may be encrypted/corrupt):', mergeErr)
            }

            if (resumeMerged) {
              const finalBytes = await mergedPdf.save()
              const blob = new Blob([finalBytes as unknown as ArrayBuffer], { type: 'application/pdf' })
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              const safeName = candidate.name.replace(/[^a-zA-Z0-9\s]/g, '').replace(/\s+/g, '_')
              a.href = url
              a.download = `AI_SUMMARY_${safeName}.pdf`
              document.body.appendChild(a)
              a.click()
              document.body.removeChild(a)
              URL.revokeObjectURL(url)
              return  // Done — merged PDF saved
            }
          }
        }
      }
    } catch (err) {
      console.warn('Failed to fetch resume for PDF merge:', err)
    }
  }

  // Fallback: render resume text if merge failed or no resume file available
  // Sanitize resume text: detect and skip garbled/mojibake content
  let cleanResumeText = candidate.resumeText?.trim() || ''
  const isMojibake = detectMojibake(cleanResumeText)

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
  doc.save(`AI_SUMMARY_${safeName}.pdf`)
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
          hasResume: full.hasResume ?? candidate.hasResume,
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

/**
 * Download the original resume as a standalone file.
 * Tries to fetch the original PDF/DOCX from backend first.
 * Falls back to generating a clean PDF from resume text.
 */
export async function downloadOriginalResume(candidate: CandidateData): Promise<void> {
  const safeName = candidate.name.replace(/[^a-zA-Z0-9\s]/g, '').replace(/\s+/g, '_')

  // 1) Try to download original resume file from backend
  if (candidate.id) {
    try {
      const token = useAuthStore.getState().token
      const res = await fetch(`${config.endpoints.candidates}/${candidate.id}/resume`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (res.ok) {
        const blob = await res.blob()
        if (blob.size > 100) {
          const contentType = res.headers.get('content-type') || ''
          const ext = contentType.includes('pdf') ? 'pdf' : contentType.includes('word') ? 'docx' : 'pdf'
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          a.href = url
          a.download = `Resume_${safeName}.${ext}`
          document.body.appendChild(a)
          a.click()
          document.body.removeChild(a)
          URL.revokeObjectURL(url)
          return
        }
      }
    } catch (err) {
      console.warn('Could not fetch original resume file:', err)
    }
  }

  // 2) Fetch full candidate data if resume text is missing
  let resumeText = candidate.resumeText?.trim() || ''
  if (!resumeText && candidate.id) {
    try {
      const token = useAuthStore.getState().token
      const res = await fetch(`${config.endpoints.candidates}/${candidate.id}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (res.ok) {
        const full = await res.json()
        resumeText = (full.resume_text || full.resumeText || '').trim()
      }
    } catch { /* ignore */ }
  }

  if (!resumeText || resumeText.length < 20) {
    throw new Error('No resume available for this candidate')
  }

  // Check for mojibake — if text is garbled, don't generate garbage PDF
  if (detectMojibake(resumeText)) {
    throw new Error('Resume text contains encoding errors. Original file not available.')
  }

  // 3) Generate a clean PDF from resume text
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
  const pageWidth = doc.internal.pageSize.getWidth()
  const pageHeight = doc.internal.pageSize.getHeight()
  const margin = 14

  // Header bar
  drawRect(doc, 0, 0, pageWidth, 12, BRAND.primaryDark)
  doc.setFontSize(10)
  doc.setFont('helvetica', 'bold')
  setColor(doc, BRAND.white)
  doc.text(`Resume — ${candidate.name}`, margin, 8)
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.primaryLight)
  const dateStr = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  doc.text(dateStr, pageWidth - margin, 8, { align: 'right' })
  drawRect(doc, 0, 12, pageWidth, 0.5, BRAND.accent)

  // Resume text body
  let y = 18
  const contentWidth = pageWidth - margin * 2
  doc.setFontSize(9)
  doc.setFont('helvetica', 'normal')
  setColor(doc, BRAND.black)

  const lines = doc.splitTextToSize(resumeText, contentWidth)
  const lineH = 4

  for (let i = 0; i < lines.length; i++) {
    if (y + lineH > pageHeight - 15) {
      doc.addPage()
      drawRect(doc, 0, 0, pageWidth, 8, BRAND.primaryDark)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'bold')
      setColor(doc, BRAND.white)
      doc.text(`Resume — ${candidate.name} (continued)`, margin, 5.5)
      drawRect(doc, 0, 8, pageWidth, 0.5, BRAND.accent)
      y = 14
      doc.setFontSize(9)
      doc.setFont('helvetica', 'normal')
      setColor(doc, BRAND.black)
    }
    doc.text(lines[i], margin, y)
    y += lineH
  }

  doc.save(`Resume_${safeName}.pdf`)
}
