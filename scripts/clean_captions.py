"""Clean caption text + clip data in place: strip hashtags and normalize
em/en/figure dashes (and the U+FFFD mojibake a corrupted em-dash becomes) to
plain punctuation — a hyphen between digits (ranges), a comma in prose.

Scaffold now does both at generation time, but files/data written earlier still
carry hashtag blocks and dash characters. This back-fills them.

Covers:
  * caption .txt — per-source `<short>.txt`, per-project `caption.txt`, `_all/*.txt`
    (strip hashtags + normalize dashes)
  * clip JSON — `work/<slug>/clips.json` + `auto-shorts/<src>/_clips.json`
    (normalize dashes in ALL string values so future scaffolds/renders + metadata
    are clean; hashtags are left in clips.json — they're stripped at caption write)

Idempotent and safe to re-run.

Usage:
    uv run python scripts/clean_captions.py            # clean everything
    uv run python scripts/clean_captions.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shortsmith.config import AUTO_SHORTS_ROOT, KIT_ROOT
from shortsmith.scaffold import _strip_hashtags, normalize_dashes

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = REPO_ROOT / "work"


def _caption_files():
    roots = [AUTO_SHORTS_ROOT, KIT_ROOT / "renders" / "_all"]
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.txt"):
            if p in seen:
                continue
            seen.add(p)
            yield p


def _clip_json_files():
    if WORK_ROOT.exists():
        for p in WORK_ROOT.glob("*/clips.json"):
            yield p
    if AUTO_SHORTS_ROOT.exists():
        for p in AUTO_SHORTS_ROOT.glob("*/_clips.json"):
            yield p


def _deep_norm(obj):
    """Recursively dash-normalize all string values. Returns (new, changed)."""
    if isinstance(obj, str):
        n = normalize_dashes(obj)
        return n, n != obj
    if isinstance(obj, list):
        changed = False
        out = []
        for v in obj:
            nv, c = _deep_norm(v)
            out.append(nv)
            changed = changed or c
        return out, changed
    if isinstance(obj, dict):
        changed = False
        out = {}
        for k, v in obj.items():
            nv, c = _deep_norm(v)
            out[k] = nv
            changed = changed or c
        return out, changed
    return obj, False


def main() -> None:
    ap = argparse.ArgumentParser(description="Strip hashtags + normalize dashes in captions and clip data.")
    ap.add_argument("--dry-run", action="store_true", help="Report changes; write nothing.")
    args = ap.parse_args()

    txt = 0
    for p in _caption_files():
        try:
            original = p.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  skip {p} ({e})")
            continue
        cleaned = normalize_dashes(_strip_hashtags(original)).rstrip("\n") + "\n"
        if cleaned == original:
            continue
        txt += 1
        print(f"{'WOULD CLEAN' if args.dry_run else 'cleaned'} txt: {p}")
        if not args.dry_run:
            p.write_text(cleaned, encoding="utf-8")

    js = 0
    for p in _clip_json_files():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  skip {p} ({e})")
            continue
        new, changed = _deep_norm(data)
        if not changed:
            continue
        js += 1
        print(f"{'WOULD CLEAN' if args.dry_run else 'cleaned'} json: {p}")
        if not args.dry_run:
            p.write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb}: {txt} caption .txt, {js} clip-json files.")


if __name__ == "__main__":
    main()
