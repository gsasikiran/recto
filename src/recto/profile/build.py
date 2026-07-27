"""profile.md + ORCID corpus -> scoring index.

ORCID is the bootstrap (no manual topic list required); profile.md is
hand-written prose that overrides ORCID where they conflict, since it
reflects where the user is going rather than where they've been — modeled
here as profile.md getting a dominant weight when averaging TF-IDF vectors.

The index is a locally-generated, locally-consumed pickle (sklearn objects
aren't JSON-serializable) at
~/Library/Application Support/recto/profile_index.pkl.
"""

from __future__ import annotations

import logging
import pickle
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from recto.net import RateLimiter, build_user_agent, request
from recto.profile import orcid
from recto.profile.orcid import OrcidWork
from recto.sources.openalex import normalize_work

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
_rate_limiter = RateLimiter(min_interval_seconds=1.0)

TITLE_ONLY_WEIGHT = 0.3
TOP_TERMS_COUNT = 30

ResolveOpenAlex = Callable[..., dict | None]


@dataclass(slots=True)
class ProfileIndex:
    # vectorizer/profile_vector are fit on the profile corpus alone — good
    # enough for the top_terms display below, but NOT for scoring: transforming
    # candidate papers into this vocabulary drops any word the profile hasn't
    # seen. score.py refits a vectorizer per run on profile_docs + the day's
    # candidates instead, using profile_doc_weights to reconstruct the same
    # weighted profile vector in that shared vocabulary.
    vectorizer: TfidfVectorizer
    profile_vector: np.ndarray
    profile_docs: list[str]
    profile_doc_weights: list[float]
    coauthor_names: set[str]
    seed_work_ids: list[str]
    reference_work_ids: list[str]
    top_terms: list[tuple[str, float]] = field(default_factory=list)
    built_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _default_resolve_openalex(doi: str, contact_email: str) -> dict | None:
    headers = {"User-Agent": build_user_agent("openalex", contact_email)}
    resp = request(
        "GET",
        f"{OPENALEX_WORKS_URL}/doi:{doi}",
        rate_limiter=_rate_limiter,
        headers=headers,
        params={"mailto": contact_email},
        timeout=20.0,
    )
    if resp is None or resp.status_code != 200:
        logger.warning("openalex doi resolution failed for %s: %s", doi, resp and resp.status_code)
        return None
    return resp.json()


def _recency_weight(year: int | None, half_life_days: float, now: datetime) -> float:
    if not year:
        return 0.5
    age_days = max((now - datetime(year, 1, 1, tzinfo=UTC)).days, 0)
    return 0.5 ** (age_days / half_life_days)


def build_index(
    *,
    orcid_id: str,
    profile_md_text: str,
    contact_email: str,
    recency_half_life_days: int = 730,
    profile_md_dominance: float = 2.0,
    max_reference_works: int = 50,
    _fetch_works: Callable[..., list[OrcidWork]] | None = None,
    _resolve_openalex: ResolveOpenAlex | None = None,
) -> ProfileIndex:
    fetch_works = _fetch_works or orcid.fetch_works
    resolve = _resolve_openalex or _default_resolve_openalex

    works = fetch_works(orcid_id, contact_email) if orcid_id else []
    now = datetime.now(UTC)

    docs: list[str] = []
    doc_weights: list[float] = []
    coauthors: set[str] = set()
    seed_work_ids: list[str] = []
    reference_counter: dict[str, int] = {}

    for w in works:
        work_data = resolve(w.doi, contact_email) if w.doi else None
        if not work_data:
            docs.append(w.title)
            recency = _recency_weight(w.year, recency_half_life_days, now)
            doc_weights.append(recency * TITLE_ONLY_WEIGHT)
            continue

        paper = normalize_work(work_data)
        docs.append(" ".join(filter(None, [paper.title, paper.abstract])))
        doc_weights.append(_recency_weight(w.year, recency_half_life_days, now))
        coauthors.update(paper.authors)

        short_id = (work_data.get("id") or "").rsplit("/", 1)[-1]
        if short_id:
            seed_work_ids.append(short_id)
        for ref in (work_data.get("referenced_works") or [])[:200]:
            reference_counter[ref] = reference_counter.get(ref, 0) + 1

    # profile.md ("Current focus") overrides ORCID history where they conflict — it
    # reflects where you're going, not where you've been — so its weight is pegged
    # to a multiple of the *entire* ORCID corpus's combined weight, not a flat
    # constant. That way it stays dominant regardless of how many ORCID works you have.
    docs.append(profile_md_text)
    orcid_weight_total = sum(doc_weights) or 1.0
    doc_weights.append(orcid_weight_total * profile_md_dominance)

    if not any(d.strip() for d in docs):
        docs, doc_weights = [""], [1.0]

    vectorizer = TfidfVectorizer(max_features=20000, stop_words="english")
    tfidf_matrix = vectorizer.fit_transform(docs)
    weights_arr = np.array(doc_weights).reshape(-1, 1)
    weighted_sum = np.asarray(tfidf_matrix.multiply(weights_arr).sum(axis=0)).ravel()
    total_weight = float(weights_arr.sum()) or 1.0
    profile_vector = (weighted_sum / total_weight).reshape(1, -1)

    feature_names = vectorizer.get_feature_names_out()
    flat_scores = profile_vector.ravel()
    top_idx = flat_scores.argsort()[::-1][:TOP_TERMS_COUNT]
    top_terms = [(feature_names[i], float(flat_scores[i])) for i in top_idx if flat_scores[i] > 0]

    ranked_refs = sorted(reference_counter, key=lambda k: reference_counter[k], reverse=True)
    reference_work_ids = ranked_refs[:max_reference_works]

    return ProfileIndex(
        vectorizer=vectorizer,
        profile_vector=profile_vector,
        profile_docs=docs,
        profile_doc_weights=doc_weights,
        coauthor_names=coauthors,
        seed_work_ids=seed_work_ids,
        reference_work_ids=reference_work_ids,
        top_terms=top_terms,
        built_at=now,
    )


def save_index(index: ProfileIndex, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(index, fh)


def load_index(path: Path) -> ProfileIndex | None:
    if not path.exists():
        return None
    with path.open("rb") as fh:
        return pickle.load(fh)


def needs_rebuild(pkl_path: Path, profile_md_path: Path, max_age_days: int = 30) -> bool:
    if not pkl_path.exists():
        return True
    pkl_mtime = pkl_path.stat().st_mtime
    if profile_md_path.exists() and profile_md_path.stat().st_mtime > pkl_mtime:
        return True
    age_days = (datetime.now().timestamp() - pkl_mtime) / 86400
    return age_days > max_age_days
