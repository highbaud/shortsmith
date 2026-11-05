"""Shared source lookup and pipeline-then-render policy for batch commands."""
from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from slugify import slugify

from shortsmith._process import resolve_executable
from shortsmith.config import VIDEO_EXTENSIONS

from .hyperframes import render_project
from .project_selection import select_projects


def load_picks(clips_path: Path) -> list[dict]:
    """Read a work dir's clips.json; OSError or ValueError when it is missing or not clip objects."""
    clips = json.loads(clips_path.read_text(encoding="utf-8"))
    if not isinstance(clips, list) or any(not isinstance(clip, dict) for clip in clips):
        raise ValueError(f"Invalid picks in {clips_path}: expected clip objects")
    return clips


def run_batch(
    work_dirs: list[Path], *, video_dir: Path, output_root: Path, kit_root: Path,
    project_root: Path, from_step: int, logger: logging.Logger,
    before_pipeline: Callable[[Path], None] | None = None,
    on_success: Callable[[Path], None] | None = None,
    pending_marker: str | None = None,
) -> tuple[list[str], list[str], int]:
    """Process each source independently and complete only fully rendered work."""
    pipeline_fail, render_fail, done = [], [], 0
    for index, wd in enumerate(work_dirs, 1):
        try:
            video = next((v for v in video_dir.iterdir()
                          if v.is_file() and v.suffix.lower() in VIDEO_EXTENSIONS
                          and slugify(v.stem)[:60] == wd.name), None)
            if video is None:
                raise FileNotFoundError(f"No source video for {wd} in {video_dir}")
            logger.info("[%d/%d] === %s ===", index, len(work_dirs), video.name)
            if pending_marker is not None:
                (wd / pending_marker).write_text("pending", encoding="ascii")
            if before_pipeline is not None:
                before_pipeline(wd)
            command = [resolve_executable("uv"), "run", "shortsmith", "run", str(video),
                       "--from-step", str(from_step)]
            logger.info("$ %s", shlex.join(command))
            started = time.time()
            result = subprocess.run(command, cwd=str(project_root), capture_output=True,
                                    text=True, encoding="utf-8")
            if result.returncode:
                logger.error("FAIL pipeline %s (exit=%d)\n%s", video.name, result.returncode,
                             result.stderr or result.stdout or "")
                pipeline_fail.append(video.name)
                continue
            logger.info("[%d/%d] pipeline %.0fs", index, len(work_dirs), time.time() - started)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("FAIL pipeline %s: %s", wd.name, exc)
            pipeline_fail.append(wd.name)
            continue

        output_dir = output_root / wd.name
        try:
            if not output_dir.is_dir():
                raise ValueError(f"No auto-shorts output dir at {output_dir}")
            projects = select_projects(output_dir, wd.parent)
        except ValueError as exc:
            logger.error("FAIL project selection %s: %s", wd.name, exc)
            render_fail.append(wd.name)
            continue
        logger.info("[%d/%d] rendering %d projects", index, len(work_dirs), len(projects))
        all_ok = bool(projects)
        for project in projects:
            started = time.time()
            ok = render_project(project, kit_root, logger)
            logger.info("  render %s %.0fs ok=%s", project.name, time.time() - started, ok)
            if not ok:
                render_fail.append(f"{wd.name}/{project.name}")
                all_ok = False
        if all_ok:
            try:
                if on_success is not None:
                    on_success(wd)
                if pending_marker is not None:
                    (wd / pending_marker).unlink()
                done += 1
            except OSError as exc:
                logger.error("Cannot record completed work for %s: %s", wd, exc)
                render_fail.append(wd.name)
    return pipeline_fail, render_fail, done
