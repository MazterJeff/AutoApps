import unittest
from pathlib import Path

from autoapps.autofill import COVER_TOKEN, RESUME_TOKEN
from autoapps.browser import SubmitResult
from autoapps.forms import FormResult, greenhouse
from autoapps.models import Settings
from autoapps.pipeline import Service
from autoapps.sources import greenhouse as gh_source

from .helpers import FakeLLM, fixture, sample_profile, temp_config


class RecordingSubmitter:
    def __init__(self, status="submitted"):
        self.status = status
        self.calls = []

    def __call__(self, url, fields, values, files, **kw):
        self.calls.append(dict(url=url, fields=fields, values=values, files=files, **kw))
        return SubmitResult(self.status, "done", problems=["Phone: field not found on page"])


def make_service(answers=None, submitter=None):
    cfg = temp_config()
    llm = FakeLLM(answers)
    form = lambda job: FormResult(fields=greenhouse.parse(fixture("greenhouse_job_questions.json")),
                                  description="Full description", notes=["fixture form"])
    svc = Service(cfg, llm=llm, form_loader=form, submitter=submitter or RecordingSubmitter(), background=False)
    svc.store.upsert_jobs(gh_source.parse("acme", fixture("greenhouse_jobs.json")))
    profile = sample_profile()
    svc.resume_dir.mkdir(parents=True, exist_ok=True)
    (svc.resume_dir / profile.resume_filename).write_bytes(b"%PDF-1.4 fake")
    svc.store.save_profile(profile)
    return svc, llm


JOB_ID = "greenhouse:acme:7001"
ESSAY = {"question_114": {"key": "question_114", "value": "I like Acme's developer tools.", "values": [],
                          "confidence": "medium", "note": "Check tone."},
         "question_115[]": {"key": "question_115[]", "value": "", "values": ["Python", "Go"], "confidence": "high", "note": ""}}


class PipelineTest(unittest.TestCase):
    def test_prepare_fills_profile_ai_and_cover_letter(self):
        svc, llm = make_service(ESSAY)
        [app] = svc.queue([JOB_ID])
        app = svc.store.get_application(app.id)
        self.assertEqual(app.status, "ready", app.log)
        by_key = {f.key: f for f in app.fields}
        self.assertEqual(by_key["first_name"].source, "profile")
        self.assertEqual(by_key["question_114"].value, "I like Acme's developer tools.")
        self.assertTrue(by_key["question_114"].needs_review)  # medium confidence
        self.assertEqual(by_key["question_115[]"].value, ["Python", "Go"])
        self.assertFalse(by_key["question_115[]"].needs_review)
        self.assertIn("Acme", app.cover_letter)
        # only questions the profile couldn't answer went to Claude
        asked = next(c[1] for c in llm.calls if c[0] == "answer")
        self.assertEqual(sorted(asked), ["question_114", "question_115[]"])
        self.assertEqual(svc.store.get_job(JOB_ID).status, "queued")

    def test_unanswered_required_question_is_flagged(self):
        svc, _ = make_service()
        [app] = svc.queue([JOB_ID])
        app = svc.store.get_application(app.id)
        essay = next(f for f in app.fields if f.key == "question_114")
        self.assertTrue(essay.is_empty())
        self.assertTrue(essay.needs_review)
        self.assertIn("question_114", [f.key for f in app.fields if f.required and f.is_empty()])
        self.assertIn("Required answers are empty", " ".join(svc.submit_blockers(app, "auto")))
        self.assertEqual(svc.submit_blockers(app, "review"), [])

    def test_regenerate_keeps_user_edits(self):
        svc, _ = make_service(ESSAY)
        [app] = svc.queue([JOB_ID])
        app = svc.store.get_application(app.id)
        essay = next(f for f in app.fields if f.key == "question_114")
        essay.value, essay.source = "My own words.", "user"
        svc.store.save_application(app)
        app = svc.prepare(app.id)
        self.assertEqual(next(f for f in app.fields if f.key == "question_114").value, "My own words.")

    def test_submit_passes_files_and_letter_and_marks_applied(self):
        submitter = RecordingSubmitter()
        svc, _ = make_service(ESSAY, submitter)
        [app] = svc.queue([JOB_ID])
        svc.store.save_settings(Settings(manual_wait_minutes=2))
        app = svc.submit(app.id, "review")
        self.assertEqual(app.status, "submitted")
        self.assertIsNotNone(app.submitted_at)
        self.assertEqual(svc.store.get_job(JOB_ID).status, "applied")
        call = submitter.calls[0]
        self.assertEqual(call["url"], "https://job-boards.greenhouse.io/acme/jobs/7001")
        self.assertFalse(call["auto_submit"])
        self.assertEqual(call["manual_wait_seconds"], 120)
        self.assertTrue(call["files"][RESUME_TOKEN].name.endswith("Resume.pdf"))
        letter_pdf = call["files"][COVER_TOKEN]
        self.assertTrue(letter_pdf.read_bytes().startswith(b"%PDF"))
        self.assertEqual(call["values"]["cover_letter"], COVER_TOKEN)  # file field: uploaded as PDF
        self.assertIn("Phone: field not found on page", " ".join(app.log))

    def test_needs_attention_and_manual_mark(self):
        svc, _ = make_service(ESSAY, RecordingSubmitter("needs_attention"))
        [app] = svc.queue([JOB_ID])
        app = svc.submit(app.id, "auto")
        self.assertEqual(app.status, "needs_attention")
        self.assertNotEqual(svc.store.get_job(JOB_ID).status, "applied")
        app = svc.mark_submitted(app.id)
        self.assertEqual(svc.store.get_job(JOB_ID).status, "applied")

    def test_revise_and_remember(self):
        svc, llm = make_service(ESSAY)
        [app] = svc.queue([JOB_ID])
        app = svc.revise_cover_letter(app.id, "shorter")
        self.assertIn("[revised: shorter]", app.cover_letter)
        app = svc.revise_answer(app.id, "question_114", "more specific")
        self.assertTrue(next(f for f in app.fields if f.key == "question_114").value.endswith("(revised)"))
        svc.remember_answers(app, ["question_114"])
        lib = svc.store.get_answer_library()
        self.assertEqual(lib[0].question, "Why are you interested in Acme?")

    def test_description_backfilled_from_form(self):
        svc, _ = make_service(ESSAY)
        svc.store.upsert_jobs([svc.store.get_job(JOB_ID).model_copy(update={"description": ""})])
        svc.queue([JOB_ID])
        self.assertTrue(svc.store.get_job(JOB_ID).description)

    def test_upsert_preserves_status_and_description(self):
        svc, _ = make_service()
        svc.store.set_job_status([JOB_ID], "shortlisted")
        job = svc.store.get_job(JOB_ID)
        new, updated = svc.store.upsert_jobs([job.model_copy(update={"description": "", "status": "new"})])
        self.assertEqual((new, updated), (0, 1))
        job = svc.store.get_job(JOB_ID)
        self.assertEqual(job.status, "shortlisted")
        self.assertTrue(job.description)


if __name__ == "__main__":
    unittest.main()
