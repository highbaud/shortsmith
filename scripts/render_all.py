"""Render current selected Hyperframes projects under auto-shorts/.

Runs the pinned Hyperframes CLI once per project.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT, REPO_ROOT
from shortsmith.rendering.hyperframes import render_project
from shortsmith.rendering.project_selection import select_projects

log = logging.getLogger("render")
WORK_ROOT = REPO_ROOT / "work"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not AUTO_SHORTS_ROOT.exists():
        log.error("auto-shorts root not found: %s", AUTO_SHORTS_ROOT)
        return 1

    projects: list[Path] = []
    failed: list[str] = []
    for src_dir in sorted(AUTO_SHORTS_ROOT.iterdir()):
        if not src_dir.is_dir():
            continue
        try:
            projects.extend(select_projects(src_dir, WORK_ROOT))
        except ValueError as exc:
            log.error("FAIL project selection %s: %s", src_dir.name, exc)
            failed.append(src_dir.name)

    log.info("Found %d projects to render", len(projects))

    for i, proj in enumerate(projects, 1):
        log.info("[%d/%d] %s/%s", i, len(projects),
                 proj.parent.name[:32], proj.name)
        t0 = time.time()
        ok = render_project(proj, KIT_ROOT, log)
        log.info("[%d/%d] done in %.0fs (ok=%s)", i, len(projects), time.time() - t0, ok)
        if not ok:
            failed.append(f"{proj.parent.name}/{proj.name}")

    if failed:
        log.error("FAILED: %d projects", len(failed))
        for f in failed[:20]:
            log.error("  - %s", f)
        return 1

    log.info("All %d renders complete.", len(projects))
    return 0


if __name__ == "__main__":
    sys.exit(main())
