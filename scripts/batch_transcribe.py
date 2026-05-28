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

from shortsmith.config import VIDEO_DIR, Config, make_work_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("batch_transcribe")

EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}


def find_pending() -> list[Path]:
    videos = sorted(
        p for p in VIDEO_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONS
    )
    pending = []
    for v in videos:
        slug = slugify(v.stem)[:60]
        wd = Path(__file__).resolve().parent.parent / "work" / slug
        transcript = wd / "transcript.json"
        if transcript.exists():
            try:
                # sanity check: ensure non-empty
                data = json.loads(transcript.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) > 100:
                    continue
            except Exception:
                pass
        pending.append(v)
    return pending


def main() -> int:
    cfg = Config()
    pending = find_pending()
    log.info("Found %d pending videos to transcribe", len(pending))
    if not pending:
        return 0

    # Lazy import
    from faster_whisper import WhisperModel

    log.info("Loading faster-whisper model=%s device=%s compute=%s",
             cfg.whisper_model, cfg.whisper_device, cfg.whisper_compute_type)
    model = WhisperModel(
        cfg.whisper_model,
        device=cfg.whisper_device,
        compute_type=cfg.whisper_compute_type,
    )

    for i, video in enumerate(pending, 1):
        log.info("[%d/%d] Transcribing %s", i, len(pending), video.name)
        wd = make_work_dir(video)
        transcript = wd / "transcript.json"
        try:
            segments, info = model.transcribe(
                str(video),
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
            transcript.write_text(
                json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            log.info("[%d/%d] DONE %d words -> %s", i, len(pending), len(words), transcript)
        except Exception as e:
            log.exception("[%d/%d] FAILED %s: %s", i, len(pending), video.name, e)
            # Write a marker so we don't retry next run
            (wd / "transcribe_failed.txt").write_text(str(e), encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
