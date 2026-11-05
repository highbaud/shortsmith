"""Public alignment recovery validates external worker artifacts before reuse."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from shortsmith import align
from shortsmith.config import Config


@pytest.fixture
def alignment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    module = align
    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "align_batch.py").write_text("", encoding="ascii")
    monkeypatch.setattr(module, "WHISPERX_ALIGN_PROJECT", worker)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"opaque external input")
    os.utime(clip, (0, 0))
    model_calls = []
    outcome = {"event": "fail"}

    class Worker:
        returncode = 0

        def communicate(self, input=None, timeout=None):
            return json.dumps({"event": outcome["event"], "in": str(clip)}), ""

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            model_calls.append(True)

        def transcribe(self, path, **kwargs):
            if outcome.get("fallback_error"):
                raise RuntimeError("fallback unavailable")
            return [SimpleNamespace(words=[SimpleNamespace(
                word="recovered", start=0.0, end=0.4,
            )])], SimpleNamespace()

    def unavailable_audio(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr=b"external decode unavailable")

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Worker())
    monkeypatch.setattr(subprocess, "run", unavailable_audio)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=WhisperModel))
    return {"module": module, "clip": clip, "words": clip.with_suffix(".words.json"),
            "model_calls": model_calls, "outcome": outcome}


@pytest.mark.parametrize("worker_event", ["fail", "ok"])
@pytest.mark.parametrize("payload", [
    '[{"text":',
    '{"not_words":true}',
    '[{"text":"bad time","start":1,"end":0}]',
    '[{"text":"","start":0,"end":1}]',
])
def test_invalid_newer_or_reported_alignment_runs_real_fallback(
    alignment: dict, worker_event: str, payload: str, caplog: pytest.LogCaptureFixture,
) -> None:
    alignment["outcome"]["event"] = worker_event
    alignment["words"].write_text(payload, encoding="utf-8")

    manifest = {"rank": 1, "raw_path": str(alignment["clip"]), "alignment_failed": True}
    result = alignment["module"].align_all([manifest], Config(align_engine="whisperx"))

    assert len(alignment["model_calls"]) == 1
    assert result[0]["words_path"] == str(alignment["words"])
    assert json.loads(alignment["words"].read_text(encoding="utf-8")) == [
        {"text": "recovered", "start": 0.0, "end": 0.4}]
    assert "Ignoring invalid alignment" in caplog.text
    assert str(alignment["words"]) in caplog.text
    assert "alignment_failed" not in result[0]


def test_valid_legacy_cached_words_keep_extra_data_without_model_calls(alignment: dict) -> None:
    previous = '[{"word":"kept","start":0,"end":1,"note":"preserve"}]'
    alignment["words"].write_text(previous, encoding="utf-8")

    result = alignment["module"].align_all(
        [{"rank": 1, "raw_path": str(alignment["clip"]), "alignment_failed": False}],
        Config(align_engine="whisperx"),
    )

    assert alignment["model_calls"] == []
    assert result[0]["words_path"] == str(alignment["words"])
    assert alignment["words"].read_text(encoding="utf-8") == previous
    assert "alignment_failed" not in result[0]


def test_current_empty_alignment_remains_valid(alignment: dict) -> None:
    alignment["outcome"]["event"] = "ok"
    alignment["words"].write_text("[]", encoding="utf-8")

    result = alignment["module"].align_all(
        [{"rank": 1, "raw_path": str(alignment["clip"]), "alignment_failed": True}],
        Config(align_engine="whisperx"),
    )

    assert alignment["model_calls"] == []
    assert alignment["words"].read_text(encoding="utf-8") == "[]"
    assert "alignment_failed" not in result[0]


def test_empty_unconfirmed_cache_keeps_existing_retry_policy(alignment: dict) -> None:
    alignment["words"].write_text("[]", encoding="utf-8")

    alignment["module"].align_all(
        [{"rank": 1, "raw_path": str(alignment["clip"])}], Config(align_engine="whisperx"),
    )

    assert len(alignment["model_calls"]) == 1
    assert json.loads(alignment["words"].read_text(encoding="utf-8")) == [
        {"text": "recovered", "start": 0.0, "end": 0.4}]


def test_total_failure_marks_manifest_and_retries_empty_placeholder(alignment: dict) -> None:
    alignment["outcome"]["fallback_error"] = True
    manifest = {"rank": 1, "raw_path": str(alignment["clip"])}
    cfg = Config(align_engine="whisperx")

    result = alignment["module"].align_all([manifest], cfg)

    assert result[0]["alignment_failed"] is True
    assert result[0]["words_path"] == str(alignment["words"])
    assert alignment["words"].read_text(encoding="utf-8") == "[]"
    assert len(alignment["model_calls"]) == 1

    alignment["outcome"]["fallback_error"] = False
    alignment["module"].align_all(result, cfg)

    assert len(alignment["model_calls"]) == 2
    assert "alignment_failed" not in manifest
    assert json.loads(alignment["words"].read_text(encoding="utf-8")) == [
        {"text": "recovered", "start": 0.0, "end": 0.4}]
