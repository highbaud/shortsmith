"""Ingest already-finished vertical shorts (1080x1920, already cut + cropped) into
the shortsmith finishing pipeline.

These clips skip find-clips / cut / clean / enhance / reframe. We only need a
per-clip word transcript (for captions + SFX timing) and a manifest that points
the scaffold step at each finished clip as its base video. After this runs:

  1. author work/<slug>/clips.json (hook + callouts + caption per rank)
  2. uv run python -c "scaffold..."  (or run scaffold via the helper below)
  3. base-render the projects
  4. scripts/finalize.py

Usage:
    uv run python scripts/ingest_premade.py <slug> <file1.mp4> <file2.mp4> ...
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

from shortsmith import config, names, transcribe
from shortsmith._files import atomic_write

log = logging.getLogger("ingest")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")

    if len(sys.argv) < 3:
        print("usage: ingest_premade.py <slug> <file1> [file2 ...]")
        return 1
    slug = sys.argv[1]
    files = [Path(p) for p in sys.argv[2:]]

    cfg = config.Config()
    work_dir = Path(__file__).resolve().parent.parent / "work" / slug
    (work_dir / "premade").mkdir(parents=True, exist_ok=True)
    (work_dir / "words").mkdir(parents=True, exist_ok=True)

    manifests: list[dict] = []
    full_transcript: list[dict] = []
    for i, src in enumerate(files, 1):
        if not src.exists():
            log.error("missing: %s", src)
            return 1
        clip_dst = work_dir / "premade" / f"short-{i:02d}.mp4"
        shutil.copy(src, clip_dst)
        words_path = work_dir / "words" / f"short-{i:02d}.json"
        log.info("[%d/%d] transcribing %s", i, len(files), src.name)
        words = transcribe.transcribe(clip_dst, words_path, cfg, reuse_existing=True)
        manifests.append({
            "rank": i,
            "raw_path": str(clip_dst),
            "vertical_path": str(clip_dst),   # already 1080x1920 — scaffold uses this as the base
            "words_path": str(words_path),
            "source_file": src.name,
        })
        # Stash a readable per-clip transcript so the author step has the text.
        full_transcript.append({
            "rank": i,
            "source_file": src.name,
            "text": " ".join(names.word_text(w) for w in words),
            "duration": words[-1]["end"] if words else 0.0,
        })

    atomic_write(work_dir / "cut_manifests.json",
                 json.dumps(manifests, indent=2, ensure_ascii=False))
    atomic_write(work_dir / "_premade_transcripts.json",
                 json.dumps(full_transcript, indent=2, ensure_ascii=False))
    log.info("Ingested %d clips -> %s", len(manifests), work_dir)
    log.info("Next: author %s/clips.json, then scaffold + render + finalize", work_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
