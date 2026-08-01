"""Offline persistence tests for job clusters and their source postings."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jobradar.models import Job
from jobradar.store import Store


@pytest.fixture
def store(tmp_path) -> Iterator[Store]:
    value = Store(tmp_path / "state.sqlite")
    try:
        yield value
    finally:
        value.close()


def _job(**overrides: object) -> Job:
    values: dict[str, object] = {
        "company": "Acme Labs",
        "title": "Software Engineer Intern - Summer 2027",
        "url": "https://boards.example.test/acme/123",
        "ats": "greenhouse",
        "external_id": "gh-123",
        "location": "Bengaluru, India",
        "department": "Engineering",
        "description": "Build Python services.",
        "posted_at": "2026-08-01T00:00:00+00:00",
        "score": 75.0,
        "reasons": ["skills: python"],
    }
    values.update(overrides)
    return Job(**values)


def test_cross_source_cluster_retains_two_source_rows(store: Store) -> None:
    greenhouse = _job()
    aggregator = _job(
        ats="himalayas",
        external_id="himalayas-987",
        url="https://himalayas.example.test/jobs/acme-987",
        description="A syndicated copy of the same role.",
    )

    fresh = store.record([greenhouse, aggregator])

    assert len(fresh) == 1
    assert greenhouse.uid == aggregator.uid
    sources = store.sources_for(greenhouse.uid)
    assert len(sources) == 2
    assert {source["provider"] for source in sources} == {
        "greenhouse",
        "himalayas",
    }
    assert {source["url"] for source in sources} == {
        greenhouse.url,
        aggregator.url,
    }


def test_record_is_idempotent_for_clusters_and_sources(store: Store) -> None:
    job = _job()

    first = store.record([job, job])
    assert len(first) == 1
    assert first[0].uid == job.uid
    assert store.record([job]) == []
    assert store.stats()["total"] == 1
    assert len(store.sources_for(job.uid)) == 1


def test_rerecord_preserves_status_and_ambiguous_mark_is_refused(
    store: Store,
) -> None:
    first = _job()
    second = _job(
        title="Data Analyst Intern - Summer 2027",
        external_id="gh-456",
        url="https://boards.example.test/acme/456",
    )
    store.record([first, second])

    assert store.mark(first.uid, "applied") == 1
    store.record([_job(score=99.0, reasons=["updated ranking"])])
    status = store.conn.execute(
        "SELECT status FROM jobs WHERE uid = ?", (first.uid,)
    ).fetchone()[0]
    assert status == "applied"

    with pytest.raises(ValueError, match="ambiguous"):
        store.mark("", "ignored")
    assert store.stats()["applied"] == 1


def test_recent_uses_epoch_window_boundaries(monkeypatch, store: Store) -> None:
    now = 2_000_000_000
    monkeypatch.setattr("jobradar.store._epoch", lambda: now - 24 * 3600)
    boundary = _job(title="Boundary Software Intern", external_id="boundary")
    store.record([boundary])

    monkeypatch.setattr("jobradar.store._epoch", lambda: now - 24 * 3600 - 1)
    old = _job(title="Old Software Intern", external_id="old")
    store.record([old])

    monkeypatch.setattr("jobradar.store._epoch", lambda: now)
    rows = store.recent(hours=24)

    assert [row["uid"] for row in rows] == [boundary.uid]


def test_prune_removes_old_run_history_but_retains_jobs(
    monkeypatch, store: Store
) -> None:
    start = 2_000_000_000
    monkeypatch.setattr("jobradar.store._epoch", lambda: start)
    job = _job()
    store.record([job])
    run_id = store.start_run("daily")
    store.finish_run(
        run_id,
        sources_ok=1,
        sources_failed=0,
        jobs_seen=1,
    )

    monkeypatch.setattr("jobradar.store._epoch", lambda: start + 91 * 24 * 3600)
    assert store.prune(days=90) == 1
    assert store.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert store.conn.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0] == 1
    assert store.mark(job.uid, "applied") == 1
