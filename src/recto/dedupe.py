"""Dedupe: DOI > arXiv id > normalized title, both within a fetch batch and
against previously-seen papers in the store."""

from __future__ import annotations

import re

from recto.models import Paper
from recto.store import Store


def normalize_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9\s]", " ", title.lower())
    return " ".join(t.split())


def canonical_key(paper: Paper) -> tuple[str, str]:
    if paper.doi:
        return ("doi", paper.doi.lower())
    arxiv_id = paper.external_ids.get("arxiv")
    if arxiv_id:
        return ("arxiv", arxiv_id)
    return ("title", normalize_title(paper.title))


def assign_id(paper: Paper) -> str:
    key_type, key_value = canonical_key(paper)
    return f"{key_type}:{key_value}"


def dedupe_batch(papers: list[Paper]) -> list[Paper]:
    """Merges duplicates within one fetch batch (e.g. the same paper showing
    up via both arXiv and an OpenAlex citation-graph query)."""
    merged: dict[tuple[str, str], Paper] = {}
    for p in papers:
        p.id = assign_id(p)
        key = canonical_key(p)
        existing = merged.get(key)
        if existing is None:
            merged[key] = p
            continue
        if not existing.abstract and p.abstract:
            existing.abstract = p.abstract
        if not existing.doi and p.doi:
            existing.doi = p.doi
        existing.categories = list(dict.fromkeys(existing.categories + p.categories))
        existing.external_ids = {**p.external_ids, **existing.external_ids}
        existing.score_reasons = list(dict.fromkeys(existing.score_reasons + p.score_reasons))
    return list(merged.values())


def filter_seen(papers: list[Paper], store: Store) -> tuple[list[Paper], list[Paper]]:
    """Splits a batch into (genuinely new, already seen on a prior day).
    "Seen" means previously shown/saved/dismissed, not merely fetched —
    a paper fetched before but never shortlisted is still a fresh candidate."""
    new: list[Paper] = []
    seen: list[Paper] = []
    for p in papers:
        title_norm = normalize_title(p.title)
        existing = store.find_existing(id_=p.id, doi=p.doi, title_norm=title_norm)
        if existing is not None and existing.state != "new":
            seen.append(p)
        else:
            new.append(p)
    return new, seen
