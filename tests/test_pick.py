from datetime import UTC, datetime

from recto.models import Paper
from recto.pick import resolve_pick
from recto.summarize import SummarizeResult


def _paper(id_, score) -> Paper:
    return Paper(
        id=id_,
        source="arxiv",
        title=id_,
        authors=[],
        abstract="",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        url="https://x",
        score=score,
    )


def test_resolve_pick_uses_llm_choice_when_valid():
    papers = [_paper("a", 0.5), _paper("b", 0.9)]
    result = SummarizeResult(paper_of_day_id="a", rationale="Extends your work.", degraded=False)
    pod, rationale = resolve_pick(papers, result)
    assert pod.id == "a"
    assert rationale == "Extends your work."


def test_resolve_pick_falls_back_to_top_score_when_degraded():
    papers = [_paper("a", 0.5), _paper("b", 0.9)]
    result = SummarizeResult(degraded=True, degraded_reason="llm_unreachable")
    pod, rationale = resolve_pick(papers, result)
    assert pod.id == "b"
    assert rationale is None


def test_resolve_pick_falls_back_when_llm_id_not_in_shortlist():
    papers = [_paper("a", 0.5), _paper("b", 0.9)]
    result = SummarizeResult(paper_of_day_id="zzz", rationale="hallucinated", degraded=False)
    pod, rationale = resolve_pick(papers, result)
    assert pod.id == "b"
    assert rationale is None


def test_resolve_pick_empty_shortlist_returns_none():
    result = SummarizeResult()
    pod, rationale = resolve_pick([], result)
    assert pod is None
    assert rationale is None
