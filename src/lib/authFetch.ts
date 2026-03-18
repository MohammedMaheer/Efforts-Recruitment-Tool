/**
 * Authenticated Fetch Utility
 * Wraps native fetch() with automatic auth token injection.
 * Use this instead of raw fetch() for any API call that needs authentication.
 * Works outside React components (no hooks required).
 */
import { useAuthStore } from '@/store/authStore';

/**
 * Get current auth headers from the store (callable from anywhere)
 */
export function getAuthHeaders(): Record<string, string> {
  const token = useAuthStore.getState().token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Authenticated fetch - same API as native fetch() but auto-injects Authorization header.
 * Does NOT set Content-Type by default (important for FormData uploads).
 */
export async function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<Response> {
  const authHeaders = getAuthHeaders();
  
  const response = await fetch(input, {
    ...init,
    headers: {
      ...authHeaders,
      ...init?.headers,
    },
  });

  // Auto-logout on 401 (expired/invalid token) — consistent with ApiClient
  if (response.status === 401) {
    const authStore = useAuthStore.getState();
    if (authStore.isAuthenticated) {
      authStore.logout();
      window.dispatchEvent(new CustomEvent('auth:session-expired'));
    }
  }

  return response;
}

export default authFetch;
