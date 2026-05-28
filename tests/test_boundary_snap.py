"""Tests for cut_clips._snap_boundary — the boundary-condition land mine.

The contract:
  * never return a time inside a word (always a gap midpoint)
  * prefer sentence-end punctuation gaps
  * when prefer_after (end-of-clip), bias toward extending FORWARD to a clean
    sentence end rather than retreating into a mid-thought word gap
"""
from __future__ import annotations

from shortsmith.config import Config
from shortsmith.cut_clips import _snap_boundary


def _words(spec):
    """spec: list of (text, start, end)."""
    return [{"text": t, "start": s, "end": e} for t, s, e in spec]


def test_returns_input_when_too_few_words():
    cfg = Config()
    assert _snap_boundary(5.0, [], cfg, prefer_after=True) == 5.0
    assert _snap_boundary(5.0, _words([("hi", 0, 1)]), cfg, prefer_after=False) == 5.0


def test_snaps_to_sentence_end_gap():
    cfg = Config()
    # "...idea." [big gap] "Next..."  — sentence end at ~2.0
    words = _words([
        ("the", 0.0, 0.3),
        ("idea.", 0.3, 1.0),
        ("Next", 1.6, 2.0),   # 0.6s gap after sentence end
        ("thing", 2.0, 2.4),
    ])
    # Request a cut near 1.1 — should snap to the 1.0->1.6 sentence-end gap (~1.3)
    b = _snap_boundary(1.1, words, cfg, prefer_after=True)
    assert 1.0 <= b <= 1.6


def test_never_lands_inside_a_word():
    cfg = Config()
    words = _words([
        ("alpha", 0.0, 0.5),
        ("beta", 0.55, 1.0),
        ("gamma", 1.05, 1.6),
    ])
    for target in (0.25, 0.7, 1.3):
        b = _snap_boundary(target, words, cfg, prefer_after=True)
        # b must sit in one of the inter-word gaps, never within a word span
        for w in words:
            assert not (w["start"] < b < w["end"]), f"{b} landed inside {w}"


def test_prefer_after_extends_forward_to_sentence_end():
    cfg = Config()
    # Agent picked an end at 1.05 — mid-thought. There's a tiny word gap right
    # before (backward) and a real sentence end ~0.7s forward. prefer_after
    # should choose the forward sentence end.
    words = _words([
        ("we", 0.0, 0.3),
        ("think", 0.32, 0.7),
        ("that", 0.74, 1.0),       # mini gaps, mid-thought
        ("wealth", 1.03, 1.5),
        ("compounds.", 1.5, 2.0),  # sentence end at 2.0
        ("Then", 2.6, 2.9),        # 0.6s gap after the period
    ])
    b = _snap_boundary(1.05, words, cfg, prefer_after=True)
    # Forward sentence-end gap is ~2.3; should be chosen over backward mid gaps.
    assert b > 1.5, f"expected forward extension past the sentence, got {b}"


def test_start_snap_is_symmetric_tight():
    cfg = Config()
    words = _words([
        ("intro.", 0.0, 0.5),
        ("Here", 1.0, 1.3),    # 0.5s gap (sentence end) at ~0.75
        ("we", 1.32, 1.5),
        ("go", 1.52, 1.8),
    ])
    # Start near 0.8 should snap to the 0.5->1.0 sentence gap.
    b = _snap_boundary(0.8, words, cfg, prefer_after=False)
    assert 0.5 <= b <= 1.0
