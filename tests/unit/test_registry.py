"""Offline registry lifecycle and seven-day scheduler tests."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from jobradar.registry import BoardRegistry
from jobradar.scheduler import cold_shard, daily_cold_slice, shard_for_day


@pytest.fixture
def registry(tmp_path) -> Iterator[BoardRegistry]:
    value = BoardRegistry(tmp_path / "registry.sqlite")
    try:
        yield value
    finally:
        value.close()


def _add_board(registry: BoardRegistry, token: str = "acme"):
    registry.add_candidates(
        [{"name": "Acme Labs", "ats": "GreenHouse", "token": token}],
        source="common-crawl",
    )
    return next(board for board in registry.all() if board.token == token)


def test_candidate_import_is_normalized_and_idempotent(
    monkeypatch, registry: BoardRegistry
) -> None:
    monkeypatch.setattr("jobradar.registry._epoch", lambda: 2_000_000_000)

    registry.add_candidates(
        [
            {"name": " Acme Labs ", "ats": " GreenHouse ", "token": " acme "},
            {"company": "Ignored", "provider": "", "token": "missing-provider"},
            {"company": "Ignored", "provider": "lever", "token": ""},
        ],
        source="common-crawl",
    )
    registry.add_candidates(
        [{"company": "Acme Labs Updated", "provider": "greenhouse", "token": "acme"}],
        source="seed",
    )

    boards = registry.all()
    assert len(boards) == 1
    assert boards[0].provider == "greenhouse"
    assert boards[0].token == "acme"
    assert boards[0].company == "Acme Labs Updated"
    assert boards[0].lifecycle == "candidate"
    assert boards[0].tier == "cold"


def test_relevant_success_promotes_board_to_hot(
    monkeypatch, registry: BoardRegistry
) -> None:
    monkeypatch.setattr("jobradar.registry._epoch", lambda: 2_000_000_000)
    board = _add_board(registry)
    registry.mark_probe(board.id, live=True, company="Acme Labs")

    poll_time = 2_000_001_000
    registry.mark_poll(
        board.id,
        success=True,
        relevant=True,
        etag='"v1"',
        last_modified="Sat, 01 Aug 2026 00:00:00 GMT",
        now=poll_time,
    )

    promoted = registry.all()[0]
    assert promoted.lifecycle == "active"
    assert promoted.tier == "hot"
    assert promoted.relevant_ever is True
    assert promoted.failure_count == 0
    assert promoted.next_poll_epoch == poll_time + 12 * 3600
    assert promoted.etag == '"v1"'


def test_transient_failures_back_off_without_automatic_retirement(
    monkeypatch, registry: BoardRegistry
) -> None:
    start = 2_000_000_000
    monkeypatch.setattr("jobradar.registry._epoch", lambda: start)
    board = _add_board(registry)
    registry.mark_probe(board.id, live=True)
    registry.mark_poll(board.id, success=True, now=start)

    expected_delays = [6, 12, 24, 48, 96, 96, 96, 96]
    for failure, delay_hours in enumerate(expected_delays, start=1):
        failed_at = start + failure * 100
        registry.mark_poll(board.id, success=False, now=failed_at)
        current = registry.all()[0]
        assert current.failure_count == failure
        assert current.next_poll_epoch == failed_at + delay_hours * 3600
        assert current.lifecycle != "retired"

    assert registry.all()[0].lifecycle == "dormant"


def test_due_modes_are_deterministic_and_exclude_retired(
    registry: BoardRegistry,
) -> None:
    hot = _add_board(registry, "hot")
    due_cold = _add_board(registry, "due-cold")
    future_cold = _add_board(registry, "future-cold")
    retired = _add_board(registry, "retired")
    now = 2_000_000_000

    with registry.conn:
        registry.conn.execute(
            "UPDATE boards SET tier = 'hot', next_poll_epoch = ? WHERE id = ?",
            (now + 999_999, hot.id),
        )
        registry.conn.execute(
            "UPDATE boards SET next_poll_epoch = ? WHERE id = ?",
            (now, due_cold.id),
        )
        registry.conn.execute(
            "UPDATE boards SET next_poll_epoch = ? WHERE id = ?",
            (now + 1, future_cold.id),
        )
    registry.retire(retired.id)

    assert [board.token for board in registry.due("hot", now)] == ["hot"]
    assert [board.token for board in registry.due("daily", now)] == [
        "hot",
        "due-cold",
    ]
    assert {board.token for board in registry.due("all", now)} == {
        "hot",
        "due-cold",
        "future-cold",
    }
    assert [board.token for board in registry.due("daily", now)] == [
        board.token for board in registry.due("daily", now)
    ]
    with pytest.raises(ValueError, match="unknown harvest mode"):
        registry.due("surprise", now)


def test_seven_consecutive_days_cover_each_cold_board_once() -> None:
    boards = [SimpleNamespace(token=f"board-{index}") for index in range(200)]
    first_day = date(2026, 8, 3)
    selected: list[str] = []

    for offset in range(7):
        day = first_day + timedelta(days=offset)
        selected.extend(board.token for board in daily_cold_slice(boards, day=day))

    assert Counter(selected) == Counter(board.token for board in boards)
    assert {shard_for_day(first_day + timedelta(days=i)) for i in range(7)} == set(
        range(7)
    )
    assert all(cold_shard(board.token) in range(7) for board in boards)


def test_shard_for_day_normalizes_aware_datetime_to_utc() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    instant_in_india = datetime(2026, 8, 8, 1, 0, tzinfo=india)
    same_in_utc = instant_in_india.astimezone(timezone.utc)

    assert shard_for_day(instant_in_india) == shard_for_day(same_in_utc)


def test_due_limit_bounds_bootstrap_batches(registry: BoardRegistry) -> None:
    for index in range(10):
        _add_board(registry, f"candidate-{index}")

    selected = registry.due("all", limit=3)

    assert len(selected) == 3
    assert len({board.id for board in selected}) == 3
    with pytest.raises(ValueError, match="limit must be positive"):
        registry.due("all", limit=0)


def test_public_aggregators_are_imported_as_hot(registry: BoardRegistry) -> None:
    registry.add_candidates(
        [{"name": "Remote OK", "ats": "remoteok", "token": "global"}]
    )

    board = registry.all()[0]
    assert board.tier == "hot"
    assert registry.due("hot")[0].provider == "remoteok"
