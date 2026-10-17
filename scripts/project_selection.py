"""Select current short projects without reviving older or held picks."""
from __future__ import annotations

import json
from pathlib import Path


def select_projects(source_dir: Path, work_root: Path) -> list[Path]:
    """Use canonical picks, then the scaffold copy, or a metadata-free legacy tree.

    An empty picks file selects nothing. An unreadable or invalid file raises
    instead of falling back to older metadata. Old rank-only specs work when
    there is exactly one project for that rank.
    """
    projects = sorted(p for p in source_dir.glob("short-*") if p.is_dir())
    canonical = work_root / source_dir.name / "clips.json"
    sidecar = source_dir / "_clips.json"
    metadata = next((p for p in (canonical, sidecar) if p.exists() or p.is_symlink()), None)
    if metadata is None:
        return projects
    try:
        clips = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot select current projects from {metadata}: {exc}") from exc
    if not isinstance(clips, list):
        raise ValueError(f"Invalid picks in {metadata}: expected a list of clips")

    selected = []
    ranks = set()
    for clip in clips:
        rank = clip.get("rank") if isinstance(clip, dict) else None
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1 or rank in ranks:
            raise ValueError(f"Invalid or duplicate clip rank in {metadata}: {rank!r}")
        ranks.add(rank)
        candidates = [p for p in projects if p.name.startswith(f"short-{rank:02d}-")]
        slug = clip.get("slug")
        if slug is not None:
            if (not isinstance(slug, str) or not slug or slug != slug.strip()
                    or slug in (".", "..") or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in slug)):
                raise ValueError(f"Invalid clip slug in {metadata}: {slug!r}")
            candidates = [p for p in candidates if p.name == f"short-{rank:02d}-{slug}"]
        elif len(candidates) > 1:
            raise ValueError(f"Clip rank {rank} is ambiguous in {metadata}; add its slug")
        selected.extend(candidates)
    return sorted(selected)
