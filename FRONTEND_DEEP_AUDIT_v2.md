# Frontend Deep Audit Report v2

**Scope:** `src/` — React 18 + TypeScript + Vite SPA  
**Date:** 2025-01-24  
**Auditor:** Automated deep review (all files read)

> **Already-fixed issues excluded from this report:**  
> (1) Missing error boundaries on routes, (2) API token in localStorage, (3) No retry on 401,  
> (4) Bare `.catch()` in hooks, (5) Missing loading states on async actions, (6) No input sanitisation on search,  
> (7) Toast notifications not accessible, (8) Missing AbortController in data fetching, (9) No CSRF token on mutations.

---

## HIGH Severity

### H-01 — `AIAssistant.tsx` is a 3 437-line monolith

| Field | Value |
|---|---|
| **SEVERITY** | HIGH |
| **FILE** | `src/pages/AIAssistant.tsx` |
| **LINE** | 1–3 437 |
| **DESCRIPTION** | A single component owns chat session management, AI chat logic with 3-tier fallback, candidate preview, split-panel results, job matching, ML ranking, predictive analytics, duplicate detection, bulk shortlisting, HR notes, and full candidate detail rendering. This violates single-responsibility, makes the file nearly impossible to review or test in isolation, and increases the risk of regressions. |
| **EVIDENCE** | File is 3 437 lines with 60+ `useState` calls, 10+ `useEffect` hooks, and 20+ handler functions. |
| **FIX** | Extract into focused modules: `ChatSessionManager`, `CandidatePreviewPanel`, `ResultDetailPanel`, `JobMatchModal` (already partially extracted), `BulkShortlistBar`, `HRNotesEditor`, etc. Share state via a local context or Zustand slice. |

---

### H-02 — Pervasive `any` types defeat TypeScript safety (39+ instances)

| Field | Value |
|---|---|
| **SEVERITY** | HIGH |
| **FILE** | Multiple (see below) |
| **LINE** | Various |
| **DESCRIPTION** | At least 39 occurrences of `: any` across the codebase bypass compile-time type checking, allowing runtime errors that TypeScript is meant to prevent. This includes function parameters, catch clauses, API response payloads, and state variables. |
| **EVIDENCE** | |

| File | Line(s) | Example |
|---|---|---|
| `src/pages/AIAssistant.tsx` | 449, 544, 550, 595, 601, 774, 1262, 1271, 1403 | `messages: any[]`, `data: any`, `entry: any`, `(job: any)`, `(edu: any)` |
| `src/pages/CandidateDetail.tsx` | 97, 151, 157, 196, 743, 778 | `err: any`, `(job: any)`, `(edu: any)` |
| `src/pages/Candidates.tsx` | 102 | `err: any` |
| `src/pages/SearchReports.tsx` | 131, 149 | `topResults: any[]`, `(r: any)` |
| `src/pages/Shortlist.tsx` | 161 | `(v: any)` in CSV escape |
| `src/pages/UploadFiles.tsx` | 54, 84 | `error: any` |
| `src/pages/JDBuilder.tsx` | 55 | `error: any` |
| `src/pages/Dashboard.tsx` | 82 | `error: any` |
| `src/services/api.ts` | 883–886, 897 | `education?: any[]`, `work_experience?: any[]`, `(c: any)` |
| `src/hooks/useCandidates.ts` | 30, 45, 51, 60 | `parseJSON(value: any, …): any`, `transformCandidate(c: any)` |
| `src/store/authStore.ts` | 64, 100 | `error: any` |
| `src/components/layout/TopBar.tsx` | 33, 175 | `notification: any`, `user: any` |

| **FIX** | Replace `any` with proper types: define `WorkHistoryEntry`, `EducationEntry`, `ChatMessage`, `PlatformStats`, `SyncResult`, etc. For catch blocks, use `unknown` and narrow with `instanceof Error`. For API responses, use generics like `ApiResponse<DashboardData>`. |

---

### H-03 — `window.confirm()` / `window.prompt()` used for destructive actions (15 call sites)

| Field | Value |
|---|---|
| **SEVERITY** | HIGH |
| **FILE** | Multiple |
| **LINE** | See table |
| **DESCRIPTION** | Native `confirm()` and `prompt()` dialogs are not keyboard-trappable, not screen-reader friendly, cannot be styled, and block the main thread. They are used before irreversible actions (shortlisting with email notifications, bulk operations, rejections, logout) across the app. |
| **EVIDENCE** | |

| File | Line | Usage |
|---|---|---|
| `src/pages/AIAssistant.tsx` | 625 | `confirm("Shortlist …?")` |
| `src/pages/AIAssistant.tsx` | 687 | `confirm("Shortlist N selected …")` |
| `src/pages/AIAssistant.tsx` | 1106 | `prompt("Type N to confirm")` |
| `src/pages/AIAssistant.tsx` | 1485 | `confirm("Shortlist N top matches?")` |
| `src/pages/AIAssistant.tsx` | 1938 | `confirm("Shortlist N selected …")` |
| `src/pages/AIAssistant.tsx` | 1962 | `prompt("Type N to confirm")` |
| `src/pages/AIAssistant.tsx` | 2116 | `confirm("Shortlist …?")` |
| `src/pages/AIAssistant.tsx` | 2142 | `confirm("Reject …?")` |
| `src/pages/Candidates.tsx` | 707 | `confirm("Shortlist …?")` |
| `src/pages/Candidates.tsx` | 941 | `confirm("Shortlist …?")` |
| `src/pages/CandidateDetail.tsx` | 263 | `confirm("Shortlist …?")` |
| `src/pages/Shortlist.tsx` | 207 | `prompt("Type N to confirm")` |
| `src/pages/SearchReports.tsx` | 44 | `confirm("Clear all …?")` |
| `src/components/layout/TopBar.tsx` | 206 | `window.confirm("Log out?")` |

| **FIX** | Replace all `confirm()` / `prompt()` calls with the existing Radix `Dialog` component (`src/components/ui/Dialog.tsx`). Create a reusable `<ConfirmDialog>` wrapper that accepts `title`, `description`, `onConfirm`, `variant` (destructive / default). `CandidateDetail.tsx` already demonstrates this pattern with its reject confirmation modal — replicate it everywhere. |

---

### H-04 — `Settings.tsx` bypasses `authFetch` for sensitive mutations

| Field | Value |
|---|---|
| **SEVERITY** | HIGH |
| **FILE** | `src/pages/Settings.tsx` |
| **LINE** | 185, 244 |
| **DESCRIPTION** | `handleSaveProfile` (L185) and `handleUpdatePassword` (L244) use raw `fetch()` with a manually constructed `Authorization` header instead of the centralized `authFetch` wrapper. This bypasses any future middleware added to `authFetch` (e.g., token refresh, audit logging, CSRF handling) and creates an inconsistency: on the same page, `authFetch` is used at L98 and L285. |
| **EVIDENCE** | ```tsx // L185 – raw fetch for profile update const response = await fetch(`${config.apiUrl}/api/users/profile`, { headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) }, … }) // L244 – raw fetch for password change const response = await fetch(`${config.apiUrl}/api/users/password`, { headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) }, … }) ``` |
| **FIX** | Replace both `fetch()` calls with `authFetch()`, which already injects the Bearer token automatically. Remove the manual `token` variable and `Authorization` header construction. |

---

## MEDIUM Severity

### M-01 — Duplicated shortlist handler in `Candidates.tsx` (~100 lines)

| Field | Value |
|---|---|
| **SEVERITY** | MEDIUM |
| **FILE** | `src/pages/Candidates.tsx` |
| **LINE** | ~700–740, ~935–970 |
| **DESCRIPTION** | The shortlist toggle logic (confirm dialog → API call → status update → notification) is duplicated almost verbatim between the "grouped view" card actions and the "list view" row actions. |
| **EVIDENCE** | Both blocks call `confirm(…)`, then `apiClient.candidates.updateStatus(…)`, then `addNotification(…)`, with identical error handling. |
| **FIX** | Extract a shared `handleToggleShortlist(candidate: Candidate)` function and call it from both views. |

---

### M-02 — Duplicated candidate card rendering in `AIAssistant.tsx` (~200 lines)

| Field | Value |
|---|---|
| **SEVERITY** | MEDIUM |
| **FILE** | `src/pages/AIAssistant.tsx` |
| **LINE** | ~1790–1920, ~2000–2050 |
| **DESCRIPTION** | Candidate result cards are rendered in both the "interleaved" (split) view and the "non-split fallback" view with near-identical JSX. Changes to one view are easily missed in the other. |
| **FIX** | Extract a `<CandidateResultCard candidate={…} onPreview={…} onShortlist={…} />` component. |

---

### M-03 — Duplicated work-history / education mapping across 3 files

| Field | Value |
|---|---|
| **SEVERITY** | MEDIUM |
| **FILE** | `src/pages/AIAssistant.tsx`, `src/pages/CandidateDetail.tsx`, `src/hooks/useCandidates.ts` |
| **LINE** | AIAssistant: 544–555 & 595–605; CandidateDetail: 151–160; useCandidates: 51–67 |
| **DESCRIPTION** | The `(job: any) => ({ title, company, … })` and `(edu: any) => ({ degree, school, … })` mapper logic is copy-pasted three times. Each copy uses `any` and manually picks fields. |
| **FIX** | Create typed mapper utilities `mapWorkHistory(raw: unknown[]): WorkHistoryEntry[]` and `mapEducation(raw: unknown[]): EducationEntry[]` in `src/lib/utils.ts` or a new `src/lib/candidateMappers.ts`. Re-use everywhere. |

---

### M-04 — Silent error swallowing via empty `catch` blocks (11+ sites)

| Field | Value |
|---|---|
| **SEVERITY** | MEDIUM |
| **FILE** | Multiple |
| **LINE** | See table |
| **DESCRIPTION** | Errors are caught and discarded without user feedback or logging, making debugging difficult and hiding potential failures. |
| **EVIDENCE** | |

| File | Line | Code |
|---|---|---|
| `src/pages/AIAssistant.tsx` | 526, 619, 2509 | `catch { /* ignore */ }` |
| `src/pages/Settings.tsx` | 69, 87, 117, 156, 174 | `catch {}` |
| `src/pages/Shortlist.tsx` | 138 | `catch { /* skip */ }` |
| `src/lib/pdfGenerator.ts` | 1416, 1601 | `catch { /* ignore */ }` |
| `src/hooks/useEmailSync.ts` | 84 | `catch { /* ignore */ }` |

| **FIX** | For non-critical operations (localStorage/sessionStorage read/write), a silent catch is acceptable but should include a comment explaining why. For API calls or data-processing operations (AIAssistant L526, L619, L2509), add at minimum `console.warn(error)` and ideally surface a toast notification. |

---

### M-05 — Email credentials sent in plain JSON body

| Field | Value |
|---|---|
| **SEVERITY** | MEDIUM |
| **FILE** | `src/pages/SetupWizard.tsx` |
| **LINE** | 280, 299, 312 |
| **DESCRIPTION** | `handleConnect`, `handleSync`, and `handleSetupAutoSync` send `{ email, password }` as plain JSON in request bodies. While transmitted over HTTPS, the password may be logged by intermediate proxies, WAFs, or application-level request loggers on the backend. The auto-sync call additionally persists the password server-side for scheduled polling. |
| **EVIDENCE** | ```tsx body: JSON.stringify({ provider: selectedProvider, email, password }) ``` |
| **FIX** | Prefer OAuth2 flows (already supported via `handleForceRefresh`). If IMAP credentials are required, transmit them once to create a server-side encrypted credential store, then reference by ID rather than re-sending on every sync. Add `autocomplete="current-password"` to the password input field. |

---

### M-06 — HR notes stored only in `localStorage` — data loss risk

| Field | Value |
|---|---|
| **SEVERITY** | MEDIUM |
| **FILE** | `src/pages/AIAssistant.tsx` |
| **LINE** | ~2460–2490 |
| **DESCRIPTION** | Recruiter notes about candidates are stored solely in `localStorage` under key `hr_candidate_notes`. Clearing browser data, switching devices, or another recruiter's session will lose all notes. There is no backend persistence or cross-device sync. |
| **EVIDENCE** | ```tsx const savedNotes = JSON.parse(localStorage.getItem('hr_candidate_notes') || '{}') localStorage.setItem('hr_candidate_notes', JSON.stringify(allNotes)) ``` |
| **FIX** | Add a `PATCH /api/candidates/:id/notes` endpoint and sync notes to the backend. Use localStorage as a write-through cache with debounced sync. |

---

### M-07 — `ScoreCircle.tsx` is dead code (never imported)

| Field | Value |
|---|---|
| **SEVERITY** | MEDIUM |
| **FILE** | `src/components/ui/ScoreCircle.tsx` |
| **LINE** | 1–187 |
| **DESCRIPTION** | `ScoreCircle` (187 lines) is never imported anywhere in the codebase. The app exclusively uses `ScoreRing` (`src/components/ui/ScoreRing.tsx`, 37 lines). This is dead code that inflates bundle analysis and creates maintenance confusion. |
| **EVIDENCE** | `grep -r "import.*ScoreCircle" src/` returns zero results. |
| **FIX** | Delete `ScoreCircle.tsx`. It will remain available in git history if needed. |

---

### M-08 — `getMatchScoreRingColor` is dead code in `utils.ts`

| Field | Value |
|---|---|
| **SEVERITY** | MEDIUM |
| **FILE** | `src/lib/utils.ts` |
| **LINE** | 110–114 |
| **DESCRIPTION** | `getMatchScoreRingColor()` returns Tailwind `ring-*` classes but is never imported or called anywhere in the codebase. The active function is `getScoreRingColor()` (L420) which returns `stroke-*` classes for SVG usage. |
| **EVIDENCE** | `grep -r "getMatchScoreRingColor" src/` returns only the definition in `utils.ts` — zero imports. |
| **FIX** | Remove `getMatchScoreRingColor` from `utils.ts`. |

---

## LOW Severity

### L-01 — "Remember me" checkbox is non-functional

| Field | Value |
|---|---|
| **SEVERITY** | LOW |
| **FILE** | `src/pages/LoginPage.tsx` |
| **LINE** | 392–397 |
| **DESCRIPTION** | The checkbox renders but has no `checked` state, no `onChange` handler, and no effect on session persistence. It misleads users into thinking their preference is saved. |
| **EVIDENCE** | ```tsx <input type="checkbox" className="w-4 h-4 text-sky-600 …" /> <span>Remember me</span> ``` No state variable, no handler. |
| **FIX** | Either implement the feature (persist token to `localStorage` when checked, `sessionStorage` when unchecked) or remove the checkbox entirely to avoid UX confusion. |

---

### L-02 — Forms lack `aria-label` / `aria-describedby` for accessibility

| Field | Value |
|---|---|
| **SEVERITY** | LOW |
| **FILE** | `src/pages/JDBuilder.tsx`, `src/pages/SetupWizard.tsx`, `src/pages/LoginPage.tsx`, `src/pages/Settings.tsx` |
| **LINE** | Various input fields |
| **DESCRIPTION** | Only `Toast.tsx` and `Dialog.tsx` use ARIA attributes in the entire frontend. Form inputs across all page components rely on visual labels alone with no programmatic association. Screen readers cannot connect labels with their inputs. |
| **EVIDENCE** | `grep -r "aria-label\|aria-describedby" src/` returns only 6 matches, all in `Toast.tsx`, `Table.tsx`, and `index.css` — zero in any form component. |
| **FIX** | Add `<label htmlFor="…">` associated with each `<Input id="…" />`, or use `aria-label` / `aria-labelledby` on all form inputs. Consider using a form library like react-hook-form with built-in accessibility patterns. |

---

### L-03 — `[...messages].reverse().find()` creates unnecessary array copies

| Field | Value |
|---|---|
| **SEVERITY** | LOW |
| **FILE** | `src/pages/AIAssistant.tsx` |
| **LINE** | Multiple locations in message processing |
| **DESCRIPTION** | Spreading and reversing the entire `messages` array just to find the last matching element creates O(n) garbage on every call. With long chat sessions this adds GC pressure. |
| **FIX** | Replace with `messages.findLast(predicate)` (ES2023, available in all modern browsers and polyfilled by Vite's target config), or use a simple backwards `for` loop. |

---

### L-04 — `useEmailSync` clears defunct `sessionStorage` keys

| Field | Value |
|---|---|
| **SEVERITY** | LOW |
| **FILE** | `src/hooks/useEmailSync.ts` |
| **LINE** | ~80–85 |
| **DESCRIPTION** | The hook's success handler removes `sessionStorage` keys (`candidates_cache`, `candidates_cache_ts`) that may no longer be written anywhere in the current codebase, making this dead cleanup code. |
| **FIX** | Verify whether these keys are still set by any active code path. If not, remove the cleanup logic. |

---

### L-05 — "Forgot password?" button is disabled with no recovery path

| Field | Value |
|---|---|
| **SEVERITY** | LOW |
| **FILE** | `src/pages/LoginPage.tsx` |
| **LINE** | 399–405 |
| **DESCRIPTION** | The "Forgot password?" link is permanently disabled with `cursor-not-allowed` and the tooltip "Password reset coming soon." Users who forget their password have no self-service recovery. |
| **EVIDENCE** | ```tsx <button type="button" className="text-sm font-medium text-gray-400 cursor-not-allowed" title="Password reset coming soon" disabled> ``` |
| **FIX** | Either implement the password reset flow or remove the button. As a stopgap, add an `href="mailto:admin@…"` link so users can request a manual reset. |

---

### L-06 — Weak client-side password policy on registration

| Field | Value |
|---|---|
| **SEVERITY** | LOW |
| **FILE** | `src/pages/LoginPage.tsx` |
| **LINE** | 64, 361 |
| **DESCRIPTION** | Registration requires only `password.length >= 6`. There is no enforcement of character diversity (uppercase, digits, special characters) or common password checking. |
| **EVIDENCE** | ```tsx if (password.length < 6) { setError('Password must be at least 6 characters') } ``` |
| **FIX** | Add a password strength meter (e.g., `zxcvbn`) and require a minimum score. Increase the minimum length to 8 characters and require at least one uppercase letter and one digit. Backend validation should also enforce this as a backstop. |

---

## Summary

| Severity | Count |
|---|---|
| **HIGH** | 4 |
| **MEDIUM** | 8 |
| **LOW** | 6 |
| **Total** | **18** |

### Top 3 Priorities

1. **H-02 — Replace `any` types** — Highest impact-to-effort ratio. Start with `useCandidates.ts` mappers and `api.ts` response types; the rest will cascade.
2. **H-01 — Decompose `AIAssistant.tsx`** — Break the 3 437-line monolith into 6–8 focused components. This also resolves M-02 (duplicated card rendering) and M-03 (duplicated mappers).
3. **H-03 — Replace `confirm()`/`prompt()`** — Create a single `<ConfirmDialog>` and wire it into all 15 call sites. This simultaneously fixes the accessibility gap reported in L-02.
