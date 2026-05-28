"""Re-process every work dir whose cut_manifests.json predates the
cut/clean/filler-list fix landed at FIX_CUTOFF_EPOCH.

For each outdated work dir we:
1. Move the existing cut_manifests.json aside (.bak) so --from-step 3
   re-runs the cut step with the new boundary snap.
2. Invoke `uv run shortsmith run <video> --from-step 3` — re-cut +
   re-clean + re-enhance + re-retranscribe + re-reframe + re-scaffold.
3. Re-render every Hyperframes project under auto-shorts/<slug>/.

Use after batch_pipeline_new.py has finished, OR sequentially queued
once that batch completes.
"""
from __future__ import annotations

import json
import logging
import shutil
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

# Anything cut BEFORE this epoch used the old (chopped-thought,
# filler-mauling, tight-silence-margin) code path. Bump if more fixes
# land later.
FIX_CUTOFF_EPOCH = 1780002924


def find_source_video_for(work_dir: Path):
    from slugify import slugify
    for v in VIDEO_DIR.iterdir():
        if not v.is_file() or v.suffix.lower() not in (".mp4", ".mkv", ".webm", ".mov"):
            continue
        slug = slugify(v.stem)[:60]
        if slug == work_dir.name:
            return v
    return None


def outdated_work_dirs() -> list[Path]:
    """Work dirs whose cut_manifests.json predates the fix."""
    out = []
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        cm = wd / "cut_manifests.json"
        if not cm.exists():
            continue
        # Skip empty clips lists
        clips_p = wd / "clips.json"
        if clips_p.exists():
            try:
                clips = json.loads(clips_p.read_text(encoding="utf-8"))
                if not isinstance(clips, list) or not clips:
                    continue
            except Exception:
                pass
        if cm.stat().st_mtime < FIX_CUTOFF_EPOCH:
            out.append(wd)
    return out


def archive_old_outputs(wd: Path) -> None:
    """Move stale cut_manifests aside so --from-step 3 re-runs the cut step."""
    cm = wd / "cut_manifests.json"
    if cm.exists():
        bak = wd / f"cut_manifests.json.bak.{int(time.time())}"
        shutil.move(str(cm), str(bak))
        log.debug("archived %s -> %s", cm.name, bak.name)


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
    work_dirs = outdated_work_dirs()
    log.info("Found %d outdated work dirs (cut_manifests < %d)",
             len(work_dirs), FIX_CUTOFF_EPOCH)

    todo = []
    for wd in work_dirs:
        v = find_source_video_for(wd)
        if v is None:
            log.warning("No source video for %s", wd.name)
            continue
        todo.append((wd, v))
    log.info("Resolved %d sources to re-process", len(todo))

    pipeline_fail = []
    render_fail = []
    for i, (wd, v) in enumerate(todo, 1):
        log.info("[%d/%d] %s", i, len(todo), v.name)
        archive_old_outputs(wd)
        t0 = time.time()
        ok = run_pipeline(v)
        log.info("[%d/%d] pipeline %.0fs ok=%s", i, len(todo), time.time() - t0, ok)
        if not ok:
            pipeline_fail.append(v.name)
            continue

        out_dir = AUTO_SHORTS_ROOT / wd.name
        if not out_dir.exists():
            log.warning("No auto-shorts output dir for %s", wd.name)
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
    return 0 if not (pipeline_fail or render_fail) else 1


if __name__ == "__main__":
    sys.exit(main())
