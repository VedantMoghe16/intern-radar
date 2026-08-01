# Intern Radar

Intern Radar builds a daily email digest of internship openings for India and
worldwide remote roles. It clusters copies from different sources, groups roles
by function, exposes a deterministic fit score, and keeps failed sources from
blocking the rest of the digest. The baseline uses no paid API or LLM.

## What is implemented

- Installable `jobradar` package and offline pytest suite.
- Stable job-cluster and source-posting identities.
- Six public ATS connectors: Greenhouse, Lever, Ashby, SmartRecruiters,
  Workable, and Recruitee.
- Generated SQLite board registry with hot/cold scheduling, seven-day cold
  coverage, promotion, lifecycle state, and health backoff.
- India/remote internship filters, deterministic function segmentation, and
  explainable local scoring.
- HTML/plain-text digest with top picks, function sections, freshness, company
  tier, and partial-coverage warnings.
- Optional generic SMTP delivery. Jobs are marked notified only after SMTP
  acceptance.
- Serialized GitHub Actions runs with a dedicated durable `state` branch.

Common Crawl discovery and additional free sources are being added behind the
same registry contract. See [the specification](docs/SPEC.md) and
[implementation plan](docs/IMPLEMENTATION_PLAN.md).

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
python -m jobradar --help
python -m jobradar run --mode daily --out out/digest.html --print
```

`config/companies.yaml` is only a migration seed. The SQLite registry owns the
working board list after import; automated discovery expands it.

The personal `config/profile.yaml` is gitignored. Copy or create it locally, or
set `JOBRADAR_PROFILE` to another YAML path. The included schema accepts the
prototype's `skills`, `boosts`, `title_include`, `title_exclude`, `locations`,
`require_intern_signal`, and `drop_senior_titles` keys.

## Email

Set these variables and add `--email`:

```bash
export JOBRADAR_SMTP_HOST=smtp.gmail.com
export JOBRADAR_SMTP_PORT=587
export JOBRADAR_SMTP_SECURITY=starttls
export JOBRADAR_SMTP_USERNAME=alerts@example.com
export JOBRADAR_SMTP_PASSWORD='app-password'
export JOBRADAR_EMAIL_FROM=alerts@example.com
export JOBRADAR_EMAIL_TO=you@example.com
python -m jobradar run --mode daily --email
```

Missing mail settings produce the digest artifact without failing the harvest.
Use `--dry-run --email` to build the complete message without opening a network
connection.

## Commands

```bash
python -m jobradar doctor
python -m jobradar run --mode hot|daily|all [--email] [--print]
python -m jobradar mark <uid-prefix> applied|ignored|closed|new
python -m jobradar stats
```

GitHub's schedule is best-effort: the morning job targets 08:00 IST and sends
the digest; the evening job refreshes hot boards without sending a second mail.
