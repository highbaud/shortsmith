"""Regression tests for malformed render inputs.

A hand-authored broll.json or layout preset is the input most likely to be
wrong, and finalize renders the whole library in one process: a bad file on one
short used to raise something no caller was catching and end the run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import finalize  # noqa: E402
import render_remotion as rr  # noqa: E402

from shortsmith import layouts  # noqa: E402


@pytest.fixture
def short(tmp_path: Path) -> Path:
    proj = tmp_path / "src" / "short-01-topic"
    (proj / "assets").mkdir(parents=True)
    (proj / "renders").mkdir()
    return proj


def write_broll(short: Path, payload: str) -> None:
    (short / "broll.json").write_text(payload, encoding="utf-8")


# --- manual b-roll -------------------------------------------------------- #

@pytest.mark.parametrize("payload", ["[{", '{"start": 1}'])
def test_an_unreadable_manual_broll_raises_a_catchable_error(short: Path, payload: str) -> None:
    """BrollSpecError, not SystemExit: finalize guards each short with
    `except Exception`, which SystemExit walks straight through."""
    write_broll(short, payload)
    with pytest.raises(rr.BrollSpecError):
        rr._load_broll(short, None)


@pytest.mark.parametrize("slide", [
    {"type": "logo", "start": "soon", "end": 3},   # non-numeric start
    {"type": "logo", "end": 3},                    # no start at all
    "logo",                                        # not a slide object
])
def test_a_bad_manual_slide_is_reported_and_dropped_not_raised(
    short: Path, slide: object, capsys: pytest.CaptureFixture[str]
) -> None:
    write_broll(short, json.dumps([slide]))
    merged = rr._merge_broll(short, None)          # used to raise in the sort
    assert rr._validate_broll(merged, [], 30.0) == []
    assert "bad start/end" in capsys.readouterr().out


def test_good_manual_slides_still_come_back_in_time_order(short: Path) -> None:
    write_broll(short, json.dumps([
        {"type": "logo", "start": 8, "end": 9},
        {"type": "logo", "start": 2, "end": 3},
    ]))
    assert [s["start"] for s in rr._merge_broll(short, None)] == [2, 8]


# --- layout presets ------------------------------------------------------- #

@pytest.mark.parametrize("payload", ["[1, 2, 3]", '"two-speaker-stack"', "42"])
def test_a_preset_that_is_not_an_object_is_a_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    monkeypatch.setattr(layouts, "LAYOUTS_DIR", tmp_path)
    (tmp_path / "broken.json").write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        layouts.load_preset("broken")


def test_a_broken_preset_stops_a_split_stack_render_with_a_usable_message(
    short: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to escape as a bare AttributeError, past the handler whose whole
    job is to say why captions would have landed on a face."""
    monkeypatch.setattr(layouts, "LAYOUTS_DIR", tmp_path / "layouts")
    (tmp_path / "layouts").mkdir()
    (tmp_path / "layouts" / "broken.json").write_text("[1, 2, 3]", encoding="utf-8")
    (short.parent / "_clips.json").write_text(json.dumps(
        [{"rank": 1, "layout": "split-stack", "layout_preset": "broken"}]),
        encoding="utf-8")
    with pytest.raises(rr.LayoutPresetError, match="cannot be loaded"):
        rr._split_stack_layout(short)


# --- consolidation -------------------------------------------------------- #

@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One source folder holding one short with a Remotion render and no SFX."""
    auto = tmp_path / "auto-shorts"
    proj = auto / "0805" / "short-01-topic"
    (proj / "renders").mkdir(parents=True)
    (proj / "renders" / "final_remotion.mp4").write_bytes(b"mp4")
    monkeypatch.setattr(finalize, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(finalize, "KIT_RENDERS", tmp_path / "renders")
    monkeypatch.setattr(finalize, "ALL_DIR", tmp_path / "renders" / "_all")
    monkeypatch.setattr(finalize, "_qa_streams", lambda p: (True, 1080, 1920))
    return tmp_path


def test_skip_sfx_consolidates_the_best_render_it_has(library: Path) -> None:
    """--skip-sfx promises "consolidate the best available render directly".
    Phase 2 only ever looked for final_sfx.mp4, so it copied nothing and still
    reported success."""
    assert finalize.phase2_consolidate(allow_unmixed=True) == 1
    assert (library / "renders" / "_all" / "0805__short-01-topic.mp4").exists()


def test_a_normal_run_still_consolidates_only_the_sfx_mix(library: Path) -> None:
    assert finalize.phase2_consolidate() == 0
    assert not (library / "renders" / "_all" / "0805__short-01-topic.mp4").exists()
