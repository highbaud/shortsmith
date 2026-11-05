"""Tests for normalize.py loudnorm filter construction + JSON parsing."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from shortsmith.config import Config
from shortsmith.normalize import (
    _parse_loudnorm_json,
    build_apply_filter,
    build_measure_filter,
    loudnorm_two_pass,
)

MEASURED = {
    "input_i": "-23.70",
    "input_tp": "-5.10",
    "input_lra": "8.20",
    "input_thresh": "-34.00",
    "target_offset": "0.42",
}


def test_measure_filter_uses_target_lufs():
    cfg = Config()
    cfg.loudness_target_lufs = -14.0
    f = build_measure_filter(cfg)
    assert "loudnorm=" in f
    assert "I=-14.0" in f
    assert "print_format=json" in f


def test_apply_filter_threads_measurements():
    cfg = Config()
    cfg.loudness_target_lufs = -14.0
    measured = {
        "input_i": "-23.7",
        "input_tp": "-5.1",
        "input_lra": "8.2",
        "input_thresh": "-34.0",
        "target_offset": "0.42",
    }
    f = build_apply_filter(cfg, measured)
    assert "measured_I=-23.7" in f
    assert "measured_TP=-5.1" in f
    assert "measured_LRA=8.2" in f
    assert "measured_thresh=-34.0" in f
    assert "offset=0.42" in f
    assert "linear=true" in f


def test_parse_loudnorm_json_extracts_trailing_object():
    stderr = (
        "ffmpeg version ...\n[Parsed_loudnorm_0 @ 0x] \n"
        '{\n  "input_i" : "-23.70",\n  "input_tp" : "-5.10",\n'
        '  "input_lra" : "8.20",\n  "input_thresh" : "-34.00",\n'
        '  "target_offset" : "0.42"\n}\n'
    )
    parsed = _parse_loudnorm_json(stderr)
    assert parsed is not None
    assert parsed["input_i"] == "-23.70"
    assert parsed["target_offset"] == "0.42"


def test_parse_loudnorm_json_returns_none_on_garbage():
    assert _parse_loudnorm_json("no json here at all") is None
    assert _parse_loudnorm_json("") is None


@pytest.fixture
def audio_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    source, output = tmp_path / "source.wav", tmp_path / "normalized.wav"
    source.write_bytes(b"original speech")
    output.write_bytes(b"previous output")
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    executable.write_bytes(b"mocked process boundary")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools))
    job = {"source": source, "output": output, "stderr": json.dumps(MEASURED),
           "measure_status": 0, "apply_status": 0, "write_output": True, "calls": []}

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        job["calls"].append(command)
        if len(job["calls"]) == 1:
            return subprocess.CompletedProcess(command, job["measure_status"], stdout="", stderr=job["stderr"])
        if job["write_output"]:
            output.write_bytes(b"normalized speech")
        return subprocess.CompletedProcess(command, job["apply_status"], stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return job


def test_failed_measurement_never_applies_even_with_valid_json(audio_job: dict) -> None:
    audio_job["measure_status"] = 1
    assert loudnorm_two_pass(audio_job["source"], audio_job["output"], Config()) is False
    assert len(audio_job["calls"]) == 1
    assert audio_job["output"].read_bytes() == b"original speech"


@pytest.mark.parametrize("missing", list(MEASURED))
def test_missing_measurement_field_copies_input_without_applying(
    missing: str, audio_job: dict,
) -> None:
    audio_job["stderr"] = json.dumps({key: value for key, value in MEASURED.items() if key != missing})
    assert loudnorm_two_pass(audio_job["source"], audio_job["output"], Config()) is False
    assert len(audio_job["calls"]) == 1
    assert audio_job["output"].read_bytes() == b"original speech"


@pytest.mark.parametrize("field", list(MEASURED))
def test_nonfinite_measurement_field_copies_input_without_applying(
    field: str, audio_job: dict,
) -> None:
    audio_job["stderr"] = json.dumps({**MEASURED, field: "-inf"})
    assert loudnorm_two_pass(audio_job["source"], audio_job["output"], Config()) is False
    assert len(audio_job["calls"]) == 1
    assert audio_job["output"].read_bytes() == b"original speech"


@pytest.mark.parametrize("invalid", [
    None, True, [], {}, "not numeric", "-18:unexpected_filter_option=1", "NaN", "Infinity",
])
def test_invalid_measurement_value_copies_input_without_applying(
    invalid: object, audio_job: dict,
) -> None:
    audio_job["stderr"] = json.dumps({**MEASURED, "input_i": invalid})
    assert loudnorm_two_pass(audio_job["source"], audio_job["output"], Config()) is False
    assert len(audio_job["calls"]) == 1
    assert audio_job["output"].read_bytes() == b"original speech"


def test_wrong_measurement_object_copies_input_without_applying(audio_job: dict) -> None:
    audio_job["stderr"] = '{"unrelated":1}'
    assert loudnorm_two_pass(audio_job["source"], audio_job["output"], Config()) is False
    assert len(audio_job["calls"]) == 1
    assert audio_job["output"].read_bytes() == b"original speech"


@pytest.mark.parametrize("raw", ["-18:unexpected_filter_option=1", "-inf", True])
def test_apply_filter_rejects_invalid_direct_measurements(raw: object) -> None:
    with pytest.raises(ValueError, match="measurements"):
        build_apply_filter(Config(), {**MEASURED, "input_i": raw})


@pytest.mark.parametrize("measured", [
    {"input_i": " -23.700 ", "input_tp": "-05.10", "input_lra": "8.200", "input_thresh": "-34.00", "target_offset": "+4.2e-1"},
    {"input_i": -23.7, "input_tp": -5.1, "input_lra": 8.2, "input_thresh": -34, "target_offset": 0.42},
])
def test_valid_measurements_reach_second_pass_as_canonical_numbers(measured: dict, audio_job: dict) -> None:
    audio_job["stderr"] = json.dumps(measured)
    cfg = Config(loudness_target_lufs=-14.0, loudness_true_peak=-1.5, loudness_range=11.0)
    assert loudnorm_two_pass(audio_job["source"], audio_job["output"], cfg) is True
    assert len(audio_job["calls"]) == 2
    command = audio_job["calls"][1]
    assert command[command.index("-af") + 1] == (
        "loudnorm=I=-14.0:TP=-1.5:LRA=11.0:measured_I=-23.7:measured_TP=-5.1:"
        "measured_LRA=8.2:measured_thresh=-34.0:offset=0.42:linear=true:print_format=summary"
    )
    assert audio_job["output"].read_bytes() == b"normalized speech"


@pytest.mark.parametrize("failure", ["failed process", "missing output"])
def test_second_pass_failure_keeps_copy_input_fallback(failure: str, audio_job: dict) -> None:
    if failure == "failed process":
        audio_job["apply_status"] = 1
    else:
        audio_job["write_output"] = False
        audio_job["output"].unlink()
    assert loudnorm_two_pass(audio_job["source"], audio_job["output"], Config()) is False
    assert len(audio_job["calls"]) == 2
    assert audio_job["output"].read_bytes() == b"original speech"
