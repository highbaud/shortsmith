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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_remotion as rr  # noqa: E402  (_probe_duration)

from shortsmith.config import AUTO_SHORTS_ROOT  # noqa: E402
from shortsmith.scaffold import normalize_dashes  # noqa: E402

KIT_RENDERS = AUTO_SHORTS_ROOT.parent.parent / "renders"
ALL_DIR = KIT_RENDERS / "_all"


def _deliverable(proj: Path) -> Path | None:
    r = proj / "renders"
    for name in ("final_sfx.mp4", "final_remotion.mp4"):
        if (r / name).exists():
            return r / name
    cands = [p for p in r.glob("*.mp4") if not p.stem.startswith("_")] if r.is_dir() else []
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _broll_keywords(proj: Path) -> list[str]:
    """Pull the named entities the b-roll engine detected (brands / people) —
    these double as discovery keywords/tags. Reads broll.auto.json defensively."""
    out: list[str] = []
    bp = proj / "broll.auto.json"
    if not bp.exists():
        return out
    try:
        data = json.loads(bp.read_text(encoding="utf-8"))
    except Exception:
        return out
    slides = data if isinstance(data, list) else data.get("slides", [])
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


def build(proj: Path, clips_by_rank: dict[int, dict]) -> dict | None:
    m = re.match(r"short-(\d+)-", proj.name)
    if not m:
        return None
    rank = int(m.group(1))
    clip = clips_by_rank.get(rank, {})
    deliv = _deliverable(proj)
    cap = proj / "caption.txt"
    description = cap.read_text(encoding="utf-8").strip() if cap.exists() else \
        (clip.get("instagram_caption") or "").strip()

    hook = clip.get("hook") or {}
    keywords = _broll_keywords(proj)
    overline = (hook.get("overline") or "").strip()
    if overline and overline not in keywords:
        keywords.insert(0, overline)

    thumb = None
    base = f"{proj.parent.name}__{proj.name}"
    if (ALL_DIR / f"{base}.jpg").exists():
        thumb = str(ALL_DIR / f"{base}.jpg")
    elif (proj / "renders" / "thumb.jpg").exists():
        thumb = str(proj / "renders" / "thumb.jpg")

    return {
        "source": proj.parent.name,
        "short": proj.name,
        "rank": rank,
        "title": normalize_dashes((clip.get("hook_text") or "").strip()),
        "label": normalize_dashes(overline),            # the hook eyebrow
        "description": normalize_dashes(description),    # hashtag-free caption body
        "summary": normalize_dashes((clip.get("reasoning") or "").strip()),  # why it lands
        "keywords": [normalize_dashes(k) for k in keywords],  # detected entities (NOT hashtags)
        "callouts": [normalize_dashes(c.get("text", "")) for c in (clip.get("callouts") or []) if c.get("text")],
        "viral_score": clip.get("viral_score"),
        "duration_seconds": round(rr._probe_duration(deliv), 2) if deliv else None,
        "width": 1080,
        "height": 1920,
        "thumbnail": thumb,
        "deliverable": str(deliv) if deliv else None,
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
            except Exception:
                pass
        for proj in sorted(src_dir.glob("short-*")):
            if not proj.is_dir():
                continue
            meta = build(proj, clips_by_rank)
            if not meta:
                continue
            (proj / "metadata.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            if ALL_DIR.exists():
                base = f"{proj.parent.name}__{proj.name}"
                (ALL_DIR / f"{base}.json").write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            written += 1
    print(f"Wrote metadata.json for {written} shorts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
