"""Saved pipeline dictionaries remain valid planner and review inputs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shortsmith.clean_clips import plan_cleaning
from shortsmith.config import Config
from shortsmith.records import CleaningPlan, ClipManifest, ClipSpec, TranscriptWord, current_media
from shortsmith.story_review import build_review_input


def test_saved_manifest_resumes_planning_without_dropping_extra_data() -> None:
    saved = """[{
      "rank": 1, "raw_path": "raw/short-01.mp4", "slug": "clear-point",
      "requested_segments": [[0.0, 3.5]], "snapped_segments": [[0.0, 3.5]],
      "segment_durations": [3.5], "seam_xfade_seconds": 0.0, "duration": 3.5,
      "editor_note": {"approved_by": "editor", "keep": true}
    }]"""
    manifests: list[ClipManifest] = json.loads(saved)
    words: list[TranscriptWord] = [
        {"text": "One", "start": 0.0, "end": 0.5},
        {"word": "clear", "start": 1.0, "end": 1.5},
        {"text": "spoken", "start": 2.0, "end": 2.5},
        {"text": "point.", "start": 3.0, "end": 3.5},
    ]
    manifest = manifests[0]
    plan: CleaningPlan = plan_cleaning(
        words, manifest["snapped_segments"], Config(story_check=True),
        segment_durations=manifest["segment_durations"],
        xfade_seconds=manifest["seam_xfade_seconds"],
    )
    clip: ClipSpec = {
        "rank": manifest["rank"], "slug": manifest["slug"],
        "start": 0.0, "end": 3.5, "segments": plan["retained_segments"],
    }

    payload = json.loads(build_review_input([clip], plan["local_words"]))
    resumed = json.loads(json.dumps(manifests))

    assert plan["cuts"] == []
    assert plan["retained_segments"] == [[0.0, 3.5]]
    assert payload["clips"][0]["segments"][0]["text"] == "One clear spoken point."
    assert resumed[0]["raw_path"] == "raw/short-01.mp4"
    assert resumed[0]["editor_note"] == {"approved_by": "editor", "keep": True}
    assert "cleaned_path" not in resumed[0]
    assert manifests == json.loads(saved)


@pytest.mark.parametrize(("paths", "expected"), [
    ({}, "raw.mp4"),
    ({"cleaned_path": "cleaned.mp4"}, "cleaned.mp4"),
    ({"cleaned_path": "cleaned.mp4", "enhanced_path": "enhanced.mp4"}, "enhanced.mp4"),
    ({"cleaned_path": "cleaned.mp4", "enhanced_path": ""}, "cleaned.mp4"),
    ({"cleaned_path": "", "enhanced_path": ""}, "raw.mp4"),
    ({"enhanced_path": "enhanced.mp4", "vertical_path": "vertical.mp4"}, "enhanced.mp4"),
])
def test_current_media_preserves_stage_precedence(paths: dict, expected: str) -> None:
    manifest: ClipManifest = {"rank": 1, "raw_path": "raw.mp4", **paths}
    before = dict(manifest)

    assert current_media(manifest) == Path(expected)
    assert manifest == before


def test_current_media_keeps_missing_required_path_error() -> None:
    with pytest.raises(KeyError, match="raw_path"):
        current_media({"rank": 1})
