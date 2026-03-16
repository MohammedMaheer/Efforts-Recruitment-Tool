"""
AI Recruitment Platform — Modular Entry Point
==============================================
Refactored from 10,037-line monolith (main_legacy.py) into modular route files.

Route modules (backend/api/):
  auth_routes.py       — Login, register, profile, password  (/api/auth/*, /api/users/*)
  admin_routes.py      — Admin tools                         (/api/admin/*)
  ai_routes.py         — AI analysis, search, chat           (/api/ai/*, /api/candidates/*/rescore)
  advanced_routes.py   — Advanced AI services (existing)     (/api/advanced/*)
  candidates_routes.py — Candidate CRUD                      (/api/candidates/*)
  email_routes.py      — Email/OAuth flows                   (/api/email/*, /api/oauth/*, /api/cron/*)
  job_routes.py        — JD generation, taxonomy, matching   (/api/jd/*, /api/taxonomy/*, /api/matching/*)
  settings_routes.py   — Health, setup, stats, scraper       (/, /health, /version, /api/setup/*, ...)
  shortlist_routes.py  — Shortlisting, bulk actions, emails  (/api/candidates/*/status, /api/audit/*)
  upload_routes.py     — Resume upload                       (/api/resumes/*)

Infrastructure (backend/core/):
  lifespan.py  — GCS persistence, background tasks, startup/shutdown
  config.py    — Centralized settings
  deps.py      — Shared service singletons (initialized once at startup)
"""
import os
import time
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from core.config import get_settings
from core.lifespan import lifespan

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_settings = get_settings()
DEBUG = _settings.debug if not _settings.is_production else False

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title=_settings.app_name,
    description="Optimized recruitment platform with email scraping, AI job matching, ML ranking, and automated campaigns",
    version=_settings.app_version,
    docs_url="/api/docs" if DEBUG else None,
    redoc_url="/api/redoc" if DEBUG else None,
    lifespan=lifespan,
)

# ── Exception handlers ────────────────────────────────────────────────────────
from fastapi import HTTPException

@app.exception_handler(HTTPException)
async def sanitized_http_exception_handler(request, exc: HTTPException):
    """Sanitize error messages to prevent internal info leakage in production."""
    detail = exc.detail
    if not DEBUG and exc.status_code >= 500:
        logger.error(f"HTTP {exc.status_code} on {request.url.path}: {detail}")
        detail = "An internal error occurred. Please try again later."
    headers = dict(exc.headers) if exc.headers else {}
    # Add CORS headers for exception responses (WWW-Authenticate for 401/403)
    origin = request.headers.get("origin", "")
    if origin and origin in [o.strip() for o in _cors_origins_raw.split(',') if o.strip()]:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    if exc.status_code in (401, 403):
        headers["WWW-Authenticate"] = 'Bearer realm="auth"'
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": detail},
        headers=headers,
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    """Catch unhandled exceptions — never leak stack traces."""
    logger.error(f"Unhandled exception on {request.url.path}: {type(exc).__name__}: {str(exc)}")
    detail = f"Internal server error: {type(exc).__name__}" if DEBUG else "An internal error occurred. Please try again later."
    headers = {}
    # Add CORS headers for exception responses
    origin = request.headers.get("origin", "")
    if origin and origin in [o.strip() for o in _cors_origins_raw.split(',') if o.strip()]:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(status_code=500, content={"detail": detail}, headers=headers)

# ── Routers ────────────────────────────────────────────────────────────────────
from api.auth_routes import router as auth_router, user_router
from api.admin_routes import router as admin_router
from api.ai_routes import router as ai_router
from api.advanced_routes import router as advanced_router
from api.analytics_routes import router as analytics_router  # Phase 2.3: Analytics endpoints
from api.candidates_routes import router as candidates_router
from api.email_routes import router as email_router
from api.job_routes import router as job_router
from api.settings_routes import router as settings_router
from api.shortlist_routes import router as shortlist_router
from api.upload_routes import router as upload_router

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(admin_router)
app.include_router(ai_router)
app.include_router(advanced_router)
app.include_router(analytics_router)  # Phase 2.3: Analytics endpoints
app.include_router(candidates_router)
app.include_router(email_router)
app.include_router(job_router)
app.include_router(settings_router)
app.include_router(shortlist_router)
app.include_router(upload_router)

# ── Middleware ─────────────────────────────────────────────────────────────────
# Core middleware (security headers, rate limiting, caching) — registered first
from core.middleware import setup_middleware
setup_middleware(app)

# CORS — registered last so it is the outermost layer
# (Starlette executes middleware in reverse registration order)
if _settings.is_production:
    _cors_origins_raw = os.getenv(
        'CORS_ORIGINS',
        'https://efforts-recruitment-ai.web.app,https://efforts-recruitment-ai.firebaseapp.com'
    )
    allowed_origins = [o.strip() for o in _cors_origins_raw.split(',') if o.strip()]
else:
    allowed_origins = [
        'http://localhost:3000',
        'http://localhost:3001',
        'http://localhost:5173',
        'http://localhost:5174',
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
    max_age=3600,
)
logger.info(f"CORS enabled for: {', '.join(allowed_origins)}")

@app.middleware("http")
async def add_performance_headers(request, call_next):
    """Add X-Process-Time header and disable browser caching for API routes."""
    start_time = time.time()
    try:
        response = await call_next(request)
    except RuntimeError as exc:
        if "No response returned" in str(exc):
            from starlette.responses import JSONResponse as _JR
            logger.error(f"Middleware: downstream handler failed for {request.url.path}")
            return _JR({"detail": "Bad Gateway"}, status_code=502)
        raise
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time * 1000, 2))
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
