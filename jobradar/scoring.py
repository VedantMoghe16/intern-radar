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
    drop_senior = cfg.get("drop_senior_titles", True)

    intern_signal = re.compile(
        r"\b(intern|internship|co-?op|apprentice|trainee|new ?grad|"
        r"graduate program|university|campus|student|summer 20\d\d|"
        r"early career|apm|associate product manager)\b",
        re.I,
    )
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
        if require_intern and not intern_signal.search(f"{job.title} {job.department}"):
            continue
        if include and not any(k in title_l for k in include):
            continue
        if locations:
            loc_l = (job.location or "").lower()
            # empty location is kept — many boards omit it, better a false
            # positive than a missed role
            if loc_l and not any(l in loc_l for l in locations):
                if "remote" not in loc_l and "remote" not in blob[:400]:
                    continue
        if restricted_remote.search(f"{job.location} {job.description[:800]}"):
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

        intake_score = 10.0 if "summer 2027" in blob else (6.0 if "2027" in blob else 2.0)
        fresh = freshness_bucket(job)
        freshness_score = {"new": 5.0, "recent": 2.5}.get(fresh, 0.0)

        classification = classify_function_with_confidence(job)
        job.function = classification.function
        job.function_confidence = classification.confidence
        job.company_tier = company_tier(
            job.company, known_tiers=profile.get("company_tiers") or {}
        )
        job.score_components = {
            "skills": round(skill_score, 1),
            "boosts": round(bonus_score, 1),
            "location": location_score,
            "intake": intake_score,
            "freshness": freshness_score,
            "function": round(job.function_confidence * 5, 1),
        }
        job.score = round(min(sum(job.score_components.values()), 100.0), 1)
        job.reasons = (
            ([f"skills: {', '.join(hits[:6])}"] if hits else [])
            + ([f"boost: {', '.join(why[:4])}"] if why else [])
            + [f"function: {job.function}", f"freshness: {fresh}"]
        )
    return sorted(
        jobs,
        key=lambda j: (-j.score, j.posted_at or "", j.uid),
    )


def score_llm(jobs: list[Job], profile: dict, top_n: int = 25) -> list[Job]:
    """Compatibility no-op: the strictly-free baseline never calls paid models."""
    return jobs
