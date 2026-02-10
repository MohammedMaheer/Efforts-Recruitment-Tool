/**
 * API Service Layer
 * Centralized API client with error handling, retries, and type safety
 */
import config from '@/config';
import { useAuthStore } from '@/store/authStore';
import type {
  Candidate,
  CandidateListResponse,
  UploadResponse,
  BatchUploadResponse,
  HealthResponse,
  ErrorResponse,
  ScraperStatus,
} from '@/types';

// ============================================================================
// Types
// ============================================================================

interface RequestConfig extends RequestInit {
  timeout?: number;
  retries?: number;
  retryDelay?: number;
}

interface ApiResponse<T> {
  data: T | null;
  error: ErrorResponse | null;
  status: number;
}

// ============================================================================
// API Client Class
// ============================================================================

class ApiClient {
  private baseUrl: string;
  private defaultTimeout: number;
  private defaultRetries: number;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
    this.defaultTimeout = 30000; // 30 seconds
    this.defaultRetries = 2;
  }

  /**
   * Make an HTTP request with timeout, retries, and error handling
   */
  private async request<T>(
    endpoint: string,
    options: RequestConfig = {}
  ): Promise<ApiResponse<T>> {
    const {
      timeout = this.defaultTimeout,
      retries = this.defaultRetries,
      retryDelay = 1000,
      ...fetchOptions
    } = options;

    const url = `${this.baseUrl}${endpoint}`;
    let lastError: Error | null = null;

    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        // Create abort controller for timeout
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);

        // Inject auth token from store if available
        const token = useAuthStore.getState().token;
        const authHeaders: Record<string, string> = token
          ? { Authorization: `Bearer ${token}` }
          : {};

        const response = await fetch(url, {
          ...fetchOptions,
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            ...authHeaders,
            ...fetchOptions.headers,
          },
        });

        clearTimeout(timeoutId);

        // Parse response
        const data = await response.json().catch(() => null);

        if (!response.ok) {
          return {
            data: null,
            error: {
              error: true,
              errorCode: data?.error_code || 'API_ERROR',
              message: data?.message || `Request failed with status ${response.status}`,
              details: data?.details || {},
            },
            status: response.status,
          };
        }

        return { data: data as T, error: null, status: response.status };
      } catch (err) {
        lastError = err instanceof Error ? err : new Error('Unknown error');

        // Don't retry on abort (timeout) or if it's the last attempt
        if (err instanceof DOMException && err.name === 'AbortError') {
          console.warn(`Request timeout for ${endpoint}`);
        }

        // Wait before retrying
        if (attempt < retries) {
          await new Promise((r) => setTimeout(r, retryDelay * (attempt + 1)));
          console.warn(`Retrying request to ${endpoint} (attempt ${attempt + 2})`);
        }
      }
    }

    return {
      data: null,
      error: {
        error: true,
        errorCode: 'NETWORK_ERROR',
        message: lastError?.message || 'Network request failed',
        details: {},
      },
      status: 0,
    };
  }

  // GET request helper
  async get<T>(endpoint: string, options?: RequestConfig): Promise<ApiResponse<T>> {
    return this.request<T>(endpoint, { ...options, method: 'GET' });
  }

  // POST request helper
  async post<T>(
    endpoint: string,
    body?: unknown,
    options?: RequestConfig
  ): Promise<ApiResponse<T>> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  // PUT request helper
  async put<T>(
    endpoint: string,
    body?: unknown,
    options?: RequestConfig
  ): Promise<ApiResponse<T>> {
    return this.request<T>(endpoint, {
      ...options,
      method: 'PUT',
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  // DELETE request helper
  async delete<T>(endpoint: string, options?: RequestConfig): Promise<ApiResponse<T>> {
    return this.request<T>(endpoint, { ...options, method: 'DELETE' });
  }

  // Upload file(s) - special handling for FormData
  async upload<T>(
    endpoint: string,
    formData: FormData,
    options?: Omit<RequestConfig, 'body'>
  ): Promise<ApiResponse<T>> {
    const url = `${this.baseUrl}${endpoint}`;

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(
        () => controller.abort(),
        options?.timeout || 60000
      ); // 60s for uploads

      const token = useAuthStore.getState().token;
      const authHeaders: Record<string, string> = token
        ? { Authorization: `Bearer ${token}` }
        : {};

      const response = await fetch(url, {
        method: 'POST',
        body: formData,
        signal: controller.signal,
        headers: authHeaders,
        // Don't set Content-Type - let browser set it with boundary
      });

      clearTimeout(timeoutId);

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        return {
          data: null,
          error: {
            error: true,
            errorCode: data?.error_code || 'UPLOAD_ERROR',
            message: data?.message || 'Upload failed',
            details: data?.details || {},
          },
          status: response.status,
        };
      }

      return { data: data as T, error: null, status: response.status };
    } catch (err) {
      return {
        data: null,
        error: {
          error: true,
          errorCode: 'UPLOAD_ERROR',
          message: err instanceof Error ? err.message : 'Upload failed',
          details: {},
        },
        status: 0,
      };
    }
  }
}

// ============================================================================
// API Service Instance
// ============================================================================

const client = new ApiClient(config.apiUrl);

// ============================================================================
// Candidate API
// ============================================================================

export const candidateApi = {
  /**
   * Get paginated list of candidates
   */
  async getAll(params?: {
    page?: number;
    limit?: number;
    jobCategory?: string;
    minScore?: number;
  }): Promise<ApiResponse<CandidateListResponse>> {
    const searchParams = new URLSearchParams();
    if (params?.page) searchParams.set('page', String(params.page));
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.jobCategory) searchParams.set('job_category', params.jobCategory);
    if (params?.minScore) searchParams.set('min_score', String(params.minScore));

    const query = searchParams.toString();
    return client.get(`/api/candidates${query ? `?${query}` : ''}`);
  },

  /**
   * Get single candidate by ID
   */
  async getById(id: string): Promise<ApiResponse<Candidate>> {
    return client.get(`/api/candidates/${id}`);
  },

  /**
   * Get new candidates since date
   */
  async getNewSince(since: string): Promise<ApiResponse<{ new_count: number; candidates: Candidate[] }>> {
    return client.get(`/api/candidates/new?since=${encodeURIComponent(since)}`);
  },

  /**
   * Update candidate status (Shortlisted, Rejected, etc.)
   * This is the primary update method - use for all candidate updates
   * When status is 'Shortlisted', backend auto-sends notification email
   */
  async updateStatus(id: string, status: string): Promise<ApiResponse<{
    status: string;
    message: string;
    candidate_id: string;
    new_status: string;
    email_sent?: { status: string; message?: string };
  }>> {
    return client.put(`/api/candidates/${id}/status`, { status });
  },

  /**
   * Reprocess candidate scores
   */
  async reprocessScores(): Promise<ApiResponse<{ processed: number; errors: number }>> {
    return client.post('/api/candidates/reprocess-scores');
  },

  /**
   * Reset and reparse all emails
   */
  async resetAndReparse(): Promise<ApiResponse<{
    deleted_count: number;
    emails_processed: number;
    candidates_created: number;
    ai_analyzed: number;
  }>> {
    return client.post('/api/candidates/reset-and-reparse');
  },
};

// ============================================================================
// Resume API
// ============================================================================

export const resumeApi = {
  /**
   * Upload single resume
   */
  async upload(file: File): Promise<ApiResponse<UploadResponse>> {
    const formData = new FormData();
    formData.append('file', file);
    return client.upload('/api/resumes/upload', formData);
  },

  /**
   * Upload multiple resumes
   */
  async uploadMultiple(files: File[]): Promise<ApiResponse<BatchUploadResponse>> {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    return client.upload('/api/resumes/upload-multiple', formData);
  },

  /**
   * Download resume
   */
  getDownloadUrl(candidateId: string): string {
    return `${config.apiUrl}/api/candidates/${candidateId}/resume`;
  },
};

// ============================================================================
// Stats API
// ============================================================================

export const statsApi = {
  /**
   * Get dashboard statistics
   */
  async getDashboard(): Promise<ApiResponse<any>> {
    return client.get('/api/stats');
  },

  /**
   * Get AI status
   */
  async getAIStatus(): Promise<ApiResponse<{
    local_ai: { status: string; model: string };
    openai: { status: string; model?: string };
  }>> {
    return client.get('/api/ai/status');
  },
};

// ============================================================================
// Scraper API
// ============================================================================

export const scraperApi = {
  /**
   * Get scraper status
   */
  async getStatus(): Promise<ApiResponse<ScraperStatus>> {
    return client.get('/api/scraper/status');
  },

  /**
   * Start email scraper
   */
  async start(): Promise<ApiResponse<{ message: string }>> {
    return client.post('/api/scraper/start');
  },

  /**
   * Stop email scraper
   */
  async stop(): Promise<ApiResponse<{ message: string }>> {
    return client.post('/api/scraper/stop');
  },

  /**
   * Trigger manual sync
   */
  async syncNow(processAll = true): Promise<ApiResponse<{
    total_emails_found: number;
    total_candidates_extracted: number;
  }>> {
    return client.post(`/api/scraper/process-now?process_all=${processAll}`);
  },
};

// ============================================================================
// Health API
// ============================================================================

export const healthApi = {
  /**
   * Get health status
   */
  async check(): Promise<ApiResponse<HealthResponse>> {
    return client.get('/health');
  },
};

// ============================================================================
// OAuth API
// ============================================================================

export const oauthApi = {
  /**
   * Get OAuth URL for authentication
   */
  async getConfig(): Promise<ApiResponse<{
    auth_url: string;
    client_id: string;
    redirect_uri: string;
    scope: string;
  }>> {
    return client.get('/api/email/oauth2/url');
  },

  /**
   * Exchange code for token
   */
  async callback(code: string, redirectUri: string): Promise<ApiResponse<{
    success: boolean;
    email: string;
    expires_at: string;
  }>> {
    return client.post('/api/email/oauth2/callback', { code, redirect_uri: redirectUri });
  },

  /**
   * Check OAuth status
   */
  async getStatus(): Promise<ApiResponse<{
    authenticated: boolean;
    email?: string;
    expires_at?: string;
  }>> {
    return client.get('/api/oauth/status');
  },
};

// ============================================================================
// Advanced AI Services API
// ============================================================================

export const advancedApi = {
  // ML Ranking
  ml: {
    async rankCandidates(candidateIds: string[], jobId?: string, topN = 10) {
      return client.post('/api/advanced/ml/rank', {
        candidate_ids: candidateIds,
        job_id: jobId,
        top_n: topN,
      });
    },
    async recordDecision(candidateId: string, jobId: string, wasHired: boolean) {
      return client.post('/api/advanced/ml/record-decision', {
        candidate_id: candidateId,
        job_id: jobId,
        was_hired: wasHired,
      });
    },
    async retrain() {
      return client.post('/api/advanced/ml/retrain');
    },
  },

  // Skill Extraction
  skills: {
    async extract(resumeText: string, useGpt4 = false) {
      return client.post('/api/advanced/skills/extract', {
        resume_text: resumeText,
        use_gpt4: useGpt4,
      });
    },
    async analyzeGap(candidateId: string, jobId: string) {
      return client.post('/api/advanced/skills/gap-analysis', {
        candidate_id: candidateId,
        job_id: jobId,
      });
    },
  },

  // Duplicate Detection
  duplicates: {
    async check(params: { candidateId?: string; email?: string; phone?: string; name?: string; threshold?: number }) {
      return client.post('/api/advanced/duplicates/check', {
        candidate_id: params.candidateId,
        email: params.email,
        phone: params.phone,
        name: params.name,
        threshold: params.threshold || 70,
      });
    },
    async merge(primaryId: string, duplicateIds: string[]) {
      return client.post('/api/advanced/duplicates/merge', {
        primary_candidate_id: primaryId,
        duplicate_candidate_ids: duplicateIds,
      });
    },
  },

  // Job Matching
  matching: {
    async matchCandidateToJobs(candidateId: string, jobIds?: string[]) {
      return client.post('/api/advanced/matching/candidate-to-jobs', {
        candidate_id: candidateId,
        job_ids: jobIds || [],
      });
    },
    async matchJobToCandidates(jobId: string, minScore = 50, limit = 20) {
      return client.post('/api/advanced/matching/job-to-candidates', {
        job_id: jobId,
        min_score: minScore,
        limit,
      });
    },
  },

  // Predictive Analytics
  analytics: {
    async predict(candidateId: string, jobId?: string) {
      return client.post('/api/advanced/analytics/predict', {
        candidate_id: candidateId,
        job_id: jobId,
      });
    },
    async getPipelineAnalytics() {
      return client.get('/api/advanced/analytics/pipeline');
    },
  },

  // Resume Quality
  quality: {
    async analyze(params: { candidateId?: string; resumeText?: string }) {
      return client.post('/api/advanced/quality/analyze', {
        candidate_id: params.candidateId,
        resume_text: params.resumeText,
      });
    },
  },

  // Email Templates
  templates: {
    async list() {
      return client.get('/api/advanced/templates');
    },
    async get(templateId: string) {
      return client.get(`/api/advanced/templates/${templateId}`);
    },
    async create(template: {
      template_id: string;
      name: string;
      subject: string;
      body: string;
      category?: string;
    }) {
      return client.post('/api/advanced/templates', template);
    },
    async update(templateId: string, updates: { name?: string; subject?: string; body?: string; category?: string }) {
      return client.put(`/api/advanced/templates/${templateId}`, updates);
    },
    async delete(templateId: string) {
      return client.delete(`/api/advanced/templates/${templateId}`);
    },
    async render(templateId: string, variables: Record<string, string>) {
      return client.post('/api/advanced/templates/render', {
        template_id: templateId,
        variables,
      });
    },
  },

  // Calendar Integration
  calendar: {
    async scheduleInterview(params: {
      candidateId: string;
      candidateEmail: string;
      candidateName: string;
      jobTitle: string;
      interviewerEmail: string;
      preferredTimes?: string[];
      durationMinutes?: number;
      interviewType?: string;
      notes?: string;
      useCalendly?: boolean;
    }) {
      return client.post('/api/advanced/calendar/schedule', {
        candidate_id: params.candidateId,
        candidate_email: params.candidateEmail,
        candidate_name: params.candidateName,
        job_title: params.jobTitle,
        interviewer_email: params.interviewerEmail,
        preferred_times: params.preferredTimes || [],
        duration_minutes: params.durationMinutes || 60,
        interview_type: params.interviewType || 'video',
        notes: params.notes,
        use_calendly: params.useCalendly || false,
      });
    },
    async getAvailability(params: {
      interviewerEmail: string;
      dateRangeStart: string;
      dateRangeEnd: string;
      durationMinutes?: number;
    }) {
      return client.post('/api/advanced/calendar/availability', {
        interviewer_email: params.interviewerEmail,
        date_range_start: params.dateRangeStart,
        date_range_end: params.dateRangeEnd,
        duration_minutes: params.durationMinutes || 60,
      });
    },
  },

  // SMS Notifications
  sms: {
    async send(params: {
      toPhone: string;
      message?: string;
      templateId?: string;
      variables?: Record<string, string>;
      candidateId?: string;
    }) {
      return client.post('/api/advanced/sms/send', {
        to_phone: params.toPhone,
        message: params.message,
        template_id: params.templateId,
        variables: params.variables || {},
        candidate_id: params.candidateId,
      });
    },
    async sendBulk(recipients: Array<{ phone: string; name: string }>, templateId: string, variables?: Record<string, string>) {
      return client.post('/api/advanced/sms/bulk', {
        recipients,
        template_id: templateId,
        variables: variables || {},
      });
    },
    async getTemplates() {
      return client.get('/api/advanced/sms/templates');
    },
  },

  // Drip Campaigns
  campaigns: {
    async list() {
      return client.get('/api/advanced/campaigns');
    },
    async get(campaignId: string) {
      return client.get(`/api/advanced/campaigns/${campaignId}`);
    },
    async create(campaign: {
      campaign_id: string;
      name: string;
      description?: string;
      trigger?: string;
      steps: Array<{
        delay_days?: number;
        delay_hours?: number;
        type: 'email' | 'sms' | 'task';
        template?: string;
        message?: string;
        subject?: string;
        condition?: string;
      }>;
      stop_conditions?: string[];
    }) {
      return client.post('/api/advanced/campaigns', campaign);
    },
    async delete(campaignId: string) {
      return client.delete(`/api/advanced/campaigns/${campaignId}`);
    },
    async enroll(params: {
      candidateId: string;
      candidateEmail: string;
      candidateName: string;
      candidatePhone?: string;
      campaignId: string;
      variables?: Record<string, string>;
    }) {
      return client.post('/api/advanced/campaigns/enroll', {
        candidate_id: params.candidateId,
        candidate_email: params.candidateEmail,
        candidate_name: params.candidateName,
        candidate_phone: params.candidatePhone,
        campaign_id: params.campaignId,
        variables: params.variables || {},
      });
    },
    async unenroll(candidateId: string, campaignId?: string, reason = 'manual') {
      return client.post('/api/advanced/campaigns/unenroll', {
        candidate_id: candidateId,
        campaign_id: campaignId,
        reason,
      });
    },
    async markResponded(candidateId: string, campaignId?: string) {
      return client.post(`/api/advanced/campaigns/mark-responded?candidate_id=${candidateId}${campaignId ? `&campaign_id=${campaignId}` : ''}`);
    },
    async getEnrollments(candidateId: string) {
      return client.get(`/api/advanced/campaigns/enrollments/${candidateId}`);
    },
    async getStats(campaignId: string) {
      return client.get(`/api/advanced/campaigns/stats/${campaignId}`);
    },
    async getAllStats() {
      return client.get('/api/advanced/campaigns/stats');
    },
    async processSteps() {
      return client.post('/api/advanced/campaigns/process');
    },
  },
};

// ============================================================================
// Export all APIs
// ============================================================================

// ============================================================================
// LinkedIn Import API
// ============================================================================

export const linkedInApi = {
  async importProfile(profileData: {
    name: string;
    email: string;
    phone?: string;
    location?: string;
    linkedin: string;
    source?: string;
    job_category?: string;
    skills?: string[];
    experience?: number;
    resume_text?: string;
    profile_image?: string;
    headline?: string;
    education?: any[];
    work_experience?: any[];
    certifications?: any[];
    languages?: any[];
    scraped_at?: string;
  }) {
    return client.post('/api/candidates/linkedin', profileData);
  },

  async getLinkedInCandidates() {
    const response = await client.get('/api/candidates');
    // Filter to only LinkedIn imports
    const candidates = response.data as any[] || [];
    return candidates.filter((c: any) => 
      c.source === 'linkedin_extension' || c.linkedin?.includes('linkedin.com')
    );
  },
};

export const api = {
  candidates: candidateApi,
  resumes: resumeApi,
  stats: statsApi,
  scraper: scraperApi,
  health: healthApi,
  oauth: oauthApi,
  advanced: advancedApi,
  linkedin: linkedInApi,
};

// ============================================================================
// Taxonomy API
// ============================================================================

export const taxonomyApi = {
  /**
   * Get full job taxonomy (categories + subcategories)
   */
  async getAll(): Promise<ApiResponse<{ categories: string[]; taxonomy: Record<string, string[]> }>> {
    return client.get('/api/taxonomy');
  },

  /**
   * Get subcategories for a specific category
   */
  async getSubcategories(category: string): Promise<ApiResponse<{ category: string; subcategories: string[] }>> {
    return client.get(`/api/taxonomy/${encodeURIComponent(category)}/subcategories`);
  },

  /**
   * Classify a free-text job title into category + subcategory
   */
  async classify(title: string): Promise<ApiResponse<{ title: string; category: string; subcategory: string }>> {
    return client.post('/api/taxonomy/classify', { title });
  },
};

// ============================================================================
// AI Smart Search API
// ============================================================================

export const aiApi = {
  /**
   * Smart search using LLM-powered semantic matching
   */
  async smartSearch(query: string, topN = 10): Promise<ApiResponse<{
    query: string;
    results: Array<{
      candidate: Candidate;
      relevance_score: number;
      match_reasons: string[];
    }>;
    total_searched: number;
    source: string;
  }>> {
    return client.post('/api/ai/smart-search', { query, top_n: topN }, { timeout: 60000 });
  },

  /**
   * AI chat with database context
   */
  async chat(message: string, includeCandidates = true): Promise<ApiResponse<{
    response: string;
    ai_powered: boolean;
    context_included: boolean;
    source: string;
  }>> {
    return client.post('/api/ai/chat', { message, include_candidates: includeCandidates });
  },

  /**
   * Generate interview questions
   */
  async interviewQuestions(candidate: Record<string, unknown>, jobDescription: Record<string, unknown>, numQuestions = 5): Promise<ApiResponse<{
    questions: string[];
    source: string;
  }>> {
    return client.post('/api/ai/interview-questions', { candidate, job_description: jobDescription, num_questions: numQuestions });
  },

  /**
   * Get AI status
   */
  async getStatus(): Promise<ApiResponse<{
    local_ai: { status: string; model: string };
    openai: { status: string; model?: string };
    llm: { status: string; model?: string };
  }>> {
    return client.get('/api/ai/status');
  },
};

export default api;
