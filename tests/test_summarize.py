import json
from datetime import UTC, datetime

from recto.config import LLMConfig
from recto.models import Paper
from recto.summarize import summarize_shortlist


def _papers():
    return [
        Paper(
            id="arxiv:1",
            source="arxiv",
            title="Paper One",
            authors=["A"],
            abstract="abstract one",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            url="https://x",
        ),
        Paper(
            id="arxiv:2",
            source="arxiv",
            title="Paper Two",
            authors=["B"],
            abstract="abstract two",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            url="https://y",
        ),
    ]


def _openrouter_envelope(content_obj: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(content_obj)}}]}


def test_summarize_happy_path():
    papers = _papers()
    good_response = _openrouter_envelope(
        {
            "summaries": [
                {"id": "arxiv:1", "summary": "Summary one."},
                {"id": "arxiv:2", "summary": "Summary two."},
            ],
            "paper_of_the_day": {"id": "arxiv:2", "rationale": "Extends your 2024 work on X."},
        }
    )
    result = summarize_shortlist(
        papers,
        profile_md_text="profile text",
        cfg=LLMConfig(),
        api_key="fake-key",
        _call=lambda cfg, key, sys_p, user_c: good_response,
    )
    assert result.degraded is False
    assert result.paper_of_day_id == "arxiv:2"
    assert result.rationale == "Extends your 2024 work on X."
    assert {s.id for s in result.summaries} == {"arxiv:1", "arxiv:2"}


def test_summarize_handles_markdown_fenced_json():
    """Regression test: Anthropic models have no strict JSON mode, so
    response_format=json_object isn't reliably enforced through OpenRouter —
    the same prompt sometimes comes back wrapped in a ```json fence. That
    used to fail json.loads outright and silently degrade every such run."""
    papers = _papers()
    content_obj = {
        "summaries": [
            {"id": "arxiv:1", "summary": "Summary one."},
            {"id": "arxiv:2", "summary": "Summary two."},
        ],
        "paper_of_the_day": {"id": "arxiv:2", "rationale": "Extends your 2024 work on X."},
    }
    fenced_response = {
        "choices": [{"message": {"content": f"```json\n{json.dumps(content_obj)}\n```"}}]
    }
    result = summarize_shortlist(
        papers,
        profile_md_text="profile text",
        cfg=LLMConfig(),
        api_key="fake-key",
        _call=lambda cfg, key, sys_p, user_c: fenced_response,
    )
    assert result.degraded is False
    assert result.paper_of_day_id == "arxiv:2"
    assert {s.id for s in result.summaries} == {"arxiv:1", "arxiv:2"}


def test_summarize_no_api_key_degrades():
    papers = _papers()
    result = summarize_shortlist(papers, profile_md_text="p", cfg=LLMConfig(), api_key=None)
    assert result.degraded is True
    assert result.degraded_reason == "llm_unreachable"


def test_summarize_network_failure_degrades():
    papers = _papers()
    result = summarize_shortlist(
        papers,
        profile_md_text="p",
        cfg=LLMConfig(),
        api_key="fake-key",
        _call=lambda cfg, key, sys_p, user_c: None,
    )
    assert result.degraded is True
    assert result.degraded_reason == "llm_unreachable"


def test_summarize_malformed_json_degrades():
    papers = _papers()
    bad_response = {"choices": [{"message": {"content": "not valid json {{{"}}]}
    result = summarize_shortlist(
        papers,
        profile_md_text="p",
        cfg=LLMConfig(),
        api_key="fake-key",
        _call=lambda cfg, key, sys_p, user_c: bad_response,
    )
    assert result.degraded is True
    assert result.degraded_reason == "llm_malformed_response"


def test_summarize_unexpected_shape_degrades():
    papers = _papers()
    wrong_shape = _openrouter_envelope({"unexpected": "shape"})
    result = summarize_shortlist(
        papers,
        profile_md_text="p",
        cfg=LLMConfig(),
        api_key="fake-key",
        _call=lambda cfg, key, sys_p, user_c: wrong_shape,
    )
    assert result.degraded is True
    assert result.degraded_reason == "llm_malformed_response"


def test_summarize_id_not_in_shortlist_degrades_pick_but_keeps_valid_summaries():
    papers = _papers()
    response = _openrouter_envelope(
        {
            "summaries": [
                {"id": "arxiv:1", "summary": "Summary one."},
                {"id": "arxiv:999", "summary": "Hallucinated id."},
            ],
            "paper_of_the_day": {"id": "arxiv:999", "rationale": "Made up."},
        }
    )
    result = summarize_shortlist(
        papers,
        profile_md_text="p",
        cfg=LLMConfig(),
        api_key="fake-key",
        _call=lambda cfg, key, sys_p, user_c: response,
    )
    assert result.degraded is True
    assert result.degraded_reason == "llm_invalid_pick"
    assert {s.id for s in result.summaries} == {"arxiv:1"}  # hallucinated id dropped


def test_summarize_empty_shortlist_short_circuits():
    result = summarize_shortlist([], profile_md_text="p", cfg=LLMConfig(), api_key="fake-key")
    assert result.degraded is False
    assert result.summaries == []
