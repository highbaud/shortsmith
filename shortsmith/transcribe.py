"""Step 1 & 6: faster-whisper transcription with word-level timestamps.

Word edges are then trimmed off measured pauses (see word_pauses).

Output schema (a flat list of word entries):
    [{"text": str, "start": float_sec, "end": float_sec}, ...]
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from . import names, word_pauses
from ._files import atomic_write
from .config import Config
from .records import TranscriptWord, require_words

if TYPE_CHECKING:
    from faster_whisper.transcribe import Segment as WhisperSegment

log = logging.getLogger(__name__)


def segments_to_words(segments: Iterable[WhisperSegment]) -> list[TranscriptWord]:
    """Use the same word text and timestamp precision for every Whisper producer."""
    return [
        {"text": word.word.strip(), "start": round(word.start, 3), "end": round(word.end, 3)}
        for segment in segments for word in segment.words or []
    ]


def transcribe(
    video_path: Path,
    out_path: Path,
    cfg: Config,
    *,
    reuse_existing: bool = True,
) -> list[TranscriptWord]:
    """Transcribe a video to a flat list of word entries.

    If `reuse_existing` and a sibling `transcript-<stem>.json` exists in the
    same directory as the source video, reuse it instead of running Whisper.
    """
    words: list[TranscriptWord]
    if reuse_existing:
        # Convention 1: transcript-<slug>.json next to source video
        # Look for any transcript-*.json that matches a slug substring of the video name
        sib_dir = video_path.parent
        stem_lower = video_path.stem.lower()
        # Several videos can share a directory, and a short slug is a substring
        # of a longer one ("xrp" matches "xrp-deaton.mp4" too). Take the LONGEST
        # matching tag so the choice is the most specific transcript rather than
        # whichever one the directory happened to list first.
        matches: list[tuple[int, Path]] = []
        for candidate in sib_dir.glob("transcript-*.json"):
            tag = candidate.stem.removeprefix("transcript-").lower()
            if tag and tag in stem_lower:
                matches.append((len(tag), candidate))
        if matches:
            candidate = max(matches)[1]
            log.info("Reusing existing transcript %s", candidate)
            words = names.fix_words(json.loads(candidate.read_text(encoding="utf-8")))
            words = _repair_pauses(video_path, words)
            _write_words(out_path, words)
            return words

    # Lazy import: faster-whisper is heavy
    from faster_whisper import WhisperModel

    log.info("Loading faster-whisper model=%s device=%s compute=%s",
             cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute_type)
    try:
        model = WhisperModel(
            cfg.whisper_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute_type,
        )

        log.info("Transcribing %s", video_path)
        segments, info = model.transcribe(
            str(video_path),
            word_timestamps=True,
            initial_prompt=cfg.whisper_initial_prompt or None,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 250},
        )

        words = segments_to_words(segments)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(_whisper_error_hint(e, cfg)) from e

    words = _repair_pauses(video_path, names.fix_words(words))
    _write_words(out_path, words)
    log.info("Wrote %d words to %s", len(words), out_path)
    return words


def _whisper_error_hint(e: Exception, cfg: Config) -> str:
    """Turn a raw Whisper/CUDA failure into an actionable message."""
    msg = str(e).lower()
    if "out of memory" in msg or "cuda" in msg and "memory" in msg:
        return (
            f"Whisper ran out of GPU memory on model '{cfg.whisper_model}'. "
            "Try a smaller model: set SHORTSMITH_WHISPER_MODEL=medium (or small), "
            "or reduce VRAM pressure by closing other GPU apps. "
            f"Original error: {e}"
        )
    if "cuda" in msg or "no kernel image" in msg or "device" in msg:
        return (
            "Whisper failed to use CUDA. Confirm the GPU build of torch/ctranslate2 "
            "is installed, or fall back to CPU with SHORTSMITH_WHISPER_DEVICE=cpu "
            "(slow). "
            f"Original error: {e}"
        )
    if "compute" in msg or "float16" in msg:
        return (
            "Whisper compute type unsupported on this device. Try "
            "SHORTSMITH_WHISPER_COMPUTE=int8 (or float32). "
            f"Original error: {e}"
        )
    return f"Whisper transcription failed: {e}"


def _repair_pauses(media_path: Path, words: list[TranscriptWord]) -> list[TranscriptWord]:
    """Trim each word off the measured pauses Whisper stretched it across."""
    measured = word_pauses.measure_levels(media_path)
    if measured is None:
        return words
    fixed = word_pauses.repair_words(words, *measured)
    moved = sum(a is not b for a, b in zip(words, fixed, strict=True))
    log.info("Word pauses: trimmed %d of %d words to their sound", moved, len(words))
    return fixed


def _write_words(out_path: Path, words: list[TranscriptWord]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(out_path, json.dumps(words, indent=2, ensure_ascii=False))


def load_words(path: Path) -> list[TranscriptWord]:
    return require_words(json.loads(Path(path).read_text(encoding="utf-8")), path)
