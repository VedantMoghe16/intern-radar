"""Decide what counts as relevant.

Two stages, deliberately separate:

  filter()  - hard rules. Wrong country, wrong seniority, wrong batch year.
              Cheap, and cuts ~95% of the raw feed.
  score()   - soft ranking of what survives, against your own profile.

Scoring runs locally by default so the tool works with no API key at all.
Set ANTHROPIC_API_KEY and pass --llm to re-rank the shortlist with Claude.
"""

from __future__ import annotations

import re
from typing import Iterable

from .models import Job
from .segmentation import (
    classify_function_with_confidence,
    company_tier,
    freshness_bucket,
)
from .timeline import extract_timeline

# Titles that are never worth surfacing to a student, whatever else matches.
SENIORITY_BLOCK = re.compile(
    r"\b(senior|staff|principal|team lead|technical lead|lead engineer|head of|director|vp|vice president|"
    r"manager ii|manager iii|architect|chief|distinguished|fellow|"
    r"phd|postdoc|professor)\b",
    re.I,
)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]{2,}", text.lower()))


def _title_is_excluded(title: str, excluded_terms: list[str]) -> bool:
    if any(term in title for term in excluded_terms):
        return True
    # Treat the configured noun as its obvious recruiting-family variants.
    if "recruiter" in excluded_terms and re.search(
        r"\brecruit(?:er|ers|ing|ment|ment coordinator)\b", title, re.I
    ):
        return True
    return False


def filter_jobs(jobs: Iterable[Job], cfg: dict) -> list[Job]:
    """Apply hard include/exclude rules from profile.yaml."""
    include = [k.lower() for k in cfg.get("title_include", [])]
    exclude = [k.lower() for k in cfg.get("title_exclude", [])]
    locations = [l.lower() for l in cfg.get("locations", [])]
    require_intern = cfg.get("require_intern_signal", True)
    strict_internships = cfg.get("strict_internships_only", False)
    drop_senior = cfg.get("drop_senior_titles", True)

    intern_terms = (
        r"intern|internship|co-?op|apprentice|trainee|university|campus|"
        r"student|summer 20\d\d"
    )
    if cfg.get("include_new_grad", False):
        intern_terms += r"|new ?grad|graduate program|early career"
    intern_signal = re.compile(rf"\b({intern_terms})\b", re.I)
    strict_intern_signal = re.compile(r"\b(intern(?:ship)?s?|co-?ops?)\b", re.I)
    restricted_remote = re.compile(
        r"\b(remote|work from home)\b.{0,35}\b(us|u\.s\.|usa|canada|emea|uk|"
        r"united kingdom|europe)\s*(only|required|residents?|based)?\b|"
        r"\b(us|u\.s\.|usa|canada|emea|uk|united kingdom|europe)[ -]only\b",
        re.I,
    )

    kept = []
    for job in jobs:
        title_l = job.title.lower()
        blob = job.haystack

        if exclude and _title_is_excluded(title_l, exclude):
            continue
        if drop_senior and SENIORITY_BLOCK.search(job.title):
            continue
        early_career_text = f"{job.title} {job.department}"
        if require_intern:
            required_pattern = strict_intern_signal if strict_internships else intern_signal
            if not required_pattern.search(early_career_text):
                continue
        if include and not any(k in title_l for k in include):
            continue
        if locations:
            loc_l = (job.location or "").lower()
            # empty location is kept — many boards omit it, better a false
            # positive than a missed role
            if loc_l and not any(l in loc_l for l in locations):
                # An explicit foreign location is authoritative; incidental
                # work-from-home wording in the description must not override it.
                continue
            if not loc_l and not cfg.get("allow_unknown_location", True):
                if not re.search(r"\b(india|worldwide|anywhere|global)\b", blob, re.I):
                    continue
            if "remote" in loc_l and not cfg.get("allow_ambiguous_remote", True):
                if not re.search(r"\b(india|worldwide|anywhere|global)\b", blob, re.I):
                    continue
        restriction_text = f"{job.title} {job.location} {job.description[:800]}"
        if restricted_remote.search(restriction_text):
            if not re.search(r"\b(india|worldwide|anywhere|global)\b", blob, re.I):
                continue
        if job.ats in {"dreamwork", "arbeitnow"} and "remote" in blob:
            # These feeds are region-focused. Generic "Remote" does not mean
            # worldwide unless the record says so explicitly.
            if not re.search(r"\b(india|worldwide|anywhere|global)\b", blob, re.I):
                continue
        kept.append(job)
    return kept


def score_local(jobs: Iterable[Job], profile: dict) -> list[Job]:
    """Rank deterministically and expose each component used by the digest."""
    skills = {s.lower() for s in profile.get("skills", [])}
    boosts = {k.lower(): float(v) for k, v in (profile.get("boosts") or {}).items()}

    for job in jobs:
        blob = job.haystack
        toks = _tokens(blob)

        hits = sorted(s for s in skills if (" " in s and s in blob) or s in toks)
        skill_score = min(len(hits) / max(len(skills) * 0.25, 1), 1.0) * 45

        bonus, why = 0.0, []
        for key, weight in boosts.items():
            if key in blob:
                bonus += weight
                why.append(key)

        bonus_score = min(bonus, 25.0)
        location_score = 0.0
        if re.search(r"\b(india|bengaluru|bangalore|mumbai|hyderabad|delhi|"
                     r"gurgaon|gurugram|pune|noida|chennai)\b", blob):
            location_score = 10.0
        elif re.search(r"\b(remote|worldwide|anywhere)\b", blob):
            location_score = 7.0

        timeline = extract_timeline(job)
        job.timeline_label = timeline.label
        job.timeline_start_month = timeline.start_month
        job.timeline_end_month = timeline.end_month
        job.timeline_year = timeline.year
        job.timeline_confidence = timeline.confidence
        if timeline.year == 2027 and timeline.start_month in {5, 6, 7, 8}:
            intake_score = 10.0
        elif timeline.year == 2027:
            intake_score = 7.0
        elif timeline.year in {2026, 2028}:
            intake_score = 3.0
        else:
            intake_score = 2.0
        fresh = freshness_bucket(job)
        freshness_score = {"new": 5.0, "recent": 2.5}.get(fresh, 0.0)

        classification = classify_function_with_confidence(job)
        job.function = classification.function
        job.function_confidence = classification.confidence
        job.company_tier = company_tier(
            job.company, known_tiers=profile.get("company_tiers") or {}
        )
        company_score = {"Big Tech": 10.0, "Scaled": 7.0, "Startup": 4.0}.get(
            job.company_tier, 0.0
        )
        job.score_components = {
            "skills": round(skill_score, 1),
            "boosts": round(bonus_score, 1),
            "location": location_score,
            "intake": intake_score,
            "freshness": freshness_score,
            "function": round(job.function_confidence * 5, 1),
            "company": company_score,
        }
        job.score = round(min(sum(job.score_components.values()), 100.0), 1)
        job.reasons = (
            ([f"skills: {', '.join(hits[:6])}"] if hits else [])
            + ([f"boost: {', '.join(why[:4])}"] if why else [])
            + [
                f"function: {job.function}",
                f"timeline: {job.timeline_label}",
                f"freshness: {fresh}",
            ]
        )
    return sorted(
        jobs,
        key=lambda j: (-j.score, j.posted_at or "", j.uid),
    )


def score_llm(jobs: list[Job], profile: dict, top_n: int = 25) -> list[Job]:
    """Compatibility no-op: the strictly-free baseline never calls paid models."""
    return jobs
