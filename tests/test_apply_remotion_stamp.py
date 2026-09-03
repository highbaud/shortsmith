"""Tests for apply_remotion's decision to render or skip, now driven by the
render stamp. Remotion, ffmpeg and the network are all stubbed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import apply_remotion as ar  # noqa: E402
import gen_broll  # noqa: E402
import render_remotion  # noqa: E402
import render_stamp  # noqa: E402


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

    monkeypatch.setattr(render_remotion, "_hyperframes_renders", lambda p: [base])
    monkeypatch.setattr(render_remotion, "render", fake_render)
    monkeypatch.setattr(gen_broll, "generate", lambda *a, **k: None)
    people = tmp_path / "people"
    people.mkdir()
    monkeypatch.setattr(gen_broll, "PEOPLE_DIR", people)
    monkeypatch.setattr(gen_broll, "PEOPLE_MANIFEST", people / "people.json")
    monkeypatch.setattr(gen_broll, "MANUAL_DIR", people / "manual")
    monkeypatch.setattr(render_stamp, "code_files", lambda root=None: [])
    ar.reset_stats()
    return {"proj": proj, "base": base, "rendered": rendered}


def test_first_render_writes_a_stamp(short: dict) -> None:
    out = ar.apply_remotion(short["proj"])
    assert out is not None and out.exists()
    assert render_stamp.read_stamp(short["proj"]) is not None
    assert ar.RUN_STATS["rendered"] == 1


def test_a_current_render_is_skipped(short: dict) -> None:
    ar.apply_remotion(short["proj"])
    ar.apply_remotion(short["proj"])
    assert len(short["rendered"]) == 1
    assert ar.RUN_STATS["current"] == 1


def test_changed_words_trigger_a_re_render(short: dict, capsys: pytest.CaptureFixture[str]) -> None:
    ar.apply_remotion(short["proj"])
    (short["proj"] / "assets" / "words.json").write_text(
        json.dumps([{"text": "Trump", "start": 0.0, "end": 0.4}]), encoding="utf-8")
    ar.apply_remotion(short["proj"])
    assert len(short["rendered"]) == 2
    assert "changed since the last render: words" in capsys.readouterr().out


def test_legacy_render_without_a_stamp_keeps_the_old_rule(short: dict) -> None:
    """A short rendered before stamps existed is left alone while it is newer
    than its base, so an unscoped finalize does not rebuild the library."""
    (short["proj"] / "renders" / "final_remotion.mp4").write_bytes(b"old")
    ar.apply_remotion(short["proj"])
    assert short["rendered"] == []
    assert ar.RUN_STATS["legacy_skipped"] == 1

    ar.apply_remotion(short["proj"], force=True)
    assert len(short["rendered"]) == 1
    assert render_stamp.read_stamp(short["proj"]) is not None


def test_broll_failure_is_counted_and_the_render_still_happens(
    short: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*a, **k):
        raise RuntimeError("wikidata down")

    monkeypatch.setattr(gen_broll, "generate", boom)
    ar.apply_remotion(short["proj"])
    assert ar.RUN_STATS["broll_failures"] == 1
    assert len(short["rendered"]) == 1
    assert "!! b-roll generation FAILED" in capsys.readouterr().out


def test_caption_opt_out_is_part_of_the_stamp(short: dict) -> None:
    ar.apply_remotion(short["proj"])
    assert render_stamp.read_stamp(short["proj"])["inputs"]["captions"] is True
    (short["proj"].parent / "_clips.json").write_text(
        json.dumps([{"rank": 1, "captions": False}]), encoding="utf-8")
    ar.apply_remotion(short["proj"])
    assert len(short["rendered"]) == 2
    assert short["rendered"][-1]["captions"] is False
