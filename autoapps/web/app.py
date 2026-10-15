"""Local web UI (Starlette + Jinja2). Run with ``python -m autoapps serve``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from .. import resume as resume_mod
from ..autofill import COVER_TOKEN, RESUME_TOKEN
from ..config import Config
from ..documents import cover_letter_docx, cover_letter_pdf
from ..filters import JobFilter, apply_filter
from ..llm import LLMError
from ..models import Education, Experience, Profile, SavedAnswer, Settings
from ..notify import email_configured
from ..pipeline import Service, _safe
from ..sources import SourceConfig

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
PAGE_SIZE = 50

STATUS_LABELS = {
    "queued": "Queued", "preparing": "Drafting…", "ready": "Ready for review", "approved": "Approved",
    "submitting": "Submitting…", "submitted": "Submitted", "needs_attention": "Needs attention", "failed": "Failed",
}


def _redirect(url: str, msg: str = "", level: str = "ok") -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + urlencode({"msg": msg, "level": level})
    return RedirectResponse(url, status_code=303)


def _lines(raw: str) -> list[str]:
    return [l.strip() for l in (raw or "").replace(",", "\n").splitlines() if l.strip()]


def create_app(service: Optional[Service] = None, config: Optional[Config] = None) -> Starlette:
    config = config or Config()
    svc = service or Service(config)
    store = svc.store
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(STATUS_LABELS=STATUS_LABELS, COVER_TOKEN=COVER_TOKEN, RESUME_TOKEN=RESUME_TOKEN)

    def render(request: Request, name: str, **ctx) -> Response:
        ctx.setdefault("msg", request.query_params.get("msg", ""))
        ctx.setdefault("level", request.query_params.get("level", "ok"))
        ctx["nav_counts"] = {
            "review": len(store.list_applications(["ready", "needs_attention", "failed"])),
            "busy": len(store.list_applications(["queued", "preparing", "submitting"])),
        }
        ctx["profile_missing"] = store.get_profile().missing_basics()
        return templates.TemplateResponse(request, name, ctx)

    # ----- jobs ---------------------------------------------------------------
    def _filter_from(request: Request) -> JobFilter:
        q = request.query_params
        if q.get("reset"):
            store.set_setting("last_filter", JobFilter().model_dump())
            return JobFilter()
        if not q.get("applied"):
            saved = store.get_setting("last_filter")
            return JobFilter.model_validate(saved) if saved else JobFilter()
        days = q.get("posted_within_days", "").strip()
        flt = JobFilter(
            keywords=q.get("keywords", ""),
            title=q.get("title", ""),
            location=q.get("location", ""),
            remote_only=bool(q.get("remote_only")),
            companies=q.get("companies", ""),
            exclude_companies=q.get("exclude_companies", ""),
            sources=q.getlist("sources"),
            statuses=q.getlist("statuses") or ["new", "shortlisted"],
            posted_within_days=int(days) if days.isdigit() else None,
            internships_only=bool(q.get("internships_only")),
            hide_no_sponsorship=bool(q.get("hide_no_sponsorship")),
            min_score=int(q.get("min_score") or 0),
            sort=q.get("sort", "posted") if q.get("sort") in {"posted", "score", "company"} else "posted",
        )
        store.set_setting("last_filter", flt.model_dump())
        return flt

    async def jobs_page(request: Request):
        flt = _filter_from(request)
        profile = store.get_profile()
        results = apply_filter(store.list_jobs(flt.statuses), flt, profile)
        page = max(1, int(request.query_params.get("page", "1") or 1))
        total = len(results)
        rows = results[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        qs = {k: v for k, v in request.query_params.multi_items() if k not in {"page", "msg", "level"}}
        return render(
            request, "jobs.html", flt=flt, rows=rows, total=total, page=page,
            pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE), qs=urlencode(qs, doseq=True),
            counts=store.job_counts(), refresh=svc.refresh_status, has_skills=bool(profile.skills),
            sources=sorted({j.source for j in store.list_jobs()} | {"greenhouse", "lever", "ashby", "simplify"}),
        )

    async def jobs_action(request: Request):
        form = await request.form()
        ids = form.getlist("job_ids")
        action = form.get("action")
        back = "/?" + (form.get("qs") or "")
        if not ids:
            return _redirect(back, "Select at least one job first.", "warn")
        if action == "prepare":
            missing = store.get_profile().missing_basics()
            if "resume" in missing:
                return _redirect("/profile", "Upload your resume before preparing applications.", "warn")
            apps = svc.queue(ids)
            return _redirect("/applications", f"Drafting {len(apps)} application(s). They'll appear here when ready.")
        status = {"shortlist": "shortlisted", "dismiss": "dismissed", "restore": "new"}.get(action)
        if status:
            store.set_job_status(ids, status)
            return _redirect(back, f"{len(ids)} job(s) marked {status}.")
        return _redirect(back, "Unknown action.", "warn")

    async def job_detail(request: Request):
        job = store.get_job(request.path_params["job_id"])
        if job is None:
            return _redirect("/", "Job not found.", "warn")
        return render(request, "job.html", job=job, app=store.get_application_for_job(job.id))

    async def refresh(request: Request):
        svc.refresh_jobs_async()
        return _redirect("/", "Refreshing job sources in the background. Reload in a minute.")

    # ----- sources ------------------------------------------------------------
    async def sources_page(request: Request):
        if request.method == "POST":
            form = await request.form()
            cfg = SourceConfig(
                greenhouse=_lines(form.get("greenhouse", "")),
                lever=_lines(form.get("lever", "")),
                ashby=_lines(form.get("ashby", "")),
                simplify=_lines(form.get("simplify", "")),
                internships_only=bool(form.get("internships_only")),
            )
            store.set_setting("sources", cfg.model_dump())
            if form.get("refresh"):
                svc.refresh_jobs_async()
                return _redirect("/sources", "Saved. Refreshing in the background.")
            return _redirect("/sources", "Saved.")
        return render(request, "sources.html", cfg=svc.source_config(), refresh=svc.refresh_status)

    # ----- profile ------------------------------------------------------------
    def _rows(form: FormData, prefix: str, attrs: list[str]) -> list[dict]:
        rows = []
        i = 0
        while f"{prefix}-{i}-{attrs[0]}" in form:
            row = {a: (form.get(f"{prefix}-{i}-{a}") or "").strip() for a in attrs}
            if any(row.values()):
                rows.append(row)
            i += 1
        return rows

    async def profile_page(request: Request):
        profile = store.get_profile()
        if request.method == "POST":
            form = await request.form()
            data = profile.model_dump()
            for name in Profile.model_fields:
                if name in {"education", "experience", "skills", "resume_filename", "resume_text"}:
                    continue
                if name in form:
                    data[name] = (form.get(name) or "").strip()
            data["skills"] = _lines(form.get("skills", ""))
            data["education"] = _rows(form, "edu", list(Education.model_fields))
            data["experience"] = _rows(form, "exp", list(Experience.model_fields))
            if "resume_text" in form:
                data["resume_text"] = form.get("resume_text") or ""
            store.save_profile(Profile.model_validate(data))
            return _redirect("/profile", "Profile saved.")
        return render(request, "profile.html", profile=profile, library=store.get_answer_library(),
                      resume_ok=svc.resume_path() is not None)

    async def upload_resume(request: Request):
        form = await request.form()
        upload = form.get("resume")
        if upload is None or not getattr(upload, "filename", ""):
            return _redirect("/profile", "Choose a file to upload.", "warn")
        data = await upload.read()
        await upload.close()
        try:
            text = resume_mod.extract_text(upload.filename, data)
        except Exception as exc:
            return _redirect("/profile", f"Couldn't read that file: {exc}", "warn")
        svc.resume_dir.mkdir(parents=True, exist_ok=True)
        for old in svc.resume_dir.iterdir():
            old.unlink()
        name = _safe(Path(upload.filename).name)
        (svc.resume_dir / name).write_bytes(data)

        profile = store.get_profile()
        profile.resume_filename, profile.resume_text = name, text
        msg = "Resume uploaded."
        if form.get("autofill"):
            try:
                extracted = svc.llm.extract_profile(text)
                merged = profile.model_dump()
                for k, v in extracted.items():
                    if v and (not merged.get(k) or form.get("overwrite")):
                        merged[k] = v
                profile = Profile.model_validate(merged)
                msg += " Claude filled in your profile from it; check the details below."
            except (LLMError, ValueError) as exc:
                msg += f" (Couldn't auto-fill the profile: {exc})"
        store.save_profile(profile)
        return _redirect("/profile", msg)

    async def resume_file(request: Request):
        path = svc.resume_path()
        if path is None:
            return Response("No resume uploaded", status_code=404)
        return FileResponse(path, filename=path.name)

    async def answer_library(request: Request):
        form = await request.form()
        answers = []
        i = 0
        while f"q-{i}" in form:
            q, a = (form.get(f"q-{i}") or "").strip(), (form.get(f"a-{i}") or "").strip()
            if q and a and not form.get(f"del-{i}"):
                answers.append(SavedAnswer(question=q, answer=a))
            i += 1
        store.save_answer_library(answers)
        return _redirect("/profile#library", "Answer library saved.")

    # ----- applications -------------------------------------------------------
    async def applications_page(request: Request):
        apps = store.list_applications()
        jobs = {a.job_id: store.get_job(a.job_id) for a in apps}
        groups = {
            "Needs you": [a for a in apps if a.status in {"ready", "needs_attention", "failed"}],
            "In progress": [a for a in apps if a.status in {"queued", "preparing", "submitting", "approved"}],
            "Submitted": [a for a in apps if a.status == "submitted"],
        }
        busy = any(a.status in {"queued", "preparing", "submitting"} for a in apps)
        return render(request, "applications.html", groups=groups, jobs=jobs, busy=busy)

    def _get_app(request: Request):
        app = store.get_application(int(request.path_params["app_id"]))
        job = store.get_job(app.job_id) if app else None
        return app, job

    async def application_page(request: Request):
        app, job = _get_app(request)
        if app is None:
            return _redirect("/applications", "Application not found.", "warn")
        settings = store.get_settings()
        return render(request, "application.html", app=app, job=job, settings=settings,
                      blockers=svc.submit_blockers(app, settings.submit_mode),
                      busy=app.status in {"queued", "preparing", "submitting"})

    def _apply_edits(app, form: FormData) -> None:
        for i, f in enumerate(app.fields):
            name = f"field-{i}"
            if f.type == "file" or f.value == COVER_TOKEN:
                continue
            if f.is_multi:
                new = [v for v in form.getlist(name) if v]
            elif name in form:
                new = (form.get(name) or "").strip()
            elif f.type == "checkbox":
                new = ""  # unchecked boxes are absent from the form
            else:
                continue
            if new != (f.value or ([] if f.is_multi else "")):
                f.value, f.source = new, "user"
            f.needs_review = False
        if "cover_letter" in form:
            app.cover_letter = (form.get("cover_letter") or "").strip()

    async def application_save(request: Request):
        app, job = _get_app(request)
        form = await request.form()
        action = form.get("action", "save")
        _apply_edits(app, form)
        app.add_log("Edits saved")
        store.save_application(app)
        remember = [app.fields[int(i)].key for i in form.getlist("remember") if i.isdigit() and int(i) < len(app.fields)]
        if remember:
            svc.remember_answers(app, remember)
        url = f"/applications/{app.id}"

        try:
            if action == "revise_letter":
                feedback = (form.get("letter_feedback") or "").strip()
                if not feedback:
                    return _redirect(url, "Tell Claude what to change about the letter.", "warn")
                svc.revise_cover_letter(app.id, feedback)
                return _redirect(url + "#cover", "Cover letter revised.")
            if action.startswith("revise_field-"):
                idx = int(action.split("-", 1)[1])
                feedback = (form.get(f"feedback-{idx}") or "").strip() or "Improve this answer."
                svc.revise_answer(app.id, app.fields[idx].key, feedback)
                return _redirect(url + f"#f{idx}", "Answer revised.")
        except LLMError as exc:
            return _redirect(url, str(exc), "warn")

        if action == "regenerate":
            app.cover_letter = "" if form.get("new_letter") else app.cover_letter
            store.save_application(app)
            svc.prepare_async(app.id)
            return _redirect(url, "Re-drafting (your own edits are kept).")
        if action in {"submit_review", "submit_auto"}:
            mode = "auto" if action == "submit_auto" else "review"
            blockers = svc.submit_blockers(app, mode)
            if blockers:
                return _redirect(url, " ".join(blockers), "warn")
            svc.submit_async(app.id, mode)
            hint = ("A browser window will open, fill the form, and submit it."
                    if mode == "auto" else
                    "A browser window will open with everything filled in. Check it, then click Submit there.")
            return _redirect(url, hint)
        return _redirect(url, "Saved.")

    async def application_post_action(request: Request):
        app, job = _get_app(request)
        action = request.path_params["action"]
        if action == "mark-submitted":
            svc.mark_submitted(app.id)
            return _redirect(f"/applications/{app.id}", "Marked as submitted.")
        if action == "delete":
            store.delete_application(app.id)
            if job and job.status == "queued":
                store.set_job_status([job.id], "shortlisted")
            return _redirect("/applications", "Application removed.")
        return _redirect(f"/applications/{app.id}", "Unknown action.", "warn")

    async def cover_letter_download(request: Request):
        app, job = _get_app(request)
        fmt = request.path_params["fmt"]
        profile = store.get_profile()
        stem = _safe(f"{profile.first_name}_{profile.last_name}_Cover_Letter_{job.company}")
        if fmt == "pdf":
            body, mime = cover_letter_pdf(app.cover_letter, profile, job), "application/pdf"
        elif fmt == "docx":
            body = cover_letter_docx(app.cover_letter, profile, job)
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            body, mime = app.cover_letter.encode(), "text/plain"
            fmt = "txt"
        return Response(body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'})

    async def screenshot(request: Request):
        app, _ = _get_app(request)
        idx = int(request.path_params["idx"])
        if idx >= len(app.screenshots) or not Path(app.screenshots[idx]).exists():
            return Response("Not found", status_code=404)
        return FileResponse(app.screenshots[idx])

    # ----- settings -----------------------------------------------------------
    async def settings_page(request: Request):
        if request.method == "POST":
            form = await request.form()
            settings = Settings(
                always_cover_letter=bool(form.get("always_cover_letter")),
                cover_letter_words=int(form.get("cover_letter_words") or 300),
                submit_mode="auto" if form.get("submit_mode") == "auto" else "review",
                headless=bool(form.get("headless")),
                notify_email=(form.get("notify_email") or "").strip(),
                manual_wait_minutes=int(form.get("manual_wait_minutes") or 15),
            )
            store.save_settings(settings)
            return _redirect("/settings", "Settings saved.")
        return render(request, "settings.html", settings=store.get_settings(), config=config,
                      email_ok=email_configured(config))

    routes = [
        Route("/", jobs_page),
        Route("/jobs/action", jobs_action, methods=["POST"]),
        Route("/jobs/refresh", refresh, methods=["POST"]),
        Route("/jobs/{job_id:path}", job_detail),
        Route("/sources", sources_page, methods=["GET", "POST"]),
        Route("/profile", profile_page, methods=["GET", "POST"]),
        Route("/profile/resume", upload_resume, methods=["POST"]),
        Route("/profile/resume/file", resume_file),
        Route("/profile/answers", answer_library, methods=["POST"]),
        Route("/applications", applications_page),
        Route("/applications/{app_id:int}", application_page),
        Route("/applications/{app_id:int}/save", application_save, methods=["POST"]),
        Route("/applications/{app_id:int}/cover-letter.{fmt}", cover_letter_download),
        Route("/applications/{app_id:int}/screenshot/{idx:int}", screenshot),
        Route("/applications/{app_id:int}/{action}", application_post_action, methods=["POST"]),
        Route("/settings", settings_page, methods=["GET", "POST"]),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    app = Starlette(routes=routes)
    app.state.service = svc
    return app
