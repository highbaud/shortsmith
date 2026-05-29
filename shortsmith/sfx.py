"""Post-render sound-effect overlay.

Given a rendered short + its clip spec (callouts, hook) + aligned words.json,
compute when each SFX should fire, then ffmpeg-mix the hits over the existing
speech audio. Non-destructive: writes a sibling file, never the input.

Triggers
--------
Structural (tied to on-screen motion, deterministic):
  * hook-impact  at t≈0           (opening slam)
  * swipe-in     at each callout local_start
  * swipe-out    at each callout local_start + duration

Semantic (tied to speech; "sparing" mode by default):
  * cash-register on the first money-word in the clip
  * wrong-answer on the first negative-outcome word in the clip
                 (lose / crashed / scam / rugged / bankrupt / ...)
  * ding         on each bigstat callout whose text has a number / $

Any SFX slot with no file present in SFX_DIR is silently skipped, so partial
sound packs work fine.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import SFX_DIR, Config

log = logging.getLogger(__name__)

SLOTS = ("swipe-in", "swipe-out", "hook-impact", "cash-register",
         "wrong-answer", "ding", "whoosh")
_AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac")
_NUMBER_RE = re.compile(r"[\$£€]|\d")


@dataclass
class SfxEvent:
    t: float          # seconds into the final clip
    slot: str         # which SFX slot
    gain: float = 1.0


def load_sfx_map(sfx_dir: Path = SFX_DIR) -> dict[str, list[Path]]:
    """Map slot name -> list of variant file paths.

    Prefers a curated, level-normalized pack at <sfx_dir>/pack/pack.json:
        {"swipe-in": ["short-whoosh.wav", "short-whoosh2.wav"], ...}
    (paths relative to the pack/ dir). Falls back to slot-named files directly
    in sfx_dir (e.g. swipe-in.wav) when no pack manifest is present.
    """
    pack_dir = sfx_dir / "pack"
    manifest = pack_dir / "pack.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            out: dict[str, list[Path]] = {}
            for slot, files in data.items():
                paths = [pack_dir / f for f in files if (pack_dir / f).exists()]
                if paths:
                    out[slot] = paths
            if out:
                return out
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: slot-named files directly in sfx_dir.
    out = {}
    if not sfx_dir.exists():
        return out
    for slot in SLOTS:
        for ext in _AUDIO_EXTS:
            p = sfx_dir / f"{slot}{ext}"
            if p.exists():
                out[slot] = [p]
                break
    return out


def _norm_word(w: dict) -> str:
    return (w.get("text") or w.get("word") or "").strip().lower().rstrip(".,!?:;\"')")


def plan_events(clip: dict, words: list[dict], sfx_map: dict[str, list[Path]],
                cfg: Config, clip_duration: float) -> list[SfxEvent]:
    """Compute the ordered SFX events for one clip (no file I/O)."""
    events: list[SfxEvent] = []
    have = sfx_map.__contains__
    slot_gain = getattr(cfg, "sfx_slot_gain", {}) or {}

    def g(slot: str) -> float:
        return float(slot_gain.get(slot, 1.0))

    # --- Hook impact ---
    if have("hook-impact") and clip.get("hook") and str(clip["hook"].get("text", "")).strip():
        events.append(SfxEvent(0.05, "hook-impact", g("hook-impact")))

    # --- Callout swipes + bigstat dings ---
    callouts = clip.get("callouts") or []
    semantic_on = (getattr(cfg, "sfx_semantic_mode", "sparing") or "sparing").lower() != "off"
    swipe_out_on = bool(getattr(cfg, "sfx_swipe_out", False))
    for co in callouts:
        try:
            ls = float(co["local_start"])
            dur = float(co.get("duration", 2.0))
        except (KeyError, ValueError, TypeError):
            continue
        ls = max(0.0, min(ls, clip_duration))
        # A bigstat number callout gets a ding as ITS entrance accent — so we
        # skip the swipe-in for that one (otherwise swipe + ding stack and
        # sound cluttered). Regular callouts get the swipe.
        is_ding = (semantic_on and have("ding")
                   and (co.get("style") or "").lower() == "bigstat"
                   and _NUMBER_RE.search(str(co.get("text", ""))))
        if is_ding:
            events.append(SfxEvent(ls, "ding", g("ding")))
        elif have("swipe-in"):
            events.append(SfxEvent(ls, "swipe-in", g("swipe-in")))
        if swipe_out_on and have("swipe-out"):
            events.append(SfxEvent(min(ls + dur, clip_duration), "swipe-out", g("swipe-out")))

    # --- Money mention -> cash register ---
    if semantic_on and have("cash-register") and words:
        money = {m.lower() for m in getattr(cfg, "money_keywords", [])}
        mode = (getattr(cfg, "sfx_semantic_mode", "sparing") or "sparing").lower()
        for w in words:
            stem = _norm_word(w)
            is_money = stem in money or _NUMBER_RE.search(stem) is not None and "$" in stem
            # also catch "$1.30", "$293", "30k" etc.
            if not is_money and ("$" in stem or re.fullmatch(r"\d+[km]?", stem)):
                is_money = True
            if is_money:
                t = float(w.get("start", 0.0))
                if 0.0 <= t <= clip_duration:
                    events.append(SfxEvent(t, "cash-register", g("cash-register")))
                    if mode == "sparing":
                        break  # only the first money mention

    # --- Negative outcome -> wrong-answer ---
    # Fires on the first (or every, depending on mode) negative-outcome word in
    # the speech. Examples: "crashed", "scammed", "rugged", "bankrupt",
    # "wrong". A sparring/quiz-show "err" cue that says "the bad thing
    # happened" — opt-in by populating negative_keywords + dropping
    # wrong-answer files into the pack.
    if semantic_on and have("wrong-answer") and words:
        negatives = {m.lower() for m in getattr(cfg, "negative_keywords", [])}
        mode = (getattr(cfg, "sfx_semantic_mode", "sparing") or "sparing").lower()
        if negatives:
            for w in words:
                stem = _norm_word(w)
                if stem in negatives:
                    t = float(w.get("start", 0.0))
                    if 0.0 <= t <= clip_duration:
                        events.append(SfxEvent(t, "wrong-answer", g("wrong-answer")))
                        if mode == "sparing":
                            break  # only the first negative word

    # De-dup near-identical (same slot within 80ms) and sort by time.
    events.sort(key=lambda e: (e.t, e.slot))
    deduped: list[SfxEvent] = []
    for e in events:
        if deduped and deduped[-1].slot == e.slot and abs(deduped[-1].t - e.t) < 0.08:
            continue
        deduped.append(e)
    return deduped


def apply_sfx(final_mp4: Path, events: list[SfxEvent], sfx_map: dict[str, list[Path]],
              out_mp4: Path, cfg: Config) -> bool:
    """Mix the planned events over final_mp4's audio -> out_mp4. Returns True
    on success. If there are no events, copies the input through unchanged.

    When a slot has multiple variant files, successive uses of that slot rotate
    through the variants so repeated swipes don't sound identical.
    """
    if not events:
        # Nothing to add — still produce out_mp4 so callers have a uniform path.
        subprocess.run(["ffmpeg", "-y", "-i", str(final_mp4), "-c", "copy",
                        "-movflags", "+faststart", str(out_mp4)],
                       check=True, capture_output=True)
        return True

    gain = float(getattr(cfg, "sfx_gain", 0.7))
    limit = float(getattr(cfg, "sfx_limit", 0.97))

    inputs = ["-i", str(final_mp4)]
    filters = []
    mix_labels = ["[0:a]"]
    slot_use: dict[str, int] = {}
    in_idx = 0  # ffmpeg input index of the most recently added SFX (0 = base video)
    for ev in events:
        variants = sfx_map.get(ev.slot) or []
        if not variants:
            continue
        k = slot_use.get(ev.slot, 0)
        chosen = variants[k % len(variants)]
        slot_use[ev.slot] = k + 1
        in_idx += 1
        inputs += ["-i", str(chosen)]
        delay_ms = int(round(ev.t * 1000))
        g = gain * ev.gain
        filters.append(f"[{in_idx}:a]adelay={delay_ms}:all=1,volume={g:.3f}[s{in_idx}]")
        mix_labels.append(f"[s{in_idx}]")

    n = in_idx + 1  # base + number of SFX actually added
    if n == 1:
        # No usable SFX inputs — copy through.
        subprocess.run(["ffmpeg", "-y", "-i", str(final_mp4), "-c", "copy",
                        "-movflags", "+faststart", str(out_mp4)],
                       check=True, capture_output=True)
        return True
    mix = (
        "".join(mix_labels)
        + f"amix=inputs={n}:normalize=0:dropout_transition=0,"
        + f"alimiter=limit={limit}[aout]"
    )
    filter_complex = ";".join(filters + [mix])

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("SFX mix failed for %s: %s", final_mp4.name, (proc.stderr or "")[-400:])
        return False
    return True
