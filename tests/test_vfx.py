"""Tests for vfx.plan_vfx_events — deterministic visual-transition placement.

These mirror the SFX trigger tests but assert on the VFX effect types
(glare / zoom-punch / flash) and color tints that the Remotion overlay
layer reads. Pure logic — no I/O, no Remotion required.
"""
from __future__ import annotations

from shortsmith.config import Config
from shortsmith.vfx import VFXEvent, plan_vfx_events


def _words(spec):
    return [{"text": t, "start": s, "end": e} for t, s, e in spec]


def test_disabled_returns_empty():
    cfg = Config()
    cfg.vfx_enabled = False
    clip = {"hook": {"text": "Big claim"}, "callouts": []}
    assert plan_vfx_events(clip, [], cfg, 30.0) == []


def test_hook_fires_glare_zoom_flash():
    clip = {"hook": {"text": "Big claim"}, "callouts": []}
    ev = plan_vfx_events(clip, [], Config(), 30.0)
    effects_at_hook = {e.effect for e in ev if e.t < 0.2}
    assert effects_at_hook == {"glare", "zoom-punch", "flash"}
    # White tint on hook
    assert all(e.color == "#ffffff" for e in ev if e.t < 0.2)


def test_bigstat_fires_glare_only_by_default():
    clip = {"callouts": [
        {"local_start": 5.0, "duration": 2.0, "text": "$293M", "style": "bigstat"},
    ]}
    ev = plan_vfx_events(clip, [], Config(), 30.0)
    at5 = [e for e in ev if abs(e.t - 5.0) < 0.01]
    assert {e.effect for e in at5} == {"glare"}
    # Gold tint on ding
    assert at5[0].color == "#f5c842"


def test_non_bigstat_callout_emits_nothing():
    # Regular swipe-style callouts don't fire VFX in the sparing default.
    clip = {"callouts": [
        {"local_start": 5.0, "duration": 2.0, "text": "POINT", "style": "punch"},
    ]}
    ev = plan_vfx_events(clip, [], Config(), 30.0)
    assert not any(abs(e.t - 5.0) < 0.01 for e in ev)


def test_money_word_fires_glare_and_flash():
    cfg = Config()
    words = _words([("I", 0.0, 0.2), ("made", 0.3, 0.6),
                    ("a", 0.7, 0.8), ("million", 1.0, 1.5)])
    ev = plan_vfx_events({"callouts": []}, words, cfg, 30.0)
    cash = [e for e in ev if abs(e.t - 1.0) < 0.01]
    assert {e.effect for e in cash} == {"glare", "flash"}
    assert all(e.color == "#f5c842" for e in cash)


def test_sparing_money_first_only():
    cfg = Config()
    cfg.sfx_semantic_mode = "sparing"
    words = _words([("million", 1.0, 1.5), ("cash", 6.0, 6.4)])
    ev = plan_vfx_events({"callouts": []}, words, cfg, 30.0)
    assert len([e for e in ev if abs(e.t - 1.0) < 0.01 and e.effect == "glare"]) == 1
    assert not any(abs(e.t - 6.0) < 0.01 for e in ev)


def test_every_mode_money_multiple():
    cfg = Config()
    cfg.sfx_semantic_mode = "every"
    words = _words([("million", 1.0, 1.5), ("cash", 6.0, 6.4)])
    ev = plan_vfx_events({"callouts": []}, words, cfg, 30.0)
    glares = [e for e in ev if e.effect == "glare" and e.color == "#f5c842"]
    assert len(glares) == 2


def test_wrong_answer_fires_flash_and_zoom():
    cfg = Config()
    words = _words([("the", 0.0, 0.1), ("market", 0.2, 0.5), ("crashed", 1.0, 1.4)])
    ev = plan_vfx_events({"callouts": []}, words, cfg, 30.0)
    at1 = [e for e in ev if abs(e.t - 1.0) < 0.01]
    assert {e.effect for e in at1} == {"flash", "zoom-punch"}
    assert all(e.color == "#ff3653" for e in at1)


def test_off_mode_only_hook_fires():
    cfg = Config()
    cfg.sfx_semantic_mode = "off"
    clip = {"hook": {"text": "claim"}, "callouts": [
        {"local_start": 5.0, "duration": 2.0, "text": "$1M", "style": "bigstat"}]}
    words = _words([("million", 1.0, 1.5), ("crashed", 2.0, 2.5)])
    ev = plan_vfx_events(clip, words, cfg, 30.0)
    # Hook always fires (it's structural, not semantic). Everything else off.
    assert all(e.t < 0.2 for e in ev)


def test_intensity_propagates():
    cfg = Config()
    cfg.vfx_intensity = 0.5
    ev = plan_vfx_events({"hook": {"text": "x"}, "callouts": []}, [], cfg, 30.0)
    assert all(abs(e.intensity - 0.5) < 1e-9 for e in ev)


def test_durations_match_effect_defaults():
    ev = plan_vfx_events({"hook": {"text": "x"}, "callouts": []}, [], Config(), 30.0)
    by_effect = {e.effect: e.duration_ms for e in ev}
    assert by_effect["glare"] == 280
    assert by_effect["zoom-punch"] == 220
    assert by_effect["flash"] == 90


def test_to_props_shape():
    e = VFXEvent(t=1.5, effect="glare", color="#f5c842", intensity=0.8, duration_ms=280)
    props = e.to_props()
    assert props == {
        "t": 1.5, "effect": "glare", "color": "#f5c842",
        "intensity": 0.8, "durationMs": 280,
    }
