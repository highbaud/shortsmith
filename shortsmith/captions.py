"""Phrase-grouping for karaoke captions.

Ported from xrp-edit/build_captions.py — the gap+word-count grouping logic.

Output shape matches Hyperframes captions.html SEGMENTS array:
    [{"words": [{"word": "Hello,", "start": 0.0, "end": 0.3}, ...]}, ...]

Note: we keep original casing and punctuation (the Hyperframes template renders
text as-is). The xrp-edit pipeline UPPERCASED everything; that's a style choice
the user can do in Hyperframes by tweaking the captions.html CSS later.
"""
from __future__ import annotations

from .config import Config


def group_into_segments(
    words: list[dict],
    *,
    gap: float | None = None,
    max_words: int | None = None,
    cfg: Config | None = None,
) -> list[dict]:
    """Group a flat word list into karaoke-friendly segments.

    Each `word` dict must have keys {"text" | "word", "start", "end"}.
    Output uses Hyperframes' "word" key (not "text") to match captions.html SEGMENTS.
    """
    if cfg is None:
        cfg = Config()
    g = gap if gap is not None else cfg.phrase_gap_seconds
    mw = max_words if max_words is not None else cfg.phrase_max_words

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


def shift_words_to_zero(words: list[dict], reference_start: float) -> list[dict]:
    """Re-base word timings so the first word starts near t=0.

    Used after cutting a clip out of a long source: the source timestamps don't
    line up with the cut clip's local timeline.
    """
    out = []
    for w in words:
        out.append({
            "text": w.get("text") or w.get("word"),
            "start": round(float(w["start"]) - reference_start, 3),
            "end": round(float(w["end"]) - reference_start, 3),
        })
    return out


def slice_words_for_segments(
    source_words: list[dict],
    segments: list[list[float]],
) -> list[dict]:
    """Slice source-video transcript to a clip's local timeline.

    `segments` is the list of (source_start, source_end) pairs that the cut
    step concatenated (in order — may be physically reordered). For each
    segment, take all source words that fall ENTIRELY within (start, end),
    then re-time them to the local clip timeline (segment 0 starts at t=0,
    segment 1 starts at t=sum_of_prior_segment_durations, etc.).

    Words that straddle a segment boundary are dropped — keeping them would
    create overlap with the next segment's first word.
    """
    out: list[dict] = []
    cumulative_offset = 0.0
    for seg in segments:
        seg_start, seg_end = float(seg[0]), float(seg[1])
        seg_duration = seg_end - seg_start
        for w in source_words:
            ws = float(w["start"])
            we = float(w["end"])
            # Require the word to be fully inside this segment
            if ws >= seg_start and we <= seg_end:
                local_start = (ws - seg_start) + cumulative_offset
                local_end = (we - seg_start) + cumulative_offset
                out.append({
                    "text": (w.get("text") or w.get("word") or "").strip(),
                    "start": round(local_start, 3),
                    "end": round(local_end, 3),
                })
        cumulative_offset += seg_duration
    return out
