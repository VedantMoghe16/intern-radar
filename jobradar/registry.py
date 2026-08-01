"""Durable board registry and lifecycle transitions."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .scheduler import cold_shard, shard_for_day

BOARD_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS boards (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider          TEXT NOT NULL,
    token             TEXT NOT NULL,
    company           TEXT NOT NULL DEFAULT '',
    lifecycle         TEXT NOT NULL DEFAULT 'candidate'
        CHECK (lifecycle IN ('candidate','verified','active','cooling','dormant','retired')),
    tier              TEXT NOT NULL DEFAULT 'cold'
        CHECK (tier IN ('hot','cold')),
    relevant_ever     INTEGER NOT NULL DEFAULT 0,
    last_relevant_epoch INTEGER,
    discovered_epoch  INTEGER NOT NULL,
    last_probe_epoch  INTEGER,
    last_poll_epoch   INTEGER,
    last_success_epoch INTEGER,
    next_poll_epoch   INTEGER,
    failure_count     INTEGER NOT NULL DEFAULT 0,
    etag              TEXT,
    last_modified     TEXT,
    source            TEXT NOT NULL DEFAULT 'seed',
    UNIQUE(provider, token)
);

CREATE INDEX IF NOT EXISTS idx_boards_due ON boards(tier, lifecycle, next_poll_epoch);
"""

VALID_LIFECYCLES = {"candidate", "verified", "active", "cooling", "dormant", "retired"}


def _epoch() -> int:
    return int(time.time())


@dataclass(frozen=True, slots=True)
class Board:
    id: int
    provider: str
    token: str
    company: str
    lifecycle: str
    tier: str
    relevant_ever: bool
    last_poll_epoch: int | None
    next_poll_epoch: int | None
    failure_count: int
    etag: str | None = None
    last_modified: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Board":
        return cls(
            id=row["id"],
            provider=row["provider"],
            token=row["token"],
            company=row["company"],
            lifecycle=row["lifecycle"],
            tier=row["tier"],
            relevant_ever=bool(row["relevant_ever"]),
            last_poll_epoch=row["last_poll_epoch"],
            next_poll_epoch=row["next_poll_epoch"],
            failure_count=row["failure_count"],
            etag=row["etag"],
            last_modified=row["last_modified"],
        )


class BoardRegistry:
    def __init__(self, path: str | Path = "data/state.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(BOARD_SCHEMA)
        columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(boards)").fetchall()
        }
        if "last_relevant_epoch" not in columns:
            self.conn.execute("ALTER TABLE boards ADD COLUMN last_relevant_epoch INTEGER")
        self.conn.commit()

    def __enter__(self) -> "BoardRegistry":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def add_candidates(self, entries: Iterable[dict], source: str = "seed") -> int:
        """Idempotently import discovered or seed board candidates."""
        now = _epoch()
        changed = 0
        with self.conn:
            for entry in entries:
                provider = str(entry.get("ats") or entry.get("provider") or "").strip().lower()
                token = str(entry.get("token") or "").strip()
                company = str(entry.get("name") or entry.get("company") or "").strip()
                if not provider or not token:
                    continue
                next_poll = now
                if source != "seed":
                    days_until = (cold_shard(f"{provider}:{token}") - shard_for_day()) % 7
                    next_poll = now + days_until * 86400
                cur = self.conn.execute(
                    """INSERT INTO boards
                       (provider, token, company, discovered_epoch, next_poll_epoch, source)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(provider, token) DO UPDATE SET
                         company = CASE WHEN excluded.company != '' THEN excluded.company ELSE company END""",
                    (provider, token, company, now, next_poll, source),
                )
                changed += max(cur.rowcount, 0)
        return changed

    def all(self) -> list[Board]:
        rows = self.conn.execute("SELECT * FROM boards ORDER BY provider, token").fetchall()
        return [Board.from_row(row) for row in rows]

    def due(self, mode: str = "daily", now: int | None = None) -> list[Board]:
        """Return hot boards every run and due cold boards for daily/all modes."""
        now = _epoch() if now is None else now
        if mode == "hot":
            tier_clause = "tier = 'hot'"
        elif mode == "daily":
            tier_clause = "(tier = 'hot' OR (tier = 'cold' AND COALESCE(next_poll_epoch, 0) <= ?))"
        elif mode == "all":
            tier_clause = "1 = 1"
        else:
            raise ValueError(f"unknown harvest mode: {mode}")

        params: tuple[int, ...] = (now,) if "?" in tier_clause else ()
        rows = self.conn.execute(
            f"""SELECT * FROM boards
                WHERE lifecycle != 'retired' AND {tier_clause}
                ORDER BY CASE tier WHEN 'hot' THEN 0 ELSE 1 END,
                         COALESCE(next_poll_epoch, 0), id""",
            params,
        ).fetchall()
        return [Board.from_row(row) for row in rows]

    def mark_probe(self, board_id: int, *, live: bool, company: str = "") -> None:
        now = _epoch()
        lifecycle = "verified" if live else "candidate"
        with self.conn:
            self.conn.execute(
                """UPDATE boards SET last_probe_epoch = ?, lifecycle = ?,
                   company = CASE WHEN ? != '' THEN ? ELSE company END,
                   failure_count = CASE WHEN ? THEN 0 ELSE failure_count + 1 END
                   WHERE id = ?""",
                (now, lifecycle, company, company, int(live), board_id),
            )

    def mark_poll(
        self,
        board_id: int,
        *,
        success: bool,
        relevant: bool = False,
        etag: str | None = None,
        last_modified: str | None = None,
        now: int | None = None,
    ) -> None:
        """Update health and schedule; transient failures back off without retiring."""
        now = _epoch() if now is None else now
        row = self.conn.execute("SELECT * FROM boards WHERE id = ?", (board_id,)).fetchone()
        if row is None:
            raise KeyError(board_id)

        failures = 0 if success else int(row["failure_count"]) + 1
        tier = row["tier"]
        if relevant:
            tier = "hot"
        elif tier == "hot" and row["last_relevant_epoch"] is not None:
            if now - int(row["last_relevant_epoch"]) >= 30 * 86400:
                tier = "cold"
        lifecycle = "active" if success else row["lifecycle"]
        if not success and failures >= 3 and lifecycle in {"active", "verified"}:
            lifecycle = "cooling"
        if not success and failures >= 8:
            lifecycle = "dormant"

        if success:
            interval = 12 * 3600 if tier == "hot" else 7 * 86400
        else:
            interval = min(6 * 3600 * (2 ** min(failures - 1, 4)), 7 * 86400)

        with self.conn:
            self.conn.execute(
                """UPDATE boards SET lifecycle = ?, tier = ?,
                   relevant_ever = CASE WHEN ? THEN 1 ELSE relevant_ever END,
                   last_relevant_epoch = CASE WHEN ? THEN ? ELSE last_relevant_epoch END,
                   last_poll_epoch = ?,
                   last_success_epoch = CASE WHEN ? THEN ? ELSE last_success_epoch END,
                   next_poll_epoch = ?, failure_count = ?,
                   etag = COALESCE(?, etag), last_modified = COALESCE(?, last_modified)
                   WHERE id = ?""",
                (
                    lifecycle,
                    tier,
                    int(relevant),
                    int(relevant),
                    now,
                    now,
                    int(success),
                    now,
                    now + interval,
                    failures,
                    etag,
                    last_modified,
                    board_id,
                ),
            )

    def retire(self, board_id: int) -> None:
        """Explicit retirement is separate from ordinary failure backoff."""
        with self.conn:
            self.conn.execute(
                "UPDATE boards SET lifecycle = 'retired' WHERE id = ?", (board_id,)
            )

    def close(self) -> None:
        self.conn.close()
