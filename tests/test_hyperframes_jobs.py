"""All HyperFrames batch commands share scaffold, process, and output checks."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import redo_existing
from shortsmith import config
from shortsmith.rendering.hyperframes import FONT_MARKER, render_project

log = logging.getLogger("test_hyperframes_job")


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    kit = tmp_path / "kit with spaces"
    project = kit / "video-projects" / "source & %PATH%" / "short-01-clip & name"
    project.mkdir(parents=True)
    (project / "index.html").write_text(f"<html>{FONT_MARKER}</html>", encoding="utf-8")
    tools = tmp_path / "native tools"
    tools.mkdir()
    node = tools / "node.exe"
    node.write_bytes(b"mocked process boundary")
    (tools / "uv.exe").write_bytes(b"mocked process boundary")
    (tools / "npx.cmd").write_text("discovery only", encoding="ascii")
    cli = tools / "node_modules" / "npm" / "bin" / "npx-cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("mocked process boundary", encoding="ascii")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(config, "HYPERFRAMES_SPEC", "hyperframes@0.9.16")
    state = {"root": tmp_path, "kit": kit, "project": project, "node": node, "cli": cli,
             "output": None, "returncode": 0, "error": None, "calls": []}

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        state["calls"].append((command, options))
        if state["error"] is not None:
            raise state["error"]
        if "render" in command and state["output"] is not None:
            state["output"].parent.mkdir(parents=True, exist_ok=True)
            state["output"].write_bytes(b"base render")
        return subprocess.CompletedProcess(command, state["returncode"], stdout="", stderr="renderer detail")

    monkeypatch.setattr(subprocess, "run", run)
    return state


@pytest.mark.parametrize("layout", ["project", "kit"])
def test_shared_job_uses_literal_pinned_command_and_both_output_layouts(
    layout: str, job: dict,
) -> None:
    project, kit = job["project"], job["kit"]
    job["output"] = (project / "renders" / "final.mp4" if layout == "project"
                     else kit / "renders" / f"{project.name}_20261103.mp4")
    assert render_project(project, kit, log) is True
    assert len(job["calls"]) == 1
    command, options = job["calls"][0]
    assert command == [str(job["node"]), str(job["cli"]), "hyperframes@0.9.16", "render",
                       "video-projects/source & %PATH%/short-01-clip & name"]
    assert options == {"cwd": str(kit), "capture_output": True, "text": True, "encoding": "utf-8"}


@pytest.mark.parametrize(("spec", "rendered"), [("hyperframes@0.9.16", False),
                                                 ("hyperframes@0.7.71", True)])
def test_pre_09_template_is_refused_only_by_newer_hyperframes(
    spec: str, rendered: bool, job: dict, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project = job["project"]
    (project / "index.html").write_text("<html>old template</html>", encoding="utf-8")
    monkeypatch.setattr(config, "HYPERFRAMES_SPEC", spec)
    job["output"] = project / "renders" / "final.mp4"

    assert render_project(project, job["kit"], log) is rendered
    assert len(job["calls"]) == (1 if rendered else 0)
    assert ("Scaffold it again" in caplog.text) is not rendered


@pytest.mark.parametrize("kind", ["missing", "empty", "derived only", "unrelated only"])
def test_successful_process_without_nonempty_base_output_is_a_failure(
    kind: str, job: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    project = job["project"]
    renders = project / "renders"
    renders.mkdir()
    if kind == "empty":
        (renders / "final.mp4").write_bytes(b"")
    elif kind == "derived only":
        for name in ("final_sfx.mp4", "final_remotion.mp4", "_base.mp4", "_sfx_tmp.mp4"):
            (renders / name).write_bytes(b"derived output")
    elif kind == "unrelated only":
        (job["kit"] / "renders").mkdir()
        (job["kit"] / "renders" / "other-project_20261103.mp4").write_bytes(b"unrelated output")
    assert render_project(project, job["kit"], log) is False
    assert len(job["calls"]) == 1
    assert "no nonempty output" in caplog.text


@pytest.mark.parametrize("layout", ["project", "kit"])
def test_existing_valid_output_stays_valid_after_idempotent_success(layout: str, job: dict) -> None:
    output = (job["project"] / "renders" / "final.mp4" if layout == "project"
              else job["kit"] / "renders" / f"{job['project'].name}_20261103.mp4")
    output.parent.mkdir(parents=True)
    output.write_bytes(b"existing render")
    assert render_project(job["project"], job["kit"], log) is True
    assert output.read_bytes() == b"existing render"


@pytest.mark.parametrize("kind", ["missing", "caption sidecar", "missing index", "index directory", "outside kit"])
def test_invalid_project_is_rejected_before_process_launch(
    kind: str, job: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    project = job["project"]
    if kind == "missing":
        project = project.parent / "missing"
    elif kind == "caption sidecar":
        project = project.parent / "short-01-caption.txt"
        project.write_text("caption", encoding="utf-8")
    elif kind == "missing index":
        (project / "index.html").unlink()
    elif kind == "index directory":
        (project / "index.html").unlink()
        (project / "index.html").mkdir()
    else:
        project = job["root"] / "outside project"
        project.mkdir()
        (project / "index.html").write_text(f"<html>{FONT_MARKER}</html>", encoding="utf-8")
    assert render_project(project, job["kit"], log) is False
    assert job["calls"] == []
    assert str(project) in caplog.text


def test_failed_process_is_not_hidden_by_an_existing_output(job: dict, caplog: pytest.LogCaptureFixture) -> None:
    output = job["project"] / "renders" / "final.mp4"
    output.parent.mkdir()
    output.write_bytes(b"old render")
    job["returncode"] = 1
    assert render_project(job["project"], job["kit"], log) is False
    assert "exit=1" in caplog.text and "renderer detail" in caplog.text
    assert output.read_bytes() == b"old render"


def test_process_launch_failure_is_logged(job: dict, caplog: pytest.LogCaptureFixture) -> None:
    job["error"] = OSError("cannot start renderer")
    assert render_project(job["project"], job["kit"], log) is False
    assert "cannot start renderer" in caplog.text


def test_missing_renderer_tool_is_logged_without_a_launch(
    job: dict, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("PATH", "")
    assert render_project(job["project"], job["kit"], log) is False
    assert job["calls"] == []
    assert "npx.cmd" in caplog.text


@pytest.mark.parametrize("has_output", [False, True])
def test_redo_existing_main_reports_render_result(
    has_output: bool, job: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = job["root"] / "work"
    source_work = work / "source"
    source_work.mkdir(parents=True)
    (source_work / "clips.json").write_text('[{"rank":1}]', encoding="utf-8")
    (source_work / "cut_manifests.json").write_text('[{"rank":1}]', encoding="utf-8")
    videos = job["root"] / "videos"
    videos.mkdir()
    (videos / "source.mp4").write_bytes(b"source")
    auto = job["kit"] / "projects"
    project = auto / "source" / "short-01-current"
    project.mkdir(parents=True)
    (project / "index.html").write_text(f"<html>{FONT_MARKER}</html>", encoding="utf-8")
    if has_output:
        job["output"] = project / "renders" / "final.mp4"
    monkeypatch.setattr(redo_existing, "WORK_ROOT", work)
    monkeypatch.setattr(redo_existing, "VIDEO_DIR", videos)
    monkeypatch.setattr(redo_existing, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(redo_existing, "KIT_ROOT", job["kit"])
    assert redo_existing.main() == (0 if has_output else 1)
