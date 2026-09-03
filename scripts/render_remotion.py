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

# Logo badge on a split-stack short: a mark-only tile on the blurred backdrop
# beside the top speaker's square. Outside both panels and the caption band by
# construction, and below every platform's top bar (<0.07 of the height).
BADGE_SIZE = 120
BADGE_MIN_SIZE = 72
BADGE_MARGIN = 24
BADGE_TOP_CLEAR = 134  # 0.07 * 1920


class LayoutPresetError(RuntimeError):
    """A split-stack clip whose layout preset cannot be loaded."""


class BrollSpecError(RuntimeError):
    """A hand-authored b-roll slide list that cannot be read.

    An exception rather than sys.exit: finalize renders the whole library in
    one process and guards each short with `except Exception`, which SystemExit
    walks straight through, so one unreadable broll.json used to end the run.
    """

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
        check=True, capture_output=True, text=True, encoding="utf-8",
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
    return _choose_band(pct(tops, 0.20), pct(bottoms, 0.80), platform)


CAPTION_MAX_WORDS = 3
CAPTION_FONT_PX = 96


def fit_caption_font(band: dict, max_words: int = CAPTION_MAX_WORDS,
                     height: int = 1920, default: int = CAPTION_FONT_PX) -> int:
    """Largest caption type size that still fits `max_words` stacked lines
    inside `band`, never larger than the default.

    Normal shorts have a face on one side of the band and open frame on the
    other, so an overflowing caption is untidy at worst. A split-stack band has
    a face on BOTH sides, so it has to fit. Worst case is every word on its own
    line, which is what this sizes for.
    """
    band_px = (float(band["bottom"]) - float(band["top"])) * height
    usable = band_px - 32  # the 8px top/bottom margin on each word span
    if max_words <= 0 or usable <= 0:
        return default
    return int(max(48, min(default, usable / (max_words * 1.05))))


def _split_stack_layout(short_dir: Path):
    """The saved stacked layout for this short, or None if it is not one.

    A split-stack clip whose preset cannot be loaded is an error, not a
    fallback: without the geometry the captions would be placed face-aware,
    which on a stacked frame means on top of one of the two faces.
    """
    clip = _clip_for(short_dir)
    if not clip or str(clip.get("layout", "")).lower() != "split-stack":
        return None
    from shortsmith.config import Config
    from shortsmith.layouts import load_preset

    preset = clip.get("layout_preset") or Config().split_stack_preset
    try:
        return load_preset(preset).layout()
    except (FileNotFoundError, ValueError, KeyError, TypeError) as e:
        raise LayoutPresetError(
            f"{short_dir.name}: split-stack layout preset {preset!r} cannot be loaded "
            f"({e}). Captions would land on a face, so this render stops here. Fix "
            "templates/layouts/ or the clip's layout_preset.") from e


def _logo_badge_anchor(layout) -> dict | None:
    """Where a logo badge sits on a split-stack short, in px, or None when the
    backdrop beside the panels is too narrow for a mark."""
    gutter = layout.top.x
    size = min(BADGE_SIZE, gutter - 2 * BADGE_MARGIN)
    if size < BADGE_MIN_SIZE:
        return None
    return {"x": (gutter - size) // 2,
            "y": max(layout.top.y, BADGE_TOP_CLEAR) + BADGE_MARGIN,
            "size": size}


def _is_badge(slide: dict) -> bool:
    return slide.get("type") == "logo" and slide.get("mode") == "badge"


def _clip_caption_band(short_dir: Path) -> dict | None:
    """Caption band dictated by the clip spec, or None to auto-place it.

    Split-stack shorts put a speaker square at the top AND bottom of the frame,
    so face-aware placement has nowhere safe to go: it would drop captions onto
    one of the two faces. The layout already reserves the middle gap for them,
    so use it. An explicit `caption_band` in the clip spec beats everything.
    """
    clip = _clip_for(short_dir)
    if not clip:
        return None
    explicit = clip.get("caption_band")
    if isinstance(explicit, dict) and "top" in explicit and "bottom" in explicit:
        return {"top": float(explicit["top"]), "bottom": float(explicit["bottom"])}
    layout = _split_stack_layout(short_dir)
    return layout.caption_band if layout else None


def _speaker_panels(short_dir: Path) -> list[dict]:
    """Panel rectangles (top, bottom) for a split-stack short.

    Passed to the template explicitly rather than re-derived there from the
    caption band: once the layout gained safe-area margins, the band no longer
    implies where the panels are, and inferring it put the lower name chip on
    the speaker's forehead.
    """
    layout = _split_stack_layout(short_dir)
    if not layout:
        return []
    return [{"x": r.x, "y": r.y, "w": r.w, "h": r.h}
            for r in (layout.top, layout.bottom)]


def _speaker_labels(short_dir: Path) -> list[dict]:
    """Name chips for a split-stack short, from the clip spec's `speakers` list
    (top speaker first). Empty for every other layout."""
    clip = _clip_for(short_dir)
    if not clip:
        return []
    names = clip.get("speakers") or []
    if not isinstance(names, list):
        return []
    positions = ("top", "bottom")
    return [
        {"name": str(n).strip(), "position": positions[i]}
        for i, n in enumerate(names[:2])
        if str(n).strip()
    ]


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
        raise BrollSpecError(f"B-roll list not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise BrollSpecError(f"B-roll JSON cannot be read ({e}): {path}") from e
    if not isinstance(data, list):
        raise BrollSpecError(f"B-roll JSON must be a list of slides: {path}")
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
        except (json.JSONDecodeError, OSError):
            print(f"  ! ignoring malformed {auto_path.name}")
    if auto:
        # Auto person slides are re-verified here, not trusted: a broll.auto.json
        # from before identity verification still names a keyword-search photo.
        # Manual slides are left as written (the escape hatch for a guest with
        # no Wikidata item). Deferred import: gen_broll imports this module.
        import gen_broll
        auto = gen_broll.verify_person_slides(auto, short_dir)

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

    def sort_key(s: dict) -> float:
        # Tolerant: a hand-written slide with a missing or non-numeric start
        # used to raise here, before _validate_broll could report it and drop
        # it. Unsortable slides go first and are named there.
        sp = span(s)
        return sp[0] if sp else 0.0

    merged = manual + kept_auto
    merged.sort(key=sort_key)
    return merged


# What each slide type's Remotion card actually reads. A `list` with no `items`
# or a `logo`/`person` with no `src` does not draw a blank card, it throws
# (`.map` of undefined, `staticFile(undefined)`) and the whole render is lost.
# Slides come from an LLM or a hand-written broll.json, so the payload is never
# guaranteed. Kept in step with remotion/src/Short.tsx `isRenderable`.
_REQUIRED_FIELD: dict[str, str] = {
    "text": "title", "list": "items", "logo": "src", "person": "src",
}


def _missing_payload(slide: dict) -> str:
    """The required field this slide lacks, or "" when it is renderable."""
    kind = slide.get("type")
    if kind == "stat":
        to = slide.get("to")
        numeric_to = isinstance(to, (int, float)) and not isinstance(to, bool)
        return "" if slide.get("value") or numeric_to else "value"
    if kind == "list":
        items = slide.get("items")
        return "" if isinstance(items, list) and items else "items"
    field = _REQUIRED_FIELD.get(str(kind))
    if field is None:
        return f"type ({kind!r} is not a slide type)"
    return "" if slide.get(field) else field


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
        missing = _missing_payload(s)
        if missing:
            print(f"  ! skipping b-roll slide missing {missing}: {s!r}")
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


def _ambient_punches(busy: list[dict], duration: float) -> list[float]:
    """Plan gentle ambient punch-in times in the dead gaps between activity.

    `busy` is every interval where something is already moving (Hyperframes
    overlays, b-roll cutaways, VFX events). We invert it to free gaps and drop a
    punch every ~`every` seconds inside gaps long enough to warrant one, keeping
    a margin from each gap edge so a punch never lands on an overlay entrance.
    Config-gated + fail-open (any import/plan hiccup returns no punches).
    """
    try:
        from shortsmith.config import Config
        cfg = Config()
    except Exception:  # noqa: BLE001 - standalone use without the package
        return []
    if not getattr(cfg, "punch_interrupt_enabled", True) or duration <= 0:
        return []

    every = max(4.0, float(getattr(cfg, "punch_interrupt_every", 10.0)))
    min_gap = float(getattr(cfg, "punch_interrupt_min_gap", 6.0))
    margin = float(getattr(cfg, "punch_interrupt_edge_margin", 1.5))

    # Merge busy intervals.
    ivs = sorted(
        ((max(0.0, float(b["start"])), min(duration, float(b["end"])))
         for b in busy if b.get("end", 0) > b.get("start", 0)),
        key=lambda p: p[0],
    )
    merged: list[list[float]] = []
    for a, b in ivs:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    # Free gaps = complement of merged busy over [0, duration].
    punches: list[float] = []
    cursor = 0.0
    for a, b in merged + [[duration, duration]]:
        gap_start, gap_end = cursor, a
        cursor = max(cursor, b)
        if gap_end - gap_start < min_gap:
            continue
        # First punch ~one interval in, then every `every` seconds, staying a
        # margin clear of both edges.
        t = gap_start + max(margin, every * 0.6)
        while t <= gap_end - margin:
            punches.append(round(t, 2))
            t += every
    return punches


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
    # On a split-stack short the normal logo badge (upper center, sized for a
    # single speaker with headroom) would sit on the top face. The badge moves
    # to the blurred backdrop beside the top square instead; if a preset leaves
    # no room there, badges are dropped and every other slide stays.
    layout = _split_stack_layout(short_dir)
    badge_anchor = _logo_badge_anchor(layout) if layout else None
    if layout and badge_anchor is None and any(_is_badge(s) for s in broll):
        dropped = sum(1 for s in broll if _is_badge(s))
        print(f"  split-stack: no backdrop room beside the panels; dropping {dropped} logo badge(s)")
        broll = [s for s in broll if not _is_badge(s)]
    clip_band = _clip_caption_band(short_dir)
    band = clip_band
    if band is None:
        band = (_face_aware_band(base_abs, platform) if captions
                else PLATFORM_BANDS.get(platform, PLATFORM_BANDS["generic"]))
    # Only shrink type for a clip-dictated band (split-stack). Leaving the
    # auto-placed bands on the fixed 96px keeps every existing short unchanged.
    caption_font = (fit_caption_font(band) if clip_band else CAPTION_FONT_PX)
    palette = _resolve_palette(style)

    vfx_events = _vfx_events(short_dir, words, duration)

    # Ambient punch-ins go in the dead gaps between every other kind of motion.
    busy = (
        [{"start": w["start"], "end": w["end"]} for w in overlays]
        + [{"start": float(s["start"]), "end": float(s["end"])} for s in broll]
        + [{"start": e["t"], "end": e["t"] + e.get("durationMs", 0) / 1000.0}
           for e in vfx_events]
    )
    ambient_punches = _ambient_punches(busy, duration)

    props = {
        "baseVideo": base_rel,
        "durationInSeconds": duration,
        "fps": FPS,
        "captionsEnabled": captions,
        "words": words,
        "captionBand": band,
        "captionMaxWords": CAPTION_MAX_WORDS,
        "captionFontSize": caption_font,
        "captionFadeSeconds": 0.2,
        "overlayWindows": overlays,
        "broll": broll,
        "palette": palette,
        "vfxEvents": vfx_events,
        "ambientPunches": ambient_punches,
        "speakerLabels": _speaker_labels(short_dir),
        "speakerPanels": _speaker_panels(short_dir),
    }
    if badge_anchor:
        props["logoBadgeAnchor"] = badge_anchor

    out_path = short_dir / "renders" / output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(props, f, ensure_ascii=False)
        props_file = Path(f.name)

    print(f"Rendering {short_dir.name} -> {out_path.name}")
    print(f"  base={base_rel} ({duration:.1f}s)  captions={'on' if captions else 'off'} "
          f"({platform})  overlays={len(overlays)}  broll={len(broll)}  palette={style}  "
          f"band=[{band['top']:.2f},{band['bottom']:.2f}]  vfx={len(vfx_events)}  "
          f"punches={len(ambient_punches)}")

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
    try:
        render(args.short_dir, captions=args.captions, platform=args.platform,
               base_mode=args.base_mode, broll_arg=args.broll, output=args.output,
               style=args.style, open_after=args.open)
    except (LayoutPresetError, BrollSpecError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
