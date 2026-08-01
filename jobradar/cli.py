"""jobradar — a daily digest of openings you haven't seen yet."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from .digest import render_html, render_text
from .fetchers import REGISTRY, fetch_all
from .registry import BoardRegistry
from .scoring import filter_jobs, score_llm, score_local
from .store import Store

ROOT = Path(__file__).resolve().parent.parent


def load(name: str, override: str | None = None) -> dict:
    path = Path(override) if override else ROOT / "config" / name
    if not path.exists():
        sys.exit(f"missing config: {path}")
    return yaml.safe_load(path.read_text()) or {}


def cmd_doctor(args) -> None:
    """Verify every board token before you trust the digest."""
    companies = load("companies.yaml").get("companies", [])
    session = requests.Session()
    ok = dead = 0

    print(f"Probing {len(companies)} boards\n")
    for entry in companies:
        name, ats, token = entry.get("name"), entry.get("ats"), entry.get("token")
        fn = REGISTRY.get((ats or "").lower())
        if not fn:
            print(f"  ??  {name:<24} unknown ats '{ats}'")
            dead += 1
            continue
        try:
            found = fn(session, name, token)
            print(f"  ok  {name:<24} {ats:<15} {len(found):>4} openings")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  --  {name:<24} {ats:<15} {type(e).__name__}: {e}")
            dead += 1

    print(f"\n{ok} live, {dead} need fixing.")
    if dead:
        print(
            "Fix a token by opening the company's careers page and reading the\n"
            "board URL: jobs.lever.co/<token>, boards.greenhouse.io/<token>,\n"
            "jobs.ashbyhq.com/<token>. Paste that slug into companies.yaml."
        )


def cmd_run(args) -> None:
    seeds = load("companies.yaml", args.companies).get("companies", [])
    profile_path = args.profile or os.environ.get("JOBRADAR_PROFILE")
    profile = load("profile.yaml", profile_path)

    with BoardRegistry(args.db) as registry:
        registry.add_candidates(seeds, source="seed")
        boards = registry.due(mode=args.mode)

        entries = [
            {"name": board.company or board.token, "ats": board.provider, "token": board.token}
            for board in boards
        ]
        print(f"Fetching {len(entries)} due boards ({args.mode})...")
        raw, failures = fetch_all(entries)
        print(f"  {len(raw)} total openings, {len(failures)} boards unreachable")

        kept = filter_jobs(raw, profile)
        print(f"  {len(kept)} pass your filters")
        ranked = score_local(kept, profile)
        if args.llm:
            ranked = score_llm(ranked, profile)

        failed_companies = {failure["company"] for failure in failures}
        relevant_companies = {job.company for job in kept}
        for board in boards:
            label = board.company or board.token
            registry.mark_poll(
                board.id,
                success=label not in failed_companies,
                relevant=label in relevant_companies,
            )

    with Store(args.db) as store:
        run_id = store.start_run(args.mode)
        fresh = store.record(ranked)
        print(f"  {len(fresh)} are new clusters")

        rows = store.pending(min_score=args.min_score)
        out = render_html(rows, failures, Path(args.out))
        text_body = render_text(rows)
        print(f"\nDigest: {out.resolve()}")

        if args.print:
            print()
            print(text_body)

        mail_error = None
        mail_status = "not-requested"
        if args.email:
            from .mailer import MailDeliveryError, send_digest

            try:
                result = send_digest(
                    subject=f"Intern Radar — {len(rows)} new roles",
                    text_body=text_body,
                    html_body=out.read_text(encoding="utf-8"),
                    settings=None,
                    dry_run=args.dry_run,
                )
                if result.sent:
                    mail_status = "sent"
                    store.mark_notified(row["uid"] for row in rows)
                    print(f"Email accepted for {result.recipients} recipient(s)")
                else:
                    mail_status = result.status
                    mail_error = result.detail
                    print(f"Email not sent: {mail_error}")
            except MailDeliveryError as exc:
                mail_status = "failed"
                mail_error = str(exc)
                print(f"Email not sent: {mail_error}")

        store.finish_run(
            run_id,
            sources_ok=max(len(boards) - len(failures), 0),
            sources_failed=len(failures),
            jobs_seen=len(raw),
            error=mail_error,
        )
        store.prune(days=90)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": args.mode,
        "boards_due": len(boards),
        "sources_ok": max(len(boards) - len(failures), 0),
        "sources_failed": len(failures),
        "jobs_seen": len(raw),
        "jobs_matching": len(kept),
        "new_clusters": len(fresh),
        "pending_digest": len(rows),
        "mail_status": mail_status,
        "partial_coverage": bool(failures),
    }
    report_path = Path(args.report) if args.report else Path(args.db).with_name("run-report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if boards and len(failures) == len(boards):
        raise SystemExit("all due boards failed; coverage digest was still generated")


def cmd_mark(args) -> None:
    store = Store(args.db)
    n = store.mark(args.uid, args.status)
    print(f"marked {n} row(s) as {args.status}")
    store.close()


def cmd_stats(args) -> None:
    store = Store(args.db)
    s = store.stats()
    print(
        f"tracked: {s['total']}   applied: {s['applied']}   "
        f"new in 24h: {s['last_24h']}"
    )
    store.close()


def cmd_discover(args) -> None:
    """Harvest unverified ATS board candidates from a Common Crawl index."""
    from .discovery import discover_candidates, serialize_candidates

    candidates = discover_candidates(
        args.index,
        patterns=args.pattern or None,
        page_size=args.page_size,
        max_pages_per_pattern=args.max_pages,
    )
    with BoardRegistry(args.db) as registry:
        registry.add_candidates(candidates, source=f"common-crawl:{args.index}")
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialize_candidates(candidates), encoding="utf-8")
    print(f"Discovered {len(candidates)} unique unverified board candidates")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="jobradar", description=__doc__)
    p.add_argument("--db", default="data/state.sqlite")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check every configured board")
    d.set_defaults(func=cmd_doctor)

    r = sub.add_parser("run", help="fetch, rank, and write today's digest")
    r.add_argument("--out", default="out/digest.html")
    r.add_argument("--report", help="redacted JSON run report path")
    r.add_argument("--min-score", type=float, default=0.0)
    r.add_argument("--mode", choices=["hot", "daily", "all"], default="daily")
    r.add_argument("--companies", help="override seed companies YAML path")
    r.add_argument("--profile", help="override profile YAML path")
    r.add_argument("--email", action="store_true", help="send through configured SMTP")
    r.add_argument("--dry-run", action="store_true", help="prepare but do not send email")
    r.add_argument("--llm", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--print", action="store_true", help="also print to stdout")
    r.set_defaults(func=cmd_run)

    m = sub.add_parser("mark", help="mark a job applied/ignored")
    m.add_argument("uid")
    m.add_argument("status", choices=["applied", "ignored", "closed", "new"])
    m.set_defaults(func=cmd_mark)

    s = sub.add_parser("stats", help="show counts")
    s.set_defaults(func=cmd_stats)

    discover = sub.add_parser("discover", help="discover ATS boards via Common Crawl")
    discover.add_argument("--index", required=True, help="e.g. CC-MAIN-2026-30")
    discover.add_argument("--pattern", action="append", help="override CDX URL pattern")
    discover.add_argument("--page-size", type=int, default=5)
    discover.add_argument("--max-pages", type=int, default=100)
    discover.add_argument("--out", default="data/discovered-candidates.json")
    discover.set_defaults(func=cmd_discover)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="  %(message)s",
    )
    args.func(args)


if __name__ == "__main__":
    main()
