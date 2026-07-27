import json
from pathlib import Path

from recto.profile import orcid

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture() -> dict:
    return json.loads((FIXTURES_DIR / "orcid" / "works_response.json").read_text())


def test_fetch_works_parses_all_groups():
    works = orcid.fetch_works(
        "0000-0003-3098-4592", "test@example.com", _fetch=lambda oid, email: _fixture()
    )
    assert len(works) == 5
    titles = [w.title for w in works]
    assert any("AIssistant" in t for t in titles)


def test_parse_works_extracts_doi_and_year():
    works = orcid.parse_works(_fixture())
    w = next(w for w in works if "MLReplicate" in w.title)
    assert w.doi == "10.48550/ARXIV.2605.16616"
    assert w.year == 2026


def test_fetch_works_returns_empty_on_none_response():
    works = orcid.fetch_works(
        "0000-0003-3098-4592", "test@example.com", _fetch=lambda oid, email: None
    )
    assert works == []


def test_parse_works_skips_group_without_title():
    data = {"group": [{"work-summary": [{"title": {"title": None}}]}]}
    assert orcid.parse_works(data) == []
