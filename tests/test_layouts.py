"""Tests for saved layout presets.

A preset is a format that was tuned on real footage and saved so the next video
gets the same result without re-deriving it. The tests that matter are therefore
about the shipped preset staying valid and honouring the platform safe areas —
a preset that silently drifts renders a whole batch wrong.
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from shortsmith.layouts import (
    DEFAULT_PRESET,
    LAYOUTS_DIR,
    LayoutSpec,
    list_presets,
    load_preset,
)

# Where each app draws its chrome, as fractions of the 1920px frame. Faces must
# stay clear of these or the platform covers them at publish time.
TOP_UI = 0.07      # status/nav bar
BOTTOM_UI = 0.83   # caption, username, music ticker


def test_the_default_preset_ships():
    assert DEFAULT_PRESET in list_presets()
    assert (LAYOUTS_DIR / f"{DEFAULT_PRESET}.json").exists()


def test_every_shipped_preset_loads_and_has_valid_geometry():
    presets = list_presets()
    assert presets, "no layout presets found"
    for name in presets:
        spec = load_preset(name)          # raises if the numbers do not fit
        lay = spec.layout()
        assert lay.top.w == lay.top.h     # squares
        assert lay.bottom.w == lay.bottom.h
        assert lay.top.w == lay.bottom.w  # equal billing for both speakers


def test_default_preset_keeps_both_faces_out_of_the_platform_ui():
    """The change this preset exists to encode: margins that stop app metadata
    from covering either speaker's face."""
    spec = load_preset()
    lay = spec.layout()
    # Face box as it lands after each square is scaled to the panel. 0.52 is the
    # measured worst case from the source this was tuned on (the speaker sitting
    # closest to their webcam sets the shared face fraction).
    face_h = lay.top.h * 0.52
    for panel, target in ((lay.top, spec.face_target_y_top),
                          (lay.bottom, spec.face_target_y_bottom)):
        centre = panel.y + panel.h * target
        top, bottom = centre - face_h / 2, centre + face_h / 2
        assert top > TOP_UI * lay.height, f"face top {top} under the status bar"
        assert bottom < BOTTOM_UI * lay.height, f"face bottom {bottom} under the caption UI"


def test_default_preset_leaves_a_bottom_safe_margin():
    lay = load_preset().layout()
    assert lay.bottom.y + lay.bottom.h < lay.height, (
        "bottom panel is flush to the frame edge — the platform caption overlay "
        "would sit on the lower speaker"
    )


def test_faces_lean_towards_the_middle_of_the_frame():
    # Top speaker's face sits low in its square, bottom speaker's sits high, so
    # both pull away from the frame edges where the chrome lives.
    spec = load_preset()
    assert spec.face_target_y_top > spec.face_target_y_bottom


def test_captions_still_fit_between_the_panels():
    from scripts.render_remotion import fit_caption_font

    lay = load_preset().layout()
    assert fit_caption_font(lay.caption_band) >= 60  # still legible on a phone


def test_unknown_preset_names_the_alternatives():
    with pytest.raises(FileNotFoundError, match="available"):
        load_preset("does-not-exist")


def test_unknown_keys_in_a_preset_are_ignored(tmp_path, monkeypatch):
    # A preset written against a newer shortsmith must still load.
    import shortsmith.layouts as layouts

    monkeypatch.setattr(layouts, "LAYOUTS_DIR", tmp_path)
    (tmp_path / "future.json").write_text(
        json.dumps({"name": "future", "panel": 700, "some_new_key": 42}),
        encoding="utf-8",
    )
    spec = layouts.load_preset("future")
    assert spec.panel == 700
    assert not hasattr(spec, "some_new_key")


def test_preset_is_immutable():
    spec = load_preset()
    with pytest.raises(FrozenInstanceError):
        spec.panel = 999  # type: ignore[misc]


def test_face_target_selects_per_panel():
    spec = LayoutSpec(face_target_y_top=0.48, face_target_y_bottom=0.38)
    assert spec.face_target_y(0) == 0.48
    assert spec.face_target_y(1) == 0.38


# --- Pinned tiles -----------------------------------------------------------
# Brightness detection reads the webcam tiles off a frame. It cannot see a tile
# whose own content is dark, so a preset may pin the geometry measured off the
# real source instead. Getting this wrong frames the wrong part of the picture
# for a whole batch, so a pinned tile that does not fit is an error, never a
# clamp.


def test_a_preset_without_pinned_tiles_still_detects():
    assert load_preset(DEFAULT_PRESET).pinned_tiles(1920, 1080) is None


def test_pinned_tiles_are_returned_in_source_pixels():
    spec = LayoutSpec(tiles=((0, 0, 955, 1080), (965, 0, 955, 1080)))
    tiles = spec.pinned_tiles(1920, 1080)
    assert [(t.x, t.y, t.w, t.h) for t in tiles] == [
        (0, 0, 955, 1080),
        (965, 0, 955, 1080),
    ]


def test_pinned_tiles_reject_a_frame_they_do_not_fit():
    spec = LayoutSpec(tiles=((0, 0, 955, 1080), (965, 0, 955, 1080)))
    with pytest.raises(ValueError, match="1280x720"):
        spec.pinned_tiles(1280, 720)


@pytest.mark.parametrize(
    "tiles, match",
    [
        ((( 0, 0, 955, 1080),), "exactly 2"),
        (((0, 0, 955, 1080), (965, 0, 955, 1080), (0, 0, 10, 10)), "exactly 2"),
        (((0, 0, 955), (965, 0, 955, 1080)), r"\[x, y, w, h\]"),
        (((0, 0, 0, 1080), (965, 0, 955, 1080)), "does not fit"),
        (((-1, 0, 955, 1080), (965, 0, 955, 1080)), "does not fit"),
    ],
)
def test_malformed_pinned_tiles_fail_loudly(tiles, match):
    with pytest.raises(ValueError, match=match):
        LayoutSpec(tiles=tiles).pinned_tiles(1920, 1080)


def test_pinned_tiles_survive_a_json_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("shortsmith.layouts.LAYOUTS_DIR", tmp_path)
    (tmp_path / "pinned.json").write_text(
        json.dumps({"name": "pinned", "tiles": [[0, 0, 955, 1080],
                                                [965, 0, 955, 1080]]}),
        encoding="utf-8",
    )
    spec = load_preset("pinned")
    # Tuples, not lists: a frozen spec must not hand out mutable geometry.
    assert spec.tiles == ((0, 0, 955, 1080), (965, 0, 955, 1080))
    assert len(spec.pinned_tiles(1920, 1080)) == 2


def test_host_right_preset_puts_the_right_hand_tile_on_top():
    spec = load_preset("two-speaker-stack-host-right")
    assert spec.order == "rl"
    # Same composition as the preset it varies, so both are safe-area verified.
    base = load_preset(DEFAULT_PRESET)
    assert (spec.panel, spec.top_margin, spec.bottom_margin) == (
        base.panel, base.top_margin, base.bottom_margin)
