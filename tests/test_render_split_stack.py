"""Tests for the split-stack pieces of render_remotion: where a logo badge
goes, and that a broken layout preset stops the render instead of letting
captions fall onto a face."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import render_remotion as rr  # noqa: E402

from shortsmith.gallery import stack_layout  # noqa: E402
from shortsmith.layouts import load_preset  # noqa: E402


def test_badge_sits_on_the_backdrop_beside_the_top_square_below_the_top_bar() -> None:
    layout = load_preset("two-speaker-stack").layout()
    anchor = rr._logo_badge_anchor(layout)
    assert anchor == {"x": 33, "y": 158, "size": 120}
    assert anchor["x"] + anchor["size"] <= layout.top.x, "never inside the top square"
    assert anchor["y"] >= rr.BADGE_TOP_CLEAR, "never under a platform's top bar"
    assert anchor["y"] + anchor["size"] <= layout.top.y + layout.top.h


def test_no_anchor_when_the_backdrop_beside_the_squares_is_too_narrow() -> None:
    edge_to_edge = stack_layout(850)  # squares 115px from the frame edge
    assert rr._logo_badge_anchor(edge_to_edge) is None


def test_only_logo_badges_count_as_badges() -> None:
    assert rr._is_badge({"type": "logo", "mode": "badge"})
    assert not rr._is_badge({"type": "logo", "mode": "full"})
    assert not rr._is_badge({"type": "logo"})
    assert not rr._is_badge({"type": "person", "mode": "badge"})


def _short(tmp_path: Path, clip: dict) -> Path:
    src = tmp_path / "src"
    short = src / "short-01-topic"
    short.mkdir(parents=True, exist_ok=True)
    (src / "_clips.json").write_text(json.dumps([{"rank": 1, **clip}]), encoding="utf-8")
    return short


def test_broken_layout_preset_stops_the_render(tmp_path: Path) -> None:
    short = _short(tmp_path, {"layout": "split-stack", "layout_preset": "no-such-preset"})
    with pytest.raises(rr.LayoutPresetError, match="no-such-preset"):
        rr._split_stack_layout(short)


def test_split_stack_clip_loads_its_preset(tmp_path: Path) -> None:
    short = _short(tmp_path, {"layout": "split-stack"})
    layout = rr._split_stack_layout(short)
    assert layout is not None and layout.top.w == 705


def test_other_layouts_have_no_split_stack_geometry(tmp_path: Path) -> None:
    assert rr._split_stack_layout(_short(tmp_path, {"layout": "static"})) is None
    assert rr._split_stack_layout(_short(tmp_path, {})) is None
