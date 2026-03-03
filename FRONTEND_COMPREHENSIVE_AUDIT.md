# Frontend Comprehensive Audit Report

**Project:** Efforts Recruitment Tool  
**Stack:** React 18.2 + TypeScript 5.3.3 + Vite 5.0 + Zustand 4.4.7 + Tailwind CSS 3.4.1  
**Audit Date:** 2025  
**Files Audited:** 50+ source files across `src/`

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 5     |
| HIGH     | 18    |
| MEDIUM   | 32    |
| LOW      | 17    |
| **Total**| **72**|

---

## CRITICAL Issues

### C-1. Authentication useEffect runs once — never re-verifies on token change
- **FILE:** [src/App.tsx](src/App.tsx#L86-L92)
- **SEVERITY:** CRITICAL
- **CATEGORY:** Bug / Security
- **DESCRIPTION:** The `useEffect` that calls `verifyToken()` has an empty dependency array `[]`. If the token changes in `sessionStorage` (e.g. after re-login in another tab), the app never re-verifies. The user may operate with a stale or expired token, or an attacker could inject a token that never gets validated.
- **FIX:** Add `[token]` to the dependency array and wrap `verifyToken` in `useCallback`. Also consider adding a periodic token health-check interval.

```tsx
// Before
useEffect(() => {
  const token = sessionStorage.getItem('token')
  if (token) { verifyToken() }
}, [])

// After
useEffect(() => {
  if (token) { verifyToken() }
}, [token, verifyToken])
```

---

### C-2. useAsync hook can trigger an infinite render loop
- **FILE:** [src/hooks/useAsync.ts](src/hooks/useAsync.ts)
- **SEVERITY:** CRITICAL
- **CATEGORY:** Bug / Performance
- **DESCRIPTION:** The `execute` function is created with `useCallback` depending on `asyncFunction`. The `immediate` useEffect includes `execute` in its dependency array. If the consumer passes an unstable (inline) `asyncFunction`, `execute` is recreated every render → useEffect fires again → re-renders → infinite loop. This will freeze the browser tab.
- **FIX:** Remove `execute` from the `immediate` useEffect's dependency array, or use a ref for the async function to break the cycle.

```tsx
// Safer pattern:
const asyncFnRef = useRef(asyncFunction)
asyncFnRef.current = asyncFunction

const execute = useCallback(async (...args) => {
  // use asyncFnRef.current instead of asyncFunction
}, []) // stable — no deps on asyncFunction

useEffect(() => {
  if (immediate) execute()
}, [immediate]) // no execute dep needed since it's stable
```

---

### C-3. AIAssistant.tsx is 3,498 lines — unmaintainable monolith
- **FILE:** [src/pages/AIAssistant.tsx](src/pages/AIAssistant.tsx)
- **SEVERITY:** CRITICAL
- **CATEGORY:** Architecture / Maintenance
- **DESCRIPTION:** A single component file with 3,498 lines containing: a modal component, session management logic, NLP query parsing, chat UI, results split-panel, candidate preview panel, and job matching. This violates single-responsibility, makes code review impossible, causes massive bundle impact, and any change risks regressions across unrelated features.
- **FIX:** Split into at least 8 modules:
  - `AIAssistant.tsx` — main orchestrator (~200 lines)
  - `components/JobMatchModal.tsx` — modal component
  - `components/ResultsPanel.tsx` — split-panel results view
  - `components/CandidatePreviewPanel.tsx` — candidate detail preview
  - `components/ChatHistory.tsx` — sidebar history
  - `hooks/useChatSession.ts` — session persistence logic
  - `hooks/useAIChat.ts` — message handling, send logic
  - `utils/queryParser.ts` — NLP query parsing
  - `utils/chatSerializer.ts` — save/restore chat history

---

### C-4. Candidates.tsx uses local toast state instead of global Toast system
- **FILE:** [src/pages/Candidates.tsx](src/pages/Candidates.tsx#L1065-L1090)
- **SEVERITY:** CRITICAL
- **CATEGORY:** Bug / Consistency
- **DESCRIPTION:** The component maintains its own custom `toast` state (`useState<{visible, message, type}>`) and renders its own toast notification at the bottom of the page. Meanwhile, a global `toast` system from `@/components/ui/Toast` exists and is used by every other page. This means: (1) the local `toast` variable name shadows the imported `toast` from Toast.tsx if imported, (2) users see inconsistent toast styling, (3) toasts may overlap or conflict with the global toast container.
- **FIX:** Replace the local toast state with the global `toast.success()`, `toast.error()`, `toast.info()` calls from `@/components/ui/Toast`, and remove the custom toast JSX at the bottom of the component.

---

### C-5. Settings.tsx uses raw `fetch()` bypassing auth interceptors
- **FILE:** [src/pages/Settings.tsx](src/pages/Settings.tsx#L257-L272)
- **SEVERITY:** CRITICAL
- **CATEGORY:** Bug / Security
- **DESCRIPTION:** `handleUpdatePassword` uses raw `fetch()` instead of `authFetch()`, manually constructing the Authorization header. This bypasses the `authFetch` utility's `cache: 'no-store'` header and any future auth interceptor logic (e.g., automatic token refresh, error handling). If `token` is null, the request goes out without auth but doesn't fail gracefully.
- **FIX:** Replace `fetch()` with `authFetch()`:
```tsx
const response = await authFetch(`${config.apiUrl}/api/users/password`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ currentPassword, newPassword })
})
```

---

## HIGH Issues

### H-1. useRealTimeStats: fetchStats missing from useEffect deps
- **FILE:** [src/hooks/useRealTimeStats.ts](src/hooks/useRealTimeStats.ts#L116)
- **SEVERITY:** HIGH
- **CATEGORY:** Bug / Stale Closure
- **DESCRIPTION:** The `useEffect` that sets up the polling interval uses `[enabled, interval]` as deps, but calls `fetchStats` which is defined inside the hook. If the component re-renders with different configuration, `fetchStats` may reference stale state. The initial call and interval both use the stale closure.
- **FIX:** Either add `fetchStats` to the deps array (ensuring it's memoized with `useCallback`) or use a ref pattern for the fetch function.

---

### H-2. useAIStatus: stale closure + no abort controller
- **FILE:** [src/hooks/useAIStatus.ts](src/hooks/useAIStatus.ts)
- **SEVERITY:** HIGH
- **CATEGORY:** Bug / Memory Leak
- **DESCRIPTION:** `checkAIStatus` is defined inside the component but called in `useEffect` with empty deps `[]`. This means it captures the initial state forever. Additionally, there is no `AbortController` — if the component unmounts before the fetch completes, it will attempt to `setState` on an unmounted component.
- **FIX:** Add an AbortController for cleanup, and use a ref or stable callback pattern:
```tsx
useEffect(() => {
  const controller = new AbortController()
  const check = async () => {
    try {
      const res = await fetch(url, { signal: controller.signal })
      // ...set state
    } catch (e) { if (!controller.signal.aborted) { /* handle */ } }
  }
  check()
  return () => controller.abort()
}, [])
```

---

### H-3. Global CSS `*` selector applies transitions to every element
- **FILE:** [src/index.css](src/index.css#L85)
- **SEVERITY:** HIGH
- **CATEGORY:** Performance
- **DESCRIPTION:** `* { @apply transition-colors; }` applies `transition-property: color, background-color, border-color, text-decoration-color, fill, stroke; transition-timing-function: ease; transition-duration: 150ms;` to **every DOM element**. This causes the browser to set up transition watchers on thousands of elements, degrading paint/composite performance — especially on pages with large tables (Candidates: 500+ rows).
- **FIX:** Remove the global `*` rule. Apply `transition-colors` only to interactive elements (buttons, links, inputs) via Tailwind utility classes where needed.

---

### H-4. Widespread `any` types defeating TypeScript strict mode
- **FILE:** Multiple files
- **SEVERITY:** HIGH
- **CATEGORY:** Type Safety
- **DESCRIPTION:** Despite `strict: true` in tsconfig, `any` is used pervasively:
  | File | Location | Usage |
  |------|----------|-------|
  | [TopBar.tsx](src/components/layout/TopBar.tsx#L37) | L37 | `notification: any` |
  | [TopBar.tsx](src/components/layout/TopBar.tsx#L183) | L183 | `user: any` in UserMenu |
  | [authStore.ts](src/store/authStore.ts#L69) | L69, L113 | `error: any` in catches |
  | [api.ts](src/services/api.ts#L523) | L523 | `ApiResponse<any>` for dashboard stats |
  | [api.ts](src/services/api.ts#L942) | L942 | `as any` cast for LinkedIn candidates |
  | [UploadFiles.tsx](src/pages/UploadFiles.tsx#L21) | L21, L29 | `any[]` for upload/scrape results |
  | [CandidateDetail.tsx](src/pages/CandidateDetail.tsx#L800) | Multiple | `job: any`, `edu: any` in workHistory/education mapping |
  | [AIAssistant.tsx](src/pages/AIAssistant.tsx) | Multiple | `any` in session messages, preview analysis, chat results |
  | [types/index.ts](src/types/index.ts#L222) | L222 | `Record<string, any>` in StatsResponse |
  | [SetupWizard.tsx](src/pages/SetupWizard.tsx#L131) | L131 | `platformStats: any` |
- **FIX:** Define proper interfaces for each `any` usage. For example, create `DashboardStats`, `UploadResult`, `ScrapeResult`, `Notification`, `WorkHistoryEntry`, `EducationEntry` types and use them throughout.

---

### H-5. Duplicate code: categoryColors defined in 4 files
- **FILE:** [src/lib/utils.ts](src/lib/utils.ts), [src/pages/Candidates.tsx](src/pages/Candidates.tsx), [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx), [src/pages/AIAssistant.tsx](src/pages/AIAssistant.tsx)
- **SEVERITY:** HIGH
- **CATEGORY:** Consistency / Maintenance
- **DESCRIPTION:** The `categoryColors` map (mapping job categories like "Engineering", "Marketing", etc. to Tailwind color classes) is copy-pasted in 4 different files. Any color scheme change requires editing all 4 files, and they can easily drift out of sync.
- **FIX:** Export a single `getCategoryColor()` function from `src/lib/utils.ts` and import it in all three page files. Delete the duplicated definitions.

---

### H-6. Duplicate code: ScoreRing/score utilities in 3 files
- **FILE:** [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx#L45-L92), [src/pages/AIAssistant.tsx](src/pages/AIAssistant.tsx#L35-L92), [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx)
- **SEVERITY:** HIGH
- **CATEGORY:** Consistency / Maintenance
- **DESCRIPTION:** `getScoreColor()`, `getScoreRingColor()`, `getFitLabel()`, and the `ScoreRing` SVG component are duplicated across Shortlist.tsx, AIAssistant.tsx, and CandidateDetail.tsx (each with slightly different thresholds). A `ScoreCircle` component already exists in `src/components/ui/ScoreCircle.tsx` but is not used by these pages.
- **FIX:** Consolidate into the existing `ScoreCircle` component or create a shared `src/lib/scoreUtils.ts`. Import everywhere.

---

### H-7. Duplicate code: cleanLocation function in 2 files
- **FILE:** [src/lib/utils.ts](src/lib/utils.ts), [src/pages/AIAssistant.tsx](src/pages/AIAssistant.tsx#L20-L30)
- **SEVERITY:** HIGH
- **CATEGORY:** Consistency
- **DESCRIPTION:** `cleanLocation()` is defined in both `utils.ts` and at the top of `AIAssistant.tsx` with identical logic.
- **FIX:** Import from `@/lib/utils` in AIAssistant.tsx. Delete the local copy.

---

### H-8. Duplicate fmtDate functions instead of using shared utility
- **FILE:** [src/pages/Dashboard.tsx](src/pages/Dashboard.tsx), [src/pages/SearchReports.tsx](src/pages/SearchReports.tsx#L68), [src/pages/SetupWizard.tsx](src/pages/SetupWizard.tsx)
- **SEVERITY:** HIGH
- **CATEGORY:** Consistency
- **DESCRIPTION:** Three pages define their own `fmtDate` function for date formatting while `formatDate()` exists in `src/lib/utils.ts`.
- **FIX:** Import and use `formatDate` from `@/lib/utils`. Delete local `fmtDate` definitions.

---

### H-9. Shortlist toggle logic duplicated with massive inline handlers
- **FILE:** [src/pages/Candidates.tsx](src/pages/Candidates.tsx#L740-L810), duplicate at [L950-L1020](src/pages/Candidates.tsx#L950-L1020)
- **SEVERITY:** HIGH
- **CATEGORY:** Maintenance / DRY
- **DESCRIPTION:** The shortlist toggle handler (confirm → API call → update local state → show toast → refetch) is a ~40-line inline `onClick` handler that is **copy-pasted twice** within the same file (once for grouped view, once for list view). This is ~80 lines of duplicated complex async logic.
- **FIX:** Extract into a shared `handleToggleShortlist(candidate)` function defined at the component level.

---

### H-10. SearchReports: fetchHistory called in useEffect without cleanup
- **FILE:** [src/pages/SearchReports.tsx](src/pages/SearchReports.tsx#L40-L46)
- **SEVERITY:** HIGH
- **CATEGORY:** Bug / Memory Leak
- **DESCRIPTION:** `useEffect(() => { fetchHistory() }, [])` fires an async fetch with no AbortController. If the component unmounts before the fetch completes, `setSearches` will be called on an unmounted component. React 18 StrictMode will double-invoke this in development, causing duplicate fetches.
- **FIX:** Add an AbortController and cleanup function.

---

### H-11. Missing keyboard accessibility on interactive elements
- **FILE:** Multiple files
- **SEVERITY:** HIGH
- **CATEGORY:** Accessibility
- **DESCRIPTION:** Many interactive elements use `<div onClick>` or `<span onClick>` without keyboard support:
  | File | Element | Issue |
  |------|---------|-------|
  | [TopBar.tsx](src/components/layout/TopBar.tsx) | Notification dropdown | No `onKeyDown`, not focusable |
  | [Candidates.tsx](src/pages/Candidates.tsx) | Category cards | `<Card onClick>` without `role="button"` or `tabIndex` |
  | [Candidates.tsx](src/pages/Candidates.tsx) | Category headers | `<div onClick>` toggle without keyboard |
  | [Shortlist.tsx](src/pages/Shortlist.tsx) | Candidate list items | `<motion.div onClick>` without `role` or `tabIndex` |
  | [AIAssistant.tsx](src/pages/AIAssistant.tsx) | Results list items | `<motion.div onClick>` without keyboard support |
  | [SetupWizard.tsx](src/pages/SetupWizard.tsx) | Provider cards | `<div onClick>` without `role="radio"` |
- **FIX:** Add `role="button"`, `tabIndex={0}`, and `onKeyDown` (Enter/Space) to all clickable non-button elements. Use semantic `<button>` elements where possible.

---

### H-12. Missing ARIA labels on icon-only buttons
- **FILE:** Multiple files  
- **SEVERITY:** HIGH
- **CATEGORY:** Accessibility
- **DESCRIPTION:** Many icon-only buttons have `title` but no `aria-label`:
  - Contact icons in Candidates.tsx (Email, WhatsApp, LinkedIn, Phone)
  - Sort buttons in Shortlist.tsx
  - Download/PDF buttons across pages
  - Search clear buttons
- **FIX:** Add `aria-label` to all icon-only buttons and clickable elements.

---

### H-13. Candidates.tsx is 1,105 lines — should be split
- **FILE:** [src/pages/Candidates.tsx](src/pages/Candidates.tsx)
- **SEVERITY:** HIGH
- **CATEGORY:** Architecture
- **DESCRIPTION:** Single component with 1,105 lines handling: filters, sorting, grouping, two view modes (list/grouped), inline shortlisting, contact actions, and a custom toast. All logic is in one function.
- **FIX:** Split into: `CandidatesPage.tsx` (orchestrator), `CandidateFilters.tsx`, `CandidateGroupedView.tsx`, `CandidateListView.tsx`, `CandidateRow.tsx`, and a `useCandidateFilters.ts` hook.

---

### H-14. CandidateDetail.tsx is 1,131 lines — should be split
- **FILE:** [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx)
- **SEVERITY:** HIGH
- **CATEGORY:** Architecture
- **DESCRIPTION:** Single component handling: data fetching, AI analysis, shortlisting, PDF generation, resume upload, interview scheduling with calendar picker modal, rejection with confirmation modal, and full profile display.
- **FIX:** Split into: `CandidateDetailPage.tsx`, `AIAnalysisCard.tsx`, `CandidateHeroCard.tsx`, `ResumeSection.tsx`, `CalendarPickerModal.tsx`, `RejectConfirmModal.tsx`.

---

### H-15. Shortlist.tsx handleBulkRemove: sequential API calls
- **FILE:** [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx#L165-L178)
- **SEVERITY:** HIGH
- **CATEGORY:** Performance
- **DESCRIPTION:** `handleBulkRemove` loops through selected IDs and calls `candidateApi.updateStatus` sequentially in a `for...of` loop. For 20 selected candidates, this makes 20 sequential HTTP requests. Errors are silently swallowed with `catch { /* skip */ }`.
- **FIX:** Use `Promise.allSettled()` for parallel execution, and report individual failures:
```tsx
const results = await Promise.allSettled(
  idsToRemove.map(id => candidateApi.updateStatus(id, 'Reviewed'))
)
const removed = results.filter(r => r.status === 'fulfilled').length
const failed = results.filter(r => r.status === 'rejected').length
```

---

### H-16. SetupWizard.tsx: password stored in component state  
- **FILE:** [src/pages/SetupWizard.tsx](src/pages/SetupWizard.tsx#L133)
- **SEVERITY:** HIGH
- **CATEGORY:** Security
- **DESCRIPTION:** The email provider password is stored in React state (`const [password, setPassword] = useState('')`) and sent in the request body to connect/sync endpoints. React DevTools can expose this. The password persists in memory until the component unmounts.
- **FIX:** Clear the password state immediately after use (`setPassword('')`), and ideally use a ref instead of state to avoid it being visible in React DevTools. Better yet, use OAuth2 flow for all providers.

---

### H-17. Missing error boundaries on page-level routes
- **FILE:** [src/App.tsx](src/App.tsx)
- **SEVERITY:** HIGH
- **CATEGORY:** Error Handling
- **DESCRIPTION:** While an `ErrorBoundary` wraps the entire app, individual routes don't have error boundaries. A crash in `AIAssistant` (the most complex page) will take down the entire app instead of just showing an error state for that page.
- **FIX:** Add route-level error boundaries:
```tsx
<Route path="/ai-assistant" element={
  <ErrorBoundary>
    <Suspense fallback={<Loading />}>
      <AIAssistant />
    </Suspense>
  </ErrorBoundary>
} />
```

---

### H-18. authStore: type assertion bypasses type checking
- **FILE:** [src/store/authStore.ts](src/store/authStore.ts#L236)
- **SEVERITY:** HIGH
- **CATEGORY:** Type Safety
- **DESCRIPTION:** `partialize: (state) => ({ ... }) as unknown as AuthState` uses double assertion (`as unknown as AuthState`) to bypass TypeScript. This means the partialize function could return an object missing required fields, and TypeScript would not catch it.
- **FIX:** Type the partialize return properly or use `Pick<AuthState, 'token' | 'user'>` and adjust the store type accordingly.

---

## MEDIUM Issues

### M-1. useCandidates: eslint-disable for exhaustive-deps
- **FILE:** [src/hooks/useCandidates.ts](src/hooks/useCandidates.ts#L237)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Bug Risk
- **DESCRIPTION:** `// eslint-disable-next-line react-hooks/exhaustive-deps` suppresses the warning about missing dependencies. The `fetchCandidates` function likely needs to be in the deps array to avoid stale closures.
- **FIX:** Wrap `fetchCandidates` in `useCallback` with proper dependencies and include it in the useEffect deps. Remove the eslint-disable.

---

### M-2. useEmailSync: dead sessionStorage cache references
- **FILE:** [src/hooks/useEmailSync.ts](src/hooks/useEmailSync.ts#L90-L92)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Dead Code
- **DESCRIPTION:** Calls `sessionStorage.removeItem('candidates_cache')`, `sessionStorage.removeItem('candidates_cache_ts')`, and `sessionStorage.removeItem('dashboard_cache')`. These cache keys are never set anywhere in the codebase (the caching was removed from useCandidates). This is dead code that misleads future developers.
- **FIX:** Remove the three `sessionStorage.removeItem` calls.

---

### M-3. useLocalStorage: stale value in setValue
- **FILE:** [src/hooks/useLocalStorage.ts](src/hooks/useLocalStorage.ts)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Bug
- **DESCRIPTION:** The `setValue` function depends on `storedValue` in its closure. If `setValue` is called rapidly (e.g., debounced input), it may use a stale `storedValue` for the functional update.
- **FIX:** Use a functional state updater pattern:
```tsx
const setValue = useCallback((value: T | ((val: T) => T)) => {
  setStoredValue(prev => {
    const valueToStore = value instanceof Function ? value(prev) : value
    localStorage.setItem(key, JSON.stringify(valueToStore))
    return valueToStore
  })
}, [key])
```

---

### M-4. candidateStore: shortlistedIds not persisted
- **FILE:** [src/store/candidateStore.ts](src/store/candidateStore.ts)
- **SEVERITY:** MEDIUM
- **CATEGORY:** UX
- **DESCRIPTION:** `shortlistedIds` is stored only in Zustand memory state. A page refresh or new tab loses all shortlist selections. The Shortlist page now uses backend `status === 'Shortlisted'` instead, making this local store partially redundant and confusing.
- **FIX:** Either persist `shortlistedIds` to localStorage using Zustand `persist` middleware (like `notificationStore` does), or remove the local shortlist tracking entirely since the backend is the source of truth.

---

### M-5. UploadFiles: handleEmailScrape not wrapped in useCallback
- **FILE:** [src/pages/UploadFiles.tsx](src/pages/UploadFiles.tsx#L73)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Performance
- **DESCRIPTION:** `handleEmailScrape` is an async function defined directly in the component body (not in `useCallback`). It's recreated on every render, though it's only used as a button onClick.
- **FIX:** Wrap in `useCallback` with `[refetch]` dependency.

---

### M-6. Notification settings checkboxes are not persisted
- **FILE:** [src/pages/Settings.tsx](src/pages/Settings.tsx#L480-L508)
- **SEVERITY:** MEDIUM
- **CATEGORY:** UX / Bug
- **DESCRIPTION:** The "Email Notifications", "Match Alerts", and "Weekly Summary" checkboxes use local `useState` but are never saved to the backend or localStorage. Toggling them has no effect — they reset on page refresh.
- **FIX:** Either connect to a backend preferences API and save on change, or persist to localStorage and integrate with the notification system.

---

### M-7. LoginPage: "Remember me" checkbox non-functional
- **FILE:** [src/pages/LoginPage.tsx](src/pages/LoginPage.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** UX
- **DESCRIPTION:** The "Remember me" checkbox is rendered but has no associated state or functionality. Clicking it does nothing. The token is always stored in `sessionStorage` regardless.
- **FIX:** If implementing: store token in `localStorage` when checked (persists across sessions) vs `sessionStorage` (current session only). If not implementing: remove the checkbox to avoid confusing users.

---

### M-8. Missing form label associations
- **FILE:** Multiple files
- **SEVERITY:** MEDIUM
- **CATEGORY:** Accessibility
- **DESCRIPTION:** Many form inputs use `<label>` elements for visual text but without `htmlFor` attributes linking to the input's `id`. Screen readers cannot associate labels with inputs.
  - [Settings.tsx](src/pages/Settings.tsx) — Profile form fields
  - [JDBuilder.tsx](src/pages/JDBuilder.tsx) — Job details form
  - [UploadFiles.tsx](src/pages/UploadFiles.tsx) — Email scraping inputs
  - [SetupWizard.tsx](src/pages/SetupWizard.tsx) — Connection form
- **FIX:** Add matching `id` to inputs and `htmlFor` to labels:
```tsx
<label htmlFor="job-title" className="...">Job Title *</label>
<input id="job-title" value={form.title} ... />
```

---

### M-9. Missing loading states for several pages
- **FILE:** [src/pages/SearchReports.tsx](src/pages/SearchReports.tsx), [src/pages/JDBuilder.tsx](src/pages/JDBuilder.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** UX
- **DESCRIPTION:** SearchReports shows a spinner for loading but JDBuilder has no initial loading state or skeleton. The generated JD area shows "No JD generated yet" even during the brief period when the component is first rendering.
- **FIX:** Add consistent loading skeletons to all pages wrapping data-dependent content.

---

### M-10. Shortlist export PDF generation: no error detail
- **FILE:** [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx#L235-L257)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Error Handling
- **DESCRIPTION:** `handleExportPDF` has a bare `catch { addNotification(...) }` — the error is not logged. If PDF generation fails, developers have no way to diagnose.
- **FIX:** `catch (error) { console.error('PDF export error:', error); addNotification(...) }`

---

### M-11. Toast.tsx: onDismiss callback identity changes every render
- **FILE:** [src/components/ui/Toast.tsx](src/components/ui/Toast.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Performance
- **DESCRIPTION:** `ToastItem` receives `onDismiss={() => removeToast(t.id)}` as a prop. This creates a new function reference every render, causing unnecessary re-renders of `ToastItem` even if nothing changed.
- **FIX:** Memoize the callback or pass `id` and `removeToast` separately to `ToastItem` so it can call `removeToast(id)` internally.

---

### M-12. Dashboard.tsx: inline upload modal instead of Dialog component
- **FILE:** [src/pages/Dashboard.tsx](src/pages/Dashboard.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Consistency
- **DESCRIPTION:** The upload modal is implemented as a raw `<div>` with manual overlay/close logic, while a `Dialog` component from `@/components/ui/Dialog` exists and follows Radix UI patterns with proper focus trapping, Escape key handling, and backdrop click.
- **FIX:** Replace the custom modal with the `Dialog` component.

---

### M-13. API service: inconsistent return types
- **FILE:** [src/services/api.ts](src/services/api.ts)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Type Safety
- **DESCRIPTION:** The API service has inconsistent typing patterns:
  - `statsApi.getDashboard()` returns `ApiResponse<any>` — should be `ApiResponse<DashboardStats>`
  - `advancedApi` methods mostly lack return type annotations
  - `linkedInApi.getLinkedInCandidates()` casts response `as any`
  - Some methods return `ApiResponse<T>` while others return raw data
- **FIX:** Define response types for all API endpoints and remove `any` casts.

---

### M-14. AIAssistant: session messages type uses `any[]`
- **FILE:** [src/pages/AIAssistant.tsx](src/pages/AIAssistant.tsx#L583)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Type Safety
- **DESCRIPTION:** `ChatSession.messages` is typed as `any[]` instead of properly typed serialized message format.
- **FIX:** Define `SerializedMessage` interface and use it:
```tsx
interface SerializedMessage {
  id: string
  type: 'user' | 'ai'
  content: string
  timestamp: string
  candidates?: Candidate[]
  intent?: string
}
interface ChatSession {
  messages: SerializedMessage[]
  // ...
}
```

---

### M-15. CandidateDetail: complex merge logic with untyped data
- **FILE:** [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx#L200-L300)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Type Safety / Maintainability
- **DESCRIPTION:** The candidate data merging between "light" (from list) and "full" (from API detail endpoint) uses many `any` casts for `workHistory`, `education`, and `aiAnalysis` mapping. Field name mismatches between API formats (e.g., `position` vs `title`, `school` vs `institution`) are handled with fallback chaining.
- **FIX:** Define a `RawCandidateResponse` interface matching the exact API shape and create a typed transformer function.

---

### M-16. SetupWizard: useEffect with implicit function references
- **FILE:** [src/pages/SetupWizard.tsx](src/pages/SetupWizard.tsx#L145-L149)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Bug Risk
- **DESCRIPTION:** 
```tsx
useEffect(() => {
  fetchAll()
  const interval = setInterval(fetchOAuthStatus, 30000)
  return () => clearInterval(interval)
}, [])
```
Neither `fetchAll` nor `fetchOAuthStatus` are in the dependency array. If these functions reference state, they'll use stale closures. Additionally, `fetchOAuthStatus` runs every 30 seconds indefinitely even if the tab is hidden.
- **FIX:** Use `useCallback` for both functions and add them to deps. Consider using `document.visibilityState` to pause polling when the tab is hidden.

---

### M-17. Multiple pages don't handle AbortController for fetch cleanup
- **FILE:** [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx), [src/pages/UploadFiles.tsx](src/pages/UploadFiles.tsx), [src/pages/JDBuilder.tsx](src/pages/JDBuilder.tsx), [src/pages/Settings.tsx](src/pages/Settings.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Bug / Memory Leak
- **DESCRIPTION:** API calls initiated by user actions (button clicks) don't use AbortController. If the user navigates away while a request is in-flight, the `.then()` handler will try to update state on an unmounted component.
- **FIX:** Store an AbortController ref and abort in the useEffect cleanup or when navigating away.

---

### M-18. Framer-motion AnimatePresence overuse
- **FILE:** [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx#L435), [src/pages/Candidates.tsx](src/pages/Candidates.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Performance
- **DESCRIPTION:** `AnimatePresence mode="popLayout"` wraps candidate lists. With 100+ candidates, layout animations trigger expensive DOM measurements on every add/remove. The Candidates page comments even note "No per-row animations for better performance" but still uses AnimatePresence with `mode="popLayout"` in Shortlist.
- **FIX:** Remove AnimatePresence from lists with many items. Use CSS transitions for simpler hover/focus effects.

---

### M-19. pdfGenerator.ts: 1,661-line utility file
- **FILE:** [src/lib/pdfGenerator.ts](src/lib/pdfGenerator.ts)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Architecture
- **DESCRIPTION:** A 1,661-line file containing complex PDF rendering logic with hand-coded gradient functions, arc drawing, layout management, etc. While it works, it's very difficult to maintain and debug.
- **FIX:** Consider splitting into `pdfLayout.ts`, `pdfDrawing.ts`, `pdfTemplates.ts`, and `pdfGenerator.ts` (orchestrator).

---

### M-20. AIAssistant parseQuery: hardcoded skill list
- **FILE:** [src/pages/AIAssistant.tsx](src/pages/AIAssistant.tsx#L1150-L1160)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Maintainability
- **DESCRIPTION:** The local NLP fallback has a hardcoded array of skills: `['react', 'python', 'javascript', 'java', ...]`. This will miss any skill not in the list, and needs manual updates.
- **FIX:** Use the candidate data to build a dynamic skill index: `const allSkills = new Set(candidates.flatMap(c => c.skills.map(s => s.toLowerCase())))`, then match against that.

---

### M-21. Inconsistent status color mapping
- **FILE:** [src/pages/Candidates.tsx](src/pages/Candidates.tsx), [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx), [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Consistency
- **DESCRIPTION:** Each page defines its own status-to-color mapping (e.g., "Strong" → green, "Reviewed" → blue). These are slightly different across pages.
- **FIX:** Create a shared `getStatusColor(status: string)` utility in `utils.ts`.

---

### M-22. AIAssistant: `confirm()` used for destructive actions
- **FILE:** [src/pages/AIAssistant.tsx](src/pages/AIAssistant.tsx), [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** UX
- **DESCRIPTION:** Browser `confirm()` and `prompt()` are used for shortlist/reset confirmations. These block the main thread, look different across browsers, can't be styled, and feel jarring in a modern SPA.
- **FIX:** Use the existing `Dialog` component for confirmation modals.

---

### M-23. CandidateDetail: auto AI analysis trigger pattern
- **FILE:** [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** UX / Performance
- **DESCRIPTION:** The page auto-triggers AI analysis on mount via `autoTriggered` state. This makes an API call to the AI service every time a user views a candidate detail, even if analysis was already cached. This costs API credits and adds latency.
- **FIX:** Check for existing cached analysis first and only auto-trigger if none exists. Show a "Run AI Analysis" button for manual re-analysis.

---

### M-24. Multiple pages: no empty state for error conditions
- **FILE:** [src/pages/UploadFiles.tsx](src/pages/UploadFiles.tsx), [src/pages/SetupWizard.tsx](src/pages/SetupWizard.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Error Handling / UX
- **DESCRIPTION:** If the initial data fetch fails (network error, 500), these pages show no error state — they either remain in a loading spinner forever or show empty data without explanation.
- **FIX:** Add error states with retry buttons for all pages that fetch data on mount.

---

### M-25. Uncontrolled to controlled input warnings risk
- **FILE:** [src/pages/Settings.tsx](src/pages/Settings.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Bug
- **DESCRIPTION:** State for profile fields (`firstName`, `lastName`, `email`, `company`) is initialized from `user?.name?.split(...)` which can be `undefined` initially (before auth resolves). If the user object loads asynchronously, the inputs may start as uncontrolled (`value={undefined}`) then become controlled (`value="John"`), causing a React warning.
- **FIX:** Initialize with empty strings: `useState(user?.name?.split(' ')[0] || '')`.

---

### M-26. Missing `key` prop in mapped elements
- **FILE:** [src/pages/SearchReports.tsx](src/pages/SearchReports.tsx#L117)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Bug
- **DESCRIPTION:** `{filtered.map((s) => { ... return (<tr key={s.id} ...>)` uses `s.id` but the interface shows `id` and `_id` as separate fields, and the delete handler uses `s.id || s._id`. If `s.id` is undefined, React will have duplicate `undefined` keys.
- **FIX:** Use `key={s.id || s._id || idx}` to ensure uniqueness.

---

### M-27. Token stored in sessionStorage without encryption
- **FILE:** [src/store/authStore.ts](src/store/authStore.ts)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Security
- **DESCRIPTION:** JWT tokens are stored in plain text in `sessionStorage`. While sessionStorage is tab-scoped (better than localStorage), any XSS vulnerability exposes the token. The app does use DOMPurify for HTML sanitization in AIAssistant, but other pages render user-provided data without sanitization.
- **FIX:** Consider using httpOnly cookies for token storage (requires backend changes). As a mitigation, ensure all user-provided content is sanitized before rendering.

---

### M-28. Candidates.tsx: displayLimit state for pagination
- **FILE:** [src/pages/Candidates.tsx](src/pages/Candidates.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Performance
- **DESCRIPTION:** Pagination is done via `displayLimit` state that increases by 50 on "Load More" click. However, the full candidate array is still filtered and sorted on every render. With 1000+ candidates, the sorting/filtering runs on the entire dataset even though only 50-100 are displayed.
- **FIX:** Consider server-side pagination, or use `useMemo` with proper dependency tracking to avoid re-computing on unrelated re-renders.

---

### M-29. Dicebear avatar URLs generated on every render
- **FILE:** [src/pages/Candidates.tsx](src/pages/Candidates.tsx), [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx), [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Performance
- **DESCRIPTION:** Avatar URLs like `https://api.dicebear.com/7.x/initials/svg?seed=${candidate.name}` are constructed inline in JSX. While the browser caches these, the string template is re-evaluated on every render for every candidate.
- **FIX:** Memoize avatar URLs or compute them once during the data transformation step.

---

### M-30. Missing Suspense boundaries for lazy-loaded routes
- **FILE:** [src/App.tsx](src/App.tsx)
- **SEVERITY:** MEDIUM
- **CATEGORY:** UX
- **DESCRIPTION:** All lazy-loaded pages share a single `<Suspense>` boundary wrapping the entire route tree. If one lazy component fails to load (network error), the entire app falls back to the loading spinner with no retry mechanism.
- **FIX:** Add individual Suspense boundaries per route with error handling and retry capabilities.

---

### M-31. No rate limiting on user-triggered API actions
- **FILE:** Multiple pages
- **SEVERITY:** MEDIUM
- **CATEGORY:** Security / UX
- **DESCRIPTION:** Buttons for actions like "Reprocess", "Re-score", "Full Repair", "Sync Now" in Settings.tsx can be clicked repeatedly. While the `disabled` state prevents visual double-clicks, there's no debounce — fast clicks before state updates can trigger duplicate API calls.
- **FIX:** Add a debounce mechanism or use a ref to track in-flight requests.

---

### M-32. config.ts: API URL falls back to window.location.origin
- **FILE:** [src/config.ts](src/config.ts)
- **SEVERITY:** MEDIUM
- **CATEGORY:** Bug Risk
- **DESCRIPTION:** In production, if `VITE_API_URL` is not set, the config falls back to `window.location.origin`. If the frontend is served from a CDN or different domain than the API, all API calls will go to the wrong URL silently.
- **FIX:** Log a warning in development when the env variable is missing, and validate the API URL on startup.

---

## LOW Issues

### L-1. main.tsx: Non-null assertion on getElementById
- **FILE:** [src/main.tsx](src/main.tsx)
- **SEVERITY:** LOW
- **CATEGORY:** Type Safety
- **DESCRIPTION:** `document.getElementById('root')!` uses non-null assertion. If the element doesn't exist, this throws at runtime.
- **FIX:** Add a null check: `const root = document.getElementById('root'); if (!root) throw new Error('Root element not found')`

---

### L-2. LoginPage: "Forgot password?" button disabled with no explanation
- **FILE:** [src/pages/LoginPage.tsx](src/pages/LoginPage.tsx)
- **SEVERITY:** LOW
- **CATEGORY:** UX
- **DESCRIPTION:** A "Forgot password?" button is rendered but permanently disabled. Users see the button but can't use it, creating confusion.
- **FIX:** Either implement the feature, or remove the button entirely. If keeping it, add a tooltip: "Coming soon".

---

### L-3. JDBuilder: Blob URL not revoked after download
- **FILE:** [src/pages/JDBuilder.tsx](src/pages/JDBuilder.tsx#L72-L76)
- **SEVERITY:** LOW
- **CATEGORY:** Memory Leak
- **DESCRIPTION:** `handleDownload` creates a blob URL with `URL.createObjectURL(blob)` and revokes it with `URL.revokeObjectURL(url)` immediately after `a.click()`. However, `a.click()` is async in some browsers — the URL may be revoked before the download completes.
- **FIX:** Revoke after a short timeout: `setTimeout(() => URL.revokeObjectURL(url), 1000)`

---

### L-4. OAuthCallback: success redirect goes to `/` not `/dashboard`
- **FILE:** [src/pages/OAuthCallback.tsx](src/pages/OAuthCallback.tsx)
- **SEVERITY:** LOW
- **CATEGORY:** UX
- **DESCRIPTION:** After successful OAuth callback, the page navigates to `/`. Since `/` redirects to `/dashboard` anyway, this adds an extra redirect hop.
- **FIX:** Navigate directly to `/dashboard`.

---

### L-5. Unused imports in some files
- **FILE:** Various
- **SEVERITY:** LOW
- **CATEGORY:** Dead Code
- **DESCRIPTION:** A few files import icons or components that may go unused depending on conditional rendering paths. tsconfig has `noUnusedLocals: true` which should catch these at compile time, but some may be suppressed.
- **FIX:** Run `tsc --noEmit` to find and clean up any unused imports.

---

### L-6. Inconsistent button sizing across pages
- **FILE:** Multiple pages
- **SEVERITY:** LOW
- **CATEGORY:** UX / Consistency
- **DESCRIPTION:** Action buttons use a mix of `size="sm"`, default size, and `className="text-xs h-8"` custom sizing. The visual inconsistency is subtle but noticeable when comparing pages.
- **FIX:** Establish and document button size guidelines. Use the `size` prop from the `Button` component consistently.

---

### L-7. Hardcoded color values instead of design tokens
- **FILE:** Multiple pages
- **SEVERITY:** LOW
- **CATEGORY:** Consistency
- **DESCRIPTION:** Colors like `text-[#0077B5]` (LinkedIn blue), `bg-[#0077B5]/10` are hardcoded instead of using Tailwind config's extended colors.
- **FIX:** Add brand colors to `tailwind.config.js`:
```js
colors: {
  linkedin: '#0077B5',
}
```

---

### L-8. Magic numbers in multiple files
- **FILE:** Various
- **SEVERITY:** LOW
- **CATEGORY:** Maintainability
- **DESCRIPTION:** Magic numbers appear throughout:
  - `10 * 1024 * 1024` (10MB file limit) in UploadFiles.tsx and AIAssistant.tsx
  - `50` (max messages saved) in AIAssistant.tsx
  - `30000` (30s polling) in SetupWizard.tsx
  - `100` (notification cap) in notificationStore.ts
- **FIX:** Extract to named constants in `config.ts` or at the top of each file.

---

### L-9. Dashboard.tsx: inline file upload duplicates UploadFiles functionality
- **FILE:** [src/pages/Dashboard.tsx](src/pages/Dashboard.tsx)
- **SEVERITY:** LOW
- **CATEGORY:** DRY
- **DESCRIPTION:** Dashboard has its own file upload handler and modal that mostly duplicates the UploadFiles page functionality.
- **FIX:** Extract shared upload logic into a reusable hook or component.

---

### L-10. Console.error left in production code
- **FILE:** Multiple files
- **SEVERITY:** LOW
- **CATEGORY:** Code Quality
- **DESCRIPTION:** Many `console.error` calls are left in production code (e.g., SearchReports, SetupWizard, AIAssistant, CandidateDetail). While useful for debugging, these expose internal error details in the browser console.
- **FIX:** Use a centralized logging utility that can be configured to suppress logs in production, or conditionally log only in development.

---

### L-11. Tailwind JIT may generate large CSS
- **FILE:** [tailwind.config.js](tailwind.config.js)
- **SEVERITY:** LOW
- **CATEGORY:** Build / Performance
- **DESCRIPTION:** The `content` array includes `./index.html` and `./src/**/*.{js,ts,jsx,tsx}`. If any file contains dynamic class construction like `` `text-${color}-600` ``, Tailwind's JIT won't detect those classes. Several pages do construct class names dynamically (e.g., category colors, score colors).
- **FIX:** Use Tailwind's safelist for dynamically constructed classes, or always use complete class names in the code.

---

### L-12. Missing `rel="noopener noreferrer"` on some external links
- **FILE:** [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx#L207)
- **SEVERITY:** LOW
- **CATEGORY:** Security
- **DESCRIPTION:** Some `window.open(..., '_blank')` calls (e.g., Google Calendar link in Shortlist) don't include `rel="noopener"`. While modern browsers handle this automatically, it's a best practice.
- **FIX:** Use `window.open(url, '_blank', 'noopener,noreferrer')`.

---

### L-13. CandidateDetail: multiple sequential motion.div animations with hardcoded delays
- **FILE:** [src/pages/CandidateDetail.tsx](src/pages/CandidateDetail.tsx)
- **SEVERITY:** LOW
- **CATEGORY:** Performance / UX
- **DESCRIPTION:** Each section uses `<motion.div transition={{ delay: 0.05 * n }}>`. With 8+ sections, the last section doesn't appear until 400ms+ after navigation. This makes the page feel slower than it is.
- **FIX:** Remove staggered delays or use a single container animation that animates all children simultaneously.

---

### L-14. AIAssistant: `formatAIContent` renders HTML with dangerouslySetInnerHTML
- **FILE:** [src/pages/AIAssistant.tsx](src/pages/AIAssistant.tsx)
- **SEVERITY:** LOW
- **CATEGORY:** Security
- **DESCRIPTION:** The function converts markdown-style content to HTML and renders it. While DOMPurify is imported, the `formatAIContent` function doesn't appear to pass its output through DOMPurify before rendering.
- **FIX:** Ensure all HTML output from `formatAIContent` is sanitized: `DOMPurify.sanitize(formatAIContent(content))`.

---

### L-15. Missing `<meta name="description">` in index.html
- **FILE:** [index.html](index.html)
- **SEVERITY:** LOW
- **CATEGORY:** SEO
- **DESCRIPTION:** The HTML file only has a `<title>` tag. Missing meta description, viewport charset (should verify), and Open Graph tags.
- **FIX:** Add standard meta tags for better SEO and social sharing.

---

### L-16. Empty `catch` blocks suppress errors
- **FILE:** Multiple files
- **SEVERITY:** LOW
- **CATEGORY:** Error Handling
- **DESCRIPTION:** Several catch blocks are empty or only log errors:
  - `catch { /* skip */ }` in Shortlist.tsx handleBulkRemove
  - `catch { /* ignore */ }` in AIAssistant.tsx loadResultDetail
  - `catch { return [] }` in chat session restore
- **FIX:** At minimum, log errors in development. Consider showing user-facing error feedback.

---

### L-17. Search inputs lack debounce
- **FILE:** [src/pages/Candidates.tsx](src/pages/Candidates.tsx), [src/pages/Shortlist.tsx](src/pages/Shortlist.tsx), [src/pages/SearchReports.tsx](src/pages/SearchReports.tsx)
- **SEVERITY:** LOW
- **CATEGORY:** Performance
- **DESCRIPTION:** Search inputs filter candidates on every keystroke. While `useDebounce` hook exists in the codebase, the search inputs in Candidates, Shortlist, and SearchReports don't use it. With large datasets, this triggers expensive re-renders on every character typed.
- **FIX:** Apply `useDebounce` to search queries before filtering.

---

## Architecture Recommendations

1. **Component splitting**: AIAssistant (3,498 lines), Candidates (1,105), CandidateDetail (1,131), pdfGenerator (1,661) need to be broken into smaller modules
2. **Shared utilities**: Create `src/lib/scoreUtils.ts` and `src/lib/statusColors.ts` to eliminate 6+ duplicate utility functions
3. **State management**: Reconcile local shortlist state (candidateStore) with backend source of truth — pick one
4. **Error handling**: Add route-level error boundaries and consistent error states across all pages
5. **Accessibility**: Conduct a full WCAG 2.1 AA audit — many interactive elements lack keyboard support, ARIA labels, and proper focus management
6. **Type safety**: Eliminate all `any` types — define interfaces for every API response shape
7. **Testing**: No test files exist — add unit tests for hooks, stores, and utility functions at minimum

---

*Report generated by comprehensive code audit of all 50+ frontend source files.*
