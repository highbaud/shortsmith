"""Source controls must affect the rendered video and its cache, not just discovery."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import finalize  # noqa: E402
import render_remotion  # noqa: E402
import render_stamp  # noqa: E402


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SHORTSMITH_BROLL_SOURCES", "off")
    root = tmp_path / "short-01-source"
    (root / "assets" / "broll" / "sources").mkdir(parents=True)
    (root / "renders").mkdir()
    (root / "assets" / "words.json").write_text("[]", encoding="utf-8")
    (root / "renders" / "final.mp4").write_bytes(b"base")
    return root


def _stamp(project: Path, sources: str) -> dict:
    return render_stamp.compute_stamp(
        project, base=project / "renders" / "final.mp4",
        style="minimal", platform="generic", captions=False, sources=sources)


def test_switch_changes_invalidate_render_cache(project: Path) -> None:
    off = _stamp(project, "off")
    press = _stamp(project, "press")
    assert render_stamp.changed_inputs(off, press) == ["source_options"]


def test_active_source_pixel_changes_invalidate_cache(project: Path) -> None:
    image = project / "assets" / "broll" / "sources" / "reference.png"
    image.write_bytes(b"first pixels")
    (project / "broll.auto.json").write_text(json.dumps([{
        "type": "image", "source_kind": "images", "start": 1, "end": 3,
        "src": "assets/broll/sources/reference.png",
    }]), encoding="utf-8")
    on, off = _stamp(project, "images"), _stamp(project, "off")
    image.write_bytes(b"different pixels")
    assert render_stamp.changed_inputs(on, _stamp(project, "images")) == ["source_assets"]
    assert render_stamp.changed_inputs(off, _stamp(project, "off")) == []


def test_disabled_cached_sources_do_not_reach_render_props(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "broll.sources.json").write_text('{"enabled": ["press"]}', encoding="utf-8")
    (project / "broll.auto.json").write_text(json.dumps([
        {"type": "image", "source_kind": "press", "src": "assets/old.png",
         "start": 1, "end": 3},
        {"type": "text", "title": "Keep the existing card", "start": 3, "end": 5},
    ]), encoding="utf-8")
    captured = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="5.0\n")
        assert command[1:3] == ["remotion", "render"]
        props_path = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--props="))
        captured.append(json.loads(Path(props_path).read_text(encoding="utf-8")))
        Path(command[5]).write_bytes(b"render")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    result = render_remotion.render(
        project, captions=False, platform="generic", base_mode="hyperframes",
        broll_arg=None, output="checked.mp4", style="minimal", open_after=False, sources="off")
    assert result.exists()
    assert captured[0]["broll"] == [
        {"type": "text", "title": "Keep the existing card", "start": 3, "end": 5}]
    assert json.loads((project / "renders" / "source-credits.json").read_text()) == {"sources": []}


def test_source_run_stops_before_copying_an_old_video_when_base_is_missing(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "renders" / "final.mp4").unlink()
    (project / "renders" / "final_remotion.mp4").write_bytes(b"old source render")
    monkeypatch.setattr(finalize, "AUTO_SHORTS_ROOT", project.parent.parent)
    # The source folder layout is AUTO_SHORTS_ROOT/source/short-NN-name.
    source_root = project.parent / "source"
    source_root.mkdir()
    target = source_root / project.name
    project.rename(target)
    monkeypatch.setattr(finalize, "AUTO_SHORTS_ROOT", source_root.parent)
    monkeypatch.setattr(finalize, "SHORTSMITH_ROOT", source_root.parent / "test-code")
    with pytest.raises(RuntimeError, match="Old videos were not copied"):
        finalize.phase0_remotion("minimal", sources="off")


@pytest.mark.parametrize("allow_unmixed", [True, False])
def test_source_updates_never_consolidate_a_stale_sound_render(
    project: Path, monkeypatch: pytest.MonkeyPatch, allow_unmixed: bool,
) -> None:
    import os

    fresh = project / "renders" / "final_remotion.mp4"
    fresh.write_bytes(b"new render without sources")
    old = project / "renders" / "final_sfx.mp4"
    old.write_bytes(b"old render with source screenshots")
    os.utime(old, ns=(fresh.stat().st_atime_ns, fresh.stat().st_mtime_ns - 1_000_000))
    monkeypatch.setattr(finalize, "AUTO_SHORTS_ROOT", project.parent.parent)
    source_root = project.parent / "source"
    source_root.mkdir()
    target = source_root / project.name
    project.rename(target)
    monkeypatch.setattr(finalize, "AUTO_SHORTS_ROOT", source_root.parent)
    final_folder = source_root.parent / "output"
    monkeypatch.setattr(finalize, "ALL_DIR", final_folder)

    def probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert command[0] == "ffprobe"
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"streams": [
            {"codec_type": "video", "width": 1080, "height": 1920},
            {"codec_type": "audio"}]}))

    monkeypatch.setattr(subprocess, "run", probe)
    if allow_unmixed:
        assert finalize.phase2_consolidate(allow_unmixed=True, sources="off") == 1
        assert next(final_folder.glob("*.mp4")).read_bytes() == b"new render without sources"
    else:
        with pytest.raises(RuntimeError, match="sound-effect video is stale"):
            finalize.phase2_consolidate(sources="off")
        assert list(final_folder.glob("*.mp4")) == []


@pytest.mark.parametrize("args", [
    ["--sources", "typo"],
    ["--sources", "off", "--skip-remotion"],
])
def test_invalid_source_run_is_rejected_before_work(
    args: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["finalize.py", *args])
    with pytest.raises(SystemExit) as result:
        finalize.main()
    assert result.value.code == 2
