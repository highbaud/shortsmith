"""Windows tool lookup must not execute a program from the current directory."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from shortsmith._process import find_windows_path_file, resolve_executable


def test_windows_tool_ignores_current_and_relative_path_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = tmp_path / "untrusted"
    relative = current / "relative"
    trusted = tmp_path / "installed tools"
    relative.mkdir(parents=True)
    trusted.mkdir()
    for directory in (current, relative, trusted):
        (directory / "ffmpeg.exe").write_bytes(b"must not run")
    monkeypatch.chdir(current)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", os.pathsep.join(("", ".", "relative", str(current), str(trusted))))
    assert resolve_executable("ffmpeg") == str(trusted / "ffmpeg.exe")


@pytest.mark.parametrize("entry", ["", ".", "relative", "absolute current directory"])
def test_windows_tool_cannot_come_from_empty_relative_or_current_path(
    entry: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = tmp_path / "untrusted"
    relative = current / "relative"
    relative.mkdir(parents=True)
    (current / "ffmpeg.exe").write_bytes(b"must not run")
    (relative / "ffmpeg.exe").write_bytes(b"must not run")
    monkeypatch.chdir(current)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(current) if entry == "absolute current directory" else entry)
    with pytest.raises(FileNotFoundError, match="absolute PATH directories"):
        resolve_executable("ffmpeg")


def test_windows_tool_does_not_fall_back_to_batch_or_powershell_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    for suffix in (".cmd", ".bat", ".ps1"):
        (tools / f"ffmpeg{suffix}").write_text("must not run", encoding="ascii")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(tools))
    with pytest.raises(FileNotFoundError):
        resolve_executable("ffmpeg")


@pytest.mark.parametrize("name", ["tool.cmd", "tool.bat", "tool.ps1", "../tool.exe", "folder\\tool.exe"])
def test_windows_tool_rejects_script_names_and_directory_components(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ValueError):
        resolve_executable(name)


def test_windows_npx_shim_discovery_uses_only_explicit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = tmp_path / "untrusted"
    trusted = tmp_path / "node installation"
    current.mkdir()
    trusted.mkdir()
    (current / "npx.cmd").write_text("must not run", encoding="ascii")
    (trusted / "npx.cmd").write_text("discovery only", encoding="ascii")
    monkeypatch.chdir(current)
    monkeypatch.setenv("PATH", str(trusted))
    assert find_windows_path_file("npx.cmd") == trusted / "npx.cmd"


def test_unix_command_keeps_normal_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PATH", "")
    assert resolve_executable("ffmpeg") == "ffmpeg"
