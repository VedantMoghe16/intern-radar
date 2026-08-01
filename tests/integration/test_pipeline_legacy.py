"""Offline test of the filter -> score -> store -> digest path (no network)."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.models import Job
from jobradar.scoring import filter_jobs, score_local
from jobradar.store import Store
from jobradar.digest import render_html, render_text

SAMPLE = [
    Job(company="Databricks", title="Product Management Intern (Summer 2027)",
        url="https://ex.com/1", ats="greenhouse", location="Bengaluru, India",
        department="Product", description="Own a product area. Work with AI Platform, RAG, agents. Graduating 2028."),
    Job(company="Sarvam AI", title="AI Engineer - Intern",
        url="https://ex.com/2", ats="lever", location="Bengaluru, India",
        department="Engineering", description="FastAPI, RAG, agents, LLM, python, prompt design"),
    Job(company="Stripe", title="Senior Staff Engineer, Payments",
        url="https://ex.com/3", ats="greenhouse", location="Dublin, Ireland",
        description="15 years experience required"),
    Job(company="BigCo", title="Account Executive, Enterprise Sales",
        url="https://ex.com/4", ats="lever", location="Mumbai, India",
        description="quota carrying sales role"),
    Job(company="Ramp", title="Forward Deployed Engineer Intern",
        url="https://ex.com/5", ats="ashby", location="Remote",
        department="Engineering", description="python typescript llm agents customer facing"),
]

PROFILE = {
    "skills": ["python", "fastapi", "llm", "rag", "agents", "product management",
               "typescript", "sql", "docker", "spark"],
    "boosts": {"product management": 18, "applied ai": 20, "forward deployed": 22,
               "india": 12, "bengaluru": 10, "remote": 8, "2028": 14},
    "title_include": [], "title_exclude": ["account executive"],
    "locations": ["india", "bengaluru", "mumbai", "remote"],
    "require_intern_signal": True, "drop_senior_titles": True,
}

def main():
    kept = filter_jobs(SAMPLE, PROFILE)
    titles = {j.title for j in kept}
    assert "Senior Staff Engineer, Payments" not in titles, "senior title leaked"
    assert "Account Executive, Enterprise Sales" not in titles, "excluded title leaked"
    assert len(kept) == 3, f"expected 3 kept, got {len(kept)}: {titles}"
    print(f"filter  ok  -> kept {len(kept)}/{len(SAMPLE)}")

    ranked = score_local(kept, PROFILE)
    assert ranked[0].score >= ranked[-1].score
    for j in ranked:
        print(f"   {j.score:>5.1f}  {j.title[:44]:<46} {j.reasons[:1]}")

    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "t.db")
        fresh = store.record(ranked)
        assert len(fresh) == 3, "first insert should be all new"
        again = store.record(ranked)
        assert len(again) == 0, "re-running must not re-report the same jobs"
        print("store   ok  -> dedupe holds across runs")

        rows = store.recent(hours=24)
        assert len(rows) == 3
        out = render_html(rows, [{"company": "Foo", "error": "HTTP 404"}],
                          Path(td) / "d.html")
        html = out.read_text()
        assert "Databricks" in html and "Job radar" in html
        assert "<1h" in html, "age gutter should mark brand-new rows"
        print(f"digest  ok  -> {len(html)} bytes")
        print(render_text(rows)[:220])
        store.close()
    print("\nall green")

main()
