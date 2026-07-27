from datetime import UTC, datetime

from recto.models import Paper
from recto.render import render_html_email, render_markdown, write_digest


def _paper(title="A Paper", abstract="An abstract.", score_reasons=None) -> Paper:
    return Paper(
        id="x",
        source="arxiv",
        title=title,
        authors=["A. Author"],
        abstract=abstract,
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        url="https://example.com/x",
        score_reasons=score_reasons or [],
    )


def test_render_markdown_includes_paper_of_day_and_shortlist():
    pod = _paper(title="Top Pick")
    other = _paper(title="Also Here")
    md = render_markdown(
        run_date="2026-07-23",
        shortlisted=[pod, other],
        paper_of_day=pod,
        rationale="This extends the method in your 2024 paper on X.",
        degraded=False,
        degraded_reasons=[],
    )
    assert "# Research Digest — 2026-07-23" in md
    assert "Top Pick" in md
    assert "Also Here" in md
    assert "extends the method in your 2024 paper" in md


def test_render_markdown_degraded_flag_shown():
    md = render_markdown(
        run_date="2026-07-23",
        shortlisted=[],
        paper_of_day=None,
        rationale=None,
        degraded=True,
        degraded_reasons=["llm_unreachable"],
    )
    assert "Degraded run: llm_unreachable" in md
    assert "No candidates today." in md


def test_render_markdown_no_rationale_labels_fallback_pick():
    pod = _paper(title="Fallback Pick")
    md = render_markdown(
        run_date="2026-07-23",
        shortlisted=[pod],
        paper_of_day=pod,
        rationale=None,
        degraded=True,
        degraded_reasons=["llm_unreachable"],
    )
    assert "fallback pick" in md.lower()


def test_render_html_email_escapes_untrusted_title():
    malicious = _paper(title="<script>alert(1)</script>", abstract="<b>bold</b> text")
    htm = render_html_email(
        run_date="2026-07-23",
        shortlisted=[malicious],
        paper_of_day=None,
        rationale=None,
        degraded=False,
        degraded_reasons=[],
        subject_prefix="[recto]",
    )
    assert "<script>alert(1)</script>" not in htm
    assert "&lt;script&gt;" in htm
    assert "&lt;b&gt;bold&lt;/b&gt;" in htm


def test_write_digest_overwrites_same_day(tmp_path):
    write_digest("2026-07-23", "first version", tmp_path)
    path = write_digest("2026-07-23", "second version", tmp_path)
    assert path.read_text() == "second version"
    assert len(list(tmp_path.glob("*.md"))) == 1
