"""Tests for apply_remotion's decision to render or skip, now driven by the
render stamp. Remotion, ffmpeg and the network are all stubbed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shortsmith.rendering import apply_remotion as ar
from shortsmith.rendering import (
    broll_assets,
    gen_broll,
    project_inputs,
    render_remotion,
    render_stamp,
)


@pytest.fixture
def short(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    src = tmp_path / "src"
    proj = src / "short-01-topic"
    (proj / "assets").mkdir(parents=True)
    (proj / "renders").mkdir()
    (proj / "assets" / "words.json").write_text("[]", encoding="utf-8")
    (src / "_clips.json").write_text(json.dumps([{"rank": 1}]), encoding="utf-8")
    base = proj / "renders" / "final.mp4"
    base.write_bytes(b"base")

    rendered: list[dict] = []

    def fake_render(project_dir: Path, **kwargs) -> Path:
        out = project_dir / "renders" / "final_remotion.mp4"
        out.write_bytes(b"mp4")
        rendered.append(kwargs)
        return out

    monkeypatch.setattr(project_inputs, 'hyperframes_renders', lambda p: [base])
    monkeypatch.setattr(render_remotion, "render", fake_render)
    monkeypatch.setattr(gen_broll, "generate", lambda *a, **k: None)
    people = tmp_path / "people"
    people.mkdir()
    monkeypatch.setattr(broll_assets, 'PEOPLE_DIR', people)
    monkeypatch.setattr(broll_assets, 'PEOPLE_MANIFEST', people / "people.json")
    monkeypatch.setattr(broll_assets, 'MANUAL_DIR', people / "manual")
    monkeypatch.setattr(render_stamp, "code_files", lambda root=None: [])
    return {"proj": proj, "base": base, "rendered": rendered, "stats": ar.RenderStats()}


def test_first_render_writes_a_stamp(short: dict) -> None:
    out = ar.apply_remotion(short["proj"], stats=short["stats"])
    assert out is not None and out.exists()
    assert render_stamp.read_stamp(short["proj"]) is not None
    assert short["stats"].rendered == 1


def test_a_current_render_is_skipped(short: dict) -> None:
    ar.apply_remotion(short["proj"], stats=short["stats"])
    ar.apply_remotion(short["proj"], stats=short["stats"])
    assert len(short["rendered"]) == 1
    assert short["stats"].current == 1


def test_changed_words_trigger_a_re_render(short: dict, capsys: pytest.CaptureFixture[str]) -> None:
    ar.apply_remotion(short["proj"], stats=short["stats"])
    (short["proj"] / "assets" / "words.json").write_text(
        json.dumps([{"text": "Trump", "start": 0.0, "end": 0.4}]), encoding="utf-8")
    ar.apply_remotion(short["proj"], stats=short["stats"])
    assert len(short["rendered"]) == 2
    assert "changed since the last render: words" in capsys.readouterr().out


def test_legacy_render_without_a_stamp_keeps_the_old_rule(short: dict) -> None:
    """A short rendered before stamps existed is left alone while it is newer
    than its base, so an unscoped finalize does not rebuild the library."""
    (short["proj"] / "renders" / "final_remotion.mp4").write_bytes(b"old")
    ar.apply_remotion(short["proj"], stats=short["stats"])
    assert short["rendered"] == []
    assert short["stats"].legacy_skipped == 1

    ar.apply_remotion(short["proj"], force=True, stats=short["stats"])
    assert len(short["rendered"]) == 1
    assert render_stamp.read_stamp(short["proj"]) is not None


def test_broll_failure_is_counted_and_the_render_still_happens(
    short: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*a, **k):
        raise RuntimeError("wikidata down")

    monkeypatch.setattr(gen_broll, "generate", boom)
    ar.apply_remotion(short["proj"], stats=short["stats"])
    assert short["stats"].broll_failures == 1
    assert len(short["rendered"]) == 1
    assert "!! b-roll generation FAILED" in capsys.readouterr().out


def test_caption_opt_out_is_part_of_the_stamp(short: dict) -> None:
    ar.apply_remotion(short["proj"], stats=short["stats"])
    assert render_stamp.read_stamp(short["proj"])["inputs"]["captions"] is True
    (short["proj"].parent / "_clips.json").write_text(
        json.dumps([{"rank": 1, "captions": False}]), encoding="utf-8")
    ar.apply_remotion(short["proj"], stats=short["stats"])
    assert len(short["rendered"]) == 2
    assert short["rendered"][-1]["captions"] is False


def test_interleaved_runs_keep_separate_counts(short: dict) -> None:
    first, second = ar.RenderStats(), ar.RenderStats()

    ar.apply_remotion(short["proj"], stats=first)
    ar.apply_remotion(short["proj"], stats=second)
    ar.apply_remotion(short["proj"], stats=first)

    assert first == ar.RenderStats(rendered=1, current=1)
    assert second == ar.RenderStats(current=1)


def test_a_call_without_stats_does_not_change_another_run(short: dict) -> None:
    run = ar.RenderStats()
    ar.apply_remotion(short["proj"], stats=run)

    ar.apply_remotion(short["proj"])

    assert run == ar.RenderStats(rendered=1)
