@echo off
rem One-step launcher for Windows: sets up a virtualenv on first run, then starts AutoApps.
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
  .venv\Scripts\playwright install chromium
)
.venv\Scripts\python main.py %*
