"""Command tools preserve real local files and report failures without services."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import (
    check_no_tokens,
    clean_captions,
    ingest_premade,
    make_metadata,
    verify_deliverables,
)


def test_ingest_copies_input_order_and_saves_real_reused_word_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingest_premade, "__file__", str(tmp_path / "scripts/ingest_premade.py"))
    work = tmp_path / "work/source"
    premade = work / "premade"
    premade.mkdir(parents=True)
    first, second = tmp_path / "z-last-name.mp4", tmp_path / "a-first-name.mp4"
    first.write_bytes(b"first source media")
    second.write_bytes(b"second source media")
    for index, words in enumerate((
        [{"word": "Legacy", "start": 0.2, "end": 1.25, "note": "keep"}],
        [{"text": "Modern", "start": 0.0, "end": 2.5}],
    ), 1):
        (premade / f"transcript-short-{index:02d}.json").write_text(json.dumps(words))

    def unavailable_decode(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr=b"No external decoder in fixture")

    monkeypatch.setattr(subprocess, "run", unavailable_decode)
    monkeypatch.setattr(sys, "argv", ["ingest_premade.py", "source", str(first), str(second)])

    assert ingest_premade.main() == 0

    manifests = json.loads((work / "cut_manifests.json").read_text())
    assert [item["rank"] for item in manifests] == [1, 2]
    assert [item["source_file"] for item in manifests] == [first.name, second.name]
    for index, (item, expected) in enumerate(zip(manifests, (first, second), strict=True), 1):
        assert Path(item["raw_path"]).read_bytes() == expected.read_bytes()
        assert item["vertical_path"] == item["raw_path"]
        assert Path(item["words_path"]) == work / "words" / f"short-{index:02d}.json"
    assert json.loads(Path(manifests[0]["words_path"]).read_text()) == [
        {"word": "Legacy", "start": 0.2, "end": 1.25, "note": "keep"}]
    assert json.loads((work / "_premade_transcripts.json").read_text()) == [
        {"rank": 1, "source_file": first.name, "text": "Legacy", "duration": 1.25},
        {"rank": 2, "source_file": second.name, "text": "Modern", "duration": 2.5},
    ]


def test_ingest_missing_source_keeps_prior_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(ingest_premade, "__file__", str(tmp_path / "scripts/ingest_premade.py"))
    work = tmp_path / "work/source"
    work.mkdir(parents=True)
    prior = work / "cut_manifests.json"
    prior.write_text('[{"rank":7,"note":"preserve"}]')
    monkeypatch.setattr(sys, "argv", ["ingest_premade.py", "source", str(tmp_path / "missing.mp4")])

    assert ingest_premade.main() == 1
    assert prior.read_text() == '[{"rank":7,"note":"preserve"}]'
    assert "missing.mp4" in caplog.text


def test_secret_command_reports_redacted_locations_and_honors_line_opt_outs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    credential = "client_" + "a" * 32
    allowed = "sk-ant-" + "B" * 24
    source = tmp_path / "settings.txt"
    source.write_text("plain text\n" + credential + "\n" + allowed + " # pragma: allowlist secret\n")

    assert check_no_tokens.main([str(source)]) == 1

    result = capsys.readouterr()
    assert f"{source}:2" in result.err
    assert "Metricool client ID: clie...aaaa" in result.err
    assert "ERROR: 1 potential secret(s)" in result.err
    assert credential not in result.err and allowed not in result.err
    assert result.out == ""


def test_secret_command_skips_media_lockfiles_and_removed_files(tmp_path: Path) -> None:
    token = "Bearer " + "Z" * 35
    media, lock, clean = tmp_path / "cover.png", tmp_path / "package-lock.json", tmp_path / "safe.py"
    media.write_text(token)
    lock.write_text(token)
    clean.write_text("value = 'plain text'\n")

    assert check_no_tokens.main([str(media), str(lock), str(clean), str(tmp_path / "deleted.py")]) == 0


def test_secret_command_cannot_report_clean_when_a_source_file_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "settings.py"
    source.write_text("safe fixture")
    read = Path.read_text

    def denied(path, *args, **kwargs):
        if path == source:
            raise PermissionError("file is locked")
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    assert check_no_tokens.main([str(source)]) == 1
    assert "file is locked" in capsys.readouterr().err


def test_caption_cleanup_dry_run_then_write_keeps_json_shape_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    auto, kit, work = tmp_path / "auto", tmp_path / "kit", tmp_path / "work"
    project = auto / "source/short-01-topic"
    project.mkdir(parents=True)
    exports = kit / "renders/_all"
    exports.mkdir(parents=True)
    (work / "source").mkdir(parents=True)
    caption = project / "caption.txt"
    caption.write_text("Value\u2014not noise. #topic\n", encoding="utf-8")
    exported_caption = exports / "source__short-01-topic.txt"
    exported_caption.write_text("Range 1\u20135.\n", encoding="utf-8")
    clip = {"rank": 1, "hook": {"text": "Price\u2014value"}, "tags": ["#keep", 3, None]}
    saved = work / "source/clips.json"
    saved.write_text(json.dumps([clip]), encoding="utf-8")
    malformed = auto / "source/_clips.json"
    malformed.write_text("{", encoding="utf-8")
    monkeypatch.setattr(clean_captions, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(clean_captions, "KIT_ROOT", kit)
    monkeypatch.setattr(clean_captions, "WORK_ROOT", work)
    originals = {path: path.read_bytes() for path in (caption, exported_caption, saved, malformed)}
    monkeypatch.setattr(sys, "argv", ["clean_captions.py", "--dry-run"])

    clean_captions.main()

    assert {path: path.read_bytes() for path in originals} == originals
    assert "would change: 2 caption .txt, 1 clip-json files" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["clean_captions.py"])
    clean_captions.main()
    assert caption.read_text() == "Value, not noise.\n"
    assert exported_caption.read_text() == "Range 1-5.\n"
    assert json.loads(saved.read_text()) == [
        {"rank": 1, "hook": {"text": "Price, value"}, "tags": ["#keep", 3, None]}]
    assert malformed.read_text() == "{"
    assert "skip" in capsys.readouterr().out
    clean_captions.main()
    assert "changed: 0 caption .txt, 0 clip-json files" in capsys.readouterr().out


@pytest.fixture
def published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "renders/_all"
    folder.mkdir(parents=True)
    monkeypatch.setattr(verify_deliverables, "ALL_DIR", folder)
    monkeypatch.setattr(verify_deliverables, "KIT_RENDERS", folder.parent)
    monkeypatch.setattr(verify_deliverables.shutil, "which", lambda _: "available ffprobe")
    return folder


def test_deliverable_qa_warning_and_failure_status_use_real_sidecars(
    published: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    video = published / "source__short-01.mp4"
    video.write_bytes(b"render")
    video.with_suffix(".txt").write_text("Caption body\n")
    info = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920},
                        {"codec_type": "audio"}], "format": {"duration": "3.0"}}
    monkeypatch.setattr(verify_deliverables, "resolve_executable", lambda _: "ffprobe")

    def probe(command, **kwargs):
        assert command[-1] == str(video)
        assert kwargs["check"] is True and kwargs["encoding"] == "utf-8"
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(info))

    monkeypatch.setattr(subprocess, "run", probe)
    assert verify_deliverables.main() == 0
    output = capsys.readouterr().out
    assert "missing metadata .json" in output and "missing thumbnail .jpg" in output
    assert "0 ok, 1 warn, 0 FAIL" in output
    video.with_suffix(".json").write_text('{"title":"Title","description":"Body"}')
    video.with_suffix(".jpg").write_bytes(b"thumbnail")
    assert verify_deliverables.main() == 0
    assert "1 ok, 0 warn, 0 FAIL" in capsys.readouterr().out
    video.with_suffix(".json").write_text("{")
    assert verify_deliverables.main() == 1
    assert "metadata .json invalid" in capsys.readouterr().out


def test_deliverable_qa_reports_missing_probe_before_processing_files(
    published: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    (published / "source.mp4").write_bytes(b"render")
    monkeypatch.setattr(verify_deliverables.shutil, "which", lambda _: None)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected probe"))

    assert verify_deliverables.main() == 1
    assert "ffprobe not on PATH" in capsys.readouterr().out


@pytest.mark.parametrize("problem", ["zero bytes", "no audio", "landscape", "short"])
def test_deliverable_qa_rejects_broken_media(
    published: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], problem: str,
) -> None:
    video = published / "broken.mp4"
    video.write_bytes(b"" if problem == "zero bytes" else b"render")
    video.with_suffix(".txt").write_text("Caption")
    streams = [{"codec_type": "video", "width": 1920 if problem == "landscape" else 1080,
                "height": 1080 if problem == "landscape" else 1920}]
    if problem != "no audio":
        streams.append({"codec_type": "audio"})
    info = {"streams": streams, "format": {"duration": "2.9" if problem == "short" else "4.0"}}
    monkeypatch.setattr(verify_deliverables, "resolve_executable", lambda _: "ffprobe")
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(
        command, 0, stdout=json.dumps(info)))

    assert verify_deliverables.main() == 1
    assert "0 ok, 0 warn, 1 FAIL" in capsys.readouterr().out


def test_metadata_command_uses_caption_render_priority_entities_and_copies_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    auto, exports = tmp_path / "auto", tmp_path / "renders/_all"
    project = auto / "source/short-02-topic"
    renders = project / "renders"
    renders.mkdir(parents=True)
    exports.mkdir(parents=True)
    final = renders / "final_sfx.mp4"
    final.write_bytes(b"approved sound mix")
    (renders / "final_remotion.mp4").write_bytes(b"base render")
    (project / "caption.txt").write_text("Saved caption\u2014ready.\n", encoding="utf-8")
    (project.parent / "_clips.json").write_text(json.dumps([{
        "rank": 2, "hook_text": "A\u2014B", "hook": {"overline": "Finance"},
        "instagram_caption": "Unused fallback", "reasoning": "Why it works", "viral_score": 9,
        "callouts": [{"text": "1\u20135"}],
    }]))
    (project / "broll.auto.json").write_text(json.dumps([
        {"entity": "Ripple"}, {"name": "ripple"}, {"label": "Alice"}]))
    base = "source__short-02-topic"
    thumb = exports / f"{base}.jpg"
    thumb.write_bytes(b"cover")
    monkeypatch.setattr(make_metadata, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(make_metadata, "ALL_DIR", exports)

    def probe(command, **kwargs):
        assert command[-1] == str(final)
        return subprocess.CompletedProcess(command, 0, stdout="12.346\n")

    monkeypatch.setattr(subprocess, "run", probe)
    assert make_metadata.main() == 0
    saved = json.loads((project / "metadata.json").read_text())
    assert saved == {
        "source": "source", "short": "short-02-topic", "rank": 2,
        "title": "A, B", "label": "Finance", "description": "Saved caption, ready.",
        "summary": "Why it works", "keywords": ["Finance", "Ripple", "Alice"],
        "callouts": ["1-5"], "viral_score": 9, "duration_seconds": 12.35,
        "width": 1080, "height": 1920, "thumbnail": str(thumb), "deliverable": str(final),
    }
    assert (project / "metadata.json").read_bytes() == (exports / f"{base}.json").read_bytes()


@pytest.mark.parametrize("broll", [None, {"slides": None}])
def test_metadata_optional_malformed_broll_does_not_block_saved_clip_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], broll: object,
) -> None:
    auto = tmp_path / "auto"
    project = auto / "source/short-01-topic"
    project.mkdir(parents=True)
    (project.parent / "_clips.json").write_text('[{"rank":1,"hook_text":"Title","instagram_caption":"Body"}]')
    (project / "broll.auto.json").write_text(json.dumps(broll))
    monkeypatch.setattr(make_metadata, "AUTO_SHORTS_ROOT", auto)
    monkeypatch.setattr(make_metadata, "ALL_DIR", tmp_path / "absent")

    assert make_metadata.main() == 0
    saved = json.loads((project / "metadata.json").read_text())
    assert saved["title"] == "Title" and saved["description"] == "Body"
    assert saved["keywords"] == [] and saved["duration_seconds"] is None
    assert "warning:" in capsys.readouterr().out
