/**
 * Lazy Import with Auto-Retry
 * 
 * After a new deployment, cached index.html may reference old chunk filenames
 * that no longer exist. Firebase SPA config returns index.html (text/html) for
 * missing JS files, causing "Failed to fetch dynamically imported module" errors.
 * 
 * This wrapper catches the failure and does ONE hard reload to fetch the new
 * index.html with updated chunk references. A sessionStorage flag prevents
 * infinite reload loops.
 */
import { lazy, type ComponentType, type LazyExoticComponent } from 'react';

type ComponentModule<T = ComponentType<Record<string, never>>> = { default: T };

const RELOAD_KEY = 'chunk_reload_ts';
const RELOAD_COOLDOWN_MS = 10_000; // Don't reload more than once every 10s

export function lazyRetry<T extends ComponentType<Record<string, never>>>(
  importFn: () => Promise<ComponentModule<T>>,
): LazyExoticComponent<T> {
  return lazy(async () => {
    try {
      return await importFn();
    } catch (error) {
      // Check if this looks like a chunk-load / MIME-type failure
      const msg = error instanceof Error ? error.message : String(error);
      const isChunkError =
        msg.includes('dynamically imported module') ||
        msg.includes('Loading chunk') ||
        msg.includes('Failed to fetch') ||
        msg.includes('Loading CSS chunk');

      if (isChunkError) {
        const lastReload = Number(sessionStorage.getItem(RELOAD_KEY) || '0');
        const now = Date.now();

        if (now - lastReload > RELOAD_COOLDOWN_MS) {
          sessionStorage.setItem(RELOAD_KEY, String(now));
          window.location.reload();
          // Return a never-resolving promise so React doesn't render during reload
          return new Promise<ComponentModule<T>>(() => {});
        }
      }

      // Not a chunk error, or already reloaded recently — re-throw for ErrorBoundary
      throw error;
    }
  });
}
