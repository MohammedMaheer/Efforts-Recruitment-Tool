/**
 * Async State Hook
 * Manages loading, error, and data states for async operations
 */
import { useState, useCallback, useRef, useEffect } from 'react';

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
}

interface UseAsyncReturn<T, Args extends unknown[]> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  execute: (...args: Args) => Promise<T | null>;
  reset: () => void;
  setData: (data: T | null) => void;
}

/**
 * Hook for managing async operations with loading/error states
 * @param asyncFunction - Async function to execute
 * @param immediate - Whether to execute immediately on mount
 * @returns Async state and control functions
 */
export function useAsync<T, Args extends unknown[] = []>(
  asyncFunction: (...args: Args) => Promise<T>,
  immediate = false
): UseAsyncReturn<T, Args> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: immediate,
    error: null,
  });

  // Track mounted state to prevent state updates after unmount
  const mountedRef = useRef(true);
  const executingRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const execute = useCallback(
    async (...args: Args): Promise<T | null> => {
      // Prevent concurrent executions
      if (executingRef.current) {
        return state.data;
      }

      executingRef.current = true;
      setState((prev) => ({ ...prev, loading: true, error: null }));

      try {
        const result = await asyncFunction(...args);

        if (mountedRef.current) {
          setState({ data: result, loading: false, error: null });
        }

        executingRef.current = false;
        return result;
      } catch (error) {
        const err = error instanceof Error ? error : new Error(String(error));

        if (mountedRef.current) {
          setState({ data: null, loading: false, error: err });
        }

        executingRef.current = false;
        return null;
      }
    },
    [asyncFunction]
  );

  const reset = useCallback(() => {
    setState({ data: null, loading: false, error: null });
  }, []);

  const setData = useCallback((data: T | null) => {
    setState((prev) => ({ ...prev, data }));
  }, []);

  // Execute immediately if requested
  useEffect(() => {
    if (immediate) {
      execute(...([] as unknown as Args));
    }
  }, [immediate, execute]);

  return {
    ...state,
    execute,
    reset,
    setData,
  };
}

/**
 * Hook for async operations with automatic retry
 */
export function useAsyncRetry<T, Args extends unknown[] = []>(
  asyncFunction: (...args: Args) => Promise<T>,
  options: {
    retries?: number;
    retryDelay?: number;
    immediate?: boolean;
  } = {}
): UseAsyncReturn<T, Args> & { retry: () => void } {
  const { retries = 3, retryDelay = 1000, immediate = false } = options;
  const lastArgsRef = useRef<Args | null>(null);
  const retryCountRef = useRef(0);

  const wrappedFunction = useCallback(
    async (...args: Args): Promise<T> => {
      lastArgsRef.current = args;
      let lastError: Error | null = null;

      for (let attempt = 0; attempt <= retries; attempt++) {
        try {
          const result = await asyncFunction(...args);
          retryCountRef.current = 0;
          return result;
        } catch (error) {
          lastError = error instanceof Error ? error : new Error(String(error));

          if (attempt < retries) {
            await new Promise((r) => setTimeout(r, retryDelay * (attempt + 1)));
          }
        }
      }

      throw lastError;
    },
    [asyncFunction, retries, retryDelay]
  );

  const asyncState = useAsync(wrappedFunction, immediate);

  const retry = useCallback(() => {
    if (lastArgsRef.current) {
      asyncState.execute(...lastArgsRef.current);
    }
  }, [asyncState]);

  return { ...asyncState, retry };
}
