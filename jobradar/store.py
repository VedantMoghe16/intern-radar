"""SQLite persistence for clustered jobs and their source postings."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Job

VALID_STATUSES = {"new", "applied", "ignored", "closed"}

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA auto_vacuum = INCREMENTAL;

CREATE TABLE IF NOT EXISTS jobs (
    uid                 TEXT PRIMARY KEY,
    company             TEXT NOT NULL,
    title               TEXT NOT NULL,
    location            TEXT NOT NULL DEFAULT '',
    department          TEXT NOT NULL DEFAULT '',
    posted_at           TEXT,
    first_seen_epoch    INTEGER NOT NULL,
    last_seen_epoch     INTEGER NOT NULL,
    score               REAL NOT NULL DEFAULT 0,
    reasons             TEXT NOT NULL DEFAULT '',
    function            TEXT NOT NULL DEFAULT 'Ops/Other',
    function_confidence REAL NOT NULL DEFAULT 0,
    company_tier        TEXT NOT NULL DEFAULT 'Unknown',
    score_components    TEXT NOT NULL DEFAULT '{}',
    timeline_label      TEXT NOT NULL DEFAULT 'Dates unspecified',
    timeline_start_month INTEGER,
    timeline_end_month  INTEGER,
    timeline_year       INTEGER,
    timeline_confidence REAL NOT NULL DEFAULT 0,
    notified_epoch      INTEGER,
    status              TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'applied', 'ignored', 'closed'))
);

CREATE TABLE IF NOT EXISTS job_sources (
    source_uid       TEXT PRIMARY KEY,
    job_uid          TEXT NOT NULL REFERENCES jobs(uid) ON DELETE CASCADE,
    provider         TEXT NOT NULL DEFAULT '',
    external_id      TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    first_seen_epoch INTEGER NOT NULL,
    last_seen_epoch  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_epoch);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_sources_job ON job_sources(job_uid);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_epoch   INTEGER NOT NULL,
    finished_epoch  INTEGER,
    mode            TEXT NOT NULL,
    sources_ok      INTEGER NOT NULL DEFAULT 0,
    sources_failed  INTEGER NOT NULL DEFAULT 0,
    jobs_seen       INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS deliveries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_key      TEXT NOT NULL UNIQUE,
    attempted_epoch INTEGER NOT NULL,
    sent_epoch      INTEGER,
    error           TEXT
);
"""


def _epoch() -> int:
    return int(time.time())


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    """Own the durable state for jobs while preserving the prototype API."""

    def __init__(self, path: str | Path = "data/state.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "notified_epoch" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN notified_epoch INTEGER")
        timeline_columns = {
            "timeline_label": "TEXT NOT NULL DEFAULT 'Dates unspecified'",
            "timeline_start_month": "INTEGER",
            "timeline_end_month": "INTEGER",
            "timeline_year": "INTEGER",
            "timeline_confidence": "REAL NOT NULL DEFAULT 0",
        }
        for name, declaration in timeline_columns.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        self.conn.execute("PRAGMA user_version = 2")
        self.conn.commit()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def record(self, jobs: Iterable[Job]) -> list[Job]:
        """Upsert clusters and source records; return never-before-seen clusters."""
        now = _epoch()
        fresh: list[Job] = []
        seen_in_batch: set[str] = set()

        with self.conn:
            for raw_job in jobs:
                job = raw_job.normalized()
                existing = self.conn.execute(
                    "SELECT uid, status FROM jobs WHERE uid = ?", (job.uid,)
                ).fetchone()
                reasons = " | ".join(job.reasons)
                components = _json(job.score_components)

                if existing is None:
                    self.conn.execute(
                        """INSERT INTO jobs
                           (uid, company, title, location, department, posted_at,
                            first_seen_epoch, last_seen_epoch, score, reasons,
                            function, function_confidence, company_tier,
                            score_components, timeline_label,
                            timeline_start_month, timeline_end_month,
                            timeline_year, timeline_confidence)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            job.uid,
                            job.company,
                            job.title,
                            job.location,
                            job.department,
                            job.posted_at,
                            now,
                            now,
                            job.score,
                            reasons,
                            job.function,
                            job.function_confidence,
                            job.company_tier,
                            components,
                            job.timeline_label,
                            job.timeline_start_month,
                            job.timeline_end_month,
                            job.timeline_year,
                            job.timeline_confidence,
                        ),
                    )
                    if job.uid not in seen_in_batch:
                        fresh.append(job)
                        seen_in_batch.add(job.uid)
                else:
                    self.conn.execute(
                        """UPDATE jobs SET
                           last_seen_epoch = ?, score = ?, reasons = ?,
                           department = CASE WHEN ? != '' THEN ? ELSE department END,
                           posted_at = COALESCE(?, posted_at),
                           function = ?, function_confidence = ?, company_tier = ?,
                           score_components = ?, timeline_label = ?,
                           timeline_start_month = ?, timeline_end_month = ?,
                           timeline_year = ?, timeline_confidence = ?
                           WHERE uid = ?""",
                        (
                            now,
                            job.score,
                            reasons,
                            job.department,
                            job.department,
                            job.posted_at,
                            job.function,
                            job.function_confidence,
                            job.company_tier,
                            components,
                            job.timeline_label,
                            job.timeline_start_month,
                            job.timeline_end_month,
                            job.timeline_year,
                            job.timeline_confidence,
                            job.uid,
                        ),
                    )

                source_urls = list(dict.fromkeys(job.source_urls or ([job.url] if job.url else [])))
                if not source_urls:
                    source_urls = [""]
                for index, url in enumerate(source_urls):
                    source_uid = job.source_uid
                    if index:
                        source_uid = f"{source_uid}:{index}"
                    self.conn.execute(
                        """INSERT INTO job_sources
                           (source_uid, job_uid, provider, external_id, url,
                            first_seen_epoch, last_seen_epoch)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(source_uid) DO UPDATE SET
                            job_uid = excluded.job_uid,
                            url = CASE WHEN excluded.url != '' THEN excluded.url ELSE url END,
                            last_seen_epoch = excluded.last_seen_epoch""",
                        (
                            source_uid,
                            job.uid,
                            job.ats,
                            job.external_id or "",
                            url,
                            now,
                            now,
                        ),
                    )

                first_epoch = now
                if existing is not None:
                    first_epoch = self.conn.execute(
                        "SELECT first_seen_epoch FROM jobs WHERE uid = ?", (job.uid,)
                    ).fetchone()[0]
                job.first_seen_at = datetime.fromtimestamp(
                    first_epoch, tz=timezone.utc
                ).isoformat(timespec="seconds")
        return fresh

    def recent(self, hours: int = 24, min_score: float = 0.0) -> list[dict]:
        """Return unseen-status clusters first observed inside an epoch window."""
        cutoff = _epoch() - max(hours, 0) * 3600
        rows = self.conn.execute(
            """SELECT j.*,
                      (SELECT url FROM job_sources s
                       WHERE s.job_uid = j.uid AND s.url != ''
                       ORDER BY s.first_seen_epoch, s.source_uid LIMIT 1) AS url,
                      (SELECT provider FROM job_sources s
                       WHERE s.job_uid = j.uid
                       ORDER BY s.first_seen_epoch, s.source_uid LIMIT 1) AS ats,
                      datetime(j.first_seen_epoch, 'unixepoch') AS first_seen_at,
                      datetime(j.last_seen_epoch, 'unixepoch') AS last_seen_at
               FROM jobs j
               WHERE j.status = 'new' AND j.score >= ?
                 AND j.first_seen_epoch >= ?
               ORDER BY j.score DESC, j.first_seen_epoch DESC, j.uid""",
            (min_score, cutoff),
        ).fetchall()
        return [dict(row) for row in rows]

    def sources_for(self, uid: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM job_sources WHERE job_uid = ? ORDER BY source_uid", (uid,)
        ).fetchall()
        return [dict(row) for row in rows]

    def pending(self, min_score: float = 0.0, limit: int = 500) -> list[dict]:
        """Return jobs not yet included in a successfully accepted email."""
        rows = self.conn.execute(
            """SELECT j.*,
                      (SELECT url FROM job_sources s
                       WHERE s.job_uid = j.uid AND s.url != ''
                       ORDER BY s.first_seen_epoch, s.source_uid LIMIT 1) AS url,
                      (SELECT provider FROM job_sources s
                       WHERE s.job_uid = j.uid
                       ORDER BY s.first_seen_epoch, s.source_uid LIMIT 1) AS ats,
                      datetime(j.first_seen_epoch, 'unixepoch') AS first_seen_at,
                      datetime(j.last_seen_epoch, 'unixepoch') AS last_seen_at
               FROM jobs j
               WHERE j.status = 'new' AND j.notified_epoch IS NULL AND j.score >= ?
               ORDER BY j.score DESC, j.first_seen_epoch DESC, j.uid
               LIMIT ?""",
            (min_score, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_notified(self, uids: Iterable[str], when: int | None = None) -> int:
        values = list(dict.fromkeys(uids))
        if not values:
            return 0
        when = _epoch() if when is None else when
        placeholders = ",".join("?" for _ in values)
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE jobs SET notified_epoch = ? WHERE uid IN ({placeholders})",
                (when, *values),
            )
        return cur.rowcount

    def mark(self, uid_prefix: str, status: str) -> int:
        """Mark one unambiguous cluster; refuse broad prefixes."""
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        escaped = uid_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        matches = self.conn.execute(
            "SELECT uid FROM jobs WHERE uid LIKE ? ESCAPE '\\'", (f"{escaped}%",)
        ).fetchall()
        if len(matches) > 1:
            raise ValueError(f"uid prefix is ambiguous ({len(matches)} matches)")
        if not matches:
            return 0
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET status = ? WHERE uid = ?", (status, matches[0]["uid"])
            )
        return 1

    def stats(self) -> dict[str, int]:
        cutoff = _epoch() - 86400
        execute = self.conn.execute
        return {
            "total": execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "applied": execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'applied'"
            ).fetchone()[0],
            "last_24h": execute(
                "SELECT COUNT(*) FROM jobs WHERE first_seen_epoch >= ?", (cutoff,)
            ).fetchone()[0],
        }

    def start_run(self, mode: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs(started_epoch, mode) VALUES (?, ?)", (_epoch(), mode)
            )
        return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        sources_ok: int,
        sources_failed: int,
        jobs_seen: int,
        error: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE runs SET finished_epoch = ?, sources_ok = ?,
                   sources_failed = ?, jobs_seen = ?, error = ? WHERE id = ?""",
                (_epoch(), sources_ok, sources_failed, jobs_seen, error, run_id),
            )

    def prune(self, days: int = 90) -> int:
        """Prune bulky run history while retaining compact job identities/statuses."""
        cutoff = _epoch() - max(days, 1) * 86400
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM runs WHERE finished_epoch IS NOT NULL AND finished_epoch < ?",
                (cutoff,),
            )
            self.conn.execute("PRAGMA incremental_vacuum(32)")
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()
