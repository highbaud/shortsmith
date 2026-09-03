"""Gallery-view (two-up webcam grid) tile detection and stacked-square layout.

Zoom / Riverside / StreamYard "gallery view" recordings put both speakers on
screen at the same time, side by side, inside a decorative frame. Neither
existing reframe mode can handle that source:

- The static crop averages the two speakers' positions and frames both badly.
- Cut-aware mode has nothing to work with, because the edit never cuts — the
  layout is identical for the whole recording.

The fix is to stop treating it as one shot and treat it as what it really is:
two independent camera feeds that happen to share one frame. Detect the two
tiles, crop a square around each speaker, and stack the squares vertically with
a caption band in the gap between them. Captions then live in dead centre
screen, touching neither face.

Everything in this module is pure geometry — no ffmpeg, no OpenCV, no file I/O
— so the layout math is unit-testable without a video. `detect_tiles` takes an
already-decoded single-channel (grayscale) frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# A gallery frame is a bright tile (or two) floating on a dark surround. Mean
# luminance below this counts as "frame furniture", not speaker video.
DEFAULT_DARK_THRESHOLD = 25.0

# Output canvas. Fixed by the platforms, not worth parameterising.
OUT_W = 1080
OUT_H = 1920

# The caption band has to hold two lines of display type comfortably. Below
# this the captions crowd the panels and the whole point of the layout is lost.
MIN_BAND_PX = 220


@dataclass(frozen=True)
class Rect:
    """Integer pixel rectangle, top-left origin."""

    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def ffmpeg_crop(self) -> str:
        """ffmpeg's `crop` argument order is w:h:x:y, NOT x:y:w:h."""
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


@dataclass(frozen=True)
class StackLayout:
    """Where the two speaker squares and the caption band sit on the canvas."""

    top: Rect
    bottom: Rect
    band_top_px: int
    band_height_px: int
    width: int = OUT_W
    height: int = OUT_H

    @property
    def caption_band(self) -> dict[str, float]:
        """Caption band as height fractions, the shape Remotion's `captionBand`
        prop expects."""
        return {
            "top": round(self.band_top_px / self.height, 4),
            "bottom": round((self.band_top_px + self.band_height_px) / self.height, 4),
        }


def stack_layout(
    panel: int,
    band: int | None = None,
    top_margin: int = 0,
    bottom_margin: int = 0,
    width: int = OUT_W,
    height: int = OUT_H,
) -> StackLayout:
    """Lay out two `panel`x`panel` squares, horizontally centred, separated by a
    caption band, inset from the frame edges by the given margins.

    The margins are what keep faces out of the platform chrome. Every app draws
    its caption, username and music ticker over the bottom of the frame, so a
    panel run flush to y=height puts the lower speaker's chin under the UI.
    Reserving a bottom margin lifts both squares clear of it.

    `band` defaults to whatever is left over. Raises if the numbers leave the
    band too thin to hold captions, which is the failure this layout exists to
    prevent.
    """
    if panel <= 0:
        raise ValueError(f"panel must be positive, got {panel}")
    if panel > width:
        raise ValueError(f"panel {panel} exceeds canvas width {width}")
    if top_margin < 0 or bottom_margin < 0:
        raise ValueError("margins cannot be negative")

    spare = height - top_margin - bottom_margin - 2 * panel
    band_height = spare if band is None else band
    if band_height < MIN_BAND_PX:
        raise ValueError(
            f"panel {panel} with margins {top_margin}/{bottom_margin} leaves only "
            f"{band_height}px for captions (minimum {MIN_BAND_PX}); use a panel of "
            f"at most {(height - top_margin - bottom_margin - MIN_BAND_PX) // 2}px"
        )
    total = top_margin + 2 * panel + band_height + bottom_margin
    if total > height:
        raise ValueError(
            f"layout is {total}px tall, {total - height}px more than the "
            f"{height}px frame"
        )

    x = (width - panel) // 2
    band_top = top_margin + panel
    return StackLayout(
        top=Rect(x, top_margin, panel, panel),
        bottom=Rect(x, band_top + band_height, panel, panel),
        band_top_px=band_top,
        band_height_px=band_height,
        width=width,
        height=height,
    )


# ---------------------------------------------------------------------------
# Tile detection
# ---------------------------------------------------------------------------


def _runs(flags: list[bool], min_len: int) -> list[tuple[int, int]]:
    """Inclusive (start, end) spans of consecutive True values, dropping any
    span shorter than `min_len`."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, on in enumerate(flags):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    if start is not None and len(flags) - start >= min_len:
        out.append((start, len(flags) - 1))
    return out


def detect_tiles(
    gray,
    dark_threshold: float = DEFAULT_DARK_THRESHOLD,
    min_tile_frac: float = 0.15,
) -> list[Rect]:
    """Find the speaker tiles in a gallery-view frame, left to right.

    `gray` is a 2-D single-channel frame (numpy array or anything numpy accepts).
    Tiles are bright rectangles separated by dark gutters, so we threshold the
    column means to find the horizontal spans, then re-measure each span's own
    row means to get its exact vertical extent. Measuring vertically per tile
    rather than globally matters when the two cameras are letterboxed
    differently.

    Returns [] when the frame does not look like a gallery grid; the caller
    decides whether that is fatal.
    """
    import numpy as np

    arr = np.asarray(gray, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"expected a 2-D grayscale frame, got shape {arr.shape}")
    h, w = arr.shape

    col_bright = [bool(v) for v in (arr.mean(axis=0) > dark_threshold)]
    spans = _runs(col_bright, min_len=int(w * min_tile_frac))

    tiles: list[Rect] = []
    for x0, x1 in spans:
        row_bright = [
            bool(v) for v in (arr[:, x0 : x1 + 1].mean(axis=1) > dark_threshold)
        ]
        vert = _runs(row_bright, min_len=int(h * min_tile_frac))
        if not vert:
            continue
        # Tallest vertical run wins: a tile's own name badge or a caption strip
        # can produce a second, shorter bright run.
        y0, y1 = max(vert, key=lambda r: r[1] - r[0])
        tiles.append(Rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1))
    return tiles


def looks_like_two_up(tiles: list[Rect], size_tolerance: float = 0.15) -> bool:
    """True when exactly two tiles of roughly equal size were found.

    A lopsided pair means the detection latched onto something that is not a
    speaker grid (a slide, a lower third), and stacking it would produce
    garbage — better to fall back to the ordinary crop.
    """
    if len(tiles) != 2:
        return False
    a, b = tiles
    for lhs, rhs in ((a.w, b.w), (a.h, b.h)):
        if max(lhs, rhs) == 0:
            return False
        if abs(lhs - rhs) / max(lhs, rhs) > size_tolerance:
            return False
    return True


# ---------------------------------------------------------------------------
# Per-speaker square crop
# ---------------------------------------------------------------------------


def square_crop_in_tile(
    face_x: float,
    face_y: float,
    face_h: float,
    tile: Rect,
    face_height_frac: float = 0.30,
    face_target_y: float = 0.44,
    min_side_frac: float = 0.55,
) -> Rect:
    """Square crop around one speaker, in absolute source coordinates.

    `face_x`/`face_y`/`face_h` are the face box centre and height in
    TILE-LOCAL coordinates (i.e. relative to `tile`'s top-left), which is what
    you get by running the detector on the cropped tile.

    The square's side is chosen so the face occupies `face_height_frac` of it,
    giving every speaker the same apparent head size regardless of how far each
    one sits from their webcam. The face is then placed `face_target_y` down the
    square, leaving natural headroom. The result is clamped inside the tile so
    the crop can never pull in the neighbouring speaker or the frame furniture.
    """
    if face_height_frac <= 0:
        raise ValueError("face_height_frac must be positive")

    side = face_h / face_height_frac
    # Never exceed the tile, and never zoom so far in that the shot turns into a
    # nostril close-up when the detector under-measures a face.
    side = min(side, float(tile.w), float(tile.h))
    side = max(side, min(tile.w, tile.h) * min_side_frac)
    side = min(side, float(tile.w), float(tile.h))

    x = face_x - side / 2.0
    y = face_y - side * face_target_y
    x = max(0.0, min(x, tile.w - side))
    y = max(0.0, min(y, tile.h - side))

    # Even dimensions keep libx264 and the yuv420p chroma planes happy.
    s = int(side) // 2 * 2
    return Rect(tile.x + int(round(x)), tile.y + int(round(y)), s, s)


def avoid_corner_badge(
    crop: Rect,
    tile: Rect,
    badge_w_frac: float = 0.32,
    badge_h_frac: float = 0.16,
) -> Rect:
    """Slide a crop sideways so it clears the participant name badge.

    Every gallery app (Zoom, Meet, Teams, Riverside) burns the speaker's name
    into the bottom-LEFT corner of their tile. A square crop centred on a face
    that sits left of centre clips that badge, and half a blue name pill in the
    corner of an otherwise clean panel looks like a mistake.

    Tiles are much wider than they are tall, so there is normally room to push
    the crop right until it clears the badge. Returns the crop unchanged when it
    already clears the badge, when it does not reach far enough down to touch
    it, or when sliding would push it out of the tile — a slightly off-centre
    face beats a clipped badge, but a crop that leaves the tile beats neither.
    """
    local_x = crop.x - tile.x
    local_bottom = (crop.y - tile.y) + crop.h
    badge_right = tile.w * badge_w_frac
    badge_top = tile.h * (1.0 - badge_h_frac)

    # Round the clearance UP: landing half a pixel short still shows a sliver of
    # the badge, which is the whole thing we are avoiding.
    clear_x = math.ceil(badge_right)

    if local_bottom <= badge_top or local_x >= clear_x:
        return crop
    if clear_x + crop.w > tile.w:
        return crop  # nowhere to slide to
    return Rect(tile.x + clear_x, crop.y, crop.w, crop.h)


def center_square_in_tile(tile: Rect, min_side_frac: float = 0.80) -> Rect:
    """Fallback square for a tile where no face was found: centred, sized off
    the tile's short edge."""
    side = int(min(tile.w, tile.h) * min_side_frac) // 2 * 2
    return Rect(
        tile.x + (tile.w - side) // 2,
        tile.y + (tile.h - side) // 2,
        side,
        side,
    )
