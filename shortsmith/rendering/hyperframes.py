"""Run one scaffolded HyperFrames project and confirm a usable base output."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from shortsmith import config
from shortsmith.config import hyperframes_cmd

from .project_inputs import hyperframes_renders

# Present only in projects scaffolded from the HyperFrames 0.9-safe template.
FONT_MARKER = "assets/fonts/BebasNeue-Regular.woff2"


def render_project(project_dir: Path, kit_root: Path, logger: logging.Logger) -> bool:
    """Render with the pinned CLI, accepting either supported output layout."""
    try:
        project_dir, kit_root = project_dir.resolve(), kit_root.resolve()
        if not project_dir.is_dir() or not (project_dir / "index.html").is_file():
            logger.error("Cannot render %s: not a scaffolded project directory", project_dir)
            return False
        if (not config.HYPERFRAMES_SPEC.startswith("hyperframes@0.7.")
                and FONT_MARKER not in (project_dir / "index.html").read_text(encoding="utf-8")):
            logger.error("Cannot render %s with %s: it uses the pre-0.9 template (wrong font, "
                         "hook flicker). Scaffold it again, or set "
                         "SHORTSMITH_HYPERFRAMES_VERSION=0.7.71.", project_dir,
                         config.HYPERFRAMES_SPEC)
            return False
        relative = project_dir.relative_to(kit_root)
        # HyperFrames 0.8.219+ writes into <project>/renders and fails if it is missing.
        (project_dir / "renders").mkdir(exist_ok=True)
        command = hyperframes_cmd("render", relative.as_posix())
        logger.info("HyperFrames command: %s", command)
        result = subprocess.run(
            command, cwd=str(kit_root), capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode:
            logger.error("HyperFrames render failed for %s (exit=%d): %s", project_dir,
                         result.returncode, (result.stderr or result.stdout or "").strip())
            return False
        outputs = [path for path in hyperframes_renders(project_dir, kit_root=kit_root)
                   if path.is_file() and path.stat().st_size > 0]
        if not outputs:
            logger.error("HyperFrames produced no nonempty output for %s", project_dir)
            return False
        logger.info("HyperFrames output: %s", outputs[-1])
        return True
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.error("Cannot render %s: %s", project_dir, exc)
        return False
