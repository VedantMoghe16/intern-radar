# Intern Radar implementation plan

The phases are ordered so each one leaves a runnable, useful system. The strict
free baseline remains the default throughout.

## Phase 1 — repair and deterministic core

- Package the flat prototype as `jobradar`, add the missing job model, and move
  public configuration and tests to their documented locations.
- Define normalized job/source records, deterministic function classification,
  explainable fit components, and conservative cross-source clustering.
- Add fixture-backed tests for normalization, filtering, clustering, scoring,
  rendering, malformed inputs, and repeat runs.
- Render HTML and text artifacts even when one source fails.

Exit condition: a fresh offline install passes tests and can render the fixture
digest with partial-coverage metadata.

## Phase 2 — SMTP delivery and workflow shell

- Integrate `jobradar.mailer` with `run --email`; mark delivery successful only
  after SMTP acceptance.
- Add morning `daily` and evening `hot` workflow modes, serialized with one
  concurrency group. Inject an optional private profile without printing it.
- Upload redacted output with `if: always()` and persist the database only on a
  dedicated state branch.

Exit condition: missing credentials degrade to artifact-only, dry-run contacts
no network, and a local SMTP fixture receives multipart HTML/text exactly once.

## Phase 3 — durable state and adaptive scheduling

- Introduce versioned SQLite migrations for boards, source postings, clustered
  jobs, delivery attempts, run results, and optional inbox message identities.
- Import the existing company YAML once as seed candidates, not permanent truth.
- Implement hot polling twice daily and deterministic seven-way cold sharding.
  Add cooling, health-aware backoff, conditional requests, and per-host limits.
- Harden the implemented dedicated state-branch persistence with recovery and
  migration tests. Avoid routine full `VACUUM` and database commits on the
  source branch.

Exit condition: two concurrent manual triggers cannot corrupt or overwrite
state, every cold board meets the seven-day bound, and an interrupted delivery
is safely retried.

## Phase 4 — automated discovery

- Query the newest Common Crawl indexes for supported ATS URL shapes.
- Canonicalize and live-probe candidates with bounded concurrency, honest user
  agent, provider-specific pacing, jittered backoff, and health telemetry.
- Track lifecycle transitions without letting discovery failure block harvest.
- Benchmark candidate volume, response size, runtime, and rate limits before
  asserting capacity numbers.

Exit condition: a monthly run expands or repairs the registry without manual
company edits and without destabilizing the twice-daily harvest.

## Phase 5 — additional free sources

- Add fixture-tested connectors for selected no-auth aggregators one at a time.
- Enable optional read-only IMAP ingestion behind a feature flag. Store only
  Message-ID/hash and normalized public fields; sanitize tracking URLs.
- Add JSON-LD discovery only after structured connectors are operationally
  stable, because its crawl and normalization surface is substantially larger.

Exit condition: each connector has independent coverage/error reporting and can
be disabled without affecting other sources or email delivery.

## Phase 6 — quality and operations

- Tune segmentation and fit weights against labeled examples; keep deterministic
  rules as the free production path.
- Add stale-source warnings, last-success reporting, retirement review, cleanup,
  backup/restore exercises, and recovery documentation for disabled scheduled
  workflows or rejected state pushes.
- Review artifacts and logs for personal data and secret leakage before making a
  repository public.

Exit condition: a 30-day unattended run stays within free limits, reports its
coverage honestly, preserves state, and recovers from connector and mail outages.
