"""Email you when a draft is ready for review (optional; needs SMTP settings)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import Config
from .models import Application, Job

log = logging.getLogger(__name__)


def email_configured(config: Config) -> bool:
    return bool(config.smtp_host and (config.smtp_from or config.smtp_user))


def send_draft_ready(config: Config, to: str, app: Application, job: Job) -> bool:
    if not to or not email_configured(config):
        return False
    link = f"{config.base_url}/applications/{app.id}"
    flagged = [f.label for f in app.fields if f.needs_review or (f.required and f.is_empty())]
    lines = [
        f"Your application draft for {job.title} at {job.company} is ready for review.",
        "",
        f"Review and edit it here: {link}",
        "",
    ]
    if flagged:
        lines += ["These answers need your attention:"] + [f"  - {label}" for label in flagged] + [""]
    if app.cover_letter:
        lines += ["--- Cover letter draft ---", app.cover_letter]
    msg = EmailMessage()
    msg["Subject"] = f"[AutoApps] Review: {job.company} - {job.title}"
    msg["From"] = config.smtp_from or config.smtp_user
    msg["To"] = to
    msg.set_content("\n".join(lines))
    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if config.smtp_user:
                smtp.login(config.smtp_user, config.smtp_password)
            smtp.send_message(msg)
        return True
    except Exception as exc:
        log.warning("Couldn't send review email: %s", exc)
        return False
