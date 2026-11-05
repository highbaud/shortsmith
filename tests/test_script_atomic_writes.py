"""Command failures preserve saved files and report partial cleanup correctly."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from scripts import clean_captions, reprocess_all


@pytest.mark.parametrize("dry_run", [False, True])
def test_caption_read_failure_reports_failure_and_keeps_other_output_behavior(
    dry_run: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    auto, kit, work = tmp_path / "auto", tmp_path / "kit", tmp_path / "work"
    project = auto / "source" / "short-01-topic"
    project.mkdir(parents=True)
    caption = project / "caption.txt"
    caption.write_text("Keep words. #topic\n", encoding="utf-8")
    (auto / "source" / "_clips.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(clean_captions, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(clean_captions, "KIT_ROOT", kit)
    monkeypatch.setattr(clean_captions, "WORK_ROOT", work)
    monkeypatch.setattr(sys, "argv", ["clean_captions.py"] + (["--dry-run"] if dry_run else []))

    assert clean_captions.main() == 1
    assert caption.read_text(encoding="utf-8") == (
        "Keep words. #topic\n" if dry_run else "Keep words.\n"
    )


def test_failed_completion_marker_write_keeps_source_eligible_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "source"
    work.mkdir()
    (work / "clips.json").write_text(json.dumps([{"rank": 1}]), encoding="utf-8")
    monkeypatch.setattr(reprocess_all, "WORK_ROOT", tmp_path)
    marker = work / reprocess_all.MARKER
    replace = os.replace

    def fail(source, destination) -> None:
        if Path(destination) == marker:
            raise OSError("completion marker could not be replaced")
        replace(source, destination)

    with monkeypatch.context() as filesystem:
        filesystem.setattr(os, "replace", fail)
        with pytest.raises(OSError, match="completion marker could not be replaced"):
            reprocess_all.mark_reprocessed(work)
    assert not marker.exists()
    assert reprocess_all.candidate_work_dirs(False) == ([work], [])
    reprocess_all.mark_reprocessed(work)
    assert reprocess_all.candidate_work_dirs(False) == ([], [])
