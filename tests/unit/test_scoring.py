"""Focused filtering regressions discovered during live harvest validation."""

from jobradar.models import Job
from jobradar.scoring import filter_jobs


def _job(title: str, location: str = "Bengaluru, India") -> Job:
    return Job(
        company="Example",
        title=title,
        url="https://example.test/job",
        ats="greenhouse",
        location=location,
        description="Internship opportunity",
    )


def test_recruiter_exclusion_also_blocks_recruitment_trainee() -> None:
    profile = {
        "title_exclude": ["recruiter"],
        "locations": ["india", "remote"],
        "require_intern_signal": True,
    }

    assert filter_jobs([_job("Trainee - Recruitment Coordinator")], profile) == []


def test_remote_us_only_role_is_not_treated_as_worldwide_remote() -> None:
    profile = {
        "locations": ["india", "remote"],
        "require_intern_signal": True,
    }

    assert filter_jobs([_job("Software Engineer Intern", "Remote - US only")], profile) == []
