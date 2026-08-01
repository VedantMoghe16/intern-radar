"""Discover public ATS board tokens from the Common Crawl CDX index.

Discovery is deliberately separate from live board probing.  This module only
turns archived public URLs into unverified registry candidates; callers decide
when and how politely to validate them against ATS APIs.
"""

from __future__ import annotations

import json
import html
import logging
import re
from typing import Iterable, Mapping, Protocol
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import Request, urlopen


log = logging.getLogger("jobradar.discovery")

CDX_BASE_URL = "https://index.commoncrawl.org"
USER_AGENT = (
    "InternRadar/1.0 (personal internship alert; Common Crawl index client)"
)
DEFAULT_PATTERNS: tuple[str, ...] = (
    "job-boards.greenhouse.io/*",
    "boards.greenhouse.io/*",
    "jobs.lever.co/*",
    "jobs.eu.lever.co/*",
    "jobs.ashbyhq.com/*",
    "jobs.smartrecruiters.com/*",
    "apply.workable.com/*",
    "*.recruitee.com/*",
)
COMMUNITY_DATASET_URLS: tuple[str, ...] = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README-Off-Season.md",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/archived/README-2026.md",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
)

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_INDEX_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_RESERVED_TOKENS = {
    "about",
    "api",
    "apply",
    "assets",
    "auth",
    "blog",
    "careers",
    "cdn",
    "contact",
    "docs",
    "embed",
    "favicon.ico",
    "help",
    "home",
    "jobs",
    "login",
    "privacy",
    "robots.txt",
    "search",
    "sitemap.xml",
    "static",
    "status",
    "support",
    "terms",
    "www",
}
_STATIC_SEGMENTS = {"asset", "assets", "cdn", "fonts", "images", "static"}
_STATIC_SUFFIXES = {
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".pdf",
    ".png",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
}


class _Response(Protocol):
    text: str

    def json(self) -> object: ...

    def raise_for_status(self) -> None: ...


class _Session(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, object],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _Response: ...


class _UrllibResponse:
    def __init__(self, payload: bytes):
        self.text = payload.decode("utf-8", errors="replace")

    def json(self) -> object:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        return None


class _UrllibSession:
    """Small requests-like adapter that keeps this module dependency-free."""

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, object],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _UrllibResponse:
        query = urlencode(params, doseq=True)
        request = Request(f"{url}?{query}", headers=dict(headers))
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return _UrllibResponse(response.read())


def _path_segments(path: str) -> list[str]:
    return [unquote(part).strip() for part in path.split("/") if part.strip()]


def _valid_token(token: str) -> bool:
    folded = token.casefold()
    return bool(
        _TOKEN.fullmatch(token)
        and folded not in _RESERVED_TOKENS
        and not any(folded.endswith(suffix) for suffix in _STATIC_SUFFIXES)
    )


def _has_static_junk(segments: Iterable[str]) -> bool:
    parts = [part.casefold() for part in segments]
    if any(part in _STATIC_SEGMENTS for part in parts):
        return True
    return bool(parts and any(parts[-1].endswith(suffix) for suffix in _STATIC_SUFFIXES))


def parse_board_url(url: str) -> dict[str, str] | None:
    """Parse a public careers URL into a registry-compatible candidate.

    Nested posting URLs are useful evidence of a board and are accepted.  Asset
    paths, generic service subdomains, invalid tokens, credentials, and URLs on
    lookalike domains are rejected.
    """

    try:
        parsed = urlsplit(str(url).strip())
        host = (parsed.hostname or "").rstrip(".").casefold()
    except (TypeError, ValueError):
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not host:
        return None
    if parsed.username or parsed.password:
        return None

    segments = _path_segments(parsed.path)
    provider = ""
    token = ""
    remainder: list[str] = []

    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        provider = "greenhouse"
    elif host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        provider = "lever"
    elif host == "jobs.ashbyhq.com":
        provider = "ashby"
    elif host == "jobs.smartrecruiters.com":
        provider = "smartrecruiters"
    elif host == "apply.workable.com":
        provider = "workable"

    if provider:
        if not segments:
            return None
        token, remainder = segments[0], segments[1:]
    elif host.endswith(".recruitee.com"):
        labels = host.split(".")
        if len(labels) != 3:
            return None
        provider = "recruitee"
        token = labels[0]
        remainder = segments
    else:
        return None

    if not _valid_token(token) or _has_static_junk(remainder):
        return None
    return {
        "provider": provider,
        "token": token,
        "company": "",
        "discovered_url": str(url).strip(),
    }


def candidates_from_text(text: str) -> list[dict[str, str]]:
    """Extract and deduplicate supported ATS board URLs from a public document."""
    found: dict[tuple[str, str], dict[str, str]] = {}
    for raw_url in re.findall(r'''https?://[^\s<>"')]+''', html.unescape(text)):
        candidate = parse_board_url(raw_url.rstrip(".,;"))
        if candidate is None:
            continue
        key = (candidate["provider"], candidate["token"].casefold())
        found.setdefault(key, candidate)
    return sorted(
        found.values(), key=lambda item: (item["provider"], item["token"].casefold())
    )


def discover_community_candidates(
    session,
    urls: Iterable[str] = COMMUNITY_DATASET_URLS,
) -> list[dict[str, str]]:
    """Use maintained public internship lists as a reliable discovery fallback."""
    found: dict[tuple[str, str], dict[str, str]] = {}
    for url in urls:
        try:
            response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            response.raise_for_status()
            for candidate in candidates_from_text(response.text):
                key = (candidate["provider"], candidate["token"].casefold())
                found.setdefault(key, candidate)
        except Exception as exc:  # noqa: BLE001 - one community source is optional
            log.warning("Community discovery source %s failed: %s", url, exc)
    return sorted(
        found.values(), key=lambda item: (item["provider"], item["token"].casefold())
    )


def _response_payload(response: _Response) -> object:
    response.raise_for_status()
    try:
        return response.json()
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        records: list[object] = []
        for line in response.text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                log.debug("Ignoring malformed CDX response line")
        return records


def _records(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict) and "url" in payload:
        return [payload]
    return []


def _page_count(payload: object) -> int | None:
    if not isinstance(payload, dict) or "pages" not in payload:
        return None
    try:
        return max(0, int(payload["pages"]))
    except (TypeError, ValueError):
        return None


def discover_candidates(
    index_name: str,
    patterns: Iterable[str] | None = None,
    session: _Session | None = None,
    page_size: int = 5,
    *,
    max_pages_per_pattern: int = 100,
) -> list[dict[str, str]]:
    """Query an official Common Crawl CDX index and deduplicate ATS boards.

    Pagination metadata is requested first.  ``max_pages_per_pattern`` places a
    deliberate ceiling on accidental high-volume runs; callers may resume with
    narrower patterns if an index exceeds it.  Network failures are isolated by
    pattern so one ATS cannot erase candidates already found for another.
    """

    if not _INDEX_NAME.fullmatch(index_name):
        raise ValueError("index_name must be a Common Crawl index identifier")
    if page_size < 1:
        raise ValueError("page_size must be positive")
    if max_pages_per_pattern < 1:
        raise ValueError("max_pages_per_pattern must be positive")

    client = session or _UrllibSession()
    endpoint = f"{CDX_BASE_URL}/{index_name}-index"
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    found: dict[tuple[str, str], dict[str, str]] = {}

    query_patterns = DEFAULT_PATTERNS if patterns is None else patterns
    for raw_pattern in query_patterns:
        pattern = str(raw_pattern).strip()
        if not pattern:
            continue
        base_params: dict[str, object] = {
            "url": pattern,
            "output": "json",
            "filter": ["status:200", "mime:.*html.*"],
            "collapse": "urlkey",
            "pageSize": page_size,
        }
        try:
            metadata_response = client.get(
                endpoint,
                params={**base_params, "showNumPages": "true"},
                headers=headers,
                timeout=30,
            )
            metadata = _response_payload(metadata_response)
            pages = _page_count(metadata)

            payloads: list[object] = []
            if pages is None:
                # Some compatible/mocked CDX services ignore showNumPages and
                # return page zero directly.  Preserve it, then continue only
                # while full pages suggest more data.
                payloads.append(metadata)
                page = 1
                previous_count = len(_records(metadata))
                while previous_count >= page_size and page < max_pages_per_pattern:
                    response = client.get(
                        endpoint,
                        params={**base_params, "page": page},
                        headers=headers,
                        timeout=30,
                    )
                    payload = _response_payload(response)
                    payloads.append(payload)
                    previous_count = len(_records(payload))
                    page += 1
            else:
                for page in range(min(pages, max_pages_per_pattern)):
                    response = client.get(
                        endpoint,
                        params={**base_params, "page": page},
                        headers=headers,
                        timeout=30,
                    )
                    payloads.append(_response_payload(response))

            for payload in payloads:
                for record in _records(payload):
                    candidate = parse_board_url(str(record.get("url") or ""))
                    if candidate is None:
                        continue
                    key = (candidate["provider"], candidate["token"].casefold())
                    found.setdefault(key, candidate)
        except Exception as exc:  # noqa: BLE001 - preserve other ATS results
            log.warning("Common Crawl pattern %s failed: %s", pattern, exc)

    return sorted(
        found.values(), key=lambda item: (item["provider"], item["token"].casefold())
    )


def serialize_candidates(candidates: Iterable[Mapping[str, str]]) -> str:
    """Serialize candidates deterministically for snapshots or CLI output."""

    normalized = [dict(candidate) for candidate in candidates]
    normalized.sort(
        key=lambda item: (
            str(item.get("provider", "")),
            str(item.get("token", "")).casefold(),
        )
    )
    return json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
