"""Speech enhancement releases a timed-out worker before it continues."""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from shortsmith import enhance_audio
from shortsmith.config import Config


def fake_voicefixer(monkeypatch: pytest.MonkeyPatch, restore: Callable) -> None:
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
    ))
    monkeypatch.setitem(sys.modules, "voicefixer", SimpleNamespace(
        VoiceFixer=lambda: SimpleNamespace(restore=restore),
    ))


def test_timed_out_worker_is_stopped_before_muxing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "audio-enhance"
    project.mkdir()
    (project / "enhance_batch.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(enhance_audio, "AUDIO_ENHANCE_PROJECT", project)
    events = []

    class Worker:
        def communicate(self, input=None, timeout=None):
            if input is not None:
                events.append("wait")
                for job in json.loads(input):
                    Path(job["out"]).write_bytes(b"enhanced audio")
                raise subprocess.TimeoutExpired("uv", timeout)
            events.append("reap")
            return "", ""

        def kill(self):
            events.append("kill")

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Worker())

    def unavailable(**kwargs):
        assert "reap" in events
        raise RuntimeError("provider unavailable")

    fake_voicefixer(monkeypatch, unavailable)
    muxed = []

    def ffmpeg(command, **kwargs):
        if Path(command[0]).stem.lower() != "ffmpeg":
            raise RuntimeError("provider unavailable")
        events.append("extract" if "-vn" in command else "mux")
        if "-vn" not in command:
            inputs = [command[i + 1] for i, value in enumerate(command) if value == "-i"]
            muxed.append(Path(inputs[1]).read_bytes())
        Path(command[-1]).write_bytes(b"original audio")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", ffmpeg)
    manifests = [{"rank": 1, "raw_path": str(tmp_path / "raw.mp4")}]

    result = enhance_audio.enhance_all(manifests, tmp_path, Config(loudness_enabled=False))

    assert events == ["extract", "wait", "kill", "reap", "mux"]
    assert Path(result[0]["enhanced_path"]).is_file()
    assert muxed == [b"original audio"]


@pytest.mark.parametrize("second_result", ["failed_partial", "reported_missing"])
def test_primary_completion_requires_current_success_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second_result: str,
) -> None:
    project = tmp_path / "audio-enhance"
    project.mkdir()
    (project / "enhance_batch.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(enhance_audio, "AUDIO_ENHANCE_PROJECT", project)
    audio = tmp_path / "enhanced" / "_tmp"
    audio.mkdir(parents=True)
    for rank in (7, 2):
        (audio / f"short-{rank:02d}_enh.wav").write_bytes(b"stale")
    muxed = []
    fallback_inputs = []

    class Worker:
        returncode = 0

        def communicate(self, input=None, timeout=None):
            jobs = json.loads(input)
            assert all(not Path(job["out"]).exists() for job in jobs)
            Path(jobs[0]["out"]).write_bytes(b"current enhanced")
            if second_result == "failed_partial":
                Path(jobs[1]["out"]).write_bytes(b"partial")
            return "\n".join(json.dumps({
                "event": "ok" if i == 0 or second_result == "reported_missing" else "fail",
                "in": job["in"], "out": job["out"],
            }) for i, job in enumerate(jobs)), ""

    def restore(**kwargs):
        assert not Path(kwargs["output"]).exists()
        fallback_inputs.append(Path(kwargs["input"]).name)
        Path(kwargs["output"]).write_bytes(b"fallback enhanced")

    def ffmpeg(command, **kwargs):
        if "-vn" not in command:
            inputs = [command[i + 1] for i, value in enumerate(command) if value == "-i"]
            muxed.append(Path(inputs[1]).read_bytes())
        Path(command[-1]).write_bytes(b"original audio")
        return subprocess.CompletedProcess(command, 0)

    fake_voicefixer(monkeypatch, restore)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Worker())
    monkeypatch.setattr(subprocess, "run", ffmpeg)
    manifests = [{"rank": rank, "raw_path": str(tmp_path / f"raw-{rank}.mp4"),
                  "editor_note": f"keep-{rank}"} for rank in (7, 2)]

    result = enhance_audio.enhance_all(manifests, tmp_path, Config(loudness_enabled=False))

    assert muxed == [b"current enhanced", b"fallback enhanced"]
    assert fallback_inputs == ["short-02_src.wav"]
    assert result is manifests
    assert [item["editor_note"] for item in result] == ["keep-7", "keep-2"]
    assert [Path(item["enhanced_path"]).name for item in result] == ["short-07.mp4", "short-02.mp4"]


def test_failed_fallback_partial_output_cannot_replace_original_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(enhance_audio, "AUDIO_ENHANCE_PROJECT", tmp_path / "missing-engine")
    muxed = []

    def restore(**kwargs):
        Path(kwargs["output"]).write_bytes(b"failed partial")
        raise RuntimeError("voice provider failed")

    def ffmpeg(command, **kwargs):
        if Path(command[0]).stem.lower() != "ffmpeg":
            raise RuntimeError("provider unavailable")
        if "-vn" not in command:
            inputs = [command[i + 1] for i, value in enumerate(command) if value == "-i"]
            muxed.append(Path(inputs[1]).read_bytes())
        Path(command[-1]).write_bytes(b"original audio")
        return subprocess.CompletedProcess(command, 0)

    fake_voicefixer(monkeypatch, restore)
    monkeypatch.setattr(subprocess, "run", ffmpeg)
    manifests = [{"rank": 1, "raw_path": str(tmp_path / "raw.mp4")}]

    enhance_audio.enhance_all(manifests, tmp_path, Config(loudness_enabled=False))

    assert muxed == [b"original audio"]


@pytest.mark.parametrize("scratch", ["_re_out", "_df_out"])
def test_fallback_cannot_reuse_output_from_a_previous_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch: str,
) -> None:
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("ffmpeg.exe", "resemble-enhance.exe", "deepFilter.exe"):
        (tools / name).write_bytes(b"")
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(enhance_audio, "AUDIO_ENHANCE_PROJECT", tmp_path / "missing-engine")
    stale = tmp_path / "enhanced" / "_tmp" / scratch / "short-01_src.wav"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale provider output")
    muxed = []

    def unavailable(**kwargs):
        raise RuntimeError("provider unavailable")

    def process(command, **kwargs):
        if Path(command[0]).stem.lower() == "ffmpeg":
            if "-vn" not in command:
                inputs = [command[i + 1] for i, value in enumerate(command) if value == "-i"]
                muxed.append(Path(inputs[1]).read_bytes())
            Path(command[-1]).write_bytes(b"original audio")
        return subprocess.CompletedProcess(command, 0)

    fake_voicefixer(monkeypatch, unavailable)
    monkeypatch.setattr(subprocess, "run", process)
    manifests = [{"rank": 1, "raw_path": str(tmp_path / "raw.mp4")}]

    enhance_audio.enhance_all(manifests, tmp_path, Config(loudness_enabled=False))

    assert muxed == [b"original audio"]
