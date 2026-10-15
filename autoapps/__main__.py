"""Command line entry point: ``python -m autoapps [serve|refresh|doctor]``."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import Config


def cmd_serve(config: Config, args) -> int:
    import uvicorn

    from .web.app import create_app

    print(f"AutoApps running at {config.base_url}  (Ctrl+C to stop)")
    uvicorn.run(create_app(config=config), host=config.host, port=config.port, log_level="warning")
    return 0


def cmd_refresh(config: Config, args) -> int:
    from .pipeline import Service

    svc = Service(config, background=False)
    result = svc.refresh_jobs()
    for label, n in sorted(result.counts.items()):
        print(f"  {label:45} {n:5d}")
    for err in result.errors:
        print(f"  ERROR {err}", file=sys.stderr)
    print(f"{svc.refresh_status['new']} new job(s), {len(result.jobs)} fetched in total.")
    return 0


def cmd_doctor(config: Config, args) -> int:
    ok = True
    print(f"Data folder: {config.data_dir}")
    for mod, hint in [
        ("anthropic", "pip install anthropic"),
        ("playwright", "pip install playwright && playwright install chromium"),
        ("starlette", "pip install -r requirements.txt"),
        ("reportlab", "pip install reportlab"),
    ]:
        try:
            __import__(mod)
            print(f"  [ok]   {mod}")
        except ImportError:
            ok = False
            print(f"  [miss] {mod}  ->  {hint}")
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print("  [ok]   Claude credentials found in the environment")
    else:
        print("  [?]    ANTHROPIC_API_KEY is not set (fine if you used `ant auth login`)")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        print("  [ok]   Chromium launches")
    except Exception as exc:
        ok = False
        print(f"  [miss] Chromium: {str(exc).splitlines()[0]}  ->  playwright install chromium")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoapps", description="Find internships, draft applications with Claude, review, submit.")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "refresh", "doctor"])
    parser.add_argument("--port", type=int, help="port for the web UI (default 8000)")
    parser.add_argument("--data-dir", help="where the database and files live (default ./data)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.data_dir:
        os.environ["AUTOAPPS_DATA_DIR"] = args.data_dir
    if args.port:
        os.environ["AUTOAPPS_PORT"] = str(args.port)
    config = Config()
    config.ensure_dirs()
    return {"serve": cmd_serve, "refresh": cmd_refresh, "doctor": cmd_doctor}[args.command](config, args)


if __name__ == "__main__":
    raise SystemExit(main())
