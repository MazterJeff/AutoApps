"""Start AutoApps: `python main.py` (same as `python -m autoapps`).

Opens the app in your browser at http://127.0.0.1:8000. Other commands:
    python main.py refresh   # pull jobs from all sources
    python main.py doctor    # check your setup
"""

from autoapps.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
