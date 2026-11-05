"""Phrase-grouping for karaoke captions.

Ported from xrp-edit/build_captions.py — the gap+word-count grouping logic.

Output shape matches Hyperframes captions.html SEGMENTS array:
    [{"words": [{"word": "Hello,", "start": 0.0, "end": 0.3}, ...]}, ...]

Note: we keep original casing and punctuation (the Hyperframes template renders
text as-is). The xrp-edit pipeline UPPERCASED everything; that's a style choice
the user can do in Hyperframes by tweaking the captions.html CSS later.
"""
from __future__ import annotations

import re

from .config import Config
from .records import Segment, TranscriptWord

_HASHTAG_RE = re.compile(r"[ \t]*#\w[\w-]*")
_DASH_CHARS = "\u2014\u2013\u2012\u2015\ufffd"
_RANGE_DASH_RE = re.compile(rf"(?<=\d)\s*[{_DASH_CHARS}]\s*(?=\d)")
_PROSE_DASH_RE = re.compile(rf"\s*[{_DASH_CHARS}]\s*")


def normalize_dashes(text: str) -> str:
    """Replace dash characters with a hyphen in numeric ranges or a comma in prose."""
    if not text:
        return text
    text = _RANGE_DASH_RE.sub("-", text)
    text = _PROSE_DASH_RE.sub(", ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"(^|\n)\s*,\s*", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def strip_hashtags(text: str) -> str:
    """Remove hashtag tokens and tidy the resulting spaces and blank lines."""
    cleaned = _HASHTAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def group_into_segments(
    words: list[TranscriptWord],
    *,
    gap: float | None = None,
    max_words: int | None = None,
) -> list[dict]:
    """Group a flat word list into karaoke-friendly segments.

    Each `word` dict must have keys {"text" | "word", "start", "end"}.
    Output uses Hyperframes' "word" key (not "text") to match captions.html SEGMENTS.
    """
    g = gap if gap is not None else Config.phrase_gap_seconds
    mw = max_words if max_words is not None else Config.phrase_max_words

    segments: list[dict] = []
    current: list[dict] = []
    prev_end = 0.0

    for w in words:
        text = (w.get("text") or w.get("word") or "").strip()
        if not text:
            continue
        start = float(w["start"])
        end = float(w["end"])
        word_gap = start - prev_end

        if current and (word_gap > g or len(current) >= mw):
            segments.append({"words": current})
            current = []

        current.append({"word": text, "start": round(start, 3), "end": round(end, 3)})
        prev_end = end

    if current:
        segments.append({"words": current})

    return segments


def shift_words_to_zero(words: list[TranscriptWord], reference_start: float) -> list[TranscriptWord]:
    """Re-base word timings so the first word starts near t=0.

    Used after cutting a clip out of a long source: the source timestamps don't
    line up with the cut clip's local timeline.
    """
    out: list[TranscriptWord] = []
    for w in words:
        out.append({
            "text": w.get("text") or w.get("word") or "",
            "start": round(float(w["start"]) - reference_start, 3),
            "end": round(float(w["end"]) - reference_start, 3),
        })
    return out


def slice_words_for_segments(
    source_words: list[TranscriptWord],
    segments: list[Segment],
    *,
    xfade_seconds: float = 0.0,
    segment_durations: list[float] | None = None,
) -> list[TranscriptWord]:
    """Slice source-video transcript to a clip's local timeline.

    `segments` is the list of (source_start, source_end) pairs that the cut
    step concatenated (in order — may be physically reordered). For each
    segment, take all source words that fall ENTIRELY within (start, end),
    then re-time them to the local clip timeline (segment 0 starts at t=0,
    segment 1 starts after the measured first duration minus its crossfade).

    Words that straddle a segment boundary are dropped — keeping them would
    create overlap with the next segment's first word.
    """
    out: list[TranscriptWord] = []
    cumulative_offset = 0.0
    if segment_durations is not None and len(segment_durations) != len(segments):
        raise ValueError("segment durations must match the cut segments")
    for index, seg in enumerate(segments):
        seg_start, seg_end = float(seg[0]), float(seg[1])
        seg_duration = (segment_durations[index] if segment_durations is not None
                        else seg_end - seg_start)
        for w in source_words:
            ws = float(w["start"])
            we = float(w["end"])
            # Require the word to be fully inside this segment
            if ws >= seg_start and we <= min(seg_end, seg_start + seg_duration):
                local_start = (ws - seg_start) + cumulative_offset
                local_end = (we - seg_start) + cumulative_offset
                out.append({
                    "text": (w.get("text") or w.get("word") or "").strip(),
                    "start": round(local_start, 3),
                    "end": round(local_end, 3),
                })
        cumulative_offset += seg_duration - (xfade_seconds if index < len(segments) - 1 else 0)
    return out
