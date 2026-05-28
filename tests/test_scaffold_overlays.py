"""Tests for scaffold._build_callouts and _build_hook — schema/validation."""
from __future__ import annotations

from shortsmith.config import Config
from shortsmith.scaffold import _build_callouts, _build_hook


def test_callouts_empty_when_none():
    cfg = Config()
    assert _build_callouts({"callouts": []}, 1, 60.0, cfg) == []
    assert _build_callouts({}, 1, 60.0, cfg) == []


def test_callout_legacy_color_mapping():
    cfg = Config()
    clip = {"callouts": [
        {"local_start": 5, "duration": 2, "text": "BIT ME", "color": "orange"},
        {"local_start": 10, "duration": 2, "text": "WEALTH", "color": "cyan"},
    ]}
    out = _build_callouts(clip, 1, 60.0, cfg)
    assert out[0]["color"] == "red"   # orange -> red
    assert out[1]["color"] == "gold"  # cyan -> gold


def test_callout_invalid_style_falls_back_to_caption():
    cfg = Config()
    clip = {"callouts": [{"local_start": 5, "duration": 2, "text": "X", "style": "explosion"}]}
    out = _build_callouts(clip, 1, 60.0, cfg)
    assert out[0]["style"] == "caption"


def test_callout_clamps_start_and_duration():
    cfg = Config()
    # local_start beyond clip length, duration absurd
    clip = {"callouts": [{"local_start": 999, "duration": 999, "text": "LATE"}]}
    out = _build_callouts(clip, 1, 30.0, cfg)
    # Start is pinned into the clip, leaving at least 0.5s of room.
    assert out[0]["local_start"] <= 30.0 - 0.5 + 0.001
    # Duration respects the 0.6s legibility floor; end may exceed the clip by
    # at most that floor in the degenerate "start at the very end" case.
    assert out[0]["duration"] >= 0.6 - 0.001
    assert out[0]["local_start"] + out[0]["duration"] <= 30.0 + 0.6


def test_callout_accent_wraps_span():
    cfg = Config()
    clip = {"callouts": [{"local_start": 1, "duration": 2, "text": "BIT ME", "accent": ["BIT"], "color": "red"}]}
    out = _build_callouts(clip, 1, 60.0, cfg)
    assert 'class="em-red"' in out[0]["html"]


def test_hook_none_when_missing():
    assert _build_hook({}, 60.0) is None
    assert _build_hook({"hook": {"text": "   "}}, 60.0) is None


def test_hook_color_and_duration_clamp():
    hook = _build_hook({"hook": {"text": "Don't be\nexit liquidity.",
                                 "accent": ["liquidity"], "color": "orange",
                                 "duration": 99}}, 60.0)
    assert hook is not None
    assert hook["color"] == "red"            # orange -> red
    assert hook["duration"] <= 60.0 * 0.30 + 0.001  # clamped to <=30% of clip
    assert "<br>" in hook["html"]            # newline -> <br>


def test_hook_uppercases_and_accents():
    hook = _build_hook({"hook": {"text": "be exit liquidity",
                                 "accent": ["liquidity"], "color": "gold"}}, 60.0)
    assert "BE EXIT" in hook["html"]          # slam style uppercases
    assert 'class="em-gold"' in hook["html"]  # accent wrapped
