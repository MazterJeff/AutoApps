"""Greenhouse public Job Board API: https://developers.greenhouse.io/job-board.html"""

from __future__ import annotations

import requests

from ..models import Job
from .common import REMOTE_RE, canonical_apply_url, html_to_text, is_internship, iso_from_string

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


def fetch(board: str, sess: requests.Session, internships_only: bool = True) -> list[Job]:
    resp = sess.get(API.format(board=board), timeout=30)
    resp.raise_for_status()
    return parse(board, resp.json(), internships_only)


def parse(board: str, payload: dict, internships_only: bool = True) -> list[Job]:
    jobs = []
    for item in payload.get("jobs", []):
        title = item.get("title", "").strip()
        if internships_only and not is_internship(title):
            continue
        job_id = str(item.get("id"))
        location = (item.get("location") or {}).get("name", "") or ""
        jobs.append(
            Job(
                id=f"greenhouse:{board}:{job_id}",
                source="greenhouse",
                ats="greenhouse",
                company=item.get("company_name") or board.replace("-", " ").title(),
                title=title,
                location=location,
                remote=bool(REMOTE_RE.search(location)),
                url=item.get("absolute_url", ""),
                apply_url=canonical_apply_url("greenhouse", board, job_id, item.get("absolute_url", "")),
                description=html_to_text(item.get("content", "")),
                posted_at=iso_from_string(item.get("first_published") or item.get("updated_at")),
                ats_board=board,
                ats_job_id=job_id,
            )
        )
    return jobs
