"""Deterministic job segmentation and presentation facets.

The baseline intentionally uses transparent title/department rules rather than
an LLM.  It always emits exactly one supported function and exposes a confidence
score for callers that want to flag ambiguous classifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Mapping

from .models import Job, identity_text


FUNCTIONS: tuple[str, ...] = (
    "Product",
    "Applied AI/ML",
    "Software",
    "Data/Analytics",
    "Quant",
    "Design",
    "Ops/Other",
)

FRESHNESS_BUCKETS: tuple[str, ...] = (
    "new",
    "recent",
    "stale",
    "unknown",
)

_RULES: dict[str, tuple[re.Pattern[str], ...]] = {
    "Product": (
        re.compile(r"\b(?:associate\s+)?product\s+manager\b", re.I),
        re.compile(r"\bproduct\s+(?:management|owner|strategy)\b", re.I),
        re.compile(r"\bapm\b", re.I),
    ),
    "Applied AI/ML": (
        re.compile(r"\b(?:machine\s+learning|deep\s+learning|artificial\s+intelligence)\b", re.I),
        re.compile(r"\b(?:ai|ml)\b", re.I),
        re.compile(r"\b(?:ai|ml)\s+(?:engineer|researcher|scientist|intern)\b", re.I),
        re.compile(r"\b(?:nlp|computer\s+vision|generative\s+ai|large\s+language\s+model|llm)\b", re.I),
    ),
    "Software": (
        re.compile(r"\bsoftware\s+(?:development\s+)?engineer\b", re.I),
        re.compile(r"\b(?:frontend|front-end|backend|back-end|full[ -]?stack|mobile|ios|android)\b", re.I),
        re.compile(r"\b(?:developer|devops|site\s+reliability|sre|quality\s+assurance|qa\s+engineer)\b", re.I),
        re.compile(r"\b(?:platform|infrastructure|cloud|security)\s+engineer\b", re.I),
    ),
    "Data/Analytics": (
        re.compile(r"\bdata\s+(?:analyst|analytics|engineer|science|scientist)\b", re.I),
        re.compile(r"\b(?:business|product|growth|decision)\s+analyst\b", re.I),
        re.compile(r"\b(?:analytics|business\s+intelligence|bi\s+engineer)\b", re.I),
    ),
    "Quant": (
        re.compile(r"\bquant(?:itative)?\b", re.I),
        re.compile(r"\bquant(?:itative)?\s+(?:researcher|research|analyst|developer|trader|trading)\b", re.I),
        re.compile(r"\b(?:algorithmic\s+trading|systematic\s+trading|financial\s+model(?:ing|ling))\b", re.I),
    ),
    "Design": (
        re.compile(r"\b(?:product|ux|ui|user\s+experience|visual|graphic|interaction)\s+design(?:er)?\b", re.I),
        re.compile(r"\b(?:ux|ui)\s*(?:/|and|&)\s*(?:ux|ui)\b", re.I),
    ),
}

# Specialized roles win deterministic ties over broad engineering/analysis terms.
_TIE_ORDER = ("Design", "Quant", "Applied AI/ML", "Product", "Data/Analytics", "Software")


@dataclass(frozen=True, slots=True)
class FunctionClassification:
    """A deterministic function label with confidence and matching evidence."""

    function: str
    confidence: float
    matched_terms: tuple[str, ...] = ()


def classify_function_with_confidence(job: Job) -> FunctionClassification:
    """Classify a job using weighted title, department, and description matches."""

    fields = (
        (identity_text(job.title), 4.0),
        (identity_text(job.department), 2.0),
        (identity_text(job.description), 0.35),
    )
    scores = {name: 0.0 for name in _RULES}
    evidence: dict[str, list[str]] = {name: [] for name in _RULES}

    for name, patterns in _RULES.items():
        for text, field_weight in fields:
            if not text:
                continue
            for pattern in patterns:
                match = pattern.search(text)
                if match:
                    scores[name] += field_weight
                    evidence[name].append(match.group(0))

    best = max(_TIE_ORDER, key=lambda name: (scores[name], -_TIE_ORDER.index(name)))
    best_score = scores[best]
    if best_score == 0:
        return FunctionClassification("Ops/Other", 0.35)

    runner_up = max(score for name, score in scores.items() if name != best)
    separation = best_score / (best_score + runner_up) if runner_up else 1.0
    confidence = min(0.99, 0.55 + (0.35 * separation) + (0.09 * min(best_score / 4.0, 1.0)))
    terms = tuple(dict.fromkeys(identity_text(term) for term in evidence[best]))
    return FunctionClassification(best, round(confidence, 2), terms)


def classify_function(job: Job) -> str:
    """Return exactly one of the seven supported function labels."""

    return classify_function_with_confidence(job).function


def apply_segmentation(job: Job) -> Job:
    """Populate a job's function fields in place and return it."""

    result = classify_function_with_confidence(job)
    job.function = result.function
    job.function_confidence = result.confidence
    return job


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def freshness_bucket(job: Job, now: datetime | None = None) -> str:
    """Bucket a job by posting age, falling back to when it was first seen."""

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    timestamp = _parse_timestamp(job.posted_at) or _parse_timestamp(job.first_seen_at)
    if timestamp is None:
        return "unknown"

    hours = max(0.0, (reference - timestamp).total_seconds() / 3600)
    if hours < 24:
        return "new"
    if hours <= 24 * 7:
        return "recent"
    return "stale"


DEFAULT_KNOWN_TIERS: dict[str, str] = {
    "alphabet": "Big Tech",
    "amazon": "Big Tech",
    "apple": "Big Tech",
    "google": "Big Tech",
    "meta": "Big Tech",
    "microsoft": "Big Tech",
    "nvidia": "Big Tech",
    "airbnb": "Scaled",
    "atlassian": "Scaled",
    "databricks": "Scaled",
    "stripe": "Scaled",
}


def company_tier(company: str, known_tiers: Mapping[str, str] | None = None) -> str:
    """Return an explicit known tier, or ``Unknown`` without guessing.

    A caller-supplied mapping augments and overrides the small built-in list.
    Keys are matched with Unicode normalization and case folding.
    """

    tiers = {identity_text(name): tier for name, tier in DEFAULT_KNOWN_TIERS.items()}
    if known_tiers:
        tiers.update(
            {
                identity_text(name): str(tier)
                for name, tier in known_tiers.items()
                if identity_text(name)
            }
        )
    return tiers.get(identity_text(company), "Unknown")
