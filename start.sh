#!/usr/bin/env bash
# One-step launcher for macOS/Linux: sets up a virtualenv on first run, then starts AutoApps.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/playwright install chromium
fi
exec .venv/bin/python main.py "$@"
