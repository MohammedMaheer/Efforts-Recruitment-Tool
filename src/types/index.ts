/**
 * Core Type Definitions
 * Single source of truth for all application types
 */

// ============================================================================
// Enums & Constants
// ============================================================================

export const CandidateStatus = {
  NEW: 'New',
  REVIEWED: 'Reviewed',
  SHORTLISTED: 'Shortlisted',
  INTERVIEWING: 'Interviewing',
  OFFERED: 'Offered',
  HIRED: 'Hired',
  REJECTED: 'Rejected',
  WITHDRAWN: 'Withdrawn',
} as const;

export type CandidateStatusType = typeof CandidateStatus[keyof typeof CandidateStatus];

export const MatchTier = {
  STRONG: 'Strong',
  PARTIAL: 'Partial',
  WEAK: 'Weak',
} as const;

export type MatchTierType = typeof MatchTier[keyof typeof MatchTier];

export const JobCategory = {
  SOFTWARE_ENGINEER: 'Software Engineer',
  DEVOPS_ENGINEER: 'DevOps Engineer',
  DATA_SCIENTIST: 'Data Scientist',
  PRODUCT_MANAGER: 'Product Manager',
  MARKETING: 'Marketing',
  SALES: 'Sales',
  HR: 'HR',
  FINANCE: 'Finance',
  DESIGN: 'Design',
  CUSTOMER_SUPPORT: 'Customer Support',
  GENERAL: 'General',
} as const;

export type JobCategoryType = typeof JobCategory[keyof typeof JobCategory];

// ============================================================================
// Base Types
// ============================================================================

export interface Education {
  degree: string;
  institution: string;
  year: string;
  field?: string;
  gpa?: number;
}

export interface WorkExperience {
  title: string;
  company: string;
  duration: string;
  description: string;
  startDate?: string;
  endDate?: string;
  isCurrent?: boolean;
}

export interface AIEvaluation {
  strengths: string[];
  gaps: string[];
  recommendation: string;
  confidenceScore?: number;
}

// ============================================================================
// Candidate Types
// ============================================================================

export interface Candidate {
  id: string;
  name: string;
  email: string;
  phone: string;
  location: string;
  experience: number;
  matchScore: number;
  status: CandidateStatusType;
  skills: string[];
  resumeUrl: string;
  appliedDate: string;
  avatar?: string;
  summary: string;
  isShortlisted?: boolean;
  hasResume: boolean;
  jobCategory: JobCategoryType | string;
  linkedin?: string;
  education: Education[];
  workHistory: WorkExperience[];
  evaluation?: AIEvaluation;
  lastUpdated?: string;
}

export interface CandidateFilters {
  search?: string;
  status?: CandidateStatusType | 'all';
  minScore?: number;
  maxScore?: number;
  minExperience?: number;
  maxExperience?: number;
  jobCategory?: JobCategoryType | 'all';
  dateStart?: string;
  dateEnd?: string;
}

export type SortOption = 
  | 'score-desc' 
  | 'score-asc' 
  | 'date-newest' 
  | 'date-oldest' 
  | 'name-asc' 
  | 'name-desc'
  | 'experience-desc'
  | 'experience-asc';

// ============================================================================
// API Response Types
// ============================================================================

export interface PaginatedResponse<T> {
  page: number;
  limit: number;
  total: number;
  data: T[];
  fromCache?: boolean;
}

export interface CandidateListResponse {
  page: number;
  limit: number;
  total: number;
  candidates: Candidate[];
  fromCache?: boolean;
}

export interface StatsResponse {
  total: number;
  strong: number;
  partial: number;
  weak: number;
  avgScore: number;
  recentCount: number;
  byCategory?: Record<string, number>;
}

export interface UploadResponse {
  success: boolean;
  message: string;
  candidate?: Candidate;
  filename?: string;
  errors?: string[];
}

export interface BatchUploadResponse {
  totalFiles: number;
  successful: number;
  failed: number;
  results: UploadResponse[];
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy';
  timestamp: string;
  version: string;
  scraperRunning: boolean;
  system: {
    cpuPercent?: number;
    memoryPercent?: number;
    diskPercent?: number;
  };
  cache: {
    responseCacheSize: number;
    aiEmbeddingCache: number;
  };
}

export interface ErrorResponse {
  error: true;
  errorCode: string;
  message: string;
  details?: Record<string, unknown>;
}

// ============================================================================
// Job Description Types
// ============================================================================

export interface JobDescription {
  id: string;
  title: string;
  company?: string;
  requiredSkills: string[];
  preferredSkills: string[];
  experienceLevel: string;
  location?: string;
  education?: string;
  responsibilities: string[];
  description: string;
  candidateCount: number;
  createdAt?: string;
}

export interface MatchResult {
  candidateId: string;
  jobDescriptionId: string;
  matchScore: number;
  status: MatchTierType;
  matchedSkills: string[];
  missingSkills: string[];
  evaluation?: AIEvaluation;
}

// ============================================================================
// Email & Auth Types
// ============================================================================

export interface EmailAccount {
  name: string;
  email: string;
  server: string;
  processedCount: number;
  lastCheck?: string;
}

export interface ScraperStatus {
  running: boolean;
  totalAccounts: number;
  accounts: EmailAccount[];
  totalProcessed: number;
  processAllHistory: boolean;
}

export interface OAuthConfig {
  authUrl: string;
  clientId: string;
  redirectUri: string;
  scope: string;
}

// ============================================================================
// UI Types
// ============================================================================

export interface CategoryColor {
  bg: string;
  text: string;
  border: string;
}

export interface ToastNotification {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message?: string;
  duration?: number;
}

// ============================================================================
// Store Types
// ============================================================================

export interface CandidateState {
  candidates: Candidate[];
  shortlistedIds: string[];
  loading: boolean;
  error: string | null;
  totalCount: number;
}

export interface AuthState {
  isAuthenticated: boolean;
  user: {
    email: string;
    name: string;
  } | null;
}

// ============================================================================
// Utility Types
// ============================================================================

export type Optional<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;

export type RequiredFields<T, K extends keyof T> = T & Required<Pick<T, K>>;

export type AsyncState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

// Type guard functions
export function isCandidate(obj: unknown): obj is Candidate {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    'id' in obj &&
    'name' in obj &&
    'email' in obj
  );
}

export function isErrorResponse(obj: unknown): obj is ErrorResponse {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    'error' in obj &&
    (obj as ErrorResponse).error === true
  );
}
