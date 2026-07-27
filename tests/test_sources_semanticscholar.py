import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from recto.models import Paper
from recto.sources import semanticscholar

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture() -> list[dict]:
    return json.loads((FIXTURES_DIR / "semanticscholar" / "batch_response.json").read_text())


def _truncated_paper() -> Paper:
    return Paper(
        id="arxiv:2201.11903",
        source="scholar_alerts",
        title="Chain of Thought Prompting Elicits Reasoning in Large Language Models",
        authors=["Jason Wei"],
        abstract=None,
        published_at=datetime(2022, 1, 1, tzinfo=UTC),
        url="https://arxiv.org/abs/2201.11903",
        doi=None,
        external_ids={"arxiv": "2201.11903"},
    )


def test_enrich_fills_missing_abstract_and_summary():
    paper = _truncated_paper()
    result = semanticscholar.enrich(
        [paper], "test@example.com", _fetch_batch=lambda ids, email: _fixture()
    )
    assert result[0].abstract is not None
    assert result[0].abstract.startswith("We explore how generating a chain of thought")
    assert result[0].summary is not None  # tldr filled


def test_enrich_skips_papers_that_already_have_abstract():
    paper = _truncated_paper()
    paper.abstract = "already have one"
    called = {"n": 0}

    def fetch_batch(ids, email):
        called["n"] += 1
        return _fixture()

    semanticscholar.enrich([paper], "test@example.com", _fetch_batch=fetch_batch)
    assert called["n"] == 0
    assert paper.abstract == "already have one"


def test_enrich_degrades_silently_on_none_response():
    paper = _truncated_paper()
    result = semanticscholar.enrich(
        [paper], "test@example.com", _fetch_batch=lambda ids, email: None
    )
    assert result[0].abstract is None  # unchanged, no raise


def test_enrich_no_papers_with_usable_ids_short_circuits():
    paper = Paper(
        id="scholar_alerts:xyz",
        source="scholar_alerts",
        title="Some Alert Paper",
        authors=[],
        abstract=None,
        published_at=None,
        url="https://scholar.google.com/x",
    )

    def fail_fetch(ids, email):
        raise AssertionError("should not be called, no doi/arxiv id")

    result = semanticscholar.enrich([paper], "test@example.com", _fetch_batch=fail_fetch)
    assert result[0].abstract is None


def test_search_by_title_returns_none_on_no_results():
    result = semanticscholar.search_by_title(
        "Some Title", "test@example.com", _fetch_search=lambda q, e: {"data": []}
    )
    assert result is None


def test_default_fetch_batch_429_degrades_with_warning_log(monkeypatch, caplog):
    def fake_request(self, method, url, **kwargs):
        return httpx.Response(429, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)

    paper = _truncated_paper()
    with caplog.at_level("WARNING"):
        result = semanticscholar.enrich([paper], "test@example.com")

    assert result[0].abstract is None
    assert any("rate-limited" in r.message for r in caplog.records)


def test_search_by_title_returns_top_result():
    result = semanticscholar.search_by_title(
        "Some Title",
        "test@example.com",
        _fetch_search=lambda q, e: {"data": [{"title": "Some Title", "abstract": "x"}]},
    )
    assert result["title"] == "Some Title"
