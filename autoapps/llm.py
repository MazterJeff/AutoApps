"""Claude-powered drafting: resume parsing, question answering, cover letters."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .config import Config
from .models import FormField, Job, Profile

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "first_name", "last_name", "email", "phone", "location", "linkedin", "github",
        "website", "education", "experience", "skills", "summary",
    ],
    "properties": {
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "location": {"type": "string"},
        "linkedin": {"type": "string"},
        "github": {"type": "string"},
        "website": {"type": "string"},
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["school", "degree", "major", "minor", "gpa", "start_date", "graduation_date"],
                "properties": {k: {"type": "string"} for k in
                               ["school", "degree", "major", "minor", "gpa", "start_date", "graduation_date"]},
            },
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["company", "title", "location", "start_date", "end_date", "summary"],
                "properties": {k: {"type": "string"} for k in
                               ["company", "title", "location", "start_date", "end_date", "summary"]},
            },
        },
        "skills": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
}

ANSWERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answers"],
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "value", "values", "confidence", "note"],
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "values": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                },
            },
        }
    },
}

APPLICANT_RULES = """You are helping a student apply to internships. You write on their behalf, in their voice.

Ground rules:
- Use only facts present in the applicant's resume and profile. Never invent employers, projects, \
metrics, coursework, awards, or skills. If something is not supported, say less rather than make it up.
- Write plainly and specifically, like a capable student: no clichés ("I am writing to express my \
interest", "passionate", "synergy"), no exaggerated enthusiasm, no placeholder brackets.
- The applicant reviews everything before it is submitted, so when you cannot answer something \
truthfully from what you know, leave it blank and explain what is missing in the note."""


def _profile_block(profile: Profile) -> str:
    data = profile.model_dump(exclude={"resume_text", "resume_filename"})
    return (
        "<applicant_profile>\n" + json.dumps(data, indent=2) + "\n</applicant_profile>\n\n"
        "<resume>\n" + (profile.resume_text or "(no resume uploaded)") + "\n</resume>"
    )


def _job_block(job: Job, max_chars: int = 15000) -> str:
    desc = job.description or "(no description available)"
    return (
        f"<job>\nCompany: {job.company}\nTitle: {job.title}\nLocation: {job.location}\n"
        f"URL: {job.url}\n\nDescription:\n{desc[:max_chars]}\n</job>"
    )


class LLM:
    def __init__(self, config: Config, client: Any = None):
        self.config = config
        self._client = client
        self._fallbacks = config.llm_fallbacks

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on install
                raise LLMError("The 'anthropic' package is not installed. Run: pip install -r requirements.txt") from exc
            # Resolves ANTHROPIC_API_KEY (or an `ant auth login` profile) from the environment.
            self._client = anthropic.Anthropic()
        return self._client

    # ----- low-level ------------------------------------------------------
    def _call(self, system: list[dict], user: str, *, schema: Optional[dict] = None,
              max_tokens: int = 16000, effort: str = "medium") -> str:
        output_config: dict[str, Any] = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        params: dict[str, Any] = dict(
            model=self.config.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        try:
            response = self._send(params)
        except LLMError:
            raise
        except Exception as exc:  # auth, rate limit, network: surface to the UI as one error type
            raise LLMError(f"Claude API error: {exc}") from exc
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMError(f"Claude declined this request ({getattr(details, 'category', None) or 'refusal'}).")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's response was cut off (max_tokens). Try again or shorten the input.")
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise LLMError("Claude returned an empty response.")
        return text

    def _send(self, params: dict[str, Any]):
        if self._fallbacks:
            try:
                return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
            except Exception as exc:
                # Some providers/accounts reject server-side fallbacks; retry once without them.
                if "fallback" not in str(exc).lower():
                    raise
                log.warning("Server-side fallbacks unavailable (%s); continuing without them.", exc)
                self._fallbacks = False
        return self.client.messages.create(**params)

    def _system(self, profile: Optional[Profile] = None) -> list[dict]:
        blocks: list[dict] = [{"type": "text", "text": APPLICANT_RULES}]
        if profile is not None:
            # Profile + resume are identical across applications, so cache them.
            blocks.append({"type": "text", "text": _profile_block(profile), "cache_control": {"type": "ephemeral"}})
        return blocks

    # ----- tasks ----------------------------------------------------------
    def extract_profile(self, resume_text: str) -> dict:
        system = [{"type": "text", "text": (
            "Extract structured data from a resume. Copy values exactly as written; use empty strings "
            "for anything not present. List the most recent education and experience first. "
            "'skills' should be short tokens (languages, frameworks, tools). 'summary' is two sentences "
            "describing the candidate, written in first person."
        )}]
        text = self._call(system, f"<resume>\n{resume_text}\n</resume>", schema=PROFILE_SCHEMA, effort="low")
        return json.loads(text)

    def answer_questions(self, profile: Profile, job: Job, fields: list[FormField]) -> dict[str, dict]:
        if not fields:
            return {}
        questions = []
        for f in fields:
            q: dict[str, Any] = {"key": f.key, "question": f.label, "type": f.type, "required": f.required}
            if f.description:
                q["help_text"] = f.description[:1000]
            if f.options:
                q["options"] = f.options
            questions.append(q)
        user = (
            _job_block(job)
            + "\n\nAnswer these application form questions for the applicant.\n"
            + "<questions>\n" + json.dumps(questions, indent=2) + "\n</questions>\n\n"
            "Rules for each answer:\n"
            "- For questions with options, 'value' must be copied exactly from the options "
            "(for multi-select, put every chosen option in 'values'). Otherwise leave 'values' empty.\n"
            "- Short factual fields get short answers. Essay questions get a focused answer of "
            "roughly 100-200 words unless the question asks for a length.\n"
            "- Demographic, salary, or legal questions you can't answer from the profile: leave "
            "'value' empty, confidence 'low', and say what's needed in 'note'.\n"
            "- confidence is 'high' only when the profile/resume directly supports the answer.\n"
            "Return one entry per question key."
        )
        data = json.loads(self._call(self._system(profile), user, schema=ANSWERS_SCHEMA))
        return {a["key"]: a for a in data.get("answers", [])}

    def write_cover_letter(self, profile: Profile, job: Job, words: int = 300, instructions: str = "") -> str:
        user = (
            _job_block(job)
            + f"\n\nWrite a cover letter for this role, about {words} words. Plain text only: "
            "a salutation (\"Dear Hiring Team,\" unless a name is given), 3-4 short paragraphs, and a sign-off "
            "with the applicant's name. Connect two or three concrete things from the resume to what this "
            "team needs. Mention the company by name and something specific about the role."
        )
        if profile.writing_notes:
            user += f"\n\nApplicant's notes on what to emphasize:\n{profile.writing_notes}"
        if instructions:
            user += f"\n\nAdditional instructions from the applicant:\n{instructions}"
        user += "\n\nReturn only the letter text."
        return self._call(self._system(profile), user, max_tokens=8000)

    def revise_cover_letter(self, profile: Profile, job: Job, current: str, feedback: str) -> str:
        user = (
            _job_block(job)
            + "\n\nHere is the current draft of the applicant's cover letter:\n<draft>\n" + current + "\n</draft>\n\n"
            "Revise it according to the applicant's feedback below. Keep what they didn't ask to change.\n"
            f"<feedback>\n{feedback}\n</feedback>\n\nReturn only the revised letter text."
        )
        return self._call(self._system(profile), user, max_tokens=8000)

    def revise_answer(self, profile: Profile, job: Job, field: FormField, current: str, feedback: str) -> str:
        user = (
            _job_block(job)
            + f"\n\nApplication question: {field.label}\n"
            + (f"Help text: {field.description}\n" if field.description else "")
            + f"Current answer:\n<answer>\n{current}\n</answer>\n\n"
            f"Revise the answer according to this feedback:\n<feedback>\n{feedback}\n</feedback>\n\n"
            "Return only the revised answer text."
        )
        return self._call(self._system(profile), user, max_tokens=4000)
