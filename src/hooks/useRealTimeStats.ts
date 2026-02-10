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
  /** Polling interval in milliseconds (default: 10000 - 10 seconds) */
  interval?: number;
  /** Whether to enable polling (default: true) */
  enabled?: boolean;
  /** Callback when stats change */
  onStatsChange?: (stats: LiveStats) => void;
}

export function useRealTimeStats(options: UseRealTimeStatsOptions = {}) {
  const { interval = 10000, enabled = true, onStatsChange } = options;
  
  const [stats, setStats] = useState<LiveStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  
  const previousStatsRef = useRef<LiveStats | null>(null);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  const onStatsChangeRef = useRef(onStatsChange);
  onStatsChangeRef.current = onStatsChange;

  const fetchStats = useCallback(async () => {
    try {
      const token = useAuthStore.getState().token;
      const response = await fetch(`${config.apiUrl}/api/stats/live`, {
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
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

    // Setup polling
    pollingRef.current = setInterval(fetchStats, interval);

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [enabled, interval]);

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
