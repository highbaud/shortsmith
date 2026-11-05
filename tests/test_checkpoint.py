"""Checkpoint writes keep earlier progress when the filesystem fails."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from shortsmith import checkpoint


def test_marks_preserve_progress_unknown_fields_and_unique_rendered_slugs(tmp_path: Path) -> None:
    target = tmp_path / checkpoint.FILENAME
    target.write_text(
        '{"steps": {"3": true}, "rendered": ["short-01"], "note": "keep me"}',
        encoding="utf-8",
    )

    checkpoint.mark_step(tmp_path, 4)
    checkpoint.mark_rendered(tmp_path, "short-02")
    checkpoint.mark_rendered(tmp_path, "short-02")

    assert checkpoint.load(tmp_path) == {
        "steps": {"3": True, "4": True},
        "rendered": ["short-01", "short-02"],
        "note": "keep me",
    }
    assert checkpoint.step_done(tmp_path, 4)
    assert checkpoint.is_rendered(tmp_path, "short-02")
    assert list(tmp_path.iterdir()) == [target]


def test_clear_step_preserves_other_progress_and_unknown_fields(tmp_path: Path) -> None:
    target = tmp_path / checkpoint.FILENAME
    target.write_text(
        '{"steps": {"5": true, "6": true}, "rendered": ["short-01"], "note": "keep me"}',
        encoding="utf-8",
    )

    checkpoint.clear_step(tmp_path, 6)
    checkpoint.clear_step(tmp_path, 6)

    assert checkpoint.load(tmp_path) == {
        "steps": {"5": True}, "rendered": ["short-01"], "note": "keep me",
    }
    assert not checkpoint.step_done(tmp_path, 6)


@pytest.mark.parametrize(("mark", "value"), [
    (checkpoint.mark_step, 4),
    (checkpoint.mark_rendered, "short-02"),
])
def test_failed_replacement_keeps_progress_and_logs_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mark: Callable[..., None],
    value: int | str,
) -> None:
    previous = {"steps": {"3": True}, "rendered": ["short-01"], "note": "keep me"}
    target = tmp_path / checkpoint.FILENAME
    target.write_text(json.dumps(previous), encoding="utf-8")
    contents = target.read_bytes()

    def fail_replace(source: str, destination: str) -> None:
        assert Path(source).parent == Path(destination).parent == tmp_path
        raise PermissionError("checkpoint locked")

    monkeypatch.setattr("os.replace", fail_replace)
    mark(tmp_path, value)

    assert target.read_bytes() == contents
    assert checkpoint.load(tmp_path) == previous
    assert list(tmp_path.iterdir()) == [target]
    assert "checkpoint locked" in caplog.text
    assert str(target) in caplog.text


def test_cannot_create_temporary_checkpoint_is_nonfatal_and_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_open(*args, **kwargs) -> None:
        raise OSError("disk is full")

    monkeypatch.setattr("os.open", fail_open)
    checkpoint.mark_step(tmp_path, 3)

    assert checkpoint.load(tmp_path) == {"steps": {}, "rendered": []}
    assert list(tmp_path.iterdir()) == []
    assert "disk is full" in caplog.text
