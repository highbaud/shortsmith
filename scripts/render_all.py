"""Render every scaffolded Hyperframes project under auto-shorts/.

Standalone script — runs `npx hyperframes render` per project, on Windows
via cmd.exe so npx resolves correctly.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import sys
import time
from pathlib import Path

from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("render")


def render_project(project_dir: Path) -> bool:
    """Invoke `npx hyperframes render <project>` via cmd.exe."""
    # Use a single command string + shell=True so the Windows shell finds npx.
    # Quote the project path for safety.
    rel = project_dir.relative_to(KIT_ROOT)
    cmd_str = f'npx hyperframes render "{rel.as_posix()}"'
    log.info("$ %s", cmd_str)
    proc = subprocess.run(
        cmd_str,
        cwd=str(KIT_ROOT),
        shell=True,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        log.error("render failed for %s (exit=%d)\nstderr=%s",
                  project_dir.name, proc.returncode, (proc.stderr or "")[-800:])
        return False
    # Confirm output exists
    final = project_dir / "renders" / "final.mp4"
    if final.exists():
        size_mb = final.stat().st_size / (1024 * 1024)
        log.info("    -> %s (%.1f MB)", final.relative_to(KIT_ROOT), size_mb)
    return True


def main() -> int:
    if not AUTO_SHORTS_ROOT.exists():
        log.error("auto-shorts root not found: %s", AUTO_SHORTS_ROOT)
        return 1

    projects: list[Path] = []
    for src_dir in sorted(AUTO_SHORTS_ROOT.iterdir()):
        if not src_dir.is_dir():
            continue
        for proj in sorted(src_dir.glob("short-*")):
            if not proj.is_dir():
                continue
            projects.append(proj)

    log.info("Found %d projects to render", len(projects))

    failed: list[str] = []
    for i, proj in enumerate(projects, 1):
        log.info("[%d/%d] %s/%s", i, len(projects),
                 proj.parent.name[:32], proj.name)
        t0 = time.time()
        ok = render_project(proj)
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
