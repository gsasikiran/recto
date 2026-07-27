"""arXiv source: official Atom API, filtered by category then by date.

Rate limit: arXiv asks for one request per 3 seconds and a real contact
email in the User-Agent — both enforced here via net.RateLimiter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree

from recto.models import Paper
from recto.net import RateLimiter, build_user_agent, request

logger = logging.getLogger(__name__)

API_URL = "https://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
PAGE_SIZE = 100

_rate_limiter = RateLimiter(min_interval_seconds=3.0)

FetchPage = Callable[..., bytes]


def _default_fetch_page(
    categories: list[str], start: int, page_size: int, contact_email: str
) -> bytes:
    query = " OR ".join(f"cat:{c}" for c in categories)
    headers = {"User-Agent": build_user_agent("arxiv", contact_email)}
    params = {
        "search_query": query,
        "start": start,
        "max_results": page_size,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    resp = request(
        "GET", API_URL, rate_limiter=_rate_limiter, headers=headers, params=params, timeout=30.0
    )
    if resp is None or resp.status_code != 200:
        logger.warning("arxiv fetch failed at start=%d: %s", start, resp and resp.status_code)
        return b""
    return resp.content


def _text(el, tag: str, ns: str = ATOM_NS) -> str | None:
    node = el.find(f"{ns}{tag}")
    return node.text.strip() if node is not None and node.text else None


def _normalize_arxiv_id(raw_id_url: str) -> str:
    # "http://arxiv.org/abs/2607.20402v1" -> "2607.20402"
    tail = raw_id_url.rsplit("/", 1)[-1]
    return tail.rsplit("v", 1)[0] if "v" in tail else tail


def _entry_to_paper(entry) -> Paper:
    raw_id = _text(entry, "id") or ""
    arxiv_id = _normalize_arxiv_id(raw_id)
    title = " ".join((_text(entry, "title") or "").split())
    abstract = " ".join((_text(entry, "summary") or "").split()) or None
    published_raw = _text(entry, "published")
    published_at = (
        datetime.fromisoformat(published_raw.replace("Z", "+00:00")) if published_raw else None
    )
    authors = [
        (_text(a, "name") or "").strip()
        for a in entry.findall(f"{ATOM_NS}author")
        if _text(a, "name")
    ]
    categories = [
        c.attrib.get("term", "")
        for c in entry.findall(f"{ATOM_NS}category")
        if c.attrib.get("term")
    ]
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"

    return Paper(
        id=f"arxiv:{arxiv_id}",
        source="arxiv",
        title=title,
        authors=authors,
        abstract=abstract,
        published_at=published_at,
        url=abs_url,
        doi=None,
        venue=None,
        categories=categories,
        external_ids={"arxiv": arxiv_id},
    )


def _parse_entries(xml_bytes: bytes) -> list:
    if not xml_bytes:
        return []
    root = ElementTree.fromstring(xml_bytes)
    return root.findall(f"{ATOM_NS}entry")


def fetch(
    categories: list[str],
    lookback_days: int,
    max_results: int,
    contact_email: str,
    *,
    _fetch_page: FetchPage | None = None,
) -> list[Paper]:
    fetch_page = _fetch_page or _default_fetch_page
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    papers: list[Paper] = []
    start = 0
    page_size = min(PAGE_SIZE, max_results)
    while start < max_results:
        xml_bytes = fetch_page(categories, start, page_size, contact_email)
        entries = _parse_entries(xml_bytes)
        if not entries:
            break

        page_had_recent = False
        for entry in entries:
            paper = _entry_to_paper(entry)
            if paper.published_at is not None and paper.published_at < cutoff:
                continue
            page_had_recent = True
            papers.append(paper)

        if len(entries) < page_size or not page_had_recent:
            break
        start += page_size

    return papers
