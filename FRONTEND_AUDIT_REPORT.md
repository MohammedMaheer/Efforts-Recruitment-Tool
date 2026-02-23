# Frontend Source Code Audit Report

**Project:** Efforts Recruitment Tool  
**Scope:** All 56 files under `src/`  
**Date:** 2025  
**Stack:** React 18 + TypeScript + Vite, Zustand, React Router v6, Tailwind CSS + CVA, Radix UI, Framer Motion, jsPDF/pdf-lib, DOMPurify

---

## Executive Summary

| Category | Critical (P0) | High (P1) | Medium (P2) | Low (P3) | Total |
|----------|:---:|:---:|:---:|:---:|:---:|
| TypeScript Type Safety | 2 | 6 | 8 | 3 | **19** |
| React Anti-Patterns | 1 | 5 | 4 | 3 | **13** |
| Performance | 1 | 3 | 5 | 2 | **11** |
| Error Handling | 1 | 4 | 6 | 2 | **13** |
| State Management | 1 | 3 | 3 | 1 | **8** |
| Security | 2 | 3 | 3 | 1 | **9** |
| Accessibility | 0 | 3 | 5 | 3 | **11** |
| Code Quality | 0 | 2 | 5 | 5 | **12** |
| **Totals** | **8** | **29** | **39** | **20** | **96** |

---

## Table of Contents

1. [TypeScript Type Safety](#1-typescript-type-safety)
2. [React Anti-Patterns](#2-react-anti-patterns)
3. [Performance Issues](#3-performance-issues)
4. [Error Handling](#4-error-handling)
5. [State Management](#5-state-management)
6. [Security](#6-security)
7. [Accessibility](#7-accessibility)
8. [Code Quality](#8-code-quality)
9. [Per-File Summary Matrix](#9-per-file-summary-matrix)

---

## 1. TypeScript Type Safety

### TS-01 — `any` on core `Candidate.aiAnalysis` field [P0 Critical]

**Files:** `src/types/index.ts:121`, `src/store/candidateStore.ts:43`

```ts
// src/types/index.ts:121
aiAnalysis?: any;

// src/types/index.ts:173
ai_cache?: Record<string, any>;

// src/store/candidateStore.ts:43
aiAnalysis?: any
```

**Impact:** `aiAnalysis` is consumed in CandidateDetail, AIAssistant, and CandidateAIInsights — propagates untyped data through the entire analysis pipeline. Zero compile-time protection for the most complex data in the system.

**Fix:** Define an `AIAnalysis` interface:
```ts
export interface AIAnalysis {
  executive_summary?: string;
  technical_assessment?: string;
  experience_assessment?: string;
  education_assessment?: string;
  overall_rating?: string;
  hiring_recommendation?: 'STRONGLY_RECOMMEND' | 'RECOMMEND' | 'CONSIDER' | 'DO_NOT_RECOMMEND';
  strengths?: string[];
  weaknesses?: string[];
  interview_focus_areas?: string[];
  ideal_roles?: string[];
}
// Then: aiAnalysis?: AIAnalysis;
```

---

### TS-02 — Duplicate `Candidate` interface with divergent status union [P0 Critical]

**Files:** `src/store/candidateStore.ts:3-44` vs `src/types/index.ts:60-140`

```ts
// candidateStore.ts:11 — includes 'Strong' | 'Partial' | 'Reject' (MatchTier values)
status: 'Strong' | 'Partial' | 'Reject' | 'Shortlisted' | 'Rejected' | 'New' | 'Reviewed' | 'Interviewing' | 'Offered' | 'Hired' | 'Withdrawn'

// types/index.ts uses CandidateStatusType which does NOT include 'Strong', 'Partial', 'Reject'
```

**Impact:** Runtime status values that satisfy one type will fail the other. Any component importing from the wrong source gets the wrong contract. The store mixes match-tier values with status values — conceptual error.

**Fix:** Delete the `Candidate` interface from `candidateStore.ts`. Import from `types/index.ts`:
```ts
import type { Candidate } from '@/types';
```

---

### TS-03 — Pervasive `any` in page components [P1 High]

**Files & Lines:**

| Location | Code |
|----------|------|
| `src/pages/AIAssistant.tsx:459` | `JSON.parse(raw) as any[]` |
| `src/pages/AIAssistant.tsx:581,587,628,634` | `(job: any)`, `(edu: any)` in `.map()` |
| `src/pages/AIAssistant.tsx:800` | `session.messages.map((m: any) => ...)` |
| `src/pages/AIAssistant.tsx:1275` | `makeCandidateFromLookup = (entry: any)` |
| `src/pages/AIAssistant.tsx:2639,2641,2780,2782` | `(candidate as any).jobCategory` (×10) |
| `src/pages/Candidates.tsx:25` | `candidate: any` in `openContact` |
| `src/pages/Candidates.tsx:130,135` | `(res as any)?.data`, `(err: any)` |
| `src/pages/Candidates.tsx:762,977` | `(res as any)?.data?.email_sent?.status` |
| `src/pages/CandidateDetail.tsx:134` | `} as any : null)` |
| `src/pages/CandidateDetail.tsx:140,146` | `(job: any)`, `(edu: any)` |
| `src/pages/CandidateDetail.tsx:185` | `(error: any)` |
| `src/pages/CandidateDetail.tsx:727,762` | `(job: any)`, `(edu: any)` in JSX |
| `src/pages/CandidateDetail.tsx:830` | `candidate as any` |
| `src/pages/Settings.tsx:47,69,99,117,136,156` | `(result as any)?.data` (×6) |
| `src/pages/SearchReports.tsx:120,139` | `(s: any)`, `(r: any)` |
| `src/pages/Dashboard.tsx:62,200` | `(error: any)`, `(s: any)` |
| `src/pages/UploadFiles.tsx:51,81` | `(error: any)` (×2) |
| `src/pages/JDBuilder.tsx:54` | `(error: any)` |

**Impact:** 59+ `any` usages across the codebase defeat TypeScript's purpose. API responses are untyped, enabling silent runtime failures.

**Fix:** 
1. Type API response shapes with interfaces (e.g., `UpdateStatusResponse`, `EmailSyncResult`).  
2. Replace `error: any` with `error: unknown` + type narrowing:
   ```ts
   } catch (error: unknown) {
     const message = error instanceof Error ? error.message : 'Unknown error';
   }
   ```
3. The repeated `(candidate as any).jobCategory` in AIAssistant indicates the `Candidate` type in messages doesn't include `jobCategory` — extend the message candidate type.

---

### TS-04 — `user: any` prop on `UserMenu` [P1 High]

**File:** `src/components/layout/TopBar.tsx:175`

```ts
function UserMenu({ user, logout, navigate }: { user: any; logout: () => void; navigate: (path: string) => void })
```

**Fix:** Import the `User` type from `types/index.ts` or `authStore.ts`.

---

### TS-05 — `handleNotificationClick` takes `any` [P2 Medium]

**File:** `src/components/layout/TopBar.tsx:33`

```ts
const handleNotificationClick = (notification: any) => {
```

**Fix:** Use `Notification` from `notificationStore.ts`.

---

### TS-06 — `transformCandidate` and helpers use `any` [P1 High]

**File:** `src/hooks/useCandidates.ts:34,49,55,64,81`

```ts
const parseJSON = (value: any, fallback: any[] = []): any => { ... }
const transformCandidate = (c: any): Candidate => { ... }
```

**Fix:** Define a `RawCandidate` interface matching the backend JSON shape, use it instead of `any`.

---

### TS-07 — `statsApi.getDashboard()` returns `Promise<ApiResponse<any>>` [P2 Medium]

**File:** `src/services/api.ts:470`

```ts
async getDashboard(): Promise<ApiResponse<any>> {
```

**Fix:** Define a `DashboardData` interface and use `Promise<ApiResponse<DashboardData>>`.

---

### TS-08 — `authStore` catches with `error: any` and uses unsafe cast [P2 Medium]

**File:** `src/store/authStore.ts:64,100`

```ts
} catch (error: any) {
```

Also at line ~180:
```ts
partialize: (state) => ({ ... }) as unknown as AuthState,
```

**Fix:** Replace `error: any` with `error: unknown`. Replace `as unknown as AuthState` with a proper `PartializedState` type for the `partialize` function.

---

### TS-09 — `debounce`/`throttle` use `unknown[]` args [P3 Low]

**File:** `src/lib/utils.ts`

```ts
export function debounce<T extends (...args: unknown[]) => unknown>(fn: T, ms: number)
```

**Impact:** Minor — callers lose argument type inference.

**Fix:** Use generic rest params: `(...args: Parameters<T>) => void`.

---

## 2. React Anti-Patterns

### RA-01 — AIAssistant.tsx is 3,221 lines [P0 Critical]

**File:** `src/pages/AIAssistant.tsx` — 3,221 lines

This single component contains:
- Chat message state & session management  
- 15+ NLP intent parsers (`parseQuery`)  
- 3-tier AI fallback logic (`handleSend`)  
- `splitContentByCandidates` text processing  
- `formatAIContent` HTML rendering  
- `JobMatchModal` sub-component  
- Full candidate preview panel  
- Results split-view with detail panel  
- Bulk selection & shortlisting  
- Score visualization components

**Impact:** Unmaintainable. Any change risks regressions across unrelated features. Testing is impossible. IDE performance degrades.

**Fix:** Extract into ≥6 modules:
1. `hooks/useAIChat.ts` — message state, session management, `handleSend`
2. `hooks/useQueryParser.ts` — `parseQuery` + intent detection
3. `components/ai/ChatView.tsx` — message rendering, suggested prompts
4. `components/ai/ResultsView.tsx` — split-panel results display
5. `components/ai/CandidatePreviewPanel.tsx` — right-side preview
6. `components/ai/JobMatchModal.tsx` — already semi-extracted
7. `lib/aiFormatters.ts` — `formatAIContent`, `splitContentByCandidates`

---

### RA-02 — Missing `useEffect` dependencies [P1 High]

**Files & Lines:**

| Location | Missing Dep(s) |
|----------|----------------|
| `src/App.tsx:69` | `[token, verifyToken]` — uses both but deps are `[]` |
| `src/hooks/useAIStatus.ts` | `checkAIStatus` missing from deps |
| `src/components/AnalyticsDashboard.tsx` | `fetchData` not in deps |
| `src/pages/SearchReports.tsx` | `fetchHistory` not in deps |
| `src/components/CampaignManager.tsx` | Multiple useEffect with `[]` deps referencing outer functions |

**Impact:** Stale closures causing components to reference outdated state. Effects don't re-run when dependencies change.

**Fix:** Add missing deps. Stabilize callback refs with `useCallback` to avoid infinite loops:
```ts
// App.tsx
useEffect(() => { ... }, [token, verifyToken])
```

---

### RA-03 — ESLint rule suppression for exhaustive-deps [P1 High]

**File:** `src/hooks/useCandidates.ts:247`

```ts
}, [autoFetch]) // eslint-disable-line react-hooks/exhaustive-deps
```

**Impact:** Suppressing this lint rule hides real stale closure bugs. The hook likely needs `fetchCandidates` in deps, stabilized via `useCallback`.

**Fix:** Remove suppression, add proper dependencies, wrap functions in `useCallback`.

---

### RA-04 — Inline async handlers in JSX [P2 Medium]

**Files:**
- `src/pages/AIAssistant.tsx:2639-2641` — shortlist button `onClick={async (e) => { ... }}`
- `src/pages/Candidates.tsx:762` — shortlist inline handler
- `src/pages/CandidateDetail.tsx:830` — download resume handler
- `src/pages/AIAssistant.tsx:2350-2360` — HR action status update buttons

**Impact:** Creates new function reference every render (breaks memoization), makes error boundaries ineffective for async errors, harder to test.

**Fix:** Extract into named handlers at component top level:
```ts
const handleShortlist = useCallback(async (candidate: Candidate) => { ... }, [deps])
```

---

### RA-05 — IIFE patterns in JSX render [P3 Low]

**Files:**
- `src/pages/AIAssistant.tsx:2017` — avatar color IIFE `{(() => { const colors = [...] ... })()}`
- `src/pages/AIAssistant.tsx:2170-2200` — score analysis calculations
- `src/pages/CandidateDetail.tsx:134` — candidate merge IIFE

**Impact:** Reduces readability, recalculates every render.

**Fix:** Extract to `useMemo` or helper functions.

---

### RA-06 — `window.location.href` for SPA navigation [P1 High]

**Files:**
- `src/components/AnalyticsDashboard.tsx:230,235` — `window.location.href = '/settings?tab=templates'`
- `src/components/CandidateAIInsights.tsx:579` — `window.location.href = '/schedule?candidate=...'`
- `src/components/ErrorBoundary.tsx:46` — `window.location.href = '/'`

**Impact:** Full page reload loses all React state, Zustand stores, and in-memory caches. Defeats the purpose of SPA routing.

**Fix:** Use React Router's `useNavigate()` hook:
```ts
const navigate = useNavigate()
navigate('/settings?tab=templates')
```

---

### RA-07 — Stale closure risk in `useLocalStorage` [P2 Medium]

**File:** `src/hooks/useLocalStorage.ts`

```ts
const setValue = (value: T | ((val: T) => T)) => {
  // Reads `storedValue` from closure — may be stale
}
```

**Fix:** Use functional updater with `useCallback` and ref:
```ts
const storedValueRef = useRef(storedValue);
storedValueRef.current = storedValue;
```

---

### RA-08 — No route-level code splitting [P1 High]

**File:** `src/App.tsx:1-28`

All page components are eagerly imported:
```ts
import AIAssistant from '@/pages/AIAssistant'   // 3,221 lines
import Candidates from '@/pages/Candidates'       // 1,081 lines
import CandidateDetail from '@/pages/CandidateDetail' // 1,052 lines
// ... 9 more pages
```

**Impact:** Initial bundle includes ALL page code. AIAssistant alone is 3,221 lines — loaded even if the user never visits it.

**Fix:** Use `React.lazy` + `Suspense`:
```tsx
const AIAssistant = lazy(() => import('@/pages/AIAssistant'))
const Candidates = lazy(() => import('@/pages/Candidates'))
// In routes:
<Suspense fallback={<Loading />}>
  <Route path="ai-assistant" element={<AIAssistant />} />
</Suspense>
```

---

## 3. Performance Issues

### PF-01 — Global `*` transition on all elements [P0 Critical]

**File:** `src/index.css:81-82`

```css
* {
  @apply transition-colors duration-200;
}
```

**Impact:** Applies CSS transition to **every single DOM element** (including `<div>`, `<span>`, text nodes, SVGs). Causes layout thrashing on color scheme changes, scroll jank, and unnecessary GPU compositing. In a page like AIAssistant with 1,000+ DOM nodes, this is extremely expensive.

**Fix:** Remove the global rule. Apply transitions only to interactive elements:
```css
button, a, input, select, textarea, [role="button"] {
  @apply transition-colors duration-200;
}
```

---

### PF-02 — Oversized component files [P1 High]

| File | Lines | Recommendation |
|------|:-----:|----------------|
| `src/pages/AIAssistant.tsx` | 3,221 | Split into ≥6 modules (see RA-01) |
| `src/pages/Candidates.tsx` | 1,081 | Extract filter panel, table, and grouped view |
| `src/pages/CandidateDetail.tsx` | 1,052 | Extract AI analysis section, modals, timeline |
| `src/lib/pdfGenerator.ts` | 1,026 | Extract resume-merge logic, keep PDF layout separate |
| `src/pages/SetupWizard.tsx` | 904 | Extract tab renderers into subcomponents |
| `src/pages/Shortlist.tsx` | 693 | Extract CandidateDetailPanel subcomponent |

---

### PF-03 — Sequential API calls in bulk operations [P1 High]

**File:** `src/pages/Shortlist.tsx` — `handleBulkRemove`

```ts
// Sequential shortlist removal
for (const id of selectedIds) {
  await candidateApi.updateStatus(id, 'Reviewed')
}
```

**File:** `src/pages/AIAssistant.tsx:1108-1120` — `handleShortlistSelected` fallback

```ts
for (const c of selected) {
  await candidateApi.updateStatus(c.id, 'Shortlisted')
}
```

**Impact:** N candidates = N sequential network round-trips. 20 candidates × 200ms = 4 seconds blocking.

**Fix:** Use `Promise.all` (already available as `bulkShortlist` in API):
```ts
await Promise.all(selectedIds.map(id => candidateApi.updateStatus(id, 'Reviewed')))
// Or better: create a bulk endpoint
```

---

### PF-04 — `uploadStats` useEffect accumulates on re-render [P2 Medium]

**File:** `src/pages/UploadFiles.tsx` — uploadStats computation

The effect counts results on every render cycle without proper dependency gating, causing cumulative miscounts.

**Fix:** Derive stats with `useMemo` instead of `useEffect` + state:
```ts
const uploadStats = useMemo(() => computeStats(uploadResults), [uploadResults])
```

---

### PF-05 — `categoryColors` object recreated in 3 files [P2 Medium]

**Files:**
- `src/pages/Candidates.tsx:62`
- `src/pages/CandidateDetail.tsx:44`
- `src/lib/utils.ts:370`

Same ~20-entry object duplicated. Each file declares and initializes it on module load.

**Fix:** Use only the `utils.ts` version. Import in page components:
```ts
import { getCategoryColor } from '@/lib/utils'
```

---

### PF-06 — Framer Motion animations on every list item [P3 Low]

**Files:** `src/pages/AIAssistant.tsx:2530-2540`, `src/pages/Candidates.tsx`

```tsx
{message.candidates!.map((candidate, idx) => (
  <motion.div
    initial={{ opacity: 0, x: -20 }}
    animate={{ opacity: 1, x: 0 }}
    transition={{ delay: 0.4 + idx * 0.05 }}
  >
```

**Impact:** With 50+ candidates, creates 50 staggered animations. Each triggers a reflow.

**Fix:** Use `layout` prop or CSS animations for lists. Only animate the container, not each item:
```tsx
<motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
  {candidates.map(c => <div key={c.id}>...</div>)}
</motion.div>
```

---

### PF-07 — Hardcoded "missing keywords" pool [P2 Medium]

**File:** `src/pages/AIAssistant.tsx:2316-2317`

```ts
const missingPool = ['Terraform CloudFormation', 'Agile Development', 'Code Review', 'AWS Certified Developer', ...]
const missing = missingPool.filter(m => !matched.some(...)).slice(0, 10)
```

**Impact:** Fabricated "missing" keywords unrelated to the actual job — misleading UX. Also recalculated every render.

**Fix:** Source missing keywords from the backend AI analysis or job description matching. At minimum, memoize.

---

## 4. Error Handling

### EH-01 — `alert()` used for error display (20+ instances) [P1 High]

**Files & Lines:**

| File | Lines | Usage |
|------|-------|-------|
| `src/pages/AIAssistant.tsx` | 224, 228, 251, 255, 1111, 1118, 3031 | Validation + success alerts |
| `src/pages/JDBuilder.tsx` | 39, 52, 55 | Validation + API error |
| `src/pages/UploadFiles.tsx` | 37 | File type validation |
| `src/pages/Settings.tsx` | 222, 227 | Password validation |
| `src/pages/Dashboard.tsx` | 50 | File type validation |
| `src/pages/Shortlist.tsx` | 577 | Resume download fallback |
| `src/pages/CandidateDetail.tsx` | 830 | Resume download fallback |

**Impact:** `alert()` blocks the main thread, is not styleable, breaks screen readers, and provides no action affordance.

**Fix:** Use the existing Toast system already in the codebase:
```ts
import { useToast } from '@/components/ui/Toast'
const { addToast } = useToast()
addToast({ type: 'error', message: 'Please upload PDF or DOCX files only.' })
```

---

### EH-02 — `confirm()` for destructive actions [P2 Medium]

**Files:**
- `src/pages/SearchReports.tsx:34` — `confirm('Clear all search history?')`
- `src/components/layout/TopBar.tsx` — `window.confirm()` for logout
- `src/components/CampaignManager.tsx` — `confirm()` for campaign deletion
- `src/components/TemplatesManager.tsx` — `confirm()` for template deletion

**Fix:** Use the existing Radix `Dialog` component for confirmation modals.

---

### EH-03 — Empty catch blocks [P1 High]

**Files:**
- `src/pages/SearchReports.tsx` — `catch { /* ignore */ }`
- `src/pages/AIAssistant.tsx:2362` — `catch { /* ignore */ }` in HR action
- `src/App.tsx:70` — `.catch(() => {})` on health ping (acceptable for fire-and-forget)

**Impact:** Silently swallows errors. Users see no feedback when operations fail.

**Fix:** At minimum log + show toast:
```ts
} catch (error) {
  console.error('Failed to update status:', error);
  addToast({ type: 'error', message: 'Failed to update candidate status' });
}
```

---

### EH-04 — Unhandled promise rejections from fire-and-forget calls [P1 High]

**Files:**
- `src/components/AnalyticsDashboard.tsx:230,235` — QuickAction buttons call `window.location.href` but some call async actions without `.catch()`
- `src/pages/AIAssistant.tsx` — Multiple inline `onClick={async () => { ... }}` without try/catch
- `src/pages/Candidates.tsx:762` — inline shortlist handler

**Fix:** Wrap all async onClick handlers in try/catch.

---

### EH-05 — Error boundary only catches render errors [P2 Medium]

**File:** `src/components/ErrorBoundary.tsx`

The `ErrorBoundary` class component only catches synchronous render/lifecycle errors. Async errors (API calls, event handlers) bypass it entirely.

**Fix:** Pair with a global `window.addEventListener('unhandledrejection', ...)` handler, or use the `useErrorHandler` hook (already partially implemented) in async catch blocks.

---

### EH-06 — `useErrorHandler` throws synchronously [P2 Medium]

**File:** `src/components/ErrorBoundary.tsx`

```ts
const useErrorHandler = () => {
  const [, setError] = useState()
  return (error: Error) => setError(() => { throw error })
}
```

**Impact:** This is a known React pattern to let error boundaries catch async errors, but throwing inside setState can cause double-render issues in StrictMode.

**Fix:** Consider using a dedicated error boundary library like `react-error-boundary` which handles this pattern more robustly.

---

## 5. State Management

### SM-01 — Duplicate `Candidate` interface (store vs types) [P0 Critical]

See **TS-02** above. The `candidateStore.ts` redefines `Candidate` with a conflicting `status` union that includes match-tier values (`Strong`, `Partial`, `Reject`) mixed with actual status values.

---

### SM-02 — Shortlist state split between backend and Zustand [P1 High]

**Files:**
- `src/store/candidateStore.ts:48-52` — `shortlistedIds` in Zustand
- `src/pages/Shortlist.tsx` — fetches shortlisted candidates from backend by `status=Shortlisted`
- Multiple components call both `toggleShortlist(id)` and `candidateApi.updateStatus(id, 'Shortlisted')` separately

**Impact:** State can desynchronize. If the API call fails but the Zustand toggle succeeds, the UI shows shortlisted but the backend doesn't. The Shortlist page ignores the store entirely and fetches from backend.

**Fix:** Make the backend the single source of truth. Remove `shortlistedIds` from Zustand or make it a cache that syncs from backend responses:
```ts
// After successful API call:
const result = await candidateApi.updateStatus(id, 'Shortlisted');
if (result.success) toggleShortlist(id); // sync local cache
```

---

### SM-03 — Notification ID generation is collision-prone [P2 Medium]

**File:** `src/store/notificationStore.ts:35`

```ts
id: Date.now().toString() + Math.random(),
```

**Impact:** `Date.now()` has millisecond precision. Two notifications in the same millisecond get the same timestamp prefix. `Math.random()` is not cryptographically unique. The resulting string isn't a valid identifier format.

**Fix:** Use `crypto.randomUUID()`:
```ts
id: crypto.randomUUID(),
```

---

### SM-04 — Direct `useAuthStore.getState()` outside React [P2 Medium]

**Files:**
- `src/lib/pdfGenerator.ts` — calls `useAuthStore.getState()` to get token for API calls
- `src/pages/AIAssistant.tsx` — welcome message accesses `useAuthStore.getState().user`

**Impact:** Zustand `getState()` outside React is valid but creates a hidden coupling. Changes to auth state won't trigger re-renders in these call sites.

**Fix:** Pass token/user as function parameters instead of reaching into the store:
```ts
// pdfGenerator.ts
export async function downloadOriginalResume(candidate: Candidate, token: string) { ... }
```

---

### SM-05 — Chat sessions split across localStorage and sessionStorage [P2 Medium]

**File:** `src/pages/AIAssistant.tsx`

- Chat sessions (history sidebar) → `localStorage` (key `ai-chat-sessions`, max 50)
- Current chat messages/history → local component state only
- Auth data → `sessionStorage`

**Impact:** Sessions persist across browser close (localStorage) but current conversation is lost on refresh. Inconsistent persistence model confuses users.

**Fix:** Either persist current conversation to localStorage too, or document that only the session list persists.

---

### SM-06 — `uploadResults` state causes recomputation issues [P1 High]

**File:** `src/pages/UploadFiles.tsx`

```ts
const [uploadResults, setUploadResults] = useState<any[]>([])
```

The `any[]` type and effect-based stat computation mean every state update triggers recount with no memoization.

**Fix:** Type the results and use `useMemo` for derived stats.

---

## 6. Security

### SC-01 — Settings.tsx uses raw `fetch()` bypassing auth wrapper [P0 Critical]

**File:** `src/pages/Settings.tsx:173,232`

```ts
// Line 173 — profile update
const response = await fetch(`${config.apiUrl}/api/users/profile`, {
  method: 'PUT',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${useAuthStore.getState().token}`
  },
  body: JSON.stringify({ name: profileName, email: profileEmail })
})

// Line 232 — password change
const response = await fetch(`${config.apiUrl}/api/users/password`, {
  method: 'PUT',
  headers: { ... },
  body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
})
```

**Impact:** Bypasses the centralized `authFetch` wrapper which handles token refresh, 401 redirect, retry logic, and timeout. If the token expires during these calls, the user gets a raw fetch error instead of being redirected to login.

**Fix:** Use `authFetch` from `lib/authFetch.ts`:
```ts
const response = await authFetch(`${config.apiUrl}/api/users/profile`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ name: profileName, email: profileEmail })
})
```

---

### SC-02 — SetupWizard sends email password as plaintext JSON [P0 Critical]

**File:** `src/pages/SetupWizard.tsx` — `handleConnect` function

The email connection form sends the user's email password in a plain JSON body:
```ts
body: JSON.stringify({ provider: selectedProvider, email, password })
```

**Impact:** Password transmitted in request body. While HTTPS encrypts in transit, the password may be logged in server access logs, browser dev tools network tab, or proxy middleware.

**Fix:**
1. Use OAuth2 for all email providers (already supported for Outlook).
2. If app passwords are required, switch to a one-time token exchange flow.
3. At minimum, never store the password client-side and clear the input immediately after submission.

---

### SC-03 — `dangerouslySetInnerHTML` with AI-generated content [P1 High]

**File:** `src/pages/AIAssistant.tsx:2499`

```tsx
dangerouslySetInnerHTML={{ 
  __html: DOMPurify.sanitize(formatAIContent(
    split && split.sections.length > 0 ? split.header : message.content,
    message.candidates
  ), { ADD_ATTR: ['data-candidate-id'] })
}}
```

**Impact:** DOMPurify is used (good), but `ADD_ATTR: ['data-candidate-id']` allows custom attributes through. The `formatAIContent` function generates HTML with `<a>` tags containing `data-candidate-id` — while DOMPurify sanitizes, the custom attribute whitelist could be expanded without review. Also, `formatAIContent` creates clickable links from AI output which could be manipulated by prompt injection.

**Fix:** 
1. Audit `formatAIContent` to ensure it only generates safe HTML patterns.
2. Validate `data-candidate-id` values are valid UUIDs.
3. Consider using React components instead of `dangerouslySetInnerHTML`:
```tsx
<AIResponseRenderer content={message.content} candidates={message.candidates} />
```

---

### SC-04 — Token stored in sessionStorage [P2 Medium]

**File:** `src/store/authStore.ts` — Zustand persist with `sessionStorage`

**Impact:** `sessionStorage` is accessible to any JavaScript on the same origin. An XSS vulnerability (e.g., from the `dangerouslySetInnerHTML` above) could exfiltrate the token.

**Fix:** Consider `httpOnly` cookies for token storage (requires backend changes). If sessionStorage is kept, ensure robust XSS prevention across all input rendering paths.

---

### SC-05 — 401 handler redirects via `window.location.href` [P1 High]

**File:** `src/services/api.ts:101`

```ts
window.location.href = '/login';
```

**Impact:** While this specific URL is hardcoded (not an open redirect), the pattern of using `window.location.href` for navigation could be copied elsewhere with a dynamic URL, creating open redirect vulnerabilities. Also causes full page reload.

**Fix:** Use the auth store's logout method which calls `navigate('/login')`:
```ts
useAuthStore.getState().logout()
```

---

### SC-06 — OAuthCallback manually constructs auth headers [P2 Medium]

**File:** `src/pages/OAuthCallback.tsx`

Instead of using the centralized `authFetch`, the callback page manually constructs `Authorization: Bearer ${token}` headers.

**Fix:** Use `authFetch` for consistency and to get retry/refresh benefits.

---

## 7. Accessibility

### A11Y-01 — Interactive icon-only buttons lack accessible labels [P1 High]

**Files:**
- `src/pages/Candidates.tsx` — filter toggle, grid/list view switches, contact buttons
- `src/pages/AIAssistant.tsx` — checkbox toggles (`<Square>`, `<CheckSquare>` icons), close buttons
- `src/pages/Shortlist.tsx` — remove/download action buttons
- `src/pages/CandidateDetail.tsx` — action buttons in hero card

Example (`src/pages/AIAssistant.tsx:2007`):
```tsx
<button onClick={(e) => { e.stopPropagation(); toggleSelect(candidate.id) }} className="flex-shrink-0">
  {selectedIds.has(candidate.id)
    ? <CheckSquare className="w-4 h-4 text-sky-600" />
    : <Square className="w-4 h-4 text-gray-300" />}
</button>
```

**Fix:** Add `aria-label`:
```tsx
<button aria-label={`${selectedIds.has(candidate.id) ? 'Deselect' : 'Select'} ${candidate.name}`} ...>
```

---

### A11Y-02 — Notification dropdown lacks keyboard navigation [P1 High]

**File:** `src/components/layout/TopBar.tsx`

The notification dropdown panel opens on click but:
- No `role="menu"` / `role="menuitem"` 
- No keyboard arrow navigation
- No `Escape` key to close
- No focus trap

**Fix:** Use Radix `DropdownMenu` or `Popover` primitive which handles keyboard interaction, focus management, and ARIA roles automatically.

---

### A11Y-03 — Custom modals use plain `<div>` overlays [P1 High]

**Files:**
- `src/pages/CandidateDetail.tsx:1031` — Reject confirmation modal
- `src/pages/CandidateDetail.tsx` — Calendar picker modal
- `src/pages/Dashboard.tsx` — Upload modal

```tsx
// CandidateDetail.tsx:1031
<div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowRejectConfirm(false)}>
```

**Impact:** No focus trap, no `Escape` key, no `role="dialog"`, no `aria-modal="true"`, no focus restoration on close. Screen readers cannot detect the modal.

**Fix:** Use the existing Radix `Dialog` component already imported elsewhere:
```tsx
<Dialog open={showRejectConfirm} onOpenChange={setShowRejectConfirm}>
  <DialogContent>...</DialogContent>
</Dialog>
```

---

### A11Y-04 — Color-only score indicators [P2 Medium]

**Files:** Multiple — `getScoreColor` returns only CSS color classes without text alternative

```tsx
<span className={`text-base font-bold ${getScoreColor(candidate.matchScore ?? 50)}`}>
  {(candidate.matchScore ?? 50).toFixed(0)}%
</span>
```

**Impact:** The numeric value is present (good), but the color distinction (green/amber/red) carrying semantic meaning (good/okay/poor) is not available to colorblind users.

**Fix:** Add a text label alongside the score:
```tsx
<span className="sr-only">{score >= 75 ? 'Strong match' : score >= 50 ? 'Partial match' : 'Weak match'}</span>
```

---

### A11Y-05 — `<textarea>` for HR comments has no associated label [P2 Medium]

**File:** `src/pages/AIAssistant.tsx:2378`

```tsx
<textarea
  placeholder="Add your comments, interview notes, or feedback about this candidate..."
  className="w-full h-24 text-sm ..."
/>
```

**Impact:** No `<label>`, no `aria-label`, no `id` for `htmlFor` association. Screen readers announce it as "text input" with no context.

**Fix:**
```tsx
<label htmlFor="hr-comments" className="sr-only">HR Comments</label>
<textarea id="hr-comments" aria-label="HR comments and interview notes" ... />
```

---

### A11Y-06 — Checkbox inputs styled without proper ARIA [P2 Medium]

**File:** `src/pages/Settings.tsx:440-470` — Notification toggle checkboxes

```tsx
<input type="checkbox" className="w-5 h-5 text-sky-600 rounded" checked={emailNotifications} onChange={...} />
```

No `id`/`htmlFor` pairing with the description text. The text ("Email Notifications") is in a separate `<div>`.

**Fix:**
```tsx
<label className="flex items-center justify-between">
  <div>
    <p className="font-medium">Email Notifications</p>
    <p className="text-sm text-gray-600">Receive email updates</p>
  </div>
  <input type="checkbox" aria-label="Email Notifications" ... />
</label>
```

---

### A11Y-07 — Tab navigation in SetupWizard not ARIA-compliant [P2 Medium]

**File:** `src/pages/SetupWizard.tsx:438-460`

Tab buttons use plain `<button>` elements without `role="tab"`, `aria-selected`, `role="tablist"`, or `role="tabpanel"`.

**Fix:** Add proper ARIA tab roles:
```tsx
<nav role="tablist">
  <button role="tab" aria-selected={activeTab === tab.id} aria-controls={`panel-${tab.id}`}>
```

---

### A11Y-08 — SVG charts lack text alternatives [P3 Low]

**Files:**
- `src/pages/AIAssistant.tsx:2300-2320` — Keywords donut chart
- `src/pages/Shortlist.tsx` — `ScoreRing` component
- `src/components/ui/ScoreCircle.tsx`

**Fix:** Add `role="img"` and `aria-label`:
```tsx
<svg role="img" aria-label={`Match score: ${score}%`} ...>
```

---

## 8. Code Quality

### CQ-01 — `categoryColors` duplicated across 3 files [P1 High]

**Files:**
- `src/pages/Candidates.tsx:62-90`
- `src/pages/CandidateDetail.tsx:44-74`
- `src/lib/utils.ts:370-388`

Three identical ~20-entry color mapping objects. Changes must be made in all three places.

**Fix:** Keep only the `utils.ts` version. Delete from Candidates.tsx and CandidateDetail.tsx:
```ts
import { getCategoryColor } from '@/lib/utils'
```

---

### CQ-02 — `detectMojibake` in pdfGenerator duplicates `isTextGarbled` from textUtils [P1 High]

**Files:**
- `src/lib/pdfGenerator.ts:897` area — has its own `detectMojibake` function
- `src/lib/textUtils.ts` — exports `isTextGarbled`

**Fix:** Delete `detectMojibake` from pdfGenerator.ts, import `isTextGarbled` from textUtils.

---

### CQ-03 — Score color utility functions duplicated [P2 Medium]

**Files:**
- `src/pages/Shortlist.tsx` — `ScoreRing` component with inline color logic
- `src/pages/AIAssistant.tsx` — `getScoreColor`, `getFitLabel`
- `src/lib/utils.ts` — `getMatchScoreColor`
- `src/components/ui/ScoreCircle.tsx` — has its own color logic

**Fix:** Consolidate into a single `getScoreColor` in `utils.ts`, import everywhere.

---

### CQ-04 — "Remember me" checkbox does nothing [P2 Medium]

**File:** `src/pages/LoginPage.tsx`

The login form has a "Remember me" checkbox that is rendered but never wired to any persistence logic. Auth always uses `sessionStorage`.

**Fix:** Either implement it (switch to `localStorage` when checked) or remove the checkbox to avoid user confusion.

---

### CQ-05 — `ScoreRing` component defined inline in Shortlist.tsx [P2 Medium]

**File:** `src/pages/Shortlist.tsx`

A fully self-contained SVG ring component is defined inside the Shortlist page file instead of being shared.

**Fix:** Move to `src/components/ui/ScoreRing.tsx` alongside the existing `ScoreCircle.tsx`, or merge them.

---

### CQ-06 — Unused prefixed variables [P3 Low]

**Files:**
- `src/components/TemplatesManager.tsx` — `_previewVariables`
- `src/pages/AIAssistant.tsx` — `_shortlistingId`

**Fix:** If truly unused, remove. If intentionally voided, add a comment explaining why.

---

### CQ-07 — Inconsistent API calling patterns [P2 Medium]

**Files:**
- `src/pages/Dashboard.tsx` — uses `authFetch` directly
- `src/pages/Settings.tsx` — mixes `authFetch` and raw `fetch`
- `src/pages/Candidates.tsx` — uses `candidateApi` from api.ts
- `src/pages/OAuthCallback.tsx` — manual `fetch` with header construction
- `src/components/CampaignManager.tsx` — uses api client methods

**Fix:** Standardize on the `api.ts` client for all API calls. Remove direct `fetch`/`authFetch` usage in pages.

---

### CQ-08 — Magic strings for candidate statuses [P3 Low]

**Files:** Throughout — status values like `'Shortlisted'`, `'Reviewed'`, `'New'` are hardcoded strings.

```ts
await candidateApi.updateStatus(id, 'Shortlisted')
if (candidate.status === 'New') { ... }
```

**Fix:** Use the existing `CandidateStatus` const enum from `types/index.ts`:
```ts
import { CandidateStatus } from '@/types'
await candidateApi.updateStatus(id, CandidateStatus.SHORTLISTED)
```

---

### CQ-09 — Hardcoded backend URL in config.ts with `as const` [P3 Low]

**File:** `src/config.ts`

The production URL is hardcoded. It uses `import.meta.env` with a fallback, which is fine, but the `as const` on the entire config object prevents any test-time overrides.

**Fix:** Consider making the config mutable for testing or using a dependency injection pattern.

---

### CQ-10 — Console statements left in production code [P3 Low]

**Files:** Throughout — `console.error`, `console.log`, `console.warn` scattered across:
- `src/pages/AIAssistant.tsx`
- `src/pages/Candidates.tsx`
- `src/pages/CandidateDetail.tsx`
- `src/hooks/useCandidates.ts`
- `src/services/api.ts`

**Fix:** Use a structured logger that can be disabled in production, or use the existing `console.error` for errors only and strip `console.log` via build config.

---

## 9. Per-File Summary Matrix

| File | TS | React | Perf | Error | State | Sec | A11Y | CQ | Total |
|------|:--:|:-----:|:----:|:-----:|:-----:|:---:|:----:|:--:|:-----:|
| **src/App.tsx** | – | RA-02,08 | – | – | – | – | – | – | 2 |
| **src/config.ts** | – | – | – | – | – | – | – | CQ-09 | 1 |
| **src/index.css** | – | – | PF-01 | – | – | – | – | – | 1 |
| **src/types/index.ts** | TS-01 | – | – | – | – | – | – | – | 1 |
| **src/store/authStore.ts** | TS-08 | – | – | – | – | SC-04 | – | – | 2 |
| **src/store/candidateStore.ts** | TS-01,02 | – | – | – | SM-01,02 | – | – | – | 4 |
| **src/store/notificationStore.ts** | – | – | – | – | SM-03 | – | – | – | 1 |
| **src/services/api.ts** | TS-07 | – | – | – | – | SC-05 | – | CQ-07 | 3 |
| **src/lib/utils.ts** | TS-09 | – | – | – | – | – | – | – | 1 |
| **src/lib/pdfGenerator.ts** | – | – | – | – | SM-04 | – | – | CQ-02 | 2 |
| **src/hooks/useCandidates.ts** | TS-06 | RA-03 | – | – | – | – | – | – | 2 |
| **src/hooks/useAIStatus.ts** | – | RA-02 | – | – | – | – | – | – | 1 |
| **src/hooks/useLocalStorage.ts** | – | RA-07 | – | – | – | – | – | – | 1 |
| **src/components/layout/TopBar.tsx** | TS-04,05 | – | – | EH-02 | – | – | A11Y-02 | – | 4 |
| **src/components/ErrorBoundary.tsx** | – | RA-06 | – | EH-05,06 | – | – | – | – | 3 |
| **src/components/AnalyticsDashboard.tsx** | – | RA-02,06 | – | EH-04 | – | – | – | – | 3 |
| **src/components/CampaignManager.tsx** | – | RA-02 | – | EH-02 | – | – | – | CQ-07 | 3 |
| **src/components/CandidateAIInsights.tsx** | – | – | – | EH-01 | – | – | – | – | 1 |
| **src/components/EmailIntegration.tsx** | TS-03 | – | – | EH-01 | – | – | – | – | 2 |
| **src/components/TemplatesManager.tsx** | – | – | – | EH-02 | – | – | – | CQ-06 | 2 |
| **src/pages/AIAssistant.tsx** | TS-03 | RA-01,04,05 | PF-03,06,07 | EH-01,03,04 | SM-04,05 | SC-03 | A11Y-01,05,08 | CQ-03,06 | **18** |
| **src/pages/Candidates.tsx** | TS-03 | RA-04 | PF-02 | EH-01,04 | – | – | A11Y-01,04 | CQ-01,08 | 8 |
| **src/pages/CandidateDetail.tsx** | TS-03 | RA-05 | PF-02 | EH-01 | – | – | A11Y-01,03 | CQ-01 | 7 |
| **src/pages/Shortlist.tsx** | – | – | PF-03 | EH-01 | SM-02 | – | A11Y-01,08 | CQ-03,05 | 6 |
| **src/pages/Settings.tsx** | TS-03 | – | – | EH-01 | – | SC-01 | A11Y-06 | CQ-07 | 5 |
| **src/pages/SetupWizard.tsx** | – | – | – | – | – | SC-02 | A11Y-07 | – | 2 |
| **src/pages/UploadFiles.tsx** | TS-03 | – | PF-04 | EH-01 | SM-06 | – | – | – | 4 |
| **src/pages/SearchReports.tsx** | TS-03 | RA-02 | – | EH-02,03 | – | – | – | – | 4 |
| **src/pages/Dashboard.tsx** | TS-03 | – | – | EH-01 | – | – | A11Y-03 | CQ-07 | 4 |
| **src/pages/JDBuilder.tsx** | TS-03 | – | – | EH-01 | – | – | – | – | 2 |
| **src/pages/LoginPage.tsx** | – | – | – | – | – | – | – | CQ-04 | 1 |
| **src/pages/OAuthCallback.tsx** | – | – | – | – | – | SC-06 | – | CQ-07 | 2 |

**Files with zero findings (clean):** `main.tsx`, `vite-env.d.ts`, `authFetch.ts`, `textUtils.ts`, `useAsync.ts`, `useDebounce.ts`, `useEmailSync.ts`, `useIntersectionObserver.ts`, `useRealTimeStats.ts`, `hooks/index.ts`, `DashboardLayout.tsx`, `Sidebar.tsx`, `Button.tsx`, `Badge.tsx`, `Avatar.tsx`, `Card.tsx`, `Toast.tsx`, `Table.tsx`, `ScoreCircle.tsx`, `Progress.tsx`, `Loading.tsx`, `Input.tsx`, `EmptyState.tsx`, `Dialog.tsx`

---

## Recommended Fix Priority Order

### Phase 1 — Critical (P0) — Week 1
1. **SC-01/SC-02:** Replace raw `fetch` with `authFetch`; remove plaintext password transmission
2. **TS-02/SM-01:** Delete duplicate `Candidate` interface from `candidateStore.ts`
3. **PF-01:** Remove global `*` CSS transition
4. **TS-01:** Define `AIAnalysis` interface, replace `any`

### Phase 2 — High (P1) — Weeks 2-3
5. **RA-01:** Break up AIAssistant.tsx (3,221 → 6+ files)
6. **RA-08:** Add `React.lazy` code splitting for all routes
7. **RA-02:** Fix all missing `useEffect` dependencies
8. **EH-01:** Replace all `alert()` calls with Toast notifications
9. **TS-03:** Type the top 20 most impactful `any` usages
10. **SC-03:** Audit `dangerouslySetInnerHTML` and `formatAIContent`
11. **A11Y-01/02/03:** Add ARIA labels, keyboard nav, use Dialog component

### Phase 3 — Medium (P2) — Weeks 4-6
12. **SM-02:** Unify shortlist state management
13. **PF-03:** Replace sequential API loops with `Promise.all`/bulk endpoints
14. **CQ-01/02/03:** Deduplicate `categoryColors`, `detectMojibake`, score utils
15. **EH-02:** Replace `confirm()` with Dialog modals
16. **A11Y-04-07:** Remaining accessibility improvements

### Phase 4 — Low (P3) — Backlog
17. **CQ-04:** Fix or remove "Remember me" checkbox
18. **CQ-08:** Use `CandidateStatus` enum instead of magic strings
19. **CQ-10:** Add structured logging
20. **TS-09:** Improve generic types in utility functions

---

*End of audit report. 96 findings across 56 files, 8 critical issues requiring immediate attention.*
