"""Render entry points pass project paths as literal command arguments."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from shortsmith import config
from shortsmith.rendering import render_remotion
from shortsmith.rendering.hyperframes import FONT_MARKER, render_project


@pytest.fixture
def npm_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    prefix = tmp_path / "npm tools & files"
    npx_cli = prefix / "node_modules" / "npm" / "bin" / "npx-cli.js"
    npx_cli.parent.mkdir(parents=True)
    npx_cli.write_text("", encoding="utf-8")
    (prefix / "npx.cmd").write_text("", encoding="utf-8")
    node = prefix / "node.exe"
    node.write_bytes(b"")
    (prefix / "ffprobe.exe").write_bytes(b"")
    fallback = tmp_path / "path node tools"
    fallback.mkdir()
    (fallback / "node.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", os.pathsep.join((str(prefix), str(fallback))))
    monkeypatch.setattr(config, "HYPERFRAMES_SPEC", "hyperframes@0.9.16")
    return node, npx_cli


@pytest.mark.parametrize("returncode", [0, 1])
def test_render_preserves_literal_project_path_and_exit_status(
    returncode: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    npm_install: tuple[Path, Path],
) -> None:
    kit = tmp_path / "kit with spaces"
    project = kit / "video-projects" / "source & %PATH%" / "short-01-clip & name"
    project.mkdir(parents=True)
    (project / "index.html").write_text(f"<html>{FONT_MARKER}</html>", encoding="utf-8")
    calls = []

    def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        if returncode == 0:
            (project / "renders").mkdir(exist_ok=True)
            (project / "renders" / "final.mp4").write_bytes(b"render")
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="render error")

    monkeypatch.setattr(subprocess, "run", run)

    assert render_project(project, kit, logging.getLogger("render_commands")) is (returncode == 0)
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        *(str(path) for path in npm_install),
        "hyperframes@0.9.16", "render",
        "video-projects/source & %PATH%/short-01-clip & name",
    ]
    assert options.get("shell", False) is False
    assert options["cwd"] == str(kit)
    assert options["capture_output"] is True
    assert options["text"] is True
    assert options["encoding"] == "utf-8"


def test_windows_command_uses_node_from_path_when_npm_has_no_node(
    npm_install: tuple[Path, Path], tmp_path: Path,
) -> None:
    node, npx_cli = npm_install
    node.unlink()
    assert config.hyperframes_cmd("render", "a clip") == [
        str(tmp_path / "path node tools" / "node.exe"), str(npx_cli), "hyperframes@0.9.16", "render", "a clip",
    ]


@pytest.mark.parametrize("missing", ["npx", "node", "cli"])
def test_windows_command_reports_missing_tools(
    missing: str, npm_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    node, npx_cli = npm_install
    if missing == "npx":
        (node.parent / "npx.cmd").unlink()
        expected = "npx.cmd"
    elif missing == "node":
        node.unlink()
        monkeypatch.setenv("PATH", str(node.parent))
        expected = "node.exe"
    else:
        npx_cli.unlink()
        expected = "npx CLI"
    with pytest.raises(FileNotFoundError, match=expected):
        config.hyperframes_cmd("render", "a clip")


def test_unix_command_keeps_npx_and_literal_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(config, "HYPERFRAMES_SPEC", "hyperframes@0.9.16")
    assert config.hyperframes_cmd("render", "a clip & $HOME") == [
        "npx", "hyperframes@0.9.16", "render", "a clip & $HOME",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command dispatch")
@pytest.mark.parametrize("package", ["hyperframes@0.9.16", "remotion"])
def test_windows_node_receives_percent_and_ampersand_arguments_unchanged(
    package: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = shutil.which("node.exe")
    if node is None:
        pytest.skip("Node runtime is not installed")
    prefix = tmp_path / "npm tools & files"
    npx_cli = prefix / "node_modules" / "npm" / "bin" / "npx-cli.js"
    npx_cli.parent.mkdir(parents=True)
    npx_cli.write_text("process.stdout.write(JSON.stringify(process.argv.slice(2)));", encoding="utf-8")
    (prefix / "npx.cmd").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", os.pathsep.join((str(prefix), str(Path(node).parent))))
    monkeypatch.setenv("RENDER_COMMAND_TEST", "must not expand")
    argument = "video-projects/source & %RENDER_COMMAND_TEST%/clip"
    command = config.npx_cmd(package, "render", argument)
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [package, "render", argument]


@pytest.fixture
def remotion_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "short-01-spaces & %PATH%"
    (project / "assets").mkdir(parents=True)
    (project / "renders").mkdir()
    (project / "assets" / "words.json").write_text("[]", encoding="utf-8")
    (project / "renders" / "final.mp4").write_bytes(b"base")
    monkeypatch.setenv("SHORTSMITH_BROLL_SOURCES", "off")
    monkeypatch.setattr(config, "AUTO_SHORTS_ROOT", tmp_path / "kit" / "video-projects" / "auto-shorts")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return project


@pytest.mark.parametrize("open_after", [False, True])
def test_remotion_passes_literal_paths_and_opens_only_when_requested(
    remotion_project: Path, npm_install: tuple[Path, Path],
    open_after: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    opened = []
    output = remotion_project / "renders" / "result & %PATH%.mp4"

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        if Path(command[0]).stem.lower() == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="5.0\n")
        calls.append((command, options))
        output.write_bytes(b"render")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(render_remotion.os, "startfile", opened.append, raising=False)
    result = render_remotion.render(
        remotion_project, captions=False, platform="generic", base_mode="hyperframes",
        broll_arg=None, output=output.name, style="minimal", open_after=open_after,
    )
    assert result == output
    assert len(calls) == 1
    command, options = calls[0]
    assert command[:7] == [
        *(str(path) for path in npm_install),
        "remotion", "render", "src/index.ts", "Short", str(output),
    ]
    assert command[-1] == f"--public-dir={remotion_project}"
    assert options.get("shell", False) is False
    assert options["check"] is True
    assert options["cwd"] == render_remotion.REMOTION_DIR
    props = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--props=")))
    assert not props.exists()
    assert opened == ([output] if open_after else [])


@pytest.mark.parametrize("failure", ["missing npm", "render failed"])
def test_remotion_cleans_props_and_does_not_open_when_command_fails(
    remotion_project: Path, npm_install: tuple[Path, Path], failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = []
    if failure == "missing npm":
        npm_install[1].unlink()

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        if Path(command[0]).stem.lower() == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="5.0\n")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(render_remotion.os, "startfile", opened.append, raising=False)
    error = FileNotFoundError if failure == "missing npm" else subprocess.CalledProcessError
    with pytest.raises(error):
        render_remotion.render(
            remotion_project, captions=False, platform="generic", base_mode="hyperframes",
            broll_arg=None, output="result.mp4", style="minimal", open_after=True,
        )
    assert opened == []
    assert list(remotion_project.parent.glob("*.json")) == []


def test_remotion_opens_a_file_uri_on_unix(
    remotion_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = remotion_project / "renders" / "result & %PATH%.mp4"
    opened = []

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        if Path(command[0]).stem.lower() == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="5.0\n")
        assert command[:3] == ["npx", "remotion", "render"]
        output.write_bytes(b"render")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(render_remotion.webbrowser, "open", opened.append)
    result = render_remotion.render(
        remotion_project, captions=False, platform="generic", base_mode="hyperframes",
        broll_arg=None, output=output.name, style="minimal", open_after=True,
    )
    assert result == output
    assert opened == [output.as_uri()]


def test_remotion_input_error_leaves_no_staged_base(
    remotion_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    kit_base = tmp_path / "kit-render.mp4"
    kit_base.write_bytes(b"kit base")

    def bad_overlays(*_args) -> list:
        raise ValueError("malformed overlays")

    monkeypatch.setattr(render_remotion.project_inputs, "pick_base", lambda *_args: kit_base)
    monkeypatch.setattr(render_remotion.project_inputs, "probe_duration", lambda _path: 5.0)
    monkeypatch.setattr(render_remotion.project_inputs, "overlay_windows", bad_overlays)
    with pytest.raises(ValueError, match="malformed overlays"):
        render_remotion.render(
            remotion_project, captions=False, platform="generic", base_mode="hyperframes",
            broll_arg=None, output="result.mp4", style="minimal", open_after=False,
        )
    assert list((remotion_project / "renders").glob("_base*")) == []
