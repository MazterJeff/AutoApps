"""Parse application forms out of server-rendered HTML (Lever and many custom career sites)."""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from ..models import FormField

SKIP_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image", "search", "password"}
CONTAINER_CLASSES = {"form-group", "form-field", "field", "input-group", "form-item"}


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.replace("✱", "").strip(" *:")


def _text(node: Tag) -> str:
    """Visible text of ``node`` without the text of controls inside it (e.g. <option>s)."""
    parts = []
    for s in node.find_all(string=True):
        if not any(p.name in {"select", "option", "textarea", "script", "style"} for p in s.parents if isinstance(p, Tag)):
            parts.append(s)
    return _clean(" ".join(parts))


def _is_container(node: Tag) -> bool:
    classes = node.get("class") or []
    return node.name in {"li", "fieldset"} or any("question" in c.lower() or c.lower() in CONTAINER_CLASSES for c in classes)


def _container(el: Tag, group: str = "") -> Optional[Tag]:
    """The element wrapping one question. For radio/checkbox groups it must hold the whole group."""
    for parent in el.parents:
        if not isinstance(parent, Tag) or parent.name in {"form", "body"}:
            return None
        if not _is_container(parent):
            continue
        if group and len(parent.find_all("input", attrs={"name": group})) < 2:
            continue
        return parent
    return None


def _label_for(soup: BeautifulSoup, el: Tag, grouped: bool) -> str:
    if not grouped and el.get("id"):
        lab = soup.find("label", attrs={"for": el["id"]})
        if lab and _text(lab):
            return _text(lab)
    box = _container(el, el.get("name", "") if grouped else "") or (_container(el) if grouped else None)
    if box is not None:
        for sel in [".application-label", "legend", ".text", "label"]:
            node = box.select_one(sel)
            if node is not None and _text(node):
                return _text(node)
    if not grouped:
        parent_label = el.find_parent("label")
        if parent_label is not None:
            return _text(parent_label)
    return _clean(el.get("aria-label") or el.get("placeholder") or el.get("name") or "")


def _is_required(el: Tag, label_html: str) -> bool:
    return el.has_attr("required") or el.get("aria-required") == "true" or "✱" in label_html or label_html.rstrip().endswith("*")


def _option_label(soup: BeautifulSoup, el: Tag) -> str:
    if el.get("id"):
        lab = soup.find("label", attrs={"for": el["id"]})
        if lab:
            return _clean(lab.get_text(" "))
    parent_label = el.find_parent("label")
    if parent_label is not None and _clean(parent_label.get_text(" ")):
        return _clean(parent_label.get_text(" "))
    return el.get("value", "")


def _pick_form(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    forms = soup.find_all("form")
    if not forms:
        return soup
    return max(forms, key=lambda f: len(f.find_all(["input", "textarea", "select"])))


def parse(html: str) -> list[FormField]:
    soup = BeautifulSoup(html, "lxml")
    form = _pick_form(soup)
    fields: list[FormField] = []
    groups: dict[str, FormField] = {}

    for el in form.find_all(["input", "textarea", "select"]):
        name = el.get("name") or el.get("id") or ""
        if not name:
            continue
        tag = el.name
        itype = (el.get("type") or "text").lower() if tag == "input" else tag
        if tag == "input" and itype in SKIP_INPUT_TYPES:
            continue
        box = _container(el, name if itype in {"radio", "checkbox"} else "") or _container(el)
        box_html = str(box)[:2000] if box is not None else ""

        if itype in {"radio", "checkbox"}:
            option = _option_label(soup, el)
            if name in groups:
                groups[name].options.append(option)
                continue
            label = _label_for(soup, el, grouped=True)
            field = FormField(
                key=name, label=label or option, type=itype, options=[option],
                required=_is_required(el, box_html), selector=f'[name="{name}"]',
            )
            groups[name] = field
            fields.append(field)
            continue

        label = _label_for(soup, el, grouped=False)
        if tag == "select":
            options = [_clean(o.get_text(" ")) for o in el.find_all("option") if o.get("value", "x") != ""]
            ftype = "multiselect" if el.has_attr("multiple") else "select"
        else:
            options = []
            ftype = {
                "textarea": "textarea", "email": "email", "tel": "tel", "url": "url",
                "number": "number", "date": "date", "file": "file",
            }.get(itype, "text")
        selector = f'[id="{el["id"]}"]' if el.get("id") else f'[name="{name}"]'
        fields.append(FormField(
            key=name, label=label, type=ftype, options=[o for o in options if o],
            required=_is_required(el, box_html or str(el)), selector=selector,
        ))

    # A lone checkbox is a yes/no toggle ("I agree..."), not a multi-choice question.
    for f in fields:
        if f.type == "checkbox" and len(f.options) == 1:
            f.options = []
    return fields
