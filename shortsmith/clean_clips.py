"""Step 4: Remove filler words and long silences from each cut clip.

Transcript-driven (not audio-amplitude-driven). Guarantees no cut lands inside
a word — every cut starts at `word_end + margin` and ends at
`next_word_start - margin`.

Approach:
  1. From the per-clip word transcript, find:
       (a) Filler-word ranges  — every "um/uh/like/..." occurrence
       (b) Silence ranges      — gaps between words longer than silence_min_to_cut
  2. Merge overlapping ranges, then use ffmpeg `select`/`aselect` filters to
     KEEP everything OUTSIDE the cut ranges (and stitch the remainder together).
  3. Return the list of applied cuts so the pipeline can adjust the post-clean
     word timings without re-transcribing (caller does this; we just emit cuts).

Auto-editor is no longer used here — it cut at audio thresholds which sometimes
trimmed the quiet tail of a real word. Word-aware cuts can't make that mistake.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from .captions import slice_words_for_segments
from .config import Config

log = logging.getLogger(__name__)

# Minimum silence gap we'll consider cutting. Anything shorter is left as a
# natural breath. Tunable via config.
SILENCE_MIN_TO_CUT = 0.55  # seconds


def clean_all(
    clip_manifests: list[dict],
    source_words: list[dict],
    work_dir: Path,
    cfg: Config,
) -> list[dict]:
    """Word-aware clean for every cut clip."""
    cleaned_dir = work_dir / "cleaned"
    cleaned_dir.mkdir(parents=True, exist_ok=True)

    for m in clip_manifests:
        rank = m["rank"]
        raw_path = Path(m["raw_path"])
        cleaned_path = cleaned_dir / f"short-{rank:02d}.mp4"

        # Slice the source transcript to this clip's local timeline.
        snapped = m.get("snapped_segments") or []
        local_words = slice_words_for_segments(source_words, snapped) if snapped else []

        if not local_words:
            log.warning("Clip %d: no transcript words for local timeline; copying raw as cleaned", rank)
            shutil.copy(raw_path, cleaned_path)
            m["cleaned_path"] = str(cleaned_path)
            m["clean_cuts"] = []
            m["words_after_clean"] = []
            continue

        # Persist the pre-clean per-clip transcript for debugging.
        (raw_path.parent / f"short-{rank:02d}.pre_clean.words.json").write_text(
            json.dumps(local_words, indent=2, ensure_ascii=False), encoding="utf-8",
        )

        cuts = _compute_cuts(local_words, cfg)
        log.info("Clip %d: %d cuts (silences + fillers totaling %.2fs)",
                 rank, len(cuts), sum(ce - cs for cs, ce in cuts))

        try:
            _apply_cuts_ffmpeg(raw_path, cuts, cleaned_path)
            words_after = _adjust_word_timings_after_cuts(local_words, cuts)
        except subprocess.CalledProcessError as e:
            log.warning("Clip %d: ffmpeg select filter failed (%s); copying raw as cleaned",
                        rank, e.stderr[:200] if e.stderr else "")
            shutil.copy(raw_path, cleaned_path)
            words_after = local_words
            cuts = []

        m["cleaned_path"] = str(cleaned_path)
        m["clean_cuts"] = cuts
        m["words_after_clean"] = words_after

    return clip_manifests


def _compute_cuts(words: list[dict], cfg: Config) -> list[tuple[float, float]]:
    """Build the (start, end) ranges to remove from this clip."""
    cuts: list[tuple[float, float]] = []

    # 1. Filler words — drop each occurrence with a small pad on each side that
    #    stays inside the inter-word gap. We never let the cut extend into a
    #    neighboring word.
    cuts.extend(_filler_cuts(words, cfg))

    # 2. Long silences — gaps between non-filler words. Cut from
    #    word_end+margin to next_word_start-margin so a breath remains.
    cuts.extend(_silence_cuts(words, cfg))

    # 3. Merge overlapping/adjacent cuts so we issue a single, contiguous range.
    return _merge_overlapping(cuts)


def _filler_cuts(words: list[dict], cfg: Config) -> list[tuple[float, float]]:
    """Per the configured filler list, compute safe cut ranges around each
    filler occurrence. Pads ±filler_pad_seconds but clamps to the surrounding
    silence so we never overrun into adjacent words.
    """
    fillers = {f.lower().strip() for f in cfg.fillers}
    single = {f for f in fillers if " " not in f}
    multi = [f.split() for f in fillers if " " in f]

    def safe_pad(prev_end_t: float, cut_start: float, cut_end: float, next_start_t: float) -> tuple[float, float]:
        # Clamp the padded cut so it never enters the previous or next word.
        cs = max(prev_end_t + 0.001, cut_start - cfg.filler_pad_seconds)
        ce = min(next_start_t - 0.001, cut_end + cfg.filler_pad_seconds)
        return cs, ce

    cuts: list[tuple[float, float]] = []
    n = len(words)
    i = 0
    while i < n:
        w = words[i]
        text = (w.get("text") or w.get("word") or "").strip().lower().rstrip(",.?!:;")
        # Single-word filler
        if text in single:
            prev_end = float(words[i - 1]["end"]) if i > 0 else 0.0
            next_start = float(words[i + 1]["start"]) if i + 1 < n else float(w["end"]) + 1.0
            cs, ce = safe_pad(prev_end, float(w["start"]), float(w["end"]), next_start)
            if ce > cs:
                cuts.append((cs, ce))
            i += 1
            continue

        # Multi-word filler — check each phrase
        matched = False
        for parts in multi:
            if i + len(parts) > n:
                continue
            ok = all(
                (words[i + j].get("text") or words[i + j].get("word") or "")
                .strip().lower().rstrip(",.?!:;") == parts[j]
                for j in range(len(parts))
            )
            if ok:
                prev_end = float(words[i - 1]["end"]) if i > 0 else 0.0
                last = words[i + len(parts) - 1]
                after = words[i + len(parts)] if i + len(parts) < n else None
                next_start = float(after["start"]) if after else float(last["end"]) + 1.0
                cs, ce = safe_pad(prev_end, float(w["start"]), float(last["end"]), next_start)
                if ce > cs:
                    cuts.append((cs, ce))
                i += len(parts)
                matched = True
                break
        if not matched:
            i += 1

    return cuts


def _silence_cuts(words: list[dict], cfg: Config) -> list[tuple[float, float]]:
    """Cut inter-word silences longer than SILENCE_MIN_TO_CUT, preserving a
    breath equal to cfg.silence_margin on each end.
    """
    out: list[tuple[float, float]] = []
    margin = cfg.silence_margin
    for prev, nxt in zip(words, words[1:]):
        gap = float(nxt["start"]) - float(prev["end"])
        if gap > SILENCE_MIN_TO_CUT:
            cut_start = float(prev["end"]) + margin
            cut_end = float(nxt["start"]) - margin
            if cut_end > cut_start + 0.05:  # don't bother with sub-50ms cuts
                out.append((cut_start, cut_end))
    return out


def _merge_overlapping(cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not cuts:
        return []
    cuts = sorted(cuts)
    merged: list[list[float]] = [list(cuts[0])]
    for cs, ce in cuts[1:]:
        if cs <= merged[-1][1] + 0.02:  # near-adjacent → fold in
            merged[-1][1] = max(merged[-1][1], ce)
        else:
            merged.append([cs, ce])
    return [(round(cs, 3), round(ce, 3)) for cs, ce in merged]


def _apply_cuts_ffmpeg(input_path: Path, cuts: list[tuple[float, float]], out_path: Path) -> None:
    """Use ffmpeg's select/aselect filter to keep only NON-cut ranges."""
    if not cuts:
        # No cuts — just transcode to normalize keyframes
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-g", "30", "-keyint_min", "30",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return

    # Build the "not in any cut" expression: not(between(t,cs1,ce1)+between(t,cs2,ce2)+...)
    between_terms = "+".join(f"between(t,{cs},{ce})" for cs, ce in cuts)
    select_expr = f"not({between_terms})"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
        "-af", f"aselect='{select_expr}',asetpts=N/SR/TB",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-g", "30", "-keyint_min", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _adjust_word_timings_after_cuts(
    words: list[dict],
    cuts: list[tuple[float, float]],
) -> list[dict]:
    """Given the pre-clean word list and the cuts applied, return new word
    timings on the post-clean local timeline.

    A word is dropped if any cut range overlaps it. Surviving words have their
    timestamps shifted back by the total cut-duration that preceded them.
    """
    if not cuts:
        return words
    cuts_sorted = sorted(cuts)

    def overlaps_any_cut(ws: float, we: float) -> bool:
        for cs, ce in cuts_sorted:
            if ws < ce and we > cs:
                return True
        return False

    def cut_before(t: float) -> float:
        total = 0.0
        for cs, ce in cuts_sorted:
            if ce <= t:
                total += ce - cs
            else:
                break
        return total

    out: list[dict] = []
    for w in words:
        ws = float(w["start"])
        we = float(w["end"])
        if overlaps_any_cut(ws, we):
            continue
        shift = cut_before(ws)
        out.append({
            "text": (w.get("text") or w.get("word") or "").strip(),
            "start": round(ws - shift, 3),
            "end": round(we - shift, 3),
        })
    return out
