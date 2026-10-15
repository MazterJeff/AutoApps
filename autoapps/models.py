"""Data models shared across the app."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

DECLINE = "Decline to self-identify"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    minor: str = ""
    gpa: str = ""
    start_date: str = ""
    graduation_date: str = ""


class Experience(BaseModel):
    company: str = ""
    title: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    summary: str = ""


class Profile(BaseModel):
    first_name: str = ""
    last_name: str = ""
    preferred_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""  # "Chicago, IL"
    address: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    summary: str = ""

    # Common screening questions. Leave blank if you want to answer per application.
    work_authorized: str = ""  # "Yes" / "No"
    needs_sponsorship: str = ""  # "Yes" / "No"
    willing_to_relocate: str = "Yes"
    earliest_start: str = ""
    how_did_you_hear: str = "Online job board"
    pronouns: str = ""

    # Voluntary self-identification. Defaults to declining.
    gender: str = DECLINE
    race: str = DECLINE
    hispanic_latino: str = DECLINE
    veteran_status: str = DECLINE
    disability_status: str = DECLINE

    # Free-form notes Claude should use when writing (projects to highlight, tone, ...).
    writing_notes: str = ""

    resume_filename: str = ""
    resume_text: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(p for p in [self.first_name, self.last_name] if p).strip()

    @property
    def primary_education(self) -> Education:
        return self.education[0] if self.education else Education()

    @property
    def current_experience(self) -> Experience:
        return self.experience[0] if self.experience else Experience()

    def missing_basics(self) -> list[str]:
        missing = []
        for attr, label in [
            ("first_name", "first name"),
            ("last_name", "last name"),
            ("email", "email"),
            ("phone", "phone"),
            ("work_authorized", "work authorization"),
            ("needs_sponsorship", "sponsorship"),
        ]:
            if not getattr(self, attr).strip():
                missing.append(label)
        if not self.resume_filename:
            missing.append("resume")
        return missing


JobStatus = Literal["new", "shortlisted", "dismissed", "queued", "applied"]


class Job(BaseModel):
    id: str
    source: str  # greenhouse | lever | ashby | simplify
    ats: str = "other"  # greenhouse | lever | ashby | other
    company: str
    title: str
    location: str = ""
    remote: bool = False
    url: str = ""
    apply_url: str = ""
    description: str = ""
    posted_at: Optional[str] = None
    ats_board: str = ""
    ats_job_id: str = ""
    sponsorship: str = ""
    terms: list[str] = Field(default_factory=list)
    status: JobStatus = "new"
    first_seen: str = Field(default_factory=now_iso)


FieldType = Literal[
    "text", "textarea", "email", "tel", "url", "number", "date",
    "select", "multiselect", "radio", "checkbox", "file",
]

FieldValue = Union[str, list[str], None]


class FormField(BaseModel):
    key: str
    label: str
    type: FieldType = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)
    description: str = ""
    selector: str = ""  # CSS selector hint for the browser filler
    value: FieldValue = None
    source: str = ""  # profile | library | ai | user | document
    needs_review: bool = False
    note: str = ""

    def is_empty(self) -> bool:
        if self.value is None:
            return True
        if isinstance(self.value, list):
            return not any(v.strip() for v in self.value)
        return not self.value.strip()

    @property
    def is_multi(self) -> bool:
        return self.type == "multiselect" or (self.type == "checkbox" and len(self.options) > 1)


AppStatus = Literal[
    "queued", "preparing", "ready", "approved", "submitting",
    "submitted", "needs_attention", "failed",
]


class Application(BaseModel):
    id: int = 0
    job_id: str
    status: AppStatus = "queued"
    fields: list[FormField] = Field(default_factory=list)
    cover_letter: str = ""
    error: str = ""
    log: list[str] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    submitted_at: Optional[str] = None

    def add_log(self, msg: str) -> None:
        self.log.append(f"{now_iso()}  {msg}")

    @property
    def review_count(self) -> int:
        return sum(1 for f in self.fields if f.needs_review or (f.required and f.is_empty()))


class SavedAnswer(BaseModel):
    question: str
    answer: str


class Settings(BaseModel):
    # Cover letters are always written when the form has a cover-letter field. With this on,
    # one is also written for forms that don't ask (so you can download it).
    always_cover_letter: bool = False
    cover_letter_words: int = 300
    submit_mode: Literal["review", "auto"] = "review"
    headless: bool = False
    notify_email: str = ""
    manual_wait_minutes: int = 15
