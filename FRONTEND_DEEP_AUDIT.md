# Frontend Deep Audit Report

**Date:** 2025-01-XX  
**Scope:** All 25 files in `src/` — pages, hooks, stores, services, components, types, config  
**Focus:** Real production bugs only — no style/formatting issues

---

## Summary

| Severity | Count |
|----------|-------|
| **Critical** | 3 |
| **High** | 8 |
| **Medium** | 12 |
| **Low** | 6 |
| **Total** | **29** |

---

## 1. CRITICAL — Upload Stats Accumulate Infinitely

**Category:** Bug / Logic Error  
**Severity:** Critical  
**File:** `src/pages/UploadFiles.tsx` line 34–37  

**Description:**  
The `useEffect` watching `uploadResults` adds to `uploadStats` on *every* change, including when the same results are re-rendered. Each time `uploadResults` changes (even from a single upload batch), it **adds** the total counts to the running stats. If a user uploads files, views results, clicks "Upload More", and uploads again, the `today` / `success` / `failed` counters double-count the first batch because the `useEffect` fires again when `setUploadResults([])` is called (adding 0) and then again with new results. More critically, React StrictMode (dev) will fire this twice per mount.

**Code:**
```tsx
useEffect(() => {
  const s = uploadResults.filter(r => r.status === 'success').length
  const f = uploadResults.filter(r => r.status !== 'success').length
  if (uploadResults.length) setUploadStats(prev => ({
    today: prev.today + uploadResults.length,
    success: prev.success + s,
    failed: prev.failed + f
  }))
}, [uploadResults])
```

**Fix:**  
Replace the accumulating `useEffect` with a direct update inside `handleFileUpload`:
```tsx
// Remove the useEffect entirely. Instead, update stats inside handleFileUpload:
const handleFileUpload = useCallback(async (files: FileList | File[]) => {
  // ... existing upload logic ...
  if (response.ok) {
    const data = await response.json()
    const results = data.results || []
    setUploadResults(results)
    const s = results.filter((r: any) => r.status === 'success').length
    const f = results.length - s
    setUploadStats(prev => ({
      today: prev.today + results.length,
      success: prev.success + s,
      failed: prev.failed + f,
    }))
  }
}, [refetch])
```

---

## 2. CRITICAL — Memory Leak: `triggerSync` Creates Uncleanable Timers

**Category:** Memory Leak  
**Severity:** Critical  
**File:** `src/hooks/useEmailSync.ts` lines 121–127  

**Description:**  
`triggerSync` creates 3 `setTimeout` calls (at 3s, 8s, 15s) that are **never tracked or cleaned up**. If the component using this hook unmounts before those timers fire, they execute `checkSyncStatus` on an unmounted component, potentially calling `setSyncStatus` after unmount. This causes React's "Can't perform a React state update on an unmounted component" warning and leaks memory.

**Code:**
```tsx
const triggerSync = useCallback(async () => {
  try {
    // ... fetch ...
    setTimeout(checkSyncStatus, 3000)
    setTimeout(checkSyncStatus, 8000)
    setTimeout(checkSyncStatus, 15000)
  } catch { }
}, [checkSyncStatus])
```

**Fix:**  
Track timeouts and clear them on unmount:
```tsx
const pendingTimers = useRef<ReturnType<typeof setTimeout>[]>([])

const triggerSync = useCallback(async () => {
  try {
    const token = useAuthStore.getState().token
    await fetch(`${config.apiUrl}/api/email/sync-now`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    pendingTimers.current.push(
      setTimeout(checkSyncStatus, 3000),
      setTimeout(checkSyncStatus, 8000),
      setTimeout(checkSyncStatus, 15000),
    )
  } catch { }
}, [checkSyncStatus])

// In the existing useEffect cleanup:
useEffect(() => {
  // ... existing polling setup ...
  return () => {
    clearTimeout(timerId)
    pendingTimers.current.forEach(clearTimeout)
    pendingTimers.current = []
  }
}, [checkSyncStatus, pollIntervalMs])
```

---

## 3. CRITICAL — `register()` Throws on Non-JSON Error Responses

**Category:** Bug / Error Handling  
**Severity:** Critical  
**File:** `src/store/authStore.ts` lines 82–85  

**Description:**  
In `register()`, when `response.ok` is `false`, the code calls `await response.json()` **without `.catch()`**. If the server returns a non-JSON error response (e.g. HTML 502 from a proxy, or plain text), this throws an unhandled JSON parse error that shadows the *actual* registration error, making debugging impossible. The `login()` method correctly uses `.catch(() => ({}))` but `register()` does not.

**Code:**
```tsx
if (!response.ok) {
  const error = await response.json()   // ← throws if response is not JSON
  throw new Error(error.detail || 'Registration failed')
}
```

**Fix:**
```tsx
if (!response.ok) {
  const error = await response.json().catch(() => ({}))
  throw new Error(error.detail || 'Registration failed')
}
```

---

## 4. HIGH — File Size Limit Mismatch: UI Says 100MB, Config Says 10MB

**Category:** Bug / Data Inconsistency  
**Severity:** High  
**File:** `src/pages/UploadFiles.tsx` line 188 + `src/pages/Dashboard.tsx` line ~295 + `src/config.ts` line 63  

**Description:**  
The config defines `maxFileSize: 10 * 1024 * 1024` (10MB), but both `UploadFiles.tsx` and `Dashboard.tsx` display "Max 100MB per file" in the upload UI. Neither file actually *validates* the file size against the config limit. Users will attempt to upload files up to 100MB, which will either fail silently at the backend or cause out-of-memory errors.

**Code (UploadFiles.tsx:188):**
```tsx
<p className="text-sm text-gray-500">Supports PDF, DOC, DOCX files. Max 100MB per file.</p>
```

**Code (config.ts:63):**
```ts
maxFileSize: 10 * 1024 * 1024, // 10MB
```

**Fix:**  
1. Change the UI text to reference the config value: `Max ${config.ui.maxFileSize / (1024*1024)}MB per file`
2. Add client-side validation in `handleFileUpload`:
```tsx
const validFiles = Array.from(files).filter(f => {
  if (!/\.(pdf|docx?)$/i.test(f.name)) return false
  if (f.size > config.ui.maxFileSize) {
    toast.warning('File too large', `${f.name} exceeds ${config.ui.maxFileSize / (1024*1024)}MB limit`)
    return false
  }
  return true
})
```

---

## 5. HIGH — `verifyToken` Returns `true` on Timeout (Stale Auth)

**Category:** Security / Logic Error  
**Severity:** High  
**File:** `src/store/authStore.ts` lines 139–141  

**Description:**  
When `verifyToken` times out (AbortError), it returns `true` and keeps the user authenticated. This is intentionally done for Cloud Run cold starts, but it means a **truly expired or revoked token** will remain trusted for the entire session if the server is slow. The user sees a fully authenticated UI with stale/invalid credentials until a subsequent API call fails with 401.

**Code:**
```tsx
if (error instanceof DOMException && error.name === 'AbortError') {
  return true  // ← keeps auth alive even if token is actually expired
}
```

**Fix:**  
Add a retry with a longer timeout before giving up, or at minimum schedule a background re-verification:
```tsx
if (error instanceof DOMException && error.name === 'AbortError') {
  // Assume valid during cold start, but schedule a re-check
  setTimeout(() => get().verifyToken(), 10000)
  return true
}
```

---

## 6. HIGH — No AbortController in `useRealTimeStats` — Requests Pile Up

**Category:** Performance / Memory Leak  
**Severity:** High  
**File:** `src/hooks/useRealTimeStats.ts` lines 49–82  

**Description:**  
`fetchStats` has no `AbortController`. If the server takes >30s to respond (common during Cloud Run cold starts), the next polling interval fires a second concurrent request, then a third, etc. This creates an unbounded queue of pending HTTP requests that all resolve and call `setStats` in rapid succession. On unmount, none of these in-flight requests are cancelled.

**Code:**
```tsx
const fetchStats = useCallback(async () => {
  try {
    const token = useAuthStore.getState().token;
    const response = await fetch(`${config.apiUrl}/api/stats/live`, {
      headers: { ... },
      // ← no signal, no AbortController
    });
    // ...
  } catch { ... }
}, []);
```

**Fix:**  
Add an AbortController ref, abort previous requests on new fetch, and abort on unmount:
```tsx
const abortRef = useRef<AbortController | null>(null)

const fetchStats = useCallback(async () => {
  abortRef.current?.abort()
  const controller = new AbortController()
  abortRef.current = controller
  try {
    const response = await fetch(`${config.apiUrl}/api/stats/live`, {
      headers: { ... },
      signal: controller.signal,
    });
    // ... existing logic ...
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return
    // ... existing error handling ...
  }
}, [])

// In cleanup:
return () => {
  abortRef.current?.abort()
  if (pollingRef.current) clearInterval(pollingRef.current)
}
```

---

## 7. HIGH — Race Condition: `CandidateDetail` Shows Stale Data on Fast Navigation

**Category:** Bug / Race Condition  
**Severity:** High  
**File:** `src/pages/CandidateDetail.tsx` lines 293–313  

**Description:**  
When navigating quickly between candidate detail pages (`/candidates/1` → `/candidates/2`), the `useEffect` fetching full candidate data has no `AbortController`. The first fetch for candidate 1 may resolve *after* the second fetch for candidate 2 starts, overwriting `fullCandidateData` with stale data from candidate 1. Furthermore, the auto-trigger `handleAIAnalysis` could fire for the wrong candidate.

**Code:**
```tsx
useEffect(() => {
  if (candidate?.id) {
    authFetch(`${config.endpoints.candidates}/${candidate.id}/ai-analysis`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { /* sets state */ })
      .catch(() => {})
  }
}, [candidate?.id])
```

**Fix:**  
Use a cleanup flag or `AbortController`:
```tsx
useEffect(() => {
  let cancelled = false
  if (candidate?.id) {
    authFetch(`${config.endpoints.candidates}/${candidate.id}/ai-analysis`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!cancelled && data?.executive_summary) setAiAnalysis(data)
        else if (!cancelled) setAutoTriggered(true)
      })
      .catch(() => {})
  }
  return () => { cancelled = true }
}, [candidate?.id])
```

---

## 8. HIGH — `candidateStore.shortlistedIds` Not Persisted, Lost on Refresh

**Category:** Bug / Data Loss  
**Severity:** High  
**File:** `src/store/candidateStore.ts` lines 1–38  

**Description:**  
`shortlistedIds` lives in an in-memory Zustand store with **no persistence**. When the user refreshes the page, all shortlisted IDs are lost. The in-memory store and the backend `status` field are not synchronized — `toggleShortlist` only modifies local state. Some components (like `CandidateDetail`) attempt to reconcile with `candidate.status === 'Shortlisted' || isShortlisted(candidate.id)`, but others rely solely on the store, showing inconsistent shortlist state.

**Code:**
```tsx
export const useCandidateStore = create<CandidateStoreState>((set, get) => ({
  candidates: [],
  shortlistedIds: new Set<string>(),  // ← in-memory only
  // ...
}))
```

**Fix:**  
Either:
1. Remove the client-side `shortlistedIds` entirely and always use `candidate.status === 'Shortlisted'` from the backend response, OR
2. Persist `shortlistedIds` using Zustand's `persist` middleware with `localStorage`.

Option 1 is cleaner:
```tsx
// Delete shortlistedIds, toggleShortlist, isShortlisted from the store.
// Replace all isShortlisted(id) calls with candidate.status === 'Shortlisted'.
```

---

## 9. HIGH — 401 Handler Uses Hard Redirect Instead of React Router

**Category:** Bug / UX  
**Severity:** High  
**File:** `src/services/api.ts` (401 handler)  

**Description:**  
When a 401 response is received, the API client does `window.location.href = '/login'`, which triggers a **full page reload**, destroying all in-memory state (candidate data, chat history, form inputs, etc.). This is especially painful if the user had a long AI chat session. React Router's `navigate('/login')` would preserve the SPA state.

**Fix:**  
Use the React Router navigation or at minimum store redirect intention:
```tsx
// Instead of:
window.location.href = '/login'
// Use:
useAuthStore.getState().logout()
// Let the ProtectedRoute wrapper handle the redirect naturally
```

---

## 10. HIGH — `useEffect` in `App.tsx` Missing Dependencies

**Category:** Bug / Stale Closure  
**Severity:** High  
**File:** `src/App.tsx` lines 76–84  

**Description:**  
The token verification `useEffect` has an empty dependency array `[]` but references `token` and `verifyToken`. If the auth state changes during the initial render (e.g., due to sessionStorage hydration timing), the effect captures stale values. In practice, this usually works because the effect runs once and `verifyToken` reads from `get()` internally, but it's still a correctness issue.

**Code:**
```tsx
useEffect(() => {
  const verify = async () => {
    fetch(`${config.apiUrl}/health`, { method: 'GET' }).catch(() => {})
    if (token) {        // ← captured from render, not from getState()
      await verifyToken()
    }
    setIsVerifying(false)
  }
  verify()
}, [])  // ← missing token, verifyToken
```

**Fix:**
```tsx
useEffect(() => {
  const verify = async () => {
    fetch(`${config.apiUrl}/health`, { method: 'GET' }).catch(() => {})
    const currentToken = useAuthStore.getState().token
    if (currentToken) {
      await useAuthStore.getState().verifyToken()
    }
    setIsVerifying(false)
  }
  verify()
}, [])
```

---

## 11. HIGH — Dead SessionStorage Cache Cleanup (Multiple Files)

**Category:** Dead Code / Maintenance Risk  
**Severity:** High  
**File:** `src/hooks/useEmailSync.ts` lines 85–89, `src/pages/Settings.tsx` (multiple handlers)  

**Description:**  
Multiple files still call `sessionStorage.removeItem('candidates_cache')`, `sessionStorage.removeItem('candidates_cache_ts')`, and `sessionStorage.removeItem('candidates_cache_total')`. However, `useCandidates.ts` **no longer uses sessionStorage caching** (the comment explicitly says "no sessionStorage cache"). These calls are dead code that creates a false sense of cache invalidation — developers may think they're refreshing data when they're clearing nonexistent keys.

**Files affected:**
- `useEmailSync.ts` lines 85–89
- `Settings.tsx` — `handleSyncNow`, `handleReprocessGarbled`, `handleRescoreAll`, `handleCleanupGibberish`, `handleFullRepair`

**Fix:**  
Remove all `sessionStorage.removeItem('candidates_cache*')` calls from these files.

---

## 12. MEDIUM — `CandidateAIInsights.tsx` Is Complete Dead Code

**Category:** Dead Code  
**Severity:** Medium  
**File:** `src/components/CandidateAIInsights.tsx` (625 lines)  

**Description:**  
A grep search for `import.*CandidateAIInsights` across the entire `src/` directory returned **zero matches**. This 625-line component is never imported or used anywhere. It imports `authFetch` and `advancedApi` and defines its own API calls, representing dead weight in the bundle (even if tree-shaken in production, it still consumes developer attention).

**Fix:**  
Delete the file or move it to a `_deprecated/` folder if you want to keep it for reference.

---

## 13. MEDIUM — Notification ID Generation Has Collision Risk

**Category:** Bug  
**Severity:** Medium  
**File:** `src/store/notificationStore.ts` line 36  

**Description:**  
IDs are generated as `Date.now().toString() + Math.random()`. `Math.random()` produces values like `0.123456789`, so the full ID might be `"17048523456780.123456789"`. If two notifications are created in the same millisecond (e.g., bulk operations), `Date.now()` is identical — and while `Math.random()` adds entropy, it's not cryptographically secure, and the string concatenation produces IDs that are hard to compare reliably.

**Code:**
```tsx
id: Date.now().toString() + Math.random(),
```

**Fix:**  
Use `crypto.randomUUID()` (supported in all modern browsers):
```tsx
id: crypto.randomUUID(),
```

---

## 14. MEDIUM — Notification `unreadCount` Can Drift From Actual Count

**Category:** Bug  
**Severity:** Medium  
**File:** `src/store/notificationStore.ts` lines 30–50  

**Description:**  
`unreadCount` is tracked as a separate counter incremented/decremented alongside the `notifications` array. If there's ever a deserialization issue from localStorage (e.g., `notifications` is restored but `unreadCount` isn't, or vice versa), or if `updated.length = 100` truncation removes unread notifications, the counter will be wrong. The truncation at line 42 removes the oldest notifications but doesn't check if those were unread.

**Code:**
```tsx
if (updated.length > 100) {
  updated.length = 100  // ← may drop unread notifications without updating unreadCount
}
return {
  notifications: updated,
  unreadCount: state.unreadCount + 1,
}
```

**Fix:**  
Derive `unreadCount` from the actual array, or after truncation recalculate:
```tsx
if (updated.length > 100) {
  updated.length = 100
}
return {
  notifications: updated,
  unreadCount: updated.filter(n => !n.read).length,
}
```

---

## 15. MEDIUM — `useCandidates` Stats Recalculated on Every Render

**Category:** Performance  
**Severity:** Medium  
**File:** `src/hooks/useCandidates.ts` lines 239–268  

**Description:**  
The `stats` object is computed inline (not wrapped in `useMemo`) and creates **two** `new Date()` objects on every render via `recentCount` and `recentUploads` calculations. On pages that poll frequently (Dashboard: 30s stats + 60s candidates), this runs hundreds of times with the same `candidates` array.

**Code:**
```tsx
const stats = {
  total: candidates.length,
  // ...
  recentCount: candidates.filter(c => {
    const date = new Date(c.appliedDate)
    const oneDayAgo = new Date()           // ← new Date() on every render
    oneDayAgo.setDate(oneDayAgo.getDate() - 1)
    return date >= oneDayAgo
  }).length,
  // identical calculation duplicated as recentUploads
}
```

**Fix:**  
Wrap in `useMemo`:
```tsx
const stats = useMemo(() => ({
  total: candidates.length,
  // ...
}), [candidates])
```

---

## 16. MEDIUM — Double Polling on Dashboard

**Category:** Performance  
**Severity:** Medium  
**File:** `src/pages/Dashboard.tsx`  

**Description:**  
The Dashboard calls `useCandidates({ refreshInterval: 60000 })` AND `useRealTimeStats({ interval: 30000 })`. Both poll the backend independently — `useCandidates` fetches `/api/candidates` every 60s and `useRealTimeStats` fetches `/api/stats/live` every 30s. This is 3 network requests per minute for overlapping data (both return candidate counts/scores). On mobile networks this wastes bandwidth unnecessarily.

**Fix:**  
Either disable `refreshInterval` in `useCandidates` on Dashboard (let `useRealTimeStats` be the sole poller), or merge both into a single endpoint/hook.

---

## 17. MEDIUM — Missing Confirmation for Destructive Settings Actions

**Category:** UX / Safety  
**Severity:** Medium  
**File:** `src/pages/Settings.tsx` lines 444–533  

**Description:**  
The "Cleanup Gibberish Profiles" (labeled **Destructive**), "Full Database Repair" (labeled **Heavy**), and "Re-score All" operations are triggered by single button clicks with **no confirmation dialog**. Only "Reset All" shortlist and "Shortlist All" in the AI Assistant use `confirm()`. A mis-click on "Cleanup" could delete real candidate data.

**Fix:**  
Add `if (!confirm('Are you sure? This action is destructive and cannot be undone.')) return` at the start of each destructive handler.

---

## 18. MEDIUM — `Settings.tsx` Uses Raw `fetch` Instead of `ApiClient`

**Category:** Bug / Inconsistency  
**Severity:** Medium  
**File:** `src/pages/Settings.tsx` lines 233–260, 262–289  

**Description:**  
`handleSaveProfile` and `handleUpdatePassword` use raw `fetch()` with manual auth header injection instead of the `ApiClient` (which handles retry, timeout, and 401 auto-logout). If the token expires during a profile save, the user won't be auto-redirected to login — they'll just see a generic "Failed to save profile" error.

**Fix:**  
Use `authFetch` or the API client's `request` method for consistency.

---

## 19. MEDIUM — `Shortlist.tsx` Imports `jsPDF` at Top Level

**Category:** Performance / Bundle Size  
**Severity:** Medium  
**File:** `src/pages/Shortlist.tsx` (top-level import)  

**Description:**  
`jsPDF` is a large library (~300KB) imported at the top of `Shortlist.tsx`. Since this page is lazy-loaded, all users who navigate to Shortlist pay the bundle cost even if they never export a PDF. The PDF feature is only used by `handleExportPDF`.

**Fix:**  
Dynamic import:
```tsx
const handleExportPDF = useCallback(async () => {
  const { jsPDF } = await import('jspdf')
  const doc = new jsPDF()
  // ...
}, [shortlistedCandidates])
```

---

## 20. MEDIUM — `useAIStatus` Has Missing Dependencies & No Cleanup

**Category:** Bug  
**Severity:** Medium  
**File:** `src/hooks/useAIStatus.ts`  

**Description:**  
The hook defines `checkAIStatus` outside the `useEffect` but doesn't include it in the dependency array. Also, there's no `AbortController` for cleanup — if the component unmounts before the fetch resolves, it will attempt to update state on an unmounted component. The `refresh` function returned by the hook creates a new identity every render since `checkAIStatus` isn't wrapped in `useCallback`.

**Fix:**  
Wrap `checkAIStatus` in `useCallback`, add it to the `useEffect` deps, and add an `AbortController`:
```tsx
const checkAIStatus = useCallback(async (signal?: AbortSignal) => {
  // ... existing logic with signal support
}, [])

useEffect(() => {
  const controller = new AbortController()
  checkAIStatus(controller.signal)
  return () => controller.abort()
}, [checkAIStatus])
```

---

## 21. MEDIUM — `AbortSignal.timeout(8000)` Browser Compatibility

**Category:** Compatibility  
**Severity:** Medium  
**File:** `src/hooks/useEmailSync.ts` line 55  

**Description:**  
`AbortSignal.timeout()` is a relatively new API (Chrome 103+, Firefox 100+, Safari 16.4+). Older browsers will throw `TypeError: AbortSignal.timeout is not a function`, breaking the entire email sync polling silently.

**Fix:**  
Use a manual timeout:
```tsx
const controller = new AbortController()
const timeoutId = setTimeout(() => controller.abort(), 8000)
try {
  const response = await fetch(url, { signal: controller.signal, ... })
  clearTimeout(timeoutId)
  // ...
} catch { clearTimeout(timeoutId); /* ... */ }
```

---

## 22. MEDIUM — `Candidates.tsx` Toast Timer Not Cleaned on Unmount

**Category:** Memory Leak  
**Severity:** Medium  
**File:** `src/pages/Candidates.tsx`  

**Description:**  
The page implements a custom local toast system with a `toastTimer` ref for auto-dismiss. However, the `useEffect` cleanup for this timer only clears on re-render, not on unmount. If the user navigates away while a toast is showing, the timer fires on an unmounted component.

**Fix:**  
Add unmount cleanup:
```tsx
useEffect(() => {
  return () => {
    if (toastTimer.current) clearTimeout(toastTimer.current)
  }
}, [])
```

---

## 23. MEDIUM — `authStore.updateProfile` Called Then Overwritten

**Category:** Bug / Logic Error  
**Severity:** Medium  
**File:** `src/pages/Settings.tsx` lines 208–218  

**Description:**  
`handleSaveProfile` calls `useAuthStore.getState().updateProfile?.(...)` followed immediately by `useAuthStore.setState({ user: { ...currentUser, ... } })` as a "fallback". The problem is `updateProfile` is an async function that calls the API and sets user from the response — but the synchronous `setState` below it **runs before the API responds**, immediately overwriting whatever state the profile API returns.

**Code:**
```tsx
useAuthStore.getState().updateProfile?.({ name, email, company }).catch(() => {})
// This runs BEFORE updateProfile's fetch completes:
const currentUser = useAuthStore.getState().user
if (currentUser) {
  useAuthStore.setState({ user: { ...currentUser, name: `${firstName} ${lastName}`.trim(), email, company } })
}
```

**Fix:**  
Either await the `updateProfile` call and don't use the fallback, or remove the redundant `updateProfile` call since the raw `fetch` above already handles the profile save:
```tsx
// The raw fetch above already saved the profile. Just update local state:
const currentUser = useAuthStore.getState().user
if (currentUser) {
  useAuthStore.setState({ user: { ...currentUser, name: `${firstName} ${lastName}`.trim(), email, company } })
}
// Remove the .updateProfile call entirely to avoid the race.
```

---

## 24. LOW — `useCandidates.ts` — `fetchCandidates` Missing from `useEffect` Deps

**Category:** Bug (Minor)  
**Severity:** Low  
**File:** `src/hooks/useCandidates.ts` line 224  

**Description:**  
The initial fetch `useEffect` disables the exhaustive-deps rule via `eslint-disable-line`. While `fetchCandidates` is stabilized by `useCallback`, the comment hides an intentional omission. The `refreshInterval` effect correctly includes `fetchCandidates` in deps. This works in practice but is fragile if `fetchCandidates` ever gains dependencies.

**Code:**
```tsx
}, [autoFetch]) // eslint-disable-line react-hooks/exhaustive-deps
```

**Fix:**  
Add `fetchCandidates` to the dependency array and remove the eslint-disable:
```tsx
}, [autoFetch, fetchCandidates])
```

---

## 25. LOW — `Shortlist.tsx` `handleBulkRemove` Is Sequential, Not Batched

**Category:** Performance  
**Severity:** Low  
**File:** `src/pages/Shortlist.tsx`  

**Description:**  
Removing multiple candidates from the shortlist is done one-by-one in a loop rather than using a single batch API call. With 20+ selected candidates, this creates 20+ sequential API requests, making the UI feel unresponsive.

**Fix:**  
If the backend supports a bulk status update endpoint, use it. Otherwise, use `Promise.all` for parallel execution:
```tsx
await Promise.all([...selectedIds].map(id => candidateApi.updateStatus(id, 'Reviewed')))
```

---

## 26. LOW — `AnalyticsDashboard.tsx` and `CampaignManager.tsx` — No Fetch Cleanup

**Category:** Memory Leak (Minor)  
**Severity:** Low  
**File:** `src/components/AnalyticsDashboard.tsx`, `src/components/CampaignManager.tsx`  

**Description:**  
Both components fetch data in `useEffect` with `[]` deps but provide no cleanup. If the component unmounts before the fetch completes, state updates will fire on unmounted components.

**Fix:**  
Add `let cancelled = false` pattern or `AbortController` in each `useEffect`.

---

## 27. LOW — `SearchReports.tsx` — `handleDeleteOne` Uses `s.id || s._id`

**Category:** Code Smell / Fragile  
**Severity:** Low  
**File:** `src/pages/SearchReports.tsx`  

**Description:**  
The `handleDeleteOne` function uses `s.id || s._id` to handle MongoDB's `_id` field inconsistency. This suggests the backend isn't normalizing IDs before sending them to the frontend, and could break if a search has `id: 0` or `id: ""` (falsy values).

**Fix:**  
Normalize IDs on the backend, or use `s.id ?? s._id` (nullish coalescing instead of OR) to only fall back on `null`/`undefined`.

---

## 28. LOW — `notificationStore` Array Mutation via `.length`

**Category:** Code Smell  
**Severity:** Low  
**File:** `src/store/notificationStore.ts` line 42  

**Description:**  
`updated.length = 100` mutates the array in-place by truncating it. While this works because `updated` is a new array (spread from state), it's an unusual pattern that may confuse developers. More importantly, Zustand's shallow comparison may not detect the change if the array reference hasn't changed (though in this case it has via the spread).

**Fix:**
```tsx
const trimmed = updated.slice(0, 100)
return { notifications: trimmed, unreadCount: trimmed.filter(n => !n.read).length }
```

---

## 29. LOW — `config.ts` Named Export vs Default Export Ambiguity

**Category:** Code Smell  
**Severity:** Low  
**File:** `src/config.ts` lines 72, 82  

**Description:**  
The file exports `config` as both a named export (`export const config`) and a default export (`export default config`). Some files import `config` as a default import, others may use the named import. This doesn't cause a bug today but makes imports inconsistent and can confuse IDEs.

**Fix:**  
Pick one pattern and use it everywhere. Default export is used by most consumers, so remove the named export or vice versa.

---

## Architecture-Level Observations (Not Bugs)

These are not bugs but structural risks that could cause issues at scale:

1. **`authStore.updateProfile` and `authStore.changePassword` bypass the `ApiClient` class** (raw `fetch`), losing retry logic, timeout handling, and 401 auto-redirect.

2. **`Dashboard.tsx` "Max 100MB" text in upload modal** — same bug as Finding #4, duplicated here in the Dashboard's upload modal.

3. **The `aiAnalysis.hiring_recommendation.replace('_', ' ')` pattern** appears in both `CandidateDetail.tsx` and `Shortlist.tsx` — it only replaces the *first* underscore. `STRONGLY_RECOMMEND` becomes `"STRONGLY RECOMMEND"` (correct by luck), but `NO_STRONG_FIT` would become `"NO STRONG_FIT"`. Use `.replace(/_/g, ' ')` (already used in `AIAssistant.tsx`).

4. **`CandidateDetail.tsx` lines 300–313**: The auto-trigger pattern (fetch cached analysis → if none, set `autoTriggered` → separate effect calls `handleAIAnalysis`) adds complexity. It could be simplified to a single `useEffect` with proper guards.

5. **`types/index.ts`** defines `CandidateState` and `AuthState` interfaces that overlap but don't match the actual store interfaces. These unused type duplicates could mislead developers.
