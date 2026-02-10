/**
 * Application Configuration
 * Centralized configuration with type safety
 */

// Environment detection
const API_URL = import.meta.env.VITE_API_URL || 
                (import.meta.env.DEV 
                  ? 'http://localhost:8000' 
                  : window.location.origin);

const ENV = import.meta.env.VITE_ENV || 
            (import.meta.env.DEV ? 'development' : 'production');

/**
 * Application configuration object
 */
export const config = {
  // Environment
  apiUrl: API_URL,
  environment: ENV,
  isDevelopment: ENV === 'development',
  isProduction: ENV === 'production',
  
  // API endpoints (centralized for easy changes)
  endpoints: {
    auth: `${API_URL}/api/auth`,
    candidates: `${API_URL}/api/candidates`,
    jobs: `${API_URL}/api/jobs`,
    scraper: `${API_URL}/api/scraper`,
    ai: `${API_URL}/api/ai`,
    stats: `${API_URL}/api/stats`,
    resumes: `${API_URL}/api/resumes`,
    oauth: `${API_URL}/api/oauth`,
    health: `${API_URL}/health`,
    setup: {
      verify: `${API_URL}/api/setup/verify`,
      status: `${API_URL}/api/setup/status`,
      instructions: `${API_URL}/api/setup/instructions`,
      testConnection: `${API_URL}/api/setup/test-connection`,
    },
  },
  
  // Feature flags
  features: {
    mockLogin: ENV === 'development',
    emailScraper: true,
    aiAnalysis: true,
    offlineMode: false,
    debugPanel: ENV === 'development',
    setupWizard: true, // Show setup wizard in production
  },
  
  // UI configuration
  ui: {
    itemsPerPage: 50,
    maxFileSize: 10 * 1024 * 1024, // 10MB
    supportedFileTypes: ['.pdf', '.docx'],
    debounceDelay: 300,
    toastDuration: 5000,
    animationsEnabled: true,
  },
  
  // Cache configuration
  cache: {
    candidatesTTL: 5 * 60 * 1000, // 5 minutes
    statsTTL: 2 * 60 * 1000, // 2 minutes
    maxStaleAge: 30 * 60 * 1000, // 30 minutes
  },
  
  // API configuration
  api: {
    timeout: 30000, // 30 seconds
    retries: 2,
    retryDelay: 1000,
  },
} as const;

// Type exports
export type Config = typeof config;
export type Endpoints = typeof config.endpoints;
export type Features = typeof config.features;

export default config;
