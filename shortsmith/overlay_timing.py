"""Keep authored hook and callout times consistent across renderers."""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping

log = logging.getLogger(__name__)


def hook_duration(hook: Mapping[str, object], clip_duration: float) -> float:
    raw_duration = hook.get("duration", 2.6)
    try:
        if not isinstance(raw_duration, (str, int, float)):
            raise TypeError("duration must be numeric")
        duration = float(raw_duration)
        if not math.isfinite(duration):
            raise ValueError("nonfinite duration")
    except (TypeError, ValueError, OverflowError):
        log.warning("Hook duration %r is not a number; using the 2.6s default", raw_duration)
        duration = 2.6
    return max(1.5, min(duration, max(2.0, clip_duration * 0.30)))


def callout_timing(callout: object, clip_duration: float) -> tuple[float, float] | None:
    if not isinstance(callout, dict):
        return None
    try:
        start = float(callout["local_start"])
        duration = float(callout.get("duration", 2.0))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(start) or not math.isfinite(duration):
        return None
    start = max(0.0, min(start, max(0.0, clip_duration - 0.5)))
    duration = max(0.6, min(duration, clip_duration - start))
    return start, duration
