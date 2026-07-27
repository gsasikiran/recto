"""Core data model shared by every source, scoring, and delivery module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

PaperState = Literal["new", "shown", "saved", "dismissed"]


@dataclass(slots=True)
class Paper:
    id: str
    source: str
    title: str
    authors: list[str]
    abstract: str | None
    published_at: datetime | None
    url: str
    doi: str | None = None
    venue: str | None = None
    categories: list[str] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)
    score: float = 0.0
    score_reasons: list[str] = field(default_factory=list)
    summary: str | None = None
    seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    state: PaperState = "new"
