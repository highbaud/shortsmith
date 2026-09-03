"""Tests for the gallery (two-up) tile detection and stacked-square layout.

The layout math is where this feature is most likely to go wrong in a way that
looks fine in code and terrible on screen: captions landing on a face, a crop
that quietly pulls in the neighbouring speaker, or two speakers rendered at
wildly different head sizes. All of that is pure geometry, so it is tested here
without touching ffmpeg or a video file.
"""
from __future__ import annotations

import numpy as np
import pytest

from shortsmith.gallery import (
    MIN_BAND_PX,
    OUT_H,
    OUT_W,
    Rect,
    avoid_corner_badge,
    center_square_in_tile,
    detect_tiles,
    looks_like_two_up,
    square_crop_in_tile,
    stack_layout,
)


def _gallery_frame(
    w: int = 3840,
    h: int = 2160,
    tiles: tuple[tuple[int, int, int, int], ...] = (
        (77, 571, 1806, 1018),
        (1957, 571, 1807, 1018),
    ),
) -> np.ndarray:
    """A synthetic gallery frame: bright tiles on a dark surround."""
    frame = np.zeros((h, w), dtype=np.uint8)
    for x, y, tw, th in tiles:
        frame[y : y + th, x : x + tw] = 160
    return frame


# ---------------------------------------------------------------------------
# stack_layout
# ---------------------------------------------------------------------------


def test_stack_layout_fills_the_frame_exactly():
    lay = stack_layout(810)
    assert lay.top == Rect(135, 0, 810, 810)
    assert lay.bottom == Rect(135, 1110, 810, 810)
    # Panels flush to both edges, band exactly the leftover.
    assert lay.top.y == 0
    assert lay.bottom.y + lay.bottom.h == OUT_H
    assert lay.band_top_px == lay.top.h
    assert lay.band_height_px == OUT_H - 2 * 810


def test_margins_lift_the_stack_off_the_platform_ui():
    """The reason margins exist: with the bottom panel flush to the frame edge,
    the lower speaker's chin sits under TikTok's caption overlay."""
    flush = stack_layout(810)  # edge-to-edge: no room for the app's chrome
    inset = stack_layout(705, band=280, top_margin=30, bottom_margin=200)
    assert flush.bottom.y + flush.bottom.h == OUT_H
    assert inset.bottom.y + inset.bottom.h == OUT_H - 200
    # The lower speaker's whole square moves up out of the caption zone.
    assert inset.bottom.y < flush.bottom.y
    assert inset.band_top_px < flush.band_top_px


def test_margins_keep_the_band_between_the_panels():
    lay = stack_layout(705, band=280, top_margin=30, bottom_margin=200)
    assert lay.band_top_px == lay.top.y + lay.top.h
    assert lay.band_top_px + lay.band_height_px == lay.bottom.y


def test_explicit_band_overrides_the_leftover():
    lay = stack_layout(700, band=300, top_margin=20, bottom_margin=200)
    assert lay.band_height_px == 300
    # Not required to fill the frame — the remainder is simply unused.
    assert 20 + 700 + 300 + 700 + 200 <= OUT_H


def test_stack_layout_rejects_a_layout_taller_than_the_frame():
    with pytest.raises(ValueError, match="more than"):
        stack_layout(800, band=400, top_margin=100, bottom_margin=100)


def test_stack_layout_rejects_negative_margins():
    with pytest.raises(ValueError, match="negative"):
        stack_layout(700, bottom_margin=-10)


def test_stack_layout_panels_are_horizontally_centred():
    lay = stack_layout(700)
    for panel in (lay.top, lay.bottom):
        assert panel.x == (OUT_W - 700) // 2
        assert panel.x + panel.w + panel.x == OUT_W


def test_caption_band_sits_between_the_panels():
    lay = stack_layout(810)
    band = lay.caption_band
    # The band must start where the top panel ends and end where the bottom
    # panel begins — that is the whole point of the layout.
    assert band["top"] == pytest.approx(lay.top.h / OUT_H, abs=1e-4)
    assert band["bottom"] == pytest.approx(lay.bottom.y / OUT_H, abs=1e-4)
    assert 0 < band["top"] < band["bottom"] < 1


def test_stack_layout_rejects_a_panel_that_squeezes_out_the_captions():
    # 900px panels leave 120px of band — captions would collide with the faces,
    # which is exactly the failure this layout exists to prevent.
    with pytest.raises(ValueError, match="captions"):
        stack_layout(900)


def test_margins_count_against_the_caption_band():
    stack_layout(800)  # fine with no margins
    with pytest.raises(ValueError, match="captions"):
        stack_layout(800, bottom_margin=200)


def test_stack_layout_largest_allowed_panel_still_fits_the_minimum_band():
    largest = (OUT_H - MIN_BAND_PX) // 2
    assert stack_layout(largest).band_height_px >= MIN_BAND_PX
    with pytest.raises(ValueError):
        stack_layout(largest + 1)


@pytest.mark.parametrize("panel", [0, -10, OUT_W + 1])
def test_stack_layout_rejects_nonsense_panels(panel):
    with pytest.raises(ValueError):
        stack_layout(panel)


# ---------------------------------------------------------------------------
# detect_tiles
# ---------------------------------------------------------------------------


def test_detect_tiles_finds_both_tiles_left_to_right():
    tiles = detect_tiles(_gallery_frame())
    assert len(tiles) == 2
    assert tiles[0].x < tiles[1].x
    # Within a pixel of the planted geometry.
    assert tiles[0].x == pytest.approx(77, abs=1)
    assert tiles[0].w == pytest.approx(1806, abs=2)
    assert tiles[0].h == pytest.approx(1018, abs=2)
    assert tiles[1].x == pytest.approx(1957, abs=1)


def test_detect_tiles_measures_each_tile_height_independently():
    # Cameras letterboxed differently — a global row profile would give both
    # tiles the union of the two heights.
    frame = _gallery_frame(tiles=((100, 400, 1600, 1200), (1900, 600, 1600, 800)))
    tiles = detect_tiles(frame)
    assert len(tiles) == 2
    assert tiles[0].h == pytest.approx(1200, abs=2)
    assert tiles[1].h == pytest.approx(800, abs=2)


def test_detect_tiles_on_a_single_speaker_frame():
    tiles = detect_tiles(_gallery_frame(tiles=((400, 300, 3000, 1600),)))
    assert len(tiles) == 1
    assert not looks_like_two_up(tiles)


def test_detect_tiles_on_an_all_dark_frame_finds_nothing():
    assert detect_tiles(np.zeros((2160, 3840), dtype=np.uint8)) == []


def test_detect_tiles_rejects_a_3d_frame():
    with pytest.raises(ValueError, match="2-D"):
        detect_tiles(np.zeros((100, 100, 3), dtype=np.uint8))


# ---------------------------------------------------------------------------
# looks_like_two_up
# ---------------------------------------------------------------------------


def test_looks_like_two_up_accepts_a_matched_pair():
    assert looks_like_two_up([Rect(0, 0, 1806, 1018), Rect(1957, 0, 1807, 1018)])


def test_looks_like_two_up_rejects_a_lopsided_pair():
    # A speaker tile next to a small PIP is not a gallery; stacking it would
    # produce one giant panel and one postage stamp.
    assert not looks_like_two_up([Rect(0, 0, 1800, 1000), Rect(1900, 0, 400, 240)])


@pytest.mark.parametrize("count", [0, 1, 3])
def test_looks_like_two_up_needs_exactly_two(count):
    assert not looks_like_two_up([Rect(i * 100, 0, 80, 80) for i in range(count)])


# ---------------------------------------------------------------------------
# square_crop_in_tile
# ---------------------------------------------------------------------------


TILE = Rect(1957, 571, 1807, 1018)


def test_square_crop_is_square_and_even():
    crop = square_crop_in_tile(900.0, 450.0, 300.0, TILE)
    assert crop.w == crop.h
    assert crop.w % 2 == 0  # odd dimensions break yuv420p chroma


def test_square_crop_stays_inside_its_tile():
    # A face detected hard against every tile edge must never produce a crop
    # that reaches into the neighbouring speaker or the frame furniture.
    for fx, fy in ((0.0, 0.0), (TILE.w, 0.0), (0.0, TILE.h), (TILE.w, TILE.h)):
        crop = square_crop_in_tile(fx, fy, 300.0, TILE)
        assert crop.x >= TILE.x
        assert crop.y >= TILE.y
        assert crop.x + crop.w <= TILE.x + TILE.w
        assert crop.y + crop.h <= TILE.y + TILE.h


def test_square_crop_returns_absolute_coordinates():
    crop = square_crop_in_tile(TILE.w / 2, TILE.h / 2, 300.0, TILE)
    assert crop.x >= TILE.x  # tile-local input, absolute output


def test_bigger_face_fraction_zooms_in():
    loose = square_crop_in_tile(900.0, 450.0, 250.0, TILE, face_height_frac=0.25)
    tight = square_crop_in_tile(900.0, 450.0, 250.0, TILE, face_height_frac=0.50)
    assert tight.w < loose.w


def test_equal_face_fraction_normalises_two_unequal_speakers():
    """The reason the mode exists: one speaker sits close to their webcam and
    one sits far back. At a shared face fraction both must end up with the same
    apparent head size once their squares are scaled to the same panel."""
    near_h, far_h = 540.0, 325.0
    frac = 0.52
    near = square_crop_in_tile(900.0, 450.0, near_h, TILE, face_height_frac=frac)
    far = square_crop_in_tile(900.0, 450.0, far_h, TILE, face_height_frac=frac)
    # Head size after each square is scaled to the same panel edge.
    assert near_h / near.w == pytest.approx(far_h / far.w, rel=0.02)
    assert near.w > far.w  # the closer speaker needs the wider crop


def test_square_crop_never_exceeds_the_tile_short_edge():
    # A huge detected face would ask for a square taller than the tile.
    crop = square_crop_in_tile(900.0, 450.0, 5000.0, TILE, face_height_frac=0.30)
    assert crop.w <= min(TILE.w, TILE.h)


def test_square_crop_rejects_a_zero_face_fraction():
    with pytest.raises(ValueError):
        square_crop_in_tile(900.0, 450.0, 300.0, TILE, face_height_frac=0.0)


def test_face_target_y_puts_the_face_above_centre():
    crop = square_crop_in_tile(900.0, 500.0, 260.0, TILE, face_height_frac=0.30,
                               face_target_y=0.44)
    face_abs_y = TILE.y + 500.0
    # Face sits above the square's midpoint, leaving headroom.
    assert face_abs_y < crop.y + crop.h / 2


# ---------------------------------------------------------------------------
# avoid_corner_badge
# ---------------------------------------------------------------------------


def test_badge_avoidance_slides_a_clipping_crop_right():
    # Full-height crop starting left of the badge: must slide clear.
    crop = Rect(TILE.x + 400, TILE.y, 1018, 1018)
    fixed = avoid_corner_badge(crop, TILE)
    assert fixed.x > crop.x
    assert fixed.x - TILE.x >= TILE.w * 0.32
    assert fixed.w == crop.w and fixed.y == crop.y  # slide only, no resize
    assert fixed.x + fixed.w <= TILE.x + TILE.w


def test_badge_avoidance_leaves_a_clear_crop_alone():
    crop = Rect(TILE.x + 900, TILE.y, 1018, 1018)
    assert avoid_corner_badge(crop, TILE) == crop


def test_badge_avoidance_ignores_crops_that_stop_above_the_badge():
    # Badge lives in the bottom 16%; a crop ending above it cannot clip it.
    crop = Rect(TILE.x, TILE.y, 700, 700)
    assert avoid_corner_badge(crop, TILE) == crop


def test_badge_avoidance_gives_up_rather_than_leaving_the_tile():
    narrow = Rect(0, 0, 1000, 1000)
    crop = Rect(0, 0, 900, 900)  # sliding would run past the tile's right edge
    assert avoid_corner_badge(crop, narrow) == crop


# ---------------------------------------------------------------------------
# center_square_in_tile
# ---------------------------------------------------------------------------


def test_center_square_is_centred_square_and_inside_the_tile():
    crop = center_square_in_tile(TILE)
    assert crop.w == crop.h
    assert crop.w % 2 == 0
    assert crop.x + crop.w <= TILE.x + TILE.w
    assert crop.y + crop.h <= TILE.y + TILE.h
    assert crop.cx == pytest.approx(TILE.cx, abs=1)
    assert crop.cy == pytest.approx(TILE.cy, abs=1)


# ---------------------------------------------------------------------------
# Rect
# ---------------------------------------------------------------------------


def test_ffmpeg_crop_uses_ffmpegs_argument_order():
    # ffmpeg is w:h:x:y, not x:y:w:h — getting this backwards crops the wrong
    # region and still produces a valid-looking video.
    assert Rect(10, 20, 300, 400).ffmpeg_crop == "crop=300:400:10:20"
