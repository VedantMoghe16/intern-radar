"""Fetch openings from structured public ATS endpoints.

Every function here talks to a documented, public, unauthenticated JSON
endpoint. Connector failures are isolated so partial coverage still produces
a digest.
"""

from __future__ import annotations

import logging
import html as html_lib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

import requests

from .models import Job

log = logging.getLogger("jobradar.fetch")

TIMEOUT = 20
HEADERS = {
    "User-Agent": "InternRadar/1.0 (+https://github.com; personal job-alert tool)",
    "Accept": "application/json",
}

_TAGS = re.compile(r"<[^>]+>")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    import html as _html

    text = _html.unescape(_TAGS.sub(" ", str(raw)))
    return " ".join(text.split())


def _validate_token(token: str) -> str:
    token = str(token or "").strip()
    if not _SAFE_TOKEN.fullmatch(token):
        raise ValueError("invalid board token")
    return token


def _ms_to_iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError):
        return None


def _seconds_to_iso(seconds) -> str | None:
    try:
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError):
        return None


def _get(session: requests.Session, url: str, **kw):
    resp = session.get(url, timeout=TIMEOUT, headers=HEADERS, **kw)
    resp.raise_for_status()
    return resp.json()


def _get_text(session: requests.Session, url: str, **kw) -> str:
    resp = session.get(url, timeout=TIMEOUT, headers=HEADERS, **kw)
    resp.raise_for_status()
    return resp.text


# --------------------------------------------------------------------------
# One fetcher per ATS. Each takes (session, company_label, board_token).
# --------------------------------------------------------------------------


def greenhouse(session, company: str, token: str) -> list[Job]:
    token = _validate_token(token)
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = _get(session, url)
    out = []
    for j in data.get("jobs", []):
        out.append(
            Job(
                company=company,
                title=j.get("title", ""),
                url=j.get("absolute_url", ""),
                ats="greenhouse",
                location=(j.get("location") or {}).get("name", ""),
                department=", ".join(
                    d.get("name", "") for d in (j.get("departments") or [])
                ),
                description=_strip_html(j.get("content", "")),
                posted_at=j.get("updated_at") or j.get("first_published"),
                external_id=str(j.get("id") or ""),
            ).normalized()
        )
    return out


def lever(session, company: str, token: str) -> list[Job]:
    token = _validate_token(token)
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    data = _get(session, url)
    out = []
    for j in data:
        cats = j.get("categories") or {}
        out.append(
            Job(
                company=company,
                title=j.get("text", ""),
                url=j.get("hostedUrl", "") or j.get("applyUrl", ""),
                ats="lever",
                location=cats.get("location", "") or "",
                department=cats.get("team", "") or "",
                description=j.get("descriptionPlain", "")
                or _strip_html(j.get("description", "")),
                posted_at=_ms_to_iso(j.get("createdAt")),
                external_id=str(j.get("id") or ""),
            ).normalized()
        )
    return out


def ashby(session, company: str, token: str) -> list[Job]:
    token = _validate_token(token)
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    data = _get(session, url)
    out = []
    for j in data.get("jobs", []):
        out.append(
            Job(
                company=company,
                title=j.get("title", ""),
                url=j.get("jobUrl", "") or j.get("applyUrl", ""),
                ats="ashby",
                location=j.get("location", "") or "",
                department=j.get("department", "") or j.get("team", "") or "",
                description=j.get("descriptionPlain", "")
                or _strip_html(j.get("descriptionHtml", "")),
                posted_at=j.get("publishedAt"),
                external_id=str(j.get("id") or j.get("jobId") or ""),
            ).normalized()
        )
    return out


def smartrecruiters(session, company: str, token: str) -> list[Job]:
    token = _validate_token(token)
    out, offset = [], 0
    while True:
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{token}"
            f"/postings?limit=100&offset={offset}"
        )
        data = _get(session, url)
        page = data.get("content", [])
        for j in page:
            loc = j.get("location") or {}
            where = ", ".join(
                p for p in [loc.get("city"), loc.get("country")] if p
            )
            out.append(
                Job(
                    company=company,
                    title=j.get("name", ""),
                    url=f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                    ats="smartrecruiters",
                    location=where,
                    department=(j.get("department") or {}).get("label", ""),
                    posted_at=j.get("releasedDate"),
                    external_id=str(j.get("id") or ""),
                ).normalized()
            )
        offset += len(page)
        if len(page) < 100 or offset >= data.get("totalFound", 0):
            break
    return out


def workable(session, company: str, token: str) -> list[Job]:
    token = _validate_token(token)
    url = f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
    data = _get(session, url)
    out = []
    for j in data.get("jobs", []):
        out.append(
            Job(
                company=company,
                title=j.get("title", ""),
                url=j.get("url", "") or j.get("application_url", ""),
                ats="workable",
                location=", ".join(
                    p for p in [j.get("city"), j.get("country")] if p
                ),
                department=j.get("department", "") or "",
                description=_strip_html(j.get("description", "")),
                posted_at=j.get("published_on"),
                external_id=str(j.get("shortcode") or j.get("id") or ""),
            ).normalized()
        )
    return out


def recruitee(session, company: str, token: str) -> list[Job]:
    token = _validate_token(token)
    url = f"https://{token}.recruitee.com/api/offers/"
    data = _get(session, url)
    out = []
    for j in data.get("offers", []):
        out.append(
            Job(
                company=company,
                title=j.get("title", ""),
                url=j.get("careers_url", "") or j.get("careers_apply_url", ""),
                ats="recruitee",
                location=j.get("location", "") or "",
                department=j.get("department", "") or "",
                description=_strip_html(j.get("description", "")),
                posted_at=j.get("published_at"),
                external_id=str(j.get("id") or j.get("slug") or ""),
            ).normalized()
        )
    return out


def arbeitnow(session, _company: str, _token: str) -> list[Job]:
    """Fetch the current public Arbeitnow remote-job page."""
    data = _get(session, "https://www.arbeitnow.com/api/job-board-api")
    out = []
    for j in data.get("data", []):
        location = str(j.get("location") or "")
        if j.get("remote") and "remote" not in location.lower():
            location = f"Remote · {location}".strip(" ·")
        out.append(
            Job(
                company=j.get("company_name", "") or "Unknown",
                title=j.get("title", ""),
                url=j.get("url", ""),
                ats="arbeitnow",
                location=location,
                department=", ".join(j.get("tags") or []),
                description=_strip_html(j.get("description", "")),
                posted_at=_seconds_to_iso(j.get("created_at")),
                external_id=str(j.get("slug") or j.get("id") or ""),
            ).normalized()
        )
    return out


def remoteok(session, _company: str, _token: str) -> list[Job]:
    """Fetch Remote OK's public JSON feed, ignoring its metadata first row."""
    data = _get(session, "https://remoteok.com/api")
    out = []
    for j in data if isinstance(data, list) else []:
        if not isinstance(j, dict) or not (j.get("position") or j.get("title")):
            continue
        posted = j.get("date") or _seconds_to_iso(j.get("epoch"))
        out.append(
            Job(
                company=j.get("company", "") or "Unknown",
                title=j.get("position", "") or j.get("title", ""),
                url=j.get("url", "") or j.get("apply_url", ""),
                ats="remoteok",
                location=j.get("location", "") or "Remote",
                department=", ".join(j.get("tags") or []),
                description=_strip_html(j.get("description", "")),
                posted_at=str(posted) if posted else None,
                external_id=str(j.get("id") or j.get("slug") or ""),
            ).normalized()
        )
    return out


def himalayas(session, _company: str, _token: str) -> list[Job]:
    """Fetch a bounded page from Himalayas' public remote-jobs API."""
    data = _get(session, "https://himalayas.app/jobs/api?limit=100&offset=0")
    out = []
    for j in data.get("jobs", []):
        restrictions = j.get("locationRestrictions") or j.get("location_restrictions") or []
        if isinstance(restrictions, str):
            restrictions = [restrictions]
        out.append(
            Job(
                company=j.get("companyName", "") or j.get("company", "") or "Unknown",
                title=j.get("title", ""),
                url=j.get("applicationLink", "") or j.get("url", ""),
                ats="himalayas",
                location=", ".join(str(value) for value in restrictions) or "Remote",
                department=", ".join(j.get("categories") or []),
                description=_strip_html(j.get("description", "")),
                posted_at=j.get("pubDate") or j.get("publishedAt"),
                external_id=str(j.get("guid") or j.get("id") or ""),
            ).normalized()
        )
    return out


def jobicy(session, _company: str, _token: str) -> list[Job]:
    """Fetch a bounded page from Jobicy's public remote-job API."""
    data = _get(session, "https://jobicy.com/api/v2/remote-jobs?count=100")
    out = []
    for j in data.get("jobs", []):
        out.append(
            Job(
                company=j.get("companyName", "") or "Unknown",
                title=j.get("jobTitle", "") or j.get("title", ""),
                url=j.get("url", "") or j.get("jobUrl", ""),
                ats="jobicy",
                location=j.get("jobGeo", "") or "Remote",
                department=j.get("jobIndustry", "") or j.get("jobType", "") or "",
                description=_strip_html(j.get("jobDescription", "")),
                posted_at=j.get("pubDate"),
                external_id=str(j.get("id") or j.get("jobSlug") or ""),
            ).normalized()
        )
    return out


def dreamwork(session, _company: str, _token: str) -> list[Job]:
    """Fetch the public Tech Internships 2027 machine-readable dataset."""
    data = _get(
        session,
        "https://raw.githubusercontent.com/dreamworkhq/Tech-Internships-2027/"
        "main/data/listings.json",
    )
    out = []
    for j in data.get("listings", []):
        location = j.get("location", "") or ""
        remote_type = str(j.get("remoteType") or "")
        if remote_type.lower() == "remote" and "remote" not in location.lower():
            location = f"Remote · {location}".strip(" ·")
        out.append(
            Job(
                company=j.get("company", "") or "Unknown",
                title=j.get("title", ""),
                url=j.get("url", ""),
                ats="dreamwork",
                location=location,
                department=str(j.get("aiRoleKind") or "").replace("_", " "),
                posted_at=j.get("postedAt") or j.get("firstIndexedAt"),
                external_id=str(j.get("id") or ""),
            ).normalized()
        )
    return out


def simplify(session, _company: str, _token: str) -> list[Job]:
    """Parse the public Summer 2027 internship repository's generated tables."""
    raw = _get_text(
        session,
        "https://raw.githubusercontent.com/SimplifyJobs/"
        "Summer2027-Internships/dev/README.md",
    )
    out: list[Job] = []
    last_company = ""
    now = datetime.now(timezone.utc)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, flags=re.I | re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.I | re.S)
        if len(cells) < 5:
            continue
        company = _strip_html(cells[0])
        if company in {"", "↳"}:
            company = last_company
        else:
            last_company = company
        title = _strip_html(cells[1])
        location = _strip_html(cells[2])
        links = re.findall(r"href=[\"']([^\"']+)", cells[3], flags=re.I)
        if not company or not title or not links:
            continue
        url = html_lib.unescape(links[0])
        age_match = re.search(r"(\d+)d", _strip_html(cells[4]), re.I)
        posted_at = None
        if age_match:
            posted_at = (now - timedelta(days=int(age_match.group(1)))).isoformat(
                timespec="seconds"
            )
        out.append(
            Job(
                company=company,
                title=title,
                url=url,
                ats="simplify",
                location=location,
                posted_at=posted_at,
            ).normalized()
        )
    return out


REGISTRY: dict[str, Callable] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "recruitee": recruitee,
    "arbeitnow": arbeitnow,
    "remoteok": remoteok,
    "himalayas": himalayas,
    "jobicy": jobicy,
    "dreamwork": dreamwork,
    "simplify": simplify,
}


def fetch_all(companies: Iterable[dict]) -> tuple[list[Job], list[dict]]:
    """Pull every configured board. Returns (jobs, failures).

    One dead board never kills the run — a company that changed ATS should
    cost you that company's listings for a day, not the whole digest.
    """
    jobs: list[Job] = []
    failures: list[dict] = []
    session = requests.Session()
    last_lever_request: float | None = None

    for entry in companies:
        name = entry.get("name", "?")
        ats = (entry.get("ats") or "").lower()
        token = entry.get("token", "")
        fn = REGISTRY.get(ats)

        if not fn:
            failures.append({"company": name, "error": f"unknown ats '{ats}'"})
            continue
        try:
            if ats == "lever" and last_lever_request is not None:
                remaining = 1.0 - (time.monotonic() - last_lever_request)
                if remaining > 0:
                    time.sleep(remaining)
            if ats == "lever":
                last_lever_request = time.monotonic()
            found = fn(session, name, token)
            jobs.extend(found)
            log.info("%-22s %-16s %3d openings", name, ats, len(found))
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            failures.append({"company": name, "error": f"HTTP {code}"})
        except Exception as e:  # noqa: BLE001 - one bad board must not stop the run
            failures.append({"company": name, "error": f"{type(e).__name__}: {e}"})

    return jobs, failures
