# Backend Comprehensive Audit Report v2

**Audit Date:** 2025-01-XX  
**Scope:** Entire Python FastAPI backend (`backend/`)  
**Files Audited:** `main.py` (9,629 lines), `core/` (13 files), `services/` (27 files), `models/` (4 files), `api/` (2 files), `Dockerfile`, `requirements.txt`

---

## EXECUTIVE SUMMARY

| Severity | Count |
|----------|-------|
| **CRITICAL** | 8 |
| **HIGH** | 19 |
| **MEDIUM** | 22 |
| **LOW** | 14 |
| **TOTAL** | **63** |

The backend suffers from a **monolithic 9,629-line `main.py`** that contains all route handlers, background tasks, AI pipelines, email sync, and authentication logic in a single file. This is the root cause of most maintainability issues. Beyond architecture, there are **real security vulnerabilities** (JWT default secret, error message leaks, unbounded memory), **concurrency bugs** (TTLCache race conditions), **deprecated API usage**, and significant **code duplication**.

---

## CRITICAL ISSUES

### C-01 | SECURITY: Default JWT Secret Key in Production Guard Is Bypassable

| Field | Value |
|-------|-------|
| **FILE** | `services/auth_service.py` |
| **LINE** | 21–26 |
| **SEVERITY** | CRITICAL |
| **CATEGORY** | Security |

**DESCRIPTION:** The JWT secret key defaults to `"ai-recruiter-platform-default-secret-change-in-production"`. The production guard checks `os.getenv("ENVIRONMENT") == "production"` and `K_SERVICE`, which is correct for Cloud Run. However, the default secret is still used for **all development, staging, and preview environments**, meaning anyone who reads the source code can forge JWT tokens for any non-production deployment.

**FIX:**
```python
# Generate a random key at startup if not set, instead of a hardcoded default
_DEFAULT_SECRET = secrets.token_urlsafe(64)  # Random per-instance
SECRET_KEY = os.getenv("JWT_SECRET_KEY", _DEFAULT_SECRET)
if not os.getenv("JWT_SECRET_KEY"):
    logger.warning("⚠️ JWT_SECRET_KEY not set — using random ephemeral key. Tokens won't survive restarts.")
```

---

### C-02 | ARCHITECTURE: 9,629-Line God File

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | 1–9629 |
| **SEVERITY** | CRITICAL |
| **CATEGORY** | Architecture / Maintainability |

**DESCRIPTION:** `main.py` contains ~100 route handlers, ~20 Pydantic models, background tasks, email sync logic, GCS backup, AI pipelines, authentication, all inline. This makes the file nearly impossible to review, test in isolation, or safely modify.

**FIX:** Extract routes into `APIRouter` modules:
- `api/candidates.py` — CRUD, bulk ops, status updates
- `api/email.py` — OAuth flows, sync, webhooks
- `api/ai.py` — analysis, chat, smart search
- `api/admin.py` — cleanup, repair, audit endpoints
- `api/auth.py` — login, register, profile
- Move Pydantic models to `models/schemas.py`
- Move background tasks to `core/tasks.py`

---

### C-03 | CONCURRENCY: TTLCache Read/Write Race Condition

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 86–87, 2779–2822 |
| **SEVERITY** | CRITICAL |
| **CATEGORY** | Concurrency / Bug |

**DESCRIPTION:** `response_cache = TTLCache(maxsize=1000, ttl=300)` is a `cachetools.TTLCache` which is **not thread-safe**. An `asyncio.Lock` called `cache_lock` is defined on line 87 but is **never used** when reading from or writing to the cache (lines 2779, 2781, 2822, 4307, 4308, 4326, 4345). Multiple concurrent requests can corrupt the internal dict structure. Under uvicorn with threads, this will cause intermittent `RuntimeError: dictionary changed size during iteration`.

**FIX:**
```python
# Wrap every cache access with the lock:
async with cache_lock:
    if cache_key in response_cache:
        return response_cache[cache_key]

# OR use cachetools @cached decorator with a Lock:
from cachetools import cached
from threading import Lock
cache_lock = Lock()

@cached(cache=response_cache, lock=cache_lock)
def get_cached_result(key):
    ...
```

---

### C-04 | SECURITY: Error Messages Leak Internal Details to Clients

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | Throughout (2098, 2346, 2859, 4500+, 8635+, etc.) |
| **SEVERITY** | CRITICAL |
| **CATEGORY** | Security |

**DESCRIPTION:** Dozens of endpoints pass raw exception messages to HTTPException: `raise HTTPException(500, f"Error: {str(e)}")`. This leaks stack traces, file paths, database schema details, and third-party API error messages to potentially unauthenticated clients. Examples:
- Line 2098: `raise HTTPException(500, f"Error purging Indeed candidates: {str(e)}")`  
- AI endpoints expose LLM connection strings on error

**FIX:** Return generic error messages to clients; log the real error server-side:
```python
except Exception as e:
    logger.error(f"Purge failed: {e}", exc_info=True)
    raise HTTPException(500, "An internal error occurred. Please try again.")
```

---

### C-05 | SECURITY: Rate Limiting on Authentication Is Easily Bypassed

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 7911–7972 |
| **SEVERITY** | CRITICAL |
| **CATEGORY** | Security |

**DESCRIPTION:** The login rate limiter uses an in-memory `_login_attempts` dict that:
1. **Resets entirely when >10,000 entries** (line 7971–7972) — an attacker spraying 10,001 different emails resets all rate limits.
2. **Is per-process** — with multiple gunicorn workers, rate limits don't share state.
3. **Has no IP-based limiting** — only email-based.
4. Registration endpoint (`/api/auth/register`) has **no rate limiting at all**.

**FIX:** Use a proper rate limiter like `slowapi` or Redis-backed counter:
```python
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, ...):
```

---

### C-06 | SECURITY: Admin Endpoints Lack Role-Based Authorization

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 2063–2610 |
| **SEVERITY** | CRITICAL |
| **CATEGORY** | Security |

**DESCRIPTION:** Admin endpoints like `/api/admin/purge-indeed`, `/api/admin/database-repair-full`, `/api/admin/cleanup-gibberish`, and `/api/admin/update-candidate/{id}` only use `Depends(require_auth)` but do **not** check user role. Any authenticated recruiter can purge the database, run repairs, or modify any candidate's data.

**FIX:** Create a `require_admin` dependency:
```python
async def require_admin(current_user: dict = Depends(require_auth)) -> dict:
    if current_user.get("role") not in ("Admin", "Manager"):
        raise HTTPException(403, "Admin access required")
    return current_user
```

---

### C-07 | BUG: `_analysis_in_progress` Dict Clearing Deadlocks Waiters

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 8821–8827 |
| **SEVERITY** | CRITICAL |
| **CATEGORY** | Concurrency / Bug |

**DESCRIPTION:** When `_analysis_in_progress` exceeds `_MAX_CONCURRENT_ANALYSES` (100), the entire dict is cleared. This means all currently-waiting `asyncio.Event.wait()` calls will **never be woken up** — they'll hang for 65 seconds until timeout.

**FIX:** Evict only the oldest entries instead of clearing:
```python
if len(_analysis_in_progress) >= _MAX_CONCURRENT_ANALYSES:
    to_remove = list(_analysis_in_progress.keys())[:_MAX_CONCURRENT_ANALYSES // 5]
    for key in to_remove:
        evt = _analysis_in_progress.pop(key, None)
        if evt:
            evt.set()  # Wake up any waiters
```

---

### C-08 | SECURITY: OAuth Tokens Stored in Plain JSON File

| Field | Value |
|-------|-------|
| **FILE** | `services/token_storage.py` |
| **LINES** | 1–204 |
| **SEVERITY** | CRITICAL |
| **CATEGORY** | Security |

**DESCRIPTION:** OAuth2 access tokens and refresh tokens (which grant full mailbox access) are stored in a plain JSON file (`oauth_tokens.json`) with no encryption. The file is readable by any process running as the same user.

**FIX:** Use Google Secret Manager or encrypt tokens at rest:
```python
from cryptography.fernet import Fernet
key = os.getenv("TOKEN_ENCRYPTION_KEY")
cipher = Fernet(key)
encrypted = cipher.encrypt(json.dumps(tokens).encode())
```

---

## HIGH ISSUES

### H-01 | PERFORMANCE: Repeated `import re as _re` Inside Functions

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 2216, 3021, 4009, 4182, 5657, 7126 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Performance / Code Quality |

**DESCRIPTION:** `import re as _re` is done inside 6+ functions. `re` is already imported at the top of the file — this is unnecessary and confusing.

**FIX:** Remove all `import re as _re` from function bodies; use the top-level `import re`.

---

### H-02 | MEMORY: `_login_attempts` Dict Grows Unbounded

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | 7911 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Memory Leak |

**DESCRIPTION:** `_login_attempts` dict accumulates timestamps for every login attempt. Old entries for dormant emails are never pruned — only a nuclear clear at 10,000 entries.

**FIX:** Use TTL-based eviction:
```python
from cachetools import TTLCache
_login_attempts = TTLCache(maxsize=10000, ttl=300)  # Auto-expire after 5 min
```

---

### H-03 | DEPRECATED: `datetime.utcnow()` Usage (7 Locations)

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 130, 271, 6181, 7015, 7239, 8369, 8635 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Deprecation |

**DESCRIPTION:** `datetime.utcnow()` is deprecated since Python 3.12 and returns a naive datetime.

**FIX:** Replace all with `datetime.now(timezone.utc)`.

---

### H-04 | BUG: `if 'candidate' in dir(): del candidate` Is Wrong

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | 6120 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Bug |

**DESCRIPTION:** `dir()` returns module-level names, not local variable state reliably inside functions. This memory cleanup is unreliable.

**FIX:**
```python
candidate = None  # Let GC handle it
```

---

### H-05 | DUPLICATION: Blocked Email Patterns in Multiple Places

| Field | Value |
|-------|-------|
| **FILE** | `main.py`, `services/database_service.py` |
| **SEVERITY** | HIGH |
| **CATEGORY** | Code Duplication |

**DESCRIPTION:** Blocked email patterns are defined in `DatabaseService.BLOCKED_EMAIL_PATTERNS` AND duplicated with inline regex checks in `main.py`'s email processing.

**FIX:** Single source of truth — always call `DatabaseService.is_blocked_email()`.

---

### H-06 | PERFORMANCE: `gc.collect()` Called in Hot Loops

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 615, 6128, 6147 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Performance |

**DESCRIPTION:** `gc.collect()` triggers a full stop-the-world garbage collection sweep. Called inside email processing loops, it adds significant latency.

**FIX:** Remove from hot loops. Process emails in fixed-size batches instead.

---

### H-07 | BUG: `response_cache.clear()` Called 13+ Times Without Lock

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 664, 864, 1557, 2098, 2116, 2138, 2218, 2346, 2577, 2859, 3023, 5795, 8655 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Concurrency |

**DESCRIPTION:** `response_cache.clear()` is called from multiple endpoints without acquiring `cache_lock`, risking corruption under concurrent load.

**FIX:** Always use `async with cache_lock:` before any cache operation.

---

### H-08 | HARDCODING: 200+ Line HTML Email Templates Inline in Python

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | ~8170–8500 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Maintainability |

**DESCRIPTION:** Rich HTML email templates with company logos, brand colors, addresses are hardcoded inline in `_send_shortlist_email()` and `_send_rejection_email()`.

**FIX:** Move to `email_templates_service.py` or Jinja2 templates.

---

### H-09 | BUG: `BATCH_SIZE = 1` Makes Parallelism Pointless

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | ~750 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Performance / Bug |

**DESCRIPTION:** IMAP fallback sync sets `BATCH_SIZE = 1`, defeating the purpose of batch processing.

**FIX:** Set `BATCH_SIZE` to 10–20.

---

### H-10 | DEAD CODE: `shutil` Import Never Used

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | 13 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Dead Code |

**FIX:** Remove `import shutil`.

---

### H-11 | SECURITY: Unvalidated File Upload Content Types

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | ~4000–4200 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Security |

**DESCRIPTION:** Resume upload endpoints check file extensions but not actual file content (magic bytes).

**FIX:** Validate file magic bytes using `python-magic` or manual header checks.

---

### H-12 | BUG: Background Tasks May Be Garbage Collected

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | ~1260–1540 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Bug |

**DESCRIPTION:** Some `asyncio.create_task()` calls don't store the task in `_persistent_tasks`.

**FIX:** Store all created tasks:
```python
task = asyncio.create_task(...)
_persistent_tasks.add(task)
task.add_done_callback(_persistent_tasks.discard)
```

---

### H-13 | PERFORMANCE: `asyncio.to_thread()` Creates Unbounded Threads for DB

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | Throughout |
| **SEVERITY** | HIGH |
| **CATEGORY** | Performance |

**DESCRIPTION:** Every database call uses `asyncio.to_thread()` which creates unbounded threads. `core/database.py` has an async pool (`AsyncConnectionPool` with `aiosqlite`) that is never used.

**FIX:** Use a bounded `ThreadPoolExecutor` or the existing async pool.

---

### H-14 | DUPLICATION: Two `require_auth` Implementations

| Field | Value |
|-------|-------|
| **FILE** | `main.py` vs `core/dependencies.py` |
| **SEVERITY** | HIGH |
| **CATEGORY** | Duplication |

**DESCRIPTION:** `require_auth` is implemented in both files and could diverge.

**FIX:** Use `core/dependencies.py` version everywhere.

---

### H-15 | INJECTION: OData Injection in Graph API Search

| Field | Value |
|-------|-------|
| **FILE** | `services/microsoft_graph.py` |
| **LINE** | 286–291 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Injection |

**DESCRIPTION:** `filter_query = f"contains(subject, '{keywords[0]}')"` — if keywords come from user input, a crafted keyword could break the OData filter.

**FIX:** Sanitize keywords by removing `'`, `"`, and OData operators.

---

### H-16 | SECURITY: Weak Password Validation

| Field | Value |
|-------|-------|
| **FILE** | `services/auth_service.py` |
| **LINES** | 132–137 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Security |

**DESCRIPTION:** Password `"Password1"` passes validation. No common password check, no special characters required.

**FIX:** Use `zxcvbn` library or check against common password lists.

---

### H-17 | RESOURCE LEAK: `get_connection_raw()` Returns Unmanaged Connection

| Field | Value |
|-------|-------|
| **FILE** | `services/database_service.py` |
| **LINE** | ~285 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Resource Leak |

**DESCRIPTION:** `get_connection_raw()` returns a connection the caller must close. Any uncaught exception leaks it.

**FIX:** Deprecate and migrate all callers to `get_connection()` context manager.

---

### H-18 | CONCURRENCY: Race in `batch_analyze` Counter Updates

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | ~9226–9260 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Concurrency |

**DESCRIPTION:** `nonlocal analyzed_count, failed_count` modified from concurrent coroutines in `asyncio.gather()`.

**FIX:** Collect results and count after gather completes.

---

### H-19 | PERFORMANCE: Loading All Candidates for Smart Search

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | ~9408 |
| **SEVERITY** | HIGH |
| **CATEGORY** | Performance |

**DESCRIPTION:** `ai_smart_search` loads all 10,000+ candidates into memory before AI matching.

**FIX:** Pre-filter by category/skills before loading.

---

## MEDIUM ISSUES

### M-01 | Global Mutable State Across Multiple Variables

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 80–88, 7911 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Architecture |

**DESCRIPTION:** `_last_email_sync_time`, `background_sync_task`, `_persistent_tasks`, `_login_attempts`, `_analysis_in_progress`, `response_cache` — all global mutable state incompatible with multi-worker deployment.

**FIX:** Package into a singleton state manager or use Redis.

---

### M-02 | `psutil` Imported Inside Functions Repeatedly

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Code Quality |

**FIX:** Single optional import at top level with `HAS_PSUTIL` flag.

---

### M-03 | `from services.llm_service import ...` Done Inside 5+ Endpoints

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 8571, 8855, 9310, etc. |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Code Quality |

**FIX:** Import once at the top of the file.

---

### M-04 | Inconsistent `matchScore` vs `match_score` Naming

| Field | Value |
|-------|-------|
| **FILE** | `main.py`, `services/database_service.py` |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Naming Consistency |

**DESCRIPTION:** Database uses `match_score`, API uses `matchScore`. Dual lookups needed everywhere.

**FIX:** Standardize with Pydantic model aliases.

---

### M-05 | Missing Request Body Validation on Admin Endpoints

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Validation |

**FIX:** Use Pydantic models with validators.

---

### M-06 | No Request Size Limits

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Security / DoS |

**FIX:** Add content-length middleware (10MB limit).

---

### M-07 | Bare `except Exception` with Pass/Minimal Logging (20+ Locations)

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | 308, 630, and 20+ more |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Error Handling |

**FIX:** Log with `exc_info=True` at minimum.

---

### M-08 | `asyncio.create_task()` Fire-and-Forget Without Error Handling

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | ~9065 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Error Handling |

**FIX:** Add done callback for error logging.

---

### M-09 | Database Migrations via Try/Except ALTER TABLE

| Field | Value |
|-------|-------|
| **FILE** | `services/database_service.py` |
| **LINES** | ~108–170 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Architecture |

**FIX:** Use Alembic or maintain a `schema_version` table.

---

### M-10 | Duplicate Detector Loads 5,000 Candidates Into Memory

| Field | Value |
|-------|-------|
| **FILE** | `api/advanced_routes.py` |
| **LINE** | ~232 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Performance |

**FIX:** Use database-level matching with indexed queries.

---

### M-11 | No HTTPS Enforcement / Security Headers

| Field | Value |
|-------|-------|
| **FILE** | `main.py`, `Dockerfile` |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Security |

**FIX:** Add `Strict-Transport-Security` header and restrict CORS in production.

---

### M-12 | `ThreadPoolExecutor` Has Fixed 4 Workers for AI

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | ~8789 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Performance |

**FIX:** Make configurable via settings.

---

### M-13 | Gunicorn Single Worker in Dockerfile

| Field | Value |
|-------|-------|
| **FILE** | `Dockerfile` |
| **LINE** | 43 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Reliability |

**FIX:** Use `-w 2` minimum or document why single worker is required.

---

### M-14 | Token Storage `_load_tokens()` Not Thread-Safe

| Field | Value |
|-------|-------|
| **FILE** | `services/token_storage.py` |
| **LINES** | 158–165 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Concurrency |

**FIX:** Wrap file read inside the lock.

---

### M-15 | `requirements.txt` Pins Minimum Versions Only

| Field | Value |
|-------|-------|
| **FILE** | `requirements.txt` |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Reproducibility |

**FIX:** Pin exact versions for production.

---

### M-16 | Connection Pool Returns Connections Without Health Check

| Field | Value |
|-------|-------|
| **FILE** | `services/database_service.py` |
| **LINES** | 52–70 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Reliability |

**FIX:** Add lightweight health check when acquiring from pool.

---

### M-17 | `LRUCache` Uses `asyncio.Lock` (Not Thread-Safe)

| Field | Value |
|-------|-------|
| **FILE** | `core/database.py` |
| **LINES** | 48–50 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Concurrency |

**FIX:** Use `threading.Lock` if accessed from threads.

---

### M-18 | `MetricsCollector.response_times` Grows Unbounded Per Endpoint

| Field | Value |
|-------|-------|
| **FILE** | `core/middleware.py` |
| **LINES** | 147–149 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Memory |

**FIX:** Use `collections.deque(maxlen=1000)`.

---

### M-19 | No Input Sanitization for Log Injection

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Security |

**FIX:** Sanitize candidate names/emails before logging.

---

### M-20 | JSON Fields Parsed Without Validation in `_row_to_candidate`

| Field | Value |
|-------|-------|
| **FILE** | `services/database_service.py` |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Robustness |

**FIX:** Wrap `json.loads()` in try/except.

---

### M-21 | No Pagination on Shortlist Audit Log

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | ~8732 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Performance |

**FIX:** Add `limit` and `offset` parameters.

---

### M-22 | `asyncio.timeout` Requires Python 3.11+

| Field | Value |
|-------|-------|
| **FILE** | `core/database.py` |
| **LINE** | ~210 |
| **SEVERITY** | MEDIUM |
| **CATEGORY** | Compatibility |

**FIX:** Use `asyncio.wait_for()` for broader compatibility.

---

## LOW ISSUES

### L-01 | Dead Code: Commented-Out Job Description Endpoints

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINE** | ~4985 |
| **SEVERITY** | LOW |
| **CATEGORY** | Dead Code |

**FIX:** Remove or move to feature branch.

---

### L-02 | Inconsistent Error Response Format

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **SEVERITY** | LOW |
| **CATEGORY** | API Consistency |

**DESCRIPTION:** Some endpoints return `{"status": "error"}`, others use HTTPException, and `core/exceptions.py` has a structured format that's unused.

**FIX:** Use `AppException` hierarchy consistently.

---

### L-03 | `ServiceContainer` in `core/dependencies.py` Is Unused

| Field | Value |
|-------|-------|
| **FILE** | `core/dependencies.py` |
| **LINES** | 73–150 |
| **SEVERITY** | LOW |
| **CATEGORY** | Dead Code |

**FIX:** Either adopt or remove.

---

### L-04 | Missing Response Type Annotations on Endpoints

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **SEVERITY** | LOW |
| **CATEGORY** | Type Safety |

**FIX:** Add `response_model=` Pydantic types.

---

### L-05 | Log Messages Use Emoji

| Field | Value |
|-------|-------|
| **FILE** | Throughout |
| **SEVERITY** | LOW |
| **CATEGORY** | Observability |

**FIX:** Use text prefixes (`[OK]`, `[ERROR]`) for better searchability.

---

### L-06 | `requirements.txt` Includes Dev Dependencies

| Field | Value |
|-------|-------|
| **FILE** | `requirements.txt` |
| **SEVERITY** | LOW |
| **CATEGORY** | Build |

**FIX:** Separate into production and dev requirement files.

---

### L-07 | `_clean_loc()` Duplicated in database_service.py

| Field | Value |
|-------|-------|
| **FILE** | `services/database_service.py` |
| **LINES** | 26–37 |
| **SEVERITY** | LOW |
| **CATEGORY** | Duplication |

**FIX:** Extract to shared utils module.

---

### L-08 | `JSONFormatter` Uses Deprecated `datetime.utcnow()`

| Field | Value |
|-------|-------|
| **FILE** | `core/logging.py` |
| **LINE** | 24 |
| **SEVERITY** | LOW |
| **CATEGORY** | Deprecation |

**FIX:** Use `datetime.now(timezone.utc)`.

---

### L-09 | `download_attachment()` Always Uses `/me/` Path

| Field | Value |
|-------|-------|
| **FILE** | `services/microsoft_graph.py` |
| **LINE** | ~355 |
| **SEVERITY** | LOW |
| **CATEGORY** | Bug |

**DESCRIPTION:** Doesn't route based on auth type — breaks with application credentials.

**FIX:** Apply same auth-type routing as other methods.

---

### L-10 | `create_folder()` Always Uses `/me/` Path

| Field | Value |
|-------|-------|
| **FILE** | `services/microsoft_graph.py` |
| **LINE** | ~380 |
| **SEVERITY** | LOW |
| **CATEGORY** | Bug |

**FIX:** Same as L-09.

---

### L-11 | No Health Check for External Dependencies

| Field | Value |
|-------|-------|
| **FILE** | `main.py` |
| **LINES** | ~1660–1810 |
| **SEVERITY** | LOW |
| **CATEGORY** | Observability |

**FIX:** Add `/health/deep` endpoint checking DB, AI, email service.

---

### L-12 | `passlib[bcrypt]` in Requirements but Direct `bcrypt` Used

| Field | Value |
|-------|-------|
| **FILE** | `requirements.txt`, `services/auth_service.py` |
| **SEVERITY** | LOW |
| **CATEGORY** | Dependencies |

**FIX:** Remove `passlib[bcrypt]` — unused.

---

### L-13 | `redis` in Requirements but Never Used

| Field | Value |
|-------|-------|
| **FILE** | `requirements.txt` |
| **SEVERITY** | LOW |
| **CATEGORY** | Dependencies |

**FIX:** Remove or mark as truly optional.

---

### L-14 | `msal` in Requirements but Never Used

| Field | Value |
|-------|-------|
| **FILE** | `requirements.txt` |
| **SEVERITY** | LOW |
| **CATEGORY** | Dependencies |

**FIX:** Either use MSAL for OAuth2 flows or remove.

---

## SUMMARY OF RECOMMENDED ACTIONS

### Immediate (Fix Before Next Deploy)
1. **C-01:** Fix JWT default secret — use random ephemeral key
2. **C-03:** Add thread-safety to TTLCache access
3. **C-04:** Stop leaking exception details to clients
4. **C-05:** Implement proper rate limiting (slowapi/Redis)
5. **C-06:** Add role-based authorization to admin endpoints
6. **C-08:** Encrypt OAuth tokens at rest

### Short-Term (1–2 Sprints)
7. **C-02:** Begin extracting `main.py` into API routers
8. **H-03:** Replace all `datetime.utcnow()` calls
9. **H-05:** Consolidate blocked email patterns
10. **H-11:** Add file content validation for uploads
11. **H-13:** Use the existing async connection pool or bounded thread pool
12. **H-14:** Unify `require_auth` implementation

### Medium-Term (1–2 Months)
13. **M-09:** Implement proper database migrations
14. **M-15:** Pin dependency versions
15. **M-05/M-06:** Add request validation and size limits
16. **L-02/L-04:** Standardize error responses and add response models
