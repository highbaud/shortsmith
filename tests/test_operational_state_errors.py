"""Operational batches report invalid saved state while finishing valid projects."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import reprocess_all
from shortsmith import sfx
from shortsmith.config import Config
from shortsmith.rendering import add_sfx, finalize
from shortsmith.rendering.hyperframes import FONT_MARKER


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    work, auto, videos = tmp_path / "work", tmp_path / "kit" / "projects", tmp_path / "videos"
    work.mkdir()
    auto.mkdir(parents=True)
    videos.mkdir()
    sounds = tmp_path / "sounds"
    (sounds / "pack").mkdir(parents=True)
    (sounds / "pack" / "impact.wav").write_bytes(b"sound")
    (sounds / "pack" / "pack.json").write_text('{"hook-impact":["impact.wav"]}', encoding="utf-8")
    for name in ("bad", "good"):
        wd = work / name
        wd.mkdir()
        (wd / "clips.json").write_text(json.dumps([
            {"rank": 1, "slug": "current", "hook": {"text": "Hook"}}]), encoding="utf-8")
        (wd / "cut_manifests.json").write_text('[{"rank":1}]', encoding="utf-8")
        project = auto / name / "short-01-current"
        (project / "renders").mkdir(parents=True)
        (project / "renders" / "final.mp4").write_bytes(b"original base")
        (project / "index.html").write_text(f"<html>{FONT_MARKER}</html>", encoding="utf-8")
        (videos / f"{name}.mp4").write_bytes(b"source")
    for module in (add_sfx, finalize, reprocess_all):
        monkeypatch.setattr(module, "WORK_ROOT", work)
        monkeypatch.setattr(module, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(add_sfx, "SFX_DIR", sounds)
    monkeypatch.setattr(add_sfx, "KIT_RENDERS", tmp_path / "kit" / "renders")
    monkeypatch.setattr(finalize, "KIT_RENDERS", tmp_path / "kit" / "renders")
    monkeypatch.setattr(finalize, "ALL_DIR", tmp_path / "kit" / "renders" / "_all")
    monkeypatch.setattr(reprocess_all, "KIT_ROOT", tmp_path / "kit")
    monkeypatch.setattr(reprocess_all, "VIDEO_DIR", videos)
    monkeypatch.setattr(sys, "argv", ["batch.py"])
    tools = tmp_path / "tools"
    tools.mkdir()
    for tool in ("ffmpeg", "ffprobe", "uv", "node"):
        executable = tools / (tool + ".exe" if os.name == "nt" else tool)
        executable.write_bytes(b"mocked process boundary")
        executable.chmod(0o755)
    (tools / "npx.cmd").write_text("discovery only", encoding="ascii")
    npx_cli = tools / "node_modules" / "npm" / "bin" / "npx-cli.js"
    npx_cli.parent.mkdir(parents=True)
    npx_cli.write_text("mocked process boundary", encoding="ascii")
    monkeypatch.setenv("PATH", str(tools))
    calls = []

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        calls.append(command)
        tool = Path(command[0]).stem.lower()
        if tool == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="5.0\n")
        if tool == "ffmpeg":
            Path(command[-1]).write_bytes(b"new mix")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return {"root": tmp_path, "work": work, "auto": auto, "videos": videos,
            "sounds": sounds, "calls": calls}


def _bad_state(library: dict, kind: str) -> None:
    wd = library["work"] / "bad"
    if kind == "malformed clips":
        (wd / "clips.json").write_text("{broken", encoding="utf-8")
    elif kind == "wrong clips shape":
        (wd / "clips.json").write_text("{}", encoding="utf-8")
    elif kind == "malformed manifests":
        (wd / "cut_manifests.json").write_text("{broken", encoding="utf-8")
    elif kind == "wrong manifests shape":
        (wd / "cut_manifests.json").write_text("[null]", encoding="utf-8")
    elif kind == "missing clips":
        (wd / "clips.json").unlink()
    elif kind == "missing manifests":
        (wd / "cut_manifests.json").unlink()
    else:
        words = wd / "words.json"
        (wd / "cut_manifests.json").write_text(json.dumps([
            {"rank": 1, "words_path": str(words)}]), encoding="utf-8")
        if kind == "malformed words":
            words.write_text("{broken", encoding="utf-8")
        elif kind == "wrong words shape":
            words.write_text("{}", encoding="utf-8")


@pytest.mark.parametrize("workflow", ["add_sfx", "finalize"])
@pytest.mark.parametrize("kind", [
    "malformed clips", "wrong clips shape", "malformed manifests", "wrong manifests shape",
    "missing clips", "missing manifests", "missing words", "malformed words", "wrong words shape",
])
def test_sound_batches_report_bad_state_and_finish_valid_projects(
    workflow: str, kind: str, library: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    _bad_state(library, kind)
    if workflow == "add_sfx":
        assert add_sfx.main() == 1
    else:
        with pytest.raises(RuntimeError, match="SFX state failed for"):
            finalize.phase1_sfx(Config(), sfx.load_sfx_map(library["sounds"]))
    bad = library["auto"] / "bad" / "short-01-current" / "renders"
    good = library["auto"] / "good" / "short-01-current" / "renders"
    assert (bad / "final.mp4").read_bytes() == b"original base"
    assert not (bad / "final_sfx.mp4").exists()
    assert (good / "final_sfx.mp4").read_bytes() == b"new mix"
    assert str(library["work"] / "bad") in caplog.text


@pytest.mark.parametrize("workflow", ["add_sfx", "finalize"])
def test_empty_picks_and_unrelated_directories_remain_successful_no_ops(
    workflow: str, library: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    wd = library["work"] / "bad"
    (wd / "clips.json").write_text("[]", encoding="utf-8")
    (wd / "cut_manifests.json").unlink()
    (library["work"] / "unrelated").mkdir()
    if workflow == "add_sfx":
        assert add_sfx.main() == 0
    else:
        assert finalize.phase1_sfx(Config(), sfx.load_sfx_map(library["sounds"])) == 1
    assert "Cannot load" not in caplog.text
    assert not (library["auto"] / "bad" / "short-01-current" / "renders" / "final_sfx.mp4").exists()


@pytest.mark.parametrize("kind", ["malformed clips", "wrong clips shape", "missing clips", "missing source"])
def test_reprocessing_reports_bad_state_and_finishes_valid_project(
    kind: str, library: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    if kind == "missing source":
        (library["videos"] / "bad.mp4").unlink()
    else:
        _bad_state(library, kind)
    assert reprocess_all.main() == 1
    assert (library["work"] / "good" / reprocess_all.MARKER).is_file()
    assert not (library["work"] / "bad" / reprocess_all.MARKER).exists()
    assert str(library["work"] / "bad") in caplog.text


def test_completed_project_is_skipped_until_force_is_requested(library: dict) -> None:
    _bad_state(library, "malformed clips")
    (library["work"] / "bad" / reprocess_all.MARKER).write_text("complete", encoding="utf-8")
    assert reprocess_all.main() == 0
    assert (library["work"] / "bad" / reprocess_all.MARKER).read_text(encoding="utf-8") == "complete"


@pytest.mark.parametrize("workflow", ["add_sfx", "finalize"])
def test_unreadable_render_duration_fails_the_clip_instead_of_stacking_sfx(
    workflow: str, library: dict, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    passthrough = subprocess.run

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        if Path(command[0]).stem.lower() == "ffprobe" and "bad" in command[-1]:
            raise subprocess.CalledProcessError(1, command)
        return passthrough(command, **options)

    monkeypatch.setattr(subprocess, "run", run)
    if workflow == "add_sfx":
        assert add_sfx.main() == 1
    else:
        with pytest.raises(RuntimeError):
            finalize.phase1_sfx(Config(), sfx.load_sfx_map(library["sounds"]))
    bad = library["auto"] / "bad" / "short-01-current" / "renders"
    good = library["auto"] / "good" / "short-01-current" / "renders"
    assert not (bad / "final_sfx.mp4").exists()
    assert (good / "final_sfx.mp4").read_bytes() == b"new mix"
    assert "Cannot read the duration" in caplog.text
