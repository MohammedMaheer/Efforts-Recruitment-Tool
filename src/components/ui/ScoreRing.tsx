/**
 * Circular Score Ring Component
 * Shared SVG ring for displaying percentage scores with color coding.
 */
import { getScoreColor, getScoreRingColor } from '@/lib/utils'

interface ScoreRingProps {
  score: number
  size?: number
}

export function ScoreRing({ score, size = 56 }: ScoreRingProps) {
  const radius = (size - 8) / 2
  const circumference = 2 * Math.PI * radius
  const dashoffset = circumference - (score / 100) * circumference
  return (
    <div className="relative" style={{ width: size, height: size }} role="img" aria-label={`Match score: ${Math.round(score)}%`}>
      <svg className="transform -rotate-90" width={size} height={size} aria-hidden="true">
        <circle cx={size/2} cy={size/2} r={radius} strokeWidth="4" fill="none" className="stroke-gray-200" />
        <circle
          cx={size/2} cy={size/2} r={radius} strokeWidth="4" fill="none"
          className={getScoreRingColor(score)}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashoffset}
          style={{ transition: 'stroke-dashoffset 0.6s ease' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={`text-sm font-bold ${getScoreColor(score)}`}>{Math.round(score)}%</span>
      </div>
    </div>
  )
}
