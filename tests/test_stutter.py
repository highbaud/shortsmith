"""Tests for clean_clips._stutter_cuts — collapse stammers, keep emphasis."""
from __future__ import annotations

from shortsmith.clean_clips import _stutter_cuts
from shortsmith.config import Config


def _words(spec):
    return [{"text": t, "start": s, "end": e} for t, s, e in spec]


def test_collapses_immediate_repeat_keeps_last():
    cfg = Config()
    # "the the wealth" — stammer on "the", gap 0.05s
    words = _words([
        ("the", 1.00, 1.20),
        ("the", 1.25, 1.45),
        ("wealth", 1.50, 2.00),
    ])
    cuts = _stutter_cuts(words, cfg)
    assert len(cuts) == 1
    cs, ce = cuts[0]
    # Cut should remove the first "the" up to the start of the second "the".
    assert abs(cs - 1.00) < 0.05
    assert abs(ce - 1.25) < 0.01


def test_three_repeat_run_collapses_to_one():
    cfg = Config()
    words = _words([
        ("I", 0.00, 0.10),
        ("I", 0.15, 0.25),
        ("I", 0.30, 0.40),
        ("think", 0.45, 0.80),
    ])
    cuts = _stutter_cuts(words, cfg)
    assert len(cuts) == 1
    cs, ce = cuts[0]
    # Keeps only the last "I" (start 0.30): cut covers 0.00 -> 0.30
    assert abs(cs - 0.00) < 0.05
    assert abs(ce - 0.30) < 0.01


def test_spaced_emphasis_is_preserved():
    cfg = Config()  # stutter_max_gap = 0.35
    # "no ... no ... no" with 0.5s gaps = deliberate emphasis, NOT a stammer
    words = _words([
        ("no", 0.0, 0.3),
        ("no", 0.8, 1.1),
        ("no", 1.6, 1.9),
    ])
    cuts = _stutter_cuts(words, cfg)
    assert cuts == []


def test_distinct_words_untouched():
    cfg = Config()
    words = _words([
        ("build", 0.0, 0.4),
        ("real", 0.45, 0.8),
        ("wealth", 0.85, 1.3),
    ])
    assert _stutter_cuts(words, cfg) == []


def test_disabled_returns_empty():
    cfg = Config()
    cfg.stutter_repair = False
    words = _words([("the", 1.0, 1.2), ("the", 1.25, 1.45), ("x", 1.5, 1.8)])
    assert _stutter_cuts(words, cfg) == []
