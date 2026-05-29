"""Post-finalize QA: scan the consolidated deliverables and flag anything wrong,
so silent failures across hundreds of shorts become one readable report.

For every `<kit>/renders/_all/*.mp4` it checks:
  * file exists and is non-zero
  * a 1080x1920 video stream is present
  * an audio stream is present
  * duration is sane (>= MIN_SECONDS)
  * a non-empty caption  `<base>.txt`  sits beside it
  * a valid       metadata `<base>.json` with title + description
  * a thumbnail   `<base>.jpg`           (warn-only — thumbnails are optional)

Prints a per-file issue list + totals + free-disk summary, and exits non-zero if
any HARD problem is found (missing/zero mp4, no audio, wrong dims, short/again
duration), so it can gate a release or run in CI.

Usage:
    uv run python scripts/verify_deliverables.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from shortsmith.config import AUTO_SHORTS_ROOT

KIT_RENDERS = AUTO_SHORTS_ROOT.parent.parent / "renders"
ALL_DIR = KIT_RENDERS / "_all"
MIN_SECONDS = 3.0
TARGET_DIMS = (1080, 1920)


def _probe(p: Path) -> tuple[bool, int, int, float]:
    """(has_audio, width, height, duration)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height:format=duration",
             "-of", "json", str(p)],
            check=True, capture_output=True, text=True)
        info = json.loads(out.stdout)
        streams = info.get("streams", [])
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        vid = next((s for s in streams if s.get("codec_type") == "video"), {})
        dur = float(info.get("format", {}).get("duration", 0) or 0)
        return has_audio, int(vid.get("width", 0) or 0), int(vid.get("height", 0) or 0), dur
    except Exception:
        return False, 0, 0, 0.0


def main() -> int:
    if not ALL_DIR.exists():
        print(f"No deliverables dir yet: {ALL_DIR}\n(run finalize.py first)")
        return 1

    mp4s = sorted(ALL_DIR.glob("*.mp4"))
    if not mp4s:
        print(f"{ALL_DIR} exists but has no .mp4 deliverables.")
        return 1

    hard = warn = ok = 0
    for mp4 in mp4s:
        base = mp4.stem
        issues_hard: list[str] = []
        issues_warn: list[str] = []

        if mp4.stat().st_size == 0:
            issues_hard.append("zero-byte mp4")
        else:
            has_audio, w, h, dur = _probe(mp4)
            if not has_audio:
                issues_hard.append("no audio stream")
            if (w, h) != TARGET_DIMS:
                issues_hard.append(f"dims {w}x{h} != 1080x1920")
            if dur < MIN_SECONDS:
                issues_hard.append(f"duration {dur:.1f}s < {MIN_SECONDS}s")

        cap = ALL_DIR / f"{base}.txt"
        if not cap.exists() or not cap.read_text(encoding="utf-8").strip():
            issues_hard.append("missing/empty caption .txt")

        meta = ALL_DIR / f"{base}.json"
        if not meta.exists():
            issues_warn.append("missing metadata .json")
        else:
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                if not (m.get("title") and m.get("description")):
                    issues_warn.append("metadata missing title/description")
            except Exception:
                issues_hard.append("metadata .json invalid")

        if not (ALL_DIR / f"{base}.jpg").exists():
            issues_warn.append("missing thumbnail .jpg")

        if issues_hard:
            hard += 1
            print(f"FAIL  {base}: {'; '.join(issues_hard + issues_warn)}")
        elif issues_warn:
            warn += 1
            print(f"warn  {base}: {'; '.join(issues_warn)}")
        else:
            ok += 1

    du = shutil.disk_usage(KIT_RENDERS)
    print(f"\n{len(mp4s)} deliverables: {ok} ok, {warn} warn, {hard} FAIL")
    print(f"disk: {du.free / 2**30:.1f} GB free of {du.total / 2**30:.1f} GB "
          f"(renders dir: {sum(f.stat().st_size for f in ALL_DIR.glob('*.mp4')) / 2**30:.1f} GB)")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
