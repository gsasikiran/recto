import json
from pathlib import Path

from recto.sources import openalex

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture() -> dict:
    return json.loads((FIXTURES_DIR / "openalex" / "keyword_search_response.json").read_text())


def test_fetch_keyword_normalizes_to_papers():
    data = _fixture()
    papers = openalex.fetch_keyword(
        "large language model reasoning",
        max_results=10,
        contact_email="test@example.com",
        _fetch=lambda params, email: data,
    )
    assert len(papers) == 3
    p = papers[0]
    assert p.source == "openalex"
    assert p.doi == "10.52202/068431-1800"
    assert p.id == "doi:10.52202/068431-1800"
    assert p.title.startswith("Chain-Of-Thought")
    assert p.authors[0] == "Jason Wei"
    assert p.abstract is None  # this entry has no abstract_inverted_index


def test_abstract_inverted_index_is_reconstructed_in_order():
    data = _fixture()
    papers = openalex.fetch_keyword(
        "large language model reasoning",
        max_results=10,
        contact_email="test@example.com",
        _fetch=lambda params, email: data,
    )
    p = papers[1]
    assert p.abstract is not None
    assert p.abstract.startswith("Importance: Large language models (LLMs)")


def test_work_without_doi_falls_back_to_openalex_id():
    work = {
        "id": "https://openalex.org/W123",
        "doi": None,
        "title": "No DOI Paper",
        "authorships": [],
        "publication_date": "2026-01-01",
        "primary_location": {},
        "abstract_inverted_index": None,
    }
    paper = openalex.normalize_work(work)
    assert paper.id == "openalex:W123"
    assert paper.doi is None


def test_fetch_citing_user_works_batches_seed_ids_into_one_filter():
    captured = {}

    def fake_fetch(params, email):
        captured.update(params)
        return {"results": []}

    openalex.fetch_citing_user_works(
        ["W1", "W2", "W3"], max_results=50, contact_email="t@example.com", _fetch=fake_fetch
    )
    assert captured["filter"].startswith("cites:W1|W2|W3,from_publication_date:")


def test_fetch_citing_user_works_empty_seeds_short_circuits():
    def fail_fetch(params, email):
        raise AssertionError("should not be called with no seeds")

    papers = openalex.fetch_citing_user_works(
        [], max_results=50, contact_email="t@example.com", _fetch=fail_fetch
    )
    assert papers == []


def test_fetch_citing_user_works_tags_score_reason():
    data = _fixture()
    papers = openalex.fetch_citing_user_works(
        ["W1"], max_results=10, contact_email="t@example.com", _fetch=lambda p, e: data
    )
    assert all("cites one of your papers" in p.score_reasons for p in papers)
