# Intern Radar v1 specification

## Product promise

Intern Radar discovers public internship postings, prioritizes India-based and
remote roles across all functions, and sends one concise daily email. The
baseline must run without a paid API, a hand-maintained company list, or an LLM.

Common Crawl is a candidate-discovery source, not a source of truth. Every ATS
board is live-probed before entering the registry, and board health is tracked
independently from relevance. A failure in discovery, one connector, or one
malformed posting must not prevent a digest from being produced from the data
that remains available.

## Three loops

1. **Discovery, monthly:** query current Common Crawl indexes for supported ATS
   URL shapes, canonicalize candidates, probe public endpoints, and update board
   lifecycle and health fields. Seed configuration exists only for migration.
2. **Harvest, twice daily:** poll hot boards every run. Poll one deterministic
   seventh of cold boards on the morning run, using provider-specific lightweight
   listing requests before fetching descriptions for plausible matches.
3. **Digest, daily:** after the morning harvest, cluster, filter, score, render,
   and optionally deliver an HTML/plain-text email around 08:00 IST. GitHub
   Actions schedules are best-effort, so this time is a target rather than an SLA.

## Board scheduling

Boards move through `candidate`, `verified`, `hot`, `cooling`, `dormant`, and
`retired` states. A relevant role promotes a board to hot. Promotion is not
permanent: boards with no relevant postings gradually cool, while recent or
historically active boards are checked more often. The v1 cold-board invariant
is that every healthy eligible board is scheduled at least once in seven days.
Repeated temporary failures trigger jittered exponential backoff; they do not
immediately retire a board. Concurrency and delay limits apply per host as well
as globally, and conditional requests use ETag or Last-Modified when supported.

## Sources and legal boundary

V1 supports public ATS JSON endpoints and free, no-auth job APIs. JSON-LD on
company-owned careers sites may follow after structured connectors are stable.
It does not scrape LinkedIn, Internshala, or Naukri.

An inbox connector is optional and disabled by default. It reads alerts that the
user explicitly asked those services to deliver. V1 should use read-only IMAP,
deduplicate by RFC Message-ID plus content hash, and retain normalized public job
fields only. Raw mail, credentials, private links, and tracking parameters must
never be committed, logged, or placed in artifacts.

## Normalization and identity

A source posting keeps its provider, external identifier, canonical URL, first
and last fetch times, and raw provenance. A job is a cross-source cluster rather
than a URL. Its candidate key uses normalized company, title, and location, with
team, requisition, season, and intake signals used to avoid merging distinct
openings. Every source URL remains attached to the cluster.

Summer 2027 and May 2028 graduation signals are preferences, not hard filters.
Undated internships and adjacent intake language remain eligible at a lower
score. Unknown locations are retained with lower confidence rather than silently
dropped.

## Digest facets

All four requested dimensions appear as layers in one digest:

- **Function:** Product, Applied AI/ML, Software, Data/Analytics, Quant, Design,
  and Ops/Other section headings, assigned by deterministic rules in v1.
- **Freshness:** age-first ordering and a prominent under-24-hour signal.
- **Fit:** a top-picks block and an explainable score composed of intake,
  location, function confidence, freshness, and profile keyword components.
- **Company tier:** Big Tech, Scaled, Startup, or Unknown. Only known mappings
  receive a confident label; hiring volume alone must not infer maturity.

The email also reports generated time, connector coverage, stale sources, and
failures. “Nothing new” must be distinguished from “coverage was unavailable.”

## Delivery

Delivery uses generic SMTP from the standard library and sends
`multipart/alternative` HTML and plain text. Configuration is supplied only by
environment variables:

- `JOBRADAR_SMTP_HOST`, `JOBRADAR_SMTP_PORT`
- `JOBRADAR_SMTP_USERNAME`, `JOBRADAR_SMTP_PASSWORD`
- `JOBRADAR_SMTP_SECURITY` (`starttls`, `ssl`, or `plain`)
- `JOBRADAR_EMAIL_FROM`, `JOBRADAR_EMAIL_TO`
- `JOBRADAR_MAIL_DRY_RUN` for rendering without sending

If the SMTP host is absent, the run succeeds in artifact-only mode. If a
configured SMTP delivery fails, jobs remain pending for retry and the run report
records a redacted error. Jobs are selected by unsent delivery state, not merely
“seen in the last 24 hours,” and are marked notified only after SMTP acceptance.
Delivery is at-least-once: a crash after acceptance but before state persistence
can produce one duplicate email.

## Persistence and privacy

SQLite uses transactional, versioned migrations and integer UTC timestamps.
Detailed descriptions and run diagnostics expire after 90 days, while compact
identity tombstones and user statuses remain so old jobs do not reappear. Routine
full `VACUUM` is prohibited because it rewrites the database; a new database may
use incremental auto-vacuum.

The workflow persists only `state.sqlite` on a dedicated `state` branch. Writers
are serialized, and Git plumbing creates a state-only commit so source files,
profiles, output, and secrets cannot be swept into the commit accidentally.
Actions caches and artifacts are not the durable source of truth because they
expire. The real profile is gitignored and may be injected as
`PROFILE_YAML_B64`; only a non-personal example should be public.

## Acceptance criteria

1. A fresh clone installs with `pip install -e '.[dev]'`; offline tests pass and
   `python -m jobradar --help` works.
2. The default path uses no paid service and requires no LLM or API key.
3. Every eligible cold board is deterministically scheduled within seven days;
   hot boards run twice daily and unhealthy boards back off without disappearing.
4. Failure of one connector or malformed posting yields a partial-coverage
   digest; only a total pipeline failure suppresses it.
5. Two source postings for the same opening create one job with both sources;
   distinct requisitions or intakes are not incorrectly merged.
6. A rerun creates neither a duplicate job nor a duplicate successful delivery.
   SMTP failure leaves the digest pending for the next run.
7. Missing SMTP configuration produces HTML/text artifacts and a successful,
   explicitly degraded result. Dry-run mode never opens a network connection.
8. The digest shows all four facets and exposes the components of its fit score.
9. Database migrations and cleanup are idempotent; 90-day cleanup preserves
   compact identity history and applied/ignored state.
10. Workflow runs are serialized, tests precede harvesting, artifacts upload
    even after harvest or mail failure, and secrets/private profile/raw inbox
    data never appear in Git, logs, or artifacts.
