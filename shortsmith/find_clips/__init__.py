"""Step 2: clip selection. Backend-dispatched.

The public API is `find_clips(words, out_path, cfg)`. Which backend gets called
depends on `cfg.clip_engine`:

- `"anthropic"` (default) — Claude API. Best quality. See `anthropic.py`.
- `"ollama"` — local OpenAI-compatible endpoint (Ollama / LM Studio / vLLM).
  Experimental. See `ollama.py`.

Backends are responsible only for "send the prompt, return parsed JSON list".
All transcript formatting, JSON parsing tolerance, validation, score filtering,
and clips.json writing live in this dispatcher + `_common.py` so the two
backends produce indistinguishable output schemas.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import Config
from ._common import normalize_clips

log = logging.getLogger(__name__)


def find_clips(words: list[dict], out_path: Path, cfg: Config) -> list[dict]:
    """Pick viral evergreen clips via the configured backend, write clips.json."""
    engine = cfg.clip_engine.lower()

    if engine == "anthropic":
        from . import anthropic as backend
    elif engine in ("ollama", "openai-compatible", "local"):
        from . import ollama as backend
    else:
        raise ValueError(
            f"Unknown clip engine {engine!r}. Valid: anthropic, ollama."
        )

    raw_clips = backend.call(words, cfg)
    clips = normalize_clips(raw_clips, words)

    raw_count = len(clips)
    clips = [c for c in clips if c["viral_score"] >= cfg.min_viral_score]
    log.info("Viral-score filter (>=%d): %d kept, %d dropped",
             cfg.min_viral_score, len(clips), raw_count - len(clips))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(clips, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Wrote %d clips to %s", len(clips), out_path)
    return clips
