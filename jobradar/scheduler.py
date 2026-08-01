"""Pure scheduling helpers used by the registry and offline tests."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import TypeVar

T = TypeVar("T")


def cold_shard(token: str, window_days: int = 7) -> int:
    """Assign a board to a deterministic shard without Python hash randomization."""
    if window_days < 1:
        raise ValueError("window_days must be positive")
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % window_days


def shard_for_day(day: date | datetime | None = None, window_days: int = 7) -> int:
    if day is None:
        day = datetime.now(timezone.utc).date()
    elif isinstance(day, datetime):
        day = day.astimezone(timezone.utc).date() if day.tzinfo else day.date()
    return day.toordinal() % window_days


def daily_cold_slice(
    boards: Iterable[T],
    *,
    token_getter=lambda board: board.token,
    day: date | datetime | None = None,
    window_days: int = 7,
) -> list[T]:
    target = shard_for_day(day, window_days)
    return [
        board
        for board in boards
        if cold_shard(str(token_getter(board)), window_days) == target
    ]
