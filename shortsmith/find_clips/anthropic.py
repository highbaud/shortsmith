"""Anthropic Claude backend for clip finding.

Default backend. Best quality — Opus 4.7 is exceptional at the topical-clarity
and evergreen-filter gates. Costs ~$0.10-$2.00 per source video depending on
transcript length.
"""
from __future__ import annotations

import logging

from ..config import Config
from ._common import format_transcript, load_system_prompt, parse_json_response

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

    transcript_text = format_transcript(words)
    system_prompt = load_system_prompt()

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    log.info("Calling Claude (%s) to rank clips...", cfg.claude_model)
    resp = client.messages.create(
        model=cfg.claude_model,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": transcript_text}],
    )

    raw = resp.content[0].text  # type: ignore[attr-defined]
    return parse_json_response(raw)
