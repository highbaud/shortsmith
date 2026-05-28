"""Ollama (and OpenAI-compatible) local-LLM backend for clip finding.

EXPERIMENTAL — local models produce lower-quality picks than Claude Opus.
Expect more retries, more rejections, and more abstract / mood-piece clips
slipping through the topical-clarity gate. Test on a few sources before
committing to a 100-video batch.

Works against any OpenAI-compatible endpoint:
  - Ollama:    `ollama serve` then point at http://localhost:11434/v1
  - LM Studio: enable the local server, point at http://localhost:1234/v1
  - vLLM:      `vllm serve <model>` then point at http://localhost:8000/v1

Recommended models (quality / VRAM trade-offs):
  - llama3.1:70b    — best local quality, ~48 GB VRAM
  - qwen2.5:72b     — strong reasoning, similar VRAM
  - mistral-large   — good balance
  - llama3.1:8b     — smallest viable, expect noticeably worse picks
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from ..config import Config
from ._common import format_transcript, load_system_prompt, parse_json_response

log = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3


def call(words: list[dict], cfg: Config) -> list[dict]:
    """Send transcript to a local OpenAI-compatible endpoint. Retries on bad JSON."""
    base_url = cfg.local_llm_url.rstrip("/")
    model = cfg.local_llm_model

    log.warning(
        "ollama: local-LLM clip selection is EXPERIMENTAL. Expected quality is "
        "noticeably below Claude Opus. Spot-check the first few picks."
    )
    log.info("ollama: calling %s with model=%s", base_url, model)

    transcript_text = format_transcript(words)
    system_prompt = load_system_prompt()

    # Append a JSON-only reinforcement — local models often add prose otherwise.
    user_msg = (
        transcript_text
        + "\n\nReturn ONLY the JSON array. No prose, no markdown fences. "
        "Start with [ and end with ]."
    )

    last_err: Exception | None = None
    for attempt in range(1, DEFAULT_MAX_RETRIES + 1):
        try:
            raw = _post_chat(base_url, model, system_prompt, user_msg,
                             temperature=cfg.local_llm_temperature)
            return parse_json_response(raw)
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("ollama: bad JSON on attempt %d/%d: %s",
                        attempt, DEFAULT_MAX_RETRIES, e)
            last_err = e
            # Lower temperature on each retry to discourage prose
            cfg = _with_lower_temp(cfg)
            time.sleep(1.0)
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"ollama: failed to reach {base_url}. Is the server running? "
                f"Original error: {e}"
            ) from e

    raise RuntimeError(
        f"ollama: model returned unparseable JSON after {DEFAULT_MAX_RETRIES} "
        f"attempts. Last error: {last_err}. Try a larger model "
        f"(SHORTSMITH_LOCAL_LLM_MODEL=llama3.1:70b) or fall back to "
        f"--clip-engine anthropic."
    )


def _post_chat(base_url: str, model: str, system: str, user: str,
               temperature: float) -> str:
    """Minimal OpenAI-compatible chat completion using stdlib urllib."""
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:  # 10 min cap
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _with_lower_temp(cfg: Config) -> Config:
    """Return a config copy with lower temperature for retry."""
    from dataclasses import replace
    new_t = max(0.0, cfg.local_llm_temperature - 0.2)
    return replace(cfg, local_llm_temperature=new_t)
