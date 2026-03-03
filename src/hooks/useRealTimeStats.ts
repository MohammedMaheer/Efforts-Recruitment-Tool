/**
 * Real-time statistics hook
 * Polls the backend for live updates on candidate counts and analytics
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuthStore } from '@/store/authStore';
import config from '@/config';

interface LiveStats {
  total_candidates: number;
  new_24h: number;
  categories: Record<string, { count: number; avg_score: number }>;
  category_count: number;
  average_score: number;
  strong_matches: number;
  timestamp: string;
  error?: string;
}

interface UseRealTimeStatsOptions {
  /** Polling interval in milliseconds (default: 30000 - 30 seconds) */
  interval?: number;
  /** Whether to enable polling (default: true) */
  enabled?: boolean;
  /** Callback when stats change */
  onStatsChange?: (stats: LiveStats) => void;
}

export function useRealTimeStats(options: UseRealTimeStatsOptions = {}) {
  const { interval = 30000, enabled = true, onStatsChange } = options;
  
  const [stats, setStats] = useState<LiveStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  
  const previousStatsRef = useRef<LiveStats | null>(null);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  const onStatsChangeRef = useRef(onStatsChange);
  onStatsChangeRef.current = onStatsChange;

  const abortControllerRef = useRef<AbortController | null>(null);

  const fetchStats = useCallback(async () => {
    try {
      // Abort any previous in-flight request
      abortControllerRef.current?.abort();
      const controller = new AbortController();
      abortControllerRef.current = controller;

      const token = useAuthStore.getState().token;
      const response = await fetch(`${config.apiUrl}/api/stats/live`, {
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        signal: controller.signal,
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      const data: LiveStats = await response.json();
      
      // Check if stats actually changed
      const hasChanged = !previousStatsRef.current || 
        previousStatsRef.current.total_candidates !== data.total_candidates ||
        previousStatsRef.current.new_24h !== data.new_24h ||
        previousStatsRef.current.strong_matches !== data.strong_matches;
      
      if (hasChanged && onStatsChangeRef.current) {
        onStatsChangeRef.current(data);
      }
      
      previousStatsRef.current = data;
      setStats(data);
      setLastUpdate(new Date());
      setError(null);
      setLoading(false);
      
      return data;
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return null;
      const message = err instanceof Error ? err.message : 'Failed to fetch stats';
      setError(message);
      setLoading(false);
      return null;
    }
  }, []);

  // Initial fetch and setup polling
  useEffect(() => {
    if (!enabled) {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
      return;
    }

    // Initial fetch
    fetchStats();

    // Setup polling — skip fetch when tab is hidden to save resources
    pollingRef.current = setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      fetchStats();
    }, interval);

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [enabled, interval, fetchStats]);

  // Manual refresh function
  const refresh = useCallback(() => {
    setLoading(true);
    return fetchStats();
  }, [fetchStats]);

  return {
    stats,
    loading,
    error,
    lastUpdate,
    refresh,
    isPolling: enabled && !!pollingRef.current,
  };
}

export default useRealTimeStats;
