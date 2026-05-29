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
    # swipe-in: callout slam-ins. Whip cracks + short whooshes — tight
    # 0.2–0.6s transients. Maxed out for rotation: every clean whip + every
    # short whoosh goes in. The mixer cycles through variants so a 6-callout
    # clip never repeats the same hit.
    "swipe-in": [
        ("Short Whoosh.wav",  "swipe-in-01.wav"),
        ("Short Whoosh2.wav", "swipe-in-02.wav"),
        ("Short Whoosh3.wav", "swipe-in-03.wav"),
        ("whip whoosh.wav",   "swipe-in-04.wav"),
        ("whip1.wav",         "swipe-in-05.wav"),
        ("whip3.wav",         "swipe-in-06.wav"),
        ("whip4.wav",         "swipe-in-07.wav"),
        ("whip6.wav",         "swipe-in-08.wav"),
        ("whip7.wav",         "swipe-in-09.wav"),
        ("whip8.wav",         "swipe-in-10.wav"),
        ("whipdouble.wav",    "swipe-in-11.wav"),
    ],
    # swipe-out: softer / shorter than swipe-in. Reverse-feel whooshes + a
    # few clean whip alternatives.
    "swipe-out": [
        ("Short Whoosh3.wav", "swipe-out-1.wav"),
        ("Short Whoosh.wav",  "swipe-out-2.wav"),
        ("whip2.mp3",         "swipe-out-3.wav"),
        ("whip5.mp3",         "swipe-out-4.wav"),
    ],
    # hook-impact: big at t=0. Bodied camera-whoosh hits, a longer riser, a
    # soft 1.26s icon body, and a 2.4s pop for a brighter-feel impact body.
    "hook-impact": [
        ("CameraWhoosh1.wav",         "hook-impact-1.wav"),
        ("CameraWhoosh.wav",          "hook-impact-2.wav"),
        ("Long Whoosh.wav",           "hook-impact-3.wav"),
        ("Riser to Notification.wav", "hook-impact-4.wav"),
        ("icon_06.wav",               "hook-impact-5.wav"),
        ("pop5.wav",                  "hook-impact-6.wav"),
    ],
    # cash-register: iconic kaching. Single sample is plenty (only fires once
    # per clip on the first money word).
    "cash-register": [
        ("cash register kaching.mp3", "cash-register-1.wav"),
    ],
    # wrong-answer: negative-outcome cue. Fires once per clip on the first
    # negative-keyword word in the speech (lose / crashed / scammed / etc.).
    # Quiz-show buzz feel — punctuates "the bad thing happened" without
    # piling on. Two distinct error textures so back-to-back shorts in a
    # batch don't ring the same buzz.
    "wrong-answer": [
        ("Errror.wav",         "wrong-answer-1.wav"),
        ("windows error.mp3",  "wrong-answer-2.wav"),
    ],
    # ding: bigstat number reveal. Three texture families rotate together —
    # pitched bells (bell ding, anime shine), bright pops (pop1/pop2),
    # icon-style UI taps (icon_03/07/08/10/11/13/14/15), and dry mouse-
    # click taps. Variant rotation means a 4-bigstat clip cycles 4 distinct
    # alert sounds; large pool keeps long batches non-repetitive.
    "ding": [
        ("bell ding1.wav",        "ding-01.wav"),
        ("anime shine.mp3",       "ding-02.wav"),
        ("beep1.wav",             "ding-03.wav"),
        ("pop1.wav",              "ding-04.wav"),
        ("pop2.wav",              "ding-05.wav"),
        ("icon_03.wav",           "ding-06.wav"),
        ("icon_07.wav",           "ding-07.wav"),
        ("icon_08.wav",           "ding-08.wav"),
        ("icon_10.wav",           "ding-09.wav"),
        ("icon_11.wav",           "ding-10.wav"),
        ("icon_13.wav",           "ding-11.wav"),
        ("icon_14.wav",           "ding-12.wav"),
        ("icon_15.wav",           "ding-13.wav"),
        ("mouse click (3).wav",   "ding-14.wav"),
        ("mouse click (4).wav",   "ding-15.wav"),
        ("mouse click (7).wav",   "ding-16.wav"),
        ("mouse click (8).wav",   "ding-17.wav"),
    ],
    # whoosh: generic transition fallback. Longer / smoother than swipe-in.
    # fastwhoosh is at -29 dBFS in source so it gets heavily gained up;
    # keeping it because the texture is still clean post-normalize.
    "whoosh": [
        ("CameraWhoosh.wav",          "whoosh-1.wav"),
        ("Click to Whoosh.wav",       "whoosh-2.wav"),
        ("Long Whoosh.wav",           "whoosh-3.wav"),
        ("Digital Small whoosh.wav",  "whoosh-4.wav"),
        ("fastwhoosh.wav",            "whoosh-5.wav"),
    ],
    # Intentionally NOT in any slot (with reasons — audit on demand):
    # - gong.mp3 (7.64s)          body dominates the hook bed; needs a
    #                             dedicated "stinger" slot or auto-trim
    #                             (future: end-of-clip cue)
    #
    # The previous round of exclusions (low-peak whips/icons and redundant
    # mouse-clicks) have been deleted from assets/sfx/. Errror.wav and
    # windows error.mp3 now feed the wrong-answer slot above.
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
