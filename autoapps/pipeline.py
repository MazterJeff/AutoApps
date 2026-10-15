"""The application workflow: queue -> prepare (autofill + Claude) -> review -> submit."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from . import autofill, notify
from .autofill import COVER_TOKEN, RESUME_TOKEN
from .config import Config
from .db import Store
from .documents import write_cover_letter_pdf
from .forms import FormResult, load_form
from .llm import LLM, LLMError
from .models import Application, FormField, Job, SavedAnswer, now_iso
from .sources import RefreshResult, SourceConfig, fetch_all

log = logging.getLogger(__name__)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "file"


class Service:
    def __init__(
        self,
        config: Config,
        store: Optional[Store] = None,
        llm: Optional[LLM] = None,
        form_loader: Callable[[Job], FormResult] = load_form,
        submitter: Optional[Callable] = None,
        background: bool = True,
    ):
        config.ensure_dirs()
        self.config = config
        self.store = store or Store(config.db_path)
        self.llm = llm or LLM(config)
        self.form_loader = form_loader
        self._submitter = submitter
        self.background = background
        self._prepare_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prepare")
        self._submit_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="submit")
        self._refresh_lock = threading.Lock()
        self.refresh_status: dict = {"running": False, "finished_at": None, "errors": [], "counts": {}, "new": 0}

    def _run(self, pool: ThreadPoolExecutor, fn, *args) -> None:
        if self.background:
            pool.submit(self._guard, fn, *args)
        else:
            fn(*args)

    @staticmethod
    def _guard(fn, *args):
        try:
            fn(*args)
        except Exception:  # keep worker threads alive and the error visible in logs
            log.exception("background task %s failed", fn.__name__)

    # ----- paths --------------------------------------------------------------
    @property
    def resume_dir(self) -> Path:
        return self.config.files_dir / "resume"

    def resume_path(self) -> Optional[Path]:
        name = self.store.get_profile().resume_filename
        path = self.resume_dir / name if name else None
        return path if path and path.exists() else None

    def cover_letter_path(self, app: Application, job: Job) -> Path:
        profile = self.store.get_profile()
        d = self.config.files_dir / "cover_letters"
        d.mkdir(parents=True, exist_ok=True)
        stem = _safe(f"{profile.first_name}_{profile.last_name}_Cover_Letter_{job.company}")
        return d / f"{stem}_{app.id}.pdf"

    # ----- sources ------------------------------------------------------------
    def source_config(self) -> SourceConfig:
        return SourceConfig.model_validate(self.store.get_setting("sources", {}))

    def refresh_jobs(self) -> Optional[RefreshResult]:
        if not self._refresh_lock.acquire(blocking=False):
            return None  # already running
        try:
            self.refresh_status.update(running=True, errors=[])
            result = fetch_all(self.source_config())
            new, _ = self.store.upsert_jobs(result.jobs)
            self.refresh_status.update(errors=result.errors, counts=result.counts, new=new, finished_at=now_iso())
            return result
        finally:
            self.refresh_status["running"] = False
            self._refresh_lock.release()

    def refresh_jobs_async(self) -> None:
        self._run(self._prepare_pool, self.refresh_jobs)

    # ----- preparing ----------------------------------------------------------
    def queue(self, job_ids: list[str]) -> list[Application]:
        apps = []
        for jid in job_ids:
            if not self.store.get_job(jid):
                continue
            app = self.store.create_application(jid)
            self.store.set_job_status([jid], "queued")
            if app.status in {"queued", "failed"}:
                self.prepare_async(app.id)
            apps.append(app)
        return apps

    def prepare_async(self, app_id: int) -> None:
        app = self.store.get_application(app_id)
        if app:
            app.status = "queued"
            self.store.save_application(app)
        self._run(self._prepare_pool, self.prepare, app_id)

    def prepare(self, app_id: int) -> Application:
        app = self.store.get_application(app_id)
        if app is None:
            raise KeyError(app_id)
        job = self.store.get_job(app.job_id)
        app.status, app.error = "preparing", ""
        app.add_log("Preparing draft")
        self.store.save_application(app)
        try:
            self._prepare(app, job)
            app.status = "ready"
            app.add_log(f"Draft ready; {app.review_count} item(s) flagged for review")
        except Exception as exc:
            log.exception("prepare failed for application %s", app_id)
            app.status, app.error = "failed", str(exc)
            app.add_log(f"Preparation failed: {exc}")
        self.store.save_application(app)
        if app.status == "ready":
            settings = self.store.get_settings()
            if notify.send_draft_ready(self.config, settings.notify_email, app, job):
                app.add_log(f"Review email sent to {settings.notify_email}")
                self.store.save_application(app)
        return app

    def _prepare(self, app: Application, job: Job) -> None:
        profile = self.store.get_profile()
        library = self.store.get_answer_library()
        settings = self.store.get_settings()

        form = self.form_loader(job)
        for note in form.notes:
            app.add_log(note)
        if form.description and not job.description:
            job.description = form.description
            self.store.upsert_jobs([job])

        fields = form.fields
        # When regenerating, keep anything you typed yourself.
        mine = {f.key: f for f in app.fields if f.source == "user"}
        for f in fields:
            if f.key in mine:
                f.value, f.source = mine[f.key].value, "user"
        autofill.fill_from_profile(fields, profile, library)

        pending = [f for f in fields if f.is_empty() and f.type != "file"]
        if pending:
            try:
                answers = self.llm.answer_questions(profile, job, pending)
                app.add_log(f"Claude drafted answers for {len(pending)} question(s)")
            except LLMError as exc:
                answers = {}
                app.add_log(f"Claude couldn't draft answers: {exc}")
            for f in pending:
                self._apply_ai_answer(f, answers.get(f.key))

        for f in fields:
            if f.required and f.is_empty():
                f.needs_review = True
                f.note = f.note or "Required and still empty."
        app.fields = fields

        wants_letter = any(f.value == COVER_TOKEN for f in fields) or settings.always_cover_letter
        if wants_letter and not app.cover_letter.strip():
            try:
                app.cover_letter = self.llm.write_cover_letter(profile, job, words=settings.cover_letter_words)
                app.add_log("Cover letter drafted")
            except LLMError as exc:
                app.add_log(f"Claude couldn't draft the cover letter: {exc}")

    @staticmethod
    def _apply_ai_answer(f: FormField, answer: Optional[dict]) -> None:
        if not answer:
            f.needs_review, f.note = True, "No answer drafted."
            return
        raw = "; ".join(answer.get("values") or []) or answer.get("value", "")
        if raw and autofill.set_value(f, raw, "ai"):
            f.needs_review = answer.get("confidence") != "high"
        else:
            f.needs_review = True
        f.note = answer.get("note", "")
        if f.is_empty() and not f.note:
            f.note = "No answer drafted."

    # ----- review -------------------------------------------------------------
    def revise_cover_letter(self, app_id: int, feedback: str) -> Application:
        app = self.store.get_application(app_id)
        job = self.store.get_job(app.job_id)
        profile = self.store.get_profile()
        if app.cover_letter.strip():
            app.cover_letter = self.llm.revise_cover_letter(profile, job, app.cover_letter, feedback)
        else:
            settings = self.store.get_settings()
            app.cover_letter = self.llm.write_cover_letter(profile, job, settings.cover_letter_words, feedback)
        app.add_log(f"Cover letter revised: {feedback[:80]}")
        self.store.save_application(app)
        return app

    def revise_answer(self, app_id: int, key: str, feedback: str) -> Application:
        app = self.store.get_application(app_id)
        job = self.store.get_job(app.job_id)
        field = next(f for f in app.fields if f.key == key)
        current = field.value if isinstance(field.value, str) else "; ".join(field.value or [])
        field.value = self.llm.revise_answer(self.store.get_profile(), job, field, current, feedback)
        field.source, field.needs_review = "ai", False
        app.add_log(f"Answer to '{field.label[:60]}' revised")
        self.store.save_application(app)
        return app

    def remember_answers(self, app: Application, keys: list[str]) -> None:
        library = {autofill.normalize(a.question): a for a in self.store.get_answer_library()}
        for f in app.fields:
            if f.key in keys and not f.is_empty() and f.value != COVER_TOKEN:
                value = f.value if isinstance(f.value, str) else "; ".join(f.value)
                library[autofill.normalize(f.label)] = SavedAnswer(question=f.label, answer=value)
        self.store.save_answer_library(library.values())

    # ----- submitting -----------------------------------------------------------
    def submit_blockers(self, app: Application, mode: str) -> list[str]:
        problems = []
        if self.resume_path() is None:
            problems.append("Upload your resume on the Profile page first.")
        if mode == "auto":
            empty = [f.label for f in app.fields if f.required and f.is_empty()]
            if empty:
                problems.append("Required answers are empty: " + ", ".join(empty[:5]))
        return problems

    def submit_async(self, app_id: int, mode: str) -> None:
        app = self.store.get_application(app_id)
        app.status = "submitting"
        app.add_log(f"Opening browser ({'auto-submit' if mode == 'auto' else 'you click Submit'})")
        self.store.save_application(app)
        self._run(self._submit_pool, self.submit, app_id, mode)

    def submit(self, app_id: int, mode: str) -> Application:
        app = self.store.get_application(app_id)
        job = self.store.get_job(app.job_id)
        profile = self.store.get_profile()
        settings = self.store.get_settings()
        try:
            files: dict[str, Path] = {}
            resume = self.resume_path()
            if resume:
                files[RESUME_TOKEN] = resume
            if app.cover_letter.strip():
                files[COVER_TOKEN] = write_cover_letter_pdf(self.cover_letter_path(app, job), app.cover_letter, profile, job)
            values = {
                f.key: (app.cover_letter if f.value == COVER_TOKEN and f.type != "file" else f.value)
                for f in app.fields
            }
            shots = self.config.files_dir / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            submitter = self._submitter
            if submitter is None:
                from .browser import run_application as submitter
            result = submitter(
                job.apply_url or job.url,
                app.fields,
                values,
                files,
                auto_submit=(mode == "auto"),
                headless=settings.headless,
                manual_wait_seconds=settings.manual_wait_minutes * 60,
                profile_dir=self.config.browser_profile_dir,
                screenshot_prefix=shots / f"app_{app.id}",
            )
            app.status = result.status
            app.add_log(result.message)
            for p in result.problems:
                app.add_log(f"  ! {p}")
            app.screenshots = [str(s) for s in result.screenshots]
            if result.status == "submitted":
                app.submitted_at = now_iso()
                self.store.set_job_status([job.id], "applied")
            else:
                app.error = result.message
        except Exception as exc:
            log.exception("submit failed for application %s", app_id)
            app.status, app.error = "failed", str(exc)
            app.add_log(f"Submission failed: {exc}")
        self.store.save_application(app)
        return app

    def mark_submitted(self, app_id: int) -> Application:
        app = self.store.get_application(app_id)
        app.status, app.submitted_at, app.error = "submitted", now_iso(), ""
        app.add_log("Marked as submitted by you")
        self.store.save_application(app)
        self.store.set_job_status([app.job_id], "applied")
        return app
