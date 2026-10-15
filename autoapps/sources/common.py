"""Helpers shared by job sources."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; AutoApps/0.1; personal internship tracker)"
TIMEOUT = 30

INTERN_RE = re.compile(r"\b(intern|interns|internship|co-?op|summer (?:analyst|associate|engineer)|apprentice)\b", re.I)
REMOTE_RE = re.compile(r"\bremote\b", re.I)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"})
    return s


def is_internship(title: str, extra: str = "") -> bool:
    return bool(INTERN_RE.search(title) or INTERN_RE.search(extra))


def html_to_text(raw: str) -> str:
    """Convert (possibly entity-escaped) HTML to readable plain text."""
    if not raw:
        return ""
    raw = html.unescape(raw)
    soup = BeautifulSoup(raw, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)
    return text.strip()


def iso_from_epoch(value) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return iso_from_string(str(value))
    if v > 1e12:  # milliseconds
        v /= 1000
    return datetime.fromtimestamp(v, tz=timezone.utc).replace(microsecond=0).isoformat()


def iso_from_string(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def detect_ats(url: str) -> tuple[str, str, str]:
    """Return (ats, board, job_id) for a posting URL, or ("other", "", "")."""
    if not url:
        return "other", "", ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [p for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)

    if "greenhouse.io" in host:
        if "embed" in parts and query.get("for") and query.get("token"):
            return "greenhouse", query["for"][0], query["token"][0]
        if len(parts) >= 3 and parts[1] == "jobs":
            return "greenhouse", parts[0], parts[2]
    if host.endswith("lever.co") and len(parts) >= 2:
        return "lever", parts[0], parts[1]
    if host.endswith("ashbyhq.com") and len(parts) >= 2:
        return "ashby", parts[0], parts[1]
    return "other", "", ""


def canonical_apply_url(ats: str, board: str, job_id: str, fallback: str) -> str:
    if ats == "greenhouse" and board and job_id:
        return f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    if ats == "lever" and board and job_id:
        return f"https://jobs.lever.co/{board}/{job_id}/apply"
    if ats == "ashby" and board and job_id:
        return f"https://jobs.ashbyhq.com/{board}/{job_id}/application"
    return fallback
