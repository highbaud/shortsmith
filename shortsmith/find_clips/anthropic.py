"""Anthropic Claude backend for clip finding.

Default backend. Best quality — Opus 4.7 is exceptional at the topical-clarity
and evergreen-filter gates. Costs ~$0.10-$2.00 per source video depending on
transcript length.
"""
from __future__ import annotations

import logging

from ..config import Config
from ._common import (
    load_system_prompt,
    parse_json_response,
    transcript_with_context,
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

    transcript_text = transcript_with_context(words)
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


def review(system_prompt: str, user_input: str, cfg: Config) -> list[dict]:
    """Run a separate story check and require a complete review response."""
    import anthropic

    if not cfg.anthropic_api_key:
        raise RuntimeError("Story check needs ANTHROPIC_API_KEY, or use --clip-engine ollama.")

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    try:
        resp = client.messages.create(
            model=cfg.claude_model,
            max_tokens=cfg.clip_max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_input}],
        )
    except Exception as exc:
        raise RuntimeError("Story check: Claude request failed. Check the API connection and retry.") from exc

    stop_reason = getattr(resp, "stop_reason", None)
    if stop_reason != "end_turn":
        advice = (
            "Raise SHORTSMITH_CLIP_MAX_TOKENS and retry."
            if stop_reason == "max_tokens"
            else "Retry the story check."
        )
        raise RuntimeError(f"Story check: Claude response did not finish ({stop_reason}). {advice}")

    content = getattr(resp, "content", None)
    if not isinstance(content, list):
        raise RuntimeError("Story check: Claude returned invalid JSON. Retry the story check.")
    raw = "\n".join(
        block.text for block in content
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    )
    try:
        reviews = parse_json_response(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Story check: Claude returned invalid JSON. Retry the story check.") from exc
    if not isinstance(reviews, list) or not reviews:
        raise RuntimeError("Story check: Claude returned no reviews. Retry the story check.")
    return reviews
