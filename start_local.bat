@echo off
setlocal enabledelayedexpansion
REM ─── Efforts Recruitment Tool – Fully Local Startup ────────────────
REM Starts everything on your machine:
REM   1. PostgreSQL (Docker container)
REM   2. Ollama (local LLM)
REM   3. FastAPI backend (uvicorn)
REM
REM Prerequisites:
REM   - Docker Desktop installed and running
REM   - Ollama installed (https://ollama.com) with model:  ollama pull qwen2.5:14b
REM   - Python 3.11+ with deps:  cd backend && pip install -r requirements.txt
REM
REM After startup, expose to Vercel via:
REM   cloudflared tunnel run maahir-api

cd /d "%~dp0"
echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║      AI Recruitment Platform — Local Startup            ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

REM ── Step 1: Check Docker is running ────────────────────────────────
echo [1/4] Checking Docker...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker is not running. Please start Docker Desktop and try again.
    pause
    exit /b 1
)
echo       Docker is running.

REM ── Step 2: Start PostgreSQL container ─────────────────────────────
echo [2/4] Starting PostgreSQL...
docker compose -f docker-compose.local.yml up -d postgres 2>nul
if %errorlevel% neq 0 (
    docker-compose -f docker-compose.local.yml up -d postgres 2>nul
)

REM Wait for PostgreSQL to be healthy
echo       Waiting for PostgreSQL to be ready...
set /a attempts=0
:pg_wait
set /a attempts+=1
if %attempts% gtr 30 (
    echo [ERROR] PostgreSQL failed to start after 30 seconds.
    pause
    exit /b 1
)
docker exec recruitment-db pg_isready -U recruiter -d ai_recruiter >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 1 /nobreak >nul
    goto pg_wait
)
echo       PostgreSQL ready on localhost:5432.

REM ── Step 3: Check/start Ollama ─────────────────────────────────────
echo [3/4] Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo       Starting Ollama...
    start "" ollama serve
    timeout /t 4 /nobreak >nul
    curl -s http://localhost:11434/api/tags >nul 2>&1
    if %errorlevel% neq 0 (
        echo       [WARN] Ollama not reachable. AI will fall back to keyword matching.
    ) else (
        echo       Ollama is running on localhost:11434.
    )
) else (
    echo       Ollama already running.
)

REM ── Step 4: Prepare backend/.env if missing ────────────────────────
cd /d "%~dp0\backend"
if not exist ".env" (
    echo.
    echo [SETUP] No backend\.env found. Creating from .env.example...
    copy /Y ".env.example" ".env" >nul
    echo       Created backend\.env — edit JWT_SECRET_KEY and ADMIN_PASSWORD before production use.
    echo.
)

REM ── Faster startup: skip HuggingFace network calls ────────────────
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1

REM ── Start backend ──────────────────────────────────────────────────
echo [4/4] Starting backend...
echo.
echo ══════════════════════════════════════════════════════════════
echo   Backend:    http://localhost:8000
echo   API Docs:   http://localhost:8000/api/docs
echo   Health:     http://localhost:8000/health
echo   PostgreSQL: localhost:5432  (ai_recruiter)
echo   Ollama:     localhost:11434
echo.
echo   Frontend:   https://maheer.tech  (Vercel)
echo   To expose:  cloudflared tunnel run maheer-api
echo ══════════════════════════════════════════════════════════════
echo.
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
