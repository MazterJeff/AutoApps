import socket
import threading
import time
import unittest

import requests
import uvicorn

from autoapps.web.app import create_app

from .helpers import sample_profile
from .test_pipeline import ESSAY, JOB_ID, RecordingSubmitter, make_service


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.submitter = RecordingSubmitter()
        cls.svc, cls.llm = make_service(ESSAY, cls.submitter)
        port = free_port()
        cls.base = f"http://127.0.0.1:{port}"
        cls.server = uvicorn.Server(uvicorn.Config(create_app(cls.svc, cls.svc.config), host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        for _ in range(100):
            try:
                requests.get(cls.base + "/static/style.css", timeout=1)
                break
            except requests.ConnectionError:
                time.sleep(0.05)
        cls.http = requests.Session()
        cls.http.trust_env = False

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True

    def get(self, path, **kw):
        r = self.http.get(self.base + path, **kw)
        self.assertEqual(r.status_code, 200, r.text[:500])
        return r

    def post(self, path, data=None, **kw):
        r = self.http.post(self.base + path, data=data, **kw)
        self.assertLess(r.status_code, 400, r.text[:500])
        return r

    def test_1_jobs_page_and_filters(self):
        r = self.get("/")
        self.assertIn("Software Engineering Intern, Summer 2027", r.text)
        r = self.get("/", params={"applied": 1, "keywords": "rust", "statuses": ["new"], "internships_only": 1})
        self.assertIn("No jobs match", r.text)
        r = self.get("/?reset=1")
        self.assertIn("Summer 2027", r.text)
        self.get(f"/jobs/{JOB_ID}")

    def test_2_prepare_review_and_submit(self):
        r = self.post("/jobs/action", {"job_ids": JOB_ID, "action": "prepare", "qs": ""})
        self.assertIn("/applications", r.url)
        app = self.svc.store.get_application_for_job(JOB_ID)
        self.assertEqual(app.status, "ready")
        page = self.get(f"/applications/{app.id}").text
        self.assertIn("Why are you interested in Acme?", page)
        self.assertIn("Dear Hiring Team", page)

        idx = next(i for i, f in enumerate(app.fields) if f.key == "question_114")
        multi = next(i for i, f in enumerate(app.fields) if f.key == "question_115[]")
        data = {f"field-{idx}": "Edited by me.", f"field-{multi}": ["Rust"], "remember": str(idx),
                "cover_letter": "Dear Acme team,\n\nHello.\n\nAda", "action": "save"}
        self.post(f"/applications/{app.id}/save", data)
        app = self.svc.store.get_application(app.id)
        essay = app.fields[idx]
        self.assertEqual((essay.value, essay.source), ("Edited by me.", "user"))
        self.assertEqual(app.fields[multi].value, ["Rust"])
        self.assertTrue(app.cover_letter.startswith("Dear Acme team"))
        self.assertEqual(self.svc.store.get_answer_library()[0].answer, "Edited by me.")

        self.post(f"/applications/{app.id}/save", {"letter_feedback": "warmer", "action": "revise_letter"})
        self.assertIn("[revised: warmer]", self.svc.store.get_application(app.id).cover_letter)

        pdf = self.get(f"/applications/{app.id}/cover-letter.pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        docx = self.get(f"/applications/{app.id}/cover-letter.docx")
        self.assertTrue(docx.content.startswith(b"PK"))

        self.post(f"/applications/{app.id}/save", {"action": "submit_review"})
        for _ in range(100):
            if self.svc.store.get_application(app.id).status == "submitted":
                break
            time.sleep(0.05)
        self.assertEqual(self.svc.store.get_application(app.id).status, "submitted")
        self.assertEqual(self.submitter.calls[-1]["values"]["question_114"], "Edited by me.")
        self.assertIn("Submitted", self.get("/applications").text)

    def test_3_profile_upload_and_save(self):
        files = {"resume": ("Ada_Resume.txt", b"Ada Lovelace\nada@uchicago.edu\nPython", "text/plain")}
        self.post("/profile/resume", {"autofill": "1"}, files=files)
        profile = self.svc.store.get_profile()
        self.assertEqual(profile.resume_filename, "Ada_Resume.txt")
        self.assertIn("Python", profile.resume_text)
        self.assertTrue((self.svc.resume_dir / "Ada_Resume.txt").exists())

        form = {"first_name": "Ada", "last_name": "King", "email": "ada@uchicago.edu", "phone": "1",
                "skills": "Python, Go\nRust", "edu-0-school": "UChicago", "edu-0-degree": "BS", "edu-0-major": "CS",
                "edu-0-minor": "", "edu-0-gpa": "", "edu-0-start_date": "", "edu-0-graduation_date": "2028",
                "edu-1-school": "", "edu-1-degree": "", "edu-1-major": "", "edu-1-minor": "", "edu-1-gpa": "",
                "edu-1-start_date": "", "edu-1-graduation_date": "", "work_authorized": "Yes", "needs_sponsorship": "No"}
        self.post("/profile", form)
        profile = self.svc.store.get_profile()
        self.assertEqual(profile.last_name, "King")
        self.assertEqual(profile.skills, ["Python", "Go", "Rust"])
        self.assertEqual([e.school for e in profile.education], ["UChicago"])
        self.assertIn("UChicago", self.get("/profile").text)
        self.post("/profile/answers", {"q-0": "Pronouns?", "a-0": "she/her", "q-1": "", "a-1": ""})
        self.assertEqual([a.question for a in self.svc.store.get_answer_library()], ["Pronouns?"])
        self.svc.store.save_profile(sample_profile())

    def test_4_sources_and_settings(self):
        self.post("/sources", {"greenhouse": "stripe\nfigma", "lever": "", "ashby": "openai", "simplify": "", "internships_only": "1"})
        cfg = self.svc.source_config()
        self.assertEqual(cfg.greenhouse, ["stripe", "figma"])
        self.assertEqual(cfg.lever, [])
        self.post("/settings", {"submit_mode": "auto", "cover_letter_words": "250", "manual_wait_minutes": "5"})
        s = self.svc.store.get_settings()
        self.assertEqual((s.submit_mode, s.cover_letter_words, s.always_cover_letter), ("auto", 250, False))
        self.get("/settings")
        self.get("/sources")


if __name__ == "__main__":
    unittest.main()
