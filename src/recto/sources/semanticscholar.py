"""Semantic Scholar: secondary/enrichment source, not a primary crawl.

No API key available yet, so every call here is keyless and must degrade
silently (log + return input unmodified) on 429/403 rather than raising —
the free tier is heavily rate-limited without a key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from recto.models import Paper
from recto.net import RateLimiter, build_user_agent, request

logger = logging.getLogger(__name__)

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
BATCH_FIELDS = "title,abstract,externalIds,tldr"
# 429s are expected on the free keyless tier; don't hammer it with retries.
_RETRYABLE_ONLY = frozenset({500, 502, 503, 504})

FetchBatch = Callable[..., list[dict] | None]
FetchSearch = Callable[..., dict | None]


def _s2_id_for(paper: Paper) -> str | None:
    if paper.doi:
        return f"DOI:{paper.doi}"
    arxiv_id = paper.external_ids.get("arxiv")
    if arxiv_id:
        return f"ARXIV:{arxiv_id}"
    return None


def _default_fetch_batch(
    ids: list[str], contact_email: str, rate_limiter: RateLimiter
) -> list[dict] | None:
    headers = {
        "User-Agent": build_user_agent("semanticscholar", contact_email),
        "Content-Type": "application/json",
    }
    resp = request(
        "POST",
        f"{BATCH_URL}?fields={BATCH_FIELDS}",
        rate_limiter=rate_limiter,
        headers=headers,
        json={"ids": ids},
        timeout=20.0,
        retry_statuses=_RETRYABLE_ONLY,
    )
    if resp is None:
        return None
    if resp.status_code in (429, 403):
        logger.warning(
            "semanticscholar rate-limited/forbidden (status %d), skipping enrichment",
            resp.status_code,
        )
        return None
    if resp.status_code != 200:
        logger.warning("semanticscholar batch fetch failed: status %d", resp.status_code)
        return None
    return resp.json()


def enrich(
    papers: list[Paper],
    contact_email: str,
    *,
    base_delay_seconds: float = 1.0,
    _fetch_batch: FetchBatch | None = None,
) -> list[Paper]:
    """Fill in missing abstracts (mainly for scholar_alerts.py output, whose
    abstracts are truncated) via the batch endpoint. Returns `papers`
    unmodified on any failure — enrichment is best-effort."""
    fetch_batch = _fetch_batch or (
        lambda ids, email: _default_fetch_batch(
            ids, email, RateLimiter(min_interval_seconds=base_delay_seconds)
        )
    )

    id_map = {}
    for p in papers:
        s2_id = _s2_id_for(p)
        if s2_id and not p.abstract:
            id_map[s2_id] = p

    if not id_map:
        return papers

    results = fetch_batch(list(id_map.keys()), contact_email)
    if not results:
        return papers

    for entry, s2_id in zip(results, id_map.keys(), strict=False):
        if not entry:
            continue
        paper = id_map[s2_id]
        if not paper.abstract and entry.get("abstract"):
            paper.abstract = entry["abstract"]
        tldr = entry.get("tldr")
        if tldr and tldr.get("text") and not paper.summary:
            paper.summary = tldr["text"]

    return papers


def _default_fetch_search(query: str, contact_email: str) -> dict | None:
    headers = {"User-Agent": build_user_agent("semanticscholar", contact_email)}
    resp = request(
        "GET",
        SEARCH_URL,
        headers=headers,
        params={"query": query, "fields": BATCH_FIELDS, "limit": 1},
        timeout=20.0,
        retry_statuses=_RETRYABLE_ONLY,
    )
    if resp is None or resp.status_code != 200:
        logger.warning("semanticscholar title search failed for %r", query)
        return None
    return resp.json()


def search_by_title(
    title: str, contact_email: str, *, _fetch_search: FetchSearch | None = None
) -> dict | None:
    """Used by scholar_alerts.py to re-resolve alert-email entries (which
    have reliable titles but truncated abstracts) against S2 metadata."""
    fetch_search = _fetch_search or _default_fetch_search
    data = fetch_search(title, contact_email)
    if not data:
        return None
    results = data.get("data") or []
    return results[0] if results else None
