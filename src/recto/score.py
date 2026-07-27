"""No-LLM prefilter: TF-IDF similarity against the profile corpus, plus
boosts for citation-graph hits, co-author matches, and configured keywords.
Scores every 'new' candidate (no recency pre-filter — an old paper can be
more relevant than a new one) then cuts to ~15 via top_shortlist.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from recto.config import ScoringConfig
from recto.models import Paper
from recto.profile.build import ProfileIndex

_EPOCH = datetime.min.replace(tzinfo=UTC)

# Only "cites one of your papers" is direct evidence (someone read and cited
# you). "Cites a reference you also cite" (co-citation) is unscored — most ML
# papers cite the same handful of foundational works, so sharing a reference
# isn't a meaningful relevance signal. It still surfaces the paper as a
# candidate; it just doesn't move the score.
DIRECT_CITATION_MARKER = "cites one of your papers"
SIMILARITY_REASON_THRESHOLD = 0.05


def _topical_similarity(papers: list[Paper], index: ProfileIndex, max_features: int) -> np.ndarray:
    """Fits TF-IDF on profile_docs + this run's candidates together, so a
    candidate's vocabulary is never truncated down to the (tiny) profile
    corpus's vocabulary. Reconstructs the weighted profile vector in that
    shared space using the same doc weights build_index computed."""
    candidate_texts = [" ".join(filter(None, [p.title, p.abstract])) for p in papers]
    vectorizer = TfidfVectorizer(
        max_features=max_features, stop_words="english", ngram_range=(1, 2)
    )
    combined = vectorizer.fit_transform([*index.profile_docs, *candidate_texts])
    n_profile_docs = len(index.profile_docs)
    profile_matrix = combined[:n_profile_docs]
    candidate_matrix = combined[n_profile_docs:]

    doc_weights = np.array(index.profile_doc_weights).reshape(-1, 1)
    weighted_sum = np.asarray(profile_matrix.multiply(doc_weights).sum(axis=0)).ravel()
    total_weight = float(doc_weights.sum()) or 1.0
    profile_vector = (weighted_sum / total_weight).reshape(1, -1)

    return cosine_similarity(candidate_matrix, profile_vector).ravel()


def cap_candidates(scored_papers: list[Paper], candidate_cap: int) -> list[Paper]:
    """Safety bound only — run AFTER scoring. Keeps the highest-scoring
    candidates if the backlog exceeds the cap, so it never discards an
    old-but-relevant paper in favor of a recent-but-irrelevant one."""
    if len(scored_papers) <= candidate_cap:
        return scored_papers
    return sorted(scored_papers, key=lambda p: p.score, reverse=True)[:candidate_cap]


def score_candidates(
    papers: list[Paper],
    index: ProfileIndex,
    keywords: list[str],
    weights: ScoringConfig,
) -> list[Paper]:
    if not papers:
        return []

    similarities = _topical_similarity(papers, index, weights.tfidf_max_features)

    coauthors_lower = {c.lower() for c in index.coauthor_names}
    keywords_lower = [k.lower() for k in keywords]

    for paper, sim in zip(papers, similarities, strict=True):
        reasons = list(paper.score_reasons)
        score = weights.weight_tfidf * float(sim)
        if sim > SIMILARITY_REASON_THRESHOLD:
            reasons.append(f"topical similarity to your profile ({sim:.2f})")

        if any(DIRECT_CITATION_MARKER in r for r in reasons):
            score += weights.weight_citation_graph

        matched_coauthors = [a for a in paper.authors if a.lower() in coauthors_lower]
        if matched_coauthors:
            score += weights.weight_coauthor
            reasons.append(f"co-author: {matched_coauthors[0]}")

        text_lower = f"{paper.title} {paper.abstract or ''}".lower()
        matched_keywords = [k for k in keywords_lower if k in text_lower]
        if matched_keywords:
            score += weights.weight_keyword * len(matched_keywords)
            reasons.append(f"matches keyword(s): {', '.join(matched_keywords)}")

        paper.score = float(score)
        paper.score_reasons = reasons

    return papers


def top_shortlist(
    scored_papers: list[Paper], shortlist_size: int, recent_slots: int = 2
) -> list[Paper]:
    """Guarantees the `recent_slots` most-recently-published papers a seat
    (so brand-new papers aren't buried before scoring signal has caught up),
    then fills the rest of the shortlist purely by relevance score across
    ALL scored candidates — not just the recent ones."""
    if not scored_papers:
        return []

    recent_slots = max(min(recent_slots, shortlist_size, len(scored_papers)), 0)
    by_recency = sorted(scored_papers, key=lambda p: p.published_at or _EPOCH, reverse=True)
    recent_picks = by_recency[:recent_slots]
    recent_ids = {p.id for p in recent_picks}
    for p in recent_picks:
        p.score_reasons = [*p.score_reasons, "recent publication (guaranteed slot)"]

    remaining = [p for p in scored_papers if p.id not in recent_ids]
    by_relevance = sorted(remaining, key=lambda p: p.score, reverse=True)
    fill = by_relevance[: max(shortlist_size - recent_slots, 0)]

    return recent_picks + fill
