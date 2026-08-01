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


def test_foreign_location_is_not_overridden_by_remote_description() -> None:
    profile = {"locations": ["india", "remote"], "require_intern_signal": True}
    job = _job("Technology Intern", "London, UK")
    job.description = "The team supports remote collaboration and flexible work."

    assert filter_jobs([job], profile) == []


def test_new_grad_requires_explicit_profile_opt_in() -> None:
    job = _job("Software Engineer, New Grad", "Remote")
    strict = {"locations": ["india", "remote"], "require_intern_signal": True}
    expanded = {**strict, "include_new_grad": True}

    assert filter_jobs([job], strict) == []
    assert filter_jobs([job], expanded) == [job]


def test_us_focused_dataset_generic_remote_is_not_worldwide() -> None:
    profile = {"locations": ["india", "remote"], "require_intern_signal": True}
    job = _job("Cybersecurity Intern", "Remote")
    job.ats = "dreamwork"

    assert filter_jobs([job], profile) == []


def test_strict_internship_mode_excludes_trainees_and_working_students() -> None:
    profile = {
        "locations": ["india", "remote"],
        "require_intern_signal": True,
        "strict_internships_only": True,
    }

    assert filter_jobs([_job("Graduate Apprentice Trainee")], profile) == []
    assert filter_jobs([_job("Working Student, Product")], profile) == []
    assert filter_jobs([_job("Product Management Intern")], profile)
