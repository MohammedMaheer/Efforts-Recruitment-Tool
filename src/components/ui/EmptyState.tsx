/**
 * Empty State Component
 * Consistent empty state UI across the application
 */
import { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { LucideIcon, FileX, Users, Mail, Search, Inbox } from 'lucide-react';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
  size?: 'sm' | 'md' | 'lg';
}

const sizeClasses = {
  sm: {
    container: 'p-6',
    icon: 'w-10 h-10',
    iconWrapper: 'w-16 h-16',
    title: 'text-base',
    description: 'text-sm',
  },
  md: {
    container: 'p-8',
    icon: 'w-12 h-12',
    iconWrapper: 'w-20 h-20',
    title: 'text-lg',
    description: 'text-sm',
  },
  lg: {
    container: 'p-12',
    icon: 'w-16 h-16',
    iconWrapper: 'w-24 h-24',
    title: 'text-xl',
    description: 'text-base',
  },
};

/**
 * Generic empty state component
 */
export function EmptyState({
  icon: Icon = FileX,
  title,
  description,
  action,
  className,
  size = 'md',
}: EmptyStateProps) {
  const sizes = sizeClasses[size];

  return (
    <div className={cn('text-center', sizes.container, className)}>
      <div
        className={cn(
          'bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4',
          sizes.iconWrapper
        )}
      >
        <Icon className={cn('text-gray-400', sizes.icon)} />
      </div>
      
      <h3 className={cn('font-semibold text-gray-900 mb-2', sizes.title)}>
        {title}
      </h3>
      
      {description && (
        <p className={cn('text-gray-600 mb-4 max-w-sm mx-auto', sizes.description)}>
          {description}
        </p>
      )}
      
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// Pre-configured empty states for common scenarios

interface PresetEmptyStateProps {
  action?: ReactNode;
  className?: string;
}

export function NoCandidatesState({ action, className }: PresetEmptyStateProps) {
  return (
    <EmptyState
      icon={Users}
      title="No candidates yet"
      description="Start by uploading resumes or connecting your email to automatically import candidates."
      action={action}
      className={className}
    />
  );
}

export function NoSearchResultsState({ action, className }: PresetEmptyStateProps) {
  return (
    <EmptyState
      icon={Search}
      title="No results found"
      description="Try adjusting your search or filter criteria to find what you're looking for."
      action={action}
      className={className}
    />
  );
}

export function NoEmailsState({ action, className }: PresetEmptyStateProps) {
  return (
    <EmptyState
      icon={Mail}
      title="No emails connected"
      description="Connect your email account to automatically import candidate applications."
      action={action}
      className={className}
    />
  );
}

export function EmptyInboxState({ action, className }: PresetEmptyStateProps) {
  return (
    <EmptyState
      icon={Inbox}
      title="Inbox is empty"
      description="No new applications have been received yet. Check back later or upload resumes manually."
      action={action}
      className={className}
    />
  );
}

export function NoShortlistState({ action, className }: PresetEmptyStateProps) {
  return (
    <EmptyState
      icon={Users}
      title="No shortlisted candidates"
      description="Add candidates to your shortlist by clicking the star icon on their profile."
      action={action}
      className={className}
    />
  );
}
