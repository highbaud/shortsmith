"""Regression tests for pre-existing failure modes in the older helper modules.

Each test pins a behavior that used to raise or return the wrong thing:

* crash-recovery reads of a half-written `.progress.json`
* a caption word carrying neither a "text" nor a "word" key
* CLI arguments that used to fail deep inside a helper instead of at parse time
* a subprocess text capture whose stderr is undecodable in the Windows ANSI
  code page, which hands the caller `None` instead of a string
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from shortsmith import captions, checkpoint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_metricool_payloads  # noqa: E402
import build_sfx_index  # noqa: E402

EMPTY = {"steps": {}, "rendered": []}


# --------------------------------------------------------------------------- #
# checkpoint: a corrupt progress file means "nothing done yet", never a crash
# --------------------------------------------------------------------------- #

def _write_progress(work_dir: Path, payload: bytes) -> None:
    (work_dir / checkpoint.FILENAME).write_bytes(payload)


def test_load_survives_invalid_utf8(tmp_path: Path) -> None:
    # A crash part-way through a write can leave a truncated multi-byte char.
    _write_progress(tmp_path, b'{"steps": {"3": true}, "rendered": ["\xff\xfe"]}')
    assert checkpoint.load(tmp_path) == EMPTY


def test_load_survives_wrong_top_level_type(tmp_path: Path) -> None:
    _write_progress(tmp_path, b"[]")
    assert checkpoint.load(tmp_path) == EMPTY


def test_load_coerces_wrong_field_types(tmp_path: Path) -> None:
    _write_progress(tmp_path, b'{"steps": [], "rendered": "short-01"}')
    assert checkpoint.load(tmp_path) == EMPTY


def test_load_keeps_unknown_keys(tmp_path: Path) -> None:
    _write_progress(tmp_path, b'{"steps": {"3": true}, "rendered": [], "note": "keep me"}')
    data = checkpoint.load(tmp_path)
    assert data["note"] == "keep me"
    assert data["steps"] == {"3": True}


def test_mark_step_recovers_from_a_corrupt_file(tmp_path: Path) -> None:
    _write_progress(tmp_path, b'{"steps": {"3": tr')
    checkpoint.mark_step(tmp_path, 4)
    assert checkpoint.step_done(tmp_path, 4) is True
    assert checkpoint.step_done(tmp_path, 3) is False


def test_mark_rendered_recovers_from_a_corrupt_file(tmp_path: Path) -> None:
    _write_progress(tmp_path, b'{"rendered": "\xff not a list"}')
    checkpoint.mark_rendered(tmp_path, "short-01-hook")
    assert checkpoint.is_rendered(tmp_path, "short-01-hook") is True
    assert checkpoint.is_rendered(tmp_path, "short-02-hook") is False


# --------------------------------------------------------------------------- #
# captions: a word with no text key must not become a None caption
# --------------------------------------------------------------------------- #

def test_shift_words_to_zero_never_emits_none_text() -> None:
    shifted = captions.shift_words_to_zero([{"start": 4.0, "end": 4.5}], 3.5)
    assert shifted == [{"text": "", "start": 0.5, "end": 1.0}]


def test_shift_words_to_zero_accepts_either_key() -> None:
    shifted = captions.shift_words_to_zero(
        [{"word": "Hello", "start": 10.0, "end": 10.4}], 10.0
    )
    assert shifted == [{"text": "Hello", "start": 0.0, "end": 0.4}]


# --------------------------------------------------------------------------- #
# build_metricool_payloads: reject bad arguments before writing a payload file
# --------------------------------------------------------------------------- #

def _metricool_argv(tmp_path: Path, **overrides: str) -> list[str]:
    url_map = tmp_path / "urls.json"
    url_map.write_text('{"short-01.mp4": "https://example.invalid/a.mp4"}', encoding="utf-8")
    args = {
        "--all-dir": str(tmp_path),
        "--url-map": str(url_map),
        "--blog-id": "12345",
        "--start": "2026-01-01",
        "--out": str(tmp_path / "payloads.json"),
    }
    args.update(overrides)
    argv = ["build_metricool_payloads.py"]
    for k, v in args.items():
        argv += [k, v]
    return argv


def test_rejects_times_without_a_colon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", _metricool_argv(tmp_path, **{"--times": "9,13"}))
    with pytest.raises(SystemExit) as exc:
        build_metricool_payloads.main()
    assert exc.value.code != 0
    assert not (tmp_path / "payloads.json").exists()


def test_rejects_a_missing_all_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    argv = _metricool_argv(tmp_path, **{"--all-dir": str(tmp_path / "nope")})
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        build_metricool_payloads.main()
    assert exc.value.code != 0
    assert not (tmp_path / "payloads.json").exists()


def test_rejects_per_day_below_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", _metricool_argv(tmp_path, **{"--per-day": "0"}))
    with pytest.raises(SystemExit) as exc:
        build_metricool_payloads.main()
    assert exc.value.code != 0


# --------------------------------------------------------------------------- #
# build_sfx_index: ffmpeg stderr that the ANSI code page cannot decode
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_measure_levels_survives_undecodable_stderr(tmp_path: Path) -> None:
    # "Á" is C3 81 in UTF-8, and 0x81 is undefined in cp1252, so ffmpeg
    # echoing this path used to kill the stdout/stderr reader thread and leave
    # `stderr` set to None.
    missing = tmp_path / "Ángel Íñigo.wav"
    levels = build_sfx_index.measure_levels(missing)
    assert levels == {"peak_dbfs": None, "mean_dbfs": None}
