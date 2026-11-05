"""Shared video inputs preserve each finishing command's selection policy."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shortsmith.rendering import add_sfx, finalize, project_inputs


def video(path: Path, modified: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture video")
    os.utime(path, (modified, modified))
    return path


def test_publishing_prefers_sfx_then_remotion_before_newer_local_renders(tmp_path: Path) -> None:
    project = tmp_path / "short-01-topic"
    assert project_inputs.finalized_deliverable(project) is None
    sfx = video(project / "renders/final_sfx.mp4", 100)
    remotion = video(project / "renders/final_remotion.mp4", 200)
    newest = video(project / "renders/new-render.mp4", 300)
    video(project / "renders/_staged.mp4", 400)

    assert project_inputs.finalized_deliverable(project) == sfx
    sfx.unlink()
    assert project_inputs.finalized_deliverable(project) == remotion
    remotion.unlink()
    assert project_inputs.finalized_deliverable(project) == newest
    newest.unlink()
    assert project_inputs.finalized_deliverable(project) is None


def test_sfx_candidates_keep_local_before_kit_and_filter_staged_local_outputs(tmp_path: Path) -> None:
    project = tmp_path / "source/short-01-topic"
    local = video(project / "renders/final.mp4", 100)
    video(project / "renders/final_sfx.mp4", 500)
    video(project / "renders/_base.mp4", 600)
    video(project / "renders/_sfx_tmp.mp4", 700)
    kit = tmp_path / "kit-renders"
    timestamped = video(kit / "short-01-topic_2026.mp4", 200)
    video(kit / "short-02-unrelated_2026.mp4", 800)

    assert project_inputs.non_sfx_renders(project, kit) == [local, timestamped]


def test_finalize_prefers_remotion_but_sfx_command_uses_the_newest_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    auto = tmp_path / "projects"
    project = auto / "source/short-01-topic"
    remotion = video(project / "renders/final_remotion.mp4", 100)
    video(project / "renders/final.mp4", 200)
    video(project / "renders/final_sfx.mp4", 500)
    video(project / "renders/_staged.mp4", 600)
    kit = tmp_path / "kit-renders"
    newest = video(kit / "short-01-topic_2026.mp4", 300)
    monkeypatch.setattr(add_sfx, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(add_sfx, "KIT_RENDERS", kit)
    monkeypatch.setattr(finalize, "KIT_RENDERS", kit)

    assert remotion in project_inputs.non_sfx_renders(project, kit)
    assert finalize.best_render(project) == remotion
    assert add_sfx.find_latest_non_sfx_render("source", 1) == (project, newest)
    remotion.unlink()
    assert finalize.best_render(project) == newest


def test_equal_timestamps_keep_the_local_candidate_for_both_newest_policies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    auto = tmp_path / "projects"
    project = auto / "source/short-01-topic"
    local = video(project / "renders/final.mp4", 100)
    kit = tmp_path / "kit-renders"
    video(kit / "short-01-topic_2026.mp4", 100)
    monkeypatch.setattr(add_sfx, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(add_sfx, "KIT_RENDERS", kit)
    monkeypatch.setattr(finalize, "KIT_RENDERS", kit)

    assert add_sfx.find_latest_non_sfx_render("source", 1) == (project, local)
    assert finalize.best_render(project) == local


def test_missing_candidate_roots_remain_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "source/short-01-topic"
    kit = tmp_path / "missing-kit"
    monkeypatch.setattr(add_sfx, "AUTO_SHORTS_ROOT", tmp_path / "missing-projects")
    monkeypatch.setattr(finalize, "KIT_RENDERS", kit)

    assert project_inputs.non_sfx_renders(project, kit) == []
    assert add_sfx.find_latest_non_sfx_render("source", 1) is None
    assert finalize.best_render(project) is None
