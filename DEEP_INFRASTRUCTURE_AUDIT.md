# Deep Infrastructure, Configuration & Dead Code Audit

**Date:** 2026-02-25  
**Scope:** Infrastructure configs, dead backend services, dead frontend components, browser extension

---

## PART 1: Infrastructure & Configuration

### 1. Dockerfile.frontend

**File:** `Dockerfile.frontend`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🔴 HIGH | **Hardcoded Cloud Run URL in ARG default** | `ARG VITE_API_URL=https://recruitment-backend-82519464499.us-central1.run.app` — the default build arg leaks the production backend URL into the image layer. Anyone pulling the image sees it. Should be empty or a placeholder. |
| 2 | 🟡 MEDIUM | **Nginx runs as root** | The production stage uses `nginx:alpine` without switching to a non-root user. Should add `RUN chown -R nginx:nginx /usr/share/nginx/html` and configure nginx to run as nginx user. |
| 3 | 🟡 MEDIUM | **Two ports exposed unnecessarily** | `EXPOSE 80` and `EXPOSE 3000` — the nginx config listens on both, but production only needs one. Port 3000 is a dev artifact. |
| 4 | 🟢 LOW | **No `.dockerignore` check** | Missing `.dockerignore` could cause `node_modules/`, `.git/`, and other large directories to be copied into the build context, slowing builds. |
| 5 | 🟢 LOW | **Layer optimization is good** | Package files copied before source — proper use of Docker layer caching. ✅ |

### 2. backend/Dockerfile

**File:** `backend/Dockerfile`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🔴 HIGH | **Runs as root** | No `USER` directive — the container runs as root. Add `RUN useradd -m appuser` and `USER appuser` before `CMD`. |
| 2 | 🔴 HIGH | **AI model downloaded at build time as root** | `RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"` downloads a ~400MB model into `/root/.cache` which won't be accessible under a non-root user and bloats the image. |
| 3 | 🟡 MEDIUM | **`COPY . .` copies everything** | Copies `oauth_tokens.json`, `__pycache__/`, `data/` (with potential uploads) into the image. Needs a `.dockerignore`. |
| 4 | 🟡 MEDIUM | **Build tools (gcc, g++) not cleaned up** | `gcc` and `g++` are installed for building wheels but remain in the final image. Use a multi-stage build or remove them after pip install. |
| 5 | 🟡 MEDIUM | **Single gunicorn worker** | `CMD ["gunicorn", ... "-w", "1", ...]` — only 1 worker. Cloud Run allows concurrency 40 in cloudbuild.yaml but a single worker can't handle that. Should be 2-4 workers or use auto-tuning. |
| 6 | 🟢 LOW | **600s timeout is excessive** | `--timeout 600` (10 min) is very long for an HTTP request handler. Could mask hung processes. |

### 3. docker-compose.yml

**File:** `docker-compose.yml`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🔴 HIGH | **Default password in compose** | `POSTGRES_PASSWORD: ${DB_PASSWORD:-changeme}` — insecure default. Same for `SECRET_KEY: ${SECRET_KEY:-dev-secret-key-change-in-production}`. |
| 2 | 🔴 HIGH | **Postgres port exposed to host** | `ports: - "5432:5432"` exposes the database to the network. Should be internal-only (remove ports or use `expose`). |
| 3 | 🔴 HIGH | **Redis port exposed to host** | `ports: - "6379:6379"` — Redis has no authentication configured and is publicly accessible. |
| 4 | 🟡 MEDIUM | **Source code mounted as volume** | `volumes: - ./backend:/app` in the backend service mounts source code into the container, overriding the image contents. This is fine for dev but dangerous if used in production. |
| 5 | 🟡 MEDIUM | **AI model cache mounted as root** | `ai_models:/root/.cache/huggingface` — uses root path, couples to running as root in container. |
| 6 | 🟡 MEDIUM | **Frontend VITE_API_URL is runtime env** | `VITE_API_URL: ${VITE_API_URL:-http://localhost:8000}` is set as a runtime env var, but Vite bakes env vars at **build time**. This env var has no effect at runtime. |
| 7 | 🟢 LOW | **Frontend `depends_on` backend but no healthcheck** | Frontend depends on backend but doesn't wait for backend health. Should use `condition: service_healthy`. |
| 8 | 🟢 LOW | **Missing `GOOGLE_CLOUD_PROJECT` and `GEMINI_MODEL` env vars** | The backend uses Gemini in production but these env vars aren't in docker-compose. |

### 4. nginx.conf

**File:** `nginx.conf`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🟡 MEDIUM | **Missing CSP header** | No `Content-Security-Policy` header. Should add a restrictive CSP to prevent XSS. |
| 2 | 🟡 MEDIUM | **Missing HSTS header** | No `Strict-Transport-Security` header for HTTPS enforcement. |
| 3 | 🟡 MEDIUM | **`X-XSS-Protection` is deprecated** | Modern browsers no longer support `X-XSS-Protection`. Remove it and use CSP instead. |
| 4 | 🟡 MEDIUM | **Cache-Control on `location /` overrides static assets** | The `add_header Cache-Control "no-cache"` in `location /` applies to all responses first, then the static asset regex block overrides for those files. However, the `add_header` directive in nginx replaces parent-block headers — this actually works correctly but is fragile (a change in block order could break it). |
| 5 | 🟢 LOW | **Dual listen (80 + 3000)** | Listening on both 80 and 3000 is redundant for production. |
| 6 | ✅ | **Gzip configured properly** | Good gzip configuration with appropriate types. |
| 7 | ✅ | **SPA routing correct** | `try_files $uri $uri/ /index.html` is correct for SPAs. |

### 5. cloudbuild.yaml

**File:** `cloudbuild.yaml`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🟡 MEDIUM | **`_BACKEND_URL` substitution is broken** | `_BACKEND_URL: 'https://recruitment-backend-${PROJECT_ID}.run.app'` — GCB substitutions can't reference `${PROJECT_ID}` inside another substitution default value. This will produce a literal `${PROJECT_ID}` string. Must use a shell expansion in the step or pass it differently. |
| 2 | 🟡 MEDIUM | **Hardcoded env vars in deploy step** | `GEMINI_MODEL=gemini-2.0-flash-lite`, `GCS_BUCKET_NAME=efforts-recruitment-data` etc. are hardcoded directly. Should be substitution variables or Secret Manager values. |
| 3 | 🟡 MEDIUM | **Backend min-instances=1 costs money 24/7** | `--min-instances 1` keeps one instance always warm. Combined with `--no-cpu-throttling`, this keeps CPU allocated permanently. Large cost implication (~$50-100/mo). |
| 4 | 🟢 LOW | **Backend/frontend build not parallelized** | Steps `build-backend` and `build-frontend` could run in parallel with `waitFor: ['-']` but currently run sequentially (default). |
| 5 | 🟢 LOW | **`latest` tag pushed but not used in deploy** | The `:latest` tags are built and pushed but never referenced. The deploy uses `${SHORT_SHA}`. The latest push adds build time for no benefit. |
| 6 | ✅ | **Secrets via Secret Manager** | Properly uses `--update-secrets` for sensitive values. |

### 6. firebase.json

**File:** `firebase.json`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | ✅ | **Caching headers correct** | Proper no-cache for HTML, long-cache for static assets with `immutable`. |
| 2 | ✅ | **SPA rewrite correct** | `"source": "**"` → `/index.html` is correct. |
| 3 | 🟢 LOW | **No security headers** | Unlike nginx.conf, firebase.json doesn't add security headers (X-Frame-Options, CSP, etc.). Firebase Hosting supports custom headers — should add them. |
| 4 | 🟢 LOW | **No API proxy/redirect** | No rewrite rule to proxy `/api/*` to the Cloud Run backend. Frontend must use full URLs. |

### 7. vite.config.ts

**File:** `vite.config.ts`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | ✅ | **Dev proxy is safe** | `server.proxy` only applies in dev mode (`vite dev`). Does not affect production builds. |
| 2 | ✅ | **Manual chunks are good** | Proper vendor splitting for React, UI, charts, utils. |
| 3 | 🟢 LOW | **`framer-motion` not in a chunk** | `framer-motion` is in dependencies (~50KB) but not assigned to a manual chunk. Will land in the main bundle. |
| 4 | 🟢 LOW | **No `build.target` specified** | Will default to Vite's default (`modules`). Consider setting explicitly for browser compatibility. |

### 8. tsconfig.json

**File:** `tsconfig.json`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | ✅ | **`strict: true` enabled** | Full strict mode is on. |
| 2 | ✅ | **Unused checks enabled** | `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch` all enabled. |
| 3 | 🟢 LOW | **Missing `forceConsistentCasingInFileNames`** | Should be `true` to prevent cross-platform issues with case-sensitive imports. |
| 4 | 🟢 LOW | **Missing `exactOptionalPropertyTypes`** | Consider enabling for stricter optional property handling. |
| 5 | 🟢 LOW | **`target: ES2020`** | Could use `ES2022` or later given the project targets modern browsers (Vite default is ESNext). |

### 9. package.json

**File:** `package.json`

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🟡 MEDIUM | **Outdated dependencies** | All deps are from Jan 2024 (~2 years old). Notable: `react@^18.2.0` (React 19 is out), `vite@^5.0.11` (Vite 6+ is out), `eslint@^8.56.0` (ESLint 9+ is out). |
| 2 | 🟡 MEDIUM | **No `test` script** | No testing script defined. `npm test` will fail. |
| 3 | 🟡 MEDIUM | **No lockfile management** | No `engines` field to enforce Node.js version. |
| 4 | 🟢 LOW | **`lucide-react@^0.303.0` is very old** | Current is 0.400+. Major icon changes since 0.303. |
| 5 | 🟢 LOW | **Missing `type-check` script** | Only `build` runs `tsc`. Should have a standalone type-check script for CI. |
| 6 | 🟢 LOW | **`pdf-lib` and `jspdf` both included** | Two PDF generation libraries. Likely only one is needed — dead dependency? |
| 7 | ✅ | **`"type": "module"` set** | Correct for Vite/ESM. |

---

## PART 2: Dead Backend Services Analysis

### Methodology
For each service, I checked:
1. Direct imports in `main.py` (top-level)
2. Direct imports in `api/advanced_routes.py`
3. Transitive imports from other services that ARE used

### Results

| # | Service | Used in main.py? | Used in advanced_routes.py? | Transitive? | Verdict |
|---|---------|-------------------|-----------------------------|-------------|---------|
| 1 | `calendar_integration_service.py` | ❌ | ✅ Yes (line 44) | — | **ACTIVE** (via advanced routes) |
| 2 | `duplicate_detection_service.py` | ❌ | ✅ Yes (line 39) | — | **ACTIVE** (via advanced routes) |
| 3 | `email_templates_service.py` | ✅ Yes (line 42) | ✅ Yes (line 43) | — | **ACTIVE** |
| 4 | `followup_service.py` | ✅ Yes (line 40) | ✅ Yes (line 46) | — | **ACTIVE** |
| 5 | `job_matching_service.py` | ❌ | ✅ Yes (line 40) | — | **ACTIVE** (via advanced routes) |
| 6 | `job_taxonomy.py` | ❌ | ❌ | ✅ Used by `openai_service`, `llm_service`, `gemini_service`, `email_scraper` | **ACTIVE** (transitive) |
| 7 | `llm_service.py` | ❌ (lazy import line 1376) | ❌ | ✅ Used by `resume_parser`, `matching_engine`, `local_ai_service`, `email_scraper` | **ACTIVE** (transitive + lazy) |
| 8 | `local_ai_service.py` | ✅ Yes (line 27) | ❌ | — | **ACTIVE** |
| 9 | `matching_engine.py` | ✅ Yes (line 22) | ❌ | — | **ACTIVE** |
| 10 | `microsoft_graph.py` | ✅ Yes (line 24) | ❌ | Also used by `oauth_automation_service` | **ACTIVE** |
| 11 | `ml_ranking_service.py` | ❌ | ✅ Yes (line 37) | — | **ACTIVE** (via advanced routes) |
| 12 | `oauth_automation_service.py` | ✅ Yes (line 31) | ❌ | — | **ACTIVE** |
| 13 | `openai_service.py` | ✅ Yes (line 26) | ❌ | Also used by `llm_service` | **ACTIVE** |
| 14 | `predictive_analytics_service.py` | ❌ | ✅ Yes (line 41) | — | **ACTIVE** (via advanced routes) |
| 15 | `resume_quality_service.py` | ❌ | ✅ Yes (line 42) | — | **ACTIVE** (via advanced routes) |
| 16 | `skill_extraction_service.py` | ❌ | ✅ Yes (line 38) | — | **ACTIVE** (via advanced routes) |
| 17 | `sms_notification_service.py` | ✅ Yes (line 41) | ✅ Yes (line 45) | — | **ACTIVE** |
| 18 | `setup_service.py` | ✅ (lazy import line 1761) | ❌ | — | **ACTIVE** (lazy import) |
| 19 | `token_storage.py` | ✅ Yes (line 25) | ❌ | Also used by `oauth_automation_service` | **ACTIVE** |
| 20 | `db_repair.py` | ✅ Yes (line 33) | ❌ | — | **ACTIVE** |

### Summary

**All 20 services are actively used.** None are dead code.

- 8 services are imported directly in `main.py`
- 8 services are imported via `advanced_routes.py` (which is mounted as a router in `main.py`)
- 2 services (`job_taxonomy.py`, `llm_service.py`) are used transitively by other active services
- 2 services (`setup_service.py`, `llm_service.py` in main.py) use lazy/deferred imports inside functions

> **However**, the question is whether the *endpoints* that consume these services are actually called by the frontend. Many advanced route endpoints (ML ranking, skill extraction, predictive analytics, calendar integration, SMS) may never be called by the current frontend UI — they represent **feature bloat** even if the code isn't technically dead.

---

## PART 3: Dead Frontend Components

### Methodology
Searched all files in `src/` for imports of each component.

| # | Component | Imported Anywhere? | Verdict |
|---|-----------|-------------------|---------|
| 1 | `AnalyticsDashboard.tsx` | ❌ Not imported by any file | **DEAD CODE** |
| 2 | `CampaignManager.tsx` | ❌ Not imported by any file | **DEAD CODE** |
| 3 | `EmailIntegration.tsx` | ❌ Not imported by any file | **DEAD CODE** |
| 4 | `TemplatesManager.tsx` | ❌ Not imported by any file | **DEAD CODE** |
| 5 | `CandidateAIInsights.tsx` | ❌ Not imported by any file | **DEAD CODE** |

### Details

- **`AnalyticsDashboard.tsx`** (325 lines) — Full analytics dashboard with charts for pipeline metrics, source effectiveness, time-to-hire. Never rendered.
- **`CampaignManager.tsx`** (570 lines) — Campaign CRUD UI with enrollment, stats, and step management. Never rendered.
- **`EmailIntegration.tsx`** (43+ lines) — Email provider connection UI. The route `/email-integration` in App.tsx redirects to `/setup` instead of rendering this component.
- **`TemplatesManager.tsx`** (431 lines) — Email template CRUD with variable rendering. Never rendered.
- **`CandidateAIInsights.tsx`** (624 lines) — AI-powered candidate analysis panel with scoring. Never rendered.

**Total dead frontend code: ~1,993 lines across 5 components.**

These components likely correspond to backend `advanced_routes.py` endpoints (campaigns, templates, analytics, ML ranking) that are also unreachable from the frontend.

---

## PART 4: Browser Extension Audit

### 4.1 manifest.json

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🔴 HIGH | **`http://localhost:8000/*` in host_permissions** | Production extension requests permission to access localhost. Should use the actual production API URL or make it configurable. |
| 2 | 🟡 MEDIUM | **No production API URL in host_permissions** | The extension can only connect to localhost out-of-the-box. Users who configure a different API URL will get CORS/permission errors because the host isn't whitelisted. |
| 3 | ✅ | **Manifest V3 compliant** | Uses `service_worker` for background, correct permission model. |
| 4 | ✅ | **Minimal permissions** | Only `activeTab`, `storage`, `tabs` — no over-requesting. |

### 4.2 background.js

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🔴 HIGH | **Hardcoded `API_BASE_URL = 'http://localhost:8000'`** | Line 3: The default URL is localhost. On install, it seeds this into `chrome.storage.sync` (line 228). Users must manually change it in settings before the extension works with a deployed backend. |
| 2 | 🟡 MEDIUM | **No authentication headers** | `sendProfilesToBackend()` sends requests without any auth token. The backend's `/api/candidates/linkedin` endpoint (if protected by `require_auth`) will reject these requests. |
| 3 | 🟡 MEDIUM | **Profiles stored in-memory** | `let scrapedProfiles = []` is volatile — lost on service worker restart (MV3 service workers are ephemeral). The code does persist to `chrome.storage.local` but the in-memory array can desync. |
| 4 | 🟡 MEDIUM | **No rate limiting** | Rapid clicks on "Send All" could fire many concurrent requests to the backend with no throttling. |
| 5 | 🟢 LOW | **Fake email generation** | `linkedin_${slug}@import.linkedin` is generated for profiles without emails. Downstream systems might treat this as a real email. |

### 4.3 content.js

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🟡 MEDIUM | **Brittle CSS selectors** | Scraping relies on LinkedIn-specific class names (`.pv-top-card`, `.pvs-list__paged-list-item`, `.text-heading-xlarge`). LinkedIn regularly changes these class names — the scraper will silently return empty data when LinkedIn updates its UI. |
| 2 | 🟡 MEDIUM | **No error reporting to user** | If scraping fails (selectors changed), the profile object returns with empty fields but no visible error in the popup. User thinks it worked. |
| 3 | 🟢 LOW | **1-second hardcoded wait** | `await wait(1000)` after detecting `.pv-top-card`. On slow connections, dynamic content may not have loaded yet. |
| 4 | ✅ | **Re-injection prevention** | `window.linkedInScraperInitialized` prevents duplicate injections. |
| 5 | ✅ | **Comprehensive scraping** | Covers name, headline, location, about, experience, education, skills, certifications, languages. |

### 4.4 popup.js

| # | Severity | Issue | Details |
|---|----------|-------|---------|
| 1 | 🟢 LOW | **XSS risk in profile rendering** | `profile.name`, `profile.headline` are interpolated directly into innerHTML via template literals without sanitization. A malicious LinkedIn profile name containing `<script>` tags could execute code. |
| 2 | 🟢 LOW | **No pagination** | All 50 stored profiles render at once in the popup. Could become slow. |
| 3 | ✅ | **Settings persistence** | Properly saves/loads from `chrome.storage.sync`. |
| 4 | ✅ | **Connection testing** | Users can test API connectivity before sending data. |

### 4.5 Functionality Completeness

| Feature | Status |
|---------|--------|
| Scrape LinkedIn profiles | ✅ Works |
| Store profiles locally | ✅ Works |
| Send to backend | ⚠️ Works only with localhost or if user manually configures URL |
| Auto-send on scrape | ✅ Implemented |
| Configurable API URL | ✅ Implemented |
| Authentication | ❌ Missing |
| Bulk operations | ✅ Send all profiles |
| Error handling | ⚠️ Basic — silent failures on scrape |
| Production-ready | ❌ No — hardcoded localhost, no auth, no production host_permissions |

---

## Executive Summary

### Critical Issues (must fix)

1. **Backend Dockerfile runs as root** — container compromise = full root access
2. **Hardcoded default passwords** in docker-compose.yml (`changeme`, `dev-secret-key`)
3. **Database and Redis ports exposed** to host network without authentication
4. **Browser extension hardcoded to localhost** — unusable in production without manual config
5. **Frontend Dockerfile leaks production URL** in build arg default

### Medium Priority

6. Missing security headers (CSP, HSTS) in nginx.conf and firebase.json
7. Browser extension sends requests without authentication
8. `_BACKEND_URL` substitution in cloudbuild.yaml is broken
9. 5 dead frontend components (~2,000 lines of unreachable code)
10. Single gunicorn worker can't handle configured concurrency of 40
11. Docker images include build tools (gcc, g++) unnecessarily
12. All npm dependencies are ~2 years outdated

### Low Priority

13. Duplicate PDF libraries (`pdf-lib` + `jspdf`)
14. Missing `forceConsistentCasingInFileNames` in tsconfig
15. `framer-motion` not in a manual chunk
16. Firebase.json missing API proxy rules
17. No `test` or `type-check` npm scripts
