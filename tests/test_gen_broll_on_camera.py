"""Tests for the rule that b-roll never cuts to a photo of someone who is on
camera (the clip spec's `speakers`)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gen_broll  # noqa: E402


def person(name: str) -> dict:
    return {"type": "person", "person": name, "name": name, "start": 1.0, "end": 4.0}


def test_person_slide_for_an_on_camera_speaker_is_dropped() -> None:
    slides = [person("John Deaton"),
              {"type": "logo", "name": "Ripple", "start": 5.0, "end": 7.0},
              person("Gary Gensler")]
    kept = gen_broll.drop_on_camera_people(slides, ["Jake Claver", "John Deaton"])
    assert [s.get("name") for s in kept] == ["Ripple", "Gary Gensler"]


def test_a_surname_alone_matches_the_speaker() -> None:
    assert gen_broll.drop_on_camera_people([person("Brad Garlinghouse")], ["Garlinghouse"]) == []
    assert gen_broll.drop_on_camera_people([person("Brad Garlinghouse")], ["Brad"]) != []


def test_no_speakers_means_nothing_is_dropped() -> None:
    slides = [person("John Deaton")]
    assert gen_broll.drop_on_camera_people(slides, []) == slides


def test_on_camera_names_come_from_the_clip_spec(tmp_path: Path) -> None:
    src = tmp_path / "src"
    short = src / "short-02-topic"
    short.mkdir(parents=True)
    (src / "_clips.json").write_text(json.dumps([
        {"rank": 1, "speakers": ["Someone Else"]},
        {"rank": 2, "speakers": ["Jake Claver", " John Deaton ", ""]},
    ]), encoding="utf-8")
    assert gen_broll.on_camera_names(short) == ["Jake Claver", "John Deaton"]
    assert gen_broll.on_camera_names(tmp_path / "short-09-nowhere") == []
