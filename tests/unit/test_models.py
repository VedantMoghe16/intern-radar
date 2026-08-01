"""Offline contract tests for the normalized job identity model."""

from __future__ import annotations

from jobradar.models import Job


def _job(**overrides: object) -> Job:
    values: dict[str, object] = {
        "company": "Acme Labs",
        "title": "Software Engineer Intern - Summer 2027",
        "url": "https://jobs.example.test/acme/req-123",
        "ats": "greenhouse",
        "location": "Bengaluru, India",
        "department": "Engineering",
        "description": "Build Python services for the AI platform.",
        "posted_at": "2026-08-01T00:00:00+00:00",
    }
    values.update(overrides)
    return Job(**values)


def test_uid_is_stable_across_sources_and_display_noise() -> None:
    greenhouse = _job()
    aggregator = _job(
        company="  acme\u00a0labs  ",
        title="software engineer intern \u2014 summer 2027",
        location="  BENGALURU,   INDIA ",
        ats="himalayas",
        url="https://himalayas.example.test/jobs/acme-internship",
        description="A differently formatted copy of the same posting.",
        posted_at="2026-08-01T05:30:00+05:30",
    )

    assert greenhouse.uid == aggregator.uid
    assert greenhouse.source_uid != aggregator.source_uid


def test_source_uid_is_stable_for_metadata_updates() -> None:
    first = _job(score=10.0, reasons=["initial match"])
    enriched = _job(
        description="A much richer description added on a later harvest.",
        posted_at="2026-08-02T00:00:00+00:00",
        score=91.0,
        reasons=["skills: python", "intake: summer 2027"],
    )

    assert first.source_uid == enriched.source_uid
    assert first.uid == enriched.uid


def test_normalized_is_idempotent_and_collapses_text_whitespace() -> None:
    raw = _job(
        company="  Acme\u00a0  Labs  ",
        title="  Software\tEngineer Intern \u2014  Summer 2027 ",
        location=" Bengaluru,\n India ",
        department="  Applied   AI ",
        description=" Build\n\nPython\tservices. ",
    )

    once = raw.normalized()
    twice = once.normalized()

    assert once.company == "Acme Labs"
    assert once.title == "Software Engineer Intern - Summer 2027"
    assert once.location == "Bengaluru, India"
    assert once.department == "Applied AI"
    assert once.description == "Build Python services."
    assert (
        once.company,
        once.title,
        once.location,
        once.department,
        once.description,
        once.uid,
        once.source_uid,
    ) == (
        twice.company,
        twice.title,
        twice.location,
        twice.department,
        twice.description,
        twice.uid,
        twice.source_uid,
    )


def test_uid_distinguishes_intake_cohorts_and_locations() -> None:
    summer_2027 = _job()
    summer_2028 = _job(title="Software Engineer Intern - Summer 2028")
    pune_2027 = _job(location="Pune, India")

    assert len({summer_2027.uid, summer_2028.uid, pune_2027.uid}) == 3


def test_haystack_contains_normalized_searchable_fields() -> None:
    job = _job(
        company="Acme Labs",
        title="Applied AI Intern",
        location="Remote - India",
        department="Machine Learning",
        description="Build RAG agents with Python.",
    ).normalized()

    assert job.haystack == job.haystack.lower()
    for phrase in (
        "acme labs",
        "applied ai intern",
        "remote - india",
        "machine learning",
        "rag agents with python",
    ):
        assert phrase in job.haystack

