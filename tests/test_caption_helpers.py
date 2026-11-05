"""Unit tests for caption-side helpers added in the caption overhaul:
filler removal + face-aware band (render_remotion) and public caption text helpers.
Pure-function tests need no video, OpenCV, or Remotion.
"""
from __future__ import annotations

from pathlib import Path

from shortsmith.captions import normalize_dashes, strip_hashtags  # noqa: E402
from shortsmith.rendering import render_remotion as rr
from shortsmith.scaffold import normalize_dashes as scaffold_normalize_dashes  # noqa: E402


def test_phrase_grouping_uses_explicit_timing_and_word_limits():
    from shortsmith.captions import group_into_segments

    words = [
        {"text": "Keep", "start": 0.0, "end": 0.1},
        {"word": "MY", "start": 0.2, "end": 0.3},
        {"text": "CASE", "start": 1.0, "end": 1.2},
    ]
    defaults = group_into_segments(words)
    one_phrase = group_into_segments(words, gap=2.0, max_words=3)
    single_words = group_into_segments(words, gap=2.0, max_words=1)

    assert [[word["word"] for word in phrase["words"]] for phrase in defaults] == [
        ["Keep", "MY"], ["CASE"],
    ]
    assert [word["word"] for word in one_phrase[0]["words"]] == ["Keep", "MY", "CASE"]
    assert [len(phrase["words"]) for phrase in single_words] == [1, 1, 1]


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


# --- strip_hashtags (post caption text) ---
def test_strip_hashtags_removes_block_and_tidies():
    text = "Big claim here.\n\nMore body.\n\nFollow for more.\n\n#crypto #xrp #investing"
    assert strip_hashtags(text) == "Big claim here.\n\nMore body.\n\nFollow for more."


def test_strip_hashtags_idempotent_on_clean_text():
    text = "Just a clean caption.\n\nNo tags here."
    assert strip_hashtags(text) == text


# --- normalize_dashes (em/en/mojibake -> plain punctuation) ---
def test_normalize_dashes_em_to_comma():
    assert normalize_dashes("portfolio rule \u2014 quotable") == "portfolio rule, quotable"


def test_normalize_dashes_mojibake_to_comma():
    assert normalize_dashes("portfolio rule \ufffd quotable") == "portfolio rule, quotable"


def test_normalize_dashes_numeric_range_to_hyphen():
    assert normalize_dashes("held 2020\u20132021 through it") == "held 2020-2021 through it"


def test_normalize_dashes_idempotent_on_clean():
    s = "Just clean, normal prose here."
    assert normalize_dashes(s) == s


def test_scaffold_keeps_its_public_normalize_dashes_export():
    assert scaffold_normalize_dashes("portfolio rule \u2014 quotable") == "portfolio rule, quotable"


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
