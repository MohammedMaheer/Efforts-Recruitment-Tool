/**
 * Score Circle Component
 * Animated circular progress indicator for match scores
 */
import { useMemo } from 'react';
import { cn } from '@/lib/utils';

interface ScoreCircleProps {
  score: number;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  showLabel?: boolean;
  animated?: boolean;
  className?: string;
}

const sizeConfig = {
  sm: { diameter: 40, strokeWidth: 4, fontSize: 'text-xs', labelSize: 'text-[8px]' },
  md: { diameter: 56, strokeWidth: 5, fontSize: 'text-sm', labelSize: 'text-[10px]' },
  lg: { diameter: 80, strokeWidth: 6, fontSize: 'text-lg', labelSize: 'text-xs' },
  xl: { diameter: 120, strokeWidth: 8, fontSize: 'text-2xl', labelSize: 'text-sm' },
};

/**
 * Get color based on score
 */
function getScoreColor(score: number): { stroke: string; text: string; bg: string } {
  if (score >= 70) {
    return {
      stroke: 'stroke-emerald-500',
      text: 'text-emerald-600',
      bg: 'bg-emerald-50',
    };
  }
  if (score >= 40) {
    return {
      stroke: 'stroke-amber-500',
      text: 'text-amber-600',
      bg: 'bg-amber-50',
    };
  }
  return {
    stroke: 'stroke-red-500',
    text: 'text-red-600',
    bg: 'bg-red-50',
  };
}

/**
 * Circular progress indicator for displaying scores
 */
export function ScoreCircle({
  score,
  size = 'md',
  showLabel = false,
  animated = true,
  className,
}: ScoreCircleProps) {
  const config = sizeConfig[size];
  const colors = getScoreColor(score);
  
  const { circumference, offset, center, radius } = useMemo(() => {
    const r = (config.diameter - config.strokeWidth) / 2;
    const c = 2 * Math.PI * r;
    const o = c - (score / 100) * c;
    
    return {
      radius: r,
      center: config.diameter / 2,
      circumference: c,
      offset: o,
    };
  }, [score, config.diameter, config.strokeWidth]);

  return (
    <div className={cn('relative inline-flex items-center justify-center', className)}>
      <svg
        width={config.diameter}
        height={config.diameter}
        className="transform -rotate-90"
      >
        {/* Background circle */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          className="stroke-gray-200"
          strokeWidth={config.strokeWidth}
        />
        
        {/* Progress circle */}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          className={cn(
            colors.stroke,
            animated && 'transition-all duration-1000 ease-out'
          )}
          strokeWidth={config.strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{
            filter: 'drop-shadow(0 1px 2px rgb(0 0 0 / 0.1))',
          }}
        />
      </svg>
      
      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={cn('font-bold', config.fontSize, colors.text)}>
          {Math.round(score)}
        </span>
        {showLabel && (
          <span className={cn('text-gray-500', config.labelSize)}>
            Match
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Compact score badge for tables and lists
 */
interface ScoreBadgeProps {
  score: number;
  className?: string;
}

export function ScoreBadge({ score, className }: ScoreBadgeProps) {
  const colors = getScoreColor(score);
  
  return (
    <div
      className={cn(
        'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full',
        colors.bg,
        className
      )}
    >
      <div className={cn('w-2 h-2 rounded-full', colors.stroke.replace('stroke-', 'bg-'))} />
      <span className={cn('text-sm font-semibold', colors.text)}>
        {Math.round(score)}%
      </span>
    </div>
  );
}

/**
 * Horizontal score bar for alternative display
 */
interface ScoreBarProps {
  score: number;
  showLabel?: boolean;
  className?: string;
}

export function ScoreBar({ score, showLabel = true, className }: ScoreBarProps) {
  const colors = getScoreColor(score);
  
  return (
    <div className={cn('w-full', className)}>
      {showLabel && (
        <div className="flex justify-between items-center mb-1">
          <span className="text-xs text-gray-500">Match Score</span>
          <span className={cn('text-sm font-semibold', colors.text)}>
            {Math.round(score)}%
          </span>
        </div>
      )}
      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500 ease-out',
            colors.stroke.replace('stroke-', 'bg-')
          )}
          style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
        />
      </div>
    </div>
  );
}
