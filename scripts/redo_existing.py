"""Re-run --from-step 5 on every existing work dir that already has clips.json,
then render its current selected Hyperframes projects.

Use this after a pipeline upgrade (new audio engine, new reframe logic, etc.)
to bring all existing shorts up to the latest standard.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT, VIDEO_DIR
from shortsmith.rendering.batch import run_batch

log = logging.getLogger("redo")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"


def all_existing_work_dirs() -> list[Path]:
    """Work dirs that have a clips.json AND a cut_manifests.json, meaning
    the pipeline already ran past step 3 on them."""
    out = []
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        if not (wd / "clips.json").exists():
            continue
        if not (wd / "cut_manifests.json").exists():
            continue
        out.append(wd)
    return out


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    work_dirs = all_existing_work_dirs()
    log.info("Found %d work dirs with clips.json + cut_manifests.json", len(work_dirs))

    failures, render_failures, _done = run_batch(
        work_dirs, video_dir=VIDEO_DIR, output_root=AUTO_SHORTS_ROOT, kit_root=KIT_ROOT,
        project_root=SHORTSMITH_ROOT, from_step=5, logger=log,
    )

    if failures:
        log.error("Pipeline failures: %s", failures)
    if render_failures:
        log.error("Render failures: %s", render_failures)
    return 1 if failures or render_failures else 0


if __name__ == "__main__":
    sys.exit(main())
