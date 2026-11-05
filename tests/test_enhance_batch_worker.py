"""ClearVoice worker results must come from the current provider attempt."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("first_result", ["no_output", "partial_error"])
def test_worker_rejects_stale_audio_cleans_current_scratch_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], first_result: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("clearvoice_worker_test", root / "audio-enhance/enhance_batch.py")
    assert spec is not None and spec.loader is not None
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    source_a, source_b = tmp_path / "a.wav", tmp_path / "b.wav"
    source_a.write_bytes(b"source a")
    source_b.write_bytes(b"source b")
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    target_a, target_b = outputs / "a-enhanced.wav", outputs / "b-enhanced.wav"
    target_a.write_bytes(b"prior accepted output")
    stale = outputs / "_cv_tmp_0" / target_a.name / "MossFormer2_SE_48K" / source_a.name
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale provider audio")

    class ClearVoice:
        def __init__(self, **kwargs):
            pass

        def __call__(self, *, input_path, online_write, output_path):
            generated = Path(output_path) / "MossFormer2_SE_48K" / Path(input_path).name
            if Path(input_path) == source_a:
                if first_result == "partial_error":
                    generated.parent.mkdir(parents=True, exist_ok=True)
                    generated.write_bytes(b"failed partial")
                    raise RuntimeError("provider failed after writing")
                return
            generated.parent.mkdir(parents=True, exist_ok=True)
            generated.write_bytes(b"fresh provider audio")

    monkeypatch.setitem(sys.modules, "clearvoice", SimpleNamespace(ClearVoice=ClearVoice))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([
        {"in": str(source_a), "out": str(target_a)},
        {"in": str(source_b), "out": str(target_b)},
    ])))

    assert worker.main() == 0

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    jobs = {event["in"]: event for event in events if "in" in event}
    assert jobs[str(source_a)]["event"] == "fail"
    if first_result == "no_output":
        assert jobs[str(source_a)]["reason"] == "no_output"
    else:
        assert "provider failed after writing" in jobs[str(source_a)]["error"]
    assert jobs[str(source_b)]["event"] == "ok"
    assert target_a.read_bytes() == b"prior accepted output"
    assert target_b.read_bytes() == b"fresh provider audio"
    assert stale.read_bytes() == b"stale provider audio"
    assert [path.name for path in outputs.iterdir() if path.is_dir()] == ["_cv_tmp_0"]
    assert source_a.read_bytes() == b"source a"
    assert source_b.read_bytes() == b"source b"
