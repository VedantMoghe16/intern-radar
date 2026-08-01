"""Deterministic internship month/year extraction tests."""

import pytest

from jobradar.models import Job
from jobradar.timeline import extract_timeline, timeline_sort_key


def _job(title: str, description: str = "") -> Job:
    return Job(
        company="Acme",
        title=title,
        url="https://example.test/job",
        ats="greenhouse",
        location="Bengaluru, India",
        description=description,
    )


@pytest.mark.parametrize(
    ("title", "description", "label", "start", "end", "year"),
    [
        ("Software Intern - Summer 2027", "", "May-August 2027", 5, 8, 2027),
        ("Data Intern", "Program runs May to August 2027.", "May-August 2027", 5, 8, 2027),
        ("Product Intern - June 2027", "", "June 2027", 6, 6, 2027),
        ("Technology Intern - 2027", "", "2027 - months unspecified", None, None, 2027),
        ("Design Intern", "This is a 6-month internship.", "6-month internship - dates unspecified", None, None, None),
        ("Operations Intern", "", "Dates unspecified", None, None, None),
    ],
)
def test_extract_timeline(
    title: str,
    description: str,
    label: str,
    start: int | None,
    end: int | None,
    year: int | None,
) -> None:
    timeline = extract_timeline(_job(title, description))
    assert (timeline.label, timeline.start_month, timeline.end_month, timeline.year) == (
        label,
        start,
        end,
        year,
    )


def test_target_summer_sorts_before_other_and_unspecified_timelines() -> None:
    labels = [
        ("Dates unspecified", None, None),
        ("September-December 2026", 2026, 9),
        ("May-August 2027", 2027, 5),
        ("January-April 2027", 2027, 1),
    ]

    ordered = sorted(labels, key=lambda item: timeline_sort_key(*item))

    assert [item[0] for item in ordered] == [
        "January-April 2027",
        "May-August 2027",
        "September-December 2026",
        "Dates unspecified",
    ]
