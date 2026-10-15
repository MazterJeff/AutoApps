import unittest

from autoapps.browser import _Baseline


class FakePage:
    def __init__(self, url, text):
        self.url, self.text = url, text

    def inner_text(self, selector, timeout=None):
        return self.text


class ConfirmationTest(unittest.TestCase):
    def test_new_confirmation_text_counts(self):
        page = FakePage("https://x/jobs/1", "Apply for this job. First name...")
        baseline = _Baseline(page)
        self.assertFalse(baseline.confirmed(page))
        page.text = "Thank you for applying! We'll be in touch."
        self.assertTrue(baseline.confirmed(page))

    def test_boilerplate_in_description_is_not_a_confirmation(self):
        page = FakePage("https://x/jobs/1", "About us... Thanks for applying to Acme! First name...")
        baseline = _Baseline(page)
        self.assertFalse(baseline.confirmed(page))
        page.url = "https://x/jobs/1/confirmation"
        self.assertTrue(baseline.confirmed(page))

    def test_interest_boilerplate_ignored(self):
        self.assertFalse(_Baseline(FakePage("u", "Thank you for your interest in Acme")).text)


if __name__ == "__main__":
    unittest.main()
