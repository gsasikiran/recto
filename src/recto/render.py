"""Markdown + HTML digest rendering from the same Paper data.
Every run writes plain Markdown the user can read without the app."""

from __future__ import annotations

import html
from pathlib import Path

from recto.models import Paper

ABSTRACT_PREVIEW_CHARS = 400


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def render_markdown(
    *,
    run_date: str,
    shortlisted: list[Paper],
    paper_of_day: Paper | None,
    rationale: str | None,
    degraded: bool,
    degraded_reasons: list[str],
) -> str:
    lines = [f"# Research Digest — {run_date}", ""]

    if degraded:
        reasons = ", ".join(degraded_reasons) if degraded_reasons else "unknown reason"
        lines += [f"_Degraded run: {reasons}_", ""]

    if paper_of_day is not None:
        lines.append("## Read this today")
        lines.append(f"**[{paper_of_day.title}]({paper_of_day.url})**")
        if paper_of_day.authors:
            lines.append(f"*{', '.join(paper_of_day.authors[:5])}*")
        lines.append("")
        if rationale:
            lines.append(rationale)
        else:
            lines.append("_No LLM rationale available this run — top lexical-score fallback pick._")
        lines.append("")

    lines.append("## Shortlist")
    lines.append("")
    if not shortlisted:
        lines.append("_No candidates today._")
    for paper in shortlisted:
        lines.append(f"### [{paper.title}]({paper.url})")
        if paper.authors:
            lines.append(f"*{', '.join(paper.authors[:5])}*")
        if paper.summary:
            lines.append(paper.summary)
        elif paper.abstract:
            lines.append(_truncate(paper.abstract, ABSTRACT_PREVIEW_CHARS))
        if paper.score_reasons:
            lines.append(f"_Why: {'; '.join(paper.score_reasons)}_")
        lines.append("")

    return "\n".join(lines)


def render_html_email(
    *,
    run_date: str,
    shortlisted: list[Paper],
    paper_of_day: Paper | None,
    rationale: str | None,
    degraded: bool,
    degraded_reasons: list[str],
    subject_prefix: str,
) -> str:
    def esc(s: str) -> str:
        return html.escape(s)

    parts = [
        '<html><body style="font-family: -apple-system, Helvetica, Arial, sans-serif; '
        'max-width: 640px; margin: 0 auto;">',
        f"<h1>{esc(subject_prefix)} Research Digest — {esc(run_date)}</h1>",
    ]

    if degraded:
        reasons = esc(", ".join(degraded_reasons) if degraded_reasons else "unknown reason")
        parts.append(f'<p style="color:#a00;"><em>Degraded run: {reasons}</em></p>')

    if paper_of_day is not None:
        parts.append("<h2>Read this today</h2>")
        pod_link = f'<a href="{esc(paper_of_day.url)}">{esc(paper_of_day.title)}</a>'
        parts.append(f"<p><strong>{pod_link}</strong></p>")
        if paper_of_day.authors:
            parts.append(f"<p><em>{esc(', '.join(paper_of_day.authors[:5]))}</em></p>")
        if rationale:
            parts.append(f"<p>{esc(rationale)}</p>")
        else:
            parts.append(
                "<p><em>No LLM rationale available this run — "
                "top lexical-score fallback pick.</em></p>"
            )

    parts.append("<h2>Shortlist</h2>")
    if not shortlisted:
        parts.append("<p><em>No candidates today.</em></p>")
    for paper in shortlisted:
        parts.append(f'<h3><a href="{esc(paper.url)}">{esc(paper.title)}</a></h3>')
        if paper.authors:
            parts.append(f"<p><em>{esc(', '.join(paper.authors[:5]))}</em></p>")
        body = paper.summary or (
            _truncate(paper.abstract, ABSTRACT_PREVIEW_CHARS) if paper.abstract else None
        )
        if body:
            parts.append(f"<p>{esc(body)}</p>")
        if paper.score_reasons:
            why = esc("; ".join(paper.score_reasons))
            parts.append(f'<p style="color:#666;font-size:0.9em;">Why: {why}</p>')

    parts.append("</body></html>")
    return "\n".join(parts)


def write_digest(run_date: str, content: str, digests_dir: Path) -> Path:
    path = digests_dir / f"{run_date}.md"
    path.write_text(content)
    return path
