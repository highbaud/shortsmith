"""Run shortsmith pipeline (--from-step 3) on every work dir that has
clips.json but no cut_manifests.json yet. Use this after the clip-picking
phase to chew through all the freshly-picked work dirs.

After pipeline finishes for each video, kicks off Hyperframes render too.
Pending attempts remain eligible until the pipeline and renders succeed.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT, VIDEO_DIR
from shortsmith.rendering.batch import load_picks, run_batch

log = logging.getLogger("batch")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"
PENDING_MARKER = ".batch_pipeline_pending"


def new_work_dirs() -> tuple[list[Path], list[str]]:
    """Newly picked work dirs and unfinished pipeline/render attempts."""
    out = []
    failed = []
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        pending = (wd / PENDING_MARKER).exists()
        clips_path = wd / "clips.json"
        if not pending and not (clips_path.exists() or clips_path.is_symlink()):
            continue
        if not pending and (wd / "cut_manifests.json").exists():
            continue
        try:
            clips = load_picks(clips_path)
            if not clips:
                log.info("Skipping %s: empty clips.json (0 clips picked)", wd.name)
                continue
        except (OSError, ValueError) as exc:
            log.error("Cannot load batch state from %s: %s", clips_path, exc)
            failed.append(wd.name)
            continue
        out.append(wd)
    return out, failed


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    work_dirs, state_fail = new_work_dirs()
    log.info("Found %d new work dirs to pipeline+render", len(work_dirs))

    pipeline_fail, render_fail, _done = run_batch(
        work_dirs, video_dir=VIDEO_DIR, output_root=AUTO_SHORTS_ROOT, kit_root=KIT_ROOT,
        project_root=SHORTSMITH_ROOT, from_step=3, logger=log,
        pending_marker=PENDING_MARKER,
    )
    pipeline_fail = state_fail + pipeline_fail

    log.info("DONE. pipeline_fail=%d render_fail=%d", len(pipeline_fail), len(render_fail))
    if pipeline_fail:
        for f in pipeline_fail[:20]:
            log.error("  pipeline fail: %s", f)
    if render_fail:
        for f in render_fail[:20]:
            log.error("  render fail: %s", f)
    return 0 if not (pipeline_fail or render_fail) else 1


if __name__ == "__main__":
    sys.exit(main())
