"""Render the day's new openings.

The design has one job: let you scan forty rows in thirty seconds and
decide which three to open. Everything is subordinate to the age column,
because for this problem freshness is the signal — a role you find on day
one has a materially different outcome than the same role found on day 20.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

CSS = """
:root{
  --bg:#10151C; --panel:#171E27; --rule:#232C38;
  --ink:#C9D3DE; --dim:#6B7A8C; --bright:#F2F6FA;
  --hot:#FFB020; --warm:#4EC9A5; --cool:#5B7A99;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Inter,Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1080px;margin:0 auto;padding:48px 28px 96px}
header{border-bottom:1px solid var(--rule);padding-bottom:20px;margin-bottom:8px}
h1{
  margin:0; font-size:13px; font-weight:600; letter-spacing:.22em;
  text-transform:uppercase; color:var(--bright);
}
.meta{
  margin-top:10px; font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
  color:var(--dim); letter-spacing:.03em;
}
.meta b{color:var(--warm);font-weight:500}
.section{margin:34px 0 2px;font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--warm)}
.top{margin:22px 0 30px;padding:18px 20px;background:var(--panel);border:1px solid var(--rule)}
.top h2{margin:0 0 10px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--hot)}
.top a{color:var(--bright);text-decoration:none}
.top div{padding:4px 0;color:var(--dim)}
.row{
  display:grid; grid-template-columns:74px 1fr 96px;
  gap:20px; padding:18px 0 17px; border-bottom:1px solid var(--rule);
  align-items:baseline;
}
.row:hover{background:var(--panel)}
.age{
  font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;
  color:var(--dim); letter-spacing:.05em; text-align:right;
  border-right:2px solid var(--rule); padding-right:12px;
}
.age.hot{color:var(--hot);border-right-color:var(--hot)}
.age.warm{color:var(--warm);border-right-color:var(--warm)}
.title{margin:0 0 5px;font-size:15.5px;font-weight:500;letter-spacing:-.005em}
.title a{color:var(--bright);text-decoration:none;border-bottom:1px solid transparent}
.title a:hover,.title a:focus-visible{border-bottom-color:var(--hot);outline:none}
.sub{
  font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
  color:var(--dim);
}
.sub .co{color:var(--cool)}
.why{margin-top:6px;font-size:12.5px;color:var(--dim);font-style:italic}
.score{
  font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;
  color:var(--dim); text-align:right;
}
.score b{display:block;font-size:19px;color:var(--ink);font-weight:500}
.empty{padding:64px 0;color:var(--dim);font-size:14px}
.fail{
  margin-top:56px;padding-top:18px;border-top:1px solid var(--rule);
  font:11.5px/1.8 ui-monospace,SFMono-Regular,Menlo,monospace;color:#6B5B4A;
}
@media(max-width:640px){
  .row{grid-template-columns:56px 1fr;gap:14px}
  .score{grid-column:2;text-align:left;margin-top:6px}
  .score b{display:inline;font-size:13px}
}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def _age_label(hours: float | None) -> tuple[str, str]:
    if hours is None:
        return "new", "hot"
    if hours < 1:
        return "<1h", "hot"
    if hours < 6:
        return f"{int(hours)}h", "hot"
    if hours < 24:
        return f"{int(hours)}h", "warm"
    return f"{int(hours / 24)}d", ""


FUNCTION_ORDER = (
    "Product",
    "Applied AI/ML",
    "Software",
    "Data/Analytics",
    "Quant",
    "Design",
    "Ops/Other",
)


def _posting_hours(row: dict, now: datetime) -> float | None:
    for key in ("posted_at", "first_seen_at"):
        value = row.get(key)
        if not value:
            continue
        try:
            seen = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            return max(0.0, (now - seen.astimezone(timezone.utc)).total_seconds() / 3600)
        except ValueError:
            continue
    return None


def _render_row(row: dict, now: datetime) -> str:
    label, cls = _age_label(_posting_hours(row, now))
    escape = html.escape
    url = str(row.get("url") or "#")
    sub = f"<span class='co'>{escape(str(row.get('company') or 'Unknown'))}</span>"
    if row.get("company_tier"):
        sub += f" &nbsp;·&nbsp; {escape(str(row['company_tier']))}"
    if row.get("location"):
        sub += f" &nbsp;/&nbsp; {escape(str(row['location']))}"
    if row.get("department"):
        sub += f" &nbsp;/&nbsp; {escape(str(row['department']))}"

    reasons = str(row.get("reasons") or "")
    components = row.get("score_components")
    if components:
        try:
            values = json.loads(components) if isinstance(components, str) else components
            detail = ", ".join(f"{key} {value:g}" for key, value in values.items() if value)
            if detail:
                reasons = f"{reasons} | {detail}" if reasons else detail
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    why = f"<div class='why'>{escape(reasons)}</div>" if reasons else ""

    return (
        f"<div class='row'>"
        f"<div class='age {cls}'>{label}</div>"
        f"<div><p class='title'><a href='{escape(url, quote=True)}' "
        f"target='_blank' rel='noopener'>{escape(str(row.get('title') or 'Untitled role'))}</a></p>"
        f"<div class='sub'>{sub}</div>{why}</div>"
        f"<div class='score'><b>{float(row.get('score') or 0):.0f}</b>fit</div>"
        f"</div>"
    )


def render_html(rows: list[dict], failures: list[dict], out: Path) -> Path:
    now = datetime.now(timezone.utc)
    stamp = now.astimezone().strftime("%a %d %b %Y, %H:%M %Z")
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Job radar</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<header><h1>Job radar</h1>",
        f"<div class='meta'>{stamp} &nbsp;·&nbsp; {len(rows)} waiting "
        f"for this digest &nbsp;·&nbsp; <b>{len(failures)}</b> source failures</div></header>",
    ]

    if not rows:
        parts.append(
            "<div class='empty'>No unnotified matching roles. Check the coverage "
            "summary below before treating this as an all-clear.</div>"
        )

    if rows:
        parts.append("<div class='top'><h2>Top picks</h2>")
        for row in rows[:5]:
            parts.append(
                f"<div><b>{float(row.get('score') or 0):.0f}</b> &nbsp;"
                f"<a href='{html.escape(str(row.get('url') or '#'), quote=True)}'>"
                f"{html.escape(str(row.get('title') or 'Untitled role'))}</a>"
                f" &nbsp;·&nbsp; {html.escape(str(row.get('company') or 'Unknown'))}</div>"
            )
        parts.append("</div>")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        section = str(row.get("function") or "Ops/Other")
        grouped[section if section in FUNCTION_ORDER else "Ops/Other"].append(row)
    for section in FUNCTION_ORDER:
        if not grouped[section]:
            continue
        parts.append(f"<h2 class='section'>{html.escape(section)} · {len(grouped[section])}</h2>")
        parts.extend(_render_row(row, now) for row in grouped[section])

    if failures:
        parts.append("<div class='fail'>Partial coverage — boards that did not respond:<br>")
        parts += [
            f"&nbsp;&nbsp;{html.escape(f['company'])} — {html.escape(f['error'])}<br>"
            for f in failures
        ]
        parts.append("</div>")

    parts.append("</div></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    return out


def render_text(rows: list[dict]) -> str:
    if not rows:
        return "Job radar — no unnotified matching roles; check source coverage.\n"
    lines = [f"Job radar — {len(rows)} waiting for this digest\n" + "=" * 52]
    for section in FUNCTION_ORDER:
        section_rows = [r for r in rows if (r.get("function") or "Ops/Other") == section]
        if not section_rows:
            continue
        lines.append(f"\n{section.upper()}")
        for row in section_rows:
            lines.append(
                f"\n[{float(row.get('score') or 0):>3.0f}] {row['title']}\n"
                f"      {row['company']} · {row.get('company_tier') or 'Unknown'} · "
                f"{row.get('location') or 'location n/a'}\n"
                f"      {row.get('url') or ''}"
            )
    return "\n".join(lines) + "\n"
