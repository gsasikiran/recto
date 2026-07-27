"""Thin resolution layer: use the LLM's pick if valid, else fall back to the
top lexical-score paper. render.py labels the fallback case explicitly (no
rationale is passed through) so the user is never misled into thinking a
non-LLM pick has a real rationale.
"""

from __future__ import annotations

from recto.models import Paper
from recto.summarize import SummarizeResult


def resolve_pick(
    shortlisted: list[Paper], result: SummarizeResult
) -> tuple[Paper | None, str | None]:
    if not shortlisted:
        return None, None

    if not result.degraded and result.paper_of_day_id:
        match = next((p for p in shortlisted if p.id == result.paper_of_day_id), None)
        if match:
            return match, result.rationale

    top = max(shortlisted, key=lambda p: p.score)
    return top, None
