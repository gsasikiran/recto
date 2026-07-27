"""OpenAlex source: keyword search plus citation-graph channels.

Citation-graph channels (works citing the user's own papers, and new works
citing the same references the user cites) are usually higher-precision
than keyword matching, per the profile design. Free API, no key — polite
pool via a contact email in the User-Agent and `mailto` param.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from recto.models import Paper
from recto.net import RateLimiter, build_user_agent, request

logger = logging.getLogger(__name__)

API_URL = "https://api.openalex.org/works"
_rate_limiter = RateLimiter(min_interval_seconds=1.0)

Fetch = Callable[..., dict]


def _default_fetch(params: dict, contact_email: str) -> dict:
    headers = {"User-Agent": build_user_agent("openalex", contact_email)}
    full_params = {**params, "mailto": contact_email}
    resp = request(
        "GET",
        API_URL,
        rate_limiter=_rate_limiter,
        headers=headers,
        params=full_params,
        timeout=30.0,
    )
    if resp is None or resp.status_code != 200:
        logger.warning("openalex fetch failed: %s", resp and resp.status_code)
        return {}
    return resp.json()


def _reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """OpenAlex never returns a plain abstract string (licensing) — only a
    position -> word inverted index that must be reassembled in order."""
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
    if not positions:
        return None
    return " ".join(positions[i] for i in sorted(positions))


def _short_id(openalex_url: str) -> str:
    return openalex_url.rsplit("/", 1)[-1] if openalex_url else ""


def _from_date(lookback_days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=lookback_days)).date().isoformat()


def normalize_work(work: dict, source: str = "openalex") -> Paper:
    openalex_id = work.get("id", "")
    short_id = _short_id(openalex_id)
    doi_url = work.get("doi")
    doi = doi_url.replace("https://doi.org/", "") if doi_url else None

    authors = [
        a.get("author", {}).get("display_name", "")
        for a in work.get("authorships", [])
        if a.get("author", {}).get("display_name")
    ]

    published_raw = work.get("publication_date")
    published_at = (
        datetime.fromisoformat(published_raw).replace(tzinfo=UTC) if published_raw else None
    )

    primary_location = work.get("primary_location") or {}
    src = primary_location.get("source") or {}
    venue = src.get("display_name") or primary_location.get("raw_source_name")
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

    external_ids = {"openalex": short_id}
    if doi:
        external_ids["doi"] = doi

    return Paper(
        id=f"doi:{doi}" if doi else f"openalex:{short_id}",
        source=source,
        title=work.get("title") or work.get("display_name") or "",
        authors=authors,
        abstract=abstract,
        published_at=published_at,
        url=primary_location.get("landing_page_url") or f"https://openalex.org/{short_id}",
        doi=doi,
        venue=venue,
        categories=[],
        external_ids=external_ids,
    )


def fetch_keyword(
    query: str,
    max_results: int,
    contact_email: str,
    *,
    lookback_days: int = 14,
    _fetch: Fetch | None = None,
) -> list[Paper]:
    fetch = _fetch or _default_fetch
    filter_val = f"from_publication_date:{_from_date(lookback_days)}"
    data = fetch(
        {"search": query, "filter": filter_val, "per_page": min(max_results, 200)}, contact_email
    )
    return [normalize_work(w) for w in data.get("results", [])]


def fetch_citing_user_works(
    seed_work_ids: list[str],
    max_results: int,
    contact_email: str,
    *,
    lookback_days: int = 14,
    _fetch: Fetch | None = None,
) -> list[Paper]:
    """New works that cite the user's own papers (batched OR filter, one
    request). Without a date bound this would re-surface the same
    highly-cited old papers forever instead of only new arrivals; live
    testing against OpenAlex showed citation indexing lags publication by
    days to weeks, hence the wider (vs. arXiv's 2-day) default window."""
    if not seed_work_ids:
        return []
    fetch = _fetch or _default_fetch
    from_date = _from_date(lookback_days)
    filter_val = f"cites:{'|'.join(seed_work_ids)},from_publication_date:{from_date}"
    data = fetch({"filter": filter_val, "per_page": min(max_results, 200)}, contact_email)
    papers = [normalize_work(w) for w in data.get("results", [])]
    for p in papers:
        p.score_reasons.append("cites one of your papers")
    return papers


def fetch_citing_shared_references(
    reference_work_ids: list[str],
    max_results: int,
    contact_email: str,
    *,
    lookback_days: int = 14,
    _fetch: Fetch | None = None,
) -> list[Paper]:
    """New works that cite references the user's own papers also cite
    (co-citation channel), batched and date-bounded the same way."""
    if not reference_work_ids:
        return []
    fetch = _fetch or _default_fetch
    filter_val = (
        f"cites:{'|'.join(reference_work_ids)},from_publication_date:{_from_date(lookback_days)}"
    )
    data = fetch({"filter": filter_val, "per_page": min(max_results, 200)}, contact_email)
    papers = [normalize_work(w) for w in data.get("results", [])]
    for p in papers:
        p.score_reasons.append("cites a reference you also cite")
    return papers
