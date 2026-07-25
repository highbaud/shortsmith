"""Anthropic Claude backend for clip finding.

Default backend. Best quality — Opus 4.7 is exceptional at the topical-clarity
and evergreen-filter gates. Costs ~$0.10-$2.00 per source video depending on
transcript length.
"""
from __future__ import annotations

import logging

from ..config import Config
from ._common import (
    covered_topics_block,
    format_transcript,
    load_system_prompt,
    parse_json_response,
    performance_block,
)

log = logging.getLogger(__name__)


def call(words: list[dict], cfg: Config) -> list[dict]:
    """Send the transcript to Claude and return parsed JSON clip list."""
    import anthropic

    if not cfg.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it in your environment or .env, "
            "or pass --clip-engine ollama / write clips.json manually and resume "
            "with --from-step 3."
        )

    transcript_text = (
        format_transcript(words) + covered_topics_block() + performance_block()
    )
    system_prompt = load_system_prompt()

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    max_tokens = getattr(cfg, "clip_max_tokens", 32000)
    log.info("Calling Claude (%s) to rank clips (max_tokens=%d)...",
             cfg.claude_model, max_tokens)
    resp = client.messages.create(
        model=cfg.claude_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": transcript_text}],
    )

    # If the model hit the output cap, the JSON array is almost certainly cut
    # off mid-clip — surface it loudly rather than silently dropping the tail.
    if getattr(resp, "stop_reason", None) == "max_tokens":
        log.warning(
            "Claude hit max_tokens (%d) — the clip list is likely truncated and "
            "trailing clips were lost. Raise SHORTSMITH_CLIP_MAX_TOKENS.",
            max_tokens,
        )

    raw = resp.content[0].text  # type: ignore[attr-defined]
    return parse_json_response(raw)
