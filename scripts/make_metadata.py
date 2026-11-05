"""Generate a per-short metadata record for publishing context.

Assembles everything the (upcoming) Metricool publish phase and a human need to
post each short — title, description, summary, keywords, score, duration, thumb
— from data ALREADY on disk (the source `_clips.json`, the cleaned `caption.txt`,
the detected b-roll entities, and an ffprobe of the deliverable). No new API
calls: the generative work (hook, reasoning, caption) was done at clip-selection
time; this just consolidates it into one machine-readable record per short.

Writes `<project>/metadata.json` and a copy to `<kit>/renders/_all/<base>.json`.
Idempotent — re-run anytime; it overwrites.

Usage:
    uv run python scripts/make_metadata.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from shortsmith._files import atomic_write
from shortsmith.captions import normalize_dashes
from shortsmith.config import AUTO_SHORTS_ROOT
from shortsmith.rendering import project_inputs

KIT_RENDERS = AUTO_SHORTS_ROOT.parent.parent / "renders"
ALL_DIR = KIT_RENDERS / "_all"


def _broll_keywords(project_dir: Path) -> list[str]:
    """Pull the named entities the b-roll engine detected (brands / people) —
    these double as discovery keywords/tags. Reads broll.auto.json defensively."""
    out: list[str] = []
    bp = project_dir / "broll.auto.json"
    if not bp.exists():
        return out
    try:
        data = json.loads(bp.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"warning: {project_dir.name}: broll.auto.json unreadable ({e})")
        return out
    slides = data.get("slides", []) if isinstance(data, dict) else data
    if not isinstance(slides, list):
        print(f"warning: {project_dir.name}: broll.auto.json must contain a slide list")
        return out
    for s in slides:
        if not isinstance(s, dict):
            continue
        for key in ("entity", "name", "label", "query", "subject", "text"):
            v = s.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
                break
    # de-dupe, keep order
    seen, uniq = set(), []
    for k in out:
        if k.lower() not in seen:
            seen.add(k.lower())
            uniq.append(k)
    return uniq


def build_project_metadata(project_dir: Path, clips_by_rank: dict[int, dict]) -> dict | None:
    m = re.match(r"short-(\d+)-", project_dir.name)
    if not m:
        return None
    rank = int(m.group(1))
    clip = clips_by_rank.get(rank, {})
    deliverable = project_inputs.finalized_deliverable(project_dir)
    caption_path = project_dir / "caption.txt"
    description = caption_path.read_text(encoding="utf-8").strip() if caption_path.exists() else \
        (clip.get("instagram_caption") or "").strip()

    hook = clip.get("hook") or {}
    keywords = _broll_keywords(project_dir)
    overline = (hook.get("overline") or "").strip()
    if overline and overline not in keywords:
        keywords.insert(0, overline)

    thumbnail_path = None
    base = f"{project_dir.parent.name}__{project_dir.name}"
    if (ALL_DIR / f"{base}.jpg").exists():
        thumbnail_path = str(ALL_DIR / f"{base}.jpg")
    elif (project_dir / "renders" / "thumb.jpg").exists():
        thumbnail_path = str(project_dir / "renders" / "thumb.jpg")

    return {
        "source": project_dir.parent.name,
        "short": project_dir.name,
        "rank": rank,
        "title": normalize_dashes((clip.get("hook_text") or "").strip()),
        "label": normalize_dashes(overline),            # the hook eyebrow
        "description": normalize_dashes(description),    # hashtag-free caption body
        "summary": normalize_dashes((clip.get("reasoning") or "").strip()),  # why it lands
        "keywords": [normalize_dashes(k) for k in keywords],  # detected entities (NOT hashtags)
        "callouts": [normalize_dashes(c.get("text", "")) for c in (clip.get("callouts") or []) if c.get("text")],
        "viral_score": clip.get("viral_score"),
        "duration_seconds": round(project_inputs.probe_duration(deliverable), 2) if deliverable else None,
        "width": 1080,
        "height": 1920,
        "thumbnail": thumbnail_path,
        "deliverable": str(deliverable) if deliverable else None,
    }


def main() -> int:
    written = 0
    for src_dir in sorted(AUTO_SHORTS_ROOT.iterdir()):
        if not src_dir.is_dir():
            continue
        clips_path = src_dir / "_clips.json"
        clips_by_rank: dict[int, dict] = {}
        if clips_path.exists():
            try:
                for c in json.loads(clips_path.read_text(encoding="utf-8")):
                    if isinstance(c, dict) and c.get("rank") is not None:
                        clips_by_rank[int(c["rank"])] = c
            except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
                print(f"warning: {src_dir.name}: _clips.json unreadable ({e})")
        for project_dir in sorted(src_dir.glob("short-*")):
            if not project_dir.is_dir():
                continue
            metadata = build_project_metadata(project_dir, clips_by_rank)
            if not metadata:
                continue
            atomic_write(project_dir / "metadata.json",
                         json.dumps(metadata, indent=2, ensure_ascii=False))
            if ALL_DIR.exists():
                base = f"{project_dir.parent.name}__{project_dir.name}"
                atomic_write(ALL_DIR / f"{base}.json",
                             json.dumps(metadata, indent=2, ensure_ascii=False))
            written += 1
    print(f"Wrote metadata.json for {written} shorts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
