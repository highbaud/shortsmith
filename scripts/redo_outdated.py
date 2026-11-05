"""Re-process every work dir whose cut_manifests.json predates the
cut/clean/filler-list fix landed at FIX_CUTOFF_EPOCH.

For each outdated work dir we:
1. Record a pending marker and move the old cut_manifests.json aside (.bak).
2. Invoke `uv run shortsmith run <video> --from-step 3` to re-cut +
   re-clean + re-enhance + re-retranscribe + re-reframe + re-scaffold.
3. Re-render current selected projects under auto-shorts/<slug>/.

The pending marker keeps failed attempts eligible, even after the pipeline
writes a new manifest. Clear it only after the pipeline and renders succeed.

Use after batch_pipeline_new.py has finished, OR sequentially queued
once that batch completes.
"""
from __future__ import annotations

import logging
import shutil
import sys
import time
from pathlib import Path

from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT, VIDEO_DIR
from shortsmith.rendering.batch import load_picks, run_batch

log = logging.getLogger("redo")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"

# Anything cut BEFORE this epoch used the old (chopped-thought,
# filler-mauling, tight-silence-margin) code path. Bump if more fixes
# land later.
FIX_CUTOFF_EPOCH = 1780002924
PENDING_MARKER = ".redo_outdated_pending"


def outdated_work_dirs() -> tuple[list[Path], list[str]]:
    """Work dirs with old manifests or an unfinished prior attempt."""
    out = []
    failed = []
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        pending = (wd / PENDING_MARKER).exists()
        cm = wd / "cut_manifests.json"
        if not pending and (not cm.exists() or cm.stat().st_mtime >= FIX_CUTOFF_EPOCH):
            continue
        clips_path = wd / "clips.json"
        try:
            clips = load_picks(clips_path)
        except (OSError, ValueError) as exc:
            log.error("Cannot load outdated state from %s: %s", clips_path, exc)
            failed.append(wd.name)
            continue
        if clips:
            out.append(wd)
    return out, failed


def archive_old_outputs(wd: Path) -> None:
    """Move the stale manifest aside after the driver records pending work."""
    cm = wd / "cut_manifests.json"
    if cm.exists() and cm.stat().st_mtime < FIX_CUTOFF_EPOCH:
        bak = wd / f"cut_manifests.json.bak.{int(time.time())}"
        shutil.move(str(cm), str(bak))
        log.debug("archived %s -> %s", cm.name, bak.name)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    work_dirs, state_fail = outdated_work_dirs()
    log.info("Found %d outdated work dirs (cut_manifests < %d)",
             len(work_dirs), FIX_CUTOFF_EPOCH)

    pipeline_fail, render_fail, _done = run_batch(
        work_dirs, video_dir=VIDEO_DIR, output_root=AUTO_SHORTS_ROOT, kit_root=KIT_ROOT,
        project_root=SHORTSMITH_ROOT, from_step=3, logger=log,
        before_pipeline=archive_old_outputs, pending_marker=PENDING_MARKER,
    )
    pipeline_fail = state_fail + pipeline_fail

    log.info("DONE. pipeline_fail=%d render_fail=%d", len(pipeline_fail), len(render_fail))
    return 0 if not (pipeline_fail or render_fail) else 1


if __name__ == "__main__":
    sys.exit(main())
