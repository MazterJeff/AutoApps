"""Job sources and the configuration that says which boards to pull from."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..models import Job
from . import ashby, greenhouse, lever, simplify
from .common import session

log = logging.getLogger(__name__)

FETCHERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "simplify": simplify.fetch,
}


class SourceConfig(BaseModel):
    """Board identifiers per ATS. Edit these on the Sources page.

    A board id is the slug in the company's job-board URL, e.g. ``stripe`` in
    ``job-boards.greenhouse.io/stripe``. The defaults are a starting point; boards that
    move or rename are reported as errors on refresh and can be removed.
    """

    greenhouse: list[str] = Field(default_factory=lambda: [
        "stripe", "airbnb", "robinhood", "databricks", "figma", "discord", "coinbase",
        "cloudflare", "datadog", "asana", "duolingo", "roblox", "reddit", "pinterest",
        "dropbox", "lyft", "instacart", "gitlab", "brex", "affirm",
    ])
    lever: list[str] = Field(default_factory=lambda: ["palantir", "spotify"])
    ashby: list[str] = Field(default_factory=lambda: ["openai", "ramp", "notion", "linear", "replit"])
    # GitHub "owner/repo" of SimplifyJobs-style lists. Add e.g. "SimplifyJobs/New-Grad-Positions".
    simplify: list[str] = Field(default_factory=lambda: ["SimplifyJobs/Summer2027-Internships"])
    internships_only: bool = True


@dataclass
class RefreshResult:
    jobs: list[Job] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def fetch_all(config: SourceConfig, max_workers: int = 8) -> RefreshResult:
    tasks = [(kind, board) for kind in FETCHERS for board in getattr(config, kind)]
    result = RefreshResult()
    sess = session()

    def run(task):
        kind, board = task
        try:
            return task, FETCHERS[kind](board, sess, config.internships_only), None
        except Exception as exc:  # network errors, renamed boards, bad JSON
            return task, [], exc

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for (kind, board), jobs, err in pool.map(run, tasks):
            label = f"{kind}:{board}"
            if err is not None:
                log.warning("source %s failed: %s", label, err)
                result.errors.append(f"{label}: {err}")
                continue
            result.counts[label] = len(jobs)
            result.jobs.extend(jobs)
    return result
