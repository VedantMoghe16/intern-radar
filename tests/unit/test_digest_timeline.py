from pathlib import Path

from jobradar.digest import render_html, render_text


def _row(**overrides: object) -> dict:
    row = {
        "uid": "job-1",
        "company": "Databricks",
        "title": "Software Engineer Intern",
        "location": "Bengaluru, India",
        "url": "https://example.test/job-1",
        "score": 80,
        "function": "Software",
        "company_tier": "Scaled",
        "timeline_label": "May-August 2027",
        "timeline_start_month": 5,
        "timeline_year": 2027,
    }
    row.update(overrides)
    return row


def test_digest_groups_target_timeline_before_unspecified(tmp_path: Path) -> None:
    rows = [
        _row(
            uid="unknown",
            company="Acme",
            title="Product Intern",
            function="Product",
            timeline_label="Dates unspecified",
            timeline_start_month=None,
            timeline_year=None,
        ),
        _row(),
    ]

    rendered = render_html(rows, [], tmp_path / "digest.html").read_text()
    text = render_text(rows)

    assert rendered.index("May-August 2027 · 1") < rendered.index(
        "Dates unspecified · 1"
    )
    assert rendered.index("Software · 1") < rendered.index("Product · 1")
    assert text.index("MAY-AUGUST 2027") < text.index("DATES UNSPECIFIED")
