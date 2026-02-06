/**
 * Utility Functions
 * Comprehensive utility library for the application
 */
import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

// ============================================================================
// Tailwind Utilities
// ============================================================================

/**
 * Merge Tailwind CSS classes with proper precedence
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ============================================================================
// Date Formatting
// ============================================================================

/**
 * Format date to readable string
 */
export function formatDate(date: Date | string | null | undefined): string {
  if (!date) return '-'
  try {
    const d = new Date(date)
    if (isNaN(d.getTime())) return '-'
    return d.toLocaleDateString('en-US', { 
      month: 'short', 
      day: 'numeric', 
      year: 'numeric' 
    })
  } catch {
    return '-'
  }
}

/**
 * Format date to ISO string for inputs
 */
export function formatDateForInput(date: Date | string | null): string {
  if (!date) return ''
  try {
    const d = new Date(date)
    return d.toISOString().split('T')[0]
  } catch {
    return ''
  }
}

/**
 * Get relative time string (e.g., "2 hours ago")
 */
export function getRelativeTime(date: Date | string): string {
  const d = new Date(date)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHour = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHour / 24)
  const diffWeek = Math.floor(diffDay / 7)
  const diffMonth = Math.floor(diffDay / 30)

  if (diffSec < 60) return 'Just now'
  if (diffMin < 60) return `${diffMin} minute${diffMin === 1 ? '' : 's'} ago`
  if (diffHour < 24) return `${diffHour} hour${diffHour === 1 ? '' : 's'} ago`
  if (diffDay < 7) return `${diffDay} day${diffDay === 1 ? '' : 's'} ago`
  if (diffWeek < 4) return `${diffWeek} week${diffWeek === 1 ? '' : 's'} ago`
  if (diffMonth < 12) return `${diffMonth} month${diffMonth === 1 ? '' : 's'} ago`
  return formatDate(date)
}

// ============================================================================
// Score & Status Formatting
// ============================================================================

/**
 * Format match score as percentage
 */
export function formatMatchScore(score: number | null | undefined): string {
  if (score === null || score === undefined) return '-'
  return `${Math.round(score)}%`
}

/**
 * Get score color class
 */
export function getMatchScoreColor(score: number): string {
  if (score >= 70) return 'text-emerald-600'
  if (score >= 40) return 'text-amber-600'
  return 'text-red-500'
}

/**
 * Get score background color class
 */
export function getMatchScoreBgColor(score: number): string {
  if (score >= 70) return 'bg-emerald-50'
  if (score >= 40) return 'bg-amber-50'
  return 'bg-red-50'
}

/**
 * Get score ring/border color
 */
export function getMatchScoreRingColor(score: number): string {
  if (score >= 70) return 'ring-emerald-500'
  if (score >= 40) return 'ring-amber-500'
  return 'ring-red-500'
}

/**
 * Get status badge colors
 */
export function getStatusBadgeColor(status: string): { bg: string; text: string; border: string } {
  switch (status.toLowerCase()) {
    case 'strong':
      return { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' }
    case 'partial':
      return { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' }
    case 'reject':
    case 'weak':
      return { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-200' }
    case 'shortlisted':
      return { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' }
    case 'interviewing':
      return { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' }
    case 'offered':
      return { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' }
    case 'hired':
      return { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' }
    case 'new':
      return { bg: 'bg-sky-50', text: 'text-sky-700', border: 'border-sky-200' }
    default:
      return { bg: 'bg-gray-50', text: 'text-gray-700', border: 'border-gray-200' }
  }
}

/**
 * Get match tier from score
 */
export function getMatchTier(score: number): 'Strong' | 'Partial' | 'Weak' {
  if (score >= 70) return 'Strong'
  if (score >= 40) return 'Partial'
  return 'Weak'
}

// ============================================================================
// String Utilities
// ============================================================================

/**
 * Truncate text with ellipsis
 */
export function truncate(text: string, length: number): string {
  if (!text || text.length <= length) return text
  return text.slice(0, length).trim() + '...'
}

/**
 * Get initials from name
 */
export function getInitials(name: string): string {
  if (!name) return '?'
  const parts = name.trim().split(/\s+/)
  if (parts.length === 1) return parts[0].charAt(0).toUpperCase()
  return (parts[0].charAt(0) + parts[parts.length - 1].charAt(0)).toUpperCase()
}

/**
 * Capitalize first letter
 */
export function capitalize(text: string): string {
  if (!text) return ''
  return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase()
}

/**
 * Title case string
 */
export function titleCase(text: string): string {
  if (!text) return ''
  return text
    .toLowerCase()
    .split(' ')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/**
 * Slugify string
 */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/[\s_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

// ============================================================================
// Number Utilities
// ============================================================================

/**
 * Format number with commas
 */
export function formatNumber(num: number): string {
  return num.toLocaleString('en-US')
}

/**
 * Format bytes to human readable
 */
export function formatBytes(bytes: number, decimals = 2): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i]
}

/**
 * Clamp number between min and max
 */
export function clamp(num: number, min: number, max: number): number {
  return Math.min(Math.max(num, min), max)
}

/**
 * Format percentage
 */
export function formatPercent(value: number, decimals = 0): string {
  return `${value.toFixed(decimals)}%`
}

// ============================================================================
// Array Utilities
// ============================================================================

/**
 * Group array by key
 */
export function groupBy<T>(array: T[], key: keyof T): Record<string, T[]> {
  return array.reduce((groups, item) => {
    const groupKey = String(item[key])
    if (!groups[groupKey]) {
      groups[groupKey] = []
    }
    groups[groupKey].push(item)
    return groups
  }, {} as Record<string, T[]>)
}

/**
 * Unique array by key
 */
export function uniqueBy<T>(array: T[], key: keyof T): T[] {
  const seen = new Set()
  return array.filter(item => {
    const value = item[key]
    if (seen.has(value)) return false
    seen.add(value)
    return true
  })
}

/**
 * Chunk array into smaller arrays
 */
export function chunk<T>(array: T[], size: number): T[][] {
  const chunks: T[][] = []
  for (let i = 0; i < array.length; i += size) {
    chunks.push(array.slice(i, i + size))
  }
  return chunks
}

// ============================================================================
// Validation Utilities
// ============================================================================

/**
 * Validate email format
 */
export function isValidEmail(email: string): boolean {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
  return emailRegex.test(email)
}

/**
 * Validate phone number (basic)
 */
export function isValidPhone(phone: string): boolean {
  const digits = phone.replace(/\D/g, '')
  return digits.length >= 7 && digits.length <= 15
}

/**
 * Check if string is empty or whitespace
 */
export function isEmpty(value: string | null | undefined): boolean {
  return !value || value.trim().length === 0
}

// ============================================================================
// Async Utilities
// ============================================================================

/**
 * Debounce function
 */
export function debounce<T extends (...args: unknown[]) => unknown>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeoutId: ReturnType<typeof setTimeout> | null = null
  
  return (...args: Parameters<T>) => {
    if (timeoutId) {
      clearTimeout(timeoutId)
    }
    timeoutId = setTimeout(() => {
      func(...args)
    }, wait)
  }
}

/**
 * Throttle function
 */
export function throttle<T extends (...args: unknown[]) => unknown>(
  func: T,
  limit: number
): (...args: Parameters<T>) => void {
  let inThrottle = false
  
  return (...args: Parameters<T>) => {
    if (!inThrottle) {
      func(...args)
      inThrottle = true
      setTimeout(() => {
        inThrottle = false
      }, limit)
    }
  }
}

/**
 * Sleep for specified milliseconds
 */
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

// ============================================================================
// Category Colors
// ============================================================================

export const categoryColors: Record<string, { bg: string; text: string; border: string }> = {
  'Software Engineer': { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
  'DevOps Engineer': { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' },
  'Data Scientist': { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
  'Marketing': { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200' },
  'Sales': { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
  'Product Manager': { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' },
  'HR': { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200' },
  'Finance': { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' },
  'Customer Support': { bg: 'bg-cyan-50', text: 'text-cyan-700', border: 'border-cyan-200' },
  'Design': { bg: 'bg-rose-50', text: 'text-rose-700', border: 'border-rose-200' },
  'General': { bg: 'bg-gray-50', text: 'text-gray-700', border: 'border-gray-200' },
}

/**
 * Get category color scheme
 */
export function getCategoryColor(category: string): { bg: string; text: string; border: string } {
  return categoryColors[category] || categoryColors['General']
}
