"""ORCID: pulls the user's own works as the profile bootstrap.

No manual topic list required — DOIs from ORCID get resolved via OpenAlex
(build.py) for abstracts, concepts, and reference lists.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from recto.net import RateLimiter, build_user_agent, request

logger = logging.getLogger(__name__)

ORCID_API_BASE = "https://pub.orcid.org/v3.0"
_rate_limiter = RateLimiter(min_interval_seconds=1.0)

FetchWorks = Callable[..., dict | None]


@dataclass(slots=True)
class OrcidWork:
    title: str
    year: int | None
    doi: str | None
    put_code: int | None


def _default_fetch_works(orcid_id: str, contact_email: str) -> dict | None:
    headers = {
        "Accept": "application/json",
        "User-Agent": build_user_agent("orcid", contact_email),
    }
    resp = request(
        "GET",
        f"{ORCID_API_BASE}/{orcid_id}/works",
        rate_limiter=_rate_limiter,
        headers=headers,
        timeout=20.0,
    )
    if resp is None or resp.status_code != 200:
        logger.warning("orcid works fetch failed for %s: %s", orcid_id, resp and resp.status_code)
        return None
    return resp.json()


def _extract_doi(external_ids: dict | None) -> str | None:
    if not external_ids:
        return None
    for eid in external_ids.get("external-id", []):
        if eid.get("external-id-type") == "doi":
            return eid.get("external-id-value")
    return None


def parse_works(data: dict) -> list[OrcidWork]:
    works: list[OrcidWork] = []
    for group in data.get("group", []):
        summaries = group.get("work-summary") or []
        if not summaries:
            continue
        ws = summaries[0]
        title = ((ws.get("title") or {}).get("title") or {}).get("value")
        if not title:
            continue
        pub_date = ws.get("publication-date") or {}
        year_raw = (pub_date.get("year") or {}).get("value") if pub_date else None
        year = int(year_raw) if year_raw else None
        doi = _extract_doi(group.get("external-ids")) or _extract_doi(ws.get("external-ids"))
        works.append(OrcidWork(title=title, year=year, doi=doi, put_code=ws.get("put-code")))
    return works


def fetch_works(
    orcid_id: str, contact_email: str, *, _fetch: FetchWorks | None = None
) -> list[OrcidWork]:
    fetch = _fetch or _default_fetch_works
    data = fetch(orcid_id, contact_email)
    if not data:
        return []
    return parse_works(data)
