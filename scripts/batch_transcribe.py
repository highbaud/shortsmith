"""Batch-transcribe every source video in SHORTSMITH_VIDEO_DIR that doesn't
already have a transcript.json in its work dir.

Loads faster-whisper once and reuses the model across all videos.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from slugify import slugify

from shortsmith._files import atomic_write
from shortsmith.config import VIDEO_DIR, VIDEO_EXTENSIONS, Config, make_work_dir

log = logging.getLogger("batch_transcribe")


def find_pending() -> list[Path]:
    videos = sorted(
        p for p in VIDEO_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    pending = []
    for v in videos:
        slug = slugify(v.stem)[:60]
        wd = Path(__file__).resolve().parent.parent / "work" / slug
        transcript = wd / "transcript.json"
        if transcript.exists():
            try:
                data = json.loads(transcript.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) > 100:
                    continue
            except (OSError, ValueError) as exc:
                log.warning("Cannot reuse transcript %s: %s; transcribing again", transcript, exc)
        pending.append(v)
    return pending


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not VIDEO_DIR.is_dir():
        log.error("Video dir not found: %s. Set SHORTSMITH_VIDEO_DIR.", VIDEO_DIR)
        return 1
    cfg = Config()
    pending = find_pending()
    log.info("Found %d pending videos to transcribe", len(pending))
    if not pending:
        return 0

    from faster_whisper import WhisperModel

    from shortsmith.transcribe import segments_to_words

    log.info("Loading faster-whisper model=%s device=%s compute=%s",
             cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute_type)
    model = WhisperModel(
        cfg.whisper_model,
        device=cfg.whisper_device,
        compute_type=cfg.whisper_compute_type,
    )

    succeeded = failed = 0
    for i, video in enumerate(pending, 1):
        log.info("[%d/%d] Transcribing %s", i, len(pending), video.name)
        wd = None
        try:
            wd = make_work_dir(video)
            transcript = wd / "transcript.json"
            segments, info = model.transcribe(
                str(video),
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 250},
            )
            words = segments_to_words(segments)
            atomic_write(transcript, json.dumps(words, indent=2, ensure_ascii=False))
            succeeded += 1
            log.info("[%d/%d] DONE %d words -> %s", i, len(pending), len(words), transcript)
        except Exception as e:
            failed += 1
            log.exception("[%d/%d] FAILED %s: %s", i, len(pending), video.name, e)
            if wd is not None:
                try:
                    atomic_write(wd / "transcribe_failed.txt", str(e))
                except OSError as marker_error:
                    log.error("Cannot record transcription failure in %s: %s", wd, marker_error)

    log.info("Batch complete: succeeded=%d failed=%d", succeeded, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
