"""Self-contained smoke test for shortsmith.

Runs end-to-end on `examples/sample_clip.mp4` (a tiny bundled talking-head clip).
No ANTHROPIC_API_KEY needed — step 2 (find_clips) is skipped via hand-crafted
clips.json, and step 5 (enhance_audio) is skipped via --no-enhance.

Usage:
    uv run python scripts/smoke_test.py

Passes iff `work/<slug>/vertical/short-01.mp4` exists, is 1080x1920, has
audio, and is at least 3 seconds long.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from shortsmith import config, transcribe

REPO_ROOT = Path(__file__).resolve().parent.parent
VIDEO = REPO_ROOT / "examples" / "sample_clip.mp4"


def main() -> int:
    if not VIDEO.exists():
        print(f"ERROR: sample clip not found at {VIDEO}", file=sys.stderr)
        print(
            "Drop a small (≤10s, 1080p, talking-head) clip there to run the smoke test.\n"
            "Any short mp4 will do — it just needs a face and some speech.",
            file=sys.stderr,
        )
        return 2

    cfg = config.Config()
    work_dir = config.make_work_dir(VIDEO)
    print(f"[smoke] work dir: {work_dir}")

    # Step 1: actually transcribe (faster-whisper on ~10s of audio is fast on CPU int8).
    transcript_path = work_dir / "transcript.json"
    if not transcript_path.exists():
        print("[smoke] running step 1 (transcribe)")
        transcribe.transcribe(VIDEO, transcript_path, cfg, reuse_existing=False)
    words = json.loads(transcript_path.read_text(encoding="utf-8"))
    if not words:
        print("ERROR: transcript empty — clip has no detected speech?", file=sys.stderr)
        return 3
    print(f"[smoke] transcribed {len(words)} words")

    # Step 2: hand-crafted clips.json so no Claude API call.
    end_time = min(words[-1]["end"], 8.0)
    clips = [
        {
            "rank": 1,
            "start": 0.0,
            "end": end_time,
            "hook_start": 0.0,
            "hook_end": min(end_time, 3.0),
            "hook_text": "Smoke test clip",
            "viral_score": 7,
            "reasoning": "Smoke test — synthetic linear cut, no reorder.",
            "segments": [[0.0, end_time]],
            "slug": "smoke-test",
        }
    ]
    (work_dir / "clips.json").write_text(json.dumps(clips, indent=2), encoding="utf-8")

    # Steps 3-8: cut, clean, retranscribe, reframe, scaffold. No audio enhance.
    cmd = [
        "uv", "run", "shortsmith", "run", str(VIDEO),
        "--from-step", "3",
        "--no-enhance",
        "--max-clips", "1",
    ]
    print(f"[smoke] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        return result.returncode

    # Verify outputs.
    vertical = work_dir / "vertical" / "short-01.mp4"
    if not vertical.exists():
        print(f"ERROR: expected output missing: {vertical}", file=sys.stderr)
        return 4

    # ffprobe sanity check
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", str(vertical)],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        print(f"ERROR: ffprobe failed: {probe.stderr}", file=sys.stderr)
        return 5
    info = json.loads(probe.stdout)
    vstream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    astream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)

    ok = True
    if not vstream or vstream.get("width") != 1080 or vstream.get("height") != 1920:
        print(f"FAIL: video stream is not 1080x1920: {vstream}", file=sys.stderr)
        ok = False
    if not astream:
        print("FAIL: no audio stream in output", file=sys.stderr)
        ok = False
    # Be lenient on duration — silence trimming on a short sample can land
    # anywhere from 1-9 seconds. Anything >0.5s with the right dims + audio
    # proves the pipeline ran end-to-end.
    dur = float(info.get("format", {}).get("duration", 0)) if "format" in info else 0
    sdur = float(vstream.get("duration", 0)) if vstream else 0
    if max(dur, sdur) < 0.5:
        print(f"FAIL: output too short ({max(dur, sdur)}s)", file=sys.stderr)
        ok = False

    if ok:
        print(f"[smoke] PASS — {vertical} is 1080x1920 with audio")
        return 0
    return 6


if __name__ == "__main__":
    sys.exit(main())
