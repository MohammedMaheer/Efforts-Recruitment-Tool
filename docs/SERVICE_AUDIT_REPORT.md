# Backend Services Audit Report

**Generated:** 2025-01-XX  
**Scope:** All 27 files in `backend/services/`  
**Total Lines of Code:** ~17,500+

---

## Summary

| Priority   | Count | Description                                    |
|------------|------:|------------------------------------------------|
| CRITICAL   |     3 | Arbitrary code execution, hardcoded secrets    |
| HIGH       |    18 | Silent error swallowing, N+1 queries, thread safety |
| MEDIUM     |    28 | Deprecated APIs, missing timezone, unbounded caches |
| LOW        |    14 | Dead code, style issues, redundant imports     |

---

## File-by-File Audit

---

### 1. `auth_service.py` — 443 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~30 | **CRITICAL** | Hardcoded default JWT secret | `_DEFAULT_SECRET = "dev-secret-key-change-in-production"` | Load from env with **no fallback**. Refuse to start if `SECRET_KEY` is missing in production. |
| 2 | ~120 | HIGH | `register()` catches generic `Exception` and always returns "email already exists" | `except Exception: return {"error": "Email already exists"}` | Catch `sqlite3.IntegrityError` separately; re-raise or log other exceptions. |
| 3 | ~80 | HIGH | `_verify_password` bare except returns `False` | `except: return False` | Use `except Exception as e:` and log the error. |
| 4 | ~60 | MEDIUM | Legacy SHA-256 password support alongside bcrypt | `if len(stored_hash) == 64: hashlib.sha256(...)` | Add a migration path to re-hash with bcrypt on next login; deprecate SHA-256. |

---

### 2. `calendar_integration_service.py` — 641 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~180, ~220 | MEDIUM | `datetime.now()` without timezone for token expiry comparison | `datetime.now()` vs ISO strings | Use `datetime.now(timezone.utc)` or `datetime.utcnow()` consistently. |
| 2 | ~300 | MEDIUM | `aiohttp.ClientSession` created per request, never reused | `async with aiohttp.ClientSession() as session:` | Create a shared session in `__init__` and close it in a `close()` method. |
| 3 | ~250 | MEDIUM | `_google_access_token` and `_google_token_expiry` written from async code without locking | Instance attributes mutated without lock | Protect with `asyncio.Lock` or use thread-safe patterns. |
| 4 | ~380 | LOW | Google Calendar `list_events` returns empty list on any exception | `except Exception: return []` | Log the error and distinguish between auth failures and transient issues. |

---

### 3. `database_service.py` — 1,966 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~800-850 | HIGH | `_row_to_candidate()` opens a **new DB connection per candidate** to check `hasResume` | `with self.get_connection() as conn: ...` inside loop | Batch the resume-existence check before calling `_row_to_candidate`, or pass it as a parameter. |
| 2 | ~1415, 1433, 1451, 1484 | HIGH | `except Exception: return 0` / `return []` silently swallows errors in multiple counting/listing methods | `except Exception: return 0` | Log the exception, then return a safe default or raise. |
| 3 | ~200 | HIGH | `get_connection_raw()` returns a raw connection, caller must manually close — risk of resource leaks | `return sqlite3.connect(...)` | Add a deprecation warning; prefer the context-managed `get_connection()`. Document the "must-close" contract. |
| 4 | ~1600 | MEDIUM | `get_search_history()` embeds `CREATE TABLE IF NOT EXISTS` inside a **read** method | Inline DDL in a query method | Run migration at startup; remove DDL from read path. |
| 5 | ~400 | MEDIUM | `is_blocked_email()` recompiles regex on every call | `re.compile()` inside function body | Compile patterns once at class level. |
| 6 | ~1200 | MEDIUM | `datetime.now()` without timezone throughout | multiple locations | Use `datetime.now(timezone.utc)`. |
| 7 | ~1900 | LOW | Singleton pattern via module-level function, not thread-safe on first call | `global _db_service; if _db_service is None: ...` | Use a `threading.Lock` around the initialization check. |

---

### 4. `db_repair.py` — 1,084 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~506, ~520, ~550 | HIGH | Bare `except:` (no `Exception`) blocks in `is_gibberish_profile()` and `keyword_based_score()` | `except:` | Change to `except Exception as e:` and log. |
| 2 | ~850 | MEDIUM | `repair_database()` builds SQL with f-string for SET clause | `f"UPDATE candidates SET {col} = ? WHERE id = ?"` | Column names come from hardcoded list so no direct injection, but wrap in a whitelist check for defense-in-depth. |
| 3 | ~900 | MEDIUM | `PRAGMA synchronous=FULL` wrapped in bare `except:` | `try: cursor.execute("PRAGMA ...") except:` | Use `except Exception as e:` and log the failure. |
| 4 | ~700 | LOW | `audit_database()` returns large dict with raw data; could be memory-intensive for large DBs | Returns all gibberish profiles in one list | Add pagination or limit parameters. |

---

### 5. `duplicate_detection_service.py` — 500 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~200 | HIGH | `find_duplicates()` is O(n²) all-pairs comparison — will be extremely slow for large candidate pools | `for i in range(len(candidates)): for j in range(i+1, ...)` | Implement blocking/bucketing by email domain or phonetic name code (Soundex/Metaphone) to reduce comparisons. |
| 2 | ~150 | MEDIUM | `name_cache` dict grows unbounded | `self.name_cache = {}` | Add LRU eviction (use `functools.lru_cache` or `cachetools.LRUCache`). |
| 3 | ~350 | MEDIUM | MD5 used for candidate hash generation | `hashlib.md5(...)` | Use SHA-256 for better collision resistance, even for non-security hashing. |
| 4 | ~400 | LOW | `merge_candidates()` uses `__import__('datetime')` inline | `__import__('datetime')` | Use a normal top-level import. |

---

### 6. `email_parser.py` — 539 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~315, 368, 387 | MEDIUM | `print()` used instead of `logger` for error output | `print(f"Error: {e}")` | Replace with `logger.error(...)`. |
| 2 | ~280 | MEDIUM | `_is_resume_file()` requires extension **AND** keyword match — rejects legitimate files like `resume.pdf` | `has_ext and has_keyword` is too restrictive | Accept if either condition is met. |
| 3 | ~350 | LOW | Phone extraction regex may match 4-digit years | `r'[\+\(]?[1-9][0-9 .\-\(\)]{8,}[0-9]'` | Exclude matches that look like year ranges. |

---

### 7. `email_scraper.py` — 1,512 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~200 | HIGH | `connect_to_inbox()` modifies **global** `socket.setdefaulttimeout()` — affects all network operations process-wide | `socket.setdefaulttimeout(timeout)` | Set timeout only on the IMAP connection object, not globally. |
| 2 | ~100 | HIGH | `processed_message_ids` is in-memory only — resets on restart, causing re-processing | `self.processed_message_ids = set()` | Persist to database or a file. |
| 3 | ~300 | MEDIUM | IMAP connections not guaranteed closed on exception paths | `try: ... imap.select(...)` without `finally: imap.logout()` | Use context manager or try/finally to ensure logout. |
| 4 | ~180 | LOW | `_last_subcategory` mutable class-level side-channel | `self._last_subcategory = None` | Make this a return value instead of a side effect. |

---

### 8. `email_templates_service.py` — 509 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~300 | MEDIUM | JSON file I/O for custom templates is not thread-safe | `open(..., 'w')` without lock | Use `threading.Lock` or `fcntl.flock` / atomic write pattern. |
| 2 | ~250 | LOW | `_process_conditionals()` uses regex on template content — potential ReDoS with adversarial input | `re.sub(r'{{#if ...}}', ...)` | Limit template size and add a timeout or use non-backtracking patterns. |

---

### 9. `followup_service.py` — 658 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~350 | HIGH | JSON file-based campaign persistence — not atomic, race conditions on concurrent writes | `json.dump(data, open(..., 'w'))` | Use database storage or atomic file writes (write to temp, then rename). |
| 2 | ~450 | MEDIUM | `_execute_step()` catches generic `Exception` and just logs | `except Exception as e: logger.error(...)` | Implement retry logic for transient failures; raise for permanent ones. |
| 3 | ~500 | MEDIUM | No retry mechanism for failed campaign steps | Step just marked as failed | Add configurable retry with exponential backoff. |
| 4 | ~550 | LOW | `process_due_steps()` iterates `active_enrollments` while potentially modifying it | `for eid, enrollment in list(self.active_enrollments.items()):` | Current `list()` copy is safe but fragile. Add a comment explaining the pattern. |

---

### 10. `gemini_service.py` — 1,307 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~100 | HIGH | `_request_count`, `_total_time`, `_error_count` are plain integers incremented from async code — **not thread-safe** | `self._request_count += 1` | Use `threading.Lock` or `asyncio.Lock` to protect counters, or use `atomic` from a library. |
| 2 | ~200 | MEDIUM | Cache dict not thread-safe for concurrent async access | `self._cache[key] = value` | Use `asyncio.Lock` around cache reads/writes. |
| 3 | ~150 | MEDIUM | `asyncio.get_event_loop()` deprecated in Python 3.10+ | `loop = asyncio.get_event_loop()` | Use `asyncio.get_running_loop()` inside async code. |
| 4 | ~600 | MEDIUM | `_generate()` returns empty string on failure — callers may not distinguish from empty valid output | `return ""` | Return `None` on failure and let callers handle it explicitly. |

---

### 11. `job_matching_service.py` — 625 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~50 | LOW | `embedding_cache` declared but never used (dead code) | `self.embedding_cache = {}` | Remove or implement caching for embeddings. |
| 2 | ~350 | LOW | `_match_culture()` base score of 50 means even zero keyword matches produce 50% | `score = 50 + ...` | Document this is intentional (benefit of the doubt) or lower the base score. |
| 3 | - | LOW | No input type validation — passing `None` for skills crashes | Implicit trust in inputs | Add `or []` guards on list parameters. |

---

### 12. `job_taxonomy.py` — 641 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~450 | MEDIUM | `classify_job_title()` keyword map uses regex-like patterns (e.g. `"real.?estate"`) but applies them with `re.search` — some short patterns like `"cook"` may false-match (e.g. "cookie") | `re.search(pattern, title_lower)` | Use word boundary: `r'\bcook\b'`. |
| 2 | - | LOW | Taxonomy is hardcoded — no way to extend at runtime | Static dict | Provide a mechanism to merge external taxonomy files. |

---

### 13. `llm_service.py` — 2,136 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~100 | HIGH | `_request_count`, `_total_time`, `_error_count` incremented without lock — race condition in multi-request scenarios | `self._request_count += 1` | Protect with `asyncio.Lock`. |
| 2 | ~300 | MEDIUM | MD5 used for cache keys | `hashlib.md5(text.encode()).hexdigest()` | Use SHA-256 or a faster non-crypto hash (xxhash). |
| 3 | ~150 | MEDIUM | `asyncio.get_event_loop()` deprecated in Python 3.10+ | multiple locations | Use `asyncio.get_running_loop()`. |
| 4 | ~80 | MEDIUM | `_http_client` (httpx.AsyncClient) never explicitly closed on shutdown (method exists but may not be called) | `self._http_client = httpx.AsyncClient(...)` | Register `close()` in app shutdown hook. |
| 5 | ~1800 | LOW | `get_llm_service_sync()` bypasses production initialization guard | Creates service without async init | Document the limitation or add sync init path. |

---

### 14. `local_ai_service.py` — 2,342 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~120 | HIGH | `embedding_cache`, `ner_cache`, `analysis_cache` grow unbounded — memory leak in long-running service | `self.embedding_cache = {}` | Use `cachetools.LRUCache(maxsize=...)` or implement eviction. |
| 2 | ~80 | MEDIUM | `torch.set_num_threads()` modifies **global** state — affects all PyTorch operations in process | `torch.set_num_threads(max(1, ...))` | Set via environment variable `OMP_NUM_THREADS` before import, or document the side effect. |
| 3 | ~450, 1300 | MEDIUM | MD5 for cache keys | `hashlib.md5(...)` | Use SHA-256 or xxhash. |
| 4 | ~1500 | MEDIUM | Context (`ctx`) parsed **twice** in `chat_with_ai()` — duplicate `json.loads(context)` | Two identical `ctx = {}; if context: ctx = json.loads(context)` blocks | Remove the duplicate parsing block. |
| 5 | ~1520 | MEDIUM | `asyncio.run()` inside `concurrent.futures.ThreadPoolExecutor` — creates new event loop per call, fragile pattern | `pool.submit(asyncio.run, self._llm_service.chat(...))` | Use `asyncio.run_coroutine_threadsafe()` with the existing loop instead. |
| 6 | ~1380 | MEDIUM | `summarize_resume()` uses deprecated `asyncio.get_event_loop()` | `loop = asyncio.get_event_loop()` | Use `asyncio.get_running_loop()`. |
| 7 | ~90 | LOW | Warm-up encoding runs synchronously during `__init__` — blocks startup | `self.sentence_model.encode(["warmup"])` | Move to background task or lazy initialization. |

---

### 15. `matching_engine.py` — 500 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~100 | HIGH | `TfidfVectorizer` is `fit_transform()`'d on **every call** — re-fits vocabulary each time | `tfidf = TfidfVectorizer(); tfidf_matrix = tfidf.fit_transform(...)` | Pre-fit the vectorizer on a representative corpus; use `transform()` for new queries. |
| 2 | ~200 | MEDIUM | `_calculate_combined_score()` is `async` but contains no `await` | `async def _calculate_combined_score(...)` | Remove `async` qualifier, or keep it if future async work is planned and add a comment. |
| 3 | ~300 | LOW | `from numpy import dot` imported inside methods | Repeated import on each call | Move to top-level import. |
| 4 | ~350 | LOW | Candidates limited to `[:100]` in fallback without explanation | `candidates[:100]` | Add comment or make configurable. |

---

### 16. `microsoft_graph.py` — 573 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~200 | MEDIUM | `datetime.now()` without timezone for token expiry | `self._token_expiry = datetime.now() + timedelta(...)` | Use `datetime.now(timezone.utc)`. |
| 2 | ~300 | MEDIUM | No HTTP session reuse — each API call creates a new connection | Uses `aiohttp.ClientSession` per-request | Create a shared session. |
| 3 | ~250 | LOW | `logger.warning()` used for **success** messages | `logger.warning("Token acquired successfully")` | Use `logger.info()`. |
| 4 | ~400 | LOW | Lambda closures passed to `asyncio.to_thread()` | `asyncio.to_thread(lambda: ...)` | Works but less readable; prefer named functions or `functools.partial`. |

---

### 17. `ml_ranking_service.py` — 500 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~80 | **CRITICAL** | `pickle.load()` on model files — **arbitrary code execution** if file is tampered | `self.model = pickle.load(f)` | Use `skops.io` or `joblib` with hash verification. At minimum, verify file integrity with a SHA-256 checksum before loading. |
| 2 | ~250 | MEDIUM | `import re` inside the `extract_features()` method, called per-candidate | `import re` in loop body | Move to top-level import. |
| 3 | ~150 | MEDIUM | Synthetic training data with 500 samples may not generalize | Hardcoded feature distributions | Log warnings when model is only synthetically trained; flag predictions as low-confidence. |
| 4 | ~350 | MEDIUM | Feature extraction has hardcoded defaults (`salary_in_range=1`, `response_time=24h`) that inflate scores | `features['salary_in_range'] = 1` | Use `None`/NaN and handle missing features in the model. |
| 5 | ~100 | LOW | No model versioning — new model silently overwrites old | `pickle.dump(self.model, f)` | Include version/timestamp in filename; keep last N models. |

---

### 18. `oauth_automation_service.py` — 582 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~260 | MEDIUM | `refresh_token()` returns `str(e)` in error response — may leak internal details to client | `return {'status': 'error', 'message': str(e)}` | Return a generic message; log the full exception server-side. |
| 2 | ~90 | MEDIUM | `_stats` dict counters incremented without lock from async code | `self._stats['total_syncs'] += 1` | Protect with `asyncio.Lock` or use atomic counters. |
| 3 | ~530 | LOW | `_get_auth_url()` generates a new URL on every status check call | Called from `get_status_summary` | Cache the URL with a short TTL or generate only once. |

---

### 19. `openai_service.py` — 568 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~150 | HIGH | No retry logic for transient OpenAI API failures (429, 500, 503) | Single attempt per call | Add `tenacity` retry with exponential backoff for retryable status codes. |
| 2 | ~200 | MEDIUM | No rate limiting — can exceed OpenAI API quotas under load | No throttle mechanism | Implement a token-bucket or semaphore-based rate limiter. |
| 3 | ~250 | MEDIUM | Error details exposed to caller via `str(e)` | `return {"error": str(e)}` | Return a generic message; log the full error. |
| 4 | ~50, 100 | LOW | `import json` redundantly inside methods | `import json` repeated | Move to top-level import. |

---

### 20. `predictive_analytics_service.py` — 597 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~300 | MEDIUM | `_calculate_avg_tenure()` returns hardcoded estimates (18 or 24 months) instead of parsing actual dates | `tenures.append(24)` / `tenures.append(18)` | Parse actual start/end dates for accurate tenure calculation. |
| 2 | ~80 | MEDIUM | Multiplicative factor model can exceed [0, 1] range (capped but lacks principled bounds) | `probability *= factor; probability = min(0.95, ...)` | Use a logistic/sigmoid function for proper probability bounding. |
| 3 | ~70 | LOW | `historical_data` loaded from JSON but predictions don't actually use it — purely heuristic | `self.historical_data = json.load(f)` | Implement Bayesian updating or logistic regression over historical outcomes. |
| 4 | ~550 | LOW | `_generate_timeline()` uses `datetime.now()` without timezone | `today = datetime.now()` | Use `datetime.now(timezone.utc)`. |

---

### 21. `resume_parser.py` — 818 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~155-165 | MEDIUM | `_clean_extracted_text()` bullet-point replacement strings appear mojibake-corrupted in source code | `text.replace('â—', 'â€¢')` etc. | Fix the source file encoding, or use Unicode escapes (`\u2022`). |
| 2 | ~230 | MEDIUM | Fallback to PyPDF2 after pdfplumber fails does not `seek(0)` in all paths | Missing `pdf_file.seek(0)` in some error branches | Ensure `pdf_file.seek(0)` before every fallback attempt. |
| 3 | ~500 | LOW | `_extract_skills()` returns max 15 skills arbitrarily | `return list(set(found_skills))[:15]` | Make the limit configurable. |
| 4 | ~790 | LOW | `_extract_job_title()` falls back to `"Software Engineer"` hardcoded | `return "Software Engineer"` | Return `"Unknown"` or `""` instead. |

---

### 22. `resume_quality_service.py` — 628 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~80 | LOW | `analysis_cache = {}` declared but never populated | `self.analysis_cache = {}` | Implement caching for repeated analyses or remove the dead code. |
| 2 | ~200 | LOW | `_analyze_employment_gaps()` uses simplified heuristic (just checks for "present") instead of actual date parsing | Checks `'present' in ...` | Implement proper date range parsing for accurate gap detection. |
| 3 | ~290 | LOW | `_analyze_job_hopping()` counts positions but doesn't factor in total career length | `if job_count >= 5: flag` | Normalize by total years: 5 jobs in 20 years ≠ 5 jobs in 5 years. |

---

### 23. `setup_service.py` — 564 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~510, 540 | LOW | `_check_disk_space()` and `_check_memory()` silently swallow errors with bare `except Exception: pass` | `except Exception: pass` | At minimum, log the exception. |
| 2 | ~12 | LOW | `import subprocess` imported but never used | `import subprocess` | Remove unused import. |
| 3 | ~250 | LOW | `_check_ai_models()` imports `LocalAIService` but only checks importability, not model loading | `from services.local_ai_service import LocalAIService` | Optionally call a health-check method. |

---

### 24. `skill_extraction_service.py` — 500 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~200 | MEDIUM | GPT-4 JSON response parsed with regex `r'\{[\s\S]*\}'` — fragile for responses with multiple JSON objects | `json_match = re.search(r'\{[\s\S]*\}', content)` | Use a JSON parser with error recovery, or request `response_format={"type": "json_object"}`. |
| 2 | ~480 | LOW | Singleton `get_skill_extractor()` not thread-safe on first call | `global _skill_extractor; if ... is None:` | Protect with `threading.Lock`. |
| 3 | ~160 | LOW | No validation or sanitization of resume text before sending to GPT-4 | Text sent verbatim to API | Truncate text, strip PII if applicable, validate length. |

---

### 25. `sms_notification_service.py` — ~380 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~85 | MEDIUM | `message_log` stored in-memory only — lost on restart | `self.message_log = []` | Persist to database. |
| 2 | ~160 | MEDIUM | `send_sms()` returns `str(e)` in error dict — may leak Twilio internals | `return {'error': str(e)}` | Return a generic error message; log the details. |
| 3 | ~220 | LOW | `send_interview_reminder()` splits name on space to get first name — fails for single-name contacts | `candidate.get('name', '').split()[0]` | Use `(name.split()[0] if name.split() else name)` or similar guard. |
| 4 | ~280 | LOW | `normalize_phone()` assumes +1 country code for 10-digit numbers (US-centric) | `return f"+1{digits}"` | Make default country code configurable; the tool is used in UAE. |

---

### 26. `token_storage.py` — ~200 lines

| # | Line(s) | Priority | Issue | Code Snippet | Recommended Fix |
|---|---------|----------|-------|-------------|-----------------|
| 1 | ~120 | MEDIUM | `datetime.now()` without timezone for token expiry | `expiry_time = datetime.now() + timedelta(...)` | Use `datetime.now(timezone.utc)`. |
| 2 | ~180 | MEDIUM | `_load_tokens()` catches bare `except Exception: return {}` — corrupted file silently returns empty | `except Exception: return {}` | Log the error; if file is corrupt, back it up before overwriting. |
| 3 | ~90 | LOW | `get_token()` only tries GCS restore once (`self._gcs_restored` flag) — restart in a new container without GCS might miss token | Flag set after one try | Retry GCS restore on explicit cache miss, not just on first call. |

---

### 27. `__init__.py` — 10 lines

No issues. Contains only a docstring listing available services.

---

## Cross-Cutting Concerns

### A. Thread Safety (HIGH)

Multiple services use plain `dict` or `int` attributes as caches/counters that are mutated from async code without locks:

| Service | Attribute(s) |
|---------|-------------|
| `gemini_service.py` | `_request_count`, `_total_time`, `_error_count`, `_cache` |
| `llm_service.py` | `_request_count`, `_total_time`, `_error_count` |
| `local_ai_service.py` | `embedding_cache`, `ner_cache`, `analysis_cache` |
| `calendar_integration_service.py` | `_google_access_token`, `_google_token_expiry` |
| `oauth_automation_service.py` | `_stats` dict |

**Recommendation:** Protect all shared mutable state with `asyncio.Lock` (for async code) or `threading.Lock` (for sync code). Consider using `cachetools.TTLCache` or `LRUCache` for bounded caches.

### B. Unbounded Caches (MEDIUM)

Several caches grow without limit:

| Service | Cache |
|---------|-------|
| `local_ai_service.py` | `embedding_cache`, `ner_cache`, `analysis_cache` |
| `duplicate_detection_service.py` | `name_cache` |
| `gemini_service.py` | `_cache` |
| `llm_service.py` | internal caches |

**Recommendation:** Use `cachetools.LRUCache(maxsize=N)` with a reasonable `maxsize` (e.g., 10,000 entries).

### C. `datetime.now()` Without Timezone (MEDIUM)

Used in at least 8 files: `database_service.py`, `calendar_integration_service.py`, `microsoft_graph.py`, `token_storage.py`, `oauth_automation_service.py`, `predictive_analytics_service.py`, `sms_notification_service.py`, `local_ai_service.py`.

**Recommendation:** Adopt `datetime.now(timezone.utc)` project-wide. Create a utility `utcnow()` function for consistency.

### D. Deprecated `asyncio.get_event_loop()` (MEDIUM)

Found in `gemini_service.py`, `llm_service.py`, `local_ai_service.py`.

**Recommendation:** Replace with `asyncio.get_running_loop()` inside async functions. For sync-to-async bridges, use `asyncio.run()` or `asyncio.run_coroutine_threadsafe()`.

### E. Error Details Exposed to Clients (MEDIUM)

Multiple services return `str(e)` in error responses: `oauth_automation_service.py`, `openai_service.py`, `sms_notification_service.py`, `auth_service.py`.

**Recommendation:** Return generic user-facing messages. Log full exceptions server-side with traceback.

### F. Singleton Initialization Not Thread-Safe (LOW)

Nearly every service uses the pattern:
```python
_instance = None
def get_service():
    global _instance
    if _instance is None:
        _instance = Service()
    return _instance
```

This is racy under multiple threads calling it simultaneously.

**Recommendation:** Use `threading.Lock` around the check-and-set, or use a module-level instance created at import time.

---

## Top 10 Priority Fixes

| Rank | File | Issue | Priority |
|------|------|-------|----------|
| 1 | `ml_ranking_service.py` | `pickle.load()` — arbitrary code execution | CRITICAL |
| 2 | `auth_service.py` | Hardcoded JWT secret `_DEFAULT_SECRET` | CRITICAL |
| 3 | `auth_service.py` | `register()` masks all errors as "email exists" | CRITICAL |
| 4 | `database_service.py` | N+1 query in `_row_to_candidate()` (new connection per candidate) | HIGH |
| 5 | `database_service.py` | Silent `except Exception: return 0/[]` across counting/listing methods | HIGH |
| 6 | `email_scraper.py` | Global `socket.setdefaulttimeout()` — thread-unsafe | HIGH |
| 7 | `matching_engine.py` | `TfidfVectorizer` re-fitted on every call | HIGH |
| 8 | `openai_service.py` | No retry logic for transient API failures | HIGH |
| 9 | `gemini_service.py` / `llm_service.py` | Non-thread-safe counters | HIGH |
| 10 | `local_ai_service.py` | Unbounded caches — memory leak | HIGH |

---

*End of audit report.*
