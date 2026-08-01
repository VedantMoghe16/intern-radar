"""Offline partial-coverage pipeline tests with a routed fake HTTP session."""

from __future__ import annotations

from pathlib import Path

import requests

from jobradar.digest import render_html
from jobradar.fetchers import fetch_all
from jobradar.store import Store


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self
            )

    def json(self) -> object:
        return self.payload


class RoutedSession:
    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.calls.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"no fake response configured for {url}")


def test_partial_fetch_persists_success_and_renders_failure(
    monkeypatch, tmp_path: Path
) -> None:
    session = RoutedSession(
        {
            "/good/jobs": FakeResponse(
                {
                    "jobs": [
                        {
                            "id": "gh-1",
                            "title": "Software Engineer Intern",
                            "absolute_url": "https://jobs.example/good/gh-1",
                            "location": {"name": "Bengaluru, India"},
                        }
                    ]
                }
            ),
            "/broken?mode=json": FakeResponse({}, status_code=503),
        }
    )
    monkeypatch.setattr(
        "jobradar.fetchers.requests.Session", lambda: session
    )
    companies = [
        {"name": "Good Co", "ats": "greenhouse", "token": "good"},
        {"name": "Broken Co", "ats": "lever", "token": "broken"},
        {"name": "Unknown Co", "ats": "mystery", "token": "unknown"},
    ]

    jobs, failures = fetch_all(companies)

    assert [job.title for job in jobs] == ["Software Engineer Intern"]
    assert failures == [
        {"company": "Broken Co", "error": "HTTP 503"},
        {"company": "Unknown Co", "error": "unknown ats 'mystery'"},
    ]

    with Store(tmp_path / "state.sqlite") as store:
        assert len(store.record(jobs)) == 1
        rows = store.recent()
    output = render_html(rows, failures, tmp_path / "partial.html")
    rendered = output.read_text(encoding="utf-8")

    assert "Software Engineer Intern" in rendered
    assert "waiting for this digest" in rendered
    assert "source failures" in rendered
    assert "Partial coverage" in rendered
    assert "Broken Co — HTTP 503" in rendered
    assert "Unknown Co — unknown ats &#x27;mystery&#x27;" in rendered


def test_all_failed_fetch_renders_coverage_warning(
    monkeypatch, tmp_path: Path
) -> None:
    session = RoutedSession(
        {"/dead/jobs": FakeResponse({}, status_code=500)}
    )
    monkeypatch.setattr(
        "jobradar.fetchers.requests.Session", lambda: session
    )

    jobs, failures = fetch_all(
        [
            {"name": "Dead Board", "ats": "greenhouse", "token": "dead"},
            {"name": "Unsupported", "ats": "unknown", "token": "unused"},
        ]
    )
    output = render_html(jobs, failures, tmp_path / "all-failed.html")
    rendered = output.read_text(encoding="utf-8")

    assert jobs == []
    assert len(failures) == 2
    assert "waiting for this digest" in rendered
    assert "source failures" in rendered
    assert "No unnotified matching roles" in rendered
    assert "Check the coverage summary" in rendered
    assert "Partial coverage" in rendered
    assert "Dead Board — HTTP 500" in rendered
    assert "Unsupported — unknown ats &#x27;unknown&#x27;" in rendered
