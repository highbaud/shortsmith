"""Duration probes keep their strict errors and script-specific fallbacks."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from shortsmith import _media
from shortsmith.rendering import project_inputs


@pytest.mark.parametrize("probe", [_media.probe_duration, project_inputs.probe_duration])
def test_duration_probe_reads_seconds_with_utf8_paths(
    probe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "caf\u00e9 clip.mp4"

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        assert Path(command[0]).stem.lower() == "ffprobe"
        assert command[1:] == [
            "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ]
        assert options["check"] is True
        assert options["capture_output"] is True
        assert options["text"] is True
        assert options["encoding"] == "utf-8"
        return subprocess.CompletedProcess(command, 0, stdout=" 12.25\n")

    monkeypatch.setattr(subprocess, "run", run)
    assert probe(path) == 12.25


@pytest.mark.parametrize("probe", [_media.probe_duration, project_inputs.probe_duration])
@pytest.mark.parametrize("failure", ["missing executable", "failed process", "bad output"])
def test_strict_duration_probes_propagate_errors(
    probe, failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        if failure == "missing executable":
            raise FileNotFoundError("missing ffprobe")
        if failure == "failed process":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="not a duration")

    monkeypatch.setattr(subprocess, "run", run)
    error = {
        "missing executable": FileNotFoundError,
        "failed process": subprocess.CalledProcessError,
        "bad output": ValueError,
    }[failure]
    with pytest.raises(error):
        probe(tmp_path / "clip.mp4")
