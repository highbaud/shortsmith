"""Tests for word_pauses: move Whisper word edges off measured pauses.

faster-whisper stretches a word across the pause before or after it, so the
transcript shows no gap where the speaker stopped. On a real 69-minute podcast
82% of measured pauses sat inside one word's span, and 23 of 32 planned cut
points landed on audible speech.
"""
from __future__ import annotations

import subprocess

import numpy as np
import pytest

from shortsmith import word_pauses
from shortsmith.config import Config
from shortsmith.cut_clips import _snap_boundary

FLOOR = -70.0
LOUD = -20.0


def _levels(seconds: float, loud: list[tuple[float, float]]) -> np.ndarray:
    """10 ms dB frames: silence everywhere except the `loud` (start, end) spans."""
    db = np.full(int(round(seconds / word_pauses.HOP)), FLOOR)
    for start, end in loud:
        db[int(round(start / word_pauses.HOP)):int(round(end / word_pauses.HOP))] = LOUD
    return db


def _w(text, start, end):
    return {"text": text, "start": start, "end": end}


def test_word_stretched_over_a_leading_pause_starts_at_the_sound():
    # "so" sounds 0.0-0.4, silence 0.4-1.0, "then" sounds 1.0-1.3,
    # but Whisper gave "then" the span 0.4-1.3.
    db = _levels(2.0, [(0.0, 0.4), (1.0, 1.3)])
    words = [_w("so", 0.0, 0.4), _w("then", 0.4, 1.3)]
    fixed = word_pauses.repair_words(words, db, FLOOR)
    assert fixed[0] == words[0]
    assert fixed[1]["start"] == pytest.approx(1.0)
    assert fixed[1]["end"] == pytest.approx(1.3)


def test_word_stretched_over_a_trailing_pause_ends_at_the_sound():
    db = _levels(2.0, [(0.0, 0.3), (1.0, 1.3)])
    words = [_w("so.", 0.0, 1.0), _w("then", 1.0, 1.3)]
    fixed = word_pauses.repair_words(words, db, FLOOR)
    assert fixed[0]["end"] == pytest.approx(0.3)
    assert fixed[1] == words[1]


def test_short_dip_inside_a_word_is_left_alone():
    # A 40 ms stop consonant is not a pause.
    db = _levels(1.0, [(0.0, 0.30), (0.34, 0.6)])
    words = [_w("stop", 0.0, 0.6)]
    assert word_pauses.repair_words(words, db, FLOOR) == words


def test_pause_with_sound_on_both_sides_stays_inside_the_word():
    # Trimming to one side would leave the other sound inside a "silent" gap.
    db = _levels(2.0, [(0.0, 0.3), (0.6, 1.2)])
    words = [_w("um people", 0.0, 1.2)]
    assert word_pauses.repair_words(words, db, FLOOR) == words


def test_silent_word_is_left_alone():
    db = _levels(1.0, [])
    words = [_w("hm", 0.2, 0.6)]
    assert word_pauses.repair_words(words, db, FLOOR) == words


def test_input_words_are_not_mutated():
    db = _levels(2.0, [(0.0, 0.4), (1.0, 1.3)])
    words = [_w("so", 0.0, 0.4), _w("then", 0.4, 1.3)]
    word_pauses.repair_words(words, db, FLOOR)
    assert words[1]["start"] == 0.4


def test_snapper_prefers_a_real_pause_over_a_closer_zero_gap_edge():
    cfg = Config()
    words = [
        _w("we", 0.0, 0.5), _w("went", 0.5, 1.0),      # zero-gap edge at 0.5
        _w("home", 1.0, 1.4), _w("and", 1.8, 2.2),     # 0.4 s pause at 1.6
    ]
    assert _snap_boundary(0.55, words, cfg, prefer_after=False) == pytest.approx(1.6)


def test_measure_levels_finds_the_silence_in_real_audio(tmp_path):
    wav = tmp_path / "tone-gap-tone.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "aevalsrc=if(between(t\\,1\\,2)\\,0\\,0.5*sin(2*PI*220*t)):d=3:s=16000", str(wav)],
        check=True,
    )
    measured = word_pauses.measure_levels(wav)
    assert measured is not None
    db, floor = measured
    assert db[int(1.5 / word_pauses.HOP)] < floor + word_pauses.QUIET_DB
    assert db[int(0.5 / word_pauses.HOP)] > floor + word_pauses.QUIET_DB


def test_sound_before_an_inner_pause_goes_to_the_previous_word():
    # Whisper ended "so" early: its sound runs to 0.5, then a pause, then "then".
    db = _levels(2.0, [(0.0, 0.5), (1.0, 1.3)])
    words = [_w("so", 0.0, 0.3), _w("then", 0.3, 1.3)]
    fixed = word_pauses.repair_words(words, db, FLOOR)
    assert (fixed[0]["start"], fixed[0]["end"]) == pytest.approx((0.0, 0.5))
    assert (fixed[1]["start"], fixed[1]["end"]) == pytest.approx((1.0, 1.3))


def test_sound_after_an_inner_pause_goes_to_the_next_word():
    db = _levels(2.0, [(0.0, 0.5), (1.0, 1.3)])
    words = [_w("so.", 0.0, 1.1), _w("then", 1.1, 1.3)]
    fixed = word_pauses.repair_words(words, db, FLOOR)
    assert (fixed[0]["start"], fixed[0]["end"]) == pytest.approx((0.0, 0.5))
    assert (fixed[1]["start"], fixed[1]["end"]) == pytest.approx((1.0, 1.3))
