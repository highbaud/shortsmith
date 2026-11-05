"""Saved overlay data behaves the same during scaffold and later rendering."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shortsmith import scaffold
from shortsmith.rendering import project_inputs, render_remotion


@pytest.mark.parametrize("hook", [
    "bad hook",
    {"text": "Hook", "duration": None},
    {"text": "Hook", "duration": "not a number"},
    {"text": "Hook", "duration": "NaN"},
])
def test_saved_malformed_overlays_follow_scaffold_timing(tmp_path: Path, hook: object) -> None:
    short = tmp_path / "short-01-topic"
    short.mkdir()
    clip = {"rank": 1, "hook": hook, "callouts": [
        None,
        {"text": "Bad time", "local_start": "bad"},
        {"text": "Not finite", "local_start": "Infinity"},
        {"text": "Visible", "local_start": 2.0, "duration": 3.0},
    ]}
    (tmp_path / "_clips.json").write_text(json.dumps([clip]), encoding="utf-8")

    built_hook = scaffold._build_hook(clip, 10.0)
    built_callouts = scaffold._build_callouts(clip, 1, 10.0)
    windows = project_inputs.overlay_windows(short, 10.0)

    assert [(item["local_start"], item["duration"]) for item in built_callouts] == [(2.0, 3.0)]
    if built_hook is None:
        assert windows == [{"start": 2.0, "end": 5.0}]
    else:
        assert built_hook["duration"] == 2.6
        assert windows == [{"start": 0.0, "end": 2.6}, {"start": 2.0, "end": 5.0}]


def test_unknown_base_mode_cannot_silently_select_a_clip(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "clip-edit.mp4").write_bytes(b"candidate media")

    with pytest.raises(ValueError, match="Unknown base mode 'hyperframe'"):
        project_inputs.pick_base(tmp_path, "hyperframe")


def test_bad_optional_motion_config_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTSMITH_PUNCH_EVERY", "bad value")

    with pytest.raises(ValueError):
        render_remotion._ambient_punches([], 20.0)


def test_a_string_accent_is_one_word_not_letters() -> None:
    assert scaffold._accent_words("liquidity") == ["liquidity"]
    assert scaffold._accent_words([" BIT ", ""]) == ["BIT"]
