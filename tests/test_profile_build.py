import json
from pathlib import Path

from recto.profile import build
from recto.profile.orcid import OrcidWork

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _orcid_works_fixture():
    from recto.profile import orcid

    data = json.loads((FIXTURES_DIR / "orcid" / "works_response.json").read_text())
    return orcid.parse_works(data)


def _openalex_lookup_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "orcid" / "openalex_doi_lookup.json").read_text())


def test_build_index_produces_profile_vector_and_terms():
    works = _orcid_works_fixture()
    lookup = _openalex_lookup_fixture()

    index = build.build_index(
        orcid_id="0000-0003-3098-4592",
        profile_md_text="I am interested in multi-agent reasoning and LLM evaluation.",
        contact_email="test@example.com",
        _fetch_works=lambda oid, email: works,
        _resolve_openalex=lambda doi, email: lookup if doi else None,
    )

    assert index.profile_vector.shape[0] == 1
    assert len(index.top_terms) > 0
    assert index.seed_work_ids  # at least one resolved openalex id captured
    assert index.built_at is not None


def test_build_index_falls_back_to_title_only_when_resolution_fails():
    works = [OrcidWork(title="Some Paper Title", year=2024, doi="10.1/x", put_code=1)]

    index = build.build_index(
        orcid_id="0000-0003-3098-4592",
        profile_md_text="research interests here",
        contact_email="test@example.com",
        _fetch_works=lambda oid, email: works,
        _resolve_openalex=lambda doi, email: None,
    )
    # still produces a usable index, just without coauthors/seed ids from that work
    assert index.profile_vector.shape[0] == 1
    assert index.seed_work_ids == []


def test_build_index_with_no_orcid_id_uses_profile_md_only():
    index = build.build_index(
        orcid_id="",
        profile_md_text="I care about neurosymbolic reasoning and agents.",
        contact_email="test@example.com",
        _fetch_works=lambda oid, email: [],
    )
    terms = {t for t, _ in index.top_terms}
    assert "neurosymbolic" in terms or "reasoning" in terms or "agents" in terms


def test_profile_md_weight_stays_dominant_regardless_of_orcid_corpus_size():
    """profile.md ("Current focus") must stay dominant even if the ORCID
    corpus has many works, since it reflects where the user is going rather
    than where they've been — so its weight is pegged to a multiple of the
    *combined* ORCID weight, not a flat constant that many small works could
    outweigh in aggregate."""
    many_works = [
        OrcidWork(title=f"Paper {i}", year=2020 + (i % 5), doi=None, put_code=i) for i in range(30)
    ]

    index = build.build_index(
        orcid_id="0000-0003-3098-4592",
        profile_md_text="current focus text",
        contact_email="test@example.com",
        profile_md_dominance=2.0,
        _fetch_works=lambda oid, email: many_works,
        _resolve_openalex=lambda doi, email: None,
    )

    orcid_weight_total = sum(index.profile_doc_weights[:-1])
    profile_md_weight = index.profile_doc_weights[-1]
    assert index.profile_docs[-1] == "current focus text"
    assert profile_md_weight == orcid_weight_total * 2.0
    assert profile_md_weight > orcid_weight_total


def test_save_and_load_index_roundtrip(tmp_path):
    index = build.build_index(
        orcid_id="",
        profile_md_text="test profile text about robotics",
        contact_email="test@example.com",
        _fetch_works=lambda oid, email: [],
    )
    path = tmp_path / "profile_index.pkl"
    build.save_index(index, path)
    loaded = build.load_index(path)
    assert loaded is not None
    assert loaded.top_terms == index.top_terms


def test_needs_rebuild_true_when_missing(tmp_path):
    pkl = tmp_path / "profile_index.pkl"
    md = tmp_path / "profile.md"
    md.write_text("x")
    assert build.needs_rebuild(pkl, md) is True


def test_needs_rebuild_false_when_fresh(tmp_path):
    import time

    pkl = tmp_path / "profile_index.pkl"
    md = tmp_path / "profile.md"
    md.write_text("x")
    time.sleep(0.01)
    pkl.write_bytes(b"fake")
    assert build.needs_rebuild(pkl, md, max_age_days=30) is False


def test_needs_rebuild_true_when_profile_md_newer(tmp_path):
    import time

    pkl = tmp_path / "profile_index.pkl"
    md = tmp_path / "profile.md"
    pkl.write_bytes(b"fake")
    time.sleep(0.01)
    md.write_text("edited later")
    assert build.needs_rebuild(pkl, md) is True
