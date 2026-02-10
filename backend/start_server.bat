@echo off
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
cd /d "%~dp0"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
