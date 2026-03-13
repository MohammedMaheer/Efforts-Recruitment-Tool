/**
 * AI Recruiter — Branded PDF Generator v3
 * Matches the official Efforts Solutions reference design exactly:
 *  – Bold geometric logo + red accent line
 *  – "AI RECRUITER AGENT SUMMARY" title with alternating cyan/navy
 *  – Blue gradient hero card with initial avatar + circular score gauge
 *  – Detailed Score Analysis (3 visual cards)
 *  – Career Timeline with skill badges
 *  – Resume Highlights
 *  – Footer with company info + logo icon
 */

import { jsPDF } from 'jspdf'
import { PDFDocument } from 'pdf-lib'
import { authFetch } from '@/lib/authFetch'
import { config } from '@/config'

/* ═══════════════════════════════════════════════════════════════════
   Brand Colors
   ═══════════════════════════════════════════════════════════════════ */
type C3 = [number, number, number]

const C = {
  navy:        [0, 32, 96]       as C3,
  navyDark:    [10, 20, 60]      as C3,
  cyan:        [0, 176, 240]     as C3,
  cyanLight:   [200, 230, 255]   as C3,
  heroBlue:    [55, 95, 220]     as C3,
  heroBlueL:   [80, 120, 235]    as C3,
  heroBlueD:   [40, 70, 190]     as C3,
  gradPink:    [200, 60, 160]    as C3,
  gradPurple:  [130, 50, 200]    as C3,
  redAccent:   [234, 76, 75]     as C3,
  gray:        [127, 127, 127]   as C3,
  grayDark:    [70, 70, 85]      as C3,
  grayLight:   [240, 242, 247]   as C3,
  calloutBg:   [235, 243, 255]   as C3,
  cardBorder:  [225, 230, 240]   as C3,
  white:       [255, 255, 255]   as C3,
  black:       [30, 30, 40]      as C3,
  green:       [34, 197, 94]     as C3,
  greenDark:   [22, 163, 74]     as C3,
  greenBg:     [220, 252, 231]   as C3,
  red:         [220, 38, 38]     as C3,
  redBg:       [254, 226, 226]   as C3,
  amber:       [217, 119, 6]     as C3,
}

/* ═══════════════════════════════════════════════════════════════════
   Interfaces
   ═══════════════════════════════════════════════════════════════════ */
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

/* ═══════════════════════════════════════════════════════════════════
   Logo — canvas-rendered geometric style
   ═══════════════════════════════════════════════════════════════════ */
let _logoUrl: string | null = null
let _logoAspect = 1

async function getLogoDataUrl(): Promise<{ url: string; aspect: number }> {
  if (_logoUrl) return { url: _logoUrl, aspect: _logoAspect }

  // Try loading the actual logo image from the public folder
  try {
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const img = new Image()
      img.crossOrigin = 'anonymous'
      img.onload = () => {
        const canvas = document.createElement('canvas')
        canvas.width = img.naturalWidth
        canvas.height = img.naturalHeight
        const ctx = canvas.getContext('2d')!
        // White background (PDF doesn't handle transparency well)
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.drawImage(img, 0, 0)
        _logoAspect = img.naturalWidth / img.naturalHeight
        resolve(canvas.toDataURL('image/png'))
      }
      img.onerror = () => reject(new Error('Image load failed'))
      img.src = '/effortz-logo-dark.png'
    })
    _logoUrl = dataUrl
    return { url: _logoUrl, aspect: _logoAspect }
  } catch {
    // Fallback: canvas-render the logo text if image file is missing
    return new Promise((resolve) => {
      const s = 4
      const w = 320 * s, h = 90 * s
      const canvas = document.createElement('canvas')
      canvas.width = w; canvas.height = h
      const ctx = canvas.getContext('2d')!
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, w, h)
      ctx.fillStyle = '#001e5a'
      ctx.font = `900 ${34 * s}px "Arial Black", "Impact", "Trebuchet MS", sans-serif`
      ctx.textBaseline = 'top'
      ctx.fillText('EFFORTS', 0, 0)
      ctx.font = `900 ${28 * s}px "Arial Black", "Impact", "Trebuchet MS", sans-serif`
      ctx.fillText('SOLUTIONS', 0, 38 * s)
      ctx.fillStyle = '#7f7f7f'
      ctx.font = `600 ${8 * s}px "Trebuchet MS", Arial, sans-serif`
      ctx.fillText('IT TECHNOLOGY & SOLUTIONS', 0, 72 * s)
      _logoUrl = canvas.toDataURL('image/png')
      _logoAspect = w / h
      resolve({ url: _logoUrl, aspect: _logoAspect })
    })
  }
}

/* Small logo icon for footer */
let _iconUrl: string | null = null
async function getLogoIconUrl(): Promise<string> {
  if (_iconUrl) return _iconUrl
  return new Promise((resolve) => {
    const s = 4, sz = 40 * s
    const canvas = document.createElement('canvas')
    canvas.width = sz; canvas.height = sz
    const ctx = canvas.getContext('2d')!
    // Navy rounded square
    ctx.fillStyle = '#001e5a'
    const r = 6 * s
    ctx.beginPath()
    ctx.moveTo(r, 0)
    ctx.lineTo(sz - r, 0)
    ctx.quadraticCurveTo(sz, 0, sz, r)
    ctx.lineTo(sz, sz - r)
    ctx.quadraticCurveTo(sz, sz, sz - r, sz)
    ctx.lineTo(r, sz)
    ctx.quadraticCurveTo(0, sz, 0, sz - r)
    ctx.lineTo(0, r)
    ctx.quadraticCurveTo(0, 0, r, 0)
    ctx.fill()
    // White "E" letter
    ctx.fillStyle = '#ffffff'
    ctx.font = `900 ${24 * s}px "Arial Black", Impact, sans-serif`
    ctx.textBaseline = 'middle'
    ctx.textAlign = 'center'
    ctx.fillText('E', sz / 2, sz / 2 + 1 * s)
    // Red accent bar at bottom
    ctx.fillStyle = '#ea4c4b'
    ctx.fillRect(6 * s, sz - 5 * s, sz - 12 * s, 2 * s)

    _iconUrl = canvas.toDataURL('image/png')
    resolve(_iconUrl)
  })
}

/* ═══════════════════════════════════════════════════════════════════
   Drawing Primitives
   ═══════════════════════════════════════════════════════════════════ */
function setT(doc: jsPDF, c: C3) { doc.setTextColor(c[0], c[1], c[2]) }

function fillR(doc: jsPDF, x: number, y: number, w: number, h: number, c: C3, r?: number) {
  doc.setFillColor(c[0], c[1], c[2])
  r ? doc.roundedRect(x, y, w, h, r, r, 'F') : doc.rect(x, y, w, h, 'F')
}

function lineH(doc: jsPDF, x1: number, y: number, x2: number, c: C3, w = 0.5) {
  doc.setDrawColor(c[0], c[1], c[2])
  doc.setLineWidth(w)
  doc.line(x1, y, x2, y)
}

function lineV(doc: jsPDF, x: number, y1: number, y2: number, c: C3, w = 0.5) {
  doc.setDrawColor(c[0], c[1], c[2])
  doc.setLineWidth(w)
  doc.line(x, y1, x, y2)
}

/* Draw a horizontal multi-stop gradient */
function gradientH(doc: jsPDF, x: number, y: number, w: number, h: number, stops: C3[]) {
  const n = 40
  const sw = w / n
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1)
    const si = t * (stops.length - 1)
    const ci = Math.floor(si)
    const f = si - ci
    const a = stops[Math.min(ci, stops.length - 1)]
    const b = stops[Math.min(ci + 1, stops.length - 1)]
    const rgb: C3 = [
      Math.round(a[0] + (b[0] - a[0]) * f),
      Math.round(a[1] + (b[1] - a[1]) * f),
      Math.round(a[2] + (b[2] - a[2]) * f),
    ]
    fillR(doc, x + i * sw, y, sw + 0.3, h, rgb)
  }
}

/* Draw a vertical multi-stop gradient */
function gradientV(doc: jsPDF, x: number, y: number, w: number, h: number, stops: C3[]) {
  const n = 25
  const sh = h / n
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1)
    const si = t * (stops.length - 1)
    const ci = Math.floor(si)
    const f = si - ci
    const a = stops[Math.min(ci, stops.length - 1)]
    const b = stops[Math.min(ci + 1, stops.length - 1)]
    const rgb: C3 = [
      Math.round(a[0] + (b[0] - a[0]) * f),
      Math.round(a[1] + (b[1] - a[1]) * f),
      Math.round(a[2] + (b[2] - a[2]) * f),
    ]
    fillR(doc, x, y + i * sh, w, sh + 0.2, rgb)
  }
}

/* Draw a circular arc (score gauge) */
function drawArc(doc: jsPDF, cx: number, cy: number, r: number, startDeg: number, endDeg: number, c: C3, lw: number) {
  const segs = Math.max(Math.ceil(Math.abs(endDeg - startDeg) / 6), 4)
  const toRad = Math.PI / 180
  doc.setDrawColor(c[0], c[1], c[2])
  doc.setLineWidth(lw)
  for (let i = 0; i < segs; i++) {
    const a1 = (startDeg + (endDeg - startDeg) * (i / segs)) * toRad
    const a2 = (startDeg + (endDeg - startDeg) * ((i + 1) / segs)) * toRad
    doc.line(
      cx + Math.cos(a1) * r, cy + Math.sin(a1) * r,
      cx + Math.cos(a2) * r, cy + Math.sin(a2) * r,
    )
  }
}

/* ═══════════════════════════════════════════════════════════════════
   Rating Derivation
   ═══════════════════════════════════════════════════════════════════ */
function deriveRating(s: number) {
  if (s >= 90) return { rating: 'A+', rec: 'STRONGLY_RECOMMEND', conf: 92 }
  if (s >= 80) return { rating: 'A', rec: 'STRONGLY_RECOMMEND', conf: 85 }
  if (s >= 70) return { rating: 'A-', rec: 'RECOMMEND', conf: 78 }
  if (s >= 60) return { rating: 'B+', rec: 'RECOMMEND', conf: 72 }
  if (s >= 50) return { rating: 'B', rec: 'CONSIDER', conf: 65 }
  if (s >= 40) return { rating: 'B-', rec: 'CONSIDER', conf: 58 }
  if (s >= 30) return { rating: 'C+', rec: 'REVIEW', conf: 50 }
  if (s >= 20) return { rating: 'C', rec: 'REVIEW', conf: 42 }
  return { rating: 'C-', rec: 'NOT_RECOMMENDED', conf: 35 }
}

function scoreStatusText(s: number): string {
  if (s >= 80) return 'Strong Fit'
  if (s >= 60) return 'Good Match'
  if (s >= 40) return 'Moderate Fit'
  return 'Needs Review'
}

function scoreColor(s: number): C3 {
  if (s >= 70) return C.greenDark
  if (s >= 50) return C.amber
  if (s > 0) return C.red
  return C.gray
}

/* ═══════════════════════════════════════════════════════════════════
   HEADER — Logo + Red Accent Line + Title
   ═══════════════════════════════════════════════════════════════════ */
async function drawHeader(doc: jsPDF, pw: number): Promise<number> {
  fillR(doc, 0, 0, pw, 32, C.white)

  // Logo — compact, top-left
  try {
    const { url, aspect } = await getLogoDataUrl()
    const logoH = 12
    let logoW = logoH * aspect
    if (logoW > 48) logoW = 48
    doc.addImage(url, 'PNG', 18, 3, logoW, logoH)
  } catch {
    doc.setFontSize(12)
    doc.setFont('helvetica', 'bold')
    setT(doc, C.navy)
    doc.text('EFFORTS SOLUTIONS', 18, 10)
  }

  // Navy decorative bar under logo
  fillR(doc, 18, 17, 42, 1.8, C.navy)

  // Red accent line from after navy bar to right edge
  lineH(doc, 64, 17.9, pw - 18, C.redAccent, 0.8)

  // Title: "AI RECRUITER AGENT SUMMARY" — alternating cyan/navy, centered
  const titleY = 27
  doc.setFontSize(11)
  doc.setFont('helvetica', 'bold')

  const parts: { t: string; c: C3 }[] = [
    { t: 'AI', c: C.cyan },
    { t: ' RECRUITER', c: C.navy },
    { t: ' AGENT', c: C.cyan },
    { t: ' SUMMARY', c: C.navy },
  ]

  let tw = 0
  for (const p of parts) tw += doc.getTextWidth(p.t)
  let cx = (pw - tw) / 2

  for (const p of parts) {
    const trimmed = p.t.trimStart()
    const gap = doc.getTextWidth(p.t) - doc.getTextWidth(trimmed)
    cx += gap
    setT(doc, p.c)
    doc.text(trimmed, cx, titleY)
    cx += doc.getTextWidth(trimmed)
  }

  return 32
}

/* ═══════════════════════════════════════════════════════════════════
   Continuation Header (pages 2+)
   ═══════════════════════════════════════════════════════════════════ */
function contHeader(doc: jsPDF, pw: number, name: string): number {
  fillR(doc, 0, 0, pw, 12, C.white)
  doc.setFontSize(8)
  doc.setFont('helvetica', 'bold')
  setT(doc, C.navy)
  doc.text('EFFORTS SOLUTIONS', 18, 7)
  fillR(doc, 18, 9, 35, 1, C.navy)
  lineH(doc, 56, 9.5, pw - 18, C.redAccent, 0.5)
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setT(doc, C.gray)
  doc.text(name, pw - 18, 8, { align: 'right' })
  return 15
}

/* ═══════════════════════════════════════════════════════════════════
   HERO CARD — Blue gradient + Initial Avatar + Score Gauge
   ═══════════════════════════════════════════════════════════════════ */
function drawHero(doc: jsPDF, cand: CandidateData, y: number, ml: number, cw: number): number {
  const cardH = 42

  // Blue card background
  fillR(doc, ml, y, cw, cardH, C.heroBlue, 5)

  // Gradient bottom strip (blue → cyan → pink → purple)
  const stripH = 3
  gradientH(doc, ml, y + cardH - stripH, cw, stripH, [
    C.heroBlue, [60, 140, 255] as C3, C.cyan, C.gradPink, C.gradPurple,
  ])
  // re-round bottom corners by overlaying tiny corner fills
  fillR(doc, ml, y + cardH - 5, 5, 5, C.heroBlue) // will be covered by gradient
  // Just let the gradient extend to edges — good enough

  // ── Initial Avatar Circle (green) ──
  const initial = (cand.name || 'C').charAt(0).toUpperCase()
  const avX = ml + 14
  const avY = y + cardH / 2 - 2
  const avR = 8
  doc.setFillColor(C.green[0], C.green[1], C.green[2])
  doc.circle(avX, avY, avR, 'F')
  // Lighter inner ring
  doc.setFillColor(40, 210, 110)
  doc.circle(avX, avY, avR - 1.5, 'F')
  doc.setFillColor(C.green[0], C.green[1], C.green[2])
  doc.circle(avX, avY, avR - 2.5, 'F')
  // Initial letter
  doc.setFontSize(16)
  doc.setFont('helvetica', 'bold')
  setT(doc, C.white)
  doc.text(initial, avX, avY + 2, { align: 'center' })

  // ── Score Gauge (right side) ──
  const score = cand.matchScore ?? 0
  const sColor = scoreColor(score)
  const gX = ml + cw - 28
  const gY = y + 16
  const gR = 13

  // White circle background
  doc.setFillColor(255, 255, 255)
  doc.circle(gX, gY, gR + 2, 'F')

  // Gray track ring
  drawArc(doc, gX, gY, gR, 0, 360, [220, 222, 230] as C3, 2.2)

  // Colored score arc (from -90° clockwise)
  if (score > 0) {
    const endDeg = -90 + (score / 100) * 360
    drawArc(doc, gX, gY, gR, -90, endDeg, sColor, 2.5)
  }

  // Score text
  doc.setFontSize(18)
  doc.setFont('helvetica', 'bold')
  setT(doc, sColor)
  doc.text(`${score.toFixed(0)}%`, gX, gY + 2.5, { align: 'center' })

  // Status text below gauge
  const statusT = scoreStatusText(score)
  doc.setFontSize(6.5)
  doc.setFont('helvetica', 'bold')
  setT(doc, sColor)
  doc.text(statusT, gX, gY + gR + 7, { align: 'center' })

  // ── Candidate Details (left of gauge) ──
  const txtX = avX + avR + 6
  const maxTxtW = gX - gR - txtX - 8

  // Name
  doc.setFontSize(15)
  doc.setFont('helvetica', 'bold')
  setT(doc, C.white)
  let name = sanitizeForHelvetica(cand.name || 'Candidate')
  if (doc.getTextWidth(name) > maxTxtW) name = name.substring(0, 26) + '...'
  doc.text(name, txtX, y + 13)

  // Title/role
  const role = sanitizeForHelvetica(cand.jobSubcategory || cand.jobCategory || 'Professional')
  doc.setFontSize(9)
  doc.setFont('helvetica', 'normal')
  setT(doc, [180, 210, 255] as C3)
  doc.text(role.substring(0, 50), txtX, y + 19)

  // Location + experience
  doc.setFontSize(7.5)
  setT(doc, [190, 215, 255] as C3)
  const locParts: string[] = []
  if (cand.location && cand.location !== 'Not Specified' && cand.location !== 'Unknown') {
    locParts.push(sanitizeForHelvetica(cand.location))
  }
  if (cand.experience > 0) locParts.push(cand.experience + ' Years experience')
  if (locParts.length > 0) {
    doc.text(locParts.join('  |  '), txtX, y + 26)
  }

  // Email + phone
  const contactParts: string[] = []
  if (cand.email) contactParts.push(sanitizeForHelvetica(cand.email))
  if (cand.phone) contactParts.push(sanitizeForHelvetica(cand.phone))
  if (contactParts.length > 0) {
    let contactStr = contactParts.join('   |   ')
    if (doc.getTextWidth(contactStr) > maxTxtW) contactStr = contactStr.substring(0, 55) + '...'
    doc.text(contactStr, txtX, y + 32)
  }

  return y + cardH + 4
}

/* ═══════════════════════════════════════════════════════════════════
   Section Title with icon dot
   ═══════════════════════════════════════════════════════════════════ */
function sectionTitle(doc: jsPDF, title: string, y: number, ml: number, cw: number, iconColor?: C3): number {
  const ic = iconColor || C.cyan
  // Icon circle
  doc.setFillColor(ic[0], ic[1], ic[2])
  doc.circle(ml + 3, y + 2.5, 2.5, 'F')
  // Small white symbol in circle
  doc.setFontSize(5)
  doc.setFont('helvetica', 'bold')
  setT(doc, C.white)
  // Use only ASCII-safe symbols (Helvetica compatible)
  doc.text('*', ml + 3, y + 3.5, { align: 'center' })

  // Title text
  doc.setFontSize(11)
  doc.setFont('helvetica', 'bold')
  setT(doc, C.navy)
  doc.text(title, ml + 9, y + 4.5)

  // Underline
  lineH(doc, ml + 9, y + 7, ml + cw, [225, 230, 240] as C3, 0.15)

  return y + 11
}

/* ═══════════════════════════════════════════════════════════════════
   AI Analysis Summary — callout box
   ═══════════════════════════════════════════════════════════════════ */
function drawAnalysisSummary(
  doc: jsPDF, text: string, y: number,
  ml: number, cw: number, pw: number, ph: number,
  pn: { value: number }, cn: string,
): number {
  doc.setFontSize(8)
  doc.setFont('helvetica', 'normal')
  const lines: string[] = doc.splitTextToSize(sanitizeForHelvetica(text), cw - 14)
  const lh = 3.8
  const padTop = 5
  const padBot = 4

  // Paginating callout box — draw background chunk per page
  let lineIdx = 0
  while (lineIdx < lines.length) {
    // How many lines fit on this page?
    const avail = ph - 22 - y - padTop - padBot
    const maxLines = Math.max(1, Math.floor(avail / lh))
    const chunk = lines.slice(lineIdx, lineIdx + maxLines)
    const chunkH = chunk.length * lh + padTop + padBot

    if (y + chunkH > ph - 22) {
      drawFooter(doc, pw, ph, pn.value)
      doc.addPage()
      pn.value++
      y = contHeader(doc, pw, cn)
      continue // re-measure available space on new page
    }

    // Draw box background for this chunk
    fillR(doc, ml, y, cw, chunkH, C.calloutBg, 3)
    fillR(doc, ml, y + 2, 2.5, chunkH - 4, C.cyan)

    setT(doc, C.grayDark)
    doc.setFontSize(8)
    doc.setFont('helvetica', 'normal')
    let ty = y + padTop
    for (const line of chunk) {
      doc.text(line, ml + 8, ty)
      ty += lh
    }
    y = ty + padBot
    lineIdx += chunk.length
  }

  return y + 1
}

/* ═══════════════════════════════════════════════════════════════════
   Detailed Score Analysis — 3 Cards
   ═══════════════════════════════════════════════════════════════════ */
function deriveScores(matchScore: number, skills: string[], exp: number, conf: number) {
  const base = matchScore || 0
  const skillFactor = Math.min(skills.length, 10) / 10 * 8 - 4
  const expFactor = Math.min(exp, 15) / 15 * 10 - 3
  const confBase = conf > 0 ? conf : base

  return {
    technical: Math.round(Math.min(100, Math.max(5, base + skillFactor))),
    problemSolving: Math.round(Math.min(100, Math.max(5, base + expFactor))),
    collaboration: Math.round(Math.min(100, Math.max(5, confBase + 2))),
  }
}

function drawScoreCards(
  doc: jsPDF, cand: CandidateData, analysis: AIAnalysisData | null,
  y: number, ml: number, cw: number, pw: number, ph: number,
  pn: { value: number }, cn: string,
): number {
  const scores = deriveScores(
    cand.matchScore ?? 0,
    cand.skills || [],
    cand.experience || 0,
    analysis?.confidence_score || 0,
  )

  const cardW = (cw - 8) / 3
  const cardH = 48
  const gap = 4

  if (y + cardH + 6 > ph - 22) {
    drawFooter(doc, pw, ph, pn.value)
    doc.addPage()
    pn.value++
    y = contHeader(doc, pw, cn)
  }

  const cards = [
    {
      title: 'Technical Skills',
      score: scores.technical,
      text: analysis?.technical_assessment
        ? sanitizeForHelvetica(analysis.technical_assessment)
        : `The candidate lists ${(cand.skills || []).length} technical skills including ${(cand.skills || []).slice(0, 6).join(', ')}. The breadth of technical stack suggests capability in relevant areas.`,
    },
    {
      title: 'Problem Solving',
      score: scores.problemSolving,
      text: analysis?.experience_assessment
        ? sanitizeForHelvetica(analysis.experience_assessment)
        : `With ${cand.experience || 0} years of professional experience, ${cand.name || 'the candidate'} demonstrates significant industry tenure. Further details about career progression should be explored in interview.`,
    },
    {
      title: 'Career & Growth',
      score: scores.collaboration,
      text: analysis?.career_trajectory
        ? sanitizeForHelvetica(analysis.career_trajectory)
        : analysis?.education_assessment
        ? sanitizeForHelvetica(analysis.education_assessment)
        : `Career trajectory and growth potential are assessed based on the candidate's profile and professional background.`,
    },
  ]

  cards.forEach((card, i) => {
    const cx = ml + i * (cardW + gap)
    const sc = scoreColor(card.score)

    // Card background with border
    doc.setDrawColor(C.cardBorder[0], C.cardBorder[1], C.cardBorder[2])
    doc.setLineWidth(0.2)
    doc.setFillColor(255, 255, 255)
    doc.roundedRect(cx, y, cardW, cardH, 2, 2, 'FD')

    // Left gradient accent bar
    gradientV(doc, cx, y, 1.5, cardH, [C.heroBlue, C.gradPurple])

    // Title + score on same line
    doc.setFontSize(8)
    doc.setFont('helvetica', 'bold')
    setT(doc, C.grayDark)
    doc.text(card.title, cx + 6, y + 7)

    doc.setFontSize(11)
    doc.setFont('helvetica', 'bold')
    setT(doc, sc)
    doc.text(`${card.score}%`, cx + cardW - 4, y + 8, { align: 'right' })

    // Thin separator
    lineH(doc, cx + 5, y + 10, cx + cardW - 3, [230, 233, 240] as C3, 0.15)

    // Description text
    doc.setFontSize(6.5)
    doc.setFont('helvetica', 'normal')
    setT(doc, C.gray)
    const descLines = doc.splitTextToSize(card.text, cardW - 10)
    let dy = y + 14
    const maxDescLines = Math.floor((cardH - 22) / 3)
    for (const dl of descLines.slice(0, maxDescLines)) {
      doc.text(dl, cx + 5, dy)
      dy += 3
    }

    // Bottom progress bar
    const barY = y + cardH - 4
    fillR(doc, cx + 5, barY, cardW - 10, 2, C.grayLight, 1)
    const fillW = Math.max(1, (cardW - 10) * (card.score / 100))
    gradientH(doc, cx + 5, barY, fillW, 2, [C.heroBlue, C.cyan, C.gradPurple])
  })

  return y + cardH + 4
}

/* ═══════════════════════════════════════════════════════════════════
   Career Timeline — Work History with skill badges
   ═══════════════════════════════════════════════════════════════════ */
function drawCareerTimeline(
  doc: jsPDF, cand: CandidateData,
  y: number, ml: number, cw: number, pw: number, ph: number,
  pn: { value: number }, cn: string,
): number {
  const jobs = (cand.workHistory || []).slice(0, 6)
  if (jobs.length === 0) return y

  const timelineX = ml + 6  // vertical line x position

  for (let ji = 0; ji < jobs.length; ji++) {
    const job = jobs[ji]
    const jobTitle = sanitizeForHelvetica(job.title || 'Position')
    const jobCompany = sanitizeForHelvetica(job.company || '')
    const jobDuration = sanitizeForHelvetica(job.duration || '')
    const jobDesc = sanitizeForHelvetica(job.description || '')

    // Calculate dynamic entry height based on description
    const descMaxW = cw - 20
    let descLines: string[] = []
    if (jobDesc.length > 5) {
      doc.setFontSize(6.5)
      doc.setFont('helvetica', 'normal')
      descLines = doc.splitTextToSize(jobDesc, descMaxW).slice(0, 3)
    }
    const entryH = 18 + descLines.length * 3

    // Page break check
    if (y + entryH > ph - 22) {
      drawFooter(doc, pw, ph, pn.value)
      doc.addPage()
      pn.value++
      y = contHeader(doc, pw, cn)
      y = sectionTitle(doc, 'Career Timeline (continued)', y, ml, cw, C.heroBlue)
    }

    // Vertical timeline line
    const lineEndY = ji < jobs.length - 1 ? y + entryH : y + 8
    lineV(doc, timelineX, y, lineEndY, [200, 210, 230] as C3, 0.3)

    // Timeline dot
    doc.setFillColor(C.heroBlue[0], C.heroBlue[1], C.heroBlue[2])
    doc.circle(timelineX, y + 4, 2.5, 'F')
    doc.setFillColor(255, 255, 255)
    doc.circle(timelineX, y + 4, 1.2, 'F')

    // Job title
    doc.setFontSize(9)
    doc.setFont('helvetica', 'bold')
    setT(doc, C.black)
    const titleX = timelineX + 8
    doc.text(jobTitle, titleX, y + 5)

    // Date range on right
    if (jobDuration) {
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.gray)
      doc.text(jobDuration, ml + cw, y + 5, { align: 'right' })
    }

    // Company
    doc.setFontSize(7.5)
    doc.setFont('helvetica', 'normal')
    setT(doc, C.gray)
    doc.text(jobCompany, titleX, y + 10)

    // Job description (replaces misleading round-robin skill badges)
    if (descLines.length > 0) {
      doc.setFontSize(6.5)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.grayDark)
      let dy = y + 14
      for (const dl of descLines) {
        doc.text(dl, titleX, dy)
        dy += 3
      }
    }

    // Thin separator line between entries
    if (ji < jobs.length - 1) {
      lineH(doc, titleX, y + entryH - 2, ml + cw, [235, 238, 245] as C3, 0.1)
    }

    y += entryH
  }

  return y + 2
}

/* ═══════════════════════════════════════════════════════════════════
   Resume Highlights — Numbered bullet points
   ═══════════════════════════════════════════════════════════════════ */
function drawResumeHighlights(
  doc: jsPDF, items: string[],
  y: number, ml: number, cw: number, pw: number, ph: number,
  pn: { value: number }, cn: string,
): number {
  if (items.length === 0) return y

  for (let i = 0; i < Math.min(items.length, 5); i++) {
    if (y + 10 > ph - 22) {
      drawFooter(doc, pw, ph, pn.value)
      doc.addPage()
      pn.value++
      y = contHeader(doc, pw, cn)
    }

    // Numbered circle
    const circX = ml + 6
    doc.setFillColor(C.heroBlue[0], C.heroBlue[1], C.heroBlue[2])
    doc.circle(circX, y + 2.5, 3, 'F')
    doc.setFontSize(6)
    doc.setFont('helvetica', 'bold')
    setT(doc, C.white)
    doc.text(`H${i + 1}`, circX, y + 3.5, { align: 'center' })

    // Text in light background
    const textX = circX + 6
    const textW = cw - 16
    doc.setFontSize(7.5)
    doc.setFont('helvetica', 'normal')
    const lines = doc.splitTextToSize(items[i], textW)
    const bgH = lines.length * 3.5 + 3
    fillR(doc, textX, y - 0.5, textW, bgH, C.grayLight, 2)
    setT(doc, C.grayDark)
    let ly = y + 3
    for (const l of lines) {
      doc.text(l, textX + 3, ly)
      ly += 3.5
    }

    y += bgH + 2
  }

  return y + 1
}

/* ═══════════════════════════════════════════════════════════════════
   FOOTER — Red accent + Company Info + Logo Icon
   ═══════════════════════════════════════════════════════════════════ */
async function drawFooterAsync(doc: jsPDF, pw: number, ph: number, pageNum: number) {
  const fy = ph - 18

  // Red accent line
  lineH(doc, 25, fy, pw - 25, C.redAccent, 0.4)

  // Company info
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setT(doc, C.grayDark)
  doc.text('Efforts Solutions IT', 25, fy + 5)

  doc.setFontSize(7)
  setT(doc, C.gray)
  doc.text('https://effortz.com', pw - 45, fy + 5, { align: 'right' })

  doc.setFontSize(6.5)
  setT(doc, C.gray)
  doc.text(
    'M12, Burooj Tower, Al Khalidhiya, Abu Dhabi, UAE | +97125468880',
    pw / 2,
    fy + 10,
    { align: 'center' },
  )

  // Page number
  doc.setFontSize(6)
  doc.text(`Page ${pageNum}`, 25, fy + 14)

  // Logo icon on right
  try {
    const iconUrl = await getLogoIconUrl()
    doc.addImage(iconUrl, 'PNG', pw - 32, fy + 2, 8, 8)
  } catch { /* skip icon */ }
}

function drawFooter(doc: jsPDF, pw: number, ph: number, pageNum: number) {
  const fy = ph - 18
  lineH(doc, 25, fy, pw - 25, C.redAccent, 0.4)
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setT(doc, C.grayDark)
  doc.text('Efforts Solutions IT', 25, fy + 5)
  doc.setFontSize(7)
  setT(doc, C.gray)
  doc.text('https://effortz.com', pw - 45, fy + 5, { align: 'right' })
  doc.setFontSize(6.5)
  setT(doc, C.gray)
  doc.text(
    'M12, Burooj Tower, Al Khalidhiya, Abu Dhabi, UAE | +97125468880',
    pw / 2, fy + 10, { align: 'center' },
  )
  doc.setFontSize(6)
  doc.text(`Page ${pageNum}`, 25, fy + 14)
}

/* ═══════════════════════════════════════════════════════════════════
   Page Break + Wrapped Text Helpers
   ═══════════════════════════════════════════════════════════════════ */
function pgBreak(doc: jsPDF, y: number, need: number, pw: number, ph: number, pn: { value: number }, cn: string): number {
  if (y + need > ph - 22) {
    drawFooter(doc, pw, ph, pn.value)
    doc.addPage()
    pn.value++
    return contHeader(doc, pw, cn)
  }
  return y
}

function wrapText(
  doc: jsPDF, text: string, x: number, y: number,
  maxW: number, fs: number, color: C3, lh: number,
  pw: number, ph: number, pn: { value: number }, cn: string,
): number {
  doc.setFontSize(fs)
  doc.setFont('helvetica', 'normal')
  setT(doc, color)
  const lines = doc.splitTextToSize(text, maxW)
  for (const line of lines) {
    if (y + lh > ph - 22) {
      drawFooter(doc, pw, ph, pn.value)
      doc.addPage()
      pn.value++
      y = contHeader(doc, pw, cn)
      doc.setFontSize(fs)
      doc.setFont('helvetica', 'normal')
      setT(doc, color)
    }
    doc.text(line, x, y)
    y += lh
  }
  return y
}

/* ═══════════════════════════════════════════════════════════════════
   Text Quality Utilities
   ═══════════════════════════════════════════════════════════════════ */
function isCoverLetter(text: string): boolean {
  if (!text || text.length < 20) return false
  const lo = text.toLowerCase().trim()
  const pats = [
    /^dear\s+(hiring|hr|team|manager|sir|madam|recruiter)/i,
    /^to\s+whom\s+it\s+may\s+concern/i,
    /^respected\s+(sir|madam|hr|hiring)/i,
    /my\s+name\s+is\s+/i,
    /i\s+am\s+writing\s+to\s+(apply|express)/i,
    /please\s+find\s+(my|attached|enclosed)\s+resume/i,
    /i\s+am\s+(a|an)\s+(highly\s+)?motivated/i,
    /sincerely|regards|best\s+wishes|yours\s+(truly|faithfully)/i,
  ]
  let hits = 0
  for (const p of pats) if (p.test(lo)) hits++
  if (hits >= 2 || /^dear\s/i.test(lo)) return true
  if (lo.includes('dear hiring') || lo.includes('dear team') || lo.includes('dear sir')) return true
  return false
}

function cleanSummary(c: CandidateData): string {
  const parts: string[] = []
  const name = c.name || 'Candidate'
  const title = c.jobSubcategory || c.jobCategory || 'Professional'
  const exp = c.experience || 0
  const loc = c.location && c.location !== 'Not Specified' && c.location !== 'Unknown' ? c.location : ''
  if (exp > 0) parts.push(`${name} is a ${title} with ${exp} year${exp !== 1 ? 's' : ''} of experience${loc ? ` based in ${loc}` : ''}.`)
  else parts.push(`${name} is a ${title}${loc ? ` based in ${loc}` : ''}.`)
  if ((c.skills || []).length > 0) parts.push(`Key competencies include ${c.skills!.slice(0, 8).join(', ')}.`)
  if (c.education && c.education.length > 0) {
    const e = c.education[0]
    parts.push(`Education: ${e.degree}${e.field ? ` in ${e.field}` : ''}${e.institution ? ` from ${e.institution}` : ''}.`)
  }
  if (c.workHistory && c.workHistory.length > 0) {
    const j = c.workHistory[0]
    if (j.title && j.company) parts.push(`Most recent: ${j.title} at ${j.company}${j.duration ? ` (${j.duration})` : ''}.`)
  }
  if (c.certifications && c.certifications.length > 0) parts.push(`Certifications: ${c.certifications.slice(0, 3).join(', ')}.`)
  if (c.languages && c.languages.length > 0) parts.push(`Languages: ${c.languages.join(', ')}.`)
  return parts.join(' ')
}

/**
 * Sanitize text for Helvetica font (jsPDF) — remove all characters that Helvetica
 * cannot render. Keeps ASCII printable + common Latin-1 accented chars.
 */
function sanitizeForHelvetica(text: string): string {
  if (!text) return ''
  // Replace common Unicode symbols with ASCII equivalents
  let t = text
    .replace(/[\u2018\u2019\u201A\uFF07]/g, "'") // smart single quotes
    .replace(/[\u201C\u201D\u201E\uFF02]/g, '"') // smart double quotes
    .replace(/[\u2013\u2014\u2015]/g, '-')         // en-dash, em-dash
    .replace(/[\u2026]/g, '...')                     // ellipsis
    .replace(/[\u2022\u2023\u25CF\u25CB]/g, '-')    // bullets
    .replace(/[\u00A0]/g, ' ')                       // non-breaking space
    .replace(/[\u200B-\u200F\u202A-\u202E\uFEFF]/g, '') // zero-width / bidi
    .replace(/[\uD800-\uDFFF]/g, '')                 // lone surrogates
  // Keep only printable ASCII (0x20-0x7E) + common Latin-1 Supplement (0xA1-0xFF) + \n \r \t
  t = t.replace(/[^\x09\x0A\x0D\x20-\x7E\xA1-\xFF]/g, '')
  // Collapse multiple spaces
  t = t.replace(/ {3,}/g, '  ')
  return t
}

function detectMojibake(text: string): boolean {
  if (!text || text.length < 30) return false
  const markers = [
    '\u00C3\u0082', '\u00C3\u0083', '\u00C3\u00A9', '\u00C3\u00A8',
    '\u00C3\u00BC', '\u00C3\u00B6', '\u00C2\u00A0', '\u00C2\u00AE',
    'Ã\u0082', 'Ã\u0083', 'Ã\u00A9', 'Ã\u00A8', 'Ã\u00BC', 'Ã\u00B6',
  ]
  let hits = 0
  for (const m of markers) {
    let pos = 0
    while ((pos = text.indexOf(m, pos)) !== -1) { hits++; pos += m.length }
  }
  // Count characters outside Helvetica-renderable range
  let nonRenderable = 0
  const sample = Math.min(text.length, 3000)
  for (let i = 0; i < sample; i++) {
    const ch = text.charCodeAt(i)
    if (ch >= 0xC0 && ch <= 0xFF) nonRenderable++ // high Latin-1 (mojibake)
    else if (ch > 0xFF && ch !== 0x2013 && ch !== 0x2014 && ch !== 0x2018 && ch !== 0x2019 && ch !== 0x201C && ch !== 0x201D && ch !== 0x2022 && ch !== 0x2026) {
      nonRenderable++ // any non-Latin Unicode (Arabic, CJK, emoji, etc.)
    }
  }
  return hits >= 3 || (nonRenderable / sample > 0.10)
}

/**
 * Detect text where characters are separated by spaces/dashes/dots/bullets,
 * e.g. "S   A   R   A   V   A   N   A" or "S•A•R•A" — bad PDF extraction.
 */
function detectSpacedCharCorruption(text: string): boolean {
  if (!text || text.length < 40) return false
  // Separator class includes spaces, dashes, dots, bullets, and common PDF artifacts (up to 20 chars between letters)
  const re = /(?:^|\s)[A-Za-z0-9](?:[\s\-._\u2022\u2023\u25CF\u25CB\u25AA\u00B7\u2219\u00A0\u2013\u2014|*]{1,20}[A-Za-z0-9]){4,}(?:$|\s)/gm
  const matches = text.match(re) || []
  const matchLen = matches.reduce((s, m) => s + m.length, 0)
  if (matchLen / text.length > 0.25) return true
  // Also check: if >75% of chars are non-alphanumeric and most alnum chars are isolated
  let alnum = 0, isolated = 0
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (/[A-Za-z0-9]/.test(c)) {
      alnum++
      const prevAlnum = i > 0 && /[A-Za-z0-9]/.test(text[i-1])
      const nextAlnum = i < text.length-1 && /[A-Za-z0-9]/.test(text[i+1])
      if (!prevAlnum && !nextAlnum) isolated++
    }
  }
  if (alnum > 10 && (text.length - alnum) / text.length > 0.75 && isolated / alnum > 0.5) return true
  return false
}

/**
 * Collapse spaced-character runs: "S   A   R   A" → "SARA", "S•A•R•A" → "SARA"
 */
function collapseSpacedChars(text: string): string {
  if (!text) return text
  const re = /(?:^|\s)[A-Za-z0-9](?:[\s\-._\u2022\u2023\u25CF\u25CB\u25AA\u00B7\u2219\u00A0\u2013\u2014|*]{1,20}[A-Za-z0-9]){4,}(?:$|\s)/gm
  return text.replace(re, (match) => {
    const letters = match.match(/[A-Za-z0-9]/g) || []
    return ' ' + letters.join('') + ' '
  }).replace(/ {2,}/g, ' ').trim()
}

/* ═══════════════════════════════════════════════════════════════════
   MAIN — generateCandidatePDF
   ═══════════════════════════════════════════════════════════════════ */
export async function generateCandidatePDF(
  candidateInput: CandidateData,
  aiAnalysis?: AIAnalysisData | null,
): Promise<void> {
  let candidate = { ...candidateInput }
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'letter' })
  const pw = doc.internal.pageSize.getWidth()
  const ph = doc.internal.pageSize.getHeight()
  const ml = 20
  const mr = 20
  const cw = pw - ml - mr
  const pn = { value: 1 }
  const cn = candidate.name || 'Candidate'

  // ── Normalize AI analysis ──
  let norm = aiAnalysis ? { ...aiAnalysis } : null
  if (norm) {
    const score = candidate.matchScore ?? 0
    const rating = norm.overall_rating || ''
    const conf = norm.confidence_score || 0
    const isFallback =
      norm.source === 'fallback' || norm.isFallback === true ||
      (rating === 'C+' && conf <= 42) ||
      (score >= 75 && (rating.startsWith('C') || rating === 'B-')) ||
      (score >= 60 && rating.startsWith('C')) ||
      (conf > 0 && conf < 45 && score >= 70)

    if (isFallback) {
      const d = deriveRating(candidate.matchScore ?? 0)
      norm.overall_rating = d.rating
      norm.hiring_recommendation = d.rec
      norm.confidence_score = d.conf
      const skills = candidate.skills || []
      const exp = candidate.experience || 0
      const relCons: string[] = []
      if (skills.length < 5) relCons.push('Limited skills breadth — expanding technical portfolio recommended')
      if (exp < 3) relCons.push('Early career stage — may need mentorship and onboarding support')
      if (!candidate.linkedin) relCons.push('No LinkedIn profile provided for background verification')
      if (!candidate.education?.length) relCons.push('Education details not specified — verification recommended')
      if (!candidate.workHistory?.length) relCons.push('Work history not detailed — explore experience depth in interview')
      if (relCons.length === 0) relCons.push('Profile appears strong — detailed AI analysis recommended')
      norm.cons = relCons
    }
  }

  // ═══════════════════════════════════════════
  // PAGE 1
  // ═══════════════════════════════════════════

  // ── Header ──
  let y = await drawHeader(doc, pw)

  // ── Hero Card ──
  y = drawHero(doc, candidate, y, ml, cw)

  // ── AI Analysis Summary ──
  y = pgBreak(doc, y, 20, pw, ph, pn, cn)
  y = sectionTitle(doc, 'AI Analysis Summary', y, ml, cw, C.heroBlue)

  // Build a rich summary from all AI fields
  let summaryParts: string[] = []
  if (norm?.executive_summary) {
    summaryParts.push(norm.executive_summary)
  } else if (candidate.summary && !isCoverLetter(candidate.summary) && candidate.summary.length < 2000) {
    summaryParts.push(candidate.summary)
  } else {
    summaryParts.push(cleanSummary(candidate))
  }
  // Add match score context
  const matchS = candidate.matchScore ?? 0
  if (matchS > 0) {
    summaryParts.push(`With a match score of ${matchS.toFixed(0)}%, they show ${matchS >= 80 ? 'strong' : matchS >= 60 ? 'moderate' : 'developing'} alignment for the target role. ${norm?.hiring_recommendation ? 'Hiring recommendation: ' + norm.hiring_recommendation + '.' : ''} Based on the available profile data, they are ${matchS >= 80 ? 'a strong' : matchS >= 60 ? 'a promising' : 'a potential'} candidate.`)
  }
  const summaryText = summaryParts.join(' ')

  y = drawAnalysisSummary(doc, summaryText, y, ml, cw, pw, ph, pn, cn)
  y += 2

  // ── Detailed Score Analysis ──
  y = pgBreak(doc, y, 60, pw, ph, pn, cn)
  y = sectionTitle(doc, 'Detailed Score Analysis', y, ml, cw, C.heroBlue)
  y = drawScoreCards(doc, candidate, norm, y, ml, cw, pw, ph, pn, cn)
  y += 1

  // ── Key Strengths (Pros) ──
  if (norm?.pros && norm.pros.length > 0) {
    y = pgBreak(doc, y, 20, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Key Strengths', y, ml, cw, C.green as unknown as C3)
    for (const pro of norm.pros.slice(0, 8)) {
      y = pgBreak(doc, y, 6, pw, ph, pn, cn)
      doc.setFontSize(7.5)
      doc.setFont('helvetica', 'bold')
      setT(doc, C.green as unknown as C3)
      doc.text('+', ml + 6, y)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.grayDark)
      const proLines = doc.splitTextToSize(sanitizeForHelvetica(pro), cw - 14)
      for (const pl of proLines) {
        y = pgBreak(doc, y, 4, pw, ph, pn, cn)
        doc.text(pl, ml + 12, y)
        y += 3.5
      }
      y += 0.5
    }
    y += 2
  }

  // ── Areas of Concern (Cons) ──
  if (norm?.cons && norm.cons.length > 0) {
    y = pgBreak(doc, y, 20, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Areas of Concern', y, ml, cw, [220, 100, 60] as C3)
    for (const con of norm.cons.slice(0, 8)) {
      y = pgBreak(doc, y, 6, pw, ph, pn, cn)
      doc.setFontSize(7.5)
      doc.setFont('helvetica', 'bold')
      setT(doc, [220, 100, 60] as C3)
      doc.text('-', ml + 6, y)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.grayDark)
      const conLines = doc.splitTextToSize(sanitizeForHelvetica(con), cw - 14)
      for (const cl of conLines) {
        y = pgBreak(doc, y, 4, pw, ph, pn, cn)
        doc.text(cl, ml + 12, y)
        y += 3.5
      }
      y += 0.5
    }
    y += 2
  }

  // ── Technical Assessment (full text) ──
  if (norm?.technical_assessment) {
    y = pgBreak(doc, y, 16, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Technical Assessment', y, ml, cw, C.heroBlue)
    y = wrapText(doc, sanitizeForHelvetica(norm.technical_assessment), ml + 8, y, cw - 10, 8, C.grayDark, 3.6, pw, ph, pn, cn)
    y += 3
  }

  // ── Experience Assessment (full text) ──
  if (norm?.experience_assessment) {
    y = pgBreak(doc, y, 16, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Experience Assessment', y, ml, cw, C.heroBlue)
    y = wrapText(doc, sanitizeForHelvetica(norm.experience_assessment), ml + 8, y, cw - 10, 8, C.grayDark, 3.6, pw, ph, pn, cn)
    y += 3
  }

  // ── Career Trajectory ──
  if (norm?.career_trajectory) {
    y = pgBreak(doc, y, 16, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Career Trajectory', y, ml, cw, C.cyan as unknown as C3)
    y = wrapText(doc, sanitizeForHelvetica(norm.career_trajectory), ml + 8, y, cw - 10, 8, C.grayDark, 3.6, pw, ph, pn, cn)
    y += 3
  }

  // ── Career Timeline ──
  if (candidate.workHistory && candidate.workHistory.length > 0) {
    y = pgBreak(doc, y, 30, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Career Timeline', y, ml, cw, C.heroBlue)
    y = drawCareerTimeline(doc, candidate, y, ml, cw, pw, ph, pn, cn)
  }

  // ── Interview Focus Areas ──
  if (norm?.interview_focus_areas && norm.interview_focus_areas.length > 0) {
    y = pgBreak(doc, y, 20, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Interview Focus Areas', y, ml, cw, [100, 80, 200] as C3)
    y = drawResumeHighlights(doc, norm.interview_focus_areas, y, ml, cw, pw, ph, pn, cn)
    y += 2
  }

  // ── Resume Highlights (from pros if no focus areas) ──
  if (!(norm?.interview_focus_areas?.length) && norm?.pros && norm.pros.length > 0) {
    y = pgBreak(doc, y, 20, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Resume Highlights', y, ml, cw, [100, 80, 200] as C3)
    y = drawResumeHighlights(doc, norm.pros, y, ml, cw, pw, ph, pn, cn)
    y += 2
  }

  // ── Hiring Recommendation ──
  if (norm?.hiring_recommendation_rationale) {
    y = pgBreak(doc, y, 16, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Hiring Recommendation', y, ml, cw, C.heroBlue)
    // Show rating badge
    if (norm.hiring_recommendation) {
      doc.setFontSize(9)
      doc.setFont('helvetica', 'bold')
      setT(doc, scoreColor(matchS))
      doc.text(`Recommendation: ${norm.hiring_recommendation}`, ml + 8, y)
      y += 5
    }
    y = wrapText(doc, sanitizeForHelvetica(norm.hiring_recommendation_rationale), ml + 8, y, cw - 10, 8, C.grayDark, 3.6, pw, ph, pn, cn)
    y += 3
  }

  // ── Ideal Roles ──
  if (norm?.ideal_roles && norm.ideal_roles.length > 0) {
    y = pgBreak(doc, y, 14, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Ideal Roles', y, ml, cw, C.cyan as unknown as C3)
    doc.setFontSize(8)
    doc.setFont('helvetica', 'normal')
    setT(doc, C.grayDark)
    doc.text(sanitizeForHelvetica(norm.ideal_roles.slice(0, 6).join('  |  ')), ml + 8, y)
    y += 5
  }

  // ── Salary Range Estimate ──
  if (norm?.salary_range_estimate) {
    y = pgBreak(doc, y, 10, pw, ph, pn, cn)
    doc.setFontSize(8)
    doc.setFont('helvetica', 'bold')
    setT(doc, C.navy)
    doc.text('Salary Range Estimate:', ml + 8, y)
    doc.setFont('helvetica', 'normal')
    setT(doc, C.grayDark)
    doc.text(sanitizeForHelvetica(norm.salary_range_estimate), ml + 42, y)
    y += 5
  }

  // ── Education ──
  if (candidate.education && candidate.education.length > 0) {
    y = pgBreak(doc, y, 14, pw, ph, pn, cn)
    y = sectionTitle(doc, 'Education', y, ml, cw, C.heroBlue)
    if (norm?.education_assessment) {
      y = wrapText(doc, sanitizeForHelvetica(norm.education_assessment), ml + 8, y, cw - 10, 7.5, C.grayDark, 3.5, pw, ph, pn, cn)
      y += 2
    }
    for (const edu of candidate.education.slice(0, 5)) {
      // Skip garbled/invalid education entries
      const degreeText = sanitizeForHelvetica(edu.degree || '').trim()
      if (!degreeText || degreeText.length < 2) continue
      // Skip entries that look like section headers or garbled extraction
      const loDeg = degreeText.toLowerCase()
      if (/^(education|skills|experience|summary|objective|profile|references|projects|hobbies|interests)$/i.test(loDeg)) continue
      if (loDeg.split(' ').length > 12) continue // suspiciously long "degree"

      y = pgBreak(doc, y, 8, pw, ph, pn, cn)
      doc.setFontSize(8)
      doc.setFont('helvetica', 'bold')
      setT(doc, C.black)
      const fieldText = edu.field ? sanitizeForHelvetica(edu.field).trim() : ''
      const degreeDisplay = fieldText && fieldText.length > 2 ? `${degreeText} in ${fieldText}` : degreeText
      doc.text(degreeDisplay, ml + 8, y)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.gray)
      const instText = sanitizeForHelvetica(edu.institution || '').trim()
      const yearText = sanitizeForHelvetica(edu.year || '').trim()
      doc.text(`${instText || 'N/A'}${yearText ? '  ·  ' + yearText : ''}`, ml + 8, y + 4)
      y += 8
    }
    y += 2
  }

  // ── Certifications & Languages ──
  if ((candidate.certifications?.length || 0) > 0 || (candidate.languages?.length || 0) > 0) {
    y = pgBreak(doc, y, 10, pw, ph, pn, cn)
    if (candidate.certifications && candidate.certifications.length > 0) {
      doc.setFontSize(7.5)
      doc.setFont('helvetica', 'bold')
      setT(doc, C.navy)
      doc.text('Certifications:', ml + 8, y)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.black)
      const certText = sanitizeForHelvetica(candidate.certifications.slice(0, 6).join('  |  '))
      const certLines = doc.splitTextToSize(certText, cw - 36)
      for (const cl of certLines) {
        doc.text(cl, ml + 30, y)
        y += 3.5
      }
      y += 1
    }
    if (candidate.languages && candidate.languages.length > 0) {
      doc.setFontSize(7.5)
      doc.setFont('helvetica', 'bold')
      setT(doc, C.navy)
      doc.text('Languages:', ml + 8, y)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.black)
      doc.text(sanitizeForHelvetica(candidate.languages.join('  |  ')), ml + 30, y)
      y += 4.5
    }
  }

  // ── Footer for last assessment page ──
  await drawFooterAsync(doc, pw, ph, pn.value)

  // ═══════════════════════════════════════════
  // ORIGINAL RESUME — PDF merge, text fallback, or fetch from candidate endpoint
  // ═══════════════════════════════════════════
  let resumeMerged = false
  let resumeFileExists = false

  if (candidate.id) {
    // ── Step 1: Try fetching the actual resume file ──
    try {
      const res = await authFetch(`${config.endpoints.candidates}/${candidate.id}/resume`)
      if (res.ok) {
        const ct = res.headers.get('content-type') || ''
        const resumeBytes = await res.arrayBuffer()

        if (resumeBytes.byteLength > 100) {
          resumeFileExists = true

          if (ct.includes('pdf')) {
            // ── PDF resume: merge pages directly ──
            try {
              const assessmentBytes = doc.output('arraybuffer')
              const merged = await PDFDocument.create()

              const aDoc = await PDFDocument.load(assessmentBytes)
              const aPages = await merged.copyPages(aDoc, aDoc.getPageIndices())
              aPages.forEach((p) => merged.addPage(p))

              // Separator page
              const sep = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'letter' })
              const sw = sep.internal.pageSize.getWidth()
              const sh = sep.internal.pageSize.getHeight()
              fillR(sep, 0, 0, sw, sh, C.navy)
              const bw = 140, bh = 60
              const bx = (sw - bw) / 2, by = (sh - bh) / 2 - 10
              fillR(sep, bx, by, bw, bh, C.white, 8)
              gradientH(sep, bx, by + bh - 4, bw, 4, [C.heroBlue, C.cyan, C.gradPurple])
              sep.setFontSize(22)
              sep.setFont('helvetica', 'bold')
              setT(sep, C.navy)
              sep.text('ORIGINAL RESUME', sw / 2, by + 22, { align: 'center' })
              sep.setFontSize(11)
              sep.setFont('helvetica', 'normal')
              setT(sep, C.gray)
              sep.text(cn, sw / 2, by + 34, { align: 'center' })
              sep.setFontSize(9)
              sep.text('Attached untouched from submission', sw / 2, by + 44, { align: 'center' })
              const sepBytes = sep.output('arraybuffer')
              const sepDoc = await PDFDocument.load(sepBytes)
              const sepPages = await merged.copyPages(sepDoc, [0])
              sepPages.forEach((p) => merged.addPage(p))

              const rDoc = await PDFDocument.load(resumeBytes, { ignoreEncryption: true })
              const rPages = await merged.copyPages(rDoc, rDoc.getPageIndices())
              rPages.forEach((p) => merged.addPage(p))
              resumeMerged = true

              const finalBytes = await merged.save()
              const blob = new Blob([finalBytes as unknown as ArrayBuffer], { type: 'application/pdf' })
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url
              a.download = `Efforts_Assessment_${cn.replace(/[^a-zA-Z0-9\s]/g, '').replace(/\s+/g, '_')}.pdf`
              document.body.appendChild(a)
              a.click()
              document.body.removeChild(a)
              URL.revokeObjectURL(url)
              return
            } catch (e) {
              console.warn('Could not merge resume PDF, will use text fallback:', e)
            }
          }
          // For DOCX or failed PDF merge — we still have the file, note it exists
        }
      }
    } catch (err) {
      console.warn('Failed to fetch resume file:', err)
    }

    // ── Step 2: If resumeText is empty, fetch from candidate endpoint ──
    if (!candidate.resumeText || candidate.resumeText.trim().length < 30) {
      try {
        const cRes = await authFetch(`${config.endpoints.candidates}/${candidate.id}`)
        if (cRes.ok) {
          const full = await cRes.json()
          const fetched = (full.resume_text || full.resumeText || '').trim()
          if (fetched.length > 30) {
            candidate = { ...candidate, resumeText: fetched }
          }
        }
      } catch { /* ignore */ }
    }
  }

  // ── Step 3: Render resume text if available (fallback when PDF merge didn't work) ──
  if (!resumeMerged) {
    let rawResumeText = candidate.resumeText?.trim() || ''
    const isMoji = detectMojibake(rawResumeText)

    // Detect and repair spaced-character corruption before sanitising
    if (detectSpacedCharCorruption(rawResumeText)) {
      console.warn('Detected spaced-char corruption in resume text, collapsing...')
      rawResumeText = collapseSpacedChars(rawResumeText)
    }

    const resumeText = sanitizeForHelvetica(rawResumeText)

    if (resumeText.length > 30 && !isMoji) {
      // We have renderable text — show it as "Original CV / Resume"
      doc.addPage()
      pn.value++

      // Separator banner
      fillR(doc, 0, 0, pw, 14, C.navy)
      doc.setFontSize(10)
      doc.setFont('helvetica', 'bold')
      setT(doc, C.white)
      doc.text('Original CV / Resume', ml, 9)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'normal')
      setT(doc, [180, 210, 255] as C3)
      doc.text(cn, pw - ml, 9, { align: 'right' })
      gradientH(doc, 0, 14, pw, 0.8, [C.heroBlue, C.cyan, C.gradPurple])

      if (resumeFileExists) {
        doc.setFontSize(6.5)
        setT(doc, C.gray)
        doc.text('Note: Original file is available for separate download from the platform.', ml, 18.5)
        y = 22
      } else {
        y = 18
      }

      doc.setFontSize(8)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.black)

      const rLines = doc.splitTextToSize(resumeText, cw)
      const rlh = 3.5

      for (let i = 0; i < rLines.length; i++) {
        if (y + rlh > ph - 22) {
          drawFooter(doc, pw, ph, pn.value)
          doc.addPage()
          pn.value++
          fillR(doc, 0, 0, pw, 10, C.navy)
          doc.setFontSize(7)
          doc.setFont('helvetica', 'bold')
          setT(doc, C.white)
          doc.text('Original CV / Resume (continued)', ml, 7)
          doc.setFont('helvetica', 'normal')
          setT(doc, [180, 210, 255] as C3)
          doc.text(cn, pw - ml, 7, { align: 'right' })
          gradientH(doc, 0, 10, pw, 0.6, [C.heroBlue, C.cyan, C.gradPurple])
          y = 16
          doc.setFontSize(8)
          doc.setFont('helvetica', 'normal')
          setT(doc, C.black)
        }
        doc.text(rLines[i], ml, y)
        y += rlh
      }
      drawFooter(doc, pw, ph, pn.value)
    } else if (resumeFileExists) {
      // Resume file exists but couldn't be rendered as text — add a note page
      doc.addPage()
      pn.value++
      fillR(doc, 0, 0, pw, 14, C.navy)
      doc.setFontSize(10)
      doc.setFont('helvetica', 'bold')
      setT(doc, C.white)
      doc.text('Original Resume', ml, 9)
      gradientH(doc, 0, 14, pw, 0.8, [C.heroBlue, C.cyan, C.gradPurple])

      doc.setFontSize(9)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.grayDark)
      doc.text('The original resume file is available for download from the Efforts platform.', ml, 26)
      doc.text('It could not be embedded in this PDF because it is in a non-PDF format (e.g. DOCX).', ml, 32)
      doc.setFontSize(8)
      setT(doc, C.gray)
      doc.text('To download: Go to Candidate Profile > Click "Download Resume"', ml, 42)
      drawFooter(doc, pw, ph, pn.value)
    }
  }

  const safeName = cn.replace(/[^a-zA-Z0-9\s]/g, '').replace(/\s+/g, '_')
  doc.save(`Efforts_Assessment_${safeName}.pdf`)
}

/* ═══════════════════════════════════════════════════════════════════
   Quick Profile PDF
   ═══════════════════════════════════════════════════════════════════ */
export async function generateQuickProfilePDF(candidate: CandidateData): Promise<void> {
  if (candidate.id) {
    try {
      const [cRes, aRes] = await Promise.all([
        authFetch(`${config.endpoints.candidates}/${candidate.id}`),
        authFetch(`${config.endpoints.candidates}/${candidate.id}/ai-analysis`).catch(() => null),
      ])
      let ai: AIAnalysisData | null = null
      if (aRes?.ok) { try { ai = await aRes.json() } catch { /* skip */ } }
      if (cRes.ok) {
        const full = await cRes.json()
        const enriched: CandidateData = {
          ...candidate,
          hasResume: full.hasResume ?? candidate.hasResume,
          summary: full.summary || candidate.summary || '',
          education: full.education || [],
          workHistory: (full.workHistory || []).map((j: Record<string, string>) => ({
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
        return generateCandidatePDF(enriched, ai || full.ai_analysis || null)
      }
    } catch (err) {
      console.error('Failed to fetch full data for PDF:', err)
    }
  }
  return generateCandidatePDF(candidate, null)
}

/* ═══════════════════════════════════════════════════════════════════
   Download Original Resume
   ═══════════════════════════════════════════════════════════════════ */
export async function downloadOriginalResume(candidate: CandidateData): Promise<void> {
  const safeName = candidate.name.replace(/[^a-zA-Z0-9\s]/g, '').replace(/\s+/g, '_')

  if (candidate.id) {
    try {
      const res = await authFetch(`${config.endpoints.candidates}/${candidate.id}/resume`)
      if (res.ok) {
        const blob = await res.blob()
        if (blob.size > 100) {
          const ct = res.headers.get('content-type') || ''
          const ext = ct.includes('pdf') ? 'pdf' : ct.includes('word') ? 'docx' : 'pdf'
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
      console.warn('Could not fetch original resume:', err)
    }
  }

  let resumeText = candidate.resumeText?.trim() || ''
  if (!resumeText && candidate.id) {
    try {
      const res = await authFetch(`${config.endpoints.candidates}/${candidate.id}`)
      if (res.ok) {
        const full = await res.json()
        resumeText = (full.resume_text || full.resumeText || '').trim()
      }
    } catch { /* ignore */ }
  }

  if (!resumeText || resumeText.length < 20) throw new Error('No resume available for this candidate')
  if (detectMojibake(resumeText)) throw new Error('Resume text contains encoding errors. Original file not available.')

  // Repair spaced-character corruption before sanitizing
  if (detectSpacedCharCorruption(resumeText)) {
    resumeText = collapseSpacedChars(resumeText)
  }

  // Sanitize for Helvetica rendering
  resumeText = sanitizeForHelvetica(resumeText)
  if (resumeText.length < 20) throw new Error('Resume text is not renderable after sanitization.')

  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'letter' })
  const pw = doc.internal.pageSize.getWidth()
  const ph = doc.internal.pageSize.getHeight()
  const ml = 20
  const cw = pw - ml * 2

  fillR(doc, 0, 0, pw, 12, C.navy)
  doc.setFontSize(10)
  doc.setFont('helvetica', 'bold')
  setT(doc, C.white)
  doc.text(`Resume — ${candidate.name}  |  Efforts Solutions`, ml, 8)
  doc.setFontSize(7)
  doc.setFont('helvetica', 'normal')
  setT(doc, [180, 210, 255] as C3)
  doc.text(new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' }), pw - ml, 8, { align: 'right' })
  gradientH(doc, 0, 12, pw, 0.8, [C.heroBlue, C.cyan, C.gradPurple])

  let y = 18
  doc.setFontSize(9)
  doc.setFont('helvetica', 'normal')
  setT(doc, C.black)

  const lines = doc.splitTextToSize(resumeText, cw)
  const lh = 4

  for (let i = 0; i < lines.length; i++) {
    if (y + lh > ph - 15) {
      doc.addPage()
      fillR(doc, 0, 0, pw, 8, C.navy)
      doc.setFontSize(7)
      doc.setFont('helvetica', 'bold')
      setT(doc, C.white)
      doc.text(`Resume — ${candidate.name} (continued)`, ml, 5.5)
      gradientH(doc, 0, 8, pw, 0.6, [C.heroBlue, C.cyan, C.gradPurple])
      y = 14
      doc.setFontSize(9)
      doc.setFont('helvetica', 'normal')
      setT(doc, C.black)
    }
    doc.text(lines[i], ml, y)
    y += lh
  }

  doc.save(`Resume_${safeName}.pdf`)
}
