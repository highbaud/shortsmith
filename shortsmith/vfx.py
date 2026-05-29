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

import re
from dataclasses import dataclass

from .config import Config
from .sfx import _NUMBER_RE, _norm_word

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
    intensity: float = 1.0    # 0..1 multiplier on opacity / scale-bump
    duration_ms: int = 280

    def to_props(self) -> dict:
        """Shape matches the JSON the Remotion VFX layer expects."""
        return {
            "t": float(self.t),
            "effect": self.effect,
            "color": self.color,
            "intensity": float(self.intensity),
            "durationMs": int(self.duration_ms),
        }


def plan_vfx_events(clip: dict, words: list[dict], cfg: Config,
                    clip_duration: float) -> list[VFXEvent]:
    """Compute the ordered VFX events for one clip (no file I/O).

    Mirrors the SFX trigger logic in sfx.plan_events. Same 4 slots that
    fire semantic SFX cues also fire visual cues — they read as one
    coordinated "punctuation" beat.
    """
    if not getattr(cfg, "vfx_enabled", True):
        return []

    triggers: dict[str, list[str]] = getattr(cfg, "vfx_triggers", {}) or {}
    colors: dict[str, str] = getattr(cfg, "vfx_colors", {}) or {}
    intensity = float(getattr(cfg, "vfx_intensity", 1.0))

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

    # --- Hook impact ---
    if clip.get("hook") and str(clip["hook"].get("text", "")).strip():
        emit(0.05, "hook-impact")

    semantic_on = (getattr(cfg, "sfx_semantic_mode", "sparing")
                   or "sparing").lower() != "off"

    # --- Bigstat $ callouts -> ding ---
    if semantic_on:
        for co in (clip.get("callouts") or []):
            try:
                ls = float(co["local_start"])
            except (KeyError, ValueError, TypeError):
                continue
            is_ding = ((co.get("style") or "").lower() == "bigstat"
                       and _NUMBER_RE.search(str(co.get("text", ""))))
            if is_ding:
                emit(max(0.0, min(ls, clip_duration)), "ding")

    # --- Money mention -> cash-register ---
    if semantic_on and words:
        money = {m.lower() for m in getattr(cfg, "money_keywords", [])}
        mode = (getattr(cfg, "sfx_semantic_mode", "sparing") or "sparing").lower()
        for w in words:
            stem = _norm_word(w)
            is_money = stem in money or _NUMBER_RE.search(stem) is not None and "$" in stem
            if not is_money and ("$" in stem or re.fullmatch(r"\d+[km]?", stem)):
                is_money = True
            if is_money:
                t = float(w.get("start", 0.0))
                emit(t, "cash-register")
                if mode == "sparing":
                    break

    # --- Negative outcome -> wrong-answer ---
    if semantic_on and words:
        negatives = {m.lower() for m in getattr(cfg, "negative_keywords", [])}
        mode = (getattr(cfg, "sfx_semantic_mode", "sparing") or "sparing").lower()
        if negatives:
            for w in words:
                stem = _norm_word(w)
                if stem in negatives:
                    t = float(w.get("start", 0.0))
                    emit(t, "wrong-answer")
                    if mode == "sparing":
                        break

    # Sort by time. We DO NOT dedup across effects — a hook frame
    # legitimately runs glare + zoom-punch + flash simultaneously.
    events.sort(key=lambda e: (e.t, e.effect))
    return events
