"""Step 7: Reframe 1920x1080 landscape -> 1080x1920 vertical (9:16) using
YuNet face detection.

Key robustness rules (v2):
- Sample frequently (every N frames, default 3).
- Reject detections with confidence below threshold.
- Reject detections whose bbox height is implausible (too small = probably a
  logo / chat avatar / overlay face).
- Reject detections far outside the IQR of the clip's detections (outlier
  defense — single misfire on a graphic must not pull the crop).
- After filtering, if too few detections remain (< 8 or < 25% of expected),
  treat as detection-failed and use median-x of all raw detections, or fall
  back to center crop.
- Use a robust statistic (median) of the filtered detections rather than EMA
  of all detections, so a tail of misfires can't drag the position.
- Apply EMA on the median to keep the smoothing feel (single crop position
  per clip; talking heads barely move horizontally).
"""
from __future__ import annotations

import logging
import statistics
import subprocess
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)


def reframe_all(
    clip_manifests: list[dict],
    work_dir: Path,
    cfg: Config,
) -> list[dict]:
    vertical_dir = work_dir / "vertical"
    vertical_dir.mkdir(parents=True, exist_ok=True)

    for m in clip_manifests:
        rank = m["rank"]
        src = Path(m.get("enhanced_path") or m.get("cleaned_path") or m["raw_path"])
        out = vertical_dir / f"short-{rank:02d}.mp4"
        try:
            reframe_one(src, out, cfg)
            m["vertical_path"] = str(out)
        except Exception as e:
            log.warning("Reframe failed for clip %d (%s); using center crop", rank, e)
            _ffmpeg_center_crop(src, out)
            m["vertical_path"] = str(out)

    return clip_manifests


def reframe_one(src_video: Path, out_video: Path, cfg: Config) -> None:
    """Detect face, compute social-safe crop with target face placement, then
    crop + scale via ffmpeg.

    The crop window is sized so:
      - face center maps to output (540, cfg.face_target_y * 1920)
      - face bbox occupies cfg.face_target_height of the output vertical

    Clamps to source bounds. If the target zoom would require cropping a region
    larger than the source, snaps zoom up to the min that keeps the crop inside
    the source (face ends up slightly bigger than target — acceptable).
    """
    import cv2  # lazy import

    if not cfg.yunet_model_path.exists():
        raise FileNotFoundError(f"YuNet model not found at {cfg.yunet_model_path}")

    cap = cv2.VideoCapture(str(src_video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {src_video}")

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1

    if src_w == 0 or src_h == 0:
        raise RuntimeError(f"Invalid video dimensions for {src_video}")

    detector = cv2.FaceDetectorYN_create(
        str(cfg.yunet_model_path),
        "",
        (src_w, src_h),
        cfg.yunet_score_threshold,
        0.3,
        5000,
    )

    sample = max(1, cfg.reframe_sample_every)
    raw: list[tuple[float, float, float, float]] = []  # (cx, cy, h, score)
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % sample == 0:
            _, faces = detector.detect(frame)
            if faces is not None and len(faces) > 0:
                # Each face row: [x, y, w, h, lm_x..., lm_y..., score]
                # Pick the most confident face above threshold.
                best = max(faces, key=lambda f: f[-1])
                score = float(best[-1])
                if score >= cfg.yunet_score_threshold:
                    x, y, w, h = (float(best[0]), float(best[1]),
                                  float(best[2]), float(best[3]))
                    raw.append((x + w / 2.0, y + h / 2.0, h, score))
        frame_idx += 1
    cap.release()

    expected = max(1, total_frames // sample)
    if not raw:
        log.warning("No faces detected in %s; using center crop", src_video.name)
        _ffmpeg_center_crop(src_video, out_video)
        return

    # === Robust filtering ===
    # 1a. Absolute minimum face height (resolution-aware). On a 4K stream
    #     a tiny avatar/logo can still be 200-400px tall — too small for
    #     a fixed threshold. Use the source height as a scale.
    abs_min_h_frac = getattr(cfg, "reframe_min_face_h_frac", 0.08)
    abs_min_h = max(getattr(cfg, "reframe_min_face_h", 180.0), src_h * abs_min_h_frac)
    filt0 = [d for d in raw if d[2] >= abs_min_h]

    # 1b. **Biggest-face-wins**: the speaker's face is the largest face in
    #     a talking-head video. Reject everything smaller than 70% of the
    #     90th-percentile face height in the clip — this rules out PIP
    #     avatars, chat thumbnails, and graphic faces that the absolute
    #     threshold lets through on high-resolution sources.
    if filt0:
        hs_sorted = sorted(d[2] for d in filt0)
        h90 = hs_sorted[max(0, int(len(hs_sorted) * 0.90) - 1)]
        big_floor = h90 * 0.70
        filt1 = [d for d in filt0 if d[2] >= big_floor]
        if len(filt1) < len(filt0):
            log.debug("%s: biggest-face filter %d -> %d (h90=%.0f, floor=%.0f)",
                      src_video.name, len(filt0), len(filt1), h90, big_floor)
    else:
        filt1 = []

    # 2. IQR outlier rejection on x and y center positions, computed from
    #    filt1. If filt1 too small to compute IQR (<6 samples), skip this step.
    if len(filt1) >= 6:
        xs = sorted(d[0] for d in filt1)
        ys = sorted(d[1] for d in filt1)
        n = len(filt1)
        q1_x = xs[n // 4]
        q3_x = xs[(3 * n) // 4]
        iqr_x = max(q3_x - q1_x, 1.0)
        q1_y = ys[n // 4]
        q3_y = ys[(3 * n) // 4]
        iqr_y = max(q3_y - q1_y, 1.0)
        # 2.5 * IQR is generous; tight enough to catch a single corner misfire,
        # loose enough that legitimate small camera movement isn't rejected.
        lo_x, hi_x = q1_x - 2.5 * iqr_x, q3_x + 2.5 * iqr_x
        lo_y, hi_y = q1_y - 2.5 * iqr_y, q3_y + 2.5 * iqr_y
        filt2 = [d for d in filt1 if lo_x <= d[0] <= hi_x and lo_y <= d[1] <= hi_y]
    else:
        filt2 = filt1

    # 3. Fallback if too few detections survive. Only check absolute count,
    #    NOT detection-rate — in talking-head video with secondary faces
    #    (chat overlays, PIPs), the biggest-face filter can legitimately
    #    discard 80%+ of detections, leaving a small but high-quality set.
    #    Falling back to the raw median would undo the filter entirely.
    used = filt2
    if len(used) < 8:
        log.warning(
            "%s: only %d viable detections after filtering. "
            "Falling back to median of detections that passed only the "
            "absolute-height floor.",
            src_video.name, len(used),
        )
        # Prefer filt0 (passed abs height) over raw — still better than
        # nothing, but doesn't include the smallest-face detections.
        used = filt0 if len(filt0) >= 8 else raw

    # Use median for robustness against any remaining outliers.
    avg_face_x = statistics.median(d[0] for d in used)
    avg_face_y = statistics.median(d[1] for d in used)
    avg_face_h = statistics.median(d[2] for d in used)

    # Sanity check: clamp to a plausible region (don't let the crop go off the
    # speaker entirely if the median itself is bad).
    # We don't impose a fixed region (speakers move around), but we do reject
    # final positions that are within 10% of the frame edge — that would mean
    # the crop is mostly going to be empty background.
    edge_margin_x = src_w * 0.10
    edge_margin_y = src_h * 0.10
    if not (edge_margin_x <= avg_face_x <= src_w - edge_margin_x and
            edge_margin_y <= avg_face_y <= src_h - edge_margin_y):
        log.warning(
            "%s: median face center (%.0f, %.0f) outside safe region. "
            "Clamping toward frame center.",
            src_video.name, avg_face_x, avg_face_y,
        )
        avg_face_x = max(edge_margin_x, min(avg_face_x, src_w - edge_margin_x))
        avg_face_y = max(edge_margin_y, min(avg_face_y, src_h - edge_margin_y))

    output_target_y = 1920 * cfg.face_target_y
    output_target_h = 1920 * cfg.face_target_height

    # Desired zoom: scale that puts source face_h into output_target_h
    zoom = output_target_h / avg_face_h

    # Crop region in source must fit within source bounds.
    min_zoom = max(1080.0 / src_w, 1920.0 / src_h)
    if zoom < min_zoom:
        log.debug("Reframe %s: bumping zoom %.3f -> %.3f to fit source",
                  src_video.name, zoom, min_zoom)
        zoom = min_zoom

    crop_w_src = 1080.0 / zoom
    crop_h_src = 1920.0 / zoom

    # Crop origin so source(face_x) -> output(540) and source(face_y) -> output(target_y)
    crop_x = avg_face_x - 540.0 / zoom
    crop_y = avg_face_y - output_target_y / zoom

    # Clamp to source
    crop_x = max(0.0, min(crop_x, src_w - crop_w_src))
    crop_y = max(0.0, min(crop_y, src_h - crop_h_src))

    crop_w_int = int(round(crop_w_src))
    crop_h_int = int(round(crop_h_src))
    crop_x_int = int(round(crop_x))
    crop_y_int = int(round(crop_y))

    eff_face_x = (avg_face_x - crop_x) * zoom
    eff_face_y = (avg_face_y - crop_y) * zoom
    log.info(
        "Reframe %s: %d raw -> %d filt detections, src face=(%.0f,%.0f,h=%.0f) "
        "zoom=%.3f crop=(%d,%d,%dx%d) out_face=(%.0f,%.0f)",
        src_video.name, len(raw), len(used), avg_face_x, avg_face_y, avg_face_h,
        zoom, crop_x_int, crop_y_int, crop_w_int, crop_h_int,
        eff_face_x, eff_face_y,
    )

    vf = f"crop={crop_w_int}:{crop_h_int}:{crop_x_int}:{crop_y_int},scale=1080:1920:flags=lanczos"
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(src_video),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
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
