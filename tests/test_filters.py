import unittest
from datetime import datetime, timedelta, timezone

from autoapps.filters import JobFilter, apply_filter, relevance
from autoapps.models import Job

from .helpers import sample_profile


def job(**kw):
    base = dict(id=kw.get("title", "x"), source="greenhouse", company="Acme", title="Software Engineering Intern",
                location="New York, NY", description="Python and SQL", posted_at=datetime.now(timezone.utc).isoformat())
    base.update(kw)
    return Job(**base)


class FilterTest(unittest.TestCase):
    def test_keywords_and_exclusion(self):
        f = JobFilter(keywords="python -java")
        self.assertTrue(f.matches(job()))
        self.assertFalse(f.matches(job(description="Python and Java")))
        self.assertFalse(JobFilter(keywords="rust").matches(job()))

    def test_title_location_company(self):
        self.assertTrue(JobFilter(title="data, software").matches(job()))
        self.assertFalse(JobFilter(title="hardware").matches(job()))
        self.assertTrue(JobFilter(location="chicago, new york").matches(job()))
        self.assertTrue(JobFilter(location="remote").matches(job(location="Anywhere", remote=True)))
        self.assertFalse(JobFilter(exclude_companies="acme").matches(job()))
        self.assertFalse(JobFilter(companies="globex").matches(job()))

    def test_status_age_and_sponsorship(self):
        self.assertFalse(JobFilter().matches(job(status="dismissed")))
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        self.assertFalse(JobFilter(posted_within_days=30).matches(job(posted_at=old)))
        self.assertFalse(JobFilter(hide_no_sponsorship=True).matches(job(sponsorship="U.S. Citizenship is Required")))
        self.assertTrue(JobFilter(hide_no_sponsorship=True).matches(job(sponsorship="Offers Sponsorship")))

    def test_internships_only(self):
        self.assertFalse(JobFilter().matches(job(title="Staff Engineer")))
        self.assertTrue(JobFilter(internships_only=False).matches(job(title="Staff Engineer")))

    def test_relevance_and_sort(self):
        p = sample_profile()  # Python, SQL, React, PyTorch
        self.assertEqual(relevance(job(description="nothing"), p), 0)
        self.assertEqual(relevance(job(description="python sql react pytorch"), p), 100)
        jobs = [job(id="a", title="A Intern", description="python"), job(id="b", title="B Intern", description="python sql react")]
        ranked = apply_filter(jobs, JobFilter(sort="score"), p)
        self.assertEqual([j.id for j, _ in ranked], ["b", "a"])
        self.assertEqual(len(apply_filter(jobs, JobFilter(min_score=100), p)), 1)


if __name__ == "__main__":
    unittest.main()
