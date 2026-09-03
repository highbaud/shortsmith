"""Shared ffprobe helpers.

Three pipeline steps need a clip's duration, and each carried its own private
copy of the same ffprobe argv. One copy keeps the flags, and the explicit utf-8
decoding a Windows console needs, in a single place.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def probe_duration(path: Path) -> float:
    """Container duration in seconds.

    Raises `subprocess.CalledProcessError` when ffprobe fails and `ValueError`
    when it prints something that is not a number, so a missing or truncated
    file surfaces instead of turning into a plausible-looking zero.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return float(out.stdout.strip())
