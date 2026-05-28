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
log = logging.getLogger("reprocess")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"
MARKER = ".reprocessed_v2"


def source_for(work_dir: Path):
    from slugify import slugify
    for v in VIDEO_DIR.iterdir():
        if v.is_file() and v.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov"):
            if slugify(v.stem)[:60] == work_dir.name:
                return v
    return None


def candidate_work_dirs(force: bool):
    out = []
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir() or not (wd / "clips.json").exists():
            continue
        try:
            clips = json.loads((wd / "clips.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(clips, list) or not clips:
            continue  # nothing picked for this source
        if not force and (wd / MARKER).exists():
            continue  # already re-processed
        out.append(wd)
    return out


def run_pipeline(video: Path) -> bool:
    cmd = ["uv", "run", "shortsmith", "run", str(video), "--from-step", "3"]
    proc = subprocess.run(cmd, cwd=str(SHORTSMITH_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("FAIL pipeline %s\n%s", video.name, (proc.stderr or proc.stdout or "")[-1500:])
        return False
    return True


def render_project(project_dir: Path) -> bool:
    rel = project_dir.relative_to(KIT_ROOT)
    cmd_str = f'npx hyperframes render "{rel.as_posix()}"'
    proc = subprocess.run(cmd_str, cwd=str(KIT_ROOT), shell=True, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("FAIL render %s exit=%d %s", project_dir.name, proc.returncode, (proc.stderr or "")[-400:])
        return False
    return True


def main() -> int:
    force = "--force" in sys.argv
    todo = []
    for wd in candidate_work_dirs(force):
        v = source_for(wd)
        if v is None:
            log.warning("no source video for %s", wd.name)
            continue
        todo.append((wd, v))

    log.info("Re-processing %d videos (force=%s)", len(todo), force)
    pfail, rfail, done = [], [], 0

    for i, (wd, v) in enumerate(todo, 1):
        log.info("[%d/%d] === %s ===", i, len(todo), v.name)
        t0 = time.time()
        if not run_pipeline(v):
            pfail.append(v.name)
            continue
        log.info("[%d/%d] pipeline %.0fs", i, len(todo), time.time() - t0)

        out_dir = AUTO_SHORTS_ROOT / wd.name
        projects = sorted(p for p in out_dir.glob("short-*") if p.is_dir()) if out_dir.exists() else []
        all_ok = True
        for proj in projects:
            t1 = time.time()
            ok = render_project(proj)
            log.info("  render %s %.0fs ok=%s", proj.name[:48], time.time() - t1, ok)
            if not ok:
                rfail.append(f"{wd.name}/{proj.name}")
                all_ok = False
        if all_ok and projects:
            (wd / MARKER).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
            done += 1

    log.info("DONE. fully_done=%d pipeline_fail=%d render_fail=%d", done, len(pfail), len(rfail))
    for f in pfail[:30]:
        log.error("  pipeline fail: %s", f)
    for f in rfail[:30]:
        log.error("  render fail: %s", f)
    return 0 if not (pfail or rfail) else 1


if __name__ == "__main__":
    sys.exit(main())
