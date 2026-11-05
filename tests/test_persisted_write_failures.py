"""Failed workflow writes keep the last complete settings and manifests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from scripts import ingest_premade
from shortsmith import _wizard, cut_clips
from shortsmith.config import Config


def fail_target_replace(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    replace = os.replace

    def fail(source, destination) -> None:
        if Path(destination) == target:
            raise OSError("persisted replacement failed")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail)


def test_wizard_failed_save_keeps_other_keys_and_does_not_change_process_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("SHORTSMITH_STYLE", "SHORTSMITH_CLIP_ENGINE"):
        monkeypatch.setenv(key, os.environ.get(key, ""))
        monkeypatch.delenv(key)
    monkeypatch.setattr(_wizard, "is_tty", lambda: True)
    env = tmp_path / ".env"
    prior = "# Local choices\nKEEP=value\nSHORTSMITH_CLIP_ENGINE=old\n"
    env.write_text(prior, encoding="utf-8")
    command = click.Command("setup", callback=lambda: _wizard.run_wizard(tmp_path))

    with monkeypatch.context() as filesystem:
        fail_target_replace(filesystem, env)
        result = CliRunner().invoke(command, input="2\n3\n")
    assert isinstance(result.exception, OSError)
    assert result.exit_code != 0
    assert env.read_text(encoding="utf-8") == prior
    assert "Saved to .env" not in result.output
    assert "SHORTSMITH_STYLE" not in os.environ
    assert "SHORTSMITH_CLIP_ENGINE" not in os.environ
    assert list(tmp_path.glob("*.tmp")) == []

    result = CliRunner().invoke(command, input="2\n3\n")
    assert result.exit_code == 0, result.output
    assert env.read_text(encoding="utf-8") == (
        "# Local choices\nKEEP=value\nSHORTSMITH_CLIP_ENGINE=ollama\nSHORTSMITH_STYLE=bold\n"
    )
    assert os.environ["SHORTSMITH_CLIP_ENGINE"] == "ollama"
    assert os.environ["SHORTSMITH_STYLE"] == "bold"


def test_cut_manifest_replacement_failure_keeps_previous_manifest_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "cut_manifests.json"
    prior = '[{"rank":7,"raw_path":"previous.mp4"}]\n'
    target.write_text(prior, encoding="utf-8")
    manifest = {"rank": 1, "slug": "new", "raw_path": "new.mp4"}
    monkeypatch.setattr(cut_clips, "cut_one", lambda *args, **kwargs: manifest.copy())

    with monkeypatch.context() as filesystem:
        fail_target_replace(filesystem, target)
        with pytest.raises(OSError, match="persisted replacement failed"):
            cut_clips.cut_all([{"rank": 1}], [], tmp_path / "source.mp4", tmp_path, Config())
    assert target.read_text(encoding="utf-8") == prior
    assert list(tmp_path.glob("*.tmp")) == []

    assert cut_clips.cut_all([{"rank": 1}], [], tmp_path / "source.mp4", tmp_path, Config()) == [manifest]
    assert json.loads(target.read_text(encoding="utf-8")) == [manifest]


@pytest.mark.parametrize("name", ["cut_manifests.json", "_premade_transcripts.json"])
def test_ingest_keeps_each_previous_manifest_on_failed_replacement_and_retries(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "work" / "existing"
    work.mkdir(parents=True)
    target = work / name
    prior = '[{"rank":7,"text":"previous words"}]\n'
    for filename in ("cut_manifests.json", "_premade_transcripts.json"):
        (work / filename).write_text(prior, encoding="utf-8")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source copied at the media service boundary")
    monkeypatch.setattr(ingest_premade, "__file__", str(tmp_path / "scripts" / "ingest_premade.py"))
    monkeypatch.setattr(sys, "argv", ["ingest_premade.py", "existing", str(source)])
    monkeypatch.setattr(ingest_premade.transcribe, "transcribe", lambda *args, **kwargs: [
        {"word": "Saved", "start": 0.0, "end": 1.0},
    ])

    with monkeypatch.context() as filesystem:
        fail_target_replace(filesystem, target)
        with pytest.raises(OSError, match="persisted replacement failed"):
            ingest_premade.main()
    assert target.read_text(encoding="utf-8") == prior
    assert list(work.glob("*.tmp")) == []

    assert ingest_premade.main() == 0
    manifests = json.loads((work / "cut_manifests.json").read_text(encoding="utf-8"))
    summaries = json.loads((work / "_premade_transcripts.json").read_text(encoding="utf-8"))
    assert manifests[0]["rank"] == 1
    assert manifests[0]["source_file"] == source.name
    assert summaries == [{"rank": 1, "source_file": source.name, "text": "Saved", "duration": 1.0}]
