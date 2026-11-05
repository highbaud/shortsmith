"""Atomic metadata writes preserve prior files across filesystem failures."""
from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shortsmith._files import atomic_write


@pytest.mark.parametrize(("content", "expected"), [
    ("caf\u00e9\nnext\r\n", b"caf\xc3\xa9\r\nnext\r\r\n" if os.name == "nt"
     else b"caf\xc3\xa9\nnext\r\n"),
    (b"\x00\xff\n\r\n", b"\x00\xff\n\r\n"),
])
def test_atomic_write_preserves_utf8_newlines_and_bytes(
    tmp_path: Path, content: str | bytes, expected: bytes,
) -> None:
    target = tmp_path / "metadata.json"
    target.write_bytes(b"previous")

    atomic_write(target, content)

    assert target.read_bytes() == expected
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("content", ["new metadata", b"new metadata"])
def test_failed_replace_keeps_previous_file_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | bytes,
) -> None:
    target = tmp_path / "metadata.json"
    target.write_bytes(b"previous")

    def fail_replace(source: str, destination: str) -> None:
        assert Path(source).parent == Path(destination).parent == tmp_path
        assert Path(source).read_bytes() == b"new metadata"
        raise OSError("replace failed")

    monkeypatch.setattr("os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write(target, content)

    assert target.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("content", ["new metadata", b"new metadata"])
def test_partial_write_failure_keeps_previous_file_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | bytes,
) -> None:
    target = tmp_path / "metadata.json"
    target.write_bytes(b"previous")
    real_open = io.open

    def open_failing_stream(*args, **kwargs) -> MagicMock:
        stream = real_open(*args, **kwargs)
        wrapped = MagicMock(wraps=stream)
        wrapped.__enter__.side_effect = stream.__enter__
        wrapped.__exit__.side_effect = stream.__exit__

        def fail_write(data: str | bytes) -> None:
            stream.write(data[:3])
            raise OSError("write failed")

        wrapped.write.side_effect = fail_write
        return wrapped

    with monkeypatch.context() as filesystem:
        filesystem.setattr("io.open", open_failing_stream)
        with pytest.raises(OSError, match="write failed"):
            atomic_write(target, content)

    assert target.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_does_not_create_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "metadata.json"

    with pytest.raises(FileNotFoundError):
        atomic_write(target, "new metadata")

    assert not target.parent.exists()
