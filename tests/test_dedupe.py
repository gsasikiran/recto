from datetime import UTC, datetime

import pytest

from recto.dedupe import canonical_key, dedupe_batch, filter_seen, normalize_title
from recto.models import Paper
from recto.store import Store


def _paper(**kwargs) -> Paper:
    defaults = dict(
        id="",
        source="arxiv",
        title="A Great Paper: Study",
        authors=["A. Author"],
        abstract="abstract",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        url="https://example.com",
        doi=None,
        external_ids={},
    )
    defaults.update(kwargs)
    return Paper(**defaults)


def test_normalize_title_strips_punctuation_and_case():
    assert normalize_title("A Great Paper: Study!") == "a great paper study"
    assert normalize_title("a  great   paper study") == "a great paper study"


def test_canonical_key_prefers_doi_over_arxiv_over_title():
    p_doi = _paper(doi="10.1/X", external_ids={"arxiv": "2507.1"})
    assert canonical_key(p_doi) == ("doi", "10.1/x")

    p_arxiv = _paper(doi=None, external_ids={"arxiv": "2507.1"})
    assert canonical_key(p_arxiv) == ("arxiv", "2507.1")

    p_title = _paper(doi=None, external_ids={})
    assert canonical_key(p_title) == ("title", "a great paper study")


def test_dedupe_batch_merges_same_arxiv_id_from_two_sources():
    a = _paper(source="arxiv", external_ids={"arxiv": "2507.1"}, abstract=None)
    b = _paper(source="openalex", external_ids={"arxiv": "2507.1"}, abstract="filled in")
    result = dedupe_batch([a, b])
    assert len(result) == 1
    assert result[0].abstract == "filled in"


def test_dedupe_batch_does_not_merge_distinct_similar_titles():
    a = _paper(title="Scaling Laws for Transformers", external_ids={})
    b = _paper(title="Scaling Laws for Diffusion Models", external_ids={})
    result = dedupe_batch([a, b])
    assert len(result) == 2


def test_dedupe_batch_assigns_stable_ids():
    a = _paper(doi="10.1/ABC", external_ids={})
    result = dedupe_batch([a])
    assert result[0].id == "doi:10.1/abc"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    yield s
    s.close()


def test_filter_seen_excludes_dismissed_but_keeps_new(store):
    shown_before = _paper(title="Already Shown Paper", external_ids={})
    shown_before.id = "title:already-shown-paper"
    store.upsert_papers([shown_before])
    store.mark_state(shown_before.id, "dismissed")

    fresh = _paper(title="Brand New Paper", external_ids={})
    fresh.id = "title:brand-new-paper"

    incoming_dupe = _paper(title="Already Shown Paper", external_ids={})
    incoming_dupe.id = "title:already-shown-paper"

    new, seen = filter_seen([fresh, incoming_dupe], store)
    assert [p.id for p in new] == ["title:brand-new-paper"]
    assert [p.id for p in seen] == ["title:already-shown-paper"]


def test_filter_seen_treats_previously_fetched_but_unshown_as_new(store):
    prior = _paper(title="Fetched Yesterday", external_ids={})
    prior.id = "title:fetched-yesterday"
    store.upsert_papers([prior])  # state stays 'new'

    incoming = _paper(title="Fetched Yesterday", external_ids={})
    incoming.id = "title:fetched-yesterday"

    new, seen = filter_seen([incoming], store)
    assert len(new) == 1
    assert len(seen) == 0
