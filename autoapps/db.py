"""SQLite persistence for jobs, applications and settings."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .models import Application, Job, Profile, SavedAnswer, Settings, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    posted_at TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # ----- settings -------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def get_profile(self) -> Profile:
        return Profile.model_validate(self.get_setting("profile", {}))

    def save_profile(self, profile: Profile) -> None:
        self.set_setting("profile", profile.model_dump())

    def get_settings(self) -> Settings:
        return Settings.model_validate(self.get_setting("settings", {}))

    def save_settings(self, settings: Settings) -> None:
        self.set_setting("settings", settings.model_dump())

    def get_answer_library(self) -> list[SavedAnswer]:
        return [SavedAnswer.model_validate(a) for a in self.get_setting("answer_library", [])]

    def save_answer_library(self, answers: Iterable[SavedAnswer]) -> None:
        self.set_setting("answer_library", [a.model_dump() for a in answers])

    # ----- jobs -----------------------------------------------------------
    def upsert_jobs(self, jobs: Iterable[Job]) -> tuple[int, int]:
        """Insert new jobs and refresh existing ones, keeping their status. Returns (new, updated)."""
        new = updated = 0
        with self._conn() as c:
            for job in jobs:
                row = c.execute("SELECT status, data FROM jobs WHERE id=?", (job.id,)).fetchone()
                if row:
                    old = json.loads(row["data"])
                    keep = {"status": row["status"], "first_seen": old.get("first_seen", job.first_seen)}
                    # Different sources know different things; don't blank out what we already have.
                    for attr in ("description", "sponsorship", "terms", "posted_at", "location"):
                        if not getattr(job, attr) and old.get(attr):
                            keep[attr] = old[attr]
                    job = job.model_copy(update=keep)
                    updated += 1
                else:
                    new += 1
                c.execute(
                    "INSERT OR REPLACE INTO jobs(id, source, company, title, status, posted_at, data) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (job.id, job.source, job.company, job.title, job.status, job.posted_at, job.model_dump_json()),
                )
        return new, updated

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._conn() as c:
            row = c.execute("SELECT status, data FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self, statuses: Optional[Iterable[str]] = None) -> list[Job]:
        sql, args = "SELECT status, data FROM jobs", []
        statuses = list(statuses or [])
        if statuses:
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            args = statuses
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [self._row_to_job(r) for r in rows]

    def set_job_status(self, job_ids: Iterable[str], status: str) -> None:
        with self._conn() as c:
            for jid in job_ids:
                row = c.execute("SELECT data FROM jobs WHERE id=?", (jid,)).fetchone()
                if not row:
                    continue
                data = json.loads(row["data"])
                data["status"] = status
                c.execute("UPDATE jobs SET status=?, data=? WHERE id=?", (status, json.dumps(data), jid))

    def job_counts(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        job = Job.model_validate_json(row["data"])
        job.status = row["status"]
        return job

    # ----- applications ---------------------------------------------------
    def create_application(self, job_id: str) -> Application:
        existing = self.get_application_for_job(job_id)
        if existing:
            return existing
        app = Application(job_id=job_id)
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO applications(job_id, status, data) VALUES(?, ?, ?)",
                (job_id, app.status, app.model_dump_json()),
            )
            app.id = int(cur.lastrowid)
            c.execute("UPDATE applications SET data=? WHERE id=?", (app.model_dump_json(), app.id))
        return app

    def save_application(self, app: Application) -> None:
        app.updated_at = now_iso()
        with self._conn() as c:
            c.execute(
                "UPDATE applications SET status=?, data=? WHERE id=?",
                (app.status, app.model_dump_json(), app.id),
            )

    def get_application(self, app_id: int) -> Optional[Application]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM applications WHERE id=?", (app_id,)).fetchone()
        return Application.model_validate_json(row["data"]) if row else None

    def get_application_for_job(self, job_id: str) -> Optional[Application]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM applications WHERE job_id=?", (job_id,)).fetchone()
        return Application.model_validate_json(row["data"]) if row else None

    def list_applications(self, statuses: Optional[Iterable[str]] = None) -> list[Application]:
        sql, args = "SELECT data FROM applications", []
        statuses = list(statuses or [])
        if statuses:
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            args = statuses
        sql += " ORDER BY id DESC"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [Application.model_validate_json(r["data"]) for r in rows]

    def delete_application(self, app_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM applications WHERE id=?", (app_id,))
