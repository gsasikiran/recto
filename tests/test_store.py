from datetime import UTC, datetime

import pytest

from recto.models import Paper
from recto.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    yield s
    s.close()


def _paper(id_="arxiv:2507.00001", title="A Great Paper", score=1.0) -> Paper:
    return Paper(
        id=id_,
        source="arxiv",
        title=title,
        authors=["A. Author"],
        abstract="abstract text",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        url="https://arxiv.org/abs/2507.00001",
        doi=None,
        venue=None,
        categories=["cs.AI"],
        score=score,
    )


def test_upsert_then_get(store):
    store.upsert_papers([_paper()])
    p = store.get_paper("arxiv:2507.00001")
    assert p is not None
    assert p.title == "A Great Paper"
    assert p.state == "new"


def test_upsert_idempotent_same_id_updates_score_not_state(store):
    store.upsert_papers([_paper(score=1.0)])
    store.mark_state("arxiv:2507.00001", "shown")
    store.upsert_papers([_paper(score=5.0)])

    p = store.get_paper("arxiv:2507.00001")
    assert p.score == 5.0
    assert p.state == "shown"  # not reset by re-upsert

    count = store.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    assert count == 1  # no duplicate row


def test_get_candidates_excludes_non_new(store):
    store.upsert_papers([_paper(id_="a"), _paper(id_="b", title="Other")])
    store.mark_state("a", "dismissed")
    candidates = store.get_candidates()
    assert [p.id for p in candidates] == ["b"]


def test_run_lifecycle(store):
    store.start_run("2026-07-23", datetime.now(UTC))
    store.finish_run(
        "2026-07-23",
        finished_at=datetime.now(UTC),
        status="ok",
        counts={"arxiv": 10},
        paper_of_day_id="arxiv:2507.00001",
        degraded_reasons=[],
        digest_path="digests/2026-07-23.md",
    )
    run = store.get_run("2026-07-23")
    assert run["status"] == "ok"
    assert run["paper_of_day_id"] == "arxiv:2507.00001"

    # re-running the same day overwrites, not duplicates
    store.start_run("2026-07-23", datetime.now(UTC))
    count = store.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert count == 1


def test_profile_terms_upsert_and_feedback_delta(store):
    now = datetime.now(UTC)
    store.upsert_profile_terms([("transformer", "orcid", 0.8)], now)
    store.adjust_feedback_term("transformer", 0.1, now)
    row = store.conn.execute(
        "SELECT weight FROM profile_terms WHERE term='transformer' AND source='feedback'"
    ).fetchone()
    assert row[0] == pytest.approx(0.1)
