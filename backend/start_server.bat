@echo off
REM Local dev server — set HF_HUB_OFFLINE=1 to skip model update checks (faster startup)
REM Remove these if running for the first time and models haven't been downloaded yet
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
cd /d "%~dp0"
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
