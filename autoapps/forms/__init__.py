"""Discover the questions on a job's application form."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

from ..models import FormField, Job
from ..sources.common import html_to_text, session
from . import greenhouse, html_form

log = logging.getLogger(__name__)

LEVER_POSTING = "https://api.lever.co/v0/postings/{board}/{job_id}?mode=json"

# Used when a form can't be read ahead of time; the filler matches these by label on the page.
DEFAULT_FIELDS = [
    FormField(key="first_name", label="First Name", type="text", required=True),
    FormField(key="last_name", label="Last Name", type="text", required=True),
    FormField(key="email", label="Email", type="email", required=True),
    FormField(key="phone", label="Phone", type="tel", required=True),
    FormField(key="resume", label="Resume/CV", type="file", required=True),
    FormField(key="cover_letter", label="Cover Letter", type="file"),
    FormField(key="linkedin", label="LinkedIn Profile", type="url"),
    FormField(key="website", label="Website", type="url"),
]


@dataclass
class FormResult:
    fields: list[FormField]
    description: str = ""
    notes: list[str] = field(default_factory=list)


def load_form(job: Job, sess: requests.Session | None = None, use_browser: bool = True) -> FormResult:
    sess = sess or session()
    notes: list[str] = []
    fields: list[FormField] = []
    description = ""

    try:
        if job.ats == "greenhouse" and job.ats_board and job.ats_job_id:
            fields, description = greenhouse.fetch(job.ats_board, job.ats_job_id, sess)
            notes.append(f"Read {len(fields)} fields from the Greenhouse API.")
        elif job.ats == "lever" and job.ats_board and job.ats_job_id:
            resp = sess.get(job.apply_url, timeout=30)
            resp.raise_for_status()
            fields = html_form.parse(resp.text)
            notes.append(f"Read {len(fields)} fields from the Lever application page.")
            if not job.description:
                description = _lever_description(job, sess)
    except Exception as exc:
        notes.append(f"Couldn't read the form directly ({exc}).")
        fields = []

    if not fields and use_browser and job.apply_url:
        try:
            from ..browser import scrape_form

            fields, page_text = scrape_form(job.apply_url)
            notes.append(f"Read {len(fields)} fields by rendering the page in a browser.")
            if not job.description and page_text:
                description = page_text
        except Exception as exc:
            notes.append(f"Browser read failed ({exc}).")

    if not fields and job.apply_url:
        try:
            resp = sess.get(job.apply_url, timeout=30)
            if resp.ok:
                fields = html_form.parse(resp.text)
                if not job.description:
                    description = html_to_text(resp.text)[:20000]
                if fields:
                    notes.append(f"Read {len(fields)} fields from the page HTML.")
        except Exception as exc:
            notes.append(f"Fetching the page failed ({exc}).")

    if not fields:
        fields = [f.model_copy(deep=True) for f in DEFAULT_FIELDS]
        notes.append(
            "Couldn't read this form, so the usual fields are pre-filled. The filler matches them by "
            "label on the page; check the browser window for anything else it asks."
        )
    return FormResult(fields=fields, description=description, notes=notes)


def _lever_description(job: Job, sess: requests.Session) -> str:
    try:
        resp = sess.get(LEVER_POSTING.format(board=job.ats_board, job_id=job.ats_job_id), timeout=30)
        resp.raise_for_status()
        item = resp.json()
    except Exception:
        return ""
    parts = [item.get("descriptionPlain", "")]
    for section in item.get("lists") or []:
        parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content', ''))}")
    parts.append(item.get("additionalPlain", ""))
    return "\n\n".join(p for p in parts if p).strip()
