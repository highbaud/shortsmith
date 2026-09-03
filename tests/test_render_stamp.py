"""Tests for the render stamp that decides when a short must re-render."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gen_broll  # noqa: E402
import render_stamp  # noqa: E402


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A short folder with words, a clip spec, a base render, and isolated
    people / code state so the digest depends only on what the test changes."""
    src = tmp_path / "source"
    short = src / "short-01-topic"
    (short / "assets").mkdir(parents=True)
    (short / "renders").mkdir()
    words = [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.4}
             for i, t in enumerate("Trump said David Schwartz was right".split())]
    (short / "assets" / "words.json").write_text(json.dumps(words), encoding="utf-8")
    (src / "_clips.json").write_text(json.dumps([{"rank": 1, "hook": {"text": "x"}}]),
                                     encoding="utf-8")
    base = short / "renders" / "final.mp4"
    base.write_bytes(b"base-render")

    people = tmp_path / "people"
    people.mkdir()
    monkeypatch.setattr(gen_broll, "PEOPLE_DIR", people)
    monkeypatch.setattr(gen_broll, "PEOPLE_MANIFEST", people / "people.json")
    monkeypatch.setattr(gen_broll, "MANUAL_DIR", people / "manual")

    code = tmp_path / "code"
    code.mkdir()
    (code / "render.py").write_text("v1", encoding="utf-8")
    monkeypatch.setattr(render_stamp, "code_files", lambda root=None: sorted(code.glob("*")))
    return {"short": short, "base": base, "people": people, "code": code}


def stamp(p: dict, **over) -> dict:
    kwargs = {"base": p["base"], "style": "xrp-revolution", "platform": "generic",
              "captions": True}
    kwargs.update(over)
    return render_stamp.compute_stamp(p["short"], **kwargs)


def test_same_inputs_give_the_same_digest(project: dict) -> None:
    assert stamp(project)["digest"] == stamp(project)["digest"]


def test_round_trip_makes_the_render_current(project: dict) -> None:
    out = project["short"] / "renders" / "final_remotion.mp4"
    s = stamp(project)
    assert not render_stamp.is_current(project["short"], s, out)
    out.write_bytes(b"mp4")
    render_stamp.write_stamp(project["short"], s)
    assert render_stamp.is_current(project["short"], s, out)
    assert render_stamp.changed_inputs(render_stamp.read_stamp(project["short"]), s) == []


def test_changed_words_change_only_the_words_input(project: dict) -> None:
    before = stamp(project)
    (project["short"] / "assets" / "words.json").write_text("[]", encoding="utf-8")
    after = stamp(project)
    assert render_stamp.changed_inputs(before, after) == ["words", "people"]


def test_manual_photo_for_a_mentioned_person_changes_the_stamp(project: dict) -> None:
    before = stamp(project)
    manual = project["people"] / "manual"
    manual.mkdir()
    (manual / "davidschwartz.jpg").write_bytes(b"photo")
    assert render_stamp.changed_inputs(before, stamp(project)) == ["people"]


def test_manual_photo_for_an_unmentioned_person_does_not(project: dict) -> None:
    before = stamp(project)
    manual = project["people"] / "manual"
    manual.mkdir()
    (manual / "jedmccaleb.png").write_bytes(b"photo")
    assert render_stamp.changed_inputs(before, stamp(project)) == []


def test_code_change_changes_the_stamp(project: dict) -> None:
    before = stamp(project)
    (project["code"] / "render.py").write_text("v2", encoding="utf-8")
    assert render_stamp.changed_inputs(before, stamp(project)) == ["code"]


def test_new_base_render_changes_the_stamp(project: dict) -> None:
    before = stamp(project)
    project["base"].write_bytes(b"base-render-v2-longer")
    assert render_stamp.changed_inputs(before, stamp(project)) == ["base"]


def test_switches_are_part_of_the_stamp(project: dict) -> None:
    before = stamp(project)
    assert render_stamp.changed_inputs(before, stamp(project, platform="tiktok")) == ["platform"]
    assert render_stamp.changed_inputs(before, stamp(project, captions=False)) == ["captions"]


def test_missing_or_broken_stamp_reads_as_none(project: dict) -> None:
    assert render_stamp.read_stamp(project["short"]) is None
    render_stamp.stamp_path(project["short"]).write_text("{ nope", encoding="utf-8")
    assert render_stamp.read_stamp(project["short"]) is None
    assert render_stamp.changed_inputs(None, stamp(project)) == ["no stamp"]
