from datetime import UTC, datetime

from recto.config import ScoringConfig
from recto.models import Paper
from recto.profile import build
from recto.score import (
    SIMILARITY_REASON_THRESHOLD,
    cap_candidates,
    score_candidates,
    top_shortlist,
)


def _paper(title, abstract="", authors=None, published_at=None, score_reasons=None) -> Paper:
    return Paper(
        id=title,
        source="arxiv",
        title=title,
        authors=authors or [],
        abstract=abstract,
        published_at=published_at or datetime(2026, 7, 1, tzinfo=UTC),
        url="https://example.com",
        score_reasons=score_reasons or [],
    )


def _index():
    return build.build_index(
        orcid_id="",
        profile_md_text="multi-agent reinforcement learning and autonomous agents",
        contact_email="test@example.com",
        _fetch_works=lambda oid, email: [],
    )


def test_cap_candidates_keeps_highest_scoring_when_over_cap():
    low = _paper("Low", published_at=datetime(2026, 1, 1, tzinfo=UTC))
    low.score = 0.1
    high = _paper("High", published_at=datetime(2020, 1, 1, tzinfo=UTC))
    high.score = 0.9
    capped = cap_candidates([low, high], candidate_cap=1)
    assert capped == [high]


def test_cap_candidates_noop_when_under_cap():
    a = _paper("A")
    b = _paper("B")
    assert cap_candidates([a, b], candidate_cap=10) == [a, b]


def test_score_candidates_ranks_topically_similar_paper_higher():
    index = _index()
    on_topic = _paper(
        "Multi-Agent Reinforcement Learning for Autonomous Agents",
        abstract="We study multi-agent reinforcement learning and autonomous agents.",
    )
    off_topic = _paper("A Study of Medieval Basket Weaving", abstract="Basket weaving history.")
    score_candidates([on_topic, off_topic], index, keywords=[], weights=ScoringConfig())
    assert on_topic.score > off_topic.score


def test_score_candidates_applies_coauthor_boost():
    index = _index()
    index.coauthor_names = {"Jane Doe"}
    paper = _paper("Some Paper", authors=["Jane Doe", "Someone Else"])
    score_candidates([paper], index, keywords=[], weights=ScoringConfig())
    assert any("co-author: Jane Doe" in r for r in paper.score_reasons)


def test_score_candidates_applies_keyword_boost():
    index = _index()
    paper = _paper("Some Paper", abstract="This discusses transformer architectures in depth.")
    score_candidates([paper], index, keywords=["transformer"], weights=ScoringConfig())
    assert any("transformer" in r for r in paper.score_reasons)


def test_score_candidates_applies_direct_citation_boost():
    index = _index()
    with_citation = _paper("Cites You", score_reasons=["cites one of your papers"])
    without_citation = _paper("Does Not Cite You")
    weights = ScoringConfig(weight_tfidf=0.0)  # isolate the citation-graph boost
    score_candidates([with_citation, without_citation], index, keywords=[], weights=weights)
    assert with_citation.score > without_citation.score


def test_score_candidates_does_not_boost_cocitation():
    index = _index()
    direct = _paper("Cites You", score_reasons=["cites one of your papers"])
    cocite = _paper("Shared Reference", score_reasons=["cites a reference you also cite"])
    weights = ScoringConfig(weight_tfidf=0.0)
    score_candidates([direct, cocite], index, keywords=[], weights=weights)
    assert direct.score > cocite.score
    assert cocite.score == 0.0


def test_score_candidates_does_not_drop_candidate_only_vocabulary():
    """Regression test: the vectorizer used to be fit on the profile corpus
    alone, so any word a candidate used that the profile hadn't already seen
    was silently dropped, collapsing similarity toward zero for almost every
    candidate. It must now be fit on profile + candidates together."""
    index = _index()
    on_topic = _paper(
        "Autonomous Agent Coordination",
        abstract=(
            "We study multi-agent reinforcement learning and autonomous agents "
            "using a novel decentralized coordination protocol."
        ),
    )
    score_candidates([on_topic], index, keywords=[], weights=ScoringConfig())
    assert on_topic.score > SIMILARITY_REASON_THRESHOLD


def test_top_shortlist_sorts_and_truncates_by_relevance():
    a = _paper("A")
    a.score = 0.9
    b = _paper("B")
    b.score = 0.5
    c = _paper("C")
    c.score = 0.7
    shortlisted = top_shortlist([a, b, c], shortlist_size=2, recent_slots=0)
    assert [p.title for p in shortlisted] == ["A", "C"]


def test_top_shortlist_guarantees_recent_slots_regardless_of_score():
    stale_high_score = _paper("Old But High Score", published_at=datetime(2020, 1, 1, tzinfo=UTC))
    stale_high_score.score = 0.9
    fresh_low_score = _paper("New But Low Score", published_at=datetime(2026, 1, 1, tzinfo=UTC))
    fresh_low_score.score = 0.1
    shortlisted = top_shortlist(
        [stale_high_score, fresh_low_score], shortlist_size=1, recent_slots=1
    )
    assert [p.title for p in shortlisted] == ["New But Low Score"]


def test_top_shortlist_fills_remaining_slots_by_relevance_across_all_candidates():
    recent = _paper("Recent", published_at=datetime(2026, 7, 1, tzinfo=UTC))
    recent.score = 0.1
    old_relevant = _paper("Old Relevant", published_at=datetime(2018, 1, 1, tzinfo=UTC))
    old_relevant.score = 0.9
    old_irrelevant = _paper("Old Irrelevant", published_at=datetime(2017, 1, 1, tzinfo=UTC))
    old_irrelevant.score = 0.2
    shortlisted = top_shortlist(
        [recent, old_relevant, old_irrelevant], shortlist_size=2, recent_slots=1
    )
    assert [p.title for p in shortlisted] == ["Recent", "Old Relevant"]
