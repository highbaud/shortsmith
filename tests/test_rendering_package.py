"""Library ownership preserves command APIs, resource roots, and render stamps."""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from shortsmith import config
from shortsmith.config import REPO_ROOT
from shortsmith.rendering import (
    add_sfx,
    broll_assets,
    finalize,
    gen_broll,
    project_inputs,
    render_remotion,
    render_stamp,
    source_capture,
)


@pytest.mark.parametrize("module,implementation", [
    ("apply_remotion", "apply_remotion"),
    ("gen_broll", "generate"),
    ("render_remotion", "render"),
    ("finalize", "phase1_sfx"),
    ("add_sfx", "load_optional_manifest_words"),
])
def test_stable_commands_only_expose_the_canonical_main(module: str, implementation: str) -> None:
    command = importlib.import_module("scripts." + module)
    backend = importlib.import_module("shortsmith.rendering." + module)

    assert command.main is backend.main
    assert not hasattr(command, implementation)


@pytest.mark.parametrize("module", [
    "render_stamp", "source_broll", "brand_logos", "person_photos", "wikidata",
    "source_capture", "source_discovery", "project_selection",
])
def test_library_helpers_have_no_command_compatibility_file(module: str) -> None:
    assert not (REPO_ROOT / "scripts" / (module + ".py")).exists()
    backend = importlib.import_module("shortsmith.rendering." + module)
    assert Path(backend.__file__).resolve() == REPO_ROOT / "shortsmith" / "rendering" / (module + ".py")


def test_resources_keep_their_existing_repo_locations() -> None:
    assert gen_broll.PROMPT_PATH == REPO_ROOT / "prompts" / "gen_broll.md"
    assert broll_assets.PEOPLE_DIR == REPO_ROOT / "assets" / "people"
    assert broll_assets._CACHE_DIR == REPO_ROOT / ".cache" / "broll-fetch"
    assert render_remotion.REMOTION_DIR == REPO_ROOT / "remotion"
    assert render_remotion.STYLES_DIR == REPO_ROOT / "templates" / "styles"
    assert render_stamp.SHORTSMITH_ROOT == REPO_ROOT
    assert source_capture._ROOT == REPO_ROOT
    assert finalize.SHORTSMITH_ROOT == add_sfx.SHORTSMITH_ROOT == REPO_ROOT
    assert finalize.WORK_ROOT == add_sfx.WORK_ROOT == REPO_ROOT / "work"
    assert finalize.KIT_RENDERS == add_sfx.KIT_RENDERS == config.KIT_ROOT / "renders"
    assert (REPO_ROOT / "scripts" / "capture_source.mjs").is_file()


def test_finalization_reads_the_same_canonical_sfx_state() -> None:
    assert finalize.load_sfx_state is add_sfx.load_sfx_state
    assert finalize.load_optional_manifest_words is add_sfx.load_optional_manifest_words


def test_finalization_library_imports_do_not_configure_logging_or_load_commands(
    tmp_path: Path,
) -> None:
    code = """
import logging
import sys
def unexpected_setup(*args, **kwargs):
    raise AssertionError("Library import configured logging")
logging.basicConfig = unexpected_setup
from shortsmith.rendering import add_sfx, finalize
assert not any(name == "scripts" or name.startswith("scripts.") for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode,module,option", [
    ("script", "finalize", "--slug"),
    ("module", "finalize", "--skip-sfx"),
    ("script", "add_sfx", "--overwrite"),
    ("module", "add_sfx", "--overwrite"),
])
def test_finalization_help_exits_before_accessing_work_files(
    tmp_path: Path, mode: str, module: str, option: str,
) -> None:
    target = ([str(REPO_ROOT / "scripts" / (module + ".py"))] if mode == "script"
              else ["-m", "shortsmith.rendering." + module])
    result = subprocess.run([sys.executable, *target, "--help"], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8", check=False)

    assert result.returncode == 0, result.stderr
    assert option in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("module", [
    "project_inputs", "gen_broll", "broll_assets", "mentions", "render_remotion",
    "source_broll", "source_evidence", "source_resolution", "source_capture", "source_discovery",
])
def test_library_changes_are_part_of_the_render_fingerprint(module: str) -> None:
    assert REPO_ROOT / "shortsmith" / "rendering" / (module + ".py") in render_stamp.code_files()


def test_render_fingerprint_hashes_library_and_browser_code_without_launchers() -> None:
    files = render_stamp.code_files()

    assert REPO_ROOT / "scripts" / "capture_source.mjs" in files
    assert all(not path.is_relative_to(REPO_ROOT / "scripts") or path.suffix == ".mjs"
               for path in files)


def test_hidden_discovery_worker_uses_the_library_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "shortsmith.rendering.source_discovery"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )

    assert result.returncode != 0
    assert "Internal source-discovery worker" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("mode", ["clip", "final", "sfx", "hyperframes", "auto"])
def test_missing_base_is_a_catchable_file_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    monkeypatch.setattr(config, "AUTO_SHORTS_ROOT", tmp_path / "kit" / "video-projects" / "auto-shorts")

    with pytest.raises(FileNotFoundError):
        project_inputs.pick_base(tmp_path, mode)


def test_missing_transcript_is_a_catchable_generation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHORTSMITH_BROLL_SOURCES", "off")

    with pytest.raises(FileNotFoundError, match="No transcript"):
        gen_broll.generate(tmp_path, heuristic=True, cap=6, dry_run=True, sources="off")


def test_missing_caption_input_is_a_catchable_render_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No words.json"):
        render_remotion.render(
            tmp_path, captions=True, platform="generic", base_mode="clip",
            broll_arg=None, output="final_remotion.mp4", style="minimal", open_after=False,
            sources="off",
        )


@pytest.mark.parametrize("command,options,message", [
    ("gen_broll", ["--heuristic", "--dry-run"], "No transcript"),
    ("render_remotion", [], "No words.json"),
    ("render_remotion", ["--no-captions", "--base", "clip"], "Requested base 'clip' not found"),
])
def test_direct_commands_report_missing_inputs_without_a_traceback(
    tmp_path: Path, command: str, options: list[str], message: str,
) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / (command + ".py")),
         str(tmp_path), "--sources", "off", *options],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )

    assert result.returncode != 0
    assert message in result.stderr
    assert "Traceback" not in result.stderr
