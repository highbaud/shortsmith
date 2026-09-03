"""Step 7: Reframe 1920x1080 landscape -> 1080x1920 vertical (9:16) using
YuNet face detection.

Two modes:

1. Static crop (default, single-speaker). One crop position for the whole clip,
   chosen from a robust statistic of the speaker's face over the clip. Perfect
   for talking-head footage where the speaker barely moves.

2. Cut-aware crop (``cfg.reframe_cut_aware`` or per-clip ``manifest["multicam"]``).
   For MULTICAM / two-speaker footage that the editor already cut into
   single-speaker shots (e.g. a podcast that hard-cuts between the host's camera
   and the guest's camera). A single static crop would average the two speakers'
   on-screen positions and frame both badly. Instead we:
     - detect the camera cuts (ffmpeg scene detection),
     - compute an INDEPENDENT face crop for each shot using the same robust
       logic as the static path,
     - stitch the shots back with one ffmpeg ``filter_complex`` that segments the
       VIDEO per shot but leaves the AUDIO as a single untouched stream, so A/V
       stays perfectly in sync (no concat-demuxer priming gaps).
   No speaker diarization is needed — the edit already did the speaker-switching;
   we just follow whoever is on screen in each shot.

Robustness rules for the static crop (v2), reused per-shot by the cut-aware path:
- Sample frequently (every N frames, default 3).
- Reject detections with confidence below threshold.
- Reject detections whose bbox height is implausible (too small = probably a
  logo / chat avatar / overlay face).
- Biggest-face-wins: the speaker is the largest face in a talking-head frame.
- Reject detections far outside the IQR of the shot's detections (outlier
  defense — single misfire on a graphic must not pull the crop).
- After filtering, if too few detections remain, fall back to a looser set, then
  to a center crop.
- Use a robust statistic (median) of the filtered detections.
"""
from __future__ import annotations

import logging
import re
import statistics
import subprocess
from pathlib import Path

from . import gallery
from .config import Config
from .layouts import LayoutSpec, load_preset

log = logging.getLogger(__name__)


def reframe_all(
    clip_manifests: list[dict],
    work_dir: Path,
    cfg: Config,
) -> list[dict]:
    vertical_dir = work_dir / "vertical"
    vertical_dir.mkdir(parents=True, exist_ok=True)

    global_cut_aware = bool(getattr(cfg, "reframe_cut_aware", False))
    global_layout = str(getattr(cfg, "reframe_layout", "static")).lower()

    for m in clip_manifests:
        rank = m["rank"]
        src = Path(m.get("enhanced_path") or m.get("cleaned_path") or m["raw_path"])
        out = vertical_dir / f"short-{rank:02d}.mp4"
        # Per-clip overrides win (one run can mix single-speaker, multicam and
        # gallery clips); else fall back to the run-level config.
        layout = str(m.get("layout", global_layout)).lower()
        cut_aware = bool(m.get("multicam", global_cut_aware))
        try:
            if layout in ("split-stack", "auto"):
                band = reframe_one_split_stack(
                    src, out, cfg, required=(layout == "split-stack"),
                    preset=m.get("layout_preset"),
                )
                if band is not None:
                    # Tell the caption layer to sit in the gap between the two
                    # speaker squares instead of auto-placing around a face.
                    m["caption_band"] = band
                    m["layout"] = "split-stack"
                elif cut_aware:
                    reframe_one_cut_aware(src, out, cfg)
                else:
                    reframe_one(src, out, cfg)
            elif cut_aware:
                reframe_one_cut_aware(src, out, cfg)
            else:
                reframe_one(src, out, cfg)
            m["vertical_path"] = str(out)
        except Exception as e:
            log.warning("Reframe failed for clip %d (%s); using center crop",
                        rank, _ffmpeg_reason(e))
            try:
                _ffmpeg_center_crop(src, out)
            except Exception as e2:  # noqa: BLE001
                # The center crop is the last resort, so a failure here is this
                # clip's failure, not the batch's: leave vertical_path unset and
                # let step 8 ship the 16:9 cut rather than abandoning every
                # clip that comes after this one.
                log.error("Center-crop fallback also failed for clip %d (%s); "
                          "shipping the un-reframed clip", rank, _ffmpeg_reason(e2))
                continue
            m["vertical_path"] = str(out)

    return clip_manifests


def _ffmpeg_reason(e: Exception) -> str:
    """A failed ffmpeg call carries its diagnosis in captured stderr, which
    `str(CalledProcessError)` drops. Without it every reframe failure logs the
    same unusable "returned non-zero exit status" line."""
    if isinstance(e, subprocess.CalledProcessError) and e.stderr:
        err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else e.stderr
        return f"{e}: {err.strip()[-400:]}"
    return str(e)


# ---------------------------------------------------------------------------
# Shared detection + geometry helpers (used by both reframe modes)
# ---------------------------------------------------------------------------

# Each detection row: (cx, cy, h, score).
Detection = tuple[float, float, float, float]


def _open_capture(src_video: Path):
    import cv2  # lazy import

    if not src_video.exists():
        raise FileNotFoundError(src_video)
    cap = cv2.VideoCapture(str(src_video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {src_video}")
    return cap


def _sample_faces(cap, detector, cfg: Config,
                  start_frame: int = 0, end_frame: int | None = None) -> list[Detection]:
    """Run YuNet over [start_frame, end_frame) and return the most-confident
    face per sampled frame as (cx, cy, h, score)."""
    import cv2  # lazy import

    sample = max(1, cfg.reframe_sample_every)
    raw: list[Detection] = []
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frame_idx = start_frame
    while True:
        if end_frame is not None and frame_idx >= end_frame:
            break
        ret, frame = cap.read()
        if not ret:
            break
        if (frame_idx - start_frame) % sample == 0:
            _, faces = detector.detect(frame)
            if faces is not None and len(faces) > 0:
                best = max(faces, key=lambda f: f[-1])
                score = float(best[-1])
                if score >= cfg.yunet_score_threshold:
                    x, y, w, h = (float(best[0]), float(best[1]),
                                  float(best[2]), float(best[3]))
                    raw.append((x + w / 2.0, y + h / 2.0, h, score))
        frame_idx += 1
    return raw


def _filter_detections(raw: list[Detection], src_w: int, src_h: int,
                       cfg: Config, label: str = "") -> list[Detection]:
    """Robust filtering: absolute-height floor -> biggest-face-wins ->
    IQR outlier rejection -> small-set fallback. Returns the set to take the
    median of (never empty if raw was non-empty)."""
    if not raw:
        return []

    # 1a. Absolute minimum face height (resolution-aware).
    abs_min_h_frac = getattr(cfg, "reframe_min_face_h_frac", 0.08)
    abs_min_h = max(getattr(cfg, "reframe_min_face_h", 180.0), src_h * abs_min_h_frac)
    filt0 = [d for d in raw if d[2] >= abs_min_h]

    # 1b. Biggest-face-wins: reject faces smaller than 70% of the 90th-percentile
    #     face height (rules out PIP avatars / chat thumbnails / graphic faces).
    if filt0:
        hs_sorted = sorted(d[2] for d in filt0)
        h90 = hs_sorted[max(0, int(len(hs_sorted) * 0.90) - 1)]
        big_floor = h90 * 0.70
        filt1 = [d for d in filt0 if d[2] >= big_floor]
    else:
        filt1 = []

    # 2. IQR outlier rejection on x/y centers (needs >= 6 samples).
    if len(filt1) >= 6:
        xs = sorted(d[0] for d in filt1)
        ys = sorted(d[1] for d in filt1)
        n = len(filt1)
        q1_x, q3_x = xs[n // 4], xs[(3 * n) // 4]
        q1_y, q3_y = ys[n // 4], ys[(3 * n) // 4]
        iqr_x = max(q3_x - q1_x, 1.0)
        iqr_y = max(q3_y - q1_y, 1.0)
        lo_x, hi_x = q1_x - 2.5 * iqr_x, q3_x + 2.5 * iqr_x
        lo_y, hi_y = q1_y - 2.5 * iqr_y, q3_y + 2.5 * iqr_y
        filt2 = [d for d in filt1 if lo_x <= d[0] <= hi_x and lo_y <= d[1] <= hi_y]
    else:
        filt2 = filt1

    # 3. Fallback if too few survive — prefer the abs-height set over raw.
    used = filt2
    if len(used) < 8:
        if label:
            log.warning("%s: only %d viable detections after filtering; "
                        "falling back to looser set.", label, len(used))
        used = filt0 if len(filt0) >= 8 else (filt0 or raw)
    return used


def _crop_geom(avg_face_x: float, avg_face_y: float, avg_face_h: float,
               src_w: int, src_h: int, cfg: Config) -> tuple[int, int, int, int]:
    """Compute (crop_w, crop_h, crop_x, crop_y) ints placing the face at the
    configured target position/height in the 1080x1920 output, clamped to the
    source. Mirrors the original static-crop math exactly."""
    # Sanity clamp: keep the median off the extreme edges (else the crop is
    # mostly empty background).
    edge_margin_x = src_w * 0.10
    edge_margin_y = src_h * 0.10
    avg_face_x = max(edge_margin_x, min(avg_face_x, src_w - edge_margin_x))
    avg_face_y = max(edge_margin_y, min(avg_face_y, src_h - edge_margin_y))

    output_target_y = 1920 * cfg.face_target_y
    output_target_h = 1920 * cfg.face_target_height

    zoom = output_target_h / max(avg_face_h, 1.0)
    min_zoom = max(1080.0 / src_w, 1920.0 / src_h)
    if zoom < min_zoom:
        zoom = min_zoom

    crop_w_src = 1080.0 / zoom
    crop_h_src = 1920.0 / zoom
    crop_x = avg_face_x - 540.0 / zoom
    crop_y = avg_face_y - output_target_y / zoom
    crop_x = max(0.0, min(crop_x, src_w - crop_w_src))
    crop_y = max(0.0, min(crop_y, src_h - crop_h_src))

    return (int(round(crop_w_src)), int(round(crop_h_src)),
            int(round(crop_x)), int(round(crop_y)))


def _center_crop_geom(src_w: int, src_h: int) -> tuple[int, int, int, int]:
    """Numeric 9:16 center-crop tuple (fallback when no face is found)."""
    cw = int(round(src_h * 9.0 / 16.0))
    cw = min(cw, src_w)
    ch = src_h
    cx = int(round((src_w - cw) / 2.0))
    return (cw, ch, cx, 0)


def reframe_one(src_video: Path, out_video: Path, cfg: Config) -> None:
    """Single static crop for the whole clip (single-speaker talking head).

    Detect the speaker's face over the clip, compute one social-safe crop that
    places the face at the configured target position/height, then crop + scale
    via ffmpeg. Output is identical to the pre-refactor implementation.
    """
    if not cfg.yunet_model_path.exists():
        raise FileNotFoundError(f"YuNet model not found at {cfg.yunet_model_path}")

    import cv2  # lazy import

    cap = _open_capture(src_video)
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if src_w == 0 or src_h == 0:
        cap.release()
        raise RuntimeError(f"Invalid video dimensions for {src_video}")

    detector = cv2.FaceDetectorYN_create(
        str(cfg.yunet_model_path), "", (src_w, src_h),
        cfg.yunet_score_threshold, 0.3, 5000,
    )
    raw = _sample_faces(cap, detector, cfg)
    cap.release()

    if not raw:
        log.warning("No faces detected in %s; using center crop", src_video.name)
        _ffmpeg_center_crop(src_video, out_video)
        return

    used = _filter_detections(raw, src_w, src_h, cfg, label=src_video.name)
    avg_x = statistics.median(d[0] for d in used)
    avg_y = statistics.median(d[1] for d in used)
    avg_h = statistics.median(d[2] for d in used)
    cw, ch, cx, cy = _crop_geom(avg_x, avg_y, avg_h, src_w, src_h, cfg)

    log.info("Reframe %s: %d raw -> %d filt, src face=(%.0f,%.0f,h=%.0f) "
             "crop=(%d,%d,%dx%d)", src_video.name, len(raw), len(used),
             avg_x, avg_y, avg_h, cx, cy, cw, ch)

    vf = f"crop={cw}:{ch}:{cx}:{cy},scale=1080:1920:flags=lanczos"
    _run_ffmpeg_vf(src_video, out_video, vf)


# ---------------------------------------------------------------------------
# Cut-aware (multicam / two-speaker) reframe
# ---------------------------------------------------------------------------

_SHOWINFO_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def _detect_cuts(src_video: Path, cfg: Config) -> list[float]:
    """Return sorted camera-cut timestamps (seconds) via ffmpeg scene detection.

    A multicam podcast hard-cuts between two very different camera feeds, so the
    scene score spikes at each cut. Threshold defaults to 0.30 — high enough to
    ignore in-shot motion, low enough to catch a cut to a different room.
    """
    thr = float(getattr(cfg, "reframe_scene_threshold", 0.30))
    proc = subprocess.run(
        ["ffmpeg", "-i", str(src_video),
         "-filter:v", f"select='gt(scene,{thr})',showinfo",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    # showinfo writes to stderr.
    cuts = sorted({float(m) for m in _SHOWINFO_PTS.findall(proc.stderr)})
    return [t for t in cuts if t > 0.0]


def _shot_boundaries(cuts: list[float], duration: float,
                     min_shot: float) -> list[tuple[float, float]]:
    """Turn cut timestamps into [start, end) shot spans, merging shots shorter
    than ``min_shot`` into their predecessor so tiny flickers don't fragment the
    filtergraph."""
    bounds = [0.0] + [c for c in cuts if 0.0 < c < duration] + [duration]
    bounds = sorted(set(bounds))
    shots: list[tuple[float, float]] = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < min_shot and shots:
            shots[-1] = (shots[-1][0], b)  # merge into previous
        else:
            shots.append((a, b))
    # If the very first shot ended up too short and there was nothing to merge
    # into, fold it forward into the next one.
    if len(shots) >= 2 and shots[0][1] - shots[0][0] < min_shot:
        shots[1] = (shots[0][0], shots[1][1])
        shots = shots[1:]
    return shots


def _probe_duration(src_video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src_video)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return float(out.stdout.strip())


def reframe_one_cut_aware(src_video: Path, out_video: Path, cfg: Config) -> None:
    """Cut-aware reframe for multicam/two-speaker footage.

    Detect camera cuts, compute an independent face crop per shot, and stitch
    the per-shot crops with a single ffmpeg filter_complex. The audio is mapped
    straight from the source as one continuous stream, so A/V never drifts.
    """
    if not cfg.yunet_model_path.exists():
        raise FileNotFoundError(f"YuNet model not found at {cfg.yunet_model_path}")

    import cv2  # lazy import

    cap = _open_capture(src_video)
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if src_w == 0 or src_h == 0:
        cap.release()
        raise RuntimeError(f"Invalid video dimensions for {src_video}")

    duration = _probe_duration(src_video)
    min_shot = float(getattr(cfg, "reframe_min_shot_seconds", 0.4))
    cuts = _detect_cuts(src_video, cfg)
    shots = _shot_boundaries(cuts, duration, min_shot)

    # No real cuts -> nothing multicam to handle; use the cheaper static path so
    # the output matches the single-speaker pipeline exactly.
    if len(shots) <= 1:
        cap.release()
        log.info("Cut-aware %s: no camera cuts found; using static crop",
                 src_video.name)
        reframe_one(src_video, out_video, cfg)
        return

    detector = cv2.FaceDetectorYN_create(
        str(cfg.yunet_model_path), "", (src_w, src_h),
        cfg.yunet_score_threshold, 0.3, 5000,
    )

    crops: list[tuple[int, int, int, int]] = []
    for (a, b) in shots:
        sf = int(round(a * fps))
        ef = int(round(b * fps))
        raw = _sample_faces(cap, detector, cfg, start_frame=sf, end_frame=ef)
        if raw:
            used = _filter_detections(raw, src_w, src_h, cfg)
            avg_x = statistics.median(d[0] for d in used)
            avg_y = statistics.median(d[1] for d in used)
            avg_h = statistics.median(d[2] for d in used)
            crops.append(_crop_geom(avg_x, avg_y, avg_h, src_w, src_h, cfg))
        else:
            crops.append(_center_crop_geom(src_w, src_h))
    cap.release()

    log.info("Cut-aware %s: %d cuts -> %d shots, per-shot crops computed",
             src_video.name, len(cuts), len(shots))

    # Build one filter_complex: trim each shot, crop it to its own window, scale
    # to 1080x1920, then concat all video segments. Audio is left untouched and
    # mapped directly from the source (concat a=0).
    parts: list[str] = []
    labels: list[str] = []
    for i, ((a, b), (cw, ch, cx, cy)) in enumerate(zip(shots, crops, strict=True)):
        lbl = f"v{i}"
        parts.append(
            f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
            f"crop={cw}:{ch}:{cx}:{cy},scale=1080:1920:flags=lanczos,"
            f"setsar=1[{lbl}]"
        )
        labels.append(f"[{lbl}]")
    concat = f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[outv]"
    filtergraph = ";".join(parts + [concat])

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(src_video),
        "-filter_complex", filtergraph,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-g", "30", "-keyint_min", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_video),
    ], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Split-stack (gallery view / two speakers on screen at once)
# ---------------------------------------------------------------------------


def _grab_gray_frame(src_video: Path, at_frac: float = 0.5):
    """Decode one frame from `at_frac` through the clip as grayscale."""
    import cv2  # lazy import

    cap = _open_capture(src_video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if total > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * at_frac))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"Could not read a frame from {src_video}")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _sample_faces_in_tiles(
    src_video: Path, tiles: list[gallery.Rect], cfg: Config,
    sample_every: int = 0,
) -> list[list[Detection]]:
    """One decode pass, detecting faces inside each tile separately.

    Running the detector on the cropped tile (rather than the whole frame and
    then asking which half a face landed in) keeps the two speakers from ever
    being confused for one another, and gives the detector a sensibly-sized
    input. Returned coordinates are TILE-LOCAL.
    """
    import cv2  # lazy import

    detectors = [
        cv2.FaceDetectorYN_create(
            str(cfg.yunet_model_path), "", (t.w, t.h),
            cfg.yunet_score_threshold, 0.3, 5000,
        )
        for t in tiles
    ]
    # Nobody moves much in a webcam grid, so sample far less often than the
    # motion-tracking path needs.
    sample = max(1, sample_every or cfg.reframe_sample_every * 4)

    cap = _open_capture(src_video)
    out: list[list[Detection]] = [[] for _ in tiles]
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if idx % sample == 0:
            for i, t in enumerate(tiles):
                sub = frame[t.y:t.y + t.h, t.x:t.x + t.w]
                _, faces = detectors[i].detect(sub)
                if faces is not None and len(faces) > 0:
                    best = max(faces, key=lambda f: f[-1])
                    score = float(best[-1])
                    if score >= cfg.yunet_score_threshold:
                        x, y, w, h = (float(best[0]), float(best[1]),
                                      float(best[2]), float(best[3]))
                        out[i].append((x + w / 2.0, y + h / 2.0, h, score))
        idx += 1
    cap.release()
    return out


def _split_stack_filtergraph(
    crops: list[gallery.Rect], layout: gallery.StackLayout, spec: LayoutSpec
) -> str:
    """Build the single-input filtergraph that composes the stacked squares.

    Deliberately ONE input: the two panels are two crops of the same decoded
    stream (via `split`), not two `-i` arguments. Passing the file twice is what
    produces the doubled-audio failure on two-up sources, because each input
    carries its own copy of the same audio track.
    """
    border = spec.border_color
    border_px = spec.border_width
    blur = spec.bg_blur
    dim = spec.bg_dim
    W, H = layout.width, layout.height
    panels = [layout.top, layout.bottom]

    parts = [
        # Ambient backdrop: the source frame itself, blown up to fill, blurred
        # and dimmed. Keeps the margins beside the squares feeling like part of
        # the shot instead of dead black bars.
        "[0:v]split=3[bg][s0][s1]",
        f"[bg]crop=ih*{W}/{H}:ih,scale={W}:{H}:flags=bilinear,"
        f"boxblur={blur}:2,eq=brightness=-{dim:.2f}:saturation=0.55,setsar=1[bgv]",
    ]
    for i, (crop, panel) in enumerate(zip(crops, panels, strict=True)):
        parts.append(
            f"[s{i}]{crop.ffmpeg_crop},"
            f"scale={panel.w}:{panel.h}:flags=lanczos,setsar=1[p{i}]"
        )

    prev = "bgv"
    for i, panel in enumerate(panels):
        nxt = f"o{i}"
        parts.append(f"[{prev}][p{i}]overlay={panel.x}:{panel.y}[{nxt}]")
        prev = nxt

    boxes = ",".join(
        f"drawbox=x={p.x}:y={p.y}:w={p.w}:h={p.h}:color={border}:t={border_px}"
        for p in panels
    )
    parts.append(f"[{prev}]{boxes},format=yuv420p[outv]")
    return ";".join(parts)


def reframe_one_split_stack(
    src_video: Path, out_video: Path, cfg: Config, required: bool = True,
    preset: str | None = None,
) -> dict[str, float] | None:
    """Reframe a gallery-view two-speaker source into stacked squares.

    Detects the two webcam tiles, tracks each speaker's face inside their own
    tile, crops a square around each, and stacks them, first speaker on top,
    second below, with a caption band in the gap between them. The saved layout
    preset supplies every geometry and styling decision.

    Returns the caption band (height fractions) so the caption layer can be
    pointed at that gap. Returns None when the source is not a two-up gallery
    and `required` is False, letting the caller fall back to another mode.
    """
    if not cfg.yunet_model_path.exists():
        raise FileNotFoundError(f"YuNet model not found at {cfg.yunet_model_path}")

    spec = load_preset(preset or getattr(cfg, "split_stack_preset", None))
    frame = _grab_gray_frame(src_video)
    frame_h, frame_w = frame.shape[:2]

    # A preset may pin the tile geometry measured off the real source. That
    # skips brightness detection, which cannot see a tile whose own content is
    # dark (a black studio backdrop, a speaker in dark clothing).
    tiles = spec.pinned_tiles(frame_w, frame_h)
    if tiles is None:
        tiles = gallery.detect_tiles(frame, dark_threshold=spec.dark_threshold)
        if not gallery.looks_like_two_up(tiles):
            msg = (f"{src_video.name}: expected a two-up gallery, found "
                   f"{len(tiles)} tile(s)")
            if required:
                raise RuntimeError(msg)
            log.info("%s; not using split-stack", msg)
            return None

    # Left tile goes on top by default (reading order); flip for sources where
    # the guest is on the left.
    if spec.order.lower() == "rl":
        tiles = list(reversed(tiles))

    layout = spec.layout()
    per_tile = _sample_faces_in_tiles(src_video, tiles, cfg, spec.sample_every)

    # Each speaker's "natural" framing: how much of a full-tile-height square
    # their face already fills. A speaker sitting close to their webcam can only
    # be zoomed IN, never out past their tile, so the shared target has to be at
    # least the largest natural value, otherwise the two heads end up wildly
    # different sizes in a layout that puts them right on top of each other.
    medians: list[tuple[float, float, float] | None] = []
    naturals: list[float] = []
    for i, raw in enumerate(per_tile):
        if not raw:
            medians.append(None)
            continue
        used = _filter_detections(raw, tiles[i].w, tiles[i].h, cfg,
                                  label=f"{src_video.name} tile{i}")
        mx = statistics.median(d[0] for d in used)
        my = statistics.median(d[1] for d in used)
        mh = statistics.median(d[2] for d in used)
        medians.append((mx, my, mh))
        naturals.append(mh / max(1.0, float(min(tiles[i].w, tiles[i].h))))

    target = spec.face_height_frac
    if naturals and spec.match_face_size:
        target = max(target, max(naturals))

    crops: list[gallery.Rect] = []
    for i, med in enumerate(medians):
        if med is None:
            log.warning("%s: no face found in tile %d; centering that panel",
                        src_video.name, i)
            crops.append(gallery.center_square_in_tile(tiles[i]))
            continue
        mx, my, mh = med
        crop = gallery.square_crop_in_tile(
            mx, my, mh, tiles[i],
            face_height_frac=target,
            face_target_y=spec.face_target_y(i),
        )
        if spec.avoid_badge:
            crop = gallery.avoid_corner_badge(
                crop, tiles[i],
                badge_w_frac=spec.badge_w_frac,
                badge_h_frac=spec.badge_h_frac,
            )
        crops.append(crop)

    log.info("Split-stack %s [%s]: tiles=%s target_face=%.2f crops=%s band=%s",
             src_video.name, spec.name, [(t.w, t.h) for t in tiles], target,
             [(c.w, c.x, c.y) for c in crops], layout.caption_band)

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(src_video),
        "-filter_complex", _split_stack_filtergraph(crops, layout, spec),
        "-map", "[outv]",
        # Exactly one audio mapping from the single input, the panels share one
        # decoded stream, so there is no second copy of the audio to collide.
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-g", "30", "-keyint_min", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_video),
    ], check=True, capture_output=True)

    return layout.caption_band


def _run_ffmpeg_vf(src_video: Path, out_video: Path, vf: str) -> None:
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(src_video),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-g", "30", "-keyint_min", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_video),
    ], check=True, capture_output=True)


def _ffmpeg_center_crop(src_video: Path, out_video: Path) -> None:
    """Dumb fallback: center-crop 9:16 strip."""
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(src_video),
        "-vf", "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_video),
    ], check=True, capture_output=True)
