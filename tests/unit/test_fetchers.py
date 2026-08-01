"""Offline contract tests for structured ATS and aggregator connectors."""

from __future__ import annotations

from collections.abc import Callable

import pytest
import requests

from jobradar.fetchers import (
    HEADERS,
    TIMEOUT,
    _strip_html,
    arbeitnow,
    ashby,
    greenhouse,
    dreamwork,
    himalayas,
    jobicy,
    lever,
    recruitee,
    remoteok,
    simplify,
    smartrecruiters,
    workable,
)


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self
            )

    def json(self) -> object:
        return self.payload


class FakeSession:
    def __init__(self, *responses: FakeResponse):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected HTTP request: {url}")
        return self.responses.pop(0)


def _assert_request(session: FakeSession, url: str) -> None:
    assert session.calls == [
        {"url": url, "timeout": TIMEOUT, "headers": HEADERS}
    ]


def test_greenhouse_mapping() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "jobs": [
                    {
                        "id": 101,
                        "title": "Applied AI Intern",
                        "absolute_url": "https://boards.example/greenhouse/101",
                        "location": {"name": "Bengaluru, India"},
                        "departments": [{"name": "AI"}, {"name": "Engineering"}],
                        "content": "Build <b>RAG</b> &amp; agents.",
                        "updated_at": "2026-08-01T01:00:00Z",
                    }
                ]
            }
        )
    )

    job = greenhouse(session, "Acme", "acme")[0]

    assert (job.ats, job.external_id) == ("greenhouse", "101")
    assert (job.location, job.department) == (
        "Bengaluru, India",
        "AI, Engineering",
    )
    assert job.description == "Build RAG & agents."
    assert job.source_urls == [job.url]
    _assert_request(
        session,
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
    )


def test_lever_mapping() -> None:
    session = FakeSession(
        FakeResponse(
            [
                {
                    "id": "lev-1",
                    "text": "Software Engineer Intern",
                    "hostedUrl": "https://jobs.example/lever/lev-1",
                    "categories": {
                        "location": "Remote - India",
                        "team": "Platform",
                    },
                    "descriptionPlain": "",
                    "description": "Build <em>Python</em> services.",
                    "createdAt": 0,
                }
            ]
        )
    )

    job = lever(session, "Acme", "acme")[0]

    assert (job.ats, job.external_id) == ("lever", "lev-1")
    assert (job.location, job.department) == ("Remote - India", "Platform")
    assert job.description == "Build Python services."
    assert job.posted_at == "1970-01-01T00:00:00+00:00"
    assert job.source_urls == [job.url]
    _assert_request(
        session, "https://api.lever.co/v0/postings/acme?mode=json"
    )


def test_ashby_mapping() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "jobs": [
                    {
                        "id": "ash-1",
                        "title": "Product Manager Intern",
                        "jobUrl": "https://jobs.example/ashby/ash-1",
                        "location": "Bengaluru, India",
                        "department": "Product",
                        "descriptionHtml": "Own <strong>AI</strong> products.",
                        "publishedAt": "2026-08-01T02:00:00Z",
                    }
                ]
            }
        )
    )

    job = ashby(session, "Acme", "acme")[0]

    assert (job.ats, job.external_id) == ("ashby", "ash-1")
    assert (job.department, job.description) == (
        "Product",
        "Own AI products.",
    )
    assert job.source_urls == [job.url]
    _assert_request(
        session, "https://api.ashbyhq.com/posting-api/job-board/acme"
    )


def test_smartrecruiters_mapping() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "totalFound": 1,
                "content": [
                    {
                        "id": "smart-1",
                        "name": "Data Analyst Intern",
                        "location": {"city": "Pune", "country": "India"},
                        "department": {"label": "Analytics"},
                        "releasedDate": "2026-08-01T03:00:00Z",
                    }
                ],
            }
        )
    )

    job = smartrecruiters(session, "Acme", "AcmeToken")[0]

    assert (job.ats, job.external_id) == ("smartrecruiters", "smart-1")
    assert (job.location, job.department) == ("Pune, India", "Analytics")
    assert job.url == "https://jobs.smartrecruiters.com/AcmeToken/smart-1"
    assert job.source_urls == [job.url]
    _assert_request(
        session,
        "https://api.smartrecruiters.com/v1/companies/AcmeToken/"
        "postings?limit=100&offset=0",
    )


def test_workable_mapping() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "jobs": [
                    {
                        "shortcode": "work-1",
                        "title": "Design Intern",
                        "url": "https://jobs.example/workable/work-1",
                        "city": "Mumbai",
                        "country": "India",
                        "department": "Design",
                        "description": "Design&nbsp;<b>mobile</b> flows.",
                        "published_on": "2026-08-01",
                    }
                ]
            }
        )
    )

    job = workable(session, "Acme", "acme")[0]

    assert (job.ats, job.external_id) == ("workable", "work-1")
    assert (job.location, job.department) == ("Mumbai, India", "Design")
    assert job.description == "Design mobile flows."
    assert job.source_urls == [job.url]
    _assert_request(
        session,
        "https://apply.workable.com/api/v1/widget/accounts/acme?details=true",
    )


def test_recruitee_mapping() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "offers": [
                    {
                        "id": 606,
                        "title": "Operations Intern",
                        "careers_url": "https://jobs.example/recruitee/606",
                        "location": "Remote - India",
                        "department": "Operations",
                        "description": "Improve <i>business</i> systems.",
                        "published_at": "2026-08-01T04:00:00Z",
                    }
                ]
            }
        )
    )

    job = recruitee(session, "Acme", "acme")[0]

    assert (job.ats, job.external_id) == ("recruitee", "606")
    assert (job.location, job.department) == (
        "Remote - India",
        "Operations",
    )
    assert job.description == "Improve business systems."
    assert job.source_urls == [job.url]
    _assert_request(session, "https://acme.recruitee.com/api/offers/")


@pytest.mark.parametrize(
    "connector",
    [greenhouse, lever, ashby, smartrecruiters, workable, recruitee],
)
def test_connectors_reject_unsafe_tokens_before_http(
    connector: Callable,
) -> None:
    session = FakeSession()

    with pytest.raises(ValueError, match="invalid board token"):
        connector(session, "Acme", "../other-host?token=x")
    assert session.calls == []


def test_html_normalization_removes_tags_decodes_entities_and_whitespace() -> None:
    assert _strip_html("  Build\n<b>RAG</b>&nbsp;&amp;\t agents.  ") == (
        "Build RAG & agents."
    )


def test_cross_source_mappings_share_cluster_but_keep_source_identity() -> None:
    greenhouse_session = FakeSession(
        FakeResponse(
            {
                "jobs": [
                    {
                        "id": "gh-10",
                        "title": "Software Engineer Intern",
                        "absolute_url": "https://boards.example/gh-10",
                        "location": {"name": "Bengaluru, India"},
                    }
                ]
            }
        )
    )
    lever_session = FakeSession(
        FakeResponse(
            [
                {
                    "id": "lev-20",
                    "text": "Software Engineer Intern",
                    "hostedUrl": "https://jobs.example/lev-20",
                    "categories": {"location": "Bengaluru, India"},
                }
            ]
        )
    )

    greenhouse_job = greenhouse(greenhouse_session, "Acme", "acme")[0]
    lever_job = lever(lever_session, "Acme", "acme")[0]

    assert greenhouse_job.uid == lever_job.uid
    assert greenhouse_job.external_id == "gh-10"
    assert lever_job.external_id == "lev-20"
    assert greenhouse_job.source_uid != lever_job.source_uid
    assert greenhouse_job.source_urls == [greenhouse_job.url]
    assert lever_job.source_urls == [lever_job.url]


def test_arbeitnow_mapping() -> None:
    session = FakeSession(FakeResponse({"data": [{
        "slug": "intern-1", "company_name": "Acme", "title": "AI Intern",
        "url": "https://example.test/arbeitnow/1", "location": "India",
        "remote": True, "tags": ["AI"], "description": "Build <b>agents</b>",
        "created_at": 0,
    }]}))

    job = arbeitnow(session, "Arbeitnow", "global")[0]

    assert (job.company, job.ats, job.external_id) == ("Acme", "arbeitnow", "intern-1")
    assert job.location == "Remote · India"
    _assert_request(session, "https://www.arbeitnow.com/api/job-board-api")


def test_remoteok_mapping_ignores_metadata_row() -> None:
    session = FakeSession(FakeResponse([
        {"legal": "metadata"},
        {"id": "remote-1", "company": "Acme", "position": "Product Intern",
         "url": "https://example.test/remote/1", "description": "Own products"},
    ]))

    jobs = remoteok(session, "Remote OK", "global")

    assert len(jobs) == 1
    assert (jobs[0].company, jobs[0].location) == ("Acme", "Remote")
    _assert_request(session, "https://remoteok.com/api")


def test_himalayas_mapping() -> None:
    session = FakeSession(FakeResponse({"jobs": [{
        "guid": "him-1", "companyName": "Acme", "title": "Data Intern",
        "applicationLink": "https://example.test/himalayas/1",
        "locationRestrictions": ["India"], "categories": ["Data"],
        "description": "Analyze <b>data</b>", "pubDate": "2026-08-01T00:00:00Z",
    }]}))

    job = himalayas(session, "Himalayas", "global")[0]

    assert (job.ats, job.location, job.department) == ("himalayas", "India", "Data")
    _assert_request(session, "https://himalayas.app/jobs/api?limit=100&offset=0")


def test_jobicy_mapping() -> None:
    session = FakeSession(FakeResponse({"jobs": [{
        "id": "jobicy-1", "companyName": "Acme", "jobTitle": "Design Intern",
        "url": "https://example.test/jobicy/1", "jobGeo": "Anywhere",
        "jobIndustry": "Design", "jobDescription": "Design <b>flows</b>",
    }]}))

    job = jobicy(session, "Jobicy", "global")[0]

    assert (job.ats, job.location, job.department) == ("jobicy", "Anywhere", "Design")
    _assert_request(session, "https://jobicy.com/api/v2/remote-jobs?count=100")


def test_dreamwork_dataset_mapping() -> None:
    session = FakeSession(FakeResponse({"listings": [{
        "id": "dream-1", "company": "Acme", "title": "AI Intern",
        "url": "https://example.test/dream/1", "location": "India",
        "remoteType": "remote", "aiRoleKind": "ai_explicit",
        "firstIndexedAt": "2026-08-01T00:00:00Z",
    }]}))

    job = dreamwork(session, "Dreamwork", "2027")[0]

    assert (job.ats, job.location, job.department) == (
        "dreamwork", "Remote · India", "ai explicit"
    )
    _assert_request(
        session,
        "https://raw.githubusercontent.com/dreamworkhq/Tech-Internships-2027/"
        "main/data/listings.json",
    )


def test_simplify_public_table_mapping() -> None:
    readme = """
    <table><tbody><tr>
      <td><strong>Acme</strong></td><td>Software Engineer Intern</td>
      <td>Remote - India</td>
      <td><a href="https://jobs.example.test/acme/1?utm_source=GHList">Apply</a></td>
      <td>2d</td>
    </tr></tbody></table>
    """
    session = FakeSession(FakeResponse(readme))

    job = simplify(session, "Simplify", "2027")[0]

    assert (job.company, job.title, job.location) == (
        "Acme", "Software Engineer Intern", "Remote - India"
    )
    assert job.url.startswith("https://jobs.example.test/acme/1")
    assert job.posted_at is not None
