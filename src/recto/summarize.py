"""One batched OpenRouter call per digest: summarize the shortlist, rank,
and pick one, all in a single request (never one call per paper). Structured
JSON output only — never parse prose. Any failure (network, malformed JSON,
unknown id) degrades to SummarizeResult(degraded=True) rather than raising —
a dead LLM API still delivers a digest with titles and abstracts.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from recto.config import LLMConfig
from recto.models import Paper
from recto.net import request

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """You are helping a researcher triage their daily paper digest.

Their research profile (from profile.md, in their own words):
---
{profile_md}
---

You will be given a shortlist of candidate papers as a JSON array, each with
its id, title, authors, abstract, and the lexical/citation-graph signals
that already surfaced it (score_reasons). For each paper, write a 2-3
sentence summary in plain language that states what is new relative to
prior work — no marketing tone. Then pick exactly ONE paper as "read this
today" and write a rationale that explicitly references the researcher's
own prior work (e.g. "this extends the method in your 2024 paper on X").

Respond with JSON only, matching this exact shape:
{{
  "summaries": [{{"id": "<paper id>", "summary": "..."}}, ...],
  "paper_of_the_day": {{"id": "<paper id>", "rationale": "..."}}
}}
Every id in your response must be one of the ids given below. Do not invent ids.
"""

Call = Callable[[LLMConfig, str | None, str, str], dict | None]

# Anthropic models have no native strict JSON mode, so `response_format:
# json_object` isn't reliably enforced when routed through OpenRouter — the
# model sometimes wraps its reply in a ```json ... ``` fence and sometimes
# doesn't, for the same prompt. Strip the fence if present before parsing.
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def _strip_code_fence(content: str) -> str:
    match = _CODE_FENCE_RE.match(content.strip())
    return match.group(1) if match else content


@dataclass(slots=True)
class PaperSummary:
    id: str
    summary: str


@dataclass(slots=True)
class SummarizeResult:
    summaries: list[PaperSummary] = field(default_factory=list)
    paper_of_day_id: str | None = None
    rationale: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None


def _shortlist_payload(papers: list[Paper]) -> str:
    items = [
        {
            "id": p.id,
            "title": p.title,
            "authors": p.authors[:5],
            "abstract": (p.abstract or "")[:1000],
            "score_reasons": p.score_reasons,
        }
        for p in papers
    ]
    return json.dumps(items, indent=2)


def _default_call(
    cfg: LLMConfig, api_key: str | None, system_prompt: str, user_content: str
) -> dict | None:
    if not api_key:
        logger.warning("no OpenRouter API key available, skipping LLM summarize/pick")
        return None
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }
    resp = request(
        "POST",
        cfg.api_base,
        headers=headers,
        json=body,
        timeout=cfg.timeout_seconds,
        max_retries=cfg.max_retries,
    )
    if resp is None or resp.status_code != 200:
        logger.warning("openrouter call failed: %s", resp and resp.status_code)
        return None
    return resp.json()


def summarize_shortlist(
    shortlisted: list[Paper],
    *,
    profile_md_text: str,
    cfg: LLMConfig,
    api_key: str | None,
    _call: Call | None = None,
) -> SummarizeResult:
    if not shortlisted:
        return SummarizeResult()

    call = _call or _default_call
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        profile_md=profile_md_text or "(no profile.md content)"
    )
    user_content = _shortlist_payload(shortlisted)

    response = call(cfg, api_key, system_prompt, user_content)
    if response is None:
        return SummarizeResult(degraded=True, degraded_reason="llm_unreachable")

    try:
        content = response["choices"][0]["message"]["content"]
        parsed = json.loads(_strip_code_fence(content))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("openrouter response malformed: %s", exc)
        return SummarizeResult(degraded=True, degraded_reason="llm_malformed_response")

    valid_ids = {p.id for p in shortlisted}
    summaries_raw = parsed.get("summaries")
    pod_raw = parsed.get("paper_of_the_day")
    if not isinstance(summaries_raw, list) or not isinstance(pod_raw, dict):
        return SummarizeResult(degraded=True, degraded_reason="llm_malformed_response")

    summaries: list[PaperSummary] = []
    for item in summaries_raw:
        if not isinstance(item, dict):
            continue
        sid, stext = item.get("id"), item.get("summary")
        if sid in valid_ids and isinstance(stext, str) and stext.strip():
            summaries.append(PaperSummary(id=sid, summary=stext.strip()))

    pod_id = pod_raw.get("id")
    rationale = pod_raw.get("rationale")
    if pod_id not in valid_ids or not isinstance(rationale, str) or not rationale.strip():
        logger.warning("openrouter picked an invalid or missing paper id: %r", pod_id)
        return SummarizeResult(
            summaries=summaries, degraded=True, degraded_reason="llm_invalid_pick"
        )

    return SummarizeResult(
        summaries=summaries, paper_of_day_id=pod_id, rationale=rationale.strip(), degraded=False
    )
