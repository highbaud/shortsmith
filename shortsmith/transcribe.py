"""Step 1 & 6: faster-whisper transcription with word-level timestamps.

Output schema (a flat list of word entries):
    [{"text": str, "start": float_sec, "end": float_sec}, ...]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)


def transcribe(
    video_path: Path,
    out_path: Path,
    cfg: Config,
    *,
    reuse_existing: bool = True,
) -> list[dict]:
    """Transcribe a video to a flat list of word entries.

    If `reuse_existing` and a sibling `transcript-<stem>.json` exists in the
    same directory as the source video, reuse it instead of running Whisper.
    """
    if reuse_existing:
        # Convention 1: transcript-<slug>.json next to source video
        # Look for any transcript-*.json that matches a slug substring of the video name
        sib_dir = video_path.parent
        stem_lower = video_path.stem.lower()
        for candidate in sib_dir.glob("transcript-*.json"):
            tag = candidate.stem.replace("transcript-", "").lower()
            if tag and tag in stem_lower:
                log.info("Reusing existing transcript %s", candidate)
                words = json.loads(candidate.read_text(encoding="utf-8"))
                _write_words(out_path, words)
                return words

    # Lazy import: faster-whisper is heavy
    from faster_whisper import WhisperModel

    log.info("Loading faster-whisper model=%s device=%s compute=%s",
             cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute_type)
    model = WhisperModel(
        cfg.whisper_model,
        device=cfg.whisper_device,
        compute_type=cfg.whisper_compute_type,
    )

    log.info("Transcribing %s", video_path)
    segments, info = model.transcribe(
        str(video_path),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 250},
    )

    words: list[dict] = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            words.append({
                "text": w.word.strip(),
                "start": round(w.start, 3),
                "end": round(w.end, 3),
            })

    _write_words(out_path, words)
    log.info("Wrote %d words to %s", len(words), out_path)
    return words


def _write_words(out_path: Path, words: list[dict]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8")


def load_words(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
