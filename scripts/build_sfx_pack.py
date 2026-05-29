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
CURATION: dict[str, list[tuple[str, str]]] = {
    "swipe-in": [
        ("Short Whoosh.wav",  "swipe-in-1.wav"),
        ("Short Whoosh2.wav", "swipe-in-2.wav"),
        ("Short Whoosh3.wav", "swipe-in-3.wav"),
        ("whip whoosh.wav",   "swipe-in-4.wav"),
    ],
    "swipe-out": [
        ("Short Whoosh3.wav", "swipe-out-1.wav"),
        ("Short Whoosh.wav",  "swipe-out-2.wav"),
    ],
    "hook-impact": [
        ("CameraWhoosh1.wav", "hook-impact-1.wav"),
    ],
    "cash-register": [
        ("cash register kaching.mp3", "cash-register-1.wav"),
    ],
    "ding": [
        ("bell ding1.wav",  "ding-1.wav"),
        ("anime shine.mp3", "ding-2.wav"),
    ],
    "whoosh": [
        ("CameraWhoosh.wav", "whoosh-1.wav"),
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
