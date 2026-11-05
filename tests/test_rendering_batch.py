"""Direct tests for shortsmith.rendering.batch.run_batch: a source is marked done
only after its pipeline run and every project render succeed."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from shortsmith.rendering import batch

LOG = logging.getLogger("test-batch")


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "My Talk.mp4").write_bytes(b"video")
    work = tmp_path / "work" / "my-talk"
    work.mkdir(parents=True)
    output = tmp_path / "out"
    (output / "my-talk").mkdir(parents=True)
    monkeypatch.setattr(batch, "resolve_executable", lambda name: name)
    monkeypatch.setattr(batch, "select_projects", lambda out_dir, _parent: [out_dir / "short-01"])
    return {"videos": videos, "work": work, "output": output, "root": tmp_path}


def _run(layout: dict, **kwargs) -> tuple[list[str], list[str], int]:
    return batch.run_batch(
        [layout["work"]], video_dir=layout["videos"], output_root=layout["output"],
        kit_root=layout["root"], project_root=layout["root"], from_step=1, logger=LOG,
        pending_marker=".pending", **kwargs,
    )


def _pipeline(returncode: int):
    def run(command, **_options):
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="boom")
    return run


def test_success_marks_done_and_clears_the_pending_marker(
    layout: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    done_dirs = []
    monkeypatch.setattr(batch.subprocess, "run", _pipeline(0))
    monkeypatch.setattr(batch, "render_project", lambda *_args: True)

    assert _run(layout, on_success=done_dirs.append) == ([], [], 1)
    assert done_dirs == [layout["work"]]
    assert not (layout["work"] / ".pending").exists()


def test_failed_render_keeps_the_pending_marker(layout: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    done_dirs = []
    monkeypatch.setattr(batch.subprocess, "run", _pipeline(0))
    monkeypatch.setattr(batch, "render_project", lambda *_args: False)

    assert _run(layout, on_success=done_dirs.append) == ([], ["my-talk/short-01"], 0)
    assert done_dirs == []
    assert (layout["work"] / ".pending").exists()


def test_failed_pipeline_skips_rendering(layout: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    rendered = []
    monkeypatch.setattr(batch.subprocess, "run", _pipeline(2))
    monkeypatch.setattr(batch, "render_project", lambda project, *_args: rendered.append(project))

    assert _run(layout) == (["My Talk.mp4"], [], 0)
    assert rendered == []


def test_missing_source_video_is_a_pipeline_failure(layout: dict) -> None:
    (layout["videos"] / "My Talk.mp4").unlink()

    assert _run(layout) == (["my-talk"], [], 0)
