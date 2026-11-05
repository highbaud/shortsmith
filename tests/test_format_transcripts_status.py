"""Transcript formatting reports failed items while keeping successful outputs."""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import format_transcripts


@pytest.mark.parametrize("failure", ["invalid json", "bad word", "cannot write"])
def test_mixed_directories_keep_valid_output_and_return_failure(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(format_transcripts, "WORK_ROOT", tmp_path)
    words = [{"text": "One", "start": 0.0, "end": 0.5},
             {"text": "two", "start": 0.5, "end": 1.0}]
    for name in ("a-failed", "b-valid", "c-reused", "d-empty"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "transcript.json").write_text(json.dumps(words), encoding="utf-8")
    failed = tmp_path / "a-failed"
    if failure == "invalid json":
        (failed / "transcript.json").write_text("{broken", encoding="utf-8")
    elif failure == "bad word":
        (failed / "transcript.json").write_text("[{}]", encoding="utf-8")
    else:
        original_replace = Path.replace

        def replace(path: Path, target: Path) -> Path:
            if target == failed / "transcript.formatted.txt":
                raise OSError("cannot write formatted transcript")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", replace)
    reused = tmp_path / "c-reused" / "transcript.formatted.txt"
    prior_output = "Existing formatted transcript. " * 4
    reused.write_text(prior_output, encoding="utf-8")
    (tmp_path / "d-empty" / "transcript.json").write_text("[]", encoding="utf-8")
    (tmp_path / "unrelated").mkdir()

    assert format_transcripts.main() == 1
    assert (tmp_path / "b-valid" / "transcript.formatted.txt").read_text(encoding="utf-8") == (
        "\n[t=0s]\nOne two"
    )
    assert not (failed / "transcript.formatted.txt").exists()
    assert reused.read_text(encoding="utf-8") == prior_output
    assert not (tmp_path / "d-empty" / "transcript.formatted.txt").exists()
    output = capsys.readouterr().out
    assert "FAILED a-failed:" in output
    assert "[1] b-valid:" in output
    assert "Formatted 1 transcripts." in output


def test_successful_formatting_and_empty_rerun_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(format_transcripts, "WORK_ROOT", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "transcript.json").write_text(
        '[{"text":"Saved words","start":0,"end":1}]', encoding="utf-8",
    )
    assert format_transcripts.main() == 0
    assert (source / "transcript.formatted.txt").is_file()
    (source / "transcript.json").write_text("[]", encoding="utf-8")
    assert format_transcripts.main() == 0


@pytest.mark.parametrize("previous", [None, "prior short text"])
def test_partial_write_keeps_previous_output_and_can_retry(
    previous: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(format_transcripts, "WORK_ROOT", tmp_path)
    words = [{"text": "spoken", "start": index / 5, "end": index / 5 + 0.1}
             for index in range(30)]
    (source / "transcript.json").write_text(json.dumps(words), encoding="utf-8")
    target = source / "transcript.formatted.txt"
    if previous is not None:
        target.write_text(previous, encoding="utf-8")
    expected = format_transcripts.format_one(words)
    assert len(expected) > 101  # The existing cache rule reuses files larger than 100 bytes.
    real_open = io.open

    def partial_stream(file, mode="r", *args, **kwargs):
        stream = real_open(file, mode, *args, **kwargs)
        if "w" not in mode:
            return stream
        wrapped = MagicMock(wraps=stream)
        wrapped.__enter__.return_value = wrapped
        wrapped.__exit__.side_effect = stream.__exit__

        def write(data: str) -> None:
            stream.write(data[:101])
            stream.flush()
            raise OSError("partial formatted write")

        wrapped.write.side_effect = write
        return wrapped

    with monkeypatch.context() as filesystem:
        filesystem.setattr(io, "open", partial_stream)
        assert format_transcripts.main() == 1
    if previous is None:
        assert not target.exists()
    else:
        assert target.read_text(encoding="utf-8") == previous
    assert list(source.glob("*.tmp")) == []
    assert format_transcripts.main() == 0
    assert target.read_text(encoding="utf-8") == expected


def test_saved_manual_text_is_kept_even_when_transcript_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(format_transcripts, "WORK_ROOT", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "transcript.json").write_text("{broken", encoding="utf-8")
    target = source / "transcript.formatted.txt"
    manual = "Keep the user's saved text. " * 4
    target.write_text(manual, encoding="utf-8")
    assert target.stat().st_size > 100
    assert format_transcripts.main() == 0
    assert target.read_text(encoding="utf-8") == manual
