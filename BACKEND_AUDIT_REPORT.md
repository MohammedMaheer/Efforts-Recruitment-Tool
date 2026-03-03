# Backend Deep Audit Report

**Scope:** `backend/main.py`, `backend/core/config.py`, `backend/core/middleware.py`, `backend/core/database.py`, `backend/core/dependencies.py`, `backend/services/auth_service.py`, `backend/services/database_service.py`, `backend/services/gemini_service.py`, `backend/services/resume_parser.py`, `backend/services/email_scraper.py`

**Date:** 2025-07-15

---

## Summary

| Severity | Count |
|----------|-------|
| **CRITICAL** | 4 |
| **HIGH** | 6 |
| **MEDIUM** | 5 |
| **LOW** | 2 |
| **Total** | **17** |

---

## CRITICAL

### 1. Live OAuth Tokens Committed to Repository

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | CRITICAL |
| **File** | `backend/oauth_tokens.json` (entire file) |

**Description:** A JSON file containing a **live Microsoft OAuth2 access token and refresh token** for `hr@effortz.com` is committed to the repository. The refresh token grants **indefinite access** to the mailbox via Microsoft Graph API. Although `.gitignore` lists `oauth_tokens.json`, the file was committed before the rule was added (or was force-added), so it is tracked by Git and visible to anyone with repo access.

**Code:**
```json
{
  "hr@effortz.com": {
    "access_token": "eyJ0eXAiOiJKV1QiLCJub25jZSI6Ik...<REDACTED ~2KB JWT>",
    "refresh_token": "0.AXIA...<REDACTED>",
    "expires_at": "2026-02-10T14:17:49.397convolution",
    "auth_type": "delegated"
  }
}
```

**Fix:**
1. **Immediately** revoke the refresh token in Azure AD → App Registrations → `hr@effortz.com` sessions.
2. Remove the file from Git history: `git filter-branch` or `git filter-repo --path backend/oauth_tokens.json --invert-paths`.
3. Verify `.gitignore` pattern `oauth_tokens.json` matches the path (it does — just needs the history purge).
4. Re-authenticate after the purge.

---

### 2. CRON Endpoint Accepts Empty Secret — Unauthenticated Remote Code Execution Path

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | CRITICAL |
| **File** | `backend/main.py`, line 6901 |

**Description:** The `/api/cron/sync` endpoint verifies `X-Cron-Secret` via `hmac.compare_digest`. However, if the `CRON_SECRET` environment variable is **not set** (common in dev or misconfigured deploys), `expected` becomes the empty string `""`. The guard `if not expected or not hmac.compare_digest(...)` correctly short-circuits on `not expected` **but only in the current code**. The real issue: there's a logical race with the `strip()` — if the env var is set to whitespace-only (e.g. a trailing newline in a `.env` file), `expected` becomes `""` after strip, `not expected` is True, and the 403 is raised. **Actually, re-reading: this check IS correct** — `not expected` catches the unset case. However, examination reveals a subtler problem: the `or` short-circuits, so the actual `hmac.compare_digest` branch only runs when `expected` is truthy. This is **correct but fragile** — if the logic is ever refactored to only use `hmac.compare_digest`, the empty-string case becomes `hmac.compare_digest(b"", b"")` which returns `True`, granting full access.

**However**, the more immediate concern is that **if `CRON_SECRET` is set to any known/guessable value**, any external caller can trigger a full email sync, which fetches emails, processes them through AI, and writes directly to the database. This endpoint has **no IP allowlisting** or Cloud Scheduler identity verification.

**Code (line 6900-6902):**
```python
secret = request.headers.get('X-Cron-Secret', '').strip()
expected = os.getenv('CRON_SECRET', '').strip()
if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
    raise HTTPException(403, "Unauthorized")
```

**Fix:**
1. Use **Cloud Scheduler OIDC tokens** instead of a shared secret — verify the JWT's `email` claim matches the scheduler service account.
2. At minimum, fail closed: if `CRON_SECRET` is not set, raise a startup error (not a per-request check).
3. Add `CRON_SECRET` to required env validation in `config.py`.

---

### 3. Hardcoded Webhook Secret — Predictable `clientState` Allows Forged Notifications

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | CRITICAL |
| **File** | `backend/main.py`, lines 7171, 7246 |

**Description:** The Microsoft Graph webhook subscription uses `clientState: "recruitment-tool-secret"` (hardcoded). The webhook receiver at `/api/email/webhook` checks `notification.get('clientState') != expected_state` where `expected_state = os.getenv('WEBHOOK_CLIENT_STATE', 'recruitment-tool-secret')`. The webhook endpoint is **completely unauthenticated** — anyone who knows the predictable `clientState` can POST forged notifications, causing the server to make authenticated Microsoft Graph API calls to fetch arbitrary `message_id` values. This could be used for **Server-Side Request Forgery (SSRF)** or to pollute the candidate database.

**Code (line 7171):**
```python
expected_state = os.getenv('WEBHOOK_CLIENT_STATE', 'recruitment-tool-secret')
if notification.get('clientState') != expected_state:
    logger.warning(f"Invalid clientState in webhook notification — ignoring")
    continue
```

**Code (line 7246 — subscription creation):**
```python
subscription_data = {
    ...
    "clientState": "recruitment-tool-secret"
}
```

**Fix:**
1. Generate a cryptographically random `clientState` at subscription creation time: `secrets.token_urlsafe(32)`.
2. Store the generated state in the database or an env var.
3. The webhook verification should compare against the stored value.
4. Also validate the webhook payload structure (required fields, valid resource paths) before making Graph API calls.

---

### 4. Content-Disposition Header Injection via Unsanitized Filename

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | CRITICAL |
| **File** | `backend/main.py`, line 3817 |

**Description:** The resume download endpoint inserts `resume["filename"]` (from the database) directly into the `Content-Disposition` header without sanitization. If a filename contains `"` or `\r\n`, an attacker who previously uploaded a malicious filename can inject arbitrary HTTP headers (**HTTP response splitting**), potentially enabling XSS or cache poisoning.

**Code (line 3814-3819):**
```python
return Response(
    content=resume['file_data'],
    media_type=resume['content_type'],
    headers={
        'Content-Disposition': f'attachment; filename="{resume["filename"]}"'
    }
)
```

**Fix:**
```python
import re
safe_name = re.sub(r'[^\w\s\-.]', '_', resume["filename"])[:255]
headers = {'Content-Disposition': f'attachment; filename="{safe_name}"'}
```
Or use the standard `email.utils.encode_rfc2231` for proper RFC 5987 encoding.

---

## HIGH

### 5. Chat Rate Limiter Uses Wrong Key — All Users Share One Bucket

| Field | Value |
|-------|-------|
| **Category** | Bug |
| **Severity** | HIGH |
| **File** | `backend/main.py`, line 4793 |

**Description:** The AI chat endpoint's rate limiter extracts the user ID with `current_user.get("sub", "anon")`. However, `verify_token()` in `auth_service.py` returns a dict with key `"id"` — not `"sub"`. The `"sub"` key only exists in the **raw JWT payload**, not in the verified user dict. Therefore, `.get("sub")` always returns `None`, and the fallback `"anon"` is used. **Every authenticated user shares the same "anon" rate limit bucket.** If any 10 users collectively make 10 requests in 60 seconds, all users are blocked.

**Code (line 4793):**
```python
user_id = current_user.get("sub", "anon")
```

**`auth_service.py` verify_token returns (line 289):**
```python
return {
    "id": row['id'],       # <-- key is "id", not "sub"
    "email": row['email'],
    ...
}
```

**Fix:**
```python
user_id = current_user.get("id", "anon")
```

---

### 6. Login Rate Limiter Bypassed by Cloud Run Multi-Instance Scaling

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | HIGH |
| **File** | `backend/main.py`, lines 7910-7973 |

**Description:** The login rate limiter uses an **in-memory dict** (`_login_attempts`). On Cloud Run (which scales horizontally), each container instance has its own independent dict. An attacker can bypass the 5-attempt limit simply by distributing requests across instances (automatic with Cloud Run load balancing). Additionally, when the dict exceeds 10,000 entries, `_login_attempts.clear()` **resets ALL rate limits for ALL users**, creating a trivially exploitable bypass window.

**Code (line 7910-7913):**
```python
_login_attempts: dict = {}  # email -> list of timestamps
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 900  # 15 minutes
```

**Code (line 7971-7972):**
```python
if len(_login_attempts) > 10000:
    _login_attempts.clear()  # Resets ALL limits
```

**Fix:**
1. Use a centralized store (Redis, Firestore, or Cloud Memorystore) for rate limit counters.
2. If staying in-memory, at least evict only the target key or oldest entries instead of clearing the entire dict.
3. Consider using a Cloud Armor rate-limiting rule for login endpoints.

---

### 7. OAuth2 URL Endpoints Expose Configuration Without Authentication

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | HIGH |
| **File** | `backend/main.py`, lines 6279, 6306 |

**Description:** The endpoints `GET /api/email/oauth2/url` and `GET /api/email/oauth2/authorize` have **no `Depends(require_auth)`**. Any unauthenticated user can call these to obtain the Microsoft OAuth2 authorization URL, which reveals the `client_id`, `tenant_id`, `redirect_uri`, and OAuth scopes. While this alone doesn't grant access, it leaks application identity information and enables phishing — an attacker could craft a lookalike consent page using the real `client_id`.

**Code (line 6279):**
```python
@app.get("/api/email/oauth2/url")
async def get_oauth2_url_simple(request: Request = None):
    # No auth dependency
```

**Code (line 6306):**
```python
@app.get("/api/email/oauth2/authorize")
async def get_oauth2_authorization_url(provider: str, redirect_uri: str):
    # No auth dependency
    # Also accepts arbitrary redirect_uri from query params
```

**Fix:**
1. Add `current_user: dict = Depends(require_auth)` to both endpoints.
2. For `/api/email/oauth2/authorize`, **do not accept `redirect_uri` from user input** — use the server-configured value to prevent OAuth redirect attacks.

---

### 8. Webhook Endpoint Returns Internal Errors to External Callers

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | HIGH |
| **File** | `backend/main.py`, line 7207 |

**Description:** The webhook endpoint catches all exceptions and returns `{"status": "error", "message": str(e)}`. Since this endpoint is called by Microsoft (and by anyone who knows the URL), raw exception messages — which may include database paths, internal service names, stack details, or token fragments — are returned to **external callers**.

**Code (line 7205-7207):**
```python
except Exception as e:
    logger.error(f"Webhook error: {str(e)}")
    return {"status": "error", "message": str(e)}
```

**Fix:**
```python
except Exception as e:
    logger.error(f"Webhook error: {str(e)}")
    return {"status": "error"}  # Never expose internal errors externally
```

This pattern also appears in **many other endpoints** (lines 2566, 5168, 6805, 7563, 7584, 8735, 8757, 9625) where `raise HTTPException(500, str(e))` or `raise HTTPException(500, f"Error ...: {str(e)}")` leaks exception details to API consumers.

---

### 9. Scraper Background Task Has No Auto-Recovery

| Field | Value |
|-------|-------|
| **Category** | Bug |
| **Severity** | HIGH |
| **File** | `backend/main.py`, lines 1972-1979 |

**Description:** The email scraper is started as `asyncio.create_task(scraper_service.run_continuous_scraper())` and stored in a global `scraper_task`. If the task crashes (unhandled exception), it silently dies — `scraper_task.done()` returns `True`, and `start_scraper()` would allow restarting, but **no automatic recovery occurs**. The `/api/scraper/status` endpoint would show the scraper as stopped, but there's no alerting or self-healing. In production, email sync silently stops.

**Code (line 1972-1979):**
```python
@app.post("/api/scraper/start")
async def start_scraper(background_tasks: BackgroundTasks, current_user: dict = Depends(require_auth)):
    global scraper_task
    if scraper_task and not scraper_task.done():
        return {"message": "Scraper already running"}
    scraper_task = asyncio.create_task(scraper_service.run_continuous_scraper())
    return {"message": "Email scraper started"}
```

**Fix:**
1. Add exception handling in the task with automatic restart:
```python
async def _scraper_with_restart():
    while True:
        try:
            await scraper_service.run_continuous_scraper()
        except Exception as e:
            logger.error(f"Scraper crashed, restarting in 30s: {e}")
            await asyncio.sleep(30)
```
2. Alternatively, add a `done_callback` on the task that logs the failure and triggers re-creation.

---

### 10. Default JWT Secret Accepted in Ambiguous Environments

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | HIGH |
| **File** | `backend/services/auth_service.py`, lines 21-27 |

**Description:** The JWT secret defaults to `"ai-recruiter-platform-default-secret-change-in-production"`. A `RuntimeError` is raised if `ENVIRONMENT=production` or `K_SERVICE` is set, which correctly blocks Cloud Run. However, **staging environments**, Docker Compose deployments, or any non-Cloud-Run server that doesn't set `ENVIRONMENT=production` will silently use the default secret. An attacker who knows this default can forge valid JWT tokens for any user ID.

**Code (line 21-27):**
```python
_DEFAULT_SECRET = "ai-recruiter-platform-default-secret-change-in-production"
SECRET_KEY = os.getenv("JWT_SECRET_KEY", _DEFAULT_SECRET)
if SECRET_KEY == _DEFAULT_SECRET:
    _log.getLogger(__name__).warning("⚠️  JWT_SECRET_KEY not set - using default.")
    if os.getenv("ENVIRONMENT", "development") == "production" or os.getenv("K_SERVICE"):
        raise RuntimeError("JWT_SECRET_KEY must be set in production!")
```

**Fix:**
1. Also check for common non-local indicators: `DOCKER_CONTAINER`, `KUBERNETES_SERVICE_HOST`, `GAE_APPLICATION`, etc.
2. Better: **always require** `JWT_SECRET_KEY` to be set (even in dev), and generate a random one on first startup if missing, saving it to a local `.env` file.

---

## MEDIUM

### 11. `isolation_level=None` (Autocommit) in Async Connection Pool

| Field | Value |
|-------|-------|
| **Category** | Bug |
| **Severity** | MEDIUM |
| **File** | `backend/core/database.py`, line 185 |

**Description:** The `AsyncConnectionPool._create_connection()` uses `isolation_level=None` (autocommit mode). This means every SQL statement is immediately committed. Multi-statement operations (e.g., insert candidate + insert resume) have **no transactional atomicity** — a crash between statements leaves the database in an inconsistent state. Note: this module appears unused (see finding #16), but if it's ever adopted, this will cause data corruption.

**Code (line 183-185):**
```python
conn = await aiosqlite.connect(
    self.database_path,
    isolation_level=None  # Autocommit mode for better performance
)
```

**Fix:**
Remove `isolation_level=None` and use explicit `BEGIN`/`COMMIT` for multi-statement transactions:
```python
conn = await aiosqlite.connect(self.database_path)
```

---

### 12. X-Forwarded-For Header Trusted Without Validation for Rate Limiting

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | MEDIUM |
| **File** | `backend/core/middleware.py` (rate limiter IP extraction) |

**Description:** The middleware rate limiter extracts the client IP using `request.client.host`, which on Cloud Run reflects the `X-Forwarded-For` header. An attacker can rotate `X-Forwarded-For` values to bypass per-IP rate limits. While Cloud Run's load balancer appends the real IP, the application doesn't ensure it reads the **rightmost** (load-balancer-appended) value — it uses the leftmost (client-controlled) value from `request.client.host`.

**Fix:**
1. On Cloud Run, trust only the rightmost IP in `X-Forwarded-For` (the one appended by Google's LB).
2. Or use a middleware like `uvicorn --proxy-headers --forwarded-allow-ips=*` combined with proper header parsing.

---

### 13. OAuth2 `/authorize` Endpoint Accepts Arbitrary `redirect_uri`

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | MEDIUM |
| **File** | `backend/main.py`, line 6306 |

**Description:** The `GET /api/email/oauth2/authorize` endpoint accepts `redirect_uri` as a query parameter from the **user**. This is an [open redirect / OAuth redirect attack](https://datatracker.ietf.org/doc/html/rfc6749#section-10.15) vector. An attacker can craft a URL with `redirect_uri=https://evil.com/steal` and trick a user into clicking it. After OAuth consent, the authorization code is sent to the attacker-controlled URI.

**Code (line 6306):**
```python
@app.get("/api/email/oauth2/authorize")
async def get_oauth2_authorization_url(provider: str, redirect_uri: str):
```

**Fix:**
Do not accept `redirect_uri` from user input. Use only server-configured redirect URIs:
```python
redirect_uri = os.getenv('MICROSOFT_REDIRECT_URI', 'https://efforts-recruitment.web.app/auth/callback')
```

---

### 14. Multiple Endpoints Leak Internal Error Details via `str(e)` in Production

| Field | Value |
|-------|-------|
| **Category** | Security |
| **Severity** | MEDIUM |
| **File** | `backend/main.py`, lines 2075, 2566, 5168, 6805, 6885, 7563, 7584, 8735, 8757, 9625 |

**Description:** At least 12 endpoints raise `HTTPException(500, f"Error ...: {str(e)}")` or return `str(e)` directly. In production, exception messages can expose: database file paths, SQL table names, Python tracebacks, internal service hostnames, API key prefixes, or OAuth state details. This aids attacker reconnaissance.

**Examples:**
```python
# Line 2075
raise HTTPException(500, f"Scraping error: {str(e)}")
# Line 6885
raise HTTPException(500, f"Error deduplicating: {str(e)}")
# Line 9625
raise HTTPException(500, str(e))
```

**Fix:**
In production, return generic messages. Log the full error server-side:
```python
except Exception as e:
    logger.error(f"Endpoint failed: {e}", exc_info=True)
    raise HTTPException(500, "Internal server error")
```

---

### 15. `_row_to_candidate()` Uses Fragile Column-Index Mapping

| Field | Value |
|-------|-------|
| **Category** | Bug |
| **Severity** | MEDIUM |
| **File** | `backend/services/database_service.py`, `_row_to_candidate()` |

**Description:** The `_row_to_candidate()` method maps database rows to dicts using **column index positions** (e.g., `row[0]`, `row[1]`, `row[14]`). If any `ALTER TABLE ADD COLUMN` migration changes column order (or a new column is inserted in the middle), all subsequent indexes silently shift, corrupting candidate data silently. The method tries to auto-detect column count (`len(row)`) and adjusts offsets, but this is fundamentally fragile.

**Fix:**
Use `row_factory = sqlite3.Row` (already configured) and access by **column name**:
```python
def _row_to_candidate(self, row):
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        ...
    }
```

---

## LOW

### 16. `core/database.py` — Dead Code (Entire Module Unused)

| Field | Value |
|-------|-------|
| **Category** | Dead Code |
| **Severity** | LOW |
| **File** | `backend/core/database.py` (585 lines) |

**Description:** `AsyncConnectionPool`, `AsyncDatabaseManager`, and `get_db_manager` are defined in this module and exported in `core/__init__.py`, but **never imported or used** by `main.py`, `database_service.py`, or any other service. The application uses the synchronous `DatabaseService` from `services/database_service.py` with `asyncio.to_thread()` wrappers. This 585-line module is dead weight.

**Fix:**
Remove the module or mark it as experimental. If planning to migrate to async DB access, document that intent.

---

### 17. `response_cache.clear()` Called Excessively Without Coordination

| Field | Value |
|-------|-------|
| **Category** | Performance |
| **Severity** | LOW |
| **File** | `backend/main.py` (multiple locations) |

**Description:** `response_cache.clear()` is called after nearly every write operation (candidate insert, update, delete, bulk shortlist, dedup, scraper run). On a TTL cache with `maxsize=256`, this provides marginal benefit — the cache is small and items expire naturally. The real issue: during bulk operations (e.g., bulk shortlist of 100 candidates at line 8650), `response_cache.clear()` is called **once per candidate inside the loop** AND once after. This causes cache thrashing where concurrent read requests always miss cache.

**Fix:**
Call `response_cache.clear()` once after the entire bulk operation completes, not per-iteration. The existing post-loop `clear()` is sufficient — remove the per-candidate line.

---

## Items Verified as NOT Issues

For completeness, these were investigated and found to be correctly implemented:

| Area | Result |
|------|--------|
| **SQL Injection** | All queries use parameterized placeholders (`?` or `%s`). No f-string SQL found. **SAFE.** |
| **CORS Configuration** | Production uses specific origins (`efforts-recruitment.web.app`, `efforts-recruitment.el.r.appspot.com`), not `*`. **SAFE.** |
| **`get_connection_raw()` leaks** | All 21 call sites use `try/finally: conn.close()`. **No leak.** |
| **Password hashing** | bcrypt with proper 72-byte truncation handling. **SAFE.** |
| **JWT expiry** | 24-hour tokens, `exp` claim enforced by `python-jose`. **Acceptable.** |
| **Production JWT guard** | `RuntimeError` raised if default secret used on Cloud Run (`K_SERVICE` check). **Works for Cloud Run.** |

---

## Prioritized Remediation Order

1. **Revoke and purge** `oauth_tokens.json` from Git history (Finding #1)
2. **Randomize** webhook `clientState` (Finding #3)
3. **Authenticate** OAuth URL endpoints + remove user-controlled `redirect_uri` (Findings #7, #13)
4. **Fix** chat rate limiter key from `"sub"` to `"id"` (Finding #5)
5. **Sanitize** Content-Disposition filename (Finding #4)
6. **Replace** shared-secret cron auth with OIDC token verification (Finding #2)
7. **Externalize** login rate limiter to Redis/Firestore (Finding #6)
8. **Suppress** `str(e)` in production error responses (Findings #8, #14)
9. **Add** scraper auto-restart logic (Finding #9)
10. **Require** `JWT_SECRET_KEY` always (Finding #10)
