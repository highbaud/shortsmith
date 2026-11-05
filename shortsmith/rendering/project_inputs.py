"""Read project media, clip metadata, and overlay timing."""

from __future__ import annotations

import json
import re
from pathlib import Path

from shortsmith import config
from shortsmith.overlay_timing import callout_timing, hook_duration
from shortsmith.records import ClipSpec, require_clips


def probe_duration(path: Path) -> float:
    from shortsmith._media import probe_duration

    return probe_duration(path)


def _kit_renders_dir() -> Path:
    """Configured kit-level folder for timestamped HyperFrames renders."""
    return config.AUTO_SHORTS_ROOT.parent.parent / "renders"


def _is_our_output(p: Path) -> bool:
    """True for files this layer produces (so they're never mistaken for a
    Hyperframes base render): final_remotion*, final_sfx*, and _-prefixed temps
    like _base.mp4 / _sfx_tmp.mp4."""
    return (
        p.stem.startswith("final_remotion")
        or p.stem.startswith("final_sfx")
        or p.stem.startswith("_")
    )


def finalized_deliverable(project_dir: Path) -> Path | None:
    """Publishing input: SFX output, Remotion output, or newest unstaged local render."""
    renders = project_dir / "renders"
    for name in ("final_sfx.mp4", "final_remotion.mp4"):
        if (renders / name).exists():
            return renders / name
    candidates = [path for path in renders.glob("*.mp4") if not path.stem.startswith("_")] \
        if renders.is_dir() else []
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def non_sfx_renders(project_dir: Path, kit_renders: Path) -> list[Path]:
    """SFX inputs in existing order: unstaged local renders, then matching kit renders."""
    candidates: list[Path] = []
    renders = project_dir / "renders"
    if renders.is_dir():
        candidates += [path for path in renders.glob("*.mp4")
                       if path.stem != "final_sfx" and not path.stem.startswith("_")]
    if kit_renders.is_dir():
        candidates += list(kit_renders.glob(f"{project_dir.name}_*.mp4"))
    return candidates


def hyperframes_renders(short_dir: Path, *, kit_root: Path | None = None) -> list[Path]:
    """Candidate Hyperframes base renders for this short, oldest→newest.

    Hyperframes writes either to the project's own renders/ (older flow:
    renders/final.mp4) or, after `npx hyperframes render`, to a kit-level
    timestamped file <proj>_<stamp>.mp4. We gather both and exclude our own
    derived outputs so re-runs stay idempotent."""
    cands: list[Path] = []
    rdir = short_dir / "renders"
    if rdir.is_dir():
        cands += [p for p in rdir.glob("*.mp4") if not _is_our_output(p)]
    kit = kit_root / "renders" if kit_root is not None else _kit_renders_dir()
    if kit and kit.is_dir():
        cands += [p for p in kit.glob(f"{short_dir.name}_*.mp4") if not _is_our_output(p)]
    return sorted(cands, key=lambda p: p.stat().st_mtime)


def pick_base(short_dir: Path, mode: str) -> Path:
    """Return the absolute path to the chosen base video.

    Raises FileNotFoundError when the selected base is missing.

    Modes:
      hyperframes — newest Hyperframes render (project renders/ or kit-level).
      sfx/final/clip — that specific project file.
      auto — sfx > newest Hyperframes render > clip.
    """
    renders = short_dir / "renders"
    explicit = {
        "sfx": renders / "final_sfx.mp4",
        "final": renders / "final.mp4",
        "clip": short_dir / "assets" / "clip-edit.mp4",
    }
    if mode in explicit:
        p = explicit[mode]
        if not p.exists():
            raise FileNotFoundError(f"Requested base {mode!r} not found at {p}")
        return p
    if mode == "hyperframes":
        hf = hyperframes_renders(short_dir)
        if not hf:
            raise FileNotFoundError(
                f"No Hyperframes render found for {short_dir.name} "
                f"(looked in renders/ and kit-level renders)"
            )
        return hf[-1]
    if mode != "auto":
        raise ValueError(f"Unknown base mode {mode!r}; use auto, hyperframes, sfx, final, or clip")
    if explicit["sfx"].exists():
        return explicit["sfx"]
    hf = hyperframes_renders(short_dir)
    if hf:
        return hf[-1]
    if explicit["clip"].exists():
        return explicit["clip"]
    raise FileNotFoundError(
        f"No base video found in {short_dir} (need a Hyperframes render or assets/clip-edit.mp4)"
    )


def clip_for(short_dir: Path) -> ClipSpec | None:
    """Load the source clip spec (hook + callouts + segments + viral_score, ...)
    for a `short-NN-<slug>` folder from its sibling `_clips.json`. Returns None
    if the folder name doesn't parse or the file isn't there."""
    m = re.match(r"short-(\d+)-", short_dir.name)
    if not m:
        return None
    rank = int(m.group(1))
    clips_path = short_dir.parent / "_clips.json"
    if not clips_path.exists():
        return None
    try:
        clips = require_clips(json.loads(clips_path.read_text(encoding="utf-8")), clips_path)
    except (OSError, ValueError):
        return None
    return next((c for c in clips if c["rank"] == rank), None)


def overlay_windows(short_dir: Path, clip_duration: float) -> list[dict]:
    """Derive Hyperframes overlay time windows (hook + callouts) from the source
    _clips.json using the same timing rules as the scaffolded overlays."""
    clip = clip_for(short_dir)
    if not clip:
        return []

    windows: list[dict] = []

    raw_hook = clip.get("hook")
    if isinstance(raw_hook, dict) and str(raw_hook.get("text", "")).strip():
        dur = hook_duration(raw_hook, clip_duration)
        windows.append({"start": 0.0, "end": round(dur, 3)})

    for co in clip.get("callouts") or []:
        timing = callout_timing(co, clip_duration)
        if timing is None:
            continue
        ls, dur = timing
        if not str(co.get("text", "")).strip():
            continue
        windows.append({"start": round(ls, 3), "end": round(ls + dur, 3)})

    windows.sort(key=lambda w: w["start"])
    return windows
