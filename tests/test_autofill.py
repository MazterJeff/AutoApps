import unittest

from autoapps import autofill
from autoapps.autofill import COVER_TOKEN, RESUME_TOKEN, match_option
from autoapps.forms import greenhouse, html_form
from autoapps.models import FormField, SavedAnswer

from .helpers import fixture, sample_profile


class AutofillTest(unittest.TestCase):
    def test_greenhouse_form(self):
        fields = greenhouse.parse(fixture("greenhouse_job_questions.json"))
        autofill.fill_from_profile(fields, sample_profile(), [])
        v = {f.key: f.value for f in fields}
        self.assertEqual(v["first_name"], "Ada")
        self.assertEqual(v["last_name"], "Lovelace")
        self.assertEqual(v["email"], "ada@uchicago.edu")
        self.assertEqual(v["resume"], RESUME_TOKEN)
        self.assertEqual(v["cover_letter"], COVER_TOKEN)
        self.assertEqual(v["question_111"], "https://linkedin.com/in/ada")
        self.assertEqual(v["question_112"], "Yes")  # authorized
        self.assertEqual(v["question_113"], "No")  # sponsorship
        self.assertEqual(v["location"], "Chicago, IL")
        self.assertEqual(v["gender"], "Decline To Self Identify")
        self.assertEqual(v["veteran_status"], "I don't wish to answer")
        self.assertIsNone(v["question_114"])  # essay is left for Claude

    def test_lever_form(self):
        fields = html_form.parse(fixture("lever_apply.html"))
        autofill.fill_from_profile(fields, sample_profile(), [])
        v = {f.key: f.value for f in fields}
        self.assertEqual(v["name"], "Ada Lovelace")
        self.assertEqual(v["org"], "Analytical Engines LLC")
        self.assertEqual(v["urls[GitHub]"], "https://github.com/ada")
        self.assertEqual(v["eeo[gender]"], "Decline to self-identify")
        self.assertEqual(v["comments"], COVER_TOKEN)  # "Add a cover letter or anything else"
        self.assertIsNone(v["cards[abc][field0]"])

    def test_authorized_without_sponsorship_is_authorization(self):
        f = FormField(key="q", label="Are you authorized to work in the US without sponsorship?", type="select", options=["Yes", "No"])
        autofill.fill_from_profile([f], sample_profile(), [])
        self.assertEqual(f.value, "Yes")

    def test_require_sponsorship(self):
        f = FormField(key="q", label="Do you need visa sponsorship now or in the future to be authorized to work?",
                      type="radio", options=["Yes", "No"])
        autofill.fill_from_profile([f], sample_profile(), [])
        self.assertEqual(f.value, "No")

    def test_library_wins_over_profile_and_handles_wording(self):
        lib = [SavedAnswer(question="How did you hear about this job?", answer="Career fair")]
        f = FormField(key="q", label="How did you hear about this job?*", type="text")
        autofill.fill_from_profile([f], sample_profile(), lib)
        self.assertEqual((f.value, f.source), ("Career fair", "library"))

    def test_unmatched_option_is_left_empty(self):
        f = FormField(key="gender", label="Gender", type="select", options=["Male", "Female"])
        autofill.fill_from_profile([f], sample_profile(), [])
        self.assertTrue(f.is_empty())

    def test_user_edits_are_not_overwritten(self):
        f = FormField(key="email", label="Email", value="other@x.com", source="user")
        autofill.fill_from_profile([f], sample_profile(), [])
        self.assertEqual(f.value, "other@x.com")

    def test_match_option(self):
        self.assertEqual(match_option("yes", ["No", "Yes, I am"]), "Yes, I am")
        self.assertEqual(match_option("Decline to self-identify", ["A", "I prefer not to say"]), "I prefer not to say")
        self.assertEqual(match_option("Computer Science", ["Computer Science & Engineering", "Biology"]), "Computer Science & Engineering")
        self.assertIsNone(match_option("Decline", ["Male", "Female"]))
        self.assertEqual(match_option("BS", ["BA", "BS", "PhD"]), "BS")


if __name__ == "__main__":
    unittest.main()
