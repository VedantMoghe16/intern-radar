"""Core domain models shared by harvest, ranking, storage, and delivery.

The :class:`Job` model deliberately keeps the small constructor used by the
original prototype while carrying enough source metadata for cross-source
clustering.  ``uid`` identifies the logical opening; ``source_uid`` identifies
one provider's representation of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit


_WHITESPACE = re.compile(r"\s+")
_TYPOGRAPHIC_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def now_iso() -> str:
    """Return the current UTC timestamp in a SQLite-friendly ISO format."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: object) -> str:
    """Normalize display text without destroying its human-readable casing.

    NFKC normalization makes visually equivalent Unicode forms stable, while
    whitespace collapsing handles differences commonly introduced by ATS HTML.
    """

    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).translate(
        _TYPOGRAPHIC_TRANSLATION
    )
    return _WHITESPACE.sub(" ", normalized).strip()


def identity_text(value: object) -> str:
    """Return canonical text suitable for identity keys and lookups."""

    return normalize_text(value).casefold()


def _stable_hash(namespace: str, *parts: str) -> str:
    material = "\x1f".join((namespace, *parts)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _normalize_url(value: object) -> str:
    url = normalize_text(value)
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        if not parsed.scheme or not parsed.netloc:
            return url
        # Fragments are browser-local and never identify a different posting.
        return urlunsplit(
            (
                parsed.scheme.casefold(),
                parsed.netloc.casefold(),
                parsed.path,
                parsed.query,
                "",
            )
        )
    except ValueError:
        return url


@dataclass(slots=True)
class Job:
    """A normalized job opening with logical and source-specific identities.

    The first four fields preserve the prototype's constructor.  New metadata
    fields are optional so existing ATS connectors can migrate independently.
    Calling :meth:`normalized` cleans the object in place and returns it, which
    preserves the chaining style used by the existing fetchers.
    """

    company: str
    title: str
    url: str
    ats: str
    location: str = ""
    department: str = ""
    description: str = ""
    posted_at: str | None = None
    external_id: str | None = None
    source_urls: list[str] = field(default_factory=list)
    function: str = ""
    function_confidence: float = 0.0
    company_tier: str = "Unknown"
    score_components: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    first_seen_at: str | None = None
    timeline_label: str = "Dates unspecified"
    timeline_start_month: int | None = None
    timeline_end_month: int | None = None
    timeline_year: int | None = None
    timeline_confidence: float = 0.0

    @property
    def uid(self) -> str:
        """Stable logical-opening identity across ATSs and aggregators.

        URLs and provider IDs are intentionally excluded.  The normalized
        company/title/location tuple is the conservative cross-source cluster
        key requested by the product contract.
        """

        return _stable_hash(
            "job-cluster-v1",
            identity_text(self.company),
            identity_text(self.title),
            identity_text(self.location),
        )

    @property
    def source_uid(self) -> str:
        """Stable identity of this provider record with deterministic fallbacks."""

        source = identity_text(self.ats) or "unknown"
        provider_key = (
            identity_text(self.external_id) or _normalize_url(self.url) or self.uid
        )
        return _stable_hash("job-source-v1", source, provider_key)

    @property
    def haystack(self) -> str:
        """Case-folded searchable text used by filters and local ranking."""

        return " ".join(
            part
            for part in (
                identity_text(self.company),
                identity_text(self.title),
                identity_text(self.location),
                identity_text(self.department),
                identity_text(self.description),
            )
            if part
        )

    def normalized(self) -> Job:
        """Normalize mutable fields in place, retain all source URLs, and return self."""

        self.company = normalize_text(self.company)
        self.title = normalize_text(self.title)
        self.url = _normalize_url(self.url)
        self.ats = identity_text(self.ats)
        self.location = normalize_text(self.location)
        self.department = normalize_text(self.department)
        self.description = normalize_text(self.description)
        self.posted_at = normalize_text(self.posted_at) or None
        self.external_id = normalize_text(self.external_id) or None
        self.function = normalize_text(self.function)
        self.company_tier = normalize_text(self.company_tier) or "Unknown"
        self.first_seen_at = normalize_text(self.first_seen_at) or None
        self.timeline_label = normalize_text(self.timeline_label) or "Dates unspecified"
        self.timeline_confidence = max(0.0, min(float(self.timeline_confidence), 1.0))

        raw_urls: list[object]
        if isinstance(self.source_urls, str):
            raw_urls = [self.source_urls]
        else:
            raw_urls = list(self.source_urls or [])
        if self.url:
            raw_urls.insert(0, self.url)

        seen: set[str] = set()
        urls: list[str] = []
        for raw_url in raw_urls:
            candidate = _normalize_url(raw_url)
            key = candidate.casefold()
            if candidate and key not in seen:
                seen.add(key)
                urls.append(candidate)
        self.source_urls = urls

        self.function_confidence = max(0.0, min(float(self.function_confidence), 1.0))
        self.score = float(self.score)
        self.score_components = {
            normalize_text(key): float(value)
            for key, value in (self.score_components or {}).items()
            if normalize_text(key)
        }
        self.reasons = [normalize_text(reason) for reason in (self.reasons or []) if normalize_text(reason)]
        return self
