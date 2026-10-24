"""Story approval must match the speech that survives physical editing."""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from shortsmith import captions, clean_clips, cut_clips, find_clips, story_review
from shortsmith.config import Config


def words(text):
    return [{"text": word, "start": round(i * .6, 3), "end": round(i * .6 + .4, 3)}
            for i, word in enumerate(text.split())]


def media_stub(monkeypatch, duration="8.5"):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "ffprobe":
            return subprocess.CompletedProcess(argv, 0, stdout=duration + "\n")
        Path(argv[-1]).touch()
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def approve(monkeypatch):
    requests = []

    def create(**kwargs):
        payload = json.loads(kwargs["messages"][0]["content"])
        requests.append(payload)
        reviews = [{"clip_id": clip["clip_id"], "decision": "pass",
                    "reason": "Complete question and answer.", "issues": []}
                   for clip in payload["clips"]]
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="text", text=json.dumps(reviews)),
        ])

    monkeypatch.setattr("anthropic.Anthropic", lambda **_: SimpleNamespace(
        messages=SimpleNamespace(create=create),
    ))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return requests


def question():
    source = words("Prelude. How do you know this works? Um, we we tested it. Next.")
    clip = {"rank": 1, "slug": "proof", "start": .5, "end": 7.1,
            "segments": [[.5, 7.1]]}
    return source, clip


def test_recording_edges_keep_the_first_and_last_words():
    source = words("This plan makes a steady path for our team today.")
    clip = {"start": 0, "end": source[-1]["end"],
            "segments": [[0, source[-1]["end"]]]}

    planned = cut_clips.plan_segments(clip, source, Config())

    assert planned == [[0, source[-1]["end"]]]
    assert [word["text"] for word in captions.slice_words_for_segments(source, planned)] == [
        word["text"] for word in source
    ]


def test_cuts_inside_edge_words_extend_to_safe_recording_edges():
    source = words("The plan works.")
    clip = {"start": .1, "end": 1.4, "segments": [[.1, 1.4]]}

    assert cut_clips.plan_segments(clip, source, Config()) == [[0, 1.6]]


def test_a_single_word_keeps_its_recording_edges():
    source = [{"text": "Yes.", "start": 0, "end": 1}]
    clip = {"start": .1, "end": .9, "segments": [[.1, .9]]}

    assert cut_clips.plan_segments(clip, source, Config()) == [[0, 1]]


def continuous_words():
    return [
        {"text": "We", "start": 0, "end": .5},
        {"text": "cannot", "start": .5, "end": 1.2},
        {"text": "allow", "start": 1.2, "end": 1.7},
        {"text": "this.", "start": 1.7, "end": 2.2},
        {"text": "Ever.", "start": 2.2, "end": 3},
    ]


def test_partial_boundary_speech_cannot_be_omitted_evidence():
    source = continuous_words()
    clip = {"segments": [[.8, 2.2]]}
    payload = json.loads(story_review.build_review_input([clip], source))
    segment = payload["clips"][0]["segments"][0]
    assert segment["partial_boundary_words"] == [{"text": "cannot", "start": .5, "end": 1.2}]
    report = story_review.validate_reviews([{
        "clip_id": 0, "decision": "reject", "reason": "The negation was omitted.",
        "issues": [{"type": "changed_meaning", "explanation": "Negation changes meaning.",
                    "segment_id": 0, "selected_quote": "allow this.",
                    "context_quote": "cannot", "context_sentence_id": 0}],
    }], [clip], source)[0]
    assert report["decision"] == "needs_review"


def test_shared_word_boundaries_keep_the_full_negation_through_review_and_clean(
    monkeypatch, tmp_path,
):
    source = continuous_words()
    clip = {"rank": 1, "slug": "negation", "start": .8, "end": 2.2,
            "segments": [[.8, 2.2]]}
    requests = approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    spoken = " ".join(part["text"] for part in requests[0]["clips"][0]["segments"])
    assert spoken == "cannot allow this."
    assert checked["story_review"]["reviewed_segments"] == [[.5, 2.2]]
    media_stub(monkeypatch)
    manifest = cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)
    cleaned = clean_clips.clean_all([manifest], source, tmp_path, cfg)[0]
    assert [word["text"] for word in cleaned["words_after_clean"]] == ["cannot", "allow", "this."]


@pytest.mark.parametrize("stage", ["review", "cut", "clean"])
def test_unsafe_partial_word_fallback_stops_before_model_or_media(monkeypatch, tmp_path, stage):
    source = [
        {"text": "We", "start": 0, "end": .6},
        {"text": "cannot", "start": .5, "end": 1.2},
        {"text": "allow", "start": 1.1, "end": 1.6},
        {"text": "this.", "start": 1.5, "end": 2.2},
        {"text": "Ever.", "start": 2.1, "end": 3},
    ]
    clip = {"rank": 1, "slug": "unsafe", "start": .8, "end": 1.8,
            "segments": [[.8, 1.8]]}
    requests = approve(monkeypatch)
    calls = media_stub(monkeypatch)
    with pytest.raises(ValueError, match="word"):
        if stage == "review":
            find_clips.check_stories([clip], source, tmp_path / "story_review.json", Config())
        elif stage == "cut":
            cut_clips.cut_one(clip, source, tmp_path / "source.mp4", tmp_path, Config())
        else:
            manifest = {"rank": 1, "raw_path": str(tmp_path / "raw.mp4"),
                        "snapped_segments": [[.8, 1.8]]}
            clean_clips.clean_all([manifest], source, tmp_path, Config())
    assert requests == []
    assert calls == []


def test_partial_filler_edits_keep_the_whole_word_in_review_and_audio(monkeypatch, tmp_path):
    source = [{"text": "We", "start": 0, "end": .7},
              {"text": "um", "start": .5, "end": 1},
              {"text": "agree.", "start": 1.1, "end": 2.2}]
    clip = {"rank": 1, "slug": "overlap", "start": 0, "end": 2.2,
            "segments": [[0, 2.2]]}
    requests = approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    assert requests[0]["clips"][0]["segments"][0]["text"] == "We um agree."
    media_stub(monkeypatch)
    manifest = cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)
    cleaned = clean_clips.clean_all([manifest], source, tmp_path, cfg)[0]
    assert cleaned["clean_cuts"] == []
    assert [word["text"] for word in cleaned["words_after_clean"]] == ["We", "um", "agree."]


@pytest.mark.parametrize("quirk", ["overlap", "empty_text"])
def test_story_opt_out_cuts_and_cleans_transcript_quirks(monkeypatch, tmp_path, quirk):
    source = [
        {"text": "We", "start": 0, "end": .6},
        {"text": "cannot", "start": .5, "end": 1.2},
        {"text": "allow", "start": 1.1, "end": 1.6},
        {"text": "this.", "start": 1.5, "end": 2.2},
        {"text": "Ever.", "start": 2.1, "end": 3},
    ]
    if quirk == "empty_text":
        source = words("We can allow this.")
        source[1]["text"] = " "
    clip = {"rank": 1, "slug": "quirk", "start": .8, "end": 1.8,
            "segments": [[.8, 1.8]]}
    calls = media_stub(monkeypatch)
    cfg = Config(story_check=False)

    manifest = cut_clips.cut_one(clip, source, tmp_path / "source.mp4", tmp_path, cfg)
    cleaned = clean_clips.clean_all([manifest], source, tmp_path, cfg)[0]

    assert calls
    assert Path(cleaned["cleaned_path"]).is_file()
    assert "story_review" not in cleaned
    assert cleaned["words_after_clean"]


@pytest.mark.parametrize("stage", ["cut", "clean"])
def test_story_check_still_rejects_empty_word_text(monkeypatch, tmp_path, stage):
    source = words("We can allow this.")
    source[1]["text"] = " "
    clip = {"rank": 1, "slug": "empty", "start": 0, "end": 2.2,
            "segments": [[0, 2.2]]}
    calls = media_stub(monkeypatch)

    with pytest.raises(ValueError, match="spoken text"):
        if stage == "cut":
            cut_clips.cut_one(clip, source, tmp_path / "source.mp4", tmp_path, Config())
        else:
            manifest = {"rank": 1, "raw_path": str(tmp_path / "raw.mp4"),
                        "snapped_segments": [[0, 2.2]]}
            clean_clips.clean_all([manifest], source, tmp_path, Config())

    assert calls == []


@pytest.mark.parametrize("story_check", [False, True])
@pytest.mark.parametrize("invalid", ["negative", "nonfinite", "inverted", "unordered", "segment"])
def test_clean_plan_keeps_time_validation_with_story_opt_out(story_check, invalid):
    source = words("We agree.")
    segments = [[0, 1]]
    if invalid == "negative":
        source[0]["start"] = -1
    elif invalid == "nonfinite":
        source[0]["end"] = float("nan")
    elif invalid == "inverted":
        source[0]["start"], source[0]["end"] = .3, .1
    elif invalid == "unordered":
        source[0]["start"], source[0]["end"] = .7, .9
    else:
        segments = [[1, 0]]

    with pytest.raises(ValueError):
        clean_clips.plan_cleaning(source, segments, Config(story_check=story_check))


def test_story_review_reads_final_retained_speech_once(monkeypatch, tmp_path):
    source, clip = question()
    requests = approve(monkeypatch)

    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", Config())

    assert len(requests) == 1
    spoken = " ".join(segment["text"] for segment in requests[0]["clips"][0]["segments"])
    assert spoken == "How do you know this works? we tested it."
    assert checked[0]["story_review"]["retained_speech_digest"]


def test_default_cleaning_preserves_contentful_you_know(monkeypatch, tmp_path):
    source, clip = question()
    approve(monkeypatch)
    calls = media_stub(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    manifest = cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)

    cleaned = clean_clips.clean_all([manifest], source, tmp_path, cfg)[0]

    assert " ".join(word["text"] for word in cleaned["words_after_clean"]) == (
        "How do you know this works? we tested it."
    )
    assert cleaned["story_review"]["decision"] == "pass"
    assert cleaned["duration"] == 8.5
    assert any(call[0] == "ffprobe" for call in calls)


def test_new_speech_deletion_invalidates_approval_before_cut(monkeypatch, tmp_path):
    source, clip = question()
    approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    cfg.fillers.append("know")
    calls = media_stub(monkeypatch)

    with pytest.raises(ValueError, match="story review"):
        cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)

    assert calls == []


def test_resumed_cleaning_checks_the_retained_speech_again(monkeypatch, tmp_path):
    source, clip = question()
    approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    media_stub(monkeypatch)
    manifest = cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)
    cfg.fillers.append("know")
    calls = media_stub(monkeypatch)

    with pytest.raises(ValueError, match="story review"):
        clean_clips.clean_all([manifest], source, tmp_path, cfg)

    assert calls == []


def test_measured_segment_duration_cannot_silently_drop_reviewed_speech(monkeypatch, tmp_path):
    source, clip = question()
    approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    media_stub(monkeypatch)
    manifest = cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)
    manifest["segment_durations"] = [1.0]
    calls = media_stub(monkeypatch)

    with pytest.raises(ValueError, match="story review"):
        clean_clips.clean_all([manifest], source, tmp_path, cfg)

    assert calls == []


@pytest.mark.parametrize("change", ["segments", "seam"])
def test_clean_resume_rejects_changed_cut_timing_even_if_retained_words_match(
    monkeypatch, tmp_path, change,
):
    source = [{"text": "Intro", "start": .1, "end": .4},
              {"text": "not", "start": 1.7, "end": 1.77},
              {"text": "um", "start": 1.8, "end": 2.1},
              {"text": "pay.", "start": 2.2, "end": 2.5}]
    clip = {"rank": 1, "slug": "timing", "start": 0, "end": 3, "segments": [[0, 3]]}
    approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    media_stub(monkeypatch, "3.0")
    manifest = cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)
    if change == "segments":
        manifest["snapped_segments"] = [[0, 1], [1, 3]]
        manifest["segment_durations"] = [1, 2]
    else:
        manifest["seam_xfade_seconds"] = .3
    calls = media_stub(monkeypatch)

    with pytest.raises(ValueError, match="story review"):
        clean_clips.clean_all([manifest], source, tmp_path, cfg)

    assert calls == []


def test_new_cut_rejects_a_changed_seam_setting_after_review(monkeypatch, tmp_path):
    source, clip = question()
    approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    cfg.seam_xfade_seconds = .3
    calls = media_stub(monkeypatch)

    with pytest.raises(ValueError, match="story review"):
        cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)

    assert calls == []


def test_opt_out_allows_changed_cleaning_settings(monkeypatch, tmp_path):
    source, clip = question()
    approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    cfg.story_check = False
    cfg.fillers.append("know")
    media_stub(monkeypatch)

    manifest = cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)
    cleaned = clean_clips.clean_all([manifest], source, tmp_path, cfg)[0]

    assert "know" not in [word["text"] for word in cleaned["words_after_clean"]]
    assert "story_review" not in cleaned or cleaned["story_review"].get("decision") != "pass"


def test_cleaner_maps_words_to_actual_crossfade_timeline(monkeypatch, tmp_path):
    source = [
        {"text": "First.", "start": .2, "end": .7},
        {"text": "um", "start": 10.3, "end": 10.5},
        {"text": "Profit.", "start": 10.51, "end": 11.0},
    ]
    manifest = {"rank": 3, "raw_path": str(tmp_path / "raw.mp4"),
                "snapped_segments": [[0, 2], [10, 12]],
                "segment_durations": [2.04, 2.04], "seam_xfade_seconds": .08}
    media_stub(monkeypatch, "2.75")

    cleaned = clean_clips.clean_all([manifest], source, tmp_path, Config(story_check=False))[0]

    actual_next_start = round(2.04 - .08 + .51, 3)
    actual_next_end = round(2.04 - .08 + 1.0, 3)
    assert all(not (start < actual_next_end and end > actual_next_start)
               for start, end in cleaned["clean_cuts"])
    assert [word["text"] for word in cleaned["words_after_clean"]] == ["First.", "Profit."]
    assert cleaned["duration"] == 2.75


def test_cutter_records_measured_duration_and_seam_metadata(monkeypatch, tmp_path):
    source = words("First clip has words. Next clip has words.")
    clip = {"rank": 1, "slug": "two", "start": 0, "end": 4.6,
            "segments": [[0, 2.3], [2.3, 4.6]]}
    calls = media_stub(monkeypatch, "3.875")

    manifest = cut_clips.cut_one(clip, source, tmp_path / "source.mp4", tmp_path, Config())

    assert manifest["duration"] == 3.875
    assert manifest["segment_durations"] == [3.875, 3.875]
    assert manifest["seam_xfade_seconds"] == .08
    assert len([call for call in calls if call[0] == "ffprobe"]) == 3


def test_failed_clean_cannot_copy_unreviewed_words_and_keep_a_pass(monkeypatch, tmp_path):
    source, clip = question()
    approve(monkeypatch)
    cfg = Config()
    checked = find_clips.check_stories([clip], source, tmp_path / "story_review.json", cfg)[0]
    media_stub(monkeypatch)
    manifest = cut_clips.cut_one(checked, source, tmp_path / "source.mp4", tmp_path, cfg)
    original = deepcopy(manifest)

    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr=b"Fixture failure")

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(RuntimeError, match="review"):
        clean_clips.clean_all([manifest], source, tmp_path, cfg)

    assert manifest == original
