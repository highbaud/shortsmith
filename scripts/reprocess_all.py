"""Comprehensive re-process of EVERY source video with the upgraded pipeline.

Re-runs `shortsmith run <video> --from-step 3` (re-cut with asymmetric boundary
snap, re-clean with stutter repair + lighter filler list, re-enhance + -14 LUFS
loudnorm, WhisperX forced alignment, reframe, scaffold) and re-renders every
short.

Resumable at the VIDEO level: a work dir that finishes fully gets a
`.reprocessed_v2` marker; re-running skips it. Delete the marker (or pass
--force) to redo a video.

Usage:
    uv run python reprocess_all.py            # process all not-yet-done videos
    uv run python reprocess_all.py --force    # redo everything from scratch
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from shortsmith._files import atomic_write
from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT, VIDEO_DIR
from shortsmith.rendering.batch import load_picks, run_batch

log = logging.getLogger("reprocess")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"
MARKER = ".reprocessed_v2"


def candidate_work_dirs(force: bool) -> tuple[list[Path], list[str]]:
    out = []
    failed = []
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        if not force and (wd / MARKER).exists():
            continue
        clips_path = wd / "clips.json"
        if not any(p.exists() or p.is_symlink() for p in (clips_path, wd / "cut_manifests.json")):
            continue
        try:
            clips = load_picks(clips_path)
        except (OSError, ValueError) as exc:
            log.error("Cannot load reprocess state for %s: %s", wd, exc)
            failed.append(wd.name)
            continue
        if not clips:
            continue
        out.append(wd)
    return out, failed


def mark_reprocessed(work_dir: Path) -> None:
    atomic_write(work_dir / MARKER, time.strftime("%Y-%m-%d %H:%M:%S"))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    force = "--force" in sys.argv
    if not WORK_ROOT.is_dir():
        log.error("No work dir yet: %s. Run the pipeline first.", WORK_ROOT)
        return 1
    if not VIDEO_DIR.is_dir():
        log.error("Video dir not found: %s. Set SHORTSMITH_VIDEO_DIR.", VIDEO_DIR)
        return 1
    candidates, pfail = candidate_work_dirs(force)
    log.info("Re-processing %d videos (force=%s)", len(candidates), force)
    pipeline_fail, rfail, done = run_batch(
        candidates, video_dir=VIDEO_DIR, output_root=AUTO_SHORTS_ROOT, kit_root=KIT_ROOT,
        project_root=SHORTSMITH_ROOT, from_step=3, logger=log, on_success=mark_reprocessed,
    )
    pfail.extend(pipeline_fail)

    log.info("DONE. fully_done=%d pipeline_fail=%d render_fail=%d", done, len(pfail), len(rfail))
    for f in pfail[:30]:
        log.error("  pipeline fail: %s", f)
    for f in rfail[:30]:
        log.error("  render fail: %s", f)
    return 0 if not (pfail or rfail) else 1


if __name__ == "__main__":
    sys.exit(main())
