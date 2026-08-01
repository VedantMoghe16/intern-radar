"""Offline CLI contracts for degraded coverage and pending delivery state."""

from __future__ import annotations

from pathlib import Path

from jobradar import cli
from jobradar.models import Job
from jobradar.store import Store


def _write_config(tmp_path: Path, companies: list[dict]) -> tuple[Path, Path]:
    companies_path = tmp_path / "companies.yaml"
    profile_path = tmp_path / "profile.yaml"
    company_lines = ["companies:"]
    for company in companies:
        company_lines.extend(
            [
                f"  - name: {company['name']}",
                f"    ats: {company['ats']}",
                f"    token: {company['token']}",
            ]
        )
    companies_path.write_text("\n".join(company_lines) + "\n", encoding="utf-8")
    profile_path.write_text(
        """skills:
  - python
boosts:
  summer 2027: 10
locations:
  - india
  - remote
title_include: []
title_exclude: []
require_intern_signal: true
drop_senior_titles: true
""",
        encoding="utf-8",
    )
    return companies_path, profile_path


def _job() -> Job:
    return Job(
        company="Healthy Co",
        title="Software Engineer Intern - Summer 2027",
        url="https://jobs.example.test/healthy/req-1",
        ats="greenhouse",
        external_id="req-1",
        location="Bengaluru, India",
        department="Engineering",
        description="Build Python services for students graduating in 2028.",
        posted_at="2026-08-01T00:00:00+00:00",
    )


def _run_args(
    *, db: Path, companies: Path, profile: Path, out: Path, email: bool = False
) -> list[str]:
    args = [
        "--db",
        str(db),
        "run",
        "--mode",
        "daily",
        "--companies",
        str(companies),
        "--profile",
        str(profile),
        "--out",
        str(out),
    ]
    if email:
        args.append("--email")
    return args


def test_partial_source_failure_still_writes_coverage_digest(
    tmp_path, monkeypatch
) -> None:
    companies, profile = _write_config(
        tmp_path,
        [
            {"name": "Healthy Co", "ats": "greenhouse", "token": "healthy"},
            {"name": "Broken Co", "ats": "lever", "token": "broken"},
        ],
    )
    db = tmp_path / "state.sqlite"
    out = tmp_path / "digest.html"

    def partial_fetch(entries):
        assert {entry["name"] for entry in entries} == {"Healthy Co", "Broken Co"}
        return [_job()], [{"company": "Broken Co", "error": "HTTP 503"}]

    monkeypatch.setattr(cli, "fetch_all", partial_fetch)

    cli.main(_run_args(db=db, companies=companies, profile=profile, out=out))

    rendered = out.read_text(encoding="utf-8")
    assert "Software Engineer Intern" in rendered
    assert "Partial coverage" in rendered
    assert "Broken Co" in rendered
    with Store(db) as store:
        assert len(store.pending()) == 1
        run = store.conn.execute(
            "SELECT sources_ok, sources_failed, jobs_seen FROM runs ORDER BY id DESC"
        ).fetchone()
        assert tuple(run) == (1, 1, 1)


def test_unnotified_role_remains_pending_and_renders_on_next_run(
    tmp_path, monkeypatch
) -> None:
    companies, profile = _write_config(
        tmp_path,
        [{"name": "Healthy Co", "ats": "greenhouse", "token": "healthy"}],
    )
    db = tmp_path / "state.sqlite"
    first_out = tmp_path / "first.html"
    second_out = tmp_path / "second.html"
    calls = 0

    def successful_fetch(_entries):
        nonlocal calls
        calls += 1
        # Return the same logical opening on both harvests. Persistence, rather
        # than the current fetch result, owns its pending delivery state.
        return [_job()], []

    monkeypatch.setattr(cli, "fetch_all", successful_fetch)

    cli.main(
        _run_args(
            db=db, companies=companies, profile=profile, out=first_out
        )
    )
    cli.main(
        _run_args(
            db=db, companies=companies, profile=profile, out=second_out
        )
    )

    assert calls == 2
    assert "Software Engineer Intern" in first_out.read_text(encoding="utf-8")
    assert "Software Engineer Intern" in second_out.read_text(encoding="utf-8")
    with Store(db) as store:
        pending = store.pending()
        assert len(pending) == 1
        assert pending[0]["notified_epoch"] is None
        assert store.stats()["total"] == 1


def test_missing_smtp_does_not_mark_pending_job_notified(
    tmp_path, monkeypatch
) -> None:
    companies, profile = _write_config(
        tmp_path,
        [{"name": "Healthy Co", "ats": "greenhouse", "token": "healthy"}],
    )
    db = tmp_path / "state.sqlite"
    out = tmp_path / "digest.html"
    for key in tuple(__import__("os").environ):
        if key.startswith("JOBRADAR_SMTP_") or key.startswith("JOBRADAR_EMAIL_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "fetch_all", lambda _entries: ([_job()], []))

    cli.main(
        _run_args(
            db=db,
            companies=companies,
            profile=profile,
            out=out,
            email=True,
        )
    )

    assert out.exists()
    with Store(db) as store:
        pending = store.pending()
        assert len(pending) == 1
        assert pending[0]["notified_epoch"] is None
        run_error = store.conn.execute(
            "SELECT error FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert "artifact only" in run_error

