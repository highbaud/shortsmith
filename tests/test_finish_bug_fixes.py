"""Finishing must export only current, valid videos and preserve held media."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import batch_pipeline  # noqa: E402
import finalize  # noqa: E402
import gen_broll  # noqa: E402
import project_selection  # noqa: E402
import render_stamp  # noqa: E402
import reprocess_all  # noqa: E402

from shortsmith.config import Config  # noqa: E402


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    auto = tmp_path / "kit" / "projects"
    source = auto / "source"
    work = tmp_path / "work"
    wd = work / "source"
    wd.mkdir(parents=True)
    source.mkdir(parents=True)
    renders = tmp_path / "kit" / "renders"
    renders.mkdir()
    finals = renders / "_all"
    finals.mkdir()
    monkeypatch.setenv("SHORTSMITH_BROLL_SOURCES", "")
    monkeypatch.setattr(finalize, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(finalize, "WORK_ROOT", work)
    monkeypatch.setattr(finalize, "KIT_RENDERS", renders)
    monkeypatch.setattr(finalize, "ALL_DIR", finals)
    return {"source": source, "wd": wd, "work": work, "auto": auto,
            "finals": finals, "renders": renders, "tmp": tmp_path}


def _project(library: dict, name: str, payload: bytes = b"video") -> Path:
    project = library["source"] / name
    (project / "renders").mkdir(parents=True)
    (project / "renders" / "final_sfx.mp4").write_bytes(payload)
    return project


def _picks(library: dict, clips: list[dict], *, sidecar: list[dict] | None = None) -> None:
    (library["wd"] / "clips.json").write_text(json.dumps(clips), encoding="utf-8")
    if sidecar is not None:
        (library["source"] / "_clips.json").write_text(json.dumps(sidecar), encoding="utf-8")


def _probe(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    assert command[0] == "ffprobe"
    if "format=duration" in command:
        return subprocess.CompletedProcess(command, 0, stdout="10.0\n")
    return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"streams": [
        {"codec_type": "video", "width": 1080, "height": 1920},
        {"codec_type": "audio"}]}))


def test_canonical_picks_override_stale_sidecar_and_old_folders(library: dict) -> None:
    active = _project(library, "short-01-current")
    (active / "renders" / "final_remotion.mp4").write_bytes(b"current base")
    _project(library, "short-01-old")
    _project(library, "short-02-held")
    _picks(library, [{"rank": 1, "slug": "current"}],
           sidecar=[{"rank": 1, "slug": "old"}, {"rank": 2, "slug": "held"}])
    assert list(finalize.scoped_projects(None)) == [(library["source"], active)]
    assert finalize.find_render("source", 1)[0] == active


def test_legacy_projects_still_work_without_pick_files(library: dict) -> None:
    legacy = _project(library, "short-01-legacy")
    orphan_export = library["finals"] / "source__short-02-old-library-file.mp4"
    orphan_export.write_bytes(b"legacy export without pick metadata")
    assert project_selection.select_projects(library["source"], library["work"]) == [legacy]
    assert list(finalize.scoped_projects(None)) == [(library["source"], legacy)]
    assert orphan_export.exists()


def test_sidecar_is_used_when_canonical_picks_are_absent(library: dict) -> None:
    active = _project(library, "short-01-current")
    _project(library, "short-02-held")
    (library["source"] / "_clips.json").write_text(
        json.dumps([{"rank": 1, "slug": "current"}]), encoding="utf-8")
    assert project_selection.select_projects(library["source"], library["work"]) == [active]


def test_old_rank_only_specs_require_an_unambiguous_project(library: dict) -> None:
    current = _project(library, "short-01-current")
    _picks(library, [{"rank": 1}])
    assert project_selection.select_projects(library["source"], library["work"]) == [current]
    _project(library, "short-01-old")
    with pytest.raises(ValueError, match="ambiguous"):
        project_selection.select_projects(library["source"], library["work"])


def test_held_picks_and_existing_exports_are_preserved_outside_all(
    library: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(library, "short-01-current", b"approved")
    held = _project(library, "short-02-held", b"held original")
    _picks(library, [{"rank": 1, "slug": "current"}])
    for suffix, content in [(".mp4", b"held export"), (".txt", b"caption"),
                            (".sources.json", b"credits")]:
        (library["finals"] / f"source__{held.name}{suffix}").write_bytes(content)
    monkeypatch.setattr(subprocess, "run", _probe)
    assert finalize.phase2_consolidate() == 1
    assert [p.name for p in library["finals"].iterdir()] == ["source__short-01-current.mp4"]
    saved = list((library["renders"] / "_held").rglob("*"))
    assert {p.read_bytes() for p in saved if p.is_file()} == {b"held export", b"caption", b"credits"}
    assert (held / "renders" / "final_sfx.mp4").read_bytes() == b"held original"


def test_empty_current_picks_hold_all_old_exports(library: dict) -> None:
    _project(library, "short-01-old")
    _picks(library, [], sidecar=[{"rank": 1, "slug": "old"}])
    exported = library["finals"] / "source__short-01-old.mp4"
    exported.write_bytes(b"preserve me")
    assert finalize.phase2_consolidate() == 0
    assert not exported.exists()
    assert next((library["renders"] / "_held").rglob("*.mp4")).read_bytes() == b"preserve me"


@pytest.mark.parametrize("raw", ["{broken", "{}", '[{"rank": true}]',
                                  '[{"rank": 1, "slug": "../escape"}]'])
def test_invalid_current_metadata_holds_exports_and_cli_fails(
    library: dict, monkeypatch: pytest.MonkeyPatch, raw: str,
) -> None:
    _project(library, "short-01-old")
    (library["wd"] / "clips.json").write_text(raw, encoding="utf-8")
    exported = library["finals"] / "source__short-01-old.mp4"
    exported.write_bytes(b"preserve me")
    monkeypatch.setattr(sys, "argv", ["finalize.py", "--skip-remotion", "--skip-sfx"])
    assert finalize.main() == 1
    assert not exported.exists()
    assert next((library["renders"] / "_held").rglob("*.mp4")).read_bytes() == b"preserve me"


def test_repeated_holds_preserve_both_files(library: dict) -> None:
    _picks(library, [])
    exported = library["finals"] / "source__short-01-old.mp4"
    exported.write_bytes(b"first")
    assert finalize.phase2_consolidate() == 0
    exported.write_bytes(b"second")
    assert finalize.phase2_consolidate() == 0
    assert {p.read_bytes() for p in (library["renders"] / "_held").rglob("*.mp4")} == {
        b"first", b"second"}


def test_scoped_finalize_keeps_other_source_exports(library: dict) -> None:
    _picks(library, [])
    other = library["auto"] / "other-source"
    other.mkdir()
    (other / "_clips.json").write_text("[]", encoding="utf-8")
    other_export = library["finals"] / "other-source__short-01-current.mp4"
    other_export.write_bytes(b"another source")
    assert finalize.phase2_consolidate(slugs={"source"}) == 0
    assert other_export.read_bytes() == b"another source"


@pytest.mark.parametrize("with_events", [True, False])
def test_failed_sound_mix_holds_previous_export_and_stops(
    library: dict, monkeypatch: pytest.MonkeyPatch, with_events: bool,
) -> None:
    project = _project(library, "short-01-current", b"old sound render")
    fresh = project / "renders" / "final_remotion.mp4"
    fresh.write_bytes(b"new captions")
    clip = {"rank": 1, "slug": "current"}
    if with_events:
        clip["hook"] = {"text": "Hook"}
    _picks(library, [clip])
    (library["wd"] / "cut_manifests.json").write_text('[{"rank": 1}]', encoding="utf-8")
    exported = library["finals"] / "source__short-01-current.mp4"
    exported.write_bytes(b"old export")

    def failed_mix(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        if command[0] == "ffprobe":
            return _probe(command, **kwargs)
        assert command[0] == "ffmpeg"
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(1, command, stderr="copy failed")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="mix failed")

    monkeypatch.setattr(subprocess, "run", failed_mix)
    with pytest.raises(RuntimeError, match="[Ss]ound|SFX"):
        finalize.phase1_sfx(Config(), {"hook-impact": [library["tmp"] / "impact.wav"]})
    assert not exported.exists()
    assert (project / "renders" / "final_sfx.mp4").read_bytes() == b"old sound render"


def test_stale_sound_render_is_blocked_with_default_sources_off(library: dict) -> None:
    project = _project(library, "short-01-current", b"old sound")
    _picks(library, [{"rank": 1, "slug": "current"}])
    fresh = project / "renders" / "final_remotion.mp4"
    fresh.write_bytes(b"new video")
    old = project / "renders" / "final_sfx.mp4"
    os.utime(old, ns=(fresh.stat().st_atime_ns, fresh.stat().st_mtime_ns - 1_000_000))
    exported = library["finals"] / "source__short-01-current.mp4"
    exported.write_bytes(b"old export")
    with pytest.raises(RuntimeError, match="stale"):
        finalize.phase2_consolidate()
    assert not exported.exists()


def test_skip_sound_uses_current_video_even_when_old_sound_file_exists(
    library: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(library, "short-01-current", b"old sound")
    _picks(library, [{"rank": 1, "slug": "current"}])
    (project / "renders" / "final_remotion.mp4").write_bytes(b"new video")
    monkeypatch.setattr(subprocess, "run", _probe)
    assert finalize.phase2_consolidate(allow_unmixed=True) == 1
    assert next(library["finals"].glob("*.mp4")).read_bytes() == b"new video"


@pytest.mark.parametrize("streams", [[], [{"codec_type": "video", "width": 1080, "height": 1920}],
                                     [{"codec_type": "video", "width": 640, "height": 360},
                                      {"codec_type": "audio"}]])
def test_bad_qa_holds_export_and_cli_returns_failure(
    library: dict, monkeypatch: pytest.MonkeyPatch, streams: list[dict],
) -> None:
    _project(library, "short-01-current", b"bad video")
    _picks(library, [{"rank": 1, "slug": "current"}])
    exported = library["finals"] / "source__short-01-current.mp4"
    exported.write_bytes(b"old export")

    def bad_probe(command: list[str], **kwargs) -> subprocess.CompletedProcess:
        assert command[0] == "ffprobe"
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"streams": streams}))

    monkeypatch.setattr(subprocess, "run", bad_probe)
    monkeypatch.setattr(sys, "argv", ["finalize.py", "--skip-remotion", "--skip-sfx"])
    assert finalize.main() == 1
    assert not exported.exists()
    assert next((library["renders"] / "_held").rglob("*.mp4")).read_bytes() == b"old export"


@pytest.mark.parametrize("module", [batch_pipeline, reprocess_all])
def test_batch_renderers_ignore_old_and_held_projects(
    library: dict, monkeypatch: pytest.MonkeyPatch, module,
) -> None:
    active = _project(library, "short-01-current")
    _project(library, "short-01-old")
    _project(library, "short-02-held")
    _picks(library, [{"rank": 1, "slug": "current"}])
    videos = library["tmp"] / "videos"
    videos.mkdir()
    (videos / "source.mp4").write_bytes(b"source")
    monkeypatch.setattr(module, "VIDEO_DIR", videos)
    monkeypatch.setattr(module, "WORK_ROOT", library["work"])
    monkeypatch.setattr(module, "AUTO_SHORTS_ROOT", library["auto"])
    monkeypatch.setattr(module, "KIT_ROOT", library["tmp"] / "kit")
    monkeypatch.setattr(sys, "argv", [module.__name__ + ".py"])
    calls = []

    def run(command, **kwargs) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert module.main() == 0
    renders = [call for call in calls if isinstance(call, str)]
    assert len(renders) == 1
    assert active.name in renders[0]
    assert "short-01-old" not in renders[0] and "short-02-held" not in renders[0]


@pytest.mark.parametrize("manual", [True, False])
def test_same_size_person_photo_replacement_changes_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manual: bool,
) -> None:
    people = tmp_path / "people"
    location = people / "manual" if manual else people
    location.mkdir(parents=True)
    monkeypatch.setattr(gen_broll, "PEOPLE_DIR", people)
    monkeypatch.setattr(gen_broll, "MANUAL_DIR", people / "manual")
    monkeypatch.setattr(gen_broll, "PEOPLE_MANIFEST", people / "manifest.json")
    photo = location / "davidschwartz.jpg"
    words = [{"text": "David", "start": 0, "end": 0.4},
             {"text": "Schwartz", "start": 0.5, "end": 0.9}]
    photo.write_bytes(b"OLD PIXELS")
    before = render_stamp.people_digest(words)
    photo.write_bytes(b"NEW PIXELS")
    assert render_stamp.people_digest(words) != before
