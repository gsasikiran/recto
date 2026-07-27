"""SQLite persistence: papers, runs, profile_terms.

Idempotency: `papers.id` is a global primary key (not per-day) — re-seeing a
paper updates its metadata/score but never resets `state`, so a paper already
`shown`/`dismissed` on a prior day is never re-shortlisted. `runs.run_date` is
also a primary key, and `INSERT OR REPLACE` there plus an unconditional digest
file overwrite in render.py is what makes re-running a day idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from recto.models import Paper

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  title_norm TEXT NOT NULL,
  authors TEXT NOT NULL,
  abstract TEXT,
  published_at TEXT,
  url TEXT NOT NULL,
  doi TEXT,
  venue TEXT,
  categories TEXT,
  external_ids TEXT,
  score REAL DEFAULT 0.0,
  score_reasons TEXT,
  summary TEXT,
  seen_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'new',
  picked_on TEXT
);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_title_norm ON papers(title_norm);

CREATE TABLE IF NOT EXISTS runs (
  run_date TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  counts_json TEXT,
  paper_of_day_id TEXT,
  degraded_reasons TEXT,
  digest_path TEXT
);

CREATE TABLE IF NOT EXISTS profile_terms (
  term TEXT NOT NULL,
  source TEXT NOT NULL,
  weight REAL NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (term, source)
);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- papers ---------------------------------------------------------

    def upsert_papers(self, papers: Iterable[Paper]) -> None:
        rows = [
            (
                p.id,
                p.source,
                p.title,
                _normalize_title(p.title),
                json.dumps(p.authors),
                p.abstract,
                _iso(p.published_at),
                p.url,
                p.doi,
                p.venue,
                json.dumps(p.categories),
                json.dumps(p.external_ids),
                p.score,
                json.dumps(p.score_reasons),
                p.summary,
                _iso(p.seen_at),
                p.state,
            )
            for p in papers
        ]
        self.conn.executemany(
            """
            INSERT INTO papers (
                id, source, title, title_norm, authors, abstract, published_at,
                url, doi, venue, categories, external_ids, score, score_reasons,
                summary, seen_at, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                title_norm=excluded.title_norm,
                authors=excluded.authors,
                abstract=COALESCE(excluded.abstract, papers.abstract),
                published_at=COALESCE(excluded.published_at, papers.published_at),
                venue=COALESCE(excluded.venue, papers.venue),
                categories=excluded.categories,
                external_ids=excluded.external_ids,
                score=excluded.score,
                score_reasons=excluded.score_reasons,
                summary=COALESCE(excluded.summary, papers.summary)
            """,
            rows,
        )
        self.conn.commit()

    def get_paper(self, paper_id: str) -> Paper | None:
        row = self.conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return self._row_to_paper(row) if row else None

    def find_existing(self, *, id_: str, doi: str | None, title_norm: str) -> Paper | None:
        row = self.conn.execute(
            "SELECT * FROM papers WHERE id = ? OR (doi IS NOT NULL AND doi = ?) "
            "OR title_norm = ? LIMIT 1",
            (id_, doi, title_norm),
        ).fetchone()
        return self._row_to_paper(row) if row else None

    def get_candidates(self, limit: int | None = None) -> list[Paper]:
        sql = "SELECT * FROM papers WHERE state = 'new' ORDER BY published_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self.conn.execute(sql).fetchall()
        return [self._row_to_paper(r) for r in rows]

    def mark_state(self, paper_id: str, state: str) -> None:
        self.conn.execute("UPDATE papers SET state = ? WHERE id = ?", (state, paper_id))
        self.conn.commit()

    def mark_picked(self, paper_id: str, run_date: str) -> None:
        self.conn.execute("UPDATE papers SET picked_on = ? WHERE id = ?", (run_date, paper_id))
        self.conn.commit()

    @staticmethod
    def _row_to_paper(row: sqlite3.Row) -> Paper:
        return Paper(
            id=row["id"],
            source=row["source"],
            title=row["title"],
            authors=json.loads(row["authors"]),
            abstract=row["abstract"],
            published_at=_parse_iso(row["published_at"]),
            url=row["url"],
            doi=row["doi"],
            venue=row["venue"],
            categories=json.loads(row["categories"]) if row["categories"] else [],
            external_ids=json.loads(row["external_ids"]) if row["external_ids"] else {},
            score=row["score"],
            score_reasons=json.loads(row["score_reasons"]) if row["score_reasons"] else [],
            summary=row["summary"],
            seen_at=_parse_iso(row["seen_at"]),
            state=row["state"],
        )

    # -- runs -------------------------------------------------------------

    def start_run(self, run_date: str, started_at: datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs (run_date, started_at, status) VALUES (?, ?, 'running')",
            (run_date, _iso(started_at)),
        )
        self.conn.commit()

    def finish_run(
        self,
        run_date: str,
        *,
        finished_at: datetime,
        status: str,
        counts: dict,
        paper_of_day_id: str | None,
        degraded_reasons: list[str],
        digest_path: str | None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE runs SET finished_at=?, status=?, counts_json=?, paper_of_day_id=?,
                degraded_reasons=?, digest_path=? WHERE run_date=?
            """,
            (
                _iso(finished_at),
                status,
                json.dumps(counts),
                paper_of_day_id,
                json.dumps(degraded_reasons),
                digest_path,
                run_date,
            ),
        )
        self.conn.commit()

    def get_run(self, run_date: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM runs WHERE run_date = ?", (run_date,)).fetchone()
        return dict(row) if row else None

    def get_latest_run(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # -- profile_terms ------------------------------------------------------

    def upsert_profile_terms(
        self, terms: Iterable[tuple[str, str, float]], updated_at: datetime
    ) -> None:
        rows = [(term, source, weight, _iso(updated_at)) for term, source, weight in terms]
        self.conn.executemany(
            """
            INSERT INTO profile_terms (term, source, weight, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(term, source) DO UPDATE SET
                weight=excluded.weight, updated_at=excluded.updated_at
            """,
            rows,
        )
        self.conn.commit()

    def adjust_feedback_term(self, term: str, delta: float, updated_at: datetime) -> None:
        self.conn.execute(
            """
            INSERT INTO profile_terms (term, source, weight, updated_at)
            VALUES (?, 'feedback', ?, ?)
            ON CONFLICT(term, source) DO UPDATE SET
                weight=weight + ?, updated_at=excluded.updated_at
            """,
            (term, delta, _iso(updated_at), delta),
        )
        self.conn.commit()
