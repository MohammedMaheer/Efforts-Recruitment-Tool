/**
 * Intersection Observer Hook
 * For lazy loading and infinite scroll
 */
import { useState, useEffect, useCallback } from 'react';

interface UseIntersectionObserverOptions {
  root?: Element | null;
  rootMargin?: string;
  threshold?: number | number[];
  freezeOnceVisible?: boolean;
}

interface UseIntersectionObserverReturn {
  ref: (node: Element | null) => void;
  isIntersecting: boolean;
  entry: IntersectionObserverEntry | null;
}

/**
 * Hook for observing element visibility
 * Useful for lazy loading, infinite scroll, animations on scroll
 */
export function useIntersectionObserver(
  options: UseIntersectionObserverOptions = {}
): UseIntersectionObserverReturn {
  const {
    root = null,
    rootMargin = '0px',
    threshold = 0,
    freezeOnceVisible = false,
  } = options;

  const [entry, setEntry] = useState<IntersectionObserverEntry | null>(null);
  const [node, setNode] = useState<Element | null>(null);

  const frozen = entry?.isIntersecting && freezeOnceVisible;

  const updateEntry = useCallback(
    ([entry]: IntersectionObserverEntry[]) => {
      setEntry(entry);
    },
    []
  );

  useEffect(() => {
    // Skip if frozen or no node
    if (frozen || !node) return;

    // Check for browser support
    if (!('IntersectionObserver' in window)) {
      console.warn('IntersectionObserver is not supported');
      return;
    }

    const observer = new IntersectionObserver(updateEntry, {
      root,
      rootMargin,
      threshold,
    });

    observer.observe(node);

    return () => {
      observer.disconnect();
    };
  }, [node, root, rootMargin, threshold, frozen, updateEntry]);

  // Ref callback to set the observed node
  const ref = useCallback((newNode: Element | null) => {
    setNode(newNode);
  }, []);

  return {
    ref,
    isIntersecting: entry?.isIntersecting ?? false,
    entry,
  };
}

/**
 * Hook for infinite scroll functionality
 */
export function useInfiniteScroll(
  callback: () => void,
  options: UseIntersectionObserverOptions = {}
): UseIntersectionObserverReturn {
  const { ref, isIntersecting, entry } = useIntersectionObserver({
    rootMargin: '100px',
    ...options,
  });

  useEffect(() => {
    if (isIntersecting) {
      callback();
    }
  }, [isIntersecting, callback]);

  return { ref, isIntersecting, entry };
}
