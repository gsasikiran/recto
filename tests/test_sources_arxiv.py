from pathlib import Path

from recto.sources import arxiv

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture_bytes() -> bytes:
    return (FIXTURES_DIR / "arxiv" / "cs_ai_response.xml").read_bytes()


def test_fetch_parses_entries_into_papers():
    xml_bytes = _fixture_bytes()
    papers = arxiv.fetch(
        categories=["cs.AI"],
        lookback_days=3650,
        max_results=50,
        contact_email="test@example.com",
        _fetch_page=lambda *a, **k: xml_bytes,
    )
    assert len(papers) == 3

    p = papers[0]
    assert p.source == "arxiv"
    assert p.id == "arxiv:2607.20402"
    assert p.external_ids["arxiv"] == "2607.20402"
    assert p.title.startswith("SoftReason")
    assert p.published_at is not None
    assert p.published_at.tzinfo is not None
    assert "cs.AI" in p.categories
    assert p.authors == ["Wael AbdAlmageed"]
    assert p.url == "https://arxiv.org/abs/2607.20402"


def test_fetch_normalizes_multi_author_and_multi_category():
    xml_bytes = _fixture_bytes()
    papers = arxiv.fetch(
        categories=["cs.AI"],
        lookback_days=3650,
        max_results=50,
        contact_email="test@example.com",
        _fetch_page=lambda *a, **k: xml_bytes,
    )
    third = papers[2]
    assert third.authors == ["Eva McCord", "Ernest Pedapati", "Zag ElSayed"]
    assert set(third.categories) >= {"cs.HC", "cs.AI", "cs.ET"}


def test_fetch_filters_by_lookback_cutoff():
    xml_bytes = _fixture_bytes()
    # Fixture entries are all real, dated relative to whenever this fixture
    # was recorded. A lookback far enough in the past (well before arXiv's
    # 1991 founding) always keeps everything; forcing a cutoff long after
    # "now" (impossible) always excludes everything. Test both directions
    # without depending on wall-clock time matching fixture dates.
    kept = arxiv.fetch(
        categories=["cs.AI"],
        lookback_days=365 * 100,
        max_results=50,
        contact_email="test@example.com",
        _fetch_page=lambda *a, **k: xml_bytes,
    )
    assert len(kept) == 3

    excluded = arxiv.fetch(
        categories=["cs.AI"],
        lookback_days=-365 * 100,
        max_results=50,
        contact_email="test@example.com",
        _fetch_page=lambda *a, **k: xml_bytes,
    )
    assert excluded == []


def test_fetch_stops_on_empty_page():
    papers = arxiv.fetch(
        categories=["cs.AI"],
        lookback_days=3650,
        max_results=50,
        contact_email="test@example.com",
        _fetch_page=lambda *a, **k: b"",
    )
    assert papers == []
