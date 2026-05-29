"""Curate + level-normalize a tasteful SFX pack from the raw drop folder.

Reads the raw sound files the user dropped in assets/sfx/, selects a curated
subset (CURATION below), normalizes each to a consistent peak (-9 dBFS),
trims leading silence so the transient lands on the beat, resamples to 48k
stereo, and writes them into assets/sfx/pack/ plus a pack.json manifest that
sfx.load_sfx_map() consumes (with variant rotation).

Re-run any time you add/swap raw files or change the curation.

Usage:
    uv run python scripts/build_sfx_pack.py
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

from shortsmith.config import SFX_DIR

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("build_sfx")

TARGET_PEAK_DB = -9.0   # normalize every one-shot to this peak

# slot -> list of (raw filename in SFX_DIR, output variant basename)
# Order matters: variants rotate in this order across repeated uses.
#
# Curated using `scripts/build_sfx_index.py` (see assets/sfx/CATALOG.md).
# Picks favor clean source peaks (-0 to -9 dBFS), tight transients, and
# enough variety per slot that the rotation feels non-repetitive across a
# multi-callout clip without ever crowding the speech bed.
CURATION: dict[str, list[tuple[str, str]]] = {
    # swipe-in: callout slam-ins. Whip cracks + short whooshes — tight 0.2–0.4s
    # transients. Rotating between whoosh + whip families keeps long batches
    # from feeling stamped out.
    "swipe-in": [
        ("Short Whoosh.wav",  "swipe-in-1.wav"),
        ("Short Whoosh2.wav", "swipe-in-2.wav"),
        ("Short Whoosh3.wav", "swipe-in-3.wav"),
        ("whip whoosh.wav",   "swipe-in-4.wav"),
        ("whip3.wav",         "swipe-in-5.wav"),
        ("whip6.wav",         "swipe-in-6.wav"),
        ("whip4.wav",         "swipe-in-7.wav"),
    ],
    # swipe-out: softer / shorter than swipe-in. Reverse-feel whooshes + a
    # cleaner whip alternative.
    "swipe-out": [
        ("Short Whoosh3.wav", "swipe-out-1.wav"),
        ("Short Whoosh.wav",  "swipe-out-2.wav"),
        ("whip5.mp3",         "swipe-out-3.wav"),
    ],
    # hook-impact: big at t=0. Bodied camera-whoosh hits + a longer riser
    # variant for clips that breathe before the first beat lands.
    "hook-impact": [
        ("CameraWhoosh1.wav",         "hook-impact-1.wav"),
        ("CameraWhoosh.wav",          "hook-impact-2.wav"),
        ("Long Whoosh.wav",           "hook-impact-3.wav"),
        ("Riser to Notification.wav", "hook-impact-4.wav"),
    ],
    # cash-register: iconic kaching, single sample is plenty (only fires once
    # per clip on the first money word).
    "cash-register": [
        ("cash register kaching.mp3", "cash-register-1.wav"),
    ],
    # ding: bigstat number reveal. Mix of pitched bells + bright UI taps so a
    # clip with 3 bigstats doesn't ring the same bell three times.
    "ding": [
        ("bell ding1.wav",  "ding-1.wav"),
        ("anime shine.mp3", "ding-2.wav"),
        ("beep1.wav",       "ding-3.wav"),
        ("pop1.wav",        "ding-4.wav"),
        ("icon_03.wav",     "ding-5.wav"),
        ("icon_14.wav",     "ding-6.wav"),
    ],
    # whoosh: generic transition fallback. Longer / smoother than swipe-in.
    "whoosh": [
        ("CameraWhoosh.wav",      "whoosh-1.wav"),
        ("Click to Whoosh.wav",   "whoosh-2.wav"),
        ("Long Whoosh.wav",       "whoosh-3.wav"),
    ],
}


def measure_peak_db(p: Path) -> float | None:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(p), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", out.stderr)
    return float(m.group(1)) if m else None


def normalize_one(src: Path, dst: Path) -> bool:
    peak = measure_peak_db(src)
    gain_db = (TARGET_PEAK_DB - peak) if peak is not None else 0.0
    # Trim leading silence, apply gain, standardize to 48k stereo.
    af = (
        "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.01,"
        f"volume={gain_db:.2f}dB,"
        "aresample=48000"
    )
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-i", str(src),
         "-af", af, "-ac", "2", "-ar", "48000", str(dst)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not dst.exists():
        log.error("normalize failed for %s: %s", src.name, (proc.stderr or "")[-300:])
        return False
    return True


def main() -> int:
    pack_dir = SFX_DIR / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, list[str]] = {}
    missing: list[str] = []
    for slot, variants in CURATION.items():
        outs: list[str] = []
        for raw_name, out_name in variants:
            src = SFX_DIR / raw_name
            if not src.exists():
                missing.append(raw_name)
                continue
            dst = pack_dir / out_name
            if normalize_one(src, dst):
                outs.append(out_name)
                log.info("%-13s <- %-26s (peak->%.0f dB)", slot, raw_name, TARGET_PEAK_DB)
        if outs:
            manifest[slot] = outs

    (pack_dir / "pack.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote pack.json with slots: %s", ", ".join(manifest))
    if missing:
        log.warning("Raw files referenced but not found (skipped): %s", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
