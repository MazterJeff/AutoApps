"""Lever public Postings API: https://github.com/lever/postings-api"""

from __future__ import annotations

import requests

from ..models import Job
from .common import REMOTE_RE, canonical_apply_url, html_to_text, is_internship, iso_from_epoch

API = "https://api.lever.co/v0/postings/{board}?mode=json"


def fetch(board: str, sess: requests.Session, internships_only: bool = True) -> list[Job]:
    resp = sess.get(API.format(board=board), timeout=30)
    resp.raise_for_status()
    return parse(board, resp.json(), internships_only)


def parse(board: str, payload: list, internships_only: bool = True) -> list[Job]:
    jobs = []
    for item in payload or []:
        title = item.get("text", "").strip()
        cats = item.get("categories") or {}
        commitment = cats.get("commitment") or ""
        if internships_only and not is_internship(title, commitment):
            continue
        job_id = item.get("id", "")
        locations = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        location = "; ".join(locations)
        parts = [item.get("descriptionPlain") or html_to_text(item.get("description", ""))]
        for section in item.get("lists") or []:
            parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content', ''))}")
        if item.get("additionalPlain"):
            parts.append(item["additionalPlain"])
        jobs.append(
            Job(
                id=f"lever:{board}:{job_id}",
                source="lever",
                ats="lever",
                company=board.replace("-", " ").title(),
                title=title,
                location=location,
                remote=item.get("workplaceType") == "remote" or bool(REMOTE_RE.search(location)),
                url=item.get("hostedUrl", ""),
                apply_url=canonical_apply_url("lever", board, job_id, item.get("applyUrl", "")),
                description="\n\n".join(p for p in parts if p).strip(),
                posted_at=iso_from_epoch(item.get("createdAt")),
                ats_board=board,
                ats_job_id=job_id,
                terms=[commitment] if commitment else [],
            )
        )
    return jobs
