"""Loudness normalization to a consistent integrated loudness (LUFS).

Two-pass ffmpeg `loudnorm`:
  Pass 1 measures integrated loudness / true peak / LRA / threshold.
  Pass 2 applies a linear gain using those measurements, hitting the target
  exactly without the pumping you get from single-pass dynamic loudnorm.

-14 LUFS integrated is the playback-normalization target TikTok / Instagram /
YouTube use for short-form, so clips land at platform reference instead of
being turned down (too hot) or scroll-past'd (too quiet).
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)


def build_measure_filter(cfg: Config) -> str:
    """loudnorm filter string for the measurement (analysis) pass."""
    return (
        f"loudnorm=I={cfg.loudness_target_lufs}:"
        f"TP={cfg.loudness_true_peak}:"
        f"LRA={cfg.loudness_range}:"
        f"print_format=json"
    )


def build_apply_filter(cfg: Config, measured: dict) -> str:
    """loudnorm filter string for the second (apply) pass, fed the measurements.

    `measured` is the JSON object ffmpeg prints in pass 1 (keys: input_i,
    input_tp, input_lra, input_thresh, target_offset).
    """
    return (
        f"loudnorm=I={cfg.loudness_target_lufs}:"
        f"TP={cfg.loudness_true_peak}:"
        f"LRA={cfg.loudness_range}:"
        f"measured_I={measured['input_i']}:"
        f"measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:"
        f"measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:"
        f"linear=true:print_format=summary"
    )


def _parse_loudnorm_json(stderr: str) -> dict | None:
    """Extract the trailing JSON object ffmpeg's loudnorm prints to stderr."""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(stderr[start:end + 1])
    except json.JSONDecodeError:
        return None


def loudnorm_two_pass(in_wav: Path, out_wav: Path, cfg: Config) -> bool:
    """Normalize `in_wav` -> `out_wav` at cfg.loudness_target_lufs. Returns
    True on success; on any failure copies input to output and returns False so
    the caller still has usable audio.
    """
    import shutil

    # Pass 1 — measure.
    measure = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(in_wav),
         "-af", build_measure_filter(cfg), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    measured = _parse_loudnorm_json(measure.stderr)
    if not measured:
        log.warning("loudnorm pass-1 measurement failed for %s; copying unnormalized",
                    in_wav.name)
        shutil.copy(in_wav, out_wav)
        return False

    # Pass 2 — apply.
    apply = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-i", str(in_wav),
         "-af", build_apply_filter(cfg, measured),
         "-ar", "48000", str(out_wav)],
        capture_output=True, text=True,
    )
    if apply.returncode != 0 or not out_wav.exists():
        log.warning("loudnorm pass-2 apply failed for %s; copying unnormalized",
                    in_wav.name)
        shutil.copy(in_wav, out_wav)
        return False
    return True
