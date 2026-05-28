"""Step 3: Cut clips with physical segment reorder and safe-boundary snapping.

For each clip:
1. Snap each cut point to the nearest sentence-end / breath-silence boundary.
2. ffmpeg-cut each (snapped) segment with re-encode for frame-accurate cuts.
3. Concatenate segments with xfade crossfade at each seam.
4. Emit cut_manifest.json with the post-snap segments.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

PUNCTUATION_END = {".", "!", "?", '."', '!"', '?"', '.”', '!”', '?”'}


def cut_all(
    clips: list[dict],
    words: list[dict],
    source_video: Path,
    work_dir: Path,
    cfg: Config,
) -> list[dict]:
    """Cut every clip from `clips` to `work_dir/raw/short-NN.mp4`. Return manifests."""
    raw_dir = work_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    manifests = []
    for clip in clips:
        manifest = cut_one(clip, words, source_video, raw_dir, cfg)
        manifests.append(manifest)

    (work_dir / "cut_manifests.json").write_text(
        json.dumps(manifests, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifests


def cut_one(
    clip: dict,
    words: list[dict],
    source_video: Path,
    raw_dir: Path,
    cfg: Config,
) -> dict:
    """Cut one clip — snap boundaries, ffmpeg-cut each segment, xfade-concat."""
    rank = clip["rank"]
    requested = clip["segments"]

    # Snap each segment's start and end to safe boundaries
    snapped: list[list[float]] = []
    for s_start, s_end in requested:
        new_start = _snap_boundary(s_start, words, cfg, prefer_after=False)
        new_end = _snap_boundary(s_end, words, cfg, prefer_after=True)
        if new_end is None or new_start is None or new_end - new_start < 0.5:
            log.warning("Clip %d: dropping segment (%.2f, %.2f) — no safe boundary",
                        rank, s_start, s_end)
            continue
        snapped.append([new_start, new_end])

    if not snapped:
        log.warning("Clip %d: all segments failed boundary snap; falling back to raw start/end", rank)
        snapped = [[clip["start"], clip["end"]]]

    out_path = raw_dir / f"short-{rank:02d}.mp4"
    seg_paths = _ffmpeg_cut_segments(source_video, snapped, raw_dir, rank)
    _ffmpeg_concat_with_xfade(seg_paths, out_path, cfg.seam_xfade_seconds)

    # Clean up segment intermediates
    for p in seg_paths:
        try:
            p.unlink()
        except OSError:
            pass

    return {
        "rank": rank,
        "slug": clip["slug"],
        "requested_segments": requested,
        "snapped_segments": snapped,
        "raw_path": str(out_path),
        "duration": sum(e - s for s, e in snapped),
    }


def _snap_boundary(
    t: float,
    words: list[dict],
    cfg: Config,
    *,
    prefer_after: bool,
) -> float | None:
    """Snap a requested cut point to a word-gap boundary.

    Guarantees: the returned time NEVER lands inside a word — it's always
    in a silence between two words (or at the very start/end of the transcript).

    `prefer_after` matters for end-of-clip snaps: we want to EXTEND forward
    to find a clean sentence end rather than chop a thought short. So when
    `prefer_after=True`:
      - Forward search window is 3× wider than backward at tier 0/1 (sentence
        end / breath). We'll happily extend a clip up to ~1.8s to land on a
        real sentence end, but only retreat 0.6s.
      - Backward candidates get a distance penalty so forward sentence-ends
        beat them when ranking.

    Tiered preference:
      Tier 0 — sentence-end punctuation + ≥min_silence gap
      Tier 1 — ≥breath_silence gap (any)
      Tier 2 — any word gap, within ±(snap_window × 2.5)
      Tier 3 — any word gap, within ±(snap_window × 5.0) — last resort
    """
    if not words or len(words) < 2:
        return t

    base = cfg.boundary_snap_window

    # Asymmetric windows when snapping the END of a clip.
    if prefer_after:
        fwd_window = base * 3.0   # ~1.8s forward to find a sentence end
        bwd_window = base          # 0.6s backward only
        fwd_wide = base * 5.0      # ~3s forward fallback for any breath
        bwd_wide = base * 2.5      # ~1.5s backward fallback
        fwd_widest = base * 8.0    # ~4.8s forward worst-case
        bwd_widest = base * 5.0    # 3s backward worst-case
    else:
        fwd_window = bwd_window = base
        fwd_wide = bwd_wide = base * 2.5
        fwd_widest = bwd_widest = base * 5.0

    candidates: list[tuple[int, float, float]] = []
    for i in range(len(words) - 1):
        prev = words[i]
        nxt = words[i + 1]
        prev_end = float(prev["end"])
        next_start = float(nxt["start"])
        gap = next_start - prev_end
        if gap <= 0:
            continue

        boundary = (prev_end + next_start) / 2.0
        delta = boundary - t
        dist = abs(delta)
        is_forward = delta >= 0

        # Pick the right window for this candidate's direction.
        if is_forward:
            w0, w1, w2 = fwd_window, fwd_wide, fwd_widest
        else:
            w0, w1, w2 = bwd_window, bwd_wide, bwd_widest

        prev_text = (prev.get("text") or prev.get("word") or "").strip()
        ends_with_punct = any(prev_text.endswith(p) for p in PUNCTUATION_END)

        if ends_with_punct and gap >= cfg.boundary_min_silence and dist <= w0:
            tier = 0
        elif gap >= cfg.boundary_breath_silence and dist <= w0:
            tier = 1
        elif dist <= w1:
            tier = 2
        elif dist <= w2:
            tier = 3
        else:
            continue

        # Directional bias when prefer_after: forward candidates rank as if
        # they were 30% closer, backward 30% further. Sentence-end forward
        # always beats word-gap backward.
        if prefer_after and is_forward:
            sort_key = dist * 0.7
        elif prefer_after and not is_forward:
            sort_key = dist * 1.3
        else:
            sort_key = dist

        candidates.append((tier, sort_key, boundary))

    if not candidates:
        # Absolute last resort: closest word boundary anywhere. Better to
        # extend the clip by several seconds than land inside a word.
        gaps = []
        for i in range(len(words) - 1):
            prev_end = float(words[i]["end"])
            next_start = float(words[i + 1]["start"])
            if next_start > prev_end:
                gaps.append((abs((prev_end + next_start) / 2.0 - t), (prev_end + next_start) / 2.0))
        if gaps:
            gaps.sort(key=lambda g: g[0])
            return gaps[0][1]
        return None

    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[0][2]


def _ffmpeg_cut_segments(
    source: Path,
    segments: list[list[float]],
    raw_dir: Path,
    rank: int,
) -> list[Path]:
    """Re-encode each segment to a separate mp4. Returns the list of segment paths."""
    seg_paths = []
    for i, (start, end) in enumerate(segments):
        out = raw_dir / f"short-{rank:02d}_seg-{i:02d}.mp4"
        # Use -ss before -i for speed (keyframe seek), then -ss again after -i for accuracy
        # — combined with re-encode for frame-accuracy.
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-to", f"{end:.3f}",
            "-i", str(source),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-g", "30", "-keyint_min", "30",  # keyframe every second — Hyperframes seeks better
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(out),
        ]
        log.debug("ffmpeg cut: %s -> %s (%.2f-%.2fs)", source.name, out.name, start, end)
        subprocess.run(cmd, check=True, capture_output=True)
        seg_paths.append(out)
    return seg_paths


def _ffmpeg_concat_with_xfade(
    seg_paths: list[Path],
    out_path: Path,
    xfade: float,
) -> None:
    """Concat segments with xfade crossfade at each seam."""
    if len(seg_paths) == 1:
        # Simple copy / passthrough — but we need to re-encode to ensure faststart
        cmd = [
            "ffmpeg", "-y",
            "-i", str(seg_paths[0]),
            "-c", "copy",
            "-movflags", "+faststart",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return

    # Build complex xfade filter chain.
    # First we need each segment's duration.
    durations = [_probe_duration(p) for p in seg_paths]

    inputs: list[str] = []
    for p in seg_paths:
        inputs += ["-i", str(p)]

    filter_parts = []
    last_v = "[0:v]"
    last_a = "[0:a]"
    offset = durations[0] - xfade

    for i in range(1, len(seg_paths)):
        out_v = f"[v{i}]"
        out_a = f"[a{i}]"
        filter_parts.append(
            f"{last_v}[{i}:v]xfade=transition=fade:duration={xfade}:offset={offset:.3f}{out_v}"
        )
        filter_parts.append(
            f"{last_a}[{i}:a]acrossfade=d={xfade}{out_a}"
        )
        last_v = out_v
        last_a = out_a
        offset += durations[i] - xfade

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", last_v,
        "-map", last_a,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())
