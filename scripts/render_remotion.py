"""Render a scaffolded short with Remotion, layered on top of the Hyperframes render.

Reads a `short-NN-<slug>/` project folder produced by the pipeline's scaffold
step and renders a 1080x1920 MP4 that:

  * uses the Hyperframes `renders/final.mp4` (or `final_sfx.mp4`) as the base so
    its hooks/callouts stay 100% intact (Hyperframes is never modified),
  * optionally overlays word-level captions in a platform-safe band (below the
    speaker's face, above the app UI), which yield/fade out whenever a
    Hyperframes overlay (hook/callout) or a b-roll cutaway is active,
  * inserts manual b-roll cutaway slides at timestamps that fall in the free
    gaps between Hyperframes overlays.

It is non-destructive: writes `renders/final_remotion.mp4`, leaving the
Hyperframes `final.mp4` untouched.

Usage:
    uv run python scripts/render_remotion.py <short-folder> [options]

Options:
    --no-captions          Render without captions (b-roll + base only).
    --platform P           Caption safe-band preset: tiktok|instagram|youtube|generic
    --base B               Base video: auto (default) | hyperframes | final | sfx | clip
    --broll PATH           B-roll slide list JSON (default: <short>/broll.json if present)
    --output NAME          Output filename in renders/ (default: final_remotion.mp4)
    --open                 Open the result when done.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REMOTION_DIR = Path(__file__).resolve().parent.parent / "remotion"
STYLES_DIR = Path(__file__).resolve().parent.parent / "templates" / "styles"
ENTRY = "src/index.ts"
COMPOSITION = "Short"
FPS = 30

# Fallback palette if no style preset resolves.
DEFAULT_PALETTE = {
    "primary": "#f5c542",
    "secondary": "#37bdf8",
    "accent": "#34c759",
    "bg": "#07121c",
}

# Caption safe-band presets (fractions of height). Face center is ~0.40 with
# height ~0.32 (shortsmith reframe), so face bottom ~0.56 -> band top stays
# below that. Band bottom stays above each app's bottom UI zone.
PLATFORM_BANDS = {
    "tiktok": {"top": 0.60, "bottom": 0.78},
    "instagram": {"top": 0.58, "bottom": 0.76},
    "youtube": {"top": 0.62, "bottom": 0.82},
    "generic": {"top": 0.60, "bottom": 0.80},
}


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def _kit_renders_dir() -> Path | None:
    """Kit-level renders folder (<kit>/renders), where `npx hyperframes render`
    drops its timestamped output. None if shortsmith.config isn't importable."""
    try:
        from shortsmith.config import AUTO_SHORTS_ROOT
        return AUTO_SHORTS_ROOT.parent.parent / "renders"
    except Exception:  # noqa: BLE001 - standalone use without the kit
        return None


def _is_our_output(p: Path) -> bool:
    """True for files this layer produces (so they're never mistaken for a
    Hyperframes base render): final_remotion*, final_sfx*, and _-prefixed temps
    like _base.mp4 / _sfx_tmp.mp4."""
    return (
        p.stem.startswith("final_remotion")
        or p.stem.startswith("final_sfx")
        or p.stem.startswith("_")
    )


def _hyperframes_renders(short_dir: Path) -> list[Path]:
    """Candidate Hyperframes base renders for this short, oldest→newest.

    Hyperframes writes either to the project's own renders/ (older flow:
    renders/final.mp4) or, after `npx hyperframes render`, to a kit-level
    timestamped file <proj>_<stamp>.mp4. We gather both and exclude our own
    derived outputs so re-runs stay idempotent."""
    cands: list[Path] = []
    rdir = short_dir / "renders"
    if rdir.is_dir():
        cands += [p for p in rdir.glob("*.mp4") if not _is_our_output(p)]
    kit = _kit_renders_dir()
    if kit and kit.is_dir():
        cands += [p for p in kit.glob(f"{short_dir.name}_*.mp4") if not _is_our_output(p)]
    return sorted(cands, key=lambda p: p.stat().st_mtime)


def _pick_base(short_dir: Path, mode: str) -> Path:
    """Return the absolute path to the chosen base video.

    Modes:
      hyperframes — newest Hyperframes render (project renders/ or kit-level).
      sfx/final/clip — that specific project file.
      auto — sfx > newest Hyperframes render > clip.
    """
    renders = short_dir / "renders"
    explicit = {
        "sfx": renders / "final_sfx.mp4",
        "final": renders / "final.mp4",
        "clip": short_dir / "assets" / "clip-edit.mp4",
    }
    if mode in explicit:
        p = explicit[mode]
        if not p.exists():
            sys.exit(f"Requested base {mode!r} not found at {p}")
        return p
    if mode == "hyperframes":
        hf = _hyperframes_renders(short_dir)
        if not hf:
            sys.exit(f"No Hyperframes render found for {short_dir.name} "
                     f"(looked in renders/ and kit-level renders)")
        return hf[-1]
    # auto
    if explicit["sfx"].exists():
        return explicit["sfx"]
    hf = _hyperframes_renders(short_dir)
    if hf:
        return hf[-1]
    if explicit["clip"].exists():
        return explicit["clip"]
    sys.exit(f"No base video found in {short_dir} (need a Hyperframes render or assets/clip-edit.mp4)")


# Pure filler tokens that should never appear as caption words. Matched on the
# token stripped of surrounding punctuation, lowercased — so "Um," / "uh." go too.
_FILLER_WORDS = {"um", "uh", "uhm", "umm", "uhh", "erm", "mm", "mmm", "hmm"}


# Band thickness (~2 lines of 96px Anton + padding) and clearance, as fractions
# of the 1920px frame height. Bottom-UI limits keep captions above each app's
# chrome; top limit keeps an above-head band off the very edge.
_BAND_H = 0.13
_BAND_GAP = 0.025
_BOTTOM_UI_LIMIT = {"tiktok": 0.86, "instagram": 0.84, "youtube": 0.90, "generic": 0.88}
_TOP_LIMIT = 0.05


def _choose_band(face_top: float, face_bottom: float, platform: str) -> dict:
    """Pure band-selection given the face's vertical extent (fractions of frame
    height). Below the chin if the band fits above the bottom-UI limit, else
    above the head if it clears the top edge, else the static platform band.

    Split out from _face_aware_band so the geometry is unit-testable without a
    video / OpenCV.
    """
    default = PLATFORM_BANDS.get(platform, PLATFORM_BANDS["generic"])
    bottom_limit = _BOTTOM_UI_LIMIT.get(platform, _BOTTOM_UI_LIMIT["generic"])

    below_top = face_bottom + _BAND_GAP
    if below_top + _BAND_H <= bottom_limit:
        return {"top": round(below_top, 4), "bottom": round(below_top + _BAND_H, 4)}

    above_bottom = face_top - _BAND_GAP
    above_top = above_bottom - _BAND_H
    if above_top >= _TOP_LIMIT:
        return {"top": round(above_top, 4), "bottom": round(above_bottom, 4)}

    return default  # face fills the frame — no clean spot, keep default


def _face_aware_band(base_abs: Path, platform: str) -> dict:
    """Pick a caption safe-band that avoids the speaker's face.

    Samples frames from the base render, finds the face's vertical extent with
    YuNet (same model reframe uses), and places the band in the clear space:
    below the chin if it fits above the platform's bottom-UI zone, else above
    the head. Falls back to the static platform band if no face is found or
    OpenCV / the model is unavailable.
    """
    default = PLATFORM_BANDS.get(platform, PLATFORM_BANDS["generic"])
    try:
        import cv2  # type: ignore

        from shortsmith.config import Config
    except Exception:
        return default

    cfg = Config()
    model = getattr(cfg, "yunet_model_path", None)
    if not model or not Path(model).exists():
        return default

    cap = cv2.VideoCapture(str(base_abs))
    if not cap.isOpened():
        return default
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1080
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1920
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    thr = float(getattr(cfg, "yunet_score_threshold", 0.6))
    try:
        detector = cv2.FaceDetectorYN_create(str(model), "", (W, H), thr, 0.3, 5000)
    except Exception:
        cap.release()
        return default

    tops: list[float] = []
    bottoms: list[float] = []
    n = 12
    idxs = [int(total * i / (n + 1)) for i in range(1, n + 1)] if total > n else list(range(total))
    for fi in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ret, frame = cap.read()
        if not ret:
            continue
        _, faces = detector.detect(frame)
        if faces is not None and len(faces) > 0:
            best = max(faces, key=lambda f: f[-1])
            if float(best[-1]) >= thr:
                y, h = float(best[1]), float(best[3])
                tops.append(max(0.0, y / H))
                bottoms.append(min(1.0, (y + h) / H))
    cap.release()

    if len(bottoms) < 3:
        return default

    def pct(arr: list[float], p: float) -> float:
        arr = sorted(arr)
        k = min(len(arr) - 1, max(0, round(p * (len(arr) - 1))))
        return arr[k]

    # 80th pct chin (respects downward head moves without chasing one outlier);
    # 20th pct hairline for the above-head option.
    # 80th pct chin (respects downward head moves without chasing one outlier);
    # 20th pct hairline for the above-head option.
    return _choose_band(pct(tops, 0.20), pct(bottoms, 0.80), platform)


def _drop_fillers(words: list[dict]) -> list[dict]:
    """Remove standalone filler interjections (um/uh/…) from caption words so the
    karaoke captions stay clean. The underlying audio is untouched — this only
    affects what text gets drawn on screen."""
    out = []
    for w in words:
        token = str(w.get("text", "")).strip().strip(".,!?;:—-").lower()
        if token in _FILLER_WORDS:
            continue
        out.append(w)
    return out


def _clip_for(short_dir: Path) -> dict | None:
    """Load the source clip spec (hook + callouts + segments + viral_score, ...)
    for a `short-NN-<slug>` folder from its sibling `_clips.json`. Returns None
    if the folder name doesn't parse or the file isn't there."""
    m = re.match(r"short-(\d+)-", short_dir.name)
    if not m:
        return None
    rank = int(m.group(1))
    clips_path = short_dir.parent / "_clips.json"
    if not clips_path.exists():
        return None
    try:
        clips = json.loads(clips_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return next((c for c in clips if c.get("rank") == rank), None)


def _overlay_windows(short_dir: Path, clip_duration: float) -> list[dict]:
    """Derive Hyperframes overlay time windows (hook + callouts) from the source
    _clips.json, replicating scaffold.py's clamping so the windows match what
    Hyperframes actually rendered."""
    clip = _clip_for(short_dir)
    if not clip:
        return []

    windows: list[dict] = []

    raw_hook = clip.get("hook")
    if raw_hook and str(raw_hook.get("text", "")).strip():
        dur = float(raw_hook.get("duration", 2.6))
        dur = max(1.5, min(dur, max(2.0, clip_duration * 0.30)))
        windows.append({"start": 0.0, "end": round(dur, 3)})

    for co in (clip.get("callouts") or []):
        try:
            ls = float(co["local_start"])
            dur = float(co.get("duration", 2.0))
        except (KeyError, ValueError, TypeError):
            continue
        if not str(co.get("text", "")).strip():
            continue
        ls = max(0.0, min(ls, max(0.0, clip_duration - 0.5)))
        dur = max(0.6, min(dur, clip_duration - ls))
        windows.append({"start": round(ls, 3), "end": round(ls + dur, 3)})

    windows.sort(key=lambda w: w["start"])
    return windows


def _load_broll(short_dir: Path, broll_arg: str | None) -> list[dict]:
    if broll_arg:
        path = Path(broll_arg)
    else:
        path = short_dir / "broll.json"
        if not path.exists():
            return []
    if not path.exists():
        sys.exit(f"B-roll list not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        sys.exit(f"B-roll JSON must be a list of slides: {path}")
    return data


def _resolve_palette(style_name: str) -> dict:
    """Map a style preset's style.json colors onto the b-roll Palette so cutaway
    slides color-match the Hyperframes overlays. Falls back gracefully."""
    style_path = STYLES_DIR / style_name / "style.json"
    if not style_path.exists():
        style_path = STYLES_DIR / "xrp-revolution" / "style.json"
    if not style_path.exists():
        return dict(DEFAULT_PALETTE)
    try:
        colors = json.loads(style_path.read_text(encoding="utf-8")).get("colors", {})
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_PALETTE)
    return {
        "primary": colors.get("gold", DEFAULT_PALETTE["primary"]),
        "secondary": colors.get("red", DEFAULT_PALETTE["secondary"]),
        "accent": colors.get("green", DEFAULT_PALETTE["accent"]),
        "bg": colors.get("background", DEFAULT_PALETTE["bg"]),
    }


def _merge_broll(short_dir: Path, broll_arg: str | None) -> list[dict]:
    """Merge auto (broll.auto.json) + manual (broll.json / --broll) slide lists.
    Manual slides win on time overlap — auto slides that collide with a manual
    one are dropped, so hand edits always take precedence over generated ones."""
    auto_path = short_dir / "broll.auto.json"
    auto: list[dict] = []
    if auto_path.exists():
        try:
            data = json.loads(auto_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                auto = data
        except json.JSONDecodeError:
            print(f"  ! ignoring malformed {auto_path.name}")

    manual = _load_broll(short_dir, broll_arg)

    def span(s: dict) -> tuple[float, float] | None:
        try:
            return float(s["start"]), float(s["end"])
        except (KeyError, ValueError, TypeError):
            return None

    manual_spans = [sp for sp in (span(s) for s in manual) if sp]
    kept_auto = []
    for s in auto:
        sp = span(s)
        if sp and any(sp[0] < mb and sp[1] > ma for ma, mb in manual_spans):
            print(f"  ! auto b-roll {sp[0]}-{sp[1]}s overridden by a manual slide")
            continue
        kept_auto.append(s)

    merged = manual + kept_auto
    merged.sort(key=lambda s: float(s.get("start", 0)))
    return merged


def _validate_broll(broll: list[dict], overlays: list[dict], duration: float) -> list[dict]:
    """Drop slides that collide with a Hyperframes overlay window or run past the
    clip; warn about each. Returns the kept slides."""
    kept: list[dict] = []
    for s in broll:
        try:
            a, b = float(s["start"]), float(s["end"])
        except (KeyError, ValueError, TypeError):
            print(f"  ! skipping b-roll slide with bad start/end: {s!r}")
            continue
        if b <= a:
            print(f"  ! skipping b-roll slide with end<=start: {a}-{b}")
            continue
        if b > duration + 0.05:
            print(f"  ! skipping b-roll slide past clip end ({a}-{b} > {duration:.1f}s)")
            continue
        clash = next((w for w in overlays if a < w["end"] and b > w["start"]), None)
        if clash:
            print(f"  ! skipping b-roll {a}-{b}s: collides with Hyperframes overlay "
                  f"{clash['start']}-{clash['end']}s")
            continue
        kept.append(s)
    return kept


def _vfx_events(short_dir: Path, words: list[dict],
                clip_duration: float) -> list[dict]:
    """Plan the VFX overlay events (glare / zoom-punch / flash) for this short,
    in the prop shape the Remotion VFX layer consumes. Empty list if disabled
    or if the clip spec / config isn't reachable — VFX is purely additive."""
    try:
        from shortsmith.config import Config
        from shortsmith.vfx import plan_vfx_events
    except Exception:  # noqa: BLE001 - standalone use w/o the package
        return []
    cfg = Config()
    if not getattr(cfg, "vfx_enabled", True):
        return []
    clip = _clip_for(short_dir)
    if not clip:
        return []
    return [e.to_props() for e in plan_vfx_events(clip, words, cfg, clip_duration)]


def render(short_dir: Path, *, captions: bool, platform: str, base_mode: str,
           broll_arg: str | None, output: str, style: str, open_after: bool) -> Path:
    short_dir = short_dir.resolve()
    words_path = short_dir / "assets" / "words.json"
    if captions and not words_path.exists():
        sys.exit(f"No words.json at {words_path} (use --no-captions to skip captions)")

    base_abs = _pick_base(short_dir, base_mode)
    duration = _probe_duration(base_abs)

    # Remotion's staticFile() resolves baseVideo relative to --public-dir (the
    # short folder). A kit-level Hyperframes render lives outside it, so stage a
    # copy inside renders/ (named _base.* so it's skipped by _hyperframes_renders)
    # and clean it up after the render.
    staged_base: Path | None = None
    try:
        base_rel = base_abs.relative_to(short_dir).as_posix()
    except ValueError:
        staged_base = short_dir / "renders" / f"_base{base_abs.suffix}"
        staged_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_abs, staged_base)
        base_rel = staged_base.relative_to(short_dir).as_posix()

    words = json.loads(words_path.read_text(encoding="utf-8")) if words_path.exists() else []
    words = _drop_fillers(words)
    overlays = _overlay_windows(short_dir, duration)
    broll = _validate_broll(_merge_broll(short_dir, broll_arg), overlays, duration)
    band = _face_aware_band(base_abs, platform) if captions else PLATFORM_BANDS.get(platform, PLATFORM_BANDS["generic"])
    palette = _resolve_palette(style)

    vfx_events = _vfx_events(short_dir, words, duration)

    props = {
        "baseVideo": base_rel,
        "durationInSeconds": duration,
        "fps": FPS,
        "captionsEnabled": captions,
        "words": words,
        "captionBand": band,
        "captionMaxWords": 3,
        "captionFadeSeconds": 0.2,
        "overlayWindows": overlays,
        "broll": broll,
        "palette": palette,
        "vfxEvents": vfx_events,
    }

    out_path = short_dir / "renders" / output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(props, f, ensure_ascii=False)
        props_file = Path(f.name)

    print(f"Rendering {short_dir.name} -> {out_path.name}")
    print(f"  base={base_rel} ({duration:.1f}s)  captions={'on' if captions else 'off'} "
          f"({platform})  overlays={len(overlays)}  broll={len(broll)}  palette={style}  "
          f"band=[{band['top']:.2f},{band['bottom']:.2f}]  vfx={len(vfx_events)}")

    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    cmd = [
        npx, "remotion", "render", ENTRY, COMPOSITION, str(out_path),
        f"--props={props_file}",
        f"--public-dir={short_dir}",
    ]
    try:
        subprocess.run(cmd, cwd=REMOTION_DIR, check=True)
    finally:
        props_file.unlink(missing_ok=True)
        if staged_base is not None:
            staged_base.unlink(missing_ok=True)

    print(f"Done: {out_path}")
    if open_after:
        subprocess.run(["cmd", "/c", "start", "", str(out_path)], check=False)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a shortsmith short with Remotion (layered on Hyperframes).")
    ap.add_argument("short_dir", type=Path, help="Path to a short-NN-<slug> folder")
    ap.add_argument("--no-captions", dest="captions", action="store_false", help="Render without captions")
    ap.add_argument("--platform", default="generic", choices=sorted(PLATFORM_BANDS),
                    help="Caption safe-band preset (default: generic)")
    ap.add_argument("--base", dest="base_mode", default="auto",
                    choices=["auto", "hyperframes", "final", "sfx", "clip"],
                    help="Base video (default: auto -> sfx>hyperframes>clip; "
                         "'hyperframes' = newest Hyperframes render, project or kit-level)")
    ap.add_argument("--broll", default=None, help="Manual b-roll slide list JSON (default: <short>/broll.json)")
    ap.add_argument("--style", default=os.environ.get("SHORTSMITH_STYLE", "xrp-revolution"),
                    help="Style preset whose palette colors the b-roll (default: $SHORTSMITH_STYLE or xrp-revolution)")
    ap.add_argument("--output", default="final_remotion.mp4", help="Output filename in renders/")
    ap.add_argument("--open", action="store_true", help="Open the result when done")
    args = ap.parse_args()
    render(args.short_dir, captions=args.captions, platform=args.platform,
           base_mode=args.base_mode, broll_arg=args.broll, output=args.output,
           style=args.style, open_after=args.open)


if __name__ == "__main__":
    main()
