"""Pack and thumbnail commands keep real file state around fake media processes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_sfx_pack, make_thumbnails


@pytest.fixture
def media_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = tmp_path / "native tools"
    tools.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (tools / (name + ".exe" if os.name == "nt" else name)).write_bytes(b"external process fake")
    monkeypatch.setenv("PATH", str(tools))


def test_pack_command_preserves_variant_order_and_approved_peak_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, media_tools: None,
) -> None:
    raw = tmp_path / "sounds"
    raw.mkdir()
    for name in ("quiet.wav", "loud.wav", "failed.wav"):
        (raw / name).write_bytes(b"raw sound")
    monkeypatch.setattr(build_sfx_pack, "SFX_DIR", raw)
    monkeypatch.setattr(build_sfx_pack, "CURATION", {"ding": [
        ("quiet.wav", "ding-1.wav"), ("absent.wav", "missing.wav"),
        ("loud.wav", "ding-2.wav"), ("failed.wav", "failed-out.wav"),
    ]})
    filters = []

    def run(command, **kwargs):
        source = Path(command[command.index("-i") + 1]).name
        assert Path(command[0]).stem.lower() == "ffmpeg"
        if "volumedetect" in command:
            peak = "-15.0" if source == "quiet.wav" else "-3.0"
            return subprocess.CompletedProcess(command, 0, stderr=f"max_volume: {peak} dB")
        filters.append(command[command.index("-af") + 1])
        assert command[-5:-1] == ["-ac", "2", "-ar", "48000"]
        if source == "failed.wav":
            return subprocess.CompletedProcess(command, 1, stderr="encoder failed")
        Path(command[-1]).write_bytes(source.encode())
        return subprocess.CompletedProcess(command, 0, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert build_sfx_pack.main() == 0
    assert json.loads((raw / "pack/pack.json").read_text()) == {"ding": ["ding-1.wav", "ding-2.wav"]}
    assert (raw / "pack/ding-1.wav").read_bytes() == b"quiet.wav"
    assert (raw / "pack/ding-2.wav").read_bytes() == b"loud.wav"
    assert "volume=6.00dB" in filters[0] and "volume=-6.00dB" in filters[1]
    assert all("start_threshold=-50dB" in item and "aresample=48000" in item for item in filters)


@pytest.mark.parametrize(("returncode", "stderr"), [
    (1, "max_volume: -3.0 dB"),
    (0, "no peak measurement"),
])
def test_pack_cannot_claim_the_approved_level_when_peak_measurement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, media_tools: None,
    returncode: int, stderr: str,
) -> None:
    (tmp_path / "raw.wav").write_bytes(b"unmeasured sound")
    monkeypatch.setattr(build_sfx_pack, "SFX_DIR", tmp_path)
    monkeypatch.setattr(build_sfx_pack, "CURATION", {"ding": [("raw.wav", "ding.wav")]})
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "volumedetect" in command:
            return subprocess.CompletedProcess(command, returncode, stderr=stderr)
        Path(command[-1]).write_bytes(b"unmeasured sound")
        return subprocess.CompletedProcess(command, 0, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert build_sfx_pack.main() == 1
    assert len(calls) == 1
    assert json.loads((tmp_path / "pack/pack.json").read_text()) == {}
    assert not (tmp_path / "pack/ding.wav").exists()


@pytest.fixture
def thumbnail_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, media_tools: None) -> dict:
    auto = tmp_path / "auto"
    project = auto / "source/short-01-topic"
    renders = project / "renders"
    renders.mkdir(parents=True)
    video = renders / "final_sfx.mp4"
    video.write_bytes(b"approved original")
    (renders / "final_remotion.mp4").write_bytes(b"other render")
    (project.parent / "_clips.json").write_text(json.dumps([
        {"rank": 1, "hook": {"text": "Opening", "duration": 4.0}}]))
    monkeypatch.setattr(make_thumbnails, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(make_thumbnails, "ALL_DIR", tmp_path / "exports")
    monkeypatch.setattr(sys, "argv", ["make_thumbnails.py"])
    return {"root": tmp_path, "project": project, "renders": renders, "video": video}


def thumbnail_processes(monkeypatch: pytest.MonkeyPatch, *, fail_poster: bool = False) -> list:
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if Path(command[0]).stem.lower() == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="10.0\n")
        output = Path(command[-1])
        if output.name == "_thumb_poster.mp4" and fail_poster:
            raise subprocess.CalledProcessError(1, command, stderr=b"poster failed")
        output.write_bytes(b"hook image" if output.suffix == ".jpg" else b"poster video")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_thumbnail_batch_without_export_folder_writes_local_cover_and_skips_current_marker(
    thumbnail_project: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = thumbnail_processes(monkeypatch)
    assert make_thumbnails.main() == 0
    renders = thumbnail_project["renders"]
    assert (renders / "thumb.jpg").read_bytes() == b"hook image"
    assert thumbnail_project["video"].read_bytes() == b"poster video"
    assert (renders / ".thumb_done").read_text() == "done\n"
    assert not (renders / "_freeze.mp4").exists() and not (renders / "_thumb_concat.mp4").exists()
    extract = next(command for command in calls if "-ss" in command)
    assert extract[extract.index("-ss") + 1] == "1.5"
    assert extract[extract.index("-i") + 1] == str(thumbnail_project["video"])
    freeze = next(command for command in calls if "anullsrc=channel_layout=stereo:sample_rate=48000" in command)
    assert freeze[freeze.index("-t") + 1] == "0.5"
    count = len(calls)
    assert make_thumbnails.main() == 0
    assert len(calls) == count
    os.utime(renders / ".thumb_done", (0, 0))
    assert make_thumbnails.main() == 0
    assert len(calls) > count


def test_thumbnail_single_video_no_freeze_writes_sidecar_and_poster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, media_tools: None,
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"original")
    monkeypatch.setattr(sys, "argv", ["make_thumbnails.py", "--video", str(video),
                                     "--hook-t", "1.25", "--no-freeze"])
    calls = thumbnail_processes(monkeypatch)

    assert make_thumbnails.main() == 0
    assert video.read_bytes() == b"poster video"
    assert video.with_suffix(".jpg").read_bytes() == b"hook image"
    assert len(calls) == 2
    assert "-disposition:v:1" in calls[-1] and "attached_pic" in calls[-1]


def test_thumbnail_batch_refreshes_consolidated_files_and_force_rebuilds(
    thumbnail_project: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    exports = thumbnail_project["root"] / "exports"
    exports.mkdir()
    output = exports / "source__short-01-topic.mp4"
    output.write_bytes(b"old export")
    calls = thumbnail_processes(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["make_thumbnails.py", "--no-freeze"])

    assert make_thumbnails.main() == 0
    assert output.read_bytes() == b"poster video"
    assert output.with_suffix(".jpg").read_bytes() == b"hook image"
    count = len(calls)
    monkeypatch.setattr(sys, "argv", ["make_thumbnails.py", "--no-freeze", "--force"])
    assert make_thumbnails.main() == 0
    assert len(calls) == count * 2


def test_thumbnail_poster_failure_keeps_original_and_does_not_mark_complete(
    thumbnail_project: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    thumbnail_processes(monkeypatch, fail_poster=True)
    exports = thumbnail_project["root"] / "exports"
    exports.mkdir()
    monkeypatch.setattr(sys, "argv", ["make_thumbnails.py", "--no-freeze"])

    with pytest.raises(subprocess.CalledProcessError):
        make_thumbnails.main()
    assert thumbnail_project["video"].read_bytes() == b"approved original"
    assert not (thumbnail_project["renders"] / ".thumb_done").exists()
    assert not list(exports.glob("*.mp4"))
