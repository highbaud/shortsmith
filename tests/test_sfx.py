"""Tests for sfx.plan_events — deterministic SFX placement."""
from __future__ import annotations

from pathlib import Path

from shortsmith.config import Config
from shortsmith.sfx import plan_events

# Pretend every slot has a file.
FULL_MAP = {s: Path(f"{s}.wav") for s in
            ("swipe-in", "swipe-out", "hook-impact", "cash-register", "ding", "whoosh")}


def _words(spec):
    return [{"text": t, "start": s, "end": e} for t, s, e in spec]


def test_hook_impact_at_start():
    clip = {"hook": {"text": "Big claim"}, "callouts": []}
    ev = plan_events(clip, [], FULL_MAP, Config(), 30.0)
    assert any(e.slot == "hook-impact" and e.t < 0.2 for e in ev)


def test_callout_swipe_in_only_by_default():
    # swipe-out is opt-in; default config emits only swipe-in on a callout.
    clip = {"callouts": [{"local_start": 10.0, "duration": 2.5, "text": "POINT"}]}
    ev = plan_events(clip, [], FULL_MAP, Config(), 30.0)
    ins = [e for e in ev if e.slot == "swipe-in"]
    outs = [e for e in ev if e.slot == "swipe-out"]
    assert len(ins) == 1 and abs(ins[0].t - 10.0) < 0.01
    assert outs == []


def test_callout_swipe_out_when_enabled():
    cfg = Config()
    cfg.sfx_swipe_out = True
    clip = {"callouts": [{"local_start": 10.0, "duration": 2.5, "text": "POINT"}]}
    ev = plan_events(clip, [], FULL_MAP, cfg, 30.0)
    outs = [e for e in ev if e.slot == "swipe-out"]
    assert len(outs) == 1 and abs(outs[0].t - 12.5) < 0.01


def test_bigstat_suppresses_its_swipe():
    # The bigstat callout should get a ding and NOT also a swipe-in at the same t.
    clip = {"callouts": [{"local_start": 5.0, "duration": 2.0, "text": "$293M", "style": "bigstat"}]}
    ev = plan_events(clip, [], FULL_MAP, Config(), 30.0)
    at5 = [e for e in ev if abs(e.t - 5.0) < 0.01]
    slots = {e.slot for e in at5}
    assert "ding" in slots and "swipe-in" not in slots


def test_bigstat_dollar_gets_ding():
    clip = {"callouts": [
        {"local_start": 5.0, "duration": 2.0, "text": "$293M", "style": "bigstat"},
        {"local_start": 9.0, "duration": 2.0, "text": "JUST WORDS", "style": "punch"},
    ]}
    ev = plan_events(clip, [], FULL_MAP, Config(), 30.0)
    dings = [e for e in ev if e.slot == "ding"]
    assert len(dings) == 1 and abs(dings[0].t - 5.0) < 0.01  # only the bigstat $


def test_sparing_cash_register_first_money_only():
    cfg = Config()
    cfg.sfx_semantic_mode = "sparing"
    words = _words([
        ("I", 0.0, 0.2), ("made", 0.3, 0.6), ("a", 0.7, 0.8),
        ("million", 1.0, 1.5), ("dollars", 1.6, 2.1),
        ("then", 5.0, 5.3), ("another", 5.4, 5.9), ("million", 6.0, 6.5),
    ])
    ev = plan_events({"callouts": []}, words, FULL_MAP, cfg, 30.0)
    cash = [e for e in ev if e.slot == "cash-register"]
    assert len(cash) == 1
    assert abs(cash[0].t - 1.0) < 0.01  # first money word ("million")


def test_every_mode_multiple_cash():
    cfg = Config()
    cfg.sfx_semantic_mode = "every"
    words = _words([("million", 1.0, 1.5), ("cash", 6.0, 6.4), ("rich", 12.0, 12.4)])
    ev = plan_events({"callouts": []}, words, FULL_MAP, cfg, 30.0)
    assert len([e for e in ev if e.slot == "cash-register"]) == 3


def test_off_mode_structural_only():
    cfg = Config()
    cfg.sfx_semantic_mode = "off"
    clip = {"callouts": [{"local_start": 5.0, "duration": 2.0, "text": "$1M", "style": "bigstat"}]}
    words = _words([("million", 1.0, 1.5)])
    ev = plan_events(clip, words, FULL_MAP, cfg, 30.0)
    assert all(e.slot in ("swipe-in", "swipe-out", "hook-impact") for e in ev)
    assert not any(e.slot in ("cash-register", "ding") for e in ev)


def test_missing_slots_are_skipped():
    partial = {"swipe-in": Path("swipe-in.wav")}  # only one slot present
    clip = {"hook": {"text": "x"}, "callouts": [{"local_start": 5.0, "duration": 2.0, "text": "$1M", "style": "bigstat"}]}
    words = _words([("million", 1.0, 1.5)])
    ev = plan_events(clip, words, partial, Config(), 30.0)
    assert {e.slot for e in ev} <= {"swipe-in"}
