"""Playwright automation: read client-rendered forms, fill them in, and submit.

CAPTCHAs are never bypassed. When a site shows one (or anything else the filler can't
handle) the browser window stays open so you can finish by hand; the app watches for the
"thanks for applying" page and records the submission.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from .models import FormField

log = logging.getLogger(__name__)

EXTRACT_JS = (Path(__file__).parent / "forms" / "extract.js").read_text()

CONFIRM_RE = re.compile(
    r"thank(s| you) for (applying|your application|submitting)|application (has been |was )?(submitted|received)"
    r"|we(’|')ve received your application|successfully (submitted|applied)",
    re.I,
)
SUBMIT_RE = re.compile(r"^\s*(submit( your)?( application)?|apply( now)?|send( application)?|complete application)\s*$", re.I)
APPLY_RE = re.compile(r"^\s*(apply( now| for this (job|role|position))?|i'?m interested)\s*$", re.I)
TRUTHY = {"yes", "true", "1", "on", "checked", "agree", "i agree", "accept", "i accept"}


class BrowserUnavailable(RuntimeError):
    pass


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on install
        raise BrowserUnavailable(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright


@contextmanager
def browser_context(profile_dir: Optional[Path], headless: bool) -> Iterator:
    """A Chromium context. With ``profile_dir`` cookies persist, so site logins (Workday etc.) stick."""
    with _sync_playwright()() as p:
        if profile_dir is not None:
            profile_dir.mkdir(parents=True, exist_ok=True)
            ctx = p.chromium.launch_persistent_context(str(profile_dir), headless=headless, viewport={"width": 1280, "height": 900})
            try:
                yield ctx
            finally:
                ctx.close()
        else:
            browser = p.chromium.launch(headless=headless)
            try:
                yield browser.new_context(viewport={"width": 1280, "height": 900})
            finally:
                browser.close()


def _goto(page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass  # long-polling sites never go idle; the DOM is usually ready anyway


def _open_application(page) -> None:
    """If we landed on a description page, click through to the form."""
    if page.locator("input:not([type=hidden]), textarea").count() >= 3:
        return
    for role in ("link", "button"):
        target = page.get_by_role(role, name=APPLY_RE)
        if target.count():
            try:
                target.first.click(timeout=5_000)
                page.wait_for_timeout(2_500)
                return
            except Exception:
                continue


def scrape_form(url: str) -> tuple[list[FormField], str]:
    """Render ``url`` headlessly and describe its form fields. Returns (fields, page_text)."""
    with browser_context(None, headless=True) as ctx:
        page = ctx.new_page()
        _goto(page, url)
        _open_application(page)
        data = page.evaluate(EXTRACT_JS)
    fields = [FormField.model_validate(f) for f in data.get("fields", [])]
    return fields, data.get("text", "")


# ----- filling -----------------------------------------------------------
def _css_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _locate(page, f: FormField):
    candidates = [f.selector] if f.selector else []
    if f.key:
        candidates += [f'[id="{_css_str(f.key)}"]', f'[name="{_css_str(f.key)}"]']
    for css in candidates:
        try:
            loc = page.locator(css)
            if loc.count():
                return loc
        except Exception:
            continue
    if f.label:
        try:
            loc = page.get_by_label(f.label, exact=False)
            if loc.count():
                return loc
        except Exception:
            pass
    return None


def _values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if str(v).strip()]
    return [value] if str(value).strip() else []


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _pick_option(page, wanted: str) -> bool:
    options = page.get_by_role("option")
    try:
        page.wait_for_timeout(600)
        n = options.count()
    except Exception:
        return False
    best = None
    for i in range(min(n, 200)):
        text = _norm(options.nth(i).inner_text())
        if text == _norm(wanted):
            best = i
            break
        if best is None and _norm(wanted) in text:
            best = i
    if best is not None:
        options.nth(best).click()
        return True
    return False


def _fill_choice_group(page, f: FormField, values: list[str]) -> bool:
    group = page.locator(f'input[name="{_css_str(f.key)}"]') if f.key else None
    if group is None or not group.count():
        loc = _locate(page, f)
        if loc is None:
            return False
        group = loc
    if not f.options:  # single toggle checkbox
        if _norm(values[0]) in TRUTHY or values[0].lower().startswith("yes"):
            try:
                group.first.check()
            except Exception:
                group.first.check(force=True)
        return True
    wanted = {_norm(v) for v in values}
    done = 0
    for i in range(group.count()):
        item = group.nth(i)
        label = item.evaluate(
            """e => { const l = (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) || e.closest('label');
                      return (l ? l.innerText : '') + '|' + (e.value || ''); }"""
        )
        text, _, value = label.partition("|")
        if _norm(text) in wanted or _norm(value) in wanted:
            try:
                item.check()
            except Exception:
                item.check(force=True)
            done += 1
    return done > 0


def fill_field(page, f: FormField, value, files: dict[str, Path]) -> Optional[str]:
    """Fill one field. Returns a problem description, or None on success."""
    values = _values(value)
    if not values:
        return None
    try:
        if f.type == "file":
            path = files.get(values[0])
            if path is None:
                return f"{f.label}: no file to upload"
            loc = _locate(page, f)
            target = None
            if loc is not None:
                tag_type = loc.first.evaluate("e => e.tagName.toLowerCase() + ':' + (e.type || '')")
                target = loc.first if tag_type == "input:file" else loc.first.locator(
                    "xpath=ancestor::*[.//input[@type='file']][1]//input[@type='file']").first
            if target is None or not target.count():
                inputs = page.locator("input[type=file]")
                if not inputs.count():
                    return f"{f.label}: upload control not found"
                target = inputs.nth(0 if values[0] == "@resume" or inputs.count() == 1 else 1)
            target.set_input_files(str(path))
            return None

        if f.type in {"radio", "checkbox"}:
            return None if _fill_choice_group(page, f, values) else f"{f.label}: couldn't select {values}"

        loc = _locate(page, f)
        if loc is None:
            return f"{f.label}: field not found on page"
        el = loc.first
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        role = el.get_attribute("role") or ""
        if tag == "input" and (el.get_attribute("type") or "").lower() in {"checkbox", "radio"}:
            return None if _fill_choice_group(page, f, values) else f"{f.label}: couldn't select {values}"

        if tag == "select":
            try:
                el.select_option(label=values if f.type == "multiselect" else values[0])
            except Exception:
                el.select_option(values if f.type == "multiselect" else values[0])
            return None

        if f.type in {"select", "multiselect"} or role == "combobox":
            for v in values:
                el.click()
                el.fill(v)
                if not _pick_option(page, v):
                    el.press("Enter")
            return None

        el.fill(str(values[0]) if f.type != "textarea" else "\n".join(values))
        return None
    except Exception as exc:
        return f"{f.label}: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}"


# ----- submitting ----------------------------------------------------------
@dataclass
class SubmitResult:
    status: str  # submitted | needs_attention | failed
    message: str
    problems: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)


def _confirmation_text(page) -> str:
    try:
        m = CONFIRM_RE.search(page.inner_text("body", timeout=5_000))
        return m.group(0).lower() if m else ""
    except Exception:
        return ""


class _Baseline:
    """What the page looked like before submitting, so boilerplate like "Thanks for applying
    to Acme!" in a job description isn't mistaken for a confirmation."""

    def __init__(self, page):
        self.url = page.url
        self.text = _confirmation_text(page)

    def confirmed(self, page) -> bool:
        text = _confirmation_text(page)
        return bool(text) and (not self.text or page.url != self.url)


def _validation_errors(page) -> list[str]:
    try:
        errs = page.locator("[aria-invalid=true], .error:visible, .field-error:visible, [role=alert]:visible")
        out = []
        for i in range(min(errs.count(), 10)):
            text = errs.nth(i).inner_text().strip() or (errs.nth(i).get_attribute("name") or "")
            if text:
                out.append(text[:150])
        return out
    except Exception:
        return []


def _wait_for_confirmation(page, baseline: _Baseline, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if page.is_closed():
            return False
        if baseline.confirmed(page):
            return True
        try:
            page.wait_for_timeout(2_000)
        except Exception:
            return False
    return False


def run_application(
    url: str,
    fields: list[FormField],
    values: dict[str, object],
    files: dict[str, Path],
    *,
    auto_submit: bool,
    headless: bool,
    manual_wait_seconds: int,
    profile_dir: Optional[Path],
    screenshot_prefix: Path,
) -> SubmitResult:
    """Open ``url``, fill every field, and submit (auto) or hand over to you (review)."""
    headless = headless and auto_submit  # review mode needs a visible window
    with browser_context(profile_dir, headless=headless) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _goto(page, url)
        _open_application(page)

        baseline = _Baseline(page)
        problems = [p for f in fields if (p := fill_field(page, f, values.get(f.key), files))]
        shots = []
        filled_png = f"{screenshot_prefix}_filled.png"
        try:
            page.screenshot(path=filled_png, full_page=True)
            shots.append(filled_png)
        except Exception:
            pass

        if auto_submit:
            button = page.get_by_role("button", name=SUBMIT_RE)
            if not button.count():
                button = page.locator("button[type=submit], input[type=submit]")
            if not button.count():
                problems.append("Submit button not found")
            else:
                button.first.click()
                if _wait_for_confirmation(page, baseline, 25):
                    return _finish(page, shots, screenshot_prefix, problems, "Submitted and confirmation page detected.")
                problems.extend(_validation_errors(page))

        if headless:
            return SubmitResult("needs_attention", "No confirmation page detected after submitting.", problems, shots)

        # Hand over to the human: fix anything flagged, solve a CAPTCHA, click Submit.
        log.info("Waiting up to %ss for you to finish in the browser window.", manual_wait_seconds)
        if _wait_for_confirmation(page, baseline, manual_wait_seconds):
            return _finish(page, shots, screenshot_prefix, problems, "Confirmation page detected.")
        return SubmitResult(
            "needs_attention",
            "The browser was closed or timed out before a confirmation page appeared. "
            "If you did submit, use 'Mark as submitted'.",
            problems, shots,
        )


def _finish(page, shots, prefix, problems, message) -> SubmitResult:
    done_png = f"{prefix}_confirmation.png"
    try:
        page.screenshot(path=done_png, full_page=True)
        shots.append(done_png)
    except Exception:
        pass
    return SubmitResult("submitted", message, problems, shots)
