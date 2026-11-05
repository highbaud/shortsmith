"""Batch retries retain failed work and only complete after selected renders pass."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scripts import batch_pipeline, redo_existing, redo_outdated, render_all, reprocess_all
from shortsmith.rendering.hyperframes import FONT_MARKER


@pytest.fixture
def batch_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    work, videos, kit = tmp_path / "work", tmp_path / "videos", tmp_path / "kit"
    work.mkdir()
    videos.mkdir()
    auto = kit / "projects"
    auto.mkdir(parents=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("uv.exe", "node.exe", "npx.cmd"):
        (tools / name).write_bytes(b"mocked process boundary")
    cli = tools / "node_modules" / "npm" / "bin" / "npx-cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("mocked process boundary", encoding="ascii")
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(sys, "argv", ["batch.py"])
    for module in (batch_pipeline, redo_existing, redo_outdated, reprocess_all):
        monkeypatch.setattr(module, "WORK_ROOT", work)
        monkeypatch.setattr(module, "VIDEO_DIR", videos)
        monkeypatch.setattr(module, "AUTO_SHORTS_ROOT", auto)
        monkeypatch.setattr(module, "KIT_ROOT", kit)
        monkeypatch.setattr(module, "SHORTSMITH_ROOT", tmp_path)
    monkeypatch.setattr(render_all, "WORK_ROOT", work)
    monkeypatch.setattr(render_all, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(render_all, "KIT_ROOT", kit)
    return {"root": tmp_path, "work": work, "videos": videos, "kit": kit, "auto": auto}


def add_source(library: dict, name: str, *, manifests: bool = True) -> tuple[Path, Path]:
    wd = library["work"] / name
    wd.mkdir()
    (wd / "clips.json").write_text('[{"rank":1,"slug":"current"}]', encoding="utf-8")
    if manifests:
        cm = wd / "cut_manifests.json"
        cm.write_text("original manifests", encoding="utf-8")
        os.utime(cm, (redo_outdated.FIX_CUTOFF_EPOCH - 1,) * 2)
    (library["videos"] / f"{name}.mp4").write_bytes(b"source")
    project = library["auto"] / name / "short-01-current"
    project.mkdir(parents=True)
    (project / "index.html").write_text(f"<html>{FONT_MARKER}</html>", encoding="utf-8")
    return wd, project


@pytest.mark.parametrize("stage", [
    "pipeline before manifests", "pipeline after manifests", "render", "missing render output",
])
@pytest.mark.parametrize("module", [batch_pipeline, redo_outdated])
def test_failed_work_remains_eligible_until_pipeline_and_render_succeed(
    module: ModuleType, stage: str, batch_library: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wd, project = add_source(batch_library, "source", manifests=module is redo_outdated)
    candidates = (redo_outdated.outdated_work_dirs if module is redo_outdated
                  else batch_pipeline.new_work_dirs)
    attempt, calls = 1, []

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        calls.append(command)
        if "render" in command:
            returncode = int(attempt == 1 and stage == "render")
            if returncode == 0 and not (attempt == 1 and stage == "missing render output"):
                (project / "renders").mkdir(exist_ok=True)
                (project / "renders" / "final.mp4").write_bytes(b"render")
        else:
            assert command[-2:] == ["--from-step", "3"]
            assert options["cwd"] == str(batch_library["root"])
            if attempt != 1 or stage != "pipeline before manifests":
                (wd / "cut_manifests.json").write_text("new manifests", encoding="utf-8")
            returncode = int(attempt == 1 and stage.startswith("pipeline"))
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="item failed")

    monkeypatch.setattr(subprocess, "run", run)
    assert module.main() == 1
    assert candidates() == ([wd], [])
    assert (wd / module.PENDING_MARKER).is_file()
    backups = list(wd.glob("cut_manifests.json.bak.*"))
    if module is redo_outdated:
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "original manifests"
    else:
        assert not backups
    assert len([call for call in calls if "render" in call]) == int(not stage.startswith("pipeline"))

    attempt = 2
    assert module.main() == 0
    assert (project / "renders" / "final.mp4").read_bytes() == b"render"
    assert candidates() == ([], [])
    assert not (wd / module.PENDING_MARKER).exists()
    assert list(wd.glob("cut_manifests.json.bak.*")) == backups
    call_count = len(calls)
    assert module.main() == 0
    assert len(calls) == call_count


@pytest.mark.parametrize("module", [batch_pipeline, redo_existing, redo_outdated, reprocess_all])
@pytest.mark.parametrize("failure", ["nonzero", "cannot launch"])
def test_failed_pipeline_is_recorded_without_rendering_and_other_sources_finish(
    module: ModuleType, failure: str, batch_library: dict, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    add_source(batch_library, "bad", manifests=module is not batch_pipeline)
    _, good = add_source(batch_library, "good", manifests=module is not batch_pipeline)
    calls = []

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        calls.append(command)
        if "render" in command:
            assert Path(command[-1]).parts[-2] == "good"
            (good / "renders").mkdir(exist_ok=True)
            (good / "renders" / "final.mp4").write_bytes(b"render")
        elif Path(command[4]).stem == "bad":
            if failure == "cannot launch":
                raise OSError("cannot start pipeline")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="bad pipeline")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert module.main() == 1
    assert len([call for call in calls if "render" in call]) == 1
    pipelines = [call for call in calls if "render" not in call]
    assert len(pipelines) == 2
    assert all(call[-2:] == ["--from-step", "5" if module is redo_existing else "3"]
               for call in pipelines)
    assert "bad" in caplog.text


@pytest.mark.parametrize("module", [batch_pipeline, redo_existing, redo_outdated, reprocess_all])
def test_missing_source_is_a_failed_item(
    module: ModuleType, batch_library: dict, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    add_source(batch_library, "missing", manifests=module is not batch_pipeline)
    (batch_library["videos"] / "missing.mp4").unlink()
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected process"))
    assert module.main() == 1
    assert "missing" in caplog.text


def test_reprocess_marker_requires_successful_render_and_force_keeps_its_behavior(
    batch_library: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wd, project = add_source(batch_library, "source")
    failed_render, calls = True, []

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        calls.append(command)
        if "render" in command:
            if failed_render:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="render failed")
            (project / "renders").mkdir(exist_ok=True)
            (project / "renders" / "final.mp4").write_bytes(b"render")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert reprocess_all.main() == 1
    assert not (wd / reprocess_all.MARKER).exists()
    failed_render = False
    assert reprocess_all.main() == 0
    marker = wd / reprocess_all.MARKER
    assert marker.is_file()
    call_count = len(calls)
    assert reprocess_all.main() == 0
    assert len(calls) == call_count
    monkeypatch.setattr(sys, "argv", ["reprocess_all.py", "--force"])
    assert reprocess_all.main() == 0
    assert len(calls) == call_count + 2


@pytest.mark.parametrize("module", [batch_pipeline, redo_existing, redo_outdated, reprocess_all])
def test_invalid_canonical_picks_fail_selection_without_rendering_stale_projects(
    module: ModuleType, batch_library: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wd, _ = add_source(batch_library, "source", manifests=module is not batch_pipeline)
    calls = []

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        calls.append(command)
        (wd / "clips.json").write_text(json.dumps({"bad": "shape"}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert module.main() == 1
    assert len(calls) == 1
    assert "render" not in calls[0]


@pytest.mark.parametrize("module", [batch_pipeline, redo_existing, redo_outdated, reprocess_all, render_all])
def test_empty_picks_do_not_render_old_project_folders(
    module: ModuleType, batch_library: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wd, _project = add_source(batch_library, "source", manifests=module is not batch_pipeline)
    (wd / "clips.json").write_text("[]", encoding="utf-8")
    calls = []

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        calls.append(command)
        assert "render" not in command
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert module.main() == 0
    assert len(calls) == int(module is redo_existing)


@pytest.mark.parametrize("module", [batch_pipeline, redo_outdated])
@pytest.mark.parametrize("data", ["{broken", "{}", "[null]"])
def test_bad_picks_are_reported_before_launch_and_other_sources_finish(
    module: ModuleType, data: str, batch_library: dict, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad, _ = add_source(batch_library, "bad", manifests=module is redo_outdated)
    _, good = add_source(batch_library, "good", manifests=module is redo_outdated)
    picks = bad / "clips.json"
    picks.write_text(data, encoding="utf-8")
    calls = []

    def run(command: list[str], **options) -> subprocess.CompletedProcess:
        calls.append(command)
        if "render" in command:
            assert Path(command[-1]).parts[-2] == "good"
            (good / "renders").mkdir(exist_ok=True)
            (good / "renders" / "final.mp4").write_bytes(b"render")
        else:
            assert Path(command[4]).stem == "good"
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert module.main() == 1
    assert len(calls) == 2
    assert str(picks) in caplog.text
    assert (good / "renders" / "final.mp4").read_bytes() == b"render"


@pytest.mark.parametrize("module", [batch_pipeline, redo_outdated])
def test_empty_picks_keep_pending_attempts_as_no_ops(
    module: ModuleType, batch_library: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    work, _ = add_source(batch_library, "source", manifests=module is redo_outdated)
    (work / "clips.json").write_text("[]", encoding="utf-8")
    (work / module.PENDING_MARKER).write_text("pending", encoding="ascii")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected process"))
    assert module.main() == 0
