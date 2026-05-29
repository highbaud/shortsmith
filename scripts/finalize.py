"""Final-version pass: layer Remotion (captions + auto b-roll) onto every short,
apply approved SFX, then consolidate all finals + captions into one flat folder.

Run AFTER scripts/reprocess_all.py has fully finished (so every short has its
final, new-pipeline Hyperframes render). Idempotent + re-runnable.

  Phase 0: for every scaffolded short with a Hyperframes base render, regenerate
           heuristic b-roll and render captions + b-roll on top
           -> <project>/renders/final_remotion.mp4. Skips shorts already up to
           date and shorts with no base render yet.
  Phase 1: for every work dir with clips.json + cut_manifests.json, locate each
           short's SFX base (prefers final_remotion.mp4, else newest render),
           mix in SFX -> <project>/renders/final_sfx.mp4.
  Phase 2: copy every final_sfx.mp4 (+ matching caption.txt) into
           <kit>/renders/_all/<source-slug>__<short-slug>.(mp4|txt).

Usage:
    uv run python scripts/finalize.py
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from shortsmith import sfx
from shortsmith.config import AUTO_SHORTS_ROOT, Config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("finalize")

SHORTSMITH_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = SHORTSMITH_ROOT / "work"
KIT_RENDERS = AUTO_SHORTS_ROOT.parent.parent / "renders"
ALL_DIR = KIT_RENDERS / "_all"


def probe_duration(p: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
            check=True, capture_output=True, text=True)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def find_render(work_slug: str, rank: int) -> tuple[Path, Path] | None:
    src_dir = AUTO_SHORTS_ROOT / work_slug
    if not src_dir.exists():
        return None
    for proj in src_dir.glob(f"short-{rank:02d}-*"):
        # Prefer the Remotion output (captions + b-roll) as the SFX base so the
        # final carries everything; fall back to the raw newest render.
        remotion = proj / "renders" / "final_remotion.mp4"
        if remotion.exists():
            return proj, remotion
        cands: list[Path] = []
        rdir = proj / "renders"
        if rdir.is_dir():
            cands += [p for p in rdir.glob("*.mp4")
                      if p.stem != "final_sfx" and not p.stem.startswith("_")]
        if KIT_RENDERS.is_dir():
            cands += list(KIT_RENDERS.glob(f"{proj.name}_*.mp4"))
        if cands:
            return proj, max(cands, key=lambda p: p.stat().st_mtime)
    return None


def phase0_remotion(style: str) -> int:
    """Layer Remotion captions + auto b-roll onto every scaffolded short."""
    sys.path.insert(0, str(SHORTSMITH_ROOT / "scripts"))
    try:
        import apply_remotion as ar
    except Exception as e:  # noqa: BLE001
        log.error("Cannot import apply_remotion (%s); skipping Remotion phase", e)
        return 0
    applied = skipped = 0
    for src_dir in sorted(AUTO_SHORTS_ROOT.iterdir()):
        if not src_dir.is_dir():
            continue
        for proj in sorted(src_dir.glob("short-*")):
            if not proj.is_dir():
                continue
            try:
                out = ar.apply_remotion(proj, style=style)
            except Exception as e:  # noqa: BLE001 - one short shouldn't sink the run
                log.warning("  Remotion failed for %s: %s", proj.name, e)
                out = None
            if out:
                applied += 1
                if applied % 25 == 0:
                    log.info("  Remotion applied to %d shorts so far...", applied)
            else:
                skipped += 1
    log.info("Phase 0 done: Remotion applied/up-to-date=%d, skipped(no base)=%d",
             applied, skipped)
    return applied


def phase1_sfx(cfg: Config, sfx_map) -> int:
    applied = skipped = 0
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        cp, cm = wd / "clips.json", wd / "cut_manifests.json"
        if not (cp.exists() and cm.exists()):
            continue
        try:
            clips = json.loads(cp.read_text(encoding="utf-8"))
            mans = json.loads(cm.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(clips, list) or not clips:
            continue
        wbyrank = {m["rank"]: m.get("words_path") for m in mans}
        for clip in clips:
            rank = clip.get("rank")
            fr = find_render(wd.name, rank)
            if not fr:
                skipped += 1
                continue
            proj, final = fr
            wp = wbyrank.get(rank)
            words = []
            if wp and Path(wp).exists():
                try:
                    words = json.loads(Path(wp).read_text(encoding="utf-8"))
                except Exception:
                    words = []
            dur = probe_duration(final)
            events = sfx.plan_events(clip, words, sfx_map, cfg, dur)
            rdir = proj / "renders"
            rdir.mkdir(parents=True, exist_ok=True)
            tmp, out = rdir / "_sfx_tmp.mp4", rdir / "final_sfx.mp4"
            if sfx.apply_sfx(final, events, sfx_map, tmp, cfg):
                tmp.replace(out)
                applied += 1
            else:
                tmp.unlink(missing_ok=True)
        if applied and applied % 50 == 0:
            log.info("  SFX applied to %d shorts so far...", applied)
    log.info("Phase 1 done: SFX applied=%d, skipped(no render)=%d", applied, skipped)
    return applied


def phase2_consolidate() -> int:
    ALL_DIR.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src_dir in sorted(AUTO_SHORTS_ROOT.iterdir()):
        if not src_dir.is_dir():
            continue
        for proj in sorted(src_dir.glob("short-*")):
            if not proj.is_dir():
                continue
            sfx_mp4 = proj / "renders" / "final_sfx.mp4"
            if not sfx_mp4.exists():
                continue
            base = f"{src_dir.name}__{proj.name}"
            shutil.copy(sfx_mp4, ALL_DIR / f"{base}.mp4")
            cap = proj / "caption.txt"
            if cap.exists():
                shutil.copy(cap, ALL_DIR / f"{base}.txt")
            copied += 1
    log.info("Phase 2 done: consolidated %d shorts -> %s", copied, ALL_DIR)
    return copied


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Final-version pass: Remotion -> SFX -> consolidate."
    )
    ap.add_argument(
        "--skip-remotion", action="store_true",
        help="Skip Phase 0 (captions + b-roll). Use when Remotion / Node / "
             "network isn't available; SFX falls back to the Hyperframes base render.",
    )
    ap.add_argument(
        "--skip-sfx", action="store_true",
        help="Skip Phase 1 (SFX overlay). Consolidate the best available render "
             "(final_remotion.mp4 or final.mp4) directly.",
    )
    ap.add_argument(
        "--offline", action="store_true",
        help="Force Phase 0 (if not skipped) to use the on-disk b-roll fetch cache only; "
             "no live HTTP to Commons / Openverse / Wikipedia.",
    )
    args = ap.parse_args()

    if args.offline:
        os.environ["SHORTSMITH_BROLL_OFFLINE"] = "1"

    cfg = Config()
    sfx_map = sfx.load_sfx_map() if not args.skip_sfx else {}
    if not args.skip_sfx and not sfx_map:
        log.error("No SFX pack found. Run scripts/build_sfx_pack.py first, "
                  "or pass --skip-sfx to consolidate the Hyperframes render directly.")
        return 1
    if sfx_map:
        log.info("SFX slots: %s",
                 ", ".join(f"{k}x{len(v)}" for k, v in sorted(sfx_map.items())))

    style = os.environ.get("SHORTSMITH_STYLE", "xrp-revolution")
    if args.skip_remotion:
        log.info("Phase 0 skipped (--skip-remotion). SFX/consolidate will use Hyperframes base renders.")
    else:
        phase0_remotion(style)

    if args.skip_sfx:
        log.info("Phase 1 skipped (--skip-sfx). Going straight to consolidation.")
    else:
        phase1_sfx(cfg, sfx_map)

    n = phase2_consolidate()
    log.info("FINALIZE COMPLETE. %d shorts consolidated to %s", n, ALL_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
