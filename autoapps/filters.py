"""Filtering and ranking of the job list."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .models import Job, Profile
from .sources.common import is_internship

NO_SPONSOR_RE = re.compile(r"does not offer sponsorship|citizenship is required|no sponsorship|u\.?s\.? citizen", re.I)


def _terms(raw: str) -> list[str]:
    return [t.strip().lower() for t in re.split(r"[,\n]", raw or "") if t.strip()]


class JobFilter(BaseModel):
    keywords: str = ""  # all must match title/company/description; prefix with - to exclude
    title: str = ""  # comma-separated; any may match the title
    location: str = ""  # comma-separated; any may match
    remote_only: bool = False
    companies: str = ""  # comma-separated include list
    exclude_companies: str = ""
    sources: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=lambda: ["new", "shortlisted"])
    posted_within_days: Optional[int] = None
    internships_only: bool = True
    hide_no_sponsorship: bool = False
    min_score: int = 0
    sort: Literal["posted", "score", "company"] = "posted"

    def matches(self, job: Job, score: int = 0) -> bool:
        title = job.title.lower()
        company = job.company.lower()
        haystack = f"{title} {company} {job.description.lower()} {job.location.lower()}"

        for kw in self.keywords.split():
            kw = kw.lower()
            if kw.startswith("-") and len(kw) > 1:
                if kw[1:] in haystack:
                    return False
            elif kw not in haystack:
                return False
        titles = _terms(self.title)
        if titles and not any(t in title for t in titles):
            return False
        locs = _terms(self.location)
        if locs and not any(l in job.location.lower() for l in locs) and not (job.remote and "remote" in locs):
            return False
        if self.remote_only and not job.remote:
            return False
        companies = _terms(self.companies)
        if companies and not any(c in company for c in companies):
            return False
        if any(c in company for c in _terms(self.exclude_companies)):
            return False
        if self.sources and job.source not in self.sources:
            return False
        if self.statuses and job.status not in self.statuses:
            return False
        if self.posted_within_days and job.posted_at:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.posted_within_days)
            if datetime.fromisoformat(job.posted_at) < cutoff:
                return False
        if self.internships_only and job.source != "simplify" and not is_internship(job.title, " ".join(job.terms)):
            return False
        if self.hide_no_sponsorship and NO_SPONSOR_RE.search(f"{job.sponsorship} {job.description}"):
            return False
        if score < self.min_score:
            return False
        return True


def relevance(job: Job, profile: Profile) -> int:
    """0-100 score: share of your listed skills that the posting mentions."""
    skills = [s.lower().strip() for s in profile.skills if s.strip()]
    if not skills:
        return 0
    text = f"{job.title} {job.description}".lower()
    hits = 0
    for skill in skills:
        pattern = r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            hits += 1
    # Most postings mention a handful of skills; scale so ~1/3 coverage reads as a strong match.
    return min(100, round(300 * hits / len(skills)))


def apply_filter(jobs: list[Job], flt: JobFilter, profile: Profile) -> list[tuple[Job, int]]:
    scored = [(j, relevance(j, profile)) for j in jobs]
    out = [(j, s) for j, s in scored if flt.matches(j, s)]
    if flt.sort == "score":
        out.sort(key=lambda js: (js[1], js[0].posted_at or ""), reverse=True)
    elif flt.sort == "company":
        out.sort(key=lambda js: (js[0].company.lower(), js[0].title.lower()))
    else:
        out.sort(key=lambda js: js[0].posted_at or "", reverse=True)
    return out
