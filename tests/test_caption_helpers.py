"""Unit tests for caption-side helpers added in the caption overhaul:
filler removal + face-aware band (render_remotion) and hashtag stripping
(scaffold). Pure-function tests — no video, OpenCV, or Remotion needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import render_remotion as rr  # noqa: E402

from shortsmith.scaffold import _strip_hashtags  # noqa: E402


# --- _drop_fillers (um/uh removal from on-screen captions) ---
def test_drop_fillers_removes_um_uh_variants():
    words = [
        {"text": "So", "start": 0.0, "end": 0.2},
        {"text": "Um,", "start": 0.2, "end": 0.4},   # capitalized + trailing comma
        {"text": "uh", "start": 0.4, "end": 0.5},
        {"text": "this", "start": 0.5, "end": 0.7},
        {"text": "uhh.", "start": 0.7, "end": 0.8},   # trailing period
        {"text": "works", "start": 0.8, "end": 1.0},
    ]
    assert [w["text"] for w in rr._drop_fillers(words)] == ["So", "this", "works"]


def test_drop_fillers_keeps_content_words():
    words = [{"text": t, "start": 0, "end": 1} for t in ("summer", "humming", "important")]
    assert [w["text"] for w in rr._drop_fillers(words)] == ["summer", "humming", "important"]


# --- _strip_hashtags (post caption text) ---
def test_strip_hashtags_removes_block_and_tidies():
    text = "Big claim here.\n\nMore body.\n\nFollow for more.\n\n#crypto #xrp #investing"
    out = _strip_hashtags(text)
    assert "#" not in out
    assert out.endswith("Follow for more.")
    assert "\n\n\n" not in out


def test_strip_hashtags_idempotent_on_clean_text():
    text = "Just a clean caption.\n\nNo tags here."
    assert _strip_hashtags(text) == text


# --- _choose_band (face-aware caption placement geometry) ---
def test_choose_band_below_chin_when_room():
    band = rr._choose_band(0.25, 0.55, "generic")  # chin at 0.55
    assert band["top"] > 0.55
    assert band["bottom"] <= rr._BOTTOM_UI_LIMIT["generic"]
    assert round(band["bottom"] - band["top"], 4) == rr._BAND_H


def test_choose_band_above_head_when_face_low():
    band = rr._choose_band(0.30, 0.85, "generic")  # chin too low for a below band
    assert band["bottom"] <= 0.30          # sits above the hairline
    assert band["top"] >= rr._TOP_LIMIT


def test_choose_band_falls_back_when_face_fills_frame():
    band = rr._choose_band(0.04, 0.95, "generic")  # no clean gap either way
    assert band == rr.PLATFORM_BANDS["generic"]


def test_face_aware_band_fallback_when_video_missing():
    # Missing file -> VideoCapture can't open -> static platform band.
    band = rr._face_aware_band(Path("does_not_exist_xyz.mp4"), "tiktok")
    assert band == rr.PLATFORM_BANDS["tiktok"]
