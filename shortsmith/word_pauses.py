"""Move Whisper word edges off measured pauses.

faster-whisper word timestamps are nearly gapless: a word's span often runs
across the pause before or after it. The boundary snapper and the silence
cleaner can only see pauses as gaps between words, so they cut inside speech.
This measures the audio in 10 ms frames and trims each word to its sounding
part, which turns every real pause back into a word gap.

Method and thresholds follow editonny's precise_edges helper.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

from ._process import resolve_executable
from .records import TranscriptWord

log = logging.getLogger(__name__)

SR = 16000
HOP = 0.010
QUIET_DB = 10.0         # a frame below floor + this is silence
PAUSE_MIN_S = 0.07      # shorter dips are stop consonants, not pauses
MIN_DYNAMIC_DB = 20.0   # p90 - floor below this: no silence to measure against


def measure_levels(path: Path) -> tuple[np.ndarray, float] | None:
    """(10 ms dB levels, noise floor) of the audio, or None when it cannot be measured."""
    try:
        raw = subprocess.run(
            [resolve_executable("ffmpeg"), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
             "-ar", str(SR), "-f", "f32le", "-"],
            check=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        log.warning("Word pauses: could not decode audio of %s (%s); word times unchanged",
                    path.name, exc)
        return None
    n = int(HOP * SR)
    samples = np.frombuffer(raw, np.float32)
    frames = samples[: len(samples) // n * n].reshape(-1, n)
    if not len(frames):
        return None
    db = 10.0 * np.log10(np.mean(frames * frames, axis=1, dtype=np.float64) + 1e-12)
    floor, top = float(np.percentile(db, 10)), float(np.percentile(db, 90))
    if top - floor < MIN_DYNAMIC_DB:
        log.warning("Word pauses: %s has no measurable silence (floor %.0f dB, speech %.0f dB); "
                    "word times unchanged", path.name, floor, top)
        return None
    return db, floor


def repair_words(words: list[TranscriptWord], db: np.ndarray, floor: float) -> list[TranscriptWord]:
    """Return new words with their spans moved off measured pauses.

    Edge silence is trimmed. A word split by one pause keeps its louder side and
    hands the other side to the neighbor it touches, so all sound stays inside
    some word and every gap this opens is silent.
    """
    quiet = db < floor + QUIET_DB
    need = int(round(PAUSE_MIN_S / HOP))
    spans = [[float(w["start"]), float(w["end"])] for w in words]
    for i, (start, end) in enumerate(spans):
        i0, i1 = int(round(start / HOP)), min(int(round(end / HOP)), len(db))
        q = quiet[i0:i1]
        parts = _sound_parts(q, need)
        if not parts:
            continue
        keep = parts
        if len(parts) == 2:
            first, last = parts
            louder = 0 if (~q[slice(*first)]).sum() >= (~q[slice(*last)]).sum() else 1
            if louder == 1 and i > 0 and first[0] == 0 and abs(start - spans[i - 1][1]) <= HOP:
                spans[i - 1][1] = (i0 + first[1]) * HOP
                keep = [last]
            elif (louder == 0 and i + 1 < len(spans) and last[1] == q.size
                  and abs(spans[i + 1][0] - end) <= HOP):
                spans[i + 1][0] = (i0 + last[0]) * HOP
                keep = [first]
        new_start, new_end = (i0 + keep[0][0]) * HOP, (i0 + keep[-1][1]) * HOP
        if i + 1 < len(spans):  # starts must stay in order for overlapping input words
            new_start = min(new_start, spans[i + 1][0])
        spans[i] = [max(new_start, start), min(new_end, end)]
    out: list[TranscriptWord] = []
    for word, (start, end) in zip(words, spans, strict=True):
        start, end = round(start, 3), round(end, 3)
        same = (start, end) == (float(word["start"]), float(word["end"]))
        out.append(word if same or end <= start else {**word, "start": start, "end": end})
    return out


def _sound_parts(quiet: np.ndarray, need: int) -> list[tuple[int, int]]:
    """Sounding (first, last+1) frame stretches split at pauses of `need`+ frames.

    Edge silence shorter than a pause is kept. Empty when the span has no sound.
    """
    loud = np.flatnonzero(~quiet)
    if not loud.size:
        return []
    breaks = np.flatnonzero(np.diff(loud) > need)
    starts = np.concatenate(([loud[0]], loud[breaks + 1]))
    ends = np.concatenate((loud[breaks], [loud[-1]])) + 1
    parts = [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]
    if parts[0][0] < need:
        parts[0] = (0, parts[0][1])
    if quiet.size - parts[-1][1] < need:
        parts[-1] = (parts[-1][0], quiet.size)
    return parts
