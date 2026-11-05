"""A failed alignment stays visible and can succeed on the next CLI run."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from shortsmith import align, checkpoint, config, pipeline
from shortsmith._files import atomic_write


def test_alignment_failure_clears_old_checkpoint_and_retry_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source fixture")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    media = work_dir / "clip.mp4"
    media.write_bytes(b"external media fixture")
    manifest = {"rank": 1, "raw_path": str(media)}
    (work_dir / "transcript.json").write_text("[]", encoding="utf-8")
    (work_dir / "clips.json").write_text('[{"rank": 1, "slug": "clip"}]', encoding="utf-8")
    (work_dir / "cut_manifests.json").write_text(json.dumps([manifest]), encoding="utf-8")
    checkpoint.mark_step(work_dir, 6)
    monkeypatch.setattr(config, "make_work_dir", lambda _: work_dir)
    monkeypatch.setattr(config, "make_output_dir", lambda _: tmp_path / "projects")
    monkeypatch.setattr(config.Config, "validate", lambda _: [])
    monkeypatch.setenv("SHORTSMITH_ALIGN", "whisperx")

    def unavailable(*args, **kwargs):
        raise RuntimeError("external model is offline")

    monkeypatch.setattr(align, "_run_whisperx_batch", unavailable)
    monkeypatch.setattr(align.transcribe, "transcribe", unavailable)
    monkeypatch.setattr(pipeline.reframe, "reframe_all", lambda manifests, *args: manifests)
    scaffolded = []

    def scaffold(manifests, *args):
        scaffolded.append(manifests)
        return []

    monkeypatch.setattr(pipeline.scaffold, "scaffold_all", scaffold)
    runner = CliRunner()
    argv = ["run", str(video), "--from-step", "6", "--no-enhance"]

    failed = runner.invoke(pipeline.cli, argv)

    assert failed.exit_code == 1, failed.output
    assert "Alignment failed for clips [1]" in failed.output
    assert not checkpoint.step_done(work_dir, 6)
    saved = json.loads((work_dir / "cut_manifests.json").read_text(encoding="utf-8"))
    assert saved[0]["alignment_failed"] is True
    assert json.loads(Path(saved[0]["words_path"]).read_text(encoding="utf-8")) == []
    assert scaffolded[0][0]["alignment_failed"] is True

    def recovered(jobs, cfg):
        for _, words_path in jobs:
            atomic_write(words_path, "[]")
        return {str(source) for source, _ in jobs}

    monkeypatch.setattr(align, "_run_whisperx_batch", recovered)

    completed = runner.invoke(pipeline.cli, argv)

    assert completed.exit_code == 0, completed.output
    assert checkpoint.step_done(work_dir, 6)
    saved = json.loads((work_dir / "cut_manifests.json").read_text(encoding="utf-8"))
    assert "alignment_failed" not in saved[0]
    assert len(scaffolded) == 2
