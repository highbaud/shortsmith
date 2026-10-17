"""Exercise the story gate through real parsing, providers, reports, and CLI."""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from shortsmith import config, cut_clips, find_clips, story_review
from shortsmith.config import Config
from shortsmith.pipeline import cli


@pytest.fixture
def words():
    text = (
        "The plan needs cash. That plan makes sense. We can fund it. "
        "The final point is clear and complete today."
    )
    return [{"text": word, "start": float(i), "end": i + 0.5}
            for i, word in enumerate(text.split())]


@pytest.fixture
def picks():
    return [
        {"rank": 1, "start": 0, "end": 7.5, "segments": [[0, 7.5]],
         "viral_score": 9, "slug": "complete-plan", "hook_text": "Generated hook"},
        {"rank": 2, "start": 4, "end": 11.5, "segments": [[4, 11.5]],
         "viral_score": 8, "slug": "needs-setup"},
        {"rank": 3, "start": 12, "end": 20.5, "segments": [[12, 20.5]],
         "viral_score": 7, "slug": "uncertain"},
    ]


def passed(clip_id):
    return {"clip_id": clip_id, "decision": "pass", "reason": "Complete thought.", "issues": []}


def fake_claude(monkeypatch, picks, reviews=None, failure=None):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        if kwargs["system"] == story_review.SYSTEM_PROMPT:
            if failure:
                raise failure
            payload = json.loads(kwargs["messages"][0]["content"])
            result = reviews(payload) if reviews else [passed(c["clip_id"]) for c in payload["clips"]]
        else:
            result = picks
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="text", text=json.dumps(result)),
        ])

    monkeypatch.setattr("anthropic.Anthropic", lambda **_: SimpleNamespace(
        messages=SimpleNamespace(create=create),
    ))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return requests


def mixed_reviews(payload):
    assert payload["clips"][1]["segments"][0]["text"].startswith("That plan")
    return [passed(0), {
        "clip_id": 1, "decision": "reject", "reason": "Missing the plan's premise.",
        "issues": [{
            "type": "missing_setup", "explanation": "The plan is not introduced.",
            "segment_id": 0, "selected_quote": "That plan",
            "context_quote": "The plan needs cash.", "context_sentence_id": 0,
        }],
    }, {
        "clip_id": 2, "decision": "needs_review", "reason": "Unclear payoff.", "issues": [],
    }]


def test_new_picks_keep_only_passes_and_preserve_all_decisions(monkeypatch, tmp_path, words, picks):
    requests = fake_claude(monkeypatch, picks, mixed_reviews)
    original = deepcopy((words, picks))
    cfg = Config()
    out = tmp_path / "clips.json"

    clips = find_clips.find_clips(words, out, cfg)

    assert len(requests) == 2
    assert [clip["slug"] for clip in clips] == ["complete-plan"]
    assert json.loads(out.read_text()) == clips
    report = json.loads((tmp_path / "story_review.json").read_text())
    assert report["summary"] == {"pass": 1, "reject": 1, "needs_review": 1}
    assert len(report["clips"]) == 3
    assert report["clips"][1]["slug"] == "needs-setup"
    issue = report["clips"][1]["story_review"]["issues"][0]
    assert issue["context_start"] == 0.0
    assert issue["context_end"] == 3.5
    assert issue["selected_quote"] == "That plan"
    assert "Generated hook" not in requests[1]["messages"][0]["content"]
    assert "viral_score" not in requests[1]["messages"][0]["content"]
    assert clips[0]["story_review"]["source_digest"] == story_review.source_digest(words)
    assert (words, picks) == original


@pytest.mark.parametrize("setting, enabled", [("on", True), ("off", False), ("false", False)])
def test_env_switch_is_read_for_each_config(monkeypatch, setting, enabled):
    monkeypatch.setenv("SHORTSMITH_STORY_CHECK", setting)
    assert Config().story_check is enabled


def test_disabled_check_uses_only_pick_call(monkeypatch, tmp_path, words, picks):
    requests = fake_claude(monkeypatch, picks, failure=AssertionError("Must not review"))

    clips = find_clips.find_clips(words, tmp_path / "clips.json", Config(story_check=False))

    assert len(requests) == 1
    assert len(clips) == 3
    assert not (tmp_path / "story_review.json").exists()
    assert all("story_review" not in clip for clip in clips)


def test_provider_failure_preserves_previous_clips_and_report(monkeypatch, tmp_path, words, picks):
    fake_claude(monkeypatch, picks, failure=RuntimeError("Offline"))
    out = tmp_path / "clips.json"
    report = tmp_path / "story_review.json"
    out.write_text("previous clips")
    report.write_text("previous report")

    with pytest.raises(RuntimeError, match="clips were not replaced"):
        find_clips.find_clips(words, out, Config(story_check=True))

    assert out.read_text() == "previous clips"
    assert report.read_text() == "previous report"


def test_incomplete_review_cannot_silently_pass_missing_clips(monkeypatch, tmp_path, words, picks):
    fake_claude(monkeypatch, picks, lambda _: [passed(0)])

    clips = find_clips.find_clips(words, tmp_path / "clips.json", Config(story_check=True))

    assert len(clips) == 1
    report = json.loads((tmp_path / "story_review.json").read_text())
    assert report["summary"] == {"pass": 1, "reject": 0, "needs_review": 2}
    assert report["clips"][1]["story_review"]["validation_errors"] == ["missing clip review"]


def test_reviewed_words_use_actual_snapped_end(monkeypatch, tmp_path, words, picks):
    requests = fake_claude(monkeypatch, picks)
    clip = {**picks[1], "end": 10.1, "segments": [[4, 10.1]]}

    approved = find_clips.check_stories([clip], words, tmp_path / "story_review.json", Config())

    reviewed = json.loads(requests[0]["messages"][0]["content"])["clips"][0]["segments"][0]
    assert reviewed["text"] == "That plan makes sense. We can fund it."
    assert reviewed["end"] > 10.1
    assert approved[0]["segments"] == [[4, 10.1]]
    assert approved[0]["story_review"]["reviewed_speech_segments"] == [
        [reviewed["start"], reviewed["end"]]
    ]
    assert approved[0]["story_review"]["reviewed_segments"] == cut_clips.plan_segments(
        clip, words, Config(),
    )


@pytest.mark.parametrize("change", ["words", "cuts", "decision"])
def test_stale_review_stops_before_media_calls(monkeypatch, tmp_path, words, picks, change):
    fake_claude(monkeypatch, picks)
    approved = find_clips.check_stories([picks[1]], words, tmp_path / "story_review.json", Config())[0]
    if change == "words":
        words[4]["text"] = "A different plan"
    elif change == "cuts":
        approved["segments"] = [[8, 20.5]]
    else:
        approved["story_review"]["decision"] = "needs_review"
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(ValueError, match="approved story review"):
        cut_clips.cut_one(approved, words, Path("source.mp4"), tmp_path, Config())

    assert calls == []


@pytest.mark.parametrize("enabled", [True, False])
def test_cut_uses_reviewed_plan_and_opt_out_bypasses_stale_guard(
    monkeypatch, tmp_path, words, picks, enabled,
):
    fake_claude(monkeypatch, picks)
    approved = find_clips.check_stories([picks[1]], words, tmp_path / "story_review.json", Config())[0]
    if not enabled:
        approved["story_review"]["source_digest"] = "stale"
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="8.0\n")

    monkeypatch.setattr(subprocess, "run", run)

    manifest = cut_clips.cut_one(
        approved, words, Path("source.mp4"), tmp_path, Config(story_check=enabled),
    )

    assert manifest["snapped_segments"] == approved["story_review"]["reviewed_segments"]
    assert len(calls) == 4
    assert calls[0][calls[0].index("-ss") + 1] == f'{manifest["snapped_segments"][0][0]:.3f}'
    assert calls[0][calls[0].index("-to") + 1] == f'{manifest["snapped_segments"][0][1]:.3f}'
    assert manifest["duration"] == 8.0


@pytest.mark.parametrize("hold", [False, True])
def test_report_command_preserves_saved_picks_and_returns_status(monkeypatch, tmp_path, words, picks, hold):
    fake_claude(monkeypatch, picks, mixed_reviews if hold else None)
    clips_path = tmp_path / "clips.json"
    clips_path.write_text(json.dumps(picks))
    (tmp_path / "transcript.json").write_text(json.dumps(words))
    original = clips_path.read_bytes()

    result = CliRunner().invoke(cli, ["story-check", str(tmp_path), "--clip-engine", "anthropic"])

    assert result.exit_code == (1 if hold else 0), result.output
    assert "clips.json was not changed" in result.output
    assert (tmp_path / "story_review.json").exists()
    assert clips_path.read_bytes() == original


def test_explicit_check_requires_cut_step_for_resume(tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"")

    result = CliRunner().invoke(cli, ["run", str(video), "--from-step", "4", "--story-check"])

    assert result.exit_code == 2
    assert "--from-step 3 --story-check" in result.output


def test_empty_picks_write_empty_report_without_call(monkeypatch, tmp_path, words):
    requests = fake_claude(monkeypatch, [])

    clips = find_clips.find_clips(words, tmp_path / "clips.json", Config(story_check=True))

    assert clips == []
    assert len(requests) == 1
    assert json.loads((tmp_path / "story_review.json").read_text())["summary"] == {
        "pass": 0, "reject": 0, "needs_review": 0,
    }


def test_recheck_preserves_held_clips_in_report_history(monkeypatch, tmp_path, words, picks):
    fake_claude(monkeypatch, picks, mixed_reviews)
    approved = find_clips.find_clips(words, tmp_path / "clips.json", Config())
    old_report = (tmp_path / "story_review.json").read_bytes()
    fake_claude(monkeypatch, picks)

    find_clips.check_stories(approved, words, tmp_path / "story_review.json", Config())

    archives = list((tmp_path / "story_review_history").glob("*.json"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == old_report
    archived = json.loads(archives[0].read_text())
    assert [clip["slug"] for clip in archived["clips"]] == [
        "complete-plan", "needs-setup", "uncertain",
    ]
    assert archived["summary"] == {"pass": 1, "reject": 1, "needs_review": 1}
    find_clips.check_stories(approved, words, tmp_path / "story_review.json", Config())
    assert list((tmp_path / "story_review_history").glob("*.json")) == archives


def run_files(monkeypatch, tmp_path, words, picks):
    # Model an installed layout inside the test filesystem, without replacing
    # the work-dir builder or any pipeline behavior.
    monkeypatch.setattr(config, "__file__", str(tmp_path / "shortsmith" / "config.py"))
    monkeypatch.setattr(config, "KIT_ROOT", tmp_path)
    monkeypatch.setattr(config, "TEMPLATE_REF", tmp_path)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"")
    work = tmp_path / "work" / "source"
    work.mkdir(parents=True)
    (work / "transcript.json").write_text(json.dumps(words))
    (work / "clips.json").write_text(json.dumps(picks))
    return video, work


def test_explicit_resume_check_holds_clips_before_any_cut(monkeypatch, tmp_path, words, picks):
    requests = fake_claude(monkeypatch, picks, lambda payload: [
        {"clip_id": c["clip_id"], "decision": "needs_review", "reason": "Unclear story.", "issues": []}
        for c in payload["clips"]
    ])
    monkeypatch.setenv("SHORTSMITH_STORY_CHECK", "off")
    video, work = run_files(monkeypatch, tmp_path, words, picks)
    media_calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: media_calls.append(a))

    result = CliRunner().invoke(cli, ["run", str(video), "--from-step", "3",
                                    "--story-check", "--no-enhance", "--clip-engine", "anthropic"])

    assert result.exit_code == 1, result.output
    assert len(requests) == 1
    assert requests[0]["system"] == story_review.SYSTEM_PROMPT
    assert media_calls == []
    assert json.loads((work / "clips.json").read_text()) == []
    assert json.loads((work / "story_review.json").read_text())["summary"]["needs_review"] == 3


def test_run_opt_out_reaches_cutter_without_review_call(monkeypatch, tmp_path, words, picks):
    requests = fake_claude(monkeypatch, picks, failure=AssertionError("Must not review"))
    monkeypatch.setenv("SHORTSMITH_STORY_CHECK", "on")
    video, work = run_files(monkeypatch, tmp_path, words, picks)
    media_calls = []

    def stop_at_media(argv, **kwargs):
        media_calls.append(argv)
        raise RuntimeError("Reached media boundary")

    monkeypatch.setattr(subprocess, "run", stop_at_media)

    result = CliRunner().invoke(cli, ["run", str(video), "--from-step", "2",
                                    "--no-story-check", "--no-enhance", "--clip-engine", "anthropic"])

    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "Reached media boundary"
    assert len(requests) == 1
    assert len(media_calls) == 1
    assert media_calls[0][0] == "ffmpeg"
    assert len(json.loads((work / "clips.json").read_text())) == 3
    assert not (work / "story_review.json").exists()
