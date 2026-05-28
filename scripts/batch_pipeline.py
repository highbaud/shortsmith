"""Run shortsmith pipeline (--from-step 3) on every work dir that has
clips.json but no cut_manifests.json yet. Use this after the clip-picking
phase to chew through all the freshly-picked work dirs.

After pipeline finishes for each video, kicks off Hyperframes render too.
"""
from __future__ import annotations

import json
import logging
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
log = logging.getLogger("batch")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"


def find_source_video_for(work_dir: Path):
    from slugify import slugify
    for v in VIDEO_DIR.iterdir():
        if not v.is_file() or v.suffix.lower() not in (".mp4", ".mkv", ".webm", ".mov"):
            continue
        slug = slugify(v.stem)[:60]
        if slug == work_dir.name:
            return v
    return None


def new_work_dirs():
    """Work dirs that have clips.json but no cut_manifests.json yet."""
    out = []
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        if not (wd / "clips.json").exists():
            continue
        if (wd / "cut_manifests.json").exists():
            continue
        # Also skip work dirs with empty clips.json (0 clips picked)
        try:
            clips = json.loads((wd / "clips.json").read_text(encoding="utf-8"))
            if not isinstance(clips, list) or not clips:
                log.info("Skipping %s: empty clips.json (0 clips picked)", wd.name)
                continue
        except Exception as e:
            log.warning("Skipping %s: bad clips.json: %s", wd.name, e)
            continue
        out.append(wd)
    return out


def run_pipeline(video: Path) -> bool:
    log.info("=== pipeline: %s ===", video.name)
    cmd = ["uv", "run", "shortsmith", "run", str(video), "--from-step", "3"]
    proc = subprocess.run(cmd, cwd=str(SHORTSMITH_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("FAIL pipeline %s\nstderr=%s", video.name, (proc.stderr or "")[-1500:])
        return False
    return True


def render_project(project_dir: Path) -> bool:
    rel = project_dir.relative_to(KIT_ROOT)
    cmd_str = f'npx hyperframes render "{rel.as_posix()}"'
    proc = subprocess.run(cmd_str, cwd=str(KIT_ROOT), shell=True, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("FAIL render %s exit=%d stderr=%s",
                  project_dir.name, proc.returncode, (proc.stderr or "")[-500:])
        return False
    return True


def main() -> int:
    work_dirs = new_work_dirs()
    log.info("Found %d new work dirs to pipeline+render", len(work_dirs))

    todo = []
    for wd in work_dirs:
        v = find_source_video_for(wd)
        if v is None:
            log.warning("No source video found for %s", wd.name)
            continue
        todo.append((wd, v))
    log.info("Resolved %d sources", len(todo))

    pipeline_fail = []
    render_fail = []
    for i, (wd, v) in enumerate(todo, 1):
        log.info("[%d/%d] %s", i, len(todo), v.name)
        t0 = time.time()
        ok = run_pipeline(v)
        log.info("[%d/%d] pipeline %.0fs ok=%s", i, len(todo), time.time() - t0, ok)
        if not ok:
            pipeline_fail.append(v.name)
            continue

        # Render every short under auto-shorts/<source-slug>/
        out_dir = AUTO_SHORTS_ROOT / wd.name
        if not out_dir.exists():
            log.warning("No output dir at %s", out_dir)
            continue
        projects = sorted(p for p in out_dir.glob("short-*") if p.is_dir())
        log.info("[%d/%d] rendering %d projects", i, len(todo), len(projects))
        for proj in projects:
            t1 = time.time()
            ok = render_project(proj)
            log.info("  %s done in %.0fs ok=%s", proj.name[:50], time.time() - t1, ok)
            if not ok:
                render_fail.append(f"{wd.name}/{proj.name}")

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
