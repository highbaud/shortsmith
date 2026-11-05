"""First-run choices and health checks use local files and process boundaries."""
from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from shortsmith import _doctor, _wizard
from shortsmith.config import Config
from shortsmith.pipeline import cli


@pytest.mark.parametrize(("from_step", "style", "engine", "expected"), [
    (2, "", "", True),
    (2, "minimal", "", True),
    (2, "minimal", "ollama", False),
    (3, "", "", False),
])
def test_wizard_gate(
    monkeypatch: pytest.MonkeyPatch, from_step: int, style: str, engine: str, expected: bool,
) -> None:
    monkeypatch.setenv("SHORTSMITH_STYLE", style)
    monkeypatch.setenv("SHORTSMITH_CLIP_ENGINE", engine)
    assert _wizard.needs_wizard(from_step) is expected


def test_headless_setup_does_not_prompt_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHORTSMITH_STYLE", raising=False)
    monkeypatch.delenv("SHORTSMITH_CLIP_ENGINE", raising=False)
    chosen = {}
    command = click.Command("setup", callback=lambda: chosen.update(_wizard.run_wizard(tmp_path)))

    result = CliRunner().invoke(command)

    assert result.exit_code == 0
    assert result.output == ""
    assert chosen == {}
    assert not (tmp_path / ".env").exists()
    assert "SHORTSMITH_STYLE" not in _wizard.os.environ
    assert "SHORTSMITH_CLIP_ENGINE" not in _wizard.os.environ


def test_setup_replaces_selected_entry_preserves_other_lines_and_updates_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHORTSMITH_STYLE", "")
    monkeypatch.setenv("SHORTSMITH_CLIP_ENGINE", "")
    monkeypatch.delenv("SHORTSMITH_STYLE", raising=False)
    monkeypatch.delenv("SHORTSMITH_CLIP_ENGINE", raising=False)
    monkeypatch.setattr(_wizard, "is_tty", lambda: True)
    env = tmp_path / ".env"
    env.write_text("# Local setup\nKEEP=value\n\nSHORTSMITH_CLIP_ENGINE=old\n", encoding="utf-8")
    chosen = {}
    command = click.Command("setup", callback=lambda: chosen.update(_wizard.run_wizard(tmp_path)))

    result = CliRunner().invoke(command, input="2\n3\n")

    assert result.exit_code == 0, result.output
    assert chosen == {"SHORTSMITH_CLIP_ENGINE": "ollama", "SHORTSMITH_STYLE": "bold"}
    assert env.read_text(encoding="utf-8") == (
        "# Local setup\nKEEP=value\n\nSHORTSMITH_CLIP_ENGINE=ollama\nSHORTSMITH_STYLE=bold\n"
    )
    assert _wizard.os.environ["SHORTSMITH_CLIP_ENGINE"] == "ollama"
    assert _wizard.os.environ["SHORTSMITH_STYLE"] == "bold"
    assert not _wizard.needs_wizard(2)


def test_setup_prompts_only_for_missing_choice_and_uses_default_for_unknown_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHORTSMITH_CLIP_ENGINE", "ollama")
    monkeypatch.setenv("SHORTSMITH_STYLE", "")
    monkeypatch.delenv("SHORTSMITH_STYLE", raising=False)
    monkeypatch.setattr(_wizard, "is_tty", lambda: True)
    chosen = {}
    command = click.Command("setup", callback=lambda: chosen.update(_wizard.run_wizard(tmp_path)))

    result = CliRunner().invoke(command, input="unknown\n")

    assert result.exit_code == 0
    assert "How should shortsmith pick" not in result.output
    assert chosen == {"SHORTSMITH_STYLE": "xrp-revolution"}
    assert _wizard.os.environ["SHORTSMITH_CLIP_ENGINE"] == "ollama"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SHORTSMITH_STYLE=xrp-revolution\n"


@pytest.fixture
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    repo = tmp_path / "install"
    audio = repo / "audio-enhance"
    whisper = repo / "whisperx-align"
    kit = repo / "kit"
    template = kit / "video-projects" / "template"
    for path in (audio / ".venv", whisper / ".venv", template, repo / "remotion/node_modules"):
        path.mkdir(parents=True)
    (repo / "remotion/package.json").write_text("{}", encoding="utf-8")
    pack = repo / "assets/sfx/pack/pack.json"
    pack.parent.mkdir(parents=True)
    pack.write_text('{"swipe-in":["swipe.wav"]}', encoding="utf-8")
    model = repo / "models/yunet.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"x" * 1024)
    cfg = Config(anthropic_api_key="local-test-value", enhance_engine="clearvoice",
                 clip_engine="anthropic", yunet_model_path=model)
    missing_tools: set[str] = set()
    monkeypatch.setattr(_doctor.shutil, "which", lambda name: None if name in missing_tools else name)
    for name, value in {
        "REPO_ROOT": repo, "AUDIO_ENHANCE_PROJECT": audio, "WHISPERX_ALIGN_PROJECT": whisper,
        "KIT_ROOT": kit, "TEMPLATE_REF": template, "HYPERFRAMES_SPEC": "hyperframes@pinned",
    }.items():
        monkeypatch.setattr(_doctor, name, value)
    monkeypatch.setattr(_doctor, "Config", lambda: cfg)
    return {"repo": repo, "audio": audio, "template": template,
            "pack": pack, "model": model, "cfg": cfg, "missing_tools": missing_tools}


def test_doctor_all_required_checks_pass(installation: dict) -> None:
    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "All required checks passed" in result.output
    assert "[fail]" not in result.output and "[warn]" not in result.output
    assert "SFX pack: 1/1 slots populated" in result.output
    assert "YuNet face detection model (1 KB)" in result.output
    assert "local-test-value" not in result.output


def test_doctor_counts_required_failures(installation: dict) -> None:
    installation["missing_tools"].update({"ffprobe", "uv"})
    installation["model"].unlink()

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "3 required check(s) failed" in result.output
    assert "ffmpeg or ffprobe not found" in result.output
    assert "uv not on PATH" in result.output
    assert "YuNet model missing" in result.output


def test_doctor_optional_warnings_keep_success_exit_status(installation: dict) -> None:
    installation["missing_tools"].update({"npm", "npx"})
    installation["audio"].joinpath(".venv").rmdir()
    installation["template"].rmdir()
    installation["cfg"].anthropic_api_key = ""
    installation["pack"].write_text("{", encoding="utf-8")

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0
    assert "All required checks passed" in result.output
    assert "[fail]" not in result.output
    assert "npm / npx not on PATH" in result.output
    assert "audio-enhance found but .venv missing" in result.output
    assert "template reference missing" in result.output
    assert "ANTHROPIC_API_KEY missing" in result.output
    assert "SFX pack.json unreadable" in result.output


@pytest.mark.parametrize(("size", "expected"), [
    (1023, "1023 B"), (1024, "1 KB"), (1024**2, "1 MB"),
    (1024**3, "1 GB"), (1024**4, "1.0 TB"),
])
def test_byte_units_keep_boundary_format(size: int, expected: str) -> None:
    assert _doctor._bytes(size) == expected
