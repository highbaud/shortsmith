"""Generate transcript.formatted.txt for every work dir that has transcript.json
but no formatted version yet.

Format: ~10s blocks with `[t=Ns]` markers, max 350 chars per block.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parent.parent / "work"


def format_one(words: list[dict]) -> str:
    out: list[str] = []
    buf: list[str] = []
    if not words:
        return ""
    buf_start = words[0]["start"]
    last_t_marker = -10
    for w in words:
        if w["start"] - buf_start > 10 or len(" ".join(buf)) > 350:
            marker_t = int(buf_start)
            if marker_t - last_t_marker >= 9:
                out.append(f"\n[t={marker_t}s]")
                last_t_marker = marker_t
            out.append(" ".join(buf))
            buf = []
            buf_start = w["start"]
        buf.append(w["text"])
    if buf:
        marker_t = int(buf_start)
        out.append(f"\n[t={marker_t}s]")
        out.append(" ".join(buf))
    return "\n".join(out)


def main() -> int:
    count = 0
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        tj = wd / "transcript.json"
        if not tj.exists():
            continue
        ft = wd / "transcript.formatted.txt"
        if ft.exists() and ft.stat().st_size > 100:
            continue
        try:
            words = json.loads(tj.read_text(encoding="utf-8"))
            if not isinstance(words, list) or not words:
                continue
            text = format_one(words)
            ft.write_text(text, encoding="utf-8")
            count += 1
            print(f"[{count}] {wd.name}: {len(words)} words, {words[-1]['end']/60:.0f}min")
        except Exception as e:
            print(f"FAILED {wd.name}: {e}")
    print(f"Formatted {count} transcripts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
