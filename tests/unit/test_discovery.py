"""Offline tests for Common Crawl ATS candidate discovery."""

from __future__ import annotations

import json

import pytest

from jobradar.discovery import (
    USER_AGENT,
    discover_candidates,
    parse_board_url,
    serialize_candidates,
)


@pytest.mark.parametrize(
    ("url", "provider", "token"),
    [
        ("https://job-boards.greenhouse.io/Acme/jobs/123", "greenhouse", "Acme"),
        ("https://boards.greenhouse.io/acme", "greenhouse", "acme"),
        ("https://jobs.lever.co/acme/one-role", "lever", "acme"),
        ("https://jobs.eu.lever.co/acme", "lever", "acme"),
        ("https://jobs.ashbyhq.com/acme/role-id", "ashby", "acme"),
        (
            "https://jobs.smartrecruiters.com/AcmeInc/123-intern",
            "smartrecruiters",
            "AcmeInc",
        ),
        ("https://apply.workable.com/acme/j/ABC123/", "workable", "acme"),
        ("https://acme.recruitee.com/o/software-intern", "recruitee", "acme"),
    ],
)
def test_parse_board_url_covers_supported_providers(
    url: str, provider: str, token: str
) -> None:
    candidate = parse_board_url(url)
    assert candidate is not None
    assert candidate["provider"] == provider
    assert candidate["token"] == token


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/acme",
        "ftp://jobs.lever.co/acme",
        "https://jobs.lever.co/",
        "https://jobs.lever.co/assets/app.js",
        "https://jobs.lever.co/acme/static/app.js",
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        "https://www.recruitee.com/",
        "https://jobs.acme.recruitee.com/o/role",
        "https://apply.workable.com/robots.txt",
        "https://user:password@jobs.ashbyhq.com/acme",
        "not a url",
    ],
)
def test_parse_board_url_rejects_reserved_static_and_lookalike_urls(url: str) -> None:
    assert parse_board_url(url) is None


class _FakeResponse:
    def __init__(self, payload: object):
        self._payload = payload
        self.text = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False)
        )

    def json(self) -> object:
        return json.loads(self._payload) if isinstance(self._payload, str) else self._payload

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        params = kwargs["params"]
        assert isinstance(params, dict)
        self.calls.append({"url": url, **kwargs})
        if params.get("showNumPages") == "true":
            return _FakeResponse({"pages": 2})
        if params.get("page") == 0:
            return _FakeResponse(
                "\n".join(
                    [
                        json.dumps({"url": "https://jobs.lever.co/acme/role-1"}),
                        json.dumps({"url": "https://jobs.lever.co/acme/role-2"}),
                    ]
                )
            )
        return _FakeResponse(
            [
                {"url": "https://jobs.lever.co/Acme/role-3"},
                {"url": "https://jobs.lever.co/assets/app.js"},
                {"not_url": "ignored"},
            ]
        )


def test_discover_candidates_paginates_dedupes_and_uses_honest_headers() -> None:
    session = _FakeSession()
    candidates = discover_candidates(
        "CC-MAIN-2026-30",
        patterns=["jobs.lever.co/*"],
        session=session,
        page_size=2,
    )

    assert len(session.calls) == 3
    assert candidates == [
        {
            "provider": "lever",
            "token": "acme",
            "company": "",
            "discovered_url": "https://jobs.lever.co/acme/role-1",
        }
    ]
    for call in session.calls:
        assert call["url"] == "https://index.commoncrawl.org/CC-MAIN-2026-30-index"
        assert call["headers"]["User-Agent"] == USER_AGENT
        assert call["params"]["pageSize"] == 2
        assert call["params"]["collapse"] == "urlkey"


def test_discover_candidates_supports_cdx_without_page_metadata() -> None:
    class SinglePageSession:
        def get(self, _url: str, **_kwargs: object) -> _FakeResponse:
            return _FakeResponse([{"url": "https://jobs.ashbyhq.com/example"}])

    candidates = discover_candidates(
        "CC-MAIN-test",
        patterns=["jobs.ashbyhq.com/*"],
        session=SinglePageSession(),
        page_size=10,
    )
    assert [(item["provider"], item["token"]) for item in candidates] == [
        ("ashby", "example")
    ]


def test_discovery_arguments_and_serialization_are_deterministic() -> None:
    with pytest.raises(ValueError):
        discover_candidates("../not-an-index", session=_FakeSession())
    with pytest.raises(ValueError):
        discover_candidates("CC-MAIN-test", page_size=0, session=_FakeSession())

    serialized = serialize_candidates(
        [
            {"provider": "lever", "token": "zeta"},
            {"provider": "ashby", "token": "alpha"},
        ]
    )
    assert json.loads(serialized)[0] == {"provider": "ashby", "token": "alpha"}
