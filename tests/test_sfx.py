"""Tests for sfx.plan_events — deterministic SFX placement."""
from __future__ import annotations

from pathlib import Path

import pytest

from shortsmith.config import Config
from shortsmith.sfx import load_sfx_map, plan_events

# Pretend every slot has a file.
FULL_MAP = {s: Path(f"{s}.wav") for s in
            ("swipe-in", "swipe-out", "hook-impact", "cash-register",
             "wrong-answer", "ding", "whoosh")}


def _words(spec):
    return [{"text": t, "start": s, "end": e} for t, s, e in spec]


@pytest.mark.parametrize("manifest_text", ["{", "[]", '{"swipe-in": "hit.wav"}'])
def test_invalid_pack_uses_fallback_and_reports_the_problem(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, manifest_text: str,
) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "pack.json").write_text(manifest_text, encoding="utf-8")
    fallback = tmp_path / "swipe-in.wav"
    fallback.write_bytes(b"audio")

    assert load_sfx_map(tmp_path) == {"swipe-in": [fallback]}
    assert "pack.json" in caplog.text


def test_pack_ignores_invalid_entries_and_directories(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "pack.json").write_text(
        '{"swipe-in": ["hit.wav", "folder"], "ding": [42]}', encoding="utf-8",
    )
    hit = pack / "hit.wav"
    hit.write_bytes(b"audio")
    (pack / "folder").mkdir()

    assert load_sfx_map(tmp_path) == {"swipe-in": [hit]}
    assert "ding" in caplog.text


def test_invalid_callout_timing_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    events = plan_events({"callouts": [{"local_start": "invalid"}]}, [], FULL_MAP, Config(), 30.0)
    assert events == []
    assert "callout" in caplog.text


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
    words = _words([("million", 1.0, 1.5), ("crashed", 4.0, 4.5)])
    ev = plan_events(clip, words, FULL_MAP, cfg, 30.0)
    assert all(e.slot in ("swipe-in", "swipe-out", "hook-impact") for e in ev)
    assert not any(e.slot in ("cash-register", "ding", "wrong-answer") for e in ev)


def test_sparing_wrong_answer_first_negative_only():
    cfg = Config()
    cfg.sfx_semantic_mode = "sparing"
    words = _words([
        ("the", 0.0, 0.1), ("market", 0.2, 0.5),
        ("crashed", 1.0, 1.4),                          # first negative — fires
        ("everyone", 5.0, 5.4), ("got", 5.5, 5.7),
        ("rugged", 6.0, 6.4),                           # second — suppressed in sparing
    ])
    ev = plan_events({"callouts": []}, words, FULL_MAP, cfg, 30.0)
    wa = [e for e in ev if e.slot == "wrong-answer"]
    assert len(wa) == 1
    assert abs(wa[0].t - 1.0) < 0.01


def test_every_mode_multiple_wrong_answers():
    cfg = Config()
    cfg.sfx_semantic_mode = "every"
    words = _words([("crashed", 1.0, 1.4), ("scammed", 6.0, 6.4), ("rekt", 12.0, 12.4)])
    ev = plan_events({"callouts": []}, words, FULL_MAP, cfg, 30.0)
    assert len([e for e in ev if e.slot == "wrong-answer"]) == 3


def test_wrong_answer_punctuation_stripped():
    # "crashed," with a trailing comma should still match the keyword list.
    cfg = Config()
    words = _words([("crashed,", 1.0, 1.4)])
    ev = plan_events({"callouts": []}, words, FULL_MAP, cfg, 30.0)
    assert any(e.slot == "wrong-answer" for e in ev)


def test_wrong_answer_skipped_when_slot_missing():
    # If pack.json has no wrong-answer entry, the trigger must silently skip.
    partial = {s: Path(f"{s}.wav") for s in
               ("swipe-in", "hook-impact", "cash-register", "ding", "whoosh")}
    words = _words([("crashed", 1.0, 1.4)])
    ev = plan_events({"callouts": []}, words, partial, Config(), 30.0)
    assert not any(e.slot == "wrong-answer" for e in ev)


def test_missing_slots_are_skipped():
    partial = {"swipe-in": Path("swipe-in.wav")}  # only one slot present
    clip = {"hook": {"text": "x"}, "callouts": [{"local_start": 5.0, "duration": 2.0, "text": "$1M", "style": "bigstat"}]}
    words = _words([("million", 1.0, 1.5)])
    ev = plan_events(clip, words, partial, Config(), 30.0)
    assert {e.slot for e in ev} <= {"swipe-in"}


@pytest.mark.parametrize("mode", ["sparing", "every", "off"])
def test_sound_and_visual_word_cues_agree(mode):
    from shortsmith.vfx import plan_vfx_events

    cfg = Config(sfx_semantic_mode=mode, vfx_enabled=True)
    cfg.vfx_triggers = {"cash-register": ["glare"], "wrong-answer": ["zoom-punch"]}
    words = _words([
        ("$1.30,", -1, -0.5), ("crashed,", -0.5, -0.1),
        ("30k", 1, 1.5), ("million", 2, 2.5),
        ("crashed,", 3, 3.5), ("scammed", 4, 4.5), ("$20", 11, 12),
    ])
    sounds = plan_events({}, words, FULL_MAP, cfg, 10)
    visuals = plan_vfx_events({}, words, cfg, 10)
    effects = {"cash-register": "glare", "wrong-answer": "zoom-punch"}
    assert sorted((e.t, effects[e.slot]) for e in sounds) == [
        (e.t, e.effect) for e in visuals
    ]
    expected = {"sparing": [1, 3], "every": [1, 2, 3, 4], "off": []}
    assert [e.t for e in sounds] == expected[mode]


def test_bad_hook_and_callout_leave_valid_sound_cues(caplog):
    clip = {"hook": "manual hook", "callouts": [None, {
        "local_start": 2, "text": "point", "style": "punch",
    }]}
    events = plan_events(clip, [], FULL_MAP, Config(), 10)
    assert [(e.t, e.slot) for e in events] == [(2, "swipe-in")]
    assert "expected an object" in caplog.text


@pytest.mark.parametrize("time", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_authored_cues_do_not_enter_sound_or_visual_plans(time, caplog):
    from shortsmith.vfx import plan_vfx_events

    clip = {"callouts": [{"local_start": time, "text": "$1M", "style": "bigstat"}]}
    words = [{"text": "million", "start": time, "end": time}]
    cfg = Config(vfx_enabled=True)
    assert plan_events(clip, words, FULL_MAP, cfg, 10) == []
    assert plan_vfx_events(clip, words, cfg, 10) == []
    assert "timing must be finite" in caplog.text
