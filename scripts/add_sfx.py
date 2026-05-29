"""Post-render SFX pass over every rendered short.

For each work dir with clips.json + cut_manifests.json, find each clip's
rendered final.mp4, compute its SFX events (swipes on callouts, hook impact,
sparing cash-register/ding), and mix them in -> final_sfx.mp4 (non-destructive).

Run AFTER renders exist. Re-runnable: regenerates final_sfx.mp4 each time, so
tweak the sounds / config and re-run freely.

Usage:
    uv run python scripts/add_sfx.py            # all rendered shorts
    uv run python scripts/add_sfx.py --overwrite  # replace final.mp4 in place
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from shortsmith import sfx
from shortsmith.config import AUTO_SHORTS_ROOT, Config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("add_sfx")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"


def probe_duration(p: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
            check=True, capture_output=True, text=True)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


KIT_RENDERS = AUTO_SHORTS_ROOT.parent.parent / "renders"  # <kit>/renders


def find_render(work_slug: str, rank: int) -> tuple[Path, Path] | None:
    """Find the newest rendered short for a work dir + clip rank.

    Renders land in two places depending on Hyperframes version / invocation:
      * the project's own renders/  (final.mp4 / final_remotion.mp4)
      * the kit-level renders/      (<project-name>_<timestamp>.mp4)
    We scan both and return (project_dir, newest_source_mp4) so the SFX output
    can always be written into the project's renders/ for tidiness.
    """
    src_dir = AUTO_SHORTS_ROOT / work_slug
    if not src_dir.exists():
        return None
    for proj in src_dir.glob(f"short-{rank:02d}-*"):
        cands: list[Path] = []
        rdir = proj / "renders"
        if rdir.is_dir():
            cands += [p for p in rdir.glob("*.mp4") if p.stem != "final_sfx"]
        if KIT_RENDERS.is_dir():
            cands += list(KIT_RENDERS.glob(f"{proj.name}_*.mp4"))
        if cands:
            newest = max(cands, key=lambda p: p.stat().st_mtime)
            return proj, newest
    return None


def main() -> int:
    overwrite = "--overwrite" in sys.argv
    cfg = Config()
    sfx_map = sfx.load_sfx_map()
    if not sfx_map:
        log.error("No SFX files found in %s — drop sounds there first (see README).",
                  __import__("shortsmith.config", fromlist=["SFX_DIR"]).SFX_DIR)
        return 1
    log.info("SFX slots available: %s", ", ".join(sorted(sfx_map)))

    done = applied = skipped = failed = 0
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        clips_p, cm_p = wd / "clips.json", wd / "cut_manifests.json"
        if not (clips_p.exists() and cm_p.exists()):
            continue
        try:
            clips = json.loads(clips_p.read_text(encoding="utf-8"))
            manifests = json.loads(cm_p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(clips, list) or not clips:
            continue
        words_by_rank = {m["rank"]: m.get("words_path") for m in manifests}

        for clip in clips:
            rank = clip.get("rank")
            found = find_render(wd.name, rank)
            if not found:
                skipped += 1
                continue
            proj, final = found
            words = []
            wp = words_by_rank.get(rank)
            if wp and Path(wp).exists():
                try:
                    words = json.loads(Path(wp).read_text(encoding="utf-8"))
                except Exception:
                    words = []
            dur = probe_duration(final)
            events = sfx.plan_events(clip, words, sfx_map, cfg, dur)
            # Always write the SFX output into the project's renders/ dir.
            rdir = proj / "renders"
            rdir.mkdir(parents=True, exist_ok=True)
            out = final if (overwrite and final.parent == rdir) else rdir / "final_sfx.mp4"
            tmp = rdir / "_sfx_tmp.mp4"
            ok = sfx.apply_sfx(final, events, sfx_map, tmp, cfg)
            if ok:
                tmp.replace(out)
                applied += 1
                if applied <= 5 or applied % 50 == 0:
                    log.info("%s/short-%02d: %d events -> %s",
                             wd.name[:28], rank, len(events), out.name)
            else:
                tmp.unlink(missing_ok=True)
                failed += 1
        done += 1

    log.info("DONE. work_dirs=%d applied=%d skipped(no render)=%d failed=%d",
             done, applied, skipped, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
