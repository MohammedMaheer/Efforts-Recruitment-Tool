# Backend Deep Security & Code Quality Audit

**Date:** 2025-01-XX  
**Scope:** Full Python FastAPI backend — `backend/` directory  
**Files analysed:** main.py (9 625 lines), 30+ service/core/model/API files  
**Methodology:** Manual line-by-line review of every file

---

## CRITICAL Findings

### 1. `require_auth` Does Not Return the User Dict — Every Protected Endpoint Receives `None`

| Field | Value |
|---|---|
| **SEVERITY** | 🔴 CRITICAL |
| **FILE** | `backend/core/dependencies.py` |
| **LINES** | 25–68 |
| **CATEGORY** | Security — Auth bypass |

**DESCRIPTION:**  
`require_auth()` validates the JWT token and obtains the `user` dict, but **never returns it**. The function body ends after the `if not user: raise` block without a `return user` statement. A stray `return user` exists on line 68, but it is **unreachable dead code** placed *after* the definition of `require_admin`.

Because FastAPI's `Depends()` uses the return value of the dependency, every route that does `current_user: dict = Depends(require_auth)` receives **`None`** as `current_user`. This means:

* `current_user.get('sub')` / `current_user.get('role')` raise `AttributeError` or silently return `None`.
* `require_admin` — which checks `current_user.get('role') != 'admin'` — always sees `None != 'admin'` → **admin check passes or fails unpredictably** depending on whether `None` has the method `.get()`.
* Audit log entries record `user='None'` instead of the real actor.

**EVIDENCE:**

```python
# Line 25-55 (simplified)
async def require_auth(authorization: Optional[str] = Header(None)) -> dict:
    ...
    user = auth_service.verify_token(parts[1])
    if not user:
        raise HTTPException(...)
    # ← MISSING: return user

# Line 57-67
async def require_admin(current_user: dict = Depends(require_auth)) -> dict:
    if current_user.get('role') != 'admin':   # current_user is None
        raise HTTPException(...)
    return current_user

    return user   # Line 68 — DEAD CODE (after require_admin's return)
```

**FIX:**  
Add `return user` at the end of `require_auth`, and remove the orphaned `return user` on line 68.

```python
async def require_auth(authorization: Optional[str] = Header(None)) -> dict:
    ...
    user = auth_service.verify_token(parts[1])
    if not user:
        raise HTTPException(...)
    return user          # ← ADD THIS

async def require_admin(current_user: dict = Depends(require_auth)) -> dict:
    if current_user.get('role') != 'admin':
        raise HTTPException(...)
    return current_user
# REMOVE the stray `return user` below require_admin
```

---

### 2. Mass Internal Error Leaking via `str(e)` in 50+ Endpoints

| Field | Value |
|---|---|
| **SEVERITY** | 🔴 CRITICAL |
| **FILE** | `backend/main.py` |
| **LINES** | See list below |
| **CATEGORY** | Security — Information disclosure |

**DESCRIPTION:**  
Over 50 HTTP 500 responses embed the raw Python exception message in the `detail` field of the response JSON. These messages can expose:

* Internal file paths and module names
* Database schema details (column names, SQL errors)
* Stack traces from third-party libraries
* OAuth token exchange error messages
* API keys partially visible in URL-related errors

**EVIDENCE (sample — 50+ occurrences):**

| Line | Code |
|---|---|
| 2828 | `raise HTTPException(500, f"Error fetching candidates: {str(e)}")` |
| 3131 | `raise HTTPException(500, f"Error: {str(e)}")` |
| 3824 | `raise HTTPException(500, f"Error downloading resume: {str(e)}")` |
| 5277 | `raise HTTPException(500, f"Batch import failed: {str(e)}")` |
| 5375 | `raise HTTPException(500, f"Failed to import LinkedIn profile: {str(e)}")` |
| 5713 | `raise HTTPException(500, f"Error syncing emails: {str(e)}")` |
| 5767 | `raise HTTPException(500, f"Error during auto-authentication: {str(e)}")` |
| 6396 | `raise HTTPException(500, f"Error processing OAuth2 callback: {str(e)}")` |
| 6983 | `raise HTTPException(500, f"Auth error: {str(auth_err)}")` |
| 7486 | `raise HTTPException(500, f"Token refresh error: {str(e)}")` |
| 8011 | `raise HTTPException(500, f"Registration error: {str(e)}")` |
| 8049 | `raise HTTPException(500, f"Error updating profile: {str(e)}")` |
| 8074 | `raise HTTPException(500, f"Error updating password: {str(e)}")` |
| 8427 | `raise HTTPException(500, f"Error updating candidate status: {str(e)}")` |
| 8825 | `raise HTTPException(500, f"Error generating AI analysis: {str(e)}")` |

(Plus ~35 more — total 50+)

**FIX:**  
Replace all with generic messages. Log `str(e)` server-side only:

```python
except Exception as e:
    logger.error(f"Batch import error: {e}", exc_info=True)
    raise HTTPException(500, "Batch import failed")
```

---

### 3. Unauthenticated Endpoints Expose Sensitive Data

| Field | Value |
|---|---|
| **SEVERITY** | 🔴 CRITICAL |
| **FILE** | `backend/main.py` |
| **LINES** | 6216, 6303, 7132, 7382 |
| **CATEGORY** | Security — Missing auth |

**DESCRIPTION:**  
Several endpoints lack `Depends(require_auth)`:

| Line | Endpoint | Risk |
|---|---|---|
| 6216 | `GET /api/oauth2/callback` | By design (redirect) — acceptable |
| 6303 | `GET /api/email/oauth2/url` | Leaks `MICROSOFT_CLIENT_ID` and tenant to unauthenticated callers |
| 7132 | `POST /api/email/webhook` | By design (Microsoft callback) — validated by `clientState`, acceptable |
| 7382 | `GET /api/email/supported-providers` | Low risk, informational — acceptable |
| 9432 | `GET /api/taxonomy` | Public taxonomy data — acceptable |
| 9441 | `GET /api/taxonomy/{category}/subcategories` | Public taxonomy data — acceptable |

The `/api/email/oauth2/url` endpoint returns the full `auth_url` including `client_id` to any unauthenticated request. While `client_id` alone isn't secret, combined with `tenant_id` it aids reconnaissance.

**FIX:**  
Add `Depends(require_auth)` to `/api/email/oauth2/url`.

---

### 4. Webhook Validation Uses Weak Default Secret

| Field | Value |
|---|---|
| **SEVERITY** | 🟠 HIGH |
| **FILE** | `backend/main.py` |
| **LINES** | 7169 |
| **CATEGORY** | Security — Weak authentication |

**DESCRIPTION:**  
The email webhook validates `clientState` against `os.getenv('WEBHOOK_CLIENT_STATE', 'recruitment-tool-secret')`. The default value is a guessable static string. If the environment variable is not set, any attacker can forge webhook notifications by including this string.

**EVIDENCE:**
```python
expected_state = os.getenv('WEBHOOK_CLIENT_STATE', 'recruitment-tool-secret')
if notification.get('clientState') != expected_state:
```

**FIX:**  
Remove the default fallback. Require the env var to be set, or generate a random UUID at startup and persist it:

```python
expected_state = os.getenv('WEBHOOK_CLIENT_STATE')
if not expected_state:
    raise HTTPException(503, "Webhook secret not configured")
```

---

### 5. OAuth Token Storage in Plain-Text JSON File

| Field | Value |
|---|---|
| **SEVERITY** | 🟠 HIGH |
| **FILE** | `backend/services/token_storage.py` |
| **LINES** | 1–204 |
| **CATEGORY** | Security — Secret management |

**DESCRIPTION:**  
OAuth2 access tokens and refresh tokens are stored **unencrypted** in `oauth_tokens.json` on disk and uploaded to GCS as plain JSON. Anyone with filesystem access or GCS read permission can extract valid tokens, gaining full Mail.Read/Mail.Send access to the configured Microsoft account.

**EVIDENCE:**
```python
tokens[email] = {
    'access_token': access_token,
    'refresh_token': refresh_token,
    ...
}
with open(self.storage_file, 'w') as f:
    json.dump(tokens, f, indent=2)
```

**FIX:**  
Encrypt tokens at rest using `cryptography.fernet` with a key from an env var or GCP Secret Manager. At minimum, restrict GCS bucket permissions to the service account only.

---

## HIGH Findings

### 6. No CSRF Protection on State-Changing POST Endpoints

| Field | Value |
|---|---|
| **SEVERITY** | 🟠 HIGH |
| **FILE** | `backend/main.py` |
| **LINES** | Multiple |
| **CATEGORY** | Security — CSRF |

**DESCRIPTION:**  
All state-changing endpoints (POST/PUT/DELETE) rely solely on the `Authorization: Bearer` header for auth. While bearer tokens in headers are inherently CSRF-resistant when stored in JS memory, if tokens are stored in cookies (e.g., for auto-auth), form-based CSRF becomes possible. The CORS config appears correct (per earlier fix), but there's no explicit CSRF token mechanism.

**FIX:**  
Ensure tokens are **never** set as cookies. If cookie auth is ever added, also add CSRF token validation.

---

### 7. `clear_search_history` Leaks Error via `str(e)` in JSON Body

| Field | Value |
|---|---|
| **SEVERITY** | 🟠 HIGH |
| **FILE** | `backend/main.py` |
| **LINE** | 5023 |
| **CATEGORY** | Security — Information disclosure |

**DESCRIPTION:**  
Unlike most error handlers that use `HTTPException`, this one returns a 200 with the error embedded:

```python
except Exception as e:
    return {"status": "error", "message": str(e)}
```

A database error would leak schema info with a 200 status code, bypassing any error-monitoring that looks for 5xx.

**FIX:**  
Return HTTPException(500) with a generic message.

---

### 8. `cron/sync` Shared Secret Compared Without Timing Safety (Minor)

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINE** | 6895 |
| **CATEGORY** | Security — Authentication |

**DESCRIPTION:**  
The cron secret comparison uses `hmac.compare_digest()` — this is **correct** and timing-safe. However, the fallback when `CRON_SECRET` is empty is to reject all requests, which is good. No issue here upon deeper review.

*(Keeping this entry for audit completeness — confirmed NOT a vulnerability.)*

---

### 9. Registration Endpoint Leaks `str(e)` (User-Triggerable)

| Field | Value |
|---|---|
| **SEVERITY** | 🟠 HIGH |
| **FILE** | `backend/main.py` |
| **LINE** | 8011 |
| **CATEGORY** | Security — Information disclosure |

**DESCRIPTION:**  
```python
except Exception as e:
    logger.error(f"Registration error: {e}")
    raise HTTPException(500, f"Registration error: {str(e)}")
```

Registration is a **public-facing** endpoint (no auth required). An attacker can send malformed data to trigger exceptions and harvest internal error details.

**FIX:**  
Return `raise HTTPException(500, "Registration failed. Please try again.")`.

---

### 10. Password-Related Errors Leak Internal State

| Field | Value |
|---|---|
| **SEVERITY** | 🟠 HIGH |
| **FILE** | `backend/main.py` |
| **LINES** | 8074 |
| **CATEGORY** | Security — Information Disclosure |

**DESCRIPTION:**  
The password update endpoint catches `ValueError` and re-raises it as 400, but then catches all `Exception` and leaks `str(e)`:

```python
except Exception as e:
    raise HTTPException(500, f"Error updating password: {str(e)}")
```

If bcrypt or the DB layer throws, internal details (e.g., "password hash is too long") could be exposed.

**FIX:**  
Use generic error: `"Password update failed"`.

---

## MEDIUM Findings

### 11. `get_sync_status` Leaks `str(e)` in Non-Error 200 Response

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | 7099–7103 |
| **CATEGORY** | Security — Information Disclosure |

**DESCRIPTION:**
```python
except Exception as e:
    return {
        ...
        'error': str(e)
    }
```

Returns error detail in a 200 response body.

---

### 12. In-Memory Dicts Growing Without Bound

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | 7917, 8808 |
| **CATEGORY** | Performance — Memory leak |

**DESCRIPTION:**  
`_login_attempts` dict grows to 10,000 entries before a nuclear clear. In high-traffic deployments, this can consume significant memory. Also, `_analysis_in_progress` has a similar 100-entry nuclear clear pattern.

The nuclear clear on `_login_attempts` (line 7958) also **resets the rate-limiting state for all users**, allowing an attacker who is rate-limited to make a burst of 10,000 failed login attempts to other emails, clear the dict, and retry.

**EVIDENCE:**
```python
if len(_login_attempts) > 10000:
    _login_attempts.clear()   # Resets rate limit for ALL users
```

**FIX:**  
Evict only stale entries (those outside the window), not all entries.

---

### 13. `JD Generation` Endpoint Leaks `str(e)` (Line 5181)

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINE** | 5181 |
| **CATEGORY** | Security — Information Disclosure |

**EVIDENCE:**
```python
except Exception as e:
    logger.error(f"JD generation error: {e}")
    raise HTTPException(status_code=500, detail=str(e))
```

Pure `str(e)` without any prefix — could expose full traceback from Gemini/OpenAI libraries.

---

### 14. `batch_size` Query Parameter Has No Upper Bound

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | 5389, 9186 |
| **CATEGORY** | Performance — DoS |

**DESCRIPTION:**  
`stream_all_candidates(batch_size: int = 100)` and `batch_analyze(batch_size: int = 50)` accept arbitrary user-supplied `batch_size` with no max. A malicious user could send `batch_size=1000000` to cause OOM.

**FIX:**  
Clamp: `batch_size = min(max(batch_size, 1), 500)`.

---

### 15. `limit` Parameters Unbounded in Multiple Endpoints

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | 4999, 5530 |
| **CATEGORY** | Performance — DoS |

**DESCRIPTION:**  
`get_search_history(limit: int = 50)` passes `limit` directly to SQL `LIMIT ?`. An attacker can send `limit=999999999` to dump the entire table.

Similarly, `/api/email/sync` accepts `request.limit` which controls how many emails are fetched in one go.

**FIX:**  
Enforce maximum: `limit = min(limit, 500)`.

---

### 16. `smart-refetch` Loads ALL Messages into Memory (5000 cap insufficient)

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | 7700 |
| **CATEGORY** | Performance — Memory |

**DESCRIPTION:**  
`smart_email_refetch` calls `graph_service.get_messages(top=5000, fetch_all=True)`. The `get_messages` method has a 2000-email hard cap, but even 2000 full email objects (with body HTML) can consume hundreds of MB.

**FIX:**  
Use `get_messages_paged()` (the paged generator) instead of loading all at once.

---

### 17. Sensitive Data Not Redacted in Logs

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | Multiple |
| **CATEGORY** | Security — Logging |

**DESCRIPTION:**  
Candidate emails, names, and OAuth token prefixes are logged at WARNING level:
- Line 5337: `logger.info(f"📥 LinkedIn import: {profile.name}")`
- Line 6052: `logger.info(f"✅ AI scored {candidate.get('name')}: {score}%")`
- Line 7078: `results["token_prefix"] = (token_data['access_token'] or '')[:20] + "..."`

The debug endpoint at `/api/email/backfill-debug` returns token prefixes in the response body.

**FIX:**  
Remove token prefixes from API responses. Hash PII in logs unless DEBUG mode.

---

### 18. `/api/email/backfill-debug` Exposes Internal State

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | 7038–7095 |
| **CATEGORY** | Security — Information disclosure |

**DESCRIPTION:**  
This endpoint returns:
- OAuth token prefix (first 20 chars)
- Graph API base URL with user email
- Message ID samples with candidate ID mappings
- Raw HTTP response bodies from Graph API

Even though it requires auth, this is excessive debug info that shouldn't be in production.

**FIX:**  
Gate behind admin-only (`Depends(require_admin)`) or disable in production.

---

### 19. Race Condition in `trigger_reset_and_reparse` Lock

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | 5780–5790 |
| **CATEGORY** | Data Integrity — Race Condition |

**DESCRIPTION:**  
The lock check and acquire aren't atomic:

```python
if trigger_reset_and_reparse._lock.locked():
    logger.info("Sync already in progress, skipping duplicate request")
    return
await trigger_reset_and_reparse._lock.acquire()
```

Between `locked()` check and `acquire()`, another coroutine could acquire the lock.

**FIX:**  
Use `try_acquire` pattern:

```python
if not trigger_reset_and_reparse._lock.locked():
    async with trigger_reset_and_reparse._lock:
        # ... sync logic
```

Or better, use `asyncio.Lock` with a try-acquire:
```python
try:
    await asyncio.wait_for(trigger_reset_and_reparse._lock.acquire(), timeout=0)
except asyncio.TimeoutError:
    return  # Already running
```

---

### 20. `advanced_routes.py` — All Routes Rely on Router-Level Auth (Correct but Fragile)

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/api/advanced_routes.py` |
| **LINE** | 53 |
| **CATEGORY** | API Design — Auth |

**DESCRIPTION:**  
```python
router = APIRouter(..., dependencies=[Depends(require_auth)])
```

Auth is enforced at router level, but **`require_auth` returns `None`** (Finding #1), so `current_user` is never available in these routes either. Since they don't destructure `current_user`, they don't crash — but they also have no access to the user identity.

**FIX:**  
Fix Finding #1 first. Then consider adding per-route `current_user` parameters where audit logging is needed.

---

### 21. `response_cache` (TTLCache) Not Protected by Lock

| Field | Value |
|---|---|
| **SEVERITY** | 🟡 MEDIUM |
| **FILE** | `backend/main.py` |
| **LINES** | ~2800 (get_candidates) and ~30 `.clear()` calls |
| **CATEGORY** | Data Integrity — Thread Safety |

**DESCRIPTION:**  
`response_cache = TTLCache(maxsize=64, ttl=300)` is accessed from multiple async coroutines without a lock. While Python's GIL provides some protection for simple dict operations, TTLCache's internal eviction logic during concurrent read+write/clear can corrupt state.

*(Note from the user's pre-audit: "TTLCache race condition — acknowledged." Documented here for completeness.)*

---

## LOW Findings

### 22. `del candidate` in `trigger_reset_and_reparse` Uses `dir()` Check

| Field | Value |
|---|---|
| **SEVERITY** | 🟢 LOW |
| **FILE** | `backend/main.py` |
| **LINE** | 6101 |
| **CATEGORY** | Code Quality |

**EVIDENCE:**
```python
if 'candidate' in dir(): del candidate
if 'analysis_text' in dir(): del analysis_text
```

`dir()` returns the local scope as strings — this works but is unconventional. Use `try/except NameError` or restructure to avoid the need for manual deletion.

---

### 23. `_backfill_resumes_task` Uses Synchronous `requests` in Lambda Closures

| Field | Value |
|---|---|
| **SEVERITY** | 🟢 LOW |
| **FILE** | `backend/main.py` |
| **LINES** | 6550–6700 |
| **CATEGORY** | Performance |

**DESCRIPTION:**  
Graph API calls use `await asyncio.to_thread(lambda u=url: requests.get(u, ...))`. This works but creates a new thread per request. For bulk operations (hundreds of emails), using `httpx.AsyncClient` would be more efficient.

---

### 24. Hardcoded Company Information in Email Templates

| Field | Value |
|---|---|
| **SEVERITY** | 🟢 LOW |
| **FILE** | `backend/main.py` |
| **LINES** | 8135–8200, 8280–8380 |
| **CATEGORY** | Code Quality — Configuration |

**DESCRIPTION:**  
Rejection and shortlist email templates contain hardcoded company details (address, phone, certifications). While `company_name` and `recruiter_name` use env vars, the physical address and certifications are inline HTML.

**FIX:**  
Move email templates to configurable files or the `email_templates_service`.

---

### 25. `main.py` Is 9,625 Lines — Extreme Monolith

| Field | Value |
|---|---|
| **SEVERITY** | 🟢 LOW |
| **FILE** | `backend/main.py` |
| **CATEGORY** | Code Quality — Maintainability |

**DESCRIPTION:**  
A single 9,625-line file contains all route handlers, background tasks, email sync logic, and utility functions. This makes security review, testing, and maintenance extremely difficult.

**FIX:**  
Split into routers: `routes/candidates.py`, `routes/email.py`, `routes/ai.py`, `routes/oauth.py`, etc.

---

### 26. Multiple Pydantic Models Defined Inline in `main.py`

| Field | Value |
|---|---|
| **SEVERITY** | 🟢 LOW |
| **FILE** | `backend/main.py` |
| **LINES** | ~5280, ~7910, ~8080, ~8550, etc. |
| **CATEGORY** | Code Quality |

**DESCRIPTION:**  
`LinkedInProfileImport`, `LoginRequest`, `RegisterRequest`, `UserProfile`, `PasswordUpdate`, `CandidateStatusUpdate`, `ChatMessage`, `AnalyzeMatchRequest`, `BulkShortlistRequest`, `GenerateEmailRequest`, `InterviewQuestionsRequest`, `SummarizeResumeRequest` — all defined inline in `main.py` rather than in `models/schemas.py`.

---

## Summary Table

| # | Severity | Category | Finding | File | Lines |
|---|---|---|---|---|---|
| 1 | 🔴 CRITICAL | Auth Bypass | `require_auth` doesn't return user | dependencies.py | 25–68 |
| 2 | 🔴 CRITICAL | Info Disclosure | 50+ endpoints leak `str(e)` | main.py | 50+ locations |
| 3 | 🔴 CRITICAL | Missing Auth | `/api/email/oauth2/url` unauthenticated | main.py | 6303 |
| 4 | 🟠 HIGH | Weak Secret | Webhook default `clientState` guessable | main.py | 7169 |
| 5 | 🟠 HIGH | Secret Mgmt | OAuth tokens stored unencrypted | token_storage.py | 1–204 |
| 6 | 🟠 HIGH | CSRF | No CSRF protection (bearer-only mitigates) | main.py | — |
| 7 | 🟠 HIGH | Info Disclosure | `clear_search_history` leaks `str(e)` in 200 | main.py | 5023 |
| 8 | 🟡 MEDIUM | Auth | `cron/sync` hmac OK ✓ | main.py | 6895 |
| 9 | 🟠 HIGH | Info Disclosure | Registration leaks `str(e)` (public endpoint) | main.py | 8011 |
| 10 | 🟠 HIGH | Info Disclosure | Password update leaks `str(e)` | main.py | 8074 |
| 11 | 🟡 MEDIUM | Info Disclosure | `get_sync_status` leaks error in 200 body | main.py | 7099 |
| 12 | 🟡 MEDIUM | Memory Leak | `_login_attempts` nuclear clear resets rate limit | main.py | 7958 |
| 13 | 🟡 MEDIUM | Info Disclosure | JD generation leaks `str(e)` | main.py | 5181 |
| 14 | 🟡 MEDIUM | DoS | `batch_size` unbounded | main.py | 5389, 9186 |
| 15 | 🟡 MEDIUM | DoS | `limit` params unbounded | main.py | 4999 |
| 16 | 🟡 MEDIUM | Memory | `smart-refetch` loads all messages at once | main.py | 7700 |
| 17 | 🟡 MEDIUM | Logging | PII and token prefixes in logs/responses | main.py | Multiple |
| 18 | 🟡 MEDIUM | Info Disclosure | `/api/email/backfill-debug` exposes internals | main.py | 7038 |
| 19 | 🟡 MEDIUM | Race Condition | Lock check-then-acquire not atomic | main.py | 5780 |
| 20 | 🟡 MEDIUM | Auth | Advanced routes inherit broken `require_auth` | advanced_routes.py | 53 |
| 21 | 🟡 MEDIUM | Thread Safety | TTLCache not locked (acknowledged) | main.py | ~2800 |
| 22 | 🟢 LOW | Code Quality | `dir()` check for variable existence | main.py | 6101 |
| 23 | 🟢 LOW | Performance | Sync `requests` via lambda in thread | main.py | 6550 |
| 24 | 🟢 LOW | Configuration | Hardcoded company info in email templates | main.py | 8135 |
| 25 | 🟢 LOW | Maintainability | 9,625-line monolith | main.py | — |
| 26 | 🟢 LOW | Code Quality | Pydantic models defined inline | main.py | Multiple |

---

## Recommended Priority

1. **Immediate (today):** Fix Finding #1 (`require_auth` return) — this breaks all protected endpoints.
2. **Urgent (this week):** Bulk-fix Finding #2 (str(e) leaking) across all 50+ locations.
3. **High priority:** Fix Findings #3, #4, #5, #9, #10.
4. **Medium priority:** Findings #12, #14, #15, #18, #19.
5. **Backlog:** Findings #22–#26.
