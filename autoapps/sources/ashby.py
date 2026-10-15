"""Ashby public Job Posting API: https://developers.ashbyhq.com/docs/public-job-posting-api"""

from __future__ import annotations

import requests

from ..models import Job
from .common import canonical_apply_url, html_to_text, is_internship, iso_from_string

API = "https://api.ashbyhq.com/posting-api/job-board/{board}"


def fetch(board: str, sess: requests.Session, internships_only: bool = True) -> list[Job]:
    resp = sess.get(API.format(board=board), timeout=30)
    resp.raise_for_status()
    return parse(board, resp.json(), internships_only)


def parse(board: str, payload: dict, internships_only: bool = True) -> list[Job]:
    jobs = []
    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue
        title = item.get("title", "").strip()
        employment = item.get("employmentType") or ""
        if internships_only and not (employment.lower() == "intern" or is_internship(title)):
            continue
        job_id = item.get("id", "")
        locations = [item.get("location") or ""]
        for sec in item.get("secondaryLocations") or []:
            locations.append(sec.get("location", "") if isinstance(sec, dict) else str(sec))
        location = "; ".join(l for l in locations if l)
        jobs.append(
            Job(
                id=f"ashby:{board}:{job_id}",
                source="ashby",
                ats="ashby",
                company=board.replace("-", " ").title(),
                title=title,
                location=location,
                remote=bool(item.get("isRemote")) or item.get("workplaceType") == "Remote",
                url=item.get("jobUrl", ""),
                apply_url=canonical_apply_url("ashby", board, job_id, item.get("applyUrl", "")),
                description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml", "")),
                posted_at=iso_from_string(item.get("publishedAt")),
                ats_board=board,
                ats_job_id=job_id,
                terms=[employment] if employment else [],
            )
        )
    return jobs
