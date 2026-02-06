/**
 * Loading Components
 * Consistent loading states across the application
 */
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg' | 'xl';
  className?: string;
}

const sizeClasses = {
  sm: 'w-4 h-4',
  md: 'w-6 h-6',
  lg: 'w-8 h-8',
  xl: 'w-12 h-12',
};

/**
 * Simple spinning loader
 */
export function Spinner({ size = 'md', className }: SpinnerProps) {
  return (
    <Loader2
      className={cn(
        'animate-spin text-primary-600',
        sizeClasses[size],
        className
      )}
    />
  );
}

interface LoadingOverlayProps {
  message?: string;
  className?: string;
}

/**
 * Full-page loading overlay
 */
export function LoadingOverlay({ message = 'Loading...', className }: LoadingOverlayProps) {
  return (
    <div
      className={cn(
        'fixed inset-0 z-50 flex items-center justify-center bg-white/80 backdrop-blur-sm',
        className
      )}
    >
      <div className="flex flex-col items-center gap-4">
        <Spinner size="xl" />
        <p className="text-gray-600 font-medium">{message}</p>
      </div>
    </div>
  );
}

interface LoadingCardProps {
  rows?: number;
  className?: string;
}

/**
 * Skeleton loading card
 */
export function LoadingCard({ rows = 3, className }: LoadingCardProps) {
  return (
    <div
      className={cn(
        'bg-white rounded-xl border border-gray-100 p-6 animate-pulse',
        className
      )}
    >
      <div className="flex items-center gap-4 mb-4">
        <div className="w-12 h-12 bg-gray-200 rounded-full" />
        <div className="flex-1">
          <div className="h-4 bg-gray-200 rounded w-1/3 mb-2" />
          <div className="h-3 bg-gray-100 rounded w-1/4" />
        </div>
      </div>
      
      <div className="space-y-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div
            key={i}
            className="h-3 bg-gray-100 rounded"
            style={{ width: `${100 - i * 15}%` }}
          />
        ))}
      </div>
    </div>
  );
}

interface LoadingTableProps {
  rows?: number;
  columns?: number;
  className?: string;
}

/**
 * Skeleton loading table
 */
export function LoadingTable({ rows = 5, columns = 5, className }: LoadingTableProps) {
  return (
    <div className={cn('bg-white rounded-xl border border-gray-100 overflow-hidden', className)}>
      {/* Header */}
      <div className="bg-gray-50 border-b border-gray-100 p-4 flex gap-4">
        {Array.from({ length: columns }).map((_, i) => (
          <div
            key={i}
            className="h-4 bg-gray-200 rounded flex-1 animate-pulse"
          />
        ))}
      </div>
      
      {/* Rows */}
      <div className="divide-y divide-gray-100">
        {Array.from({ length: rows }).map((_, rowIndex) => (
          <div key={rowIndex} className="p-4 flex gap-4 items-center">
            {Array.from({ length: columns }).map((_, colIndex) => (
              <div
                key={colIndex}
                className="h-4 bg-gray-100 rounded flex-1 animate-pulse"
                style={{
                  animationDelay: `${(rowIndex * columns + colIndex) * 50}ms`,
                }}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

interface LoadingDotsProps {
  className?: string;
}

/**
 * Animated loading dots
 */
export function LoadingDots({ className }: LoadingDotsProps) {
  return (
    <span className={cn('inline-flex gap-1', className)}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 bg-current rounded-full animate-bounce"
          style={{ animationDelay: `${i * 150}ms` }}
        />
      ))}
    </span>
  );
}

interface InlineLoadingProps {
  text?: string;
  className?: string;
}

/**
 * Inline loading indicator with text
 */
export function InlineLoading({ text = 'Loading', className }: InlineLoadingProps) {
  return (
    <span className={cn('inline-flex items-center gap-2 text-gray-500', className)}>
      <Spinner size="sm" />
      <span>{text}</span>
      <LoadingDots />
    </span>
  );
}
