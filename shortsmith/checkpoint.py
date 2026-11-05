"""Per-work-dir progress checkpoints for crash recovery.

Writes `work/<slug>/.progress.json`:
    {
      "steps": {"3": true, "4": true, ...},   # pipeline steps completed
      "rendered": ["short-01-...", ...]         # clip slugs whose final.mp4 exists
    }

Lets a re-run skip pipeline steps that already finished and skip clips that are
already rendered, so a crash during step 6 of a 12-clip video resumes at step 6
instead of re-cutting from scratch.

Best-effort: a missing or corrupt file just means "nothing done yet".
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ._files import atomic_write

FILENAME = ".progress.json"
log = logging.getLogger(__name__)


def _path(work_dir: Path) -> Path:
    return Path(work_dir) / FILENAME


def load(work_dir: Path) -> dict:
    p = _path(work_dir)
    if not p.exists():
        return {"steps": {}, "rendered": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # ValueError catches both a truncated JSON body and bytes that are not
        # valid UTF-8, which is what a crash mid-write leaves behind.
        return {"steps": {}, "rendered": []}
    if not isinstance(data, dict):
        return {"steps": {}, "rendered": []}
    if not isinstance(data.get("steps"), dict):
        data["steps"] = {}
    if not isinstance(data.get("rendered"), list):
        data["rendered"] = []
    return data


def _save(work_dir: Path, data: dict) -> None:
    target = _path(work_dir)
    try:
        atomic_write(target, json.dumps(data, indent=2, ensure_ascii=False))
    except OSError as exc:
        log.warning("Could not save checkpoint %s: %s", target, exc)


def step_done(work_dir: Path, step: int) -> bool:
    return bool(load(work_dir).get("steps", {}).get(str(step)))


def mark_step(work_dir: Path, step: int) -> None:
    data = load(work_dir)
    data["steps"][str(step)] = True
    _save(work_dir, data)


def clear_step(work_dir: Path, step: int) -> None:
    """Remove an earlier success before retrying a stage."""
    data = load(work_dir)
    if data["steps"].pop(str(step), None) is not None:
        _save(work_dir, data)


def is_rendered(work_dir: Path, slug: str) -> bool:
    return slug in load(work_dir).get("rendered", [])


def mark_rendered(work_dir: Path, slug: str) -> None:
    data = load(work_dir)
    if slug not in data["rendered"]:
        data["rendered"].append(slug)
    _save(work_dir, data)


def reset(work_dir: Path) -> None:
    """Clear all progress (used when forcing a clean re-process)."""
    _path(work_dir).unlink(missing_ok=True)
