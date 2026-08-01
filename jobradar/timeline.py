"""Extract human-readable internship timing from job text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Job

MONTHS = {
    "jan": (1, "January"), "january": (1, "January"),
    "feb": (2, "February"), "february": (2, "February"),
    "mar": (3, "March"), "march": (3, "March"),
    "apr": (4, "April"), "april": (4, "April"),
    "may": (5, "May"),
    "jun": (6, "June"), "june": (6, "June"),
    "jul": (7, "July"), "july": (7, "July"),
    "aug": (8, "August"), "august": (8, "August"),
    "sep": (9, "September"), "sept": (9, "September"), "september": (9, "September"),
    "oct": (10, "October"), "october": (10, "October"),
    "nov": (11, "November"), "november": (11, "November"),
    "dec": (12, "December"), "december": (12, "December"),
}
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
YEAR_PATTERN = r"20(?:26|27|28)"

SEASONS = {
    "spring": (1, 4, "January-April"),
    "summer": (5, 8, "May-August"),
    "fall": (9, 12, "September-December"),
    "autumn": (9, 12, "September-December"),
    "winter": (12, 2, "December-February"),
}


@dataclass(frozen=True, slots=True)
class InternshipTimeline:
    label: str = "Dates unspecified"
    start_month: int | None = None
    end_month: int | None = None
    year: int | None = None
    confidence: float = 0.0


def extract_timeline(job: Job) -> InternshipTimeline:
    """Prefer title signals, then inspect a bounded description excerpt."""
    primary = f"{job.title} {job.department}"
    text = f"{primary} {job.description[:2500]}"

    season_match = re.search(
        rf"\b(spring|summer|fall|autumn|winter)\s*({YEAR_PATTERN})\b",
        text,
        re.I,
    )
    if season_match:
        season = season_match.group(1).lower()
        start, end, label = SEASONS[season]
        year = int(season_match.group(2))
        return InternshipTimeline(f"{label} {year}", start, end, year, 0.95)

    range_match = re.search(
        rf"\b({MONTH_PATTERN})\b(?:\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s*)?"
        rf"\s*(?:-|to|through|until|–|—)\s*"
        rf"\b({MONTH_PATTERN})\b(?:\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s*)?"
        rf"\s*({YEAR_PATTERN})?",
        text,
        re.I,
    )
    if range_match:
        start = MONTHS[range_match.group(1).lower()]
        end = MONTHS[range_match.group(2).lower()]
        # The optional year at the end of the range can be skipped by the
        # regex engine once both months have matched. Fall back to the full
        # bounded text so phrases such as "May to August 2027" retain 2027.
        year_text = range_match.group(3) or re.search(YEAR_PATTERN, text)
        year = int(year_text.group(0) if hasattr(year_text, "group") else year_text) if year_text else None
        suffix = f" {year}" if year else ""
        return InternshipTimeline(
            f"{start[1]}-{end[1]}{suffix}", start[0], end[0], year, 0.9
        )

    month_match = re.search(rf"\b({MONTH_PATTERN})\s+({YEAR_PATTERN})\b", text, re.I)
    if month_match:
        month = MONTHS[month_match.group(1).lower()]
        year = int(month_match.group(2))
        return InternshipTimeline(f"{month[1]} {year}", month[0], month[0], year, 0.8)

    year_match = re.search(rf"\b({YEAR_PATTERN})\b", primary)
    if year_match:
        year = int(year_match.group(1))
        return InternshipTimeline(f"{year} - months unspecified", None, None, year, 0.55)

    duration_match = re.search(r"\b(\d{1,2})[ -]?(?:month|months|mo)\b", text, re.I)
    if duration_match:
        return InternshipTimeline(
            f"{int(duration_match.group(1))}-month internship - dates unspecified",
            confidence=0.45,
        )

    if re.search(r"\b(rolling|year[- ]round|immediate start)\b", text, re.I):
        return InternshipTimeline("Rolling / immediate", confidence=0.4)
    return InternshipTimeline()


def timeline_sort_key(label: str, year: int | None, start_month: int | None) -> tuple:
    """Put target-year dated cohorts first and unspecified dates last."""
    unknown = label == "Dates unspecified"
    rolling = label == "Rolling / immediate"
    year_priority = {2027: 0, 2026: 1, 2028: 2}.get(year, 3)
    return (unknown, rolling, year_priority, start_month or 99, label)
