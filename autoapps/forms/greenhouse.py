"""Read a Greenhouse application form from the public Job Board API (``?questions=true``)."""

from __future__ import annotations

import requests

from ..models import FormField
from ..sources.common import html_to_text

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true"

TYPE_MAP = {
    "input_text": "text",
    "input_file": "file",
    "textarea": "textarea",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multiselect",
}


def fetch(board: str, job_id: str, sess: requests.Session) -> tuple[list[FormField], str]:
    resp = sess.get(API.format(board=board, job_id=job_id), timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    return parse(payload), html_to_text(payload.get("content", ""))


def _option_labels(values) -> list[str]:
    out = []
    for v in values or []:
        label = v.get("label") if isinstance(v, dict) else str(v)
        if label:
            out.append(str(label))
    return out


def _question_fields(q: dict) -> list[FormField]:
    fields = [f for f in q.get("fields") or [] if f.get("type") in TYPE_MAP]
    # "Resume/CV" and "Cover Letter" come as an upload plus a paste-text alternative; keep the upload.
    if any(f["type"] == "input_file" for f in fields):
        fields = [f for f in fields if f["type"] == "input_file"]
    label = (q.get("label") or "").strip()
    out = []
    for f in fields:
        name = f.get("name", "")
        ftype = TYPE_MAP[f["type"]]
        lowered = label.lower()
        if ftype == "text" and "email" in lowered:
            ftype = "email"
        elif ftype == "text" and "phone" in lowered:
            ftype = "tel"
        out.append(FormField(
            key=name,
            label=label,
            type=ftype,
            required=bool(q.get("required")),
            options=_option_labels(f.get("values")),
            description=html_to_text(q.get("description") or ""),
            selector=f'[id="{name}"]',
        ))
    return out


def parse(payload: dict) -> list[FormField]:
    fields: list[FormField] = []
    for q in payload.get("questions") or []:
        fields.extend(_question_fields(q))
    for q in payload.get("location_questions") or []:
        fields.extend(_question_fields(q))
    for section in payload.get("compliance") or []:
        for q in section.get("questions") or []:
            fields.extend(_question_fields(q))
    demo = payload.get("demographic_questions") or {}
    for q in demo.get("questions") or []:
        options = [o.get("label", "") for o in q.get("answer_options") or [] if o.get("label")]
        qtype = "multiselect" if "multi" in (q.get("type") or "") and "single" not in (q.get("type") or "") else "select"
        fields.append(FormField(
            key=f"demographic_{q.get('id')}",
            label=(q.get("label") or "").strip(),
            type=qtype,
            required=bool(q.get("required")),
            options=options,
        ))
    seen, unique = set(), []
    for f in fields:
        if f.key and f.key not in seen:
            seen.add(f.key)
            unique.append(f)
    return unique
