"""Offline contract tests for deterministic digest segmentation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from jobradar.models import Job
from jobradar.segmentation import classify_function, company_tier, freshness_bucket


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _job(
    title: str,
    *,
    department: str = "",
    description: str = "",
    posted_at: str | None = "2026-08-08T06:00:00+00:00",
) -> Job:
    return Job(
        company="Acme Labs",
        title=title,
        url="https://jobs.example.test/req-123",
        ats="greenhouse",
        location="Remote - India",
        department=department,
        description=description,
        posted_at=posted_at,
    )


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (_job("Associate Product Manager Intern"), "Product"),
        (_job("Machine Learning Engineer Intern"), "Applied AI/ML"),
        (_job("Backend Software Engineer Intern"), "Software"),
        (_job("Data Analyst Intern"), "Data/Analytics"),
        (_job("Quantitative Research Intern"), "Quant"),
        (_job("Product Designer Intern"), "Design"),
        (_job("Business Operations Intern"), "Ops/Other"),
        (_job("Intern, Office of the CEO"), "Ops/Other"),
    ],
)
def test_classify_function_covers_every_digest_section(
    job: Job, expected: str
) -> None:
    assert classify_function(job) == expected


@pytest.mark.parametrize(
    ("title", "department", "expected"),
    [
        # The role noun wins over incidental words in a compound title.
        ("Product Designer Intern", "Product", "Design"),
        ("Product Data Analyst Intern", "Product Analytics", "Data/Analytics"),
        ("Quantitative Software Developer Intern", "Engineering", "Quant"),
        # Explicit AI/ML specialization wins over generic software engineering.
        ("AI Software Engineer Intern", "Engineering", "Applied AI/ML"),
        # A generic engineer on an AI team remains software without an AI title signal.
        ("Software Engineer Intern", "AI Platform", "Software"),
    ],
)
def test_ambiguous_function_precedence_is_explicit_and_deterministic(
    title: str, department: str, expected: str
) -> None:
    job = _job(title, department=department)

    assert {classify_function(job) for _ in range(20)} == {expected}


@pytest.mark.parametrize(
    ("posted_at", "expected"),
    [
        ("2026-08-07T12:00:01+00:00", "new"),
        ("2026-08-07T12:00:00+00:00", "recent"),
        ("2026-08-01T12:00:00+00:00", "recent"),
        ("2026-08-01T11:59:59+00:00", "stale"),
        (None, "unknown"),
        ("not-a-timestamp", "unknown"),
    ],
)
def test_freshness_bucket_boundaries(
    posted_at: str | None, expected: str
) -> None:
    job = _job("Software Intern", posted_at=posted_at)
    assert freshness_bucket(job, now=NOW) == expected


def test_freshness_bucket_normalizes_timezone_offsets() -> None:
    utc = _job("Software Intern", posted_at="2026-08-07T12:30:00+00:00")
    india = _job("Software Intern", posted_at="2026-08-07T18:00:00+05:30")

    assert freshness_bucket(utc, now=NOW) == "new"
    assert freshness_bucket(india, now=NOW) == "new"
    assert freshness_bucket(utc, now=NOW) == freshness_bucket(india, now=NOW)


def test_company_tier_uses_known_mapping_and_defaults_to_unknown() -> None:
    known = {"Databricks": "Scaled", "Google": "Big Tech"}

    assert company_tier("Databricks", known_tiers=known) == "Scaled"
    assert company_tier(" google ", known_tiers=known) == "Big Tech"
    assert company_tier("Tiny Company With Two Open Roles", known_tiers=known) == "Unknown"
    assert company_tier("", known_tiers=known) == "Unknown"
