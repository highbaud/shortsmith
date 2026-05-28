"""Re-run --from-step 5 on every existing work dir that already has clips.json,
then render every scaffolded Hyperframes project.

Use this after a pipeline upgrade (new audio engine, new reframe logic, etc.)
to bring all existing shorts up to the latest standard.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import sys
import time
from pathlib import Path

from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT, VIDEO_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("redo")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"


def find_source_video_for(work_dir: Path) -> Path | None:
    """Given a work dir, find the source video file it was derived from.

    The slug is slugify(video.stem)[:60]. We have no inverse, so iterate
    the video directory.
    """
    from slugify import slugify
    for v in VIDEO_DIR.iterdir():
        if not v.is_file() or v.suffix.lower() not in (".mp4", ".mkv", ".webm", ".mov"):
            continue
        slug = slugify(v.stem)[:60]
        if slug == work_dir.name:
            return v
    return None


def all_existing_work_dirs() -> list[Path]:
    """Work dirs that have a clips.json AND a cut_manifests.json — meaning
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


def redo_pipeline(video: Path) -> bool:
    """Re-run shortsmith with --from-step 5 on a source video."""
    log.info("=== redo pipeline: %s ===", video.name)
    cmd = [
        "uv", "run", "shortsmith", "run",
        str(video), "--from-step", "5",
    ]
    log.info("$ %s", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, cwd=str(SHORTSMITH_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("FAILED pipeline for %s\nstderr=%s", video.name, proc.stderr[-2000:])
        return False
    return True


def render_project(project_dir: Path) -> bool:
    """Run `npx hyperframes render` on a scaffolded project, output to its renders/."""
    if not project_dir.is_dir():
        return False
    cmd = [
        "npx", "hyperframes", "render", str(project_dir),
        "--output", str(project_dir / "renders" / "final.mp4"),
    ]
    log.info("$ %s", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, cwd=str(KIT_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        log.warning("render exit=%d for %s; trying without --output", proc.returncode, project_dir.name)
        # Some hyperframes versions don't support --output; fall back.
        cmd2 = ["npx", "hyperframes", "render", str(project_dir)]
        proc = subprocess.run(cmd2, cwd=str(KIT_ROOT), capture_output=True, text=True)
        if proc.returncode != 0:
            log.error("FAILED render for %s\nstderr=%s", project_dir.name, proc.stderr[-1000:])
            return False
    return True


def main() -> int:
    work_dirs = all_existing_work_dirs()
    log.info("Found %d work dirs with clips.json + cut_manifests.json", len(work_dirs))

    todo = []
    for wd in work_dirs:
        v = find_source_video_for(wd)
        if v is None:
            log.warning("Cannot find source video for %s", wd.name)
            continue
        todo.append((wd, v))

    log.info("Will redo %d work dirs", len(todo))

    failures = []
    for i, (_wd, v) in enumerate(todo, 1):
        log.info("[%d/%d] %s", i, len(todo), v.name)
        t0 = time.time()
        ok = redo_pipeline(v)
        log.info("[%d/%d] pipeline done in %.0fs (ok=%s)", i, len(todo), time.time() - t0, ok)
        if not ok:
            failures.append(v.name)
            continue

    # Render every short under auto-shorts/<source-slug>/short-*/
    log.info("Rendering all scaffolded projects ...")
    for wd, _v in todo:
        out_dir = AUTO_SHORTS_ROOT / wd.name
        if not out_dir.exists():
            log.warning("No auto-shorts output dir for %s", wd.name)
            continue
        for proj in sorted(out_dir.glob("short-*")):
            if not proj.is_dir():
                continue
            t0 = time.time()
            ok = render_project(proj)
            log.info("[render] %s/%s done in %.0fs (ok=%s)", wd.name[:30], proj.name[:40], time.time() - t0, ok)

    if failures:
        log.error("Pipeline failures: %s", failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
