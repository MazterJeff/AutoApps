import unittest

from autoapps.sources import ashby, greenhouse, lever, simplify
from autoapps.sources.common import detect_ats, html_to_text, is_internship

from .helpers import fixture


class SourceParsingTest(unittest.TestCase):
    def test_greenhouse_keeps_internships_and_canonical_apply_url(self):
        jobs = greenhouse.parse("acme", fixture("greenhouse_jobs.json"))
        self.assertEqual([j.title for j in jobs], ["Software Engineering Intern, Summer 2027"])
        job = jobs[0]
        self.assertEqual(job.id, "greenhouse:acme:7001")
        self.assertEqual(job.company, "Acme")
        self.assertEqual(job.apply_url, "https://job-boards.greenhouse.io/acme/jobs/7001")
        self.assertIn("Python", job.description)
        self.assertNotIn("<strong>", job.description)
        self.assertTrue(job.posted_at.startswith("2026-09-28T13:00"))

    def test_greenhouse_all_jobs_when_not_filtering(self):
        self.assertEqual(len(greenhouse.parse("acme", fixture("greenhouse_jobs.json"), internships_only=False)), 2)

    def test_lever(self):
        jobs = lever.parse("widgets", fixture("lever_postings.json"))
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.apply_url, "https://jobs.lever.co/widgets/aaaa-1111/apply")
        self.assertIn("San Francisco, CA; Remote", job.location)
        self.assertTrue(job.remote)
        self.assertIn("SQL", job.description)
        self.assertEqual(job.terms, ["Internship"])

    def test_ashby_uses_employment_type_and_skips_unlisted(self):
        jobs = ashby.parse("gizmo", fixture("ashby_board.json"))
        self.assertEqual([j.ats_job_id for j in jobs], ["c0ffee-1"])
        self.assertTrue(jobs[0].remote)

    def test_simplify_merges_ids_with_direct_boards(self):
        jobs = simplify.parse("SimplifyJobs/Summer2027-Internships", fixture("simplify_listings.json"))
        self.assertEqual(len(jobs), 2)  # inactive one dropped
        gh, wd = jobs
        self.assertEqual(gh.id, "greenhouse:acme:7001")
        self.assertEqual(gh.ats, "greenhouse")
        self.assertEqual(wd.ats, "other")
        self.assertTrue(wd.id.startswith("simplify:"))
        self.assertEqual(wd.sponsorship, "U.S. Citizenship is Required")

    def test_detect_ats(self):
        self.assertEqual(detect_ats("https://boards.greenhouse.io/stripe/jobs/123?gh_src=x"), ("greenhouse", "stripe", "123"))
        self.assertEqual(detect_ats("https://boards.greenhouse.io/embed/job_app?for=figma&token=55"), ("greenhouse", "figma", "55"))
        self.assertEqual(detect_ats("https://jobs.lever.co/palantir/abc-def"), ("lever", "palantir", "abc-def"))
        self.assertEqual(detect_ats("https://jobs.ashbyhq.com/openai/xyz/application"), ("ashby", "openai", "xyz"))
        self.assertEqual(detect_ats("https://example.com/jobs/1")[0], "other")

    def test_is_internship(self):
        for title in ["SWE Intern", "Software Engineering Internship", "Co-op, Hardware", "Summer Analyst 2027"]:
            self.assertTrue(is_internship(title), title)
        for title in ["Internal Tools Engineer", "International Sales Manager", "Staff Engineer"]:
            self.assertFalse(is_internship(title), title)

    def test_html_to_text(self):
        self.assertEqual(html_to_text("&lt;p&gt;a&amp;amp;b&lt;/p&gt;"), "a&b")


if __name__ == "__main__":
    unittest.main()
