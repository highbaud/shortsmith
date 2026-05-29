"""Hook-sting thumbnail pass: give every short a cover that matches its opening
text/logo card.

For each short's final deliverable this does THREE things ("both" mode):
  1. extracts the hook-card frame -> a sidecar `<source>__<short>.jpg` (upload as
     a custom cover on IG / YouTube),
  2. holds that frame as a short FREEZE at the very start so auto-thumbnail
     pickers / first-frame defaults land on the hook (audio is delayed by the
     same beat, so A/V stays in sync),
  3. embeds the frame as the MP4 poster (attached_pic) for file players / CMSes.

Run AFTER finalize (or wire as finalize Phase 3). Idempotent: a short whose
deliverable already carries the thumbnail is marked with renders/.thumb_done and
skipped unless --force.

Usage:
    uv run python scripts/make_thumbnails.py                 # batch, freeze on
    uv run python scripts/make_thumbnails.py --no-freeze     # poster + jpg only
    uv run python scripts/make_thumbnails.py --video X.mp4 --hook-t 1.0   # test one file
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_remotion as rr  # noqa: E402  (_overlay_windows, _probe_duration)

from shortsmith.config import AUTO_SHORTS_ROOT  # noqa: E402

KIT_RENDERS = AUTO_SHORTS_ROOT.parent.parent / "renders"
ALL_DIR = KIT_RENDERS / "_all"
FREEZE_SECONDS = 0.5
W, H, FPS = 1080, 1920, 30


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def _deliverable(proj: Path) -> Path | None:
    r = proj / "renders"
    for name in ("final_sfx.mp4", "final_remotion.mp4"):
        if (r / name).exists():
            return r / name
    cands = [p for p in r.glob("*.mp4") if not p.stem.startswith("_")] if r.is_dir() else []
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _hook_time(proj: Path, dur: float) -> float:
    """Mid-point of the opening (hook) overlay window; fall back to ~1s."""
    try:
        wins = rr._overlay_windows(proj, dur)
    except Exception:
        wins = []
    t = (wins[0]["start"] + wins[0]["end"]) / 2.0 if wins else min(1.0, dur * 0.1)
    return max(0.3, min(t, dur - 0.1))


def apply_thumbnail(deliverable: Path, hook_t: float, freeze: bool,
                    sidecar_jpg: Path | None) -> Path:
    """Extract the hook frame, optionally freeze it at the start, embed it as the
    poster, and replace `deliverable` in place. Returns the hook .jpg path."""
    r = deliverable.parent
    thumb = r / "thumb.jpg"
    _run(["ffmpeg", "-y", "-ss", f"{hook_t}", "-i", str(deliverable),
          "-frames:v", "1", "-q:v", "2", str(thumb)])
    if sidecar_jpg:
        sidecar_jpg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(thumb, sidecar_jpg)

    src = deliverable
    freeze_clip = r / "_freeze.mp4"
    concat_out = r / "_thumb_concat.mp4"
    if freeze:
        # Silent freeze of the hook frame, matched to the render's specs so the
        # concat filter (which requires identical w/h/fps/sr) joins cleanly.
        _run(["ffmpeg", "-y", "-loop", "1", "-i", str(thumb),
              "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
              "-t", f"{FREEZE_SECONDS}", "-r", str(FPS), "-s", f"{W}x{H}",
              "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac",
              "-shortest", str(freeze_clip)])
        _run(["ffmpeg", "-y", "-i", str(freeze_clip), "-i", str(deliverable),
              "-filter_complex",
              "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
              "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac",
              "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(concat_out)])
        freeze_clip.unlink(missing_ok=True)
        src = concat_out

    # Embed the hook frame as the MP4 poster (attached_pic).
    poster_out = r / "_thumb_poster.mp4"
    _run(["ffmpeg", "-y", "-i", str(src), "-i", str(thumb),
          "-map", "0", "-map", "1", "-c", "copy", "-c:v:1", "mjpeg",
          "-disposition:v:1", "attached_pic", "-movflags", "+faststart",
          str(poster_out)])
    if src != deliverable:
        src.unlink(missing_ok=True)
    poster_out.replace(deliverable)
    return thumb


def process(proj: Path, freeze: bool, force: bool) -> bool:
    deliv = _deliverable(proj)
    if not deliv:
        return False
    marker = proj / "renders" / ".thumb_done"
    if marker.exists() and not force:
        return False
    dur = rr._probe_duration(deliv)
    hook_t = _hook_time(proj, dur)
    base = f"{proj.parent.name}__{proj.name}"
    sidecar = (ALL_DIR / f"{base}.jpg") if ALL_DIR.exists() else (proj / "renders" / "thumb.jpg")
    apply_thumbnail(deliv, hook_t, freeze, sidecar)
    if ALL_DIR.exists():  # refresh the consolidated copy with the new poster/freeze
        shutil.copy(deliv, ALL_DIR / f"{base}.mp4")
    marker.write_text("done\n", encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Hook-sting thumbnails (sidecar jpg + freeze + poster).")
    ap.add_argument("--no-freeze", dest="freeze", action="store_false",
                    help="Skip the opening freeze; only sidecar jpg + embedded poster.")
    ap.add_argument("--force", action="store_true", help="Re-apply even if already marked done.")
    ap.add_argument("--video", type=Path, help="Test mode: apply to one mp4 directly.")
    ap.add_argument("--hook-t", type=float, default=1.0, help="Test mode hook frame time (s).")
    args = ap.parse_args()

    if args.video:  # single-file test path
        apply_thumbnail(args.video, args.hook_t, args.freeze,
                        args.video.with_suffix(".jpg"))
        print(f"Applied thumbnail to {args.video}")
        return 0

    done = 0
    for src_dir in sorted(AUTO_SHORTS_ROOT.iterdir()):
        if not src_dir.is_dir():
            continue
        for proj in sorted(src_dir.glob("short-*")):
            if proj.is_dir() and process(proj, args.freeze, args.force):
                done += 1
    print(f"Thumbnailed {done} shorts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
