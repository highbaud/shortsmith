"""Scan assets/sfx/ and build a rich, structured catalog of every one-shot.

For each .wav / .mp3 under assets/sfx/ (root drops + pack/), this:
  1. ffprobes duration, sample rate, channels
  2. ffmpeg volumedetects peak and mean dBFS
  3. Heuristically tags a category from the filename
     (whoosh / whip / swipe / impact / ding / pop / click / money /
      magic / error / gong / riser / icon / beep)
  4. Suggests one or more slots from the SFX pipeline's 6 slot taxonomy
     (swipe-in, swipe-out, hook-impact, cash-register, ding, whoosh)
     using a category+duration matrix
  5. Generates a human-readable label from duration + loudness buckets

Output:
  assets/sfx/index.json   — structured JSON catalog (consumed by humans + AI
                            picking slot variants when curating pack.json)
  assets/sfx/CATALOG.md   — readable summary table grouped by slot

This DOES NOT modify pack.json or the curated pack — it is purely a discovery
tool. Re-run any time you drop new raw files in assets/sfx/.

Usage:
    uv run python scripts/build_sfx_index.py
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from shortsmith.config import SFX_DIR

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sfx_index")

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}

# Filename-keyword -> category. First match wins; order matters.
# Keep keywords lowercase; we lowercase the stem before matching.
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("cash register", "money"),
    ("kaching",       "money"),
    ("register",      "money"),
    ("coin",          "money"),
    ("riser",         "riser"),
    ("whip",          "whip"),
    ("fastwhoosh",    "whoosh-fast"),
    ("camera",        "camera"),     # camera-shutter / camera-whoosh — works as hook-impact
    ("whoosh",        "whoosh"),
    ("swipe",         "whoosh"),
    ("shine",         "magic"),
    ("sparkle",       "magic"),
    ("bell",          "ding"),
    ("ding",          "ding"),
    ("chime",         "ding"),
    ("notification",  "ding"),
    ("gong",          "impact"),
    ("impact",        "impact"),
    ("boom",          "impact"),
    ("thud",          "impact"),
    ("error",         "error"),
    ("buzz",          "error"),
    ("click",         "click"),
    ("tap",           "click"),
    ("mouse",         "click"),
    ("pop",           "pop"),
    ("beep",          "beep"),
    ("icon",          "ui"),
]

# Slot taxonomy mirrors shortsmith.sfx's pack.json keys. Each slot entry says
# what kind of one-shot belongs there (level cue, ideal duration, which
# categories typically map in).
SLOT_DEFINITIONS: dict[str, dict] = {
    "wrong-answer": {
        "trigger": ("First negative-outcome word in a clip "
                    "(crashed, scammed, rugged, bankrupt, ...). Quiz-show "
                    "buzz feel rather than mean-spirited."),
        "ideal_duration_s": [0.5, 2.0],
        "level_dbfs_under_voice": [8, 14],
        "categories_preferred": ["error"],
        "notes": "Classic error/buzz texture. Fires once per clip in sparing mode.",
    },
    "swipe-in": {
        "trigger": "Played on callout/text appearing (Hyperframes slam-in).",
        "ideal_duration_s": [0.15, 0.55],
        "level_dbfs_under_voice": [10, 16],
        "categories_preferred": ["whoosh", "whoosh-fast", "whip"],
        "notes": "Short transient with quick decay. Stereo width helps.",
    },
    "swipe-out": {
        "trigger": "Played on callout leaving / scene wipe.",
        "ideal_duration_s": [0.15, 0.55],
        "level_dbfs_under_voice": [12, 18],
        "categories_preferred": ["whoosh", "whip"],
        "notes": "Even shorter / softer than swipe-in — should not pull focus.",
    },
    "hook-impact": {
        "trigger": "Hook slam at t=0 (opening 2.6s).",
        "ideal_duration_s": [0.4, 1.5],
        "level_dbfs_under_voice": [6, 12],
        "categories_preferred": ["camera", "impact", "riser", "whoosh"],
        "notes": "Big, dramatic. Riser + impact bodies welcome.",
    },
    "cash-register": {
        "trigger": "First money word in a clip ($, million, cash, rich, wealth).",
        "ideal_duration_s": [0.5, 2.0],
        "level_dbfs_under_voice": [8, 14],
        "categories_preferred": ["money"],
        "notes": "Iconic 'kaching'. Only fires once per clip.",
    },
    "ding": {
        "trigger": "Bigstat number reveal (callouts marked bigstat).",
        "ideal_duration_s": [0.3, 1.2],
        "level_dbfs_under_voice": [10, 14],
        "categories_preferred": ["ding", "magic", "beep"],
        "notes": "Bright pitched hit. Bell / anime shine / chime all valid.",
    },
    "whoosh": {
        "trigger": "Generic transition fallback (cut seams, reorders).",
        "ideal_duration_s": [0.3, 1.0],
        "level_dbfs_under_voice": [12, 18],
        "categories_preferred": ["whoosh", "whoosh-fast"],
        "notes": "Longer / smoother than swipe-in.",
    },
}

# category -> list of slots it's a good fit for, in order of preference.
CATEGORY_TO_SLOTS: dict[str, list[str]] = {
    "money":       ["cash-register"],
    "ding":        ["ding"],
    "magic":       ["ding"],
    "beep":        ["ding"],
    "whip":        ["swipe-in", "swipe-out"],
    "whoosh-fast": ["swipe-in", "swipe-out", "whoosh"],
    "whoosh":      ["whoosh", "swipe-in", "swipe-out", "hook-impact"],
    "camera":     ["hook-impact", "whoosh"],
    "impact":      ["hook-impact"],
    "riser":       ["hook-impact"],
    "click":       ["ding"],   # mouse-click family promoted into ding rotation
    "pop":         ["ding"],   # short bright pop can sub for a ding in a pinch
    "error":       ["wrong-answer"],  # negative cue -> wrong-answer slot
    "ui":          ["ding", "swipe-in"],
    "unknown":     [],
}


def ffprobe_stats(p: Path) -> dict | None:
    """Return {duration, sample_rate, channels} or None on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=duration,sample_rate,channels", "-of", "json", str(p)],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
        streams = data.get("streams", [])
        if not streams:
            return None
        s = streams[0]
        dur = s.get("duration")
        return {
            "duration_s": round(float(dur), 3) if dur else None,
            "sample_rate": int(s["sample_rate"]) if s.get("sample_rate") else None,
            "channels": int(s["channels"]) if s.get("channels") else None,
        }
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError):
        return None


def measure_levels(p: Path) -> dict:
    """Return {peak_dbfs, mean_dbfs} via ffmpeg volumedetect."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(p), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    peak = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", out.stderr)
    mean = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", out.stderr)
    return {
        "peak_dbfs": float(peak.group(1)) if peak else None,
        "mean_dbfs": float(mean.group(1)) if mean else None,
    }


def categorize(stem: str) -> str:
    s = stem.lower()
    for keyword, cat in CATEGORY_KEYWORDS:
        if keyword in s:
            return cat
    return "unknown"


def suggest_slots(category: str, duration_s: float | None) -> list[str]:
    """Pick slot candidates, filtering by ideal duration band."""
    candidates = CATEGORY_TO_SLOTS.get(category, [])
    if not duration_s:
        return list(candidates)
    out: list[str] = []
    for slot in candidates:
        lo, hi = SLOT_DEFINITIONS[slot]["ideal_duration_s"]
        # Allow 50% slop on either side so good-but-not-perfect picks still surface.
        if (lo * 0.5) <= duration_s <= (hi * 1.5):
            out.append(slot)
    # If duration filtering rejected everything, fall back to category mapping
    # so the file still shows up as "consider for X" rather than disappearing.
    return out or list(candidates)


def duration_bucket(d: float | None) -> str:
    if d is None:
        return "unknown"
    if d < 0.25:
        return "very-short"
    if d < 0.6:
        return "short"
    if d < 1.5:
        return "medium"
    return "long"


def loudness_bucket(peak_dbfs: float | None) -> str:
    if peak_dbfs is None:
        return "unknown"
    if peak_dbfs >= -3.0:
        return "loud"
    if peak_dbfs >= -9.0:
        return "normal"
    if peak_dbfs >= -18.0:
        return "soft"
    return "quiet"


def describe(category: str, dur_b: str, loud_b: str) -> str:
    return f"{dur_b} {loud_b} {category}".replace("unknown ", "").strip()


def scan(root: Path) -> list[dict]:
    """Walk root for audio files; ffprobe + level-detect + categorize each."""
    entries: list[dict] = []
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )
    for p in files:
        rel = p.relative_to(root).as_posix()
        location = "pack" if rel.startswith("pack/") else "raw"
        stats = ffprobe_stats(p) or {}
        levels = measure_levels(p)
        category = categorize(p.stem)
        duration_s = stats.get("duration_s")
        slots = suggest_slots(category, duration_s)
        entries.append({
            "path": rel,
            "filename": p.name,
            "location": location,
            "size_bytes": p.stat().st_size,
            "duration_s": duration_s,
            "sample_rate": stats.get("sample_rate"),
            "channels": stats.get("channels"),
            "peak_dbfs": levels["peak_dbfs"],
            "mean_dbfs": levels["mean_dbfs"],
            "category": category,
            "duration_bucket": duration_bucket(duration_s),
            "loudness_bucket": loudness_bucket(levels["peak_dbfs"]),
            "description": describe(category, duration_bucket(duration_s),
                                    loudness_bucket(levels["peak_dbfs"])),
            "suggested_slots": slots,
        })
        log.info("scan %-32s -> %-12s %s",
                 rel, category, ",".join(slots) or "(no slot)")
    return entries


def write_catalog_md(index: dict, dst: Path) -> None:
    """Human-readable summary grouped by slot."""
    lines: list[str] = []
    lines.append("# SFX catalog\n")
    lines.append(
        f"_Auto-generated by `scripts/build_sfx_index.py` at "
        f"{index['generated_at']}. {len(index['files'])} files indexed._\n"
    )
    lines.append("## Slot taxonomy\n")
    lines.append("| Slot | Trigger | Ideal duration | Level under voice |")
    lines.append("|---|---|---|---|")
    for slot, defn in index["slot_definitions"].items():
        lo, hi = defn["ideal_duration_s"]
        ldb, hdb = defn["level_dbfs_under_voice"]
        lines.append(
            f"| `{slot}` | {defn['trigger']} | {lo:.2f}–{hi:.2f}s | -{ldb} to -{hdb} dB |"
        )
    lines.append("")

    # Group files by their first suggested slot (or 'unmapped').
    by_slot: dict[str, list[dict]] = {s: [] for s in index["slot_definitions"]}
    by_slot["unmapped"] = []
    for f in index["files"]:
        target = f["suggested_slots"][0] if f["suggested_slots"] else "unmapped"
        by_slot.setdefault(target, []).append(f)

    for slot, files in by_slot.items():
        if not files:
            continue
        lines.append(f"## {slot}  ({len(files)} candidate{'s' if len(files) != 1 else ''})\n")
        lines.append("| File | Location | Duration | Peak | Category | Also fits |")
        lines.append("|---|---|---|---|---|---|")
        for f in sorted(files, key=lambda x: (x["duration_s"] or 0)):
            dur = f"{f['duration_s']:.2f}s" if f["duration_s"] is not None else "—"
            peak = f"{f['peak_dbfs']:+.1f} dB" if f["peak_dbfs"] is not None else "—"
            also = ", ".join(s for s in f["suggested_slots"] if s != slot) or "—"
            lines.append(
                f"| `{f['filename']}` | {f['location']} | {dur} | {peak} | "
                f"{f['category']} | {also} |"
            )
        lines.append("")

    dst.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not SFX_DIR.exists():
        log.error("SFX_DIR does not exist: %s", SFX_DIR)
        return 1

    # SFX_DIR resolves to assets/sfx/pack/. We want to scan the *parent* so we
    # cover both raw drops and the curated pack.
    sfx_root = SFX_DIR.parent if SFX_DIR.name == "pack" else SFX_DIR
    log.info("scanning %s", sfx_root)

    files = scan(sfx_root)

    index = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "root": sfx_root.as_posix(),
        "slot_definitions": SLOT_DEFINITIONS,
        "category_to_slots": CATEGORY_TO_SLOTS,
        "files": files,
    }

    index_path = sfx_root / "index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    log.info("wrote %s (%d files)", index_path, len(files))

    catalog_path = sfx_root / "CATALOG.md"
    write_catalog_md(index, catalog_path)
    log.info("wrote %s", catalog_path)

    # Print a short summary by slot.
    print()
    print("Slot coverage (first-pick suggestions):")
    counts: dict[str, int] = {}
    for f in files:
        target = f["suggested_slots"][0] if f["suggested_slots"] else "unmapped"
        counts[target] = counts.get(target, 0) + 1
    for slot in list(SLOT_DEFINITIONS) + ["unmapped"]:
        n = counts.get(slot, 0)
        print(f"  {slot:14s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
