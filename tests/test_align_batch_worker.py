"""WhisperX worker commits complete JSON and preserves prior output on failure."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("failure_mode", ["write", "replace"])
def test_failed_worker_commit_preserves_prior_json_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], empty: bool, failure_mode: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("whisperx_worker_test", root / "whisperx-align/align_batch.py")
    assert spec is not None and spec.loader is not None
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    source_a, source_b = tmp_path / "a.wav", tmp_path / "b.wav"
    source_a.write_bytes(b"source a")
    source_b.write_bytes(b"source b")
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    target_a, target_b = outputs / "a.json", outputs / "b.json"
    previous = b'[{"text":"prior","start":0,"end":1,"note":"keep"}]'
    target_a.write_bytes(previous)
    asr = SimpleNamespace(transcribe=lambda *args, **kwargs: {"segments": [] if empty else [{}]})
    service = SimpleNamespace(
        load_model=lambda *args, **kwargs: asr,
        load_align_model=lambda **kwargs: (None, {}),
        load_audio=lambda path: path,
        align=lambda *args, **kwargs: {"segments": [{"words": [
            {"word": " fresh ", "start": 0.12349, "end": 0.9876},
            {"word": "unaligned punctuation"},
        ]}]},
    )
    monkeypatch.setitem(sys.modules, "whisperx", service)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([
        {"in": str(source_a), "out": str(target_a)},
        {"in": str(source_b), "out": str(target_b)},
    ])))
    replace = os.replace

    def cannot_commit_first(source, target):
        assert Path(source).parent == Path(target).parent == outputs
        if Path(target) == target_a:
            raise OSError("worker output locked")
        return replace(source, target)

    if failure_mode == "replace":
        monkeypatch.setattr(os, "replace", cannot_commit_first)
    elif hasattr(worker, "NamedTemporaryFile"):
        temporary_file = worker.NamedTemporaryFile

        class FailedWrite:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                stream = self.stream.__enter__()

                def cannot_write(text):
                    stream.write(text[:1])
                    raise OSError("worker write interrupted")

                return SimpleNamespace(name=stream.name, write=cannot_write)

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

        def interrupt_first_write(**kwargs):
            stream = temporary_file(**kwargs)
            return FailedWrite(stream) if kwargs["prefix"].startswith(target_a.name) else stream

        monkeypatch.setattr(worker, "NamedTemporaryFile", interrupt_first_write)

    assert worker.main() == 0

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    jobs = {event["in"]: event for event in events if "in" in event}
    assert jobs[str(source_a)]["event"] == "fail"
    reason = "worker output locked" if failure_mode == "replace" else "worker write interrupted"
    assert reason in jobs[str(source_a)]["error"]
    assert jobs[str(source_b)]["event"] == "ok"
    assert target_a.read_bytes() == previous
    expected = [] if empty else [{"text": "fresh", "start": 0.123, "end": 0.988}]
    assert json.loads(target_b.read_text(encoding="utf-8")) == expected
    assert set(path.name for path in outputs.iterdir()) == {"a.json", "b.json"}
