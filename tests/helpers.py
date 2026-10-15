import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from autoapps.config import Config
from autoapps.models import Education, Experience, Profile

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    text = (FIXTURES / name).read_text()
    return json.loads(text) if name.endswith(".json") else text


def temp_config() -> Config:
    tmp = tempfile.mkdtemp(prefix="autoapps-test-")
    cfg = Config(data_dir=Path(tmp))
    cfg.ensure_dirs()
    return cfg


def sample_profile(**kw) -> Profile:
    base = dict(
        first_name="Ada", last_name="Lovelace", email="ada@uchicago.edu", phone="312-555-0100",
        location="Chicago, IL", linkedin="https://linkedin.com/in/ada", github="https://github.com/ada",
        education=[Education(school="University of Chicago", degree="BS", major="Computer Science", gpa="3.9",
                             graduation_date="June 2028")],
        experience=[Experience(company="Analytical Engines LLC", title="Research Assistant")],
        skills=["Python", "SQL", "React", "PyTorch"],
        work_authorized="Yes", needs_sponsorship="No",
        resume_filename="Ada_Lovelace_Resume.pdf", resume_text="Ada Lovelace\nPython, SQL\nBuilt a compiler.",
    )
    base.update(kw)
    return Profile(**base)


class FakeLLM:
    """Stands in for autoapps.llm.LLM so tests never call the API."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def answer_questions(self, profile, job, fields):
        self.calls.append(("answer", [f.key for f in fields]))
        out = {}
        for f in fields:
            if f.key in self.answers:
                out[f.key] = self.answers[f.key]
            else:
                out[f.key] = {"key": f.key, "value": "", "values": [], "confidence": "low", "note": "Need your input."}
        return out

    def write_cover_letter(self, profile, job, words=300, instructions=""):
        self.calls.append(("letter", instructions))
        return f"Dear Hiring Team,\n\nI want to work at {job.company}.\n\nSincerely,\n{profile.full_name}"

    def revise_cover_letter(self, profile, job, current, feedback):
        self.calls.append(("revise_letter", feedback))
        return current + f"\n\n[revised: {feedback}]"

    def revise_answer(self, profile, job, field, current, feedback):
        self.calls.append(("revise_answer", field.key))
        return f"{current} (revised)"

    def extract_profile(self, resume_text):
        self.calls.append(("extract",))
        return {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@uchicago.edu", "skills": ["Python"]}


def fake_message(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, stop_details=None,
                           content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])
