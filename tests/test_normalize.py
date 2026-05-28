"""Tests for normalize.py loudnorm filter construction + JSON parsing."""
from __future__ import annotations

from shortsmith.config import Config
from shortsmith.normalize import (
    _parse_loudnorm_json,
    build_apply_filter,
    build_measure_filter,
)


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
