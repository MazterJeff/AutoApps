"""SimplifyJobs community internship lists (github.com/SimplifyJobs).

These repos publish a machine-readable ``listings.json`` alongside the README table.
Each listing links to the company's own application page, which we classify by ATS.
"""

from __future__ import annotations

import hashlib

import requests

from ..models import Job
from .common import REMOTE_RE, canonical_apply_url, detect_ats, iso_from_epoch

RAW = "https://raw.githubusercontent.com/{repo}/{branch}/.github/scripts/listings.json"
BRANCHES = ("dev", "main")


def fetch(repo: str, sess: requests.Session, internships_only: bool = True) -> list[Job]:
    last_error: Exception | None = None
    for branch in BRANCHES:
        resp = sess.get(RAW.format(repo=repo, branch=branch), timeout=60)
        if resp.status_code == 404:
            last_error = requests.HTTPError(f"404 for {resp.url}")
            continue
        resp.raise_for_status()
        return parse(repo, resp.json())
    raise last_error or RuntimeError(f"Could not load listings for {repo}")


def parse(repo: str, payload: list) -> list[Job]:
    jobs = []
    for item in payload or []:
        if not item.get("active", True) or not item.get("is_visible", True):
            continue
        url = item.get("url", "")
        if not url:
            continue
        ats, board, ats_job_id = detect_ats(url)
        if ats != "other":
            # Same id scheme as the direct board fetchers so listings merge instead of duplicating.
            job_key = f"{ats}:{board}:{ats_job_id}"
        else:
            job_key = f"simplify:{item.get('id') or hashlib.sha1(url.encode()).hexdigest()[:16]}"
        locations = item.get("locations") or []
        location = "; ".join(locations)
        jobs.append(
            Job(
                id=job_key,
                source="simplify",
                ats=ats,
                company=item.get("company_name", "").strip() or "Unknown",
                title=item.get("title", "").strip(),
                location=location,
                remote=bool(REMOTE_RE.search(location)),
                url=url,
                apply_url=canonical_apply_url(ats, board, ats_job_id, url),
                description="",
                posted_at=iso_from_epoch(item.get("date_posted")),
                ats_board=board,
                ats_job_id=ats_job_id,
                sponsorship=item.get("sponsorship", "") or "",
                terms=list(item.get("terms") or []),
            )
        )
    return jobs
