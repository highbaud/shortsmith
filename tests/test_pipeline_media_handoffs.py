"""Real local media crosses the pipeline; only external ML engines are faked."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from shortsmith import (
    align,
    clean_clips,
    config,
    cut_clips,
    enhance_audio,
    pipeline,
    reframe,
    scaffold,
)
from shortsmith._process import resolve_executable
from shortsmith.config import Config


def run_media(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [resolve_executable("ffmpeg"), "-v", "error", "-y", *arguments],
        check=True, capture_output=True,
    )


def probe(path: Path) -> dict:
    result = subprocess.run(
        [resolve_executable("ffprobe"), "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        check=True, capture_output=True,
    )
    return json.loads(result.stdout)


def pixel(path: Path, seconds: float, x: int, y: int) -> tuple[int, ...]:
    result = run_media(
        "-ss", str(seconds), "-i", str(path), "-frames:v", "1", "-vf",
        f"format=rgb24,crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    )
    return tuple(result.stdout)


def peak_db(path: Path) -> float:
    result = subprocess.run(
        [resolve_executable("ffmpeg"), "-hide_banner", "-i", str(path),
         "-af", "volumedetect", "-vn", "-f", "null", "-"],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    match = re.search(r"max_volume: ([-\d.]+) dB", result.stderr)
    assert match is not None
    return float(match.group(1))


@pytest.fixture
def media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "four-colors.mp4"
    inputs = []
    for color in ("red", "green", "blue", "yellow"):
        inputs.extend(["-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=10:d=1"])
    run_media(
        *inputs, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
        "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0,"
        "drawbox=x=0:y=0:w=80:h=180:color=white:t=fill[video]",
        "-map", "[video]", "-map", "4:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    )
    monkeypatch.setattr(config, "AUTO_SHORTS_ROOT", tmp_path / "exports")
    monkeypatch.setattr(scaffold, "TEMPLATE_REF", tmp_path / "missing-template-reference")
    return source


def external_models(monkeypatch: pytest.MonkeyPatch, model_file: Path) -> None:
    import cv2

    class Detector:
        def detect(self, frame):
            # YuNet's external result locates a face inside the left white panel.
            return 1, np.array([[40, 30, 40, 60, *([0] * 10), 0.99]], dtype=np.float32)

    monkeypatch.setattr(cv2, "FaceDetectorYN", SimpleNamespace(create=lambda *args: Detector()))
    model_file.write_bytes(b"external detector fixture")

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            if Path(path).stem == "short-02":
                expected = [("Green.", 0.25, 0.75), ("Yellow.", 0.75, 1.65)]
            else:
                expected = [("Blue.", 0.05, 0.95), ("Red.", 0.95, 1.85)]
            words = [SimpleNamespace(word=text, start=start, end=end)
                     for text, start, end in expected]
            return [SimpleNamespace(words=words)], SimpleNamespace()

    def restore(**kwargs):
        if Path(kwargs["input"]).stem == "short-02_src":
            Path(kwargs["output"]).write_bytes(b"partial provider output")
            raise RuntimeError("external speech engine failed")
        shutil.copyfile(kwargs["input"], kwargs["output"])

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=WhisperModel))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
    ))
    monkeypatch.setitem(sys.modules, "voicefixer", SimpleNamespace(
        VoiceFixer=lambda: SimpleNamespace(restore=restore),
    ))
    actual_run = subprocess.run

    def without_external_engines(command, *args, **kwargs):
        if Path(command[0]).stem.lower() in {"resemble-enhance", "deepfilter"}:
            raise subprocess.CalledProcessError(1, command, stderr=b"external engine unavailable")
        return actual_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", without_external_engines)


def test_real_media_and_metadata_cross_all_stage_handoffs(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_file = tmp_path / "detector.onnx"
    external_models(monkeypatch, model_file)
    cfg = Config(
        story_check=False, seam_xfade_seconds=0.1, filler_pad_seconds=0.0,
        enhance_engine="voicefixer", loudness_enabled=True, align_engine="faster-whisper",
        yunet_model_path=model_file, style="minimal",
    )
    words = [
        {"text": "Red.", "start": 0.05, "end": 0.95},
        {"text": "um", "start": 1.05, "end": 1.25},
        {"text": "Green.", "start": 1.45, "end": 1.95},
        {"text": "Blue.", "start": 2.05, "end": 2.95},
        {"text": "Yellow.", "start": 3.05, "end": 3.95},
    ]
    clips = [
        {"rank": 2, "slug": "green-yellow", "start": 1.0, "end": 4.0,
         "segments": [[1.0, 2.0], [3.0, 4.0]], "instagram_caption": "Two colors. #tag",
         # Model-written text must reach the rendered HTML escaped.
         "hook": {"text": "Green first", "overline": "<img src=x onerror=alert(1)>"}},
        {"rank": 1, "slug": "blue-red", "start": 0.0, "end": 3.0,
         "segments": [[2.0, 3.0], [0.0, 1.0]], "instagram_caption": "Reordered colors."},
    ]
    work = tmp_path / "work"
    work.mkdir()
    (work / "transcript.json").write_text(json.dumps(words), encoding="utf-8")
    (work / "clips.json").write_text(json.dumps(clips), encoding="utf-8")
    original = media.read_bytes()
    source_peak = peak_db(media)

    manifests = cut_clips.cut_all(clips, words, media, work, cfg)
    saved = json.loads((work / "cut_manifests.json").read_text(encoding="utf-8"))
    assert [item["rank"] for item in saved] == [2, 1]
    assert [item["snapped_segments"] for item in saved] == [
        [[1.0, 2.0], [3.0, 4.0]], [[2.0, 3.0], [0.0, 1.0]],
    ]
    for item in manifests:
        item["editor_note"] = "preserve across resume"
    manifests = clean_clips.clean_all(manifests, words, work, cfg)
    assert len(manifests[0]["clean_cuts"]) == 1
    assert manifests[0]["clean_cuts"][0] == pytest.approx((0.05, 0.25))
    assert [word["text"] for word in manifests[0]["words_after_clean"]] == ["Green.", "Yellow."]
    assert manifests[0]["words_after_clean"][0]["start"] == pytest.approx(0.25)
    assert manifests[0]["words_after_clean"][1]["start"] == pytest.approx(0.75)
    manifests = enhance_audio.enhance_all(manifests, work, cfg)
    assert "using original audio" in caplog.text
    manifests = align.align_all(manifests, cfg)
    manifests = reframe.reframe_all(manifests, work, cfg)
    pipeline._save_manifests(manifests, work)
    resumed = json.loads((work / "cut_manifests.json").read_text(encoding="utf-8"))
    projects = scaffold.scaffold_all(resumed, clips, media, work, cfg.style)

    assert [project.name for project in projects] == ["short-02-green-yellow", "short-01-blue-red"]
    assert [item["editor_note"] for item in resumed] == ["preserve across resume"] * 2
    assert media.read_bytes() == original
    for item, project, expected_duration, expected_words, expected_span in zip(
        resumed, projects, (1.7, 1.9), (["Green.", "Yellow."], ["Blue.", "Red."]),
        ((0.25, 1.65), (0.05, 1.85)), strict=True,
    ):
        for key in ("raw_path", "cleaned_path", "enhanced_path", "words_path", "vertical_path"):
            assert Path(item[key]).is_file()
        video = project / "assets" / "clip-edit.mp4"
        assert video.read_bytes() == Path(item["vertical_path"]).read_bytes()
        info = probe(video)
        stream = next(part for part in info["streams"] if part["codec_type"] == "video")
        assert (stream["width"], stream["height"]) == (1080, 1920)
        assert any(part["codec_type"] == "audio" for part in info["streams"])
        assert peak_db(video) > source_peak
        # A 10 fps source allows one frame plus one 1024-sample AAC packet of timing error.
        assert float(info["format"]["duration"]) == pytest.approx(
            expected_duration, abs=0.1 + 1024 / 48000,
        )
        stored_words = json.loads((project / "assets" / "words.json").read_text(encoding="utf-8"))
        assert [word["text"] for word in stored_words] == expected_words
        assert (stored_words[0]["start"], stored_words[-1]["end"]) == expected_span
        metadata = json.loads((project / "meta.json").read_text(encoding="utf-8"))
        assert metadata["_shortsmith"]["source_segments"] == item["requested_segments"]
        assert metadata["_shortsmith"]["duration"] == float(info["format"]["duration"])
        html = (project / "index.html").read_text(encoding="utf-8")
        assert "assets/clip-edit.mp4" in html
        # The declared face must ship, or HyperFrames swaps Bebas Neue for League Gothic.
        assert "url('assets/fonts/BebasNeue-Regular.woff2')" in html
        assert (project / "assets/fonts/BebasNeue-Regular.woff2").stat().st_size > 10_000
        # back.out overshoots a blur to an invalid negative value that renders as flicker.
        assert not re.search(r"filter: 'blur\(0px\)'[^;]*back\.out", html)
        # GSAP owns transforms; a CSS translate on an animated clip is dropped by HyperFrames 0.9.
        assert "translateY(-50%)" not in html
        assert "<img src=x" not in html
        center = pixel(video, 0.3, 540, 960)
        assert all(channel > 0 for channel in center) and min(center) == max(center)
    hook_html = (projects[0] / "index.html").read_text(encoding="utf-8")
    assert "&lt;img src=x onerror=alert(1)&gt;" in hook_html
    first_green, later_yellow = pixel(projects[0] / "assets/clip-edit.mp4", 0.3, 1026, 960), pixel(
        projects[0] / "assets/clip-edit.mp4", 1.3, 1026, 960,
    )
    assert first_green[1] > first_green[0] and first_green[1] > first_green[2]
    assert later_yellow[0] > later_yellow[2] and later_yellow[1] > later_yellow[2]
    first_blue, later_red = pixel(projects[1] / "assets/clip-edit.mp4", 0.3, 1026, 960), pixel(
        projects[1] / "assets/clip-edit.mp4", 1.3, 1026, 960,
    )
    assert first_blue[2] > first_blue[0] and first_blue[2] > first_blue[1]
    assert later_red[0] > later_red[1] and later_red[0] > later_red[2]
    assert (projects[0] / "caption.txt").read_text(encoding="utf-8") == "Two colors.\n"


def test_missing_external_models_use_real_center_crop_and_empty_alignment(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class MissingWhisper:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("external alignment model unavailable")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=MissingWhisper))
    cfg = Config(align_engine="faster-whisper", yunet_model_path=tmp_path / "missing.onnx")
    manifests = [{"rank": 1, "raw_path": str(media)}]

    result = align.align_all(manifests, cfg)
    result = reframe.reframe_all(result, tmp_path, cfg)

    assert Path(result[0]["words_path"]).read_text(encoding="utf-8") == "[]"
    video = Path(result[0]["vertical_path"])
    stream = next(part for part in probe(video)["streams"] if part["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == (1080, 1920)
    red = pixel(video, 0.3, 540, 960)
    assert red[0] > red[1] and red[0] > red[2]
    assert "Alignment failed" in caplog.text
    assert "using center crop" in caplog.text


def test_reframe_modes_transform_real_frames_and_isolate_a_failed_clip(
    media: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_file = tmp_path / "detector.onnx"
    external_models(monkeypatch, model_file)
    gallery = tmp_path / "gallery.mp4"
    run_media(
        "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
        "-vf", "drawbox=x=10:y=10:w=140:h=150:color=white:t=fill,"
        "drawbox=x=170:y=10:w=140:h=150:color=gray:t=fill",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(gallery),
    )
    cfg = Config(yunet_model_path=model_file, reframe_scene_threshold=0.0)
    manifests = [
        {"rank": 99, "raw_path": str(tmp_path / "missing.mp4")},
        {"rank": 2, "raw_path": str(media), "multicam": True},
        {"rank": 3, "raw_path": str(gallery), "layout": "split-stack"},
    ]
    caplog.set_level("INFO", logger="shortsmith.reframe")

    result = reframe.reframe_all(manifests, tmp_path, cfg)

    assert result is manifests
    assert "vertical_path" not in result[0]
    assert "Center-crop fallback also failed" in caplog.text
    assert "per-shot crops computed" in caplog.text
    assert result[2]["layout"] == "split-stack"
    # Preset edges are 735px and 1015px, saved as fractions rounded to four decimals.
    assert result[2]["caption_band"] == {"top": 0.3828, "bottom": 0.5286}
    for item in result[1:]:
        video = Path(item["vertical_path"])
        stream = next(part for part in probe(video)["streams"] if part["codec_type"] == "video")
        assert (stream["width"], stream["height"]) == (1080, 1920)
    cut_aware = Path(result[1]["vertical_path"])
    blue = pixel(cut_aware, 2.3, 1026, 960)
    assert blue[2] > blue[0] and blue[2] > blue[1]
    stacked = Path(result[2]["vertical_path"])
    top, bottom = pixel(stacked, 0.3, 540, 382), pixel(stacked, 0.3, 540, 1367)
    assert sum(top) > sum(bottom) > 0
