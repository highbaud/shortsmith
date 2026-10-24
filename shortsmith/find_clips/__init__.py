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

import hashlib
import json
import logging
from pathlib import Path

from .. import story_review
from ..clean_clips import plan_cleaning
from ..config import Config
from ..cut_clips import plan_segments
from ._common import normalize_clips

log = logging.getLogger(__name__)


def _backend(cfg: Config):
    engine = cfg.clip_engine.lower()

    if engine == "anthropic":
        from . import anthropic as backend
    elif engine in ("ollama", "openai-compatible", "local"):
        from . import ollama as backend
    else:
        raise ValueError(
            f"Unknown clip engine {engine!r}. Valid: anthropic, ollama."
        )

    return backend


def check_stories(
    clips: list[dict], words: list[dict], report_path: Path, cfg: Config,
) -> list[dict]:
    """Review planned cuts, save all decisions, and return only passing clips."""
    if not isinstance(clips, list) or not all(isinstance(clip, dict) for clip in clips):
        raise ValueError("Story check requires a JSON array of clip objects.")
    # Validate requested cuts before planning them. Review the actual boundary
    # snaps, in playback order, using the same planner as the cutter.
    story_review.build_review_input(clips, words)
    snapped = [plan_segments(clip, words, cfg) for clip in clips]
    planned = [
        {**clip, "segments": plan_cleaning(words, segments, cfg)["retained_segments"]}
        for clip, segments in zip(clips, snapped, strict=True)
    ]
    user_input = story_review.build_review_input(planned, words)
    backend = _backend(cfg)
    if planned:
        log.info("Story check: reviewing %d planned clips via %s", len(planned), cfg.clip_engine)
        try:
            raw_reviews = backend.review(story_review.SYSTEM_PROMPT, user_input, cfg)
        except Exception as exc:
            raise RuntimeError(
                "Story check failed; clips were not replaced. "
                "Retry the check, or use --no-story-check to skip it. "
                f"Provider error: {exc}"
            ) from exc
        reviews = story_review.validate_reviews(raw_reviews, planned, words)
    else:
        reviews = []

    approved = []
    report_clips = []
    counts = {"pass": 0, "reject": 0, "needs_review": 0}
    source_digest = story_review.source_digest(words)
    for clip, planned_clip, segments, review in zip(clips, planned, snapped, reviews, strict=True):
        review = {**review, "reviewed_segments": segments,
                  "reviewed_seam_xfade_seconds": cfg.seam_xfade_seconds,
                  "reviewed_speech_segments": planned_clip["segments"],
                  "retained_speech_digest": story_review.retained_speech_digest(
                      words, planned_clip["segments"]),
                  "source_digest": source_digest}
        checked = {**clip, "story_review": review}
        counts[review["decision"]] += 1
        report_clips.append(checked)
        if review["decision"] == "pass":
            approved.append(checked)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_text = json.dumps({
        "schema_version": 1,
        "source_digest": source_digest,
        "history_directory": "story_review_history",
        "engine": cfg.clip_engine,
        "model": cfg.claude_model if cfg.clip_engine == "anthropic" else cfg.local_llm_model,
        "summary": counts,
        "clips": report_clips,
    }, indent=2)
    if report_path.exists():
        previous = report_path.read_bytes()
        if previous != report_text.encode("utf-8"):
            history = report_path.parent / "story_review_history"
            history.mkdir(exist_ok=True)
            archived = history / f"{hashlib.sha256(previous).hexdigest()}.json"
            if not archived.exists():
                archived.write_bytes(previous)
    report_path.write_bytes(report_text.encode("utf-8"))
    log.info("Story check: %d passed, %d rejected, %d need review. Report: %s",
             counts["pass"], counts["reject"], counts["needs_review"], report_path)
    return approved


def find_clips(words: list[dict], out_path: Path, cfg: Config) -> list[dict]:
    """Pick clips, check their stories when enabled, and write clips.json."""
    raw_clips = _backend(cfg).call(words, cfg)
    clips = normalize_clips(raw_clips, words)

    raw_count = len(clips)
    clips = [c for c in clips if c["viral_score"] >= cfg.min_viral_score]
    log.info("Viral-score filter (>=%d): %d kept, %d dropped",
             cfg.min_viral_score, len(clips), raw_count - len(clips))

    if cfg.story_check:
        clips = check_stories(clips, words, out_path.with_name("story_review.json"), cfg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(clips, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Wrote %d clips to %s", len(clips), out_path)
    return clips
