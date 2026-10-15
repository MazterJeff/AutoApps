import unittest

from autoapps.forms import greenhouse, html_form

from .helpers import fixture


class GreenhouseFormTest(unittest.TestCase):
    def setUp(self):
        self.fields = {f.key: f for f in greenhouse.parse(fixture("greenhouse_job_questions.json"))}

    def test_types_and_options(self):
        f = self.fields
        self.assertEqual(f["email"].type, "email")
        self.assertEqual(f["phone"].type, "tel")
        self.assertEqual(f["question_112"].type, "select")
        self.assertEqual(f["question_112"].options, ["Yes", "No"])
        self.assertEqual(f["question_115[]"].type, "multiselect")
        self.assertEqual(f["question_114"].description, "2-3 sentences")
        self.assertTrue(f["question_114"].required)

    def test_upload_preferred_over_paste_text_and_hidden_skipped(self):
        self.assertEqual(self.fields["resume"].type, "file")
        self.assertNotIn("resume_text", self.fields)
        self.assertNotIn("cover_letter_text", self.fields)
        self.assertNotIn("question_116", self.fields)

    def test_location_and_compliance_included(self):
        self.assertIn("location", self.fields)
        self.assertEqual(self.fields["gender"].options[-1], "Decline To Self Identify")


class HtmlFormTest(unittest.TestCase):
    def setUp(self):
        self.fields = html_form.parse(fixture("lever_apply.html"))
        self.by_key = {f.key: f for f in self.fields}

    def test_labels(self):
        b = self.by_key
        self.assertEqual(b["name"].label, "Full name")
        self.assertTrue(b["name"].required)
        self.assertFalse(b["phone"].required)
        self.assertEqual(b["urls[LinkedIn]"].label, "LinkedIn URL")
        self.assertEqual(b["resume"].type, "file")
        self.assertTrue(b["resume"].required)

    def test_hidden_inputs_skipped(self):
        self.assertNotIn("cards[abc][baseTemplate]", self.by_key)

    def test_radio_and_checkbox_groups(self):
        radio = self.by_key["cards[abc][field0]"]
        self.assertEqual(radio.type, "radio")
        self.assertEqual(radio.label, "Are you currently enrolled in a degree program?")
        self.assertEqual(radio.options, ["Yes", "No"])
        self.assertTrue(radio.required)
        multi = self.by_key["cards[abc][field1]"]
        self.assertEqual(multi.options, ["Excel", "Python"])
        self.assertTrue(multi.is_multi)

    def test_select_textarea_and_single_checkbox(self):
        self.assertEqual(self.by_key["cards[abc][field3]"].options, ["Junior", "Senior"])
        self.assertEqual(self.by_key["cards[abc][field2]"].type, "textarea")
        consent = self.by_key["consent[marketing]"]
        self.assertEqual(consent.type, "checkbox")
        self.assertEqual(consent.options, [])
        self.assertIn("agree", consent.label)
        self.assertEqual(self.by_key["eeo[gender]"].label, "Gender")


if __name__ == "__main__":
    unittest.main()
