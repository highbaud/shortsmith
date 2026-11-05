"""Visual transitions overlay (Remotion layer).

Plans when each VFX effect (glare / zoom-punch / flash) should fire, in
the same trigger taxonomy as the audio SFX (sfx.py). Pure logic — no I/O.
The Remotion render reads the resulting event list as JSON props and
draws the effects in CSS/React.

Triggers (mirrors sfx.SLOTS, but only the 4 high-impact slots fire VFX
by default; structural swipes don't pile on visual flash):
  * hook-impact   at t≈0     (opening slam)
  * ding          at each bigstat $ callout
  * cash-register on the first money-word in the clip
  * wrong-answer  on the first negative-outcome word in the clip

Each trigger can fire one or more effects, configured per slot via
Config.vfx_triggers. Default mapping:
  hook-impact   -> glare + zoom-punch + flash       (big slam)
  ding          -> glare                            (gold accent)
  cash-register -> glare + flash                    (gold money pop)
  wrong-answer  -> flash + zoom-punch               (red error punch)

Disable wholesale via cfg.vfx_enabled = False (or SHORTSMITH_VFX=off).
"""
from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

from .config import Config
from .records import ClipSpec, TranscriptWord
from .sfx import is_bigstat_callout, semantic_word_cues

log = logging.getLogger(__name__)

# Per-effect default durations (ms). Tuned so flash is shortest (punctuates),
# zoom-punch in the middle (subtle body), glare longest (cinematic sweep).
DEFAULT_DURATIONS_MS: dict[str, int] = {
    "glare":      280,
    "zoom-punch": 220,
    "flash":       90,
}

EFFECTS = tuple(DEFAULT_DURATIONS_MS.keys())


@dataclass
class VFXEvent:
    t: float                  # seconds into the final clip
    effect: str               # 'glare' | 'zoom-punch' | 'flash'
    color: str = "#ffffff"    # hex tint
    intensity: float = 1.0    # Nonnegative multiplier; values above 1 add emphasis.
    duration_ms: int = 280

    def to_props(self) -> dict[str, str | int | float]:
        """Shape matches the JSON the Remotion VFX layer expects."""
        return {
            "t": float(self.t),
            "effect": self.effect,
            "color": self.color,
            "intensity": float(self.intensity),
            "durationMs": int(self.duration_ms),
        }


def plan_vfx_events(clip: ClipSpec, words: Sequence[TranscriptWord], cfg: Config,
                    clip_duration: float) -> list[VFXEvent]:
    """Return the ordered visual cues for one clip without file access."""
    if not cfg.vfx_enabled:
        return []

    triggers = cfg.vfx_triggers or {}
    colors = cfg.vfx_colors or {}
    intensity = float(cfg.vfx_intensity)

    events: list[VFXEvent] = []

    def emit(t: float, slot: str) -> None:
        """Append one event per effect bound to `slot`."""
        if t < 0.0 or t > clip_duration:
            return
        effects = triggers.get(slot, []) or []
        color = colors.get(slot, "#ffffff")
        for fx in effects:
            if fx not in DEFAULT_DURATIONS_MS:
                continue  # unknown effect name — skip silently
            events.append(VFXEvent(
                t=t, effect=fx, color=color,
                intensity=intensity,
                duration_ms=DEFAULT_DURATIONS_MS[fx],
            ))

    hook = clip.get("hook")
    if isinstance(hook, dict) and str(hook.get("text", "")).strip():
        emit(0.05, "hook-impact")

    semantic_on = (cfg.sfx_semantic_mode
                   or "sparing").lower() != "off"

    if semantic_on:
        for co in (clip.get("callouts") or []):
            try:
                ls = float(co["local_start"])
                if not math.isfinite(ls):
                    raise ValueError("callout timing must be finite")
            except (KeyError, ValueError, TypeError) as exc:
                log.warning("Skipping VFX callout with invalid timing: %s", exc)
                continue
            if is_bigstat_callout(co):
                emit(max(0.0, min(ls, clip_duration)), "ding")

    for time, slot in semantic_word_cues(words, cfg, clip_duration):
        emit(time, slot)

    # Sort by time. We DO NOT dedup across effects — a hook frame
    # legitimately runs glare + zoom-punch + flash simultaneously.
    events.sort(key=lambda e: (e.t, e.effect))
    return events
