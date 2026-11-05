"""Batch transcription reports failures and keeps successful item outputs."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from scripts import batch_transcribe
from shortsmith import config


@pytest.fixture
def batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    videos = tmp_path / "videos"
    videos.mkdir()
    monkeypatch.setattr(batch_transcribe, "VIDEO_DIR", videos)
    monkeypatch.setattr(batch_transcribe, "__file__", str(tmp_path / "scripts" / "batch_transcribe.py"))
    monkeypatch.setattr(config, "__file__", str(tmp_path / "shortsmith" / "config.py"))
    loaded, calls = [], []
    failures = set()

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            loaded.append(args)

        def transcribe(self, video: str, **kwargs):
            calls.append(Path(video).stem)
            if Path(video).stem in failures:
                raise RuntimeError("model failed for " + Path(video).name)
            return [SimpleNamespace(words=[SimpleNamespace(word=" kept ", start=0.0, end=1.0)])], None

    service = ModuleType("faster_whisper")
    service.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", service)
    return {"root": tmp_path, "videos": videos, "loaded": loaded, "calls": calls, "failures": failures}


@pytest.mark.parametrize("failures", [set(), {"a-first"}, {"a-first", "b-second"}])
def test_batch_exit_status_matches_item_results_and_keeps_successes(
    batch: dict, failures: set[str], caplog: pytest.LogCaptureFixture,
) -> None:
    batch["failures"].update(failures)
    for name in ("a-first", "b-second"):
        (batch["videos"] / f"{name}.mp4").write_bytes(b"video")
    with caplog.at_level(logging.INFO, logger=batch_transcribe.log.name):
        assert batch_transcribe.main() == (1 if failures else 0)
    assert batch["calls"] == ["a-first", "b-second"]
    assert len(batch["loaded"]) == 1
    for name in ("a-first", "b-second"):
        work = batch["root"] / "work" / name
        if name in failures:
            assert "model failed" in (work / "transcribe_failed.txt").read_text(encoding="utf-8")
            assert not (work / "transcript.json").exists()
        else:
            assert json.loads((work / "transcript.json").read_text(encoding="utf-8")) == [
                {"text": "kept", "start": 0.0, "end": 1.0}]
    assert f"Batch complete: succeeded={2 - len(failures)} failed={len(failures)}" in caplog.messages


def test_failed_item_does_not_replace_an_existing_transcript(batch: dict) -> None:
    (batch["videos"] / "source.mp4").write_bytes(b"video")
    work = batch["root"] / "work" / "source"
    work.mkdir(parents=True)
    previous = b'[{"text":"prior","start":0,"end":1}]'
    (work / "transcript.json").write_bytes(previous)
    batch["failures"].add("source")
    assert batch_transcribe.main() == 1
    assert (work / "transcript.json").read_bytes() == previous


def test_work_directory_failure_does_not_stop_remaining_items(
    batch: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("a-first", "b-second"):
        (batch["videos"] / f"{name}.mp4").write_bytes(b"video")
    mkdir = Path.mkdir

    def make_directory(path: Path, *args, **kwargs) -> None:
        if path == batch["root"] / "work" / "a-first":
            raise OSError("work directory cannot be created")
        mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", make_directory)
    assert batch_transcribe.main() == 1
    assert batch["calls"] == ["b-second"]
    assert (batch["root"] / "work" / "b-second" / "transcript.json").is_file()


def test_failure_marker_write_error_does_not_stop_remaining_items(
    batch: dict, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    for name in ("a-first", "b-second"):
        (batch["videos"] / f"{name}.mp4").write_bytes(b"video")
    batch["failures"].add("a-first")
    replace = Path.replace

    def replace_file(path: Path, target: Path) -> Path:
        if Path(target).name == "transcribe_failed.txt":
            raise OSError("marker write failed")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", replace_file)
    assert batch_transcribe.main() == 1
    assert (batch["root"] / "work" / "b-second" / "transcript.json").is_file()
    assert "Cannot record transcription failure" in caplog.text


def test_malformed_existing_transcript_is_reported_and_retried(
    batch: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    (batch["videos"] / "source.mp4").write_bytes(b"video")
    work = batch["root"] / "work" / "source"
    work.mkdir(parents=True)
    (work / "transcript.json").write_text("{broken", encoding="utf-8")
    assert batch_transcribe.main() == 0
    assert "Cannot reuse transcript" in caplog.text and str(work / "transcript.json") in caplog.text
    assert batch["calls"] == ["source"]


def test_complete_existing_transcript_skips_model_loading(batch: dict) -> None:
    (batch["videos"] / "source.mp4").write_bytes(b"video")
    work = batch["root"] / "work" / "source"
    work.mkdir(parents=True)
    # Existing reuse policy is more than 100 words.
    (work / "transcript.json").write_text(json.dumps([{"text": "kept"}] * 101), encoding="utf-8")
    assert batch_transcribe.main() == 0
    assert batch["loaded"] == []
