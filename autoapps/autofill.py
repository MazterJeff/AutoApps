"""Deterministic form filling from your profile and saved answers (no AI involved)."""

from __future__ import annotations

import difflib
import re
from typing import Callable, Optional

from .models import FormField, Profile, SavedAnswer

RESUME_TOKEN = "@resume"
COVER_TOKEN = "@cover_letter"

DECLINE_RE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not to (answer|disclose|say)|choose not|rather not", re.I)
YES_RE = re.compile(r"^\s*(yes|y|true)\b", re.I)
NO_RE = re.compile(r"^\s*(no|n|false)\b", re.I)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _edu(attr: str) -> Callable[[Profile], str]:
    return lambda p: getattr(p.primary_education, attr)


def _grad_year(p: Profile) -> str:
    m = re.search(r"(20\d\d)", p.primary_education.graduation_date)
    return m.group(1) if m else ""


# (label regex, key regex, resolver). First match wins, so specific rules come first.
Rule = tuple[Optional[str], Optional[str], Callable[[Profile], str]]
RULES: list[Rule] = [
    (r"preferred (first )?name|nickname", r"preferred_name", lambda p: p.preferred_name or p.first_name),
    (r"first name|given name", r"^first_?name$", lambda p: p.first_name),
    (r"last name|family name|surname", r"^last_?name$", lambda p: p.last_name),
    (r"^(full |legal )?name$|full name|legal name|your name", r"^name$", lambda p: p.full_name),
    (r"e ?mail", r"^email$", lambda p: p.email),
    (r"phone|mobile|cell", r"^phone$", lambda p: p.phone),
    (r"linkedin", r"linkedin", lambda p: p.linkedin),
    (r"github", r"github", lambda p: p.github),
    (r"portfolio|personal (web)?site|^website|website url|other website", r"portfolio|website", lambda p: p.website),
    (r"pronoun", None, lambda p: p.pronouns),
    (r"gpa|grade point", None, _edu("gpa")),
    (r"graduation year|year of graduation|class year|grad year", None, _grad_year),
    (r"graduat|grad date|completion date", None, _edu("graduation_date")),
    (r"^(school|university|college|institution)( name)?$|which (school|university)|current (school|university)|"
     r"name of (school|university|college)|university attended|school attended", r"^school", _edu("school")),
    (r"^degree|degree (type|level)|highest degree|degree pursuing|pursuing (a|your) degree", r"^degree", _edu("degree")),
    (r"major|discipline|field of study|area of study|concentration", r"discipline|major", _edu("major")),
    (r"minor", None, _edu("minor")),
    (r"current (company|employer)|most recent (company|employer)", r"^org$", lambda p: p.current_experience.company),
    (r"current (title|role|position)|most recent title", None, lambda p: p.current_experience.title),
    # "Will you require sponsorship?" before "authorized to work", but "authorized to work
    # without sponsorship" is an authorization question, so the bare-sponsorship rule comes after.
    (r"(require|need).{0,60}(sponsor|visa)", None, lambda p: p.needs_sponsorship),
    (r"(legally )?(authori[sz]ed|eligible|permitted) to work|work authori[sz]ation|right to work", None,
     lambda p: p.work_authorized),
    (r"sponsor", None, lambda p: p.needs_sponsorship),
    (r"relocat", None, lambda p: p.willing_to_relocate),
    (r"start date|available to start|earliest (start|availability)|when can you start", None, lambda p: p.earliest_start),
    (r"how did you (hear|find|learn)|where did you (hear|find|learn)|referral source|source of application", None,
     lambda p: p.how_did_you_hear),
    (r"hispanic|latin[oax]", r"hispanic", lambda p: p.hispanic_latino),
    (r"\brace\b|ethnicity", r"^race|eeo\[race\]", lambda p: p.race),
    (r"gender|\bsex\b", r"gender", lambda p: p.gender),
    (r"veteran", r"veteran", lambda p: p.veteran_status),
    (r"disabilit", r"disabilit", lambda p: p.disability_status),
    (r"^(current )?(location|city)|where are you (located|based)|current (city|location)|city.*state|"
     r"location \(city\)", r"^location$", lambda p: p.location),
    (r"address", None, lambda p: p.address),
]
_COMPILED = [(re.compile(l, re.I) if l else None, re.compile(k, re.I) if k else None, fn) for l, k, fn in RULES]


def profile_value(field: FormField, profile: Profile) -> str:
    label = normalize(field.label)
    for label_re, key_re, fn in _COMPILED:
        if (label_re and label_re.search(label)) or (key_re and key_re.search(field.key)):
            return (fn(profile) or "").strip()
    return ""


def match_option(value: str, options: list[str]) -> Optional[str]:
    """Pick the option that best represents ``value``. Returns None if nothing fits."""
    if not value or not options:
        return None
    nv = normalize(value)
    by_norm = {normalize(o): o for o in options}
    if nv in by_norm:
        return by_norm[nv]
    if DECLINE_RE.search(value):
        for o in options:
            if DECLINE_RE.search(o):
                return o
        return None
    if YES_RE.match(value):
        return next((o for o in options if YES_RE.match(o)), None)
    if NO_RE.match(value):
        return next((o for o in options if NO_RE.match(o)), None)
    for o in options:
        no = normalize(o)
        if no and (no.startswith(nv) or nv.startswith(no)):
            return o
    close = difflib.get_close_matches(nv, list(by_norm), n=1, cutoff=0.75)
    return by_norm[close[0]] if close else None


def library_answer(field: FormField, library: list[SavedAnswer]) -> Optional[str]:
    label = normalize(field.label)
    if not label:
        return None
    best, best_score = None, 0.0
    for item in library:
        score = difflib.SequenceMatcher(None, label, normalize(item.question)).ratio()
        if score > best_score:
            best, best_score = item, score
    return best.answer if best and best_score >= 0.85 else None


def set_value(field: FormField, value: str, source: str) -> bool:
    """Store ``value`` (coerced to the field's options) and report whether it stuck."""
    if field.options:
        if field.type == "multiselect" or (field.type == "checkbox" and len(field.options) > 1):
            picks = [match_option(v.strip(), field.options) for v in value.split(";")]
            picks = [p for p in picks if p]
            if not picks:
                return False
            field.value = picks
        else:
            pick = match_option(value, field.options)
            if not pick:
                return False
            field.value = pick
    else:
        field.value = value
    field.source = source
    return True


def is_cover_letter(field: FormField) -> bool:
    return "cover" in normalize(field.label) or "cover_letter" in field.key


def fill_from_profile(fields: list[FormField], profile: Profile, library: list[SavedAnswer]) -> None:
    for f in fields:
        if not f.is_empty() and f.source == "user":
            continue
        if f.type == "file":
            label = normalize(f.label) + " " + f.key.lower()
            if is_cover_letter(f):
                f.value, f.source = COVER_TOKEN, "document"
            elif "resume" in label or re.search(r"\bcv\b", label):
                f.value, f.source = RESUME_TOKEN, "document"
            else:
                f.needs_review = True
                f.note = "Unrecognized upload; attach it in the browser window."
            continue
        if f.type == "textarea" and is_cover_letter(f):
            f.value, f.source = COVER_TOKEN, "document"
            continue
        saved = library_answer(f, library)
        if saved and set_value(f, saved, "library"):
            continue
        value = profile_value(f, profile)
        if value and set_value(f, value, "profile"):
            continue
