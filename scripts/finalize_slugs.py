"""Targeted finalize for one or more source slugs (not the whole library).

Same three phases as scripts/finalize.py, but scoped to the given
auto-shorts/<slug> source folders, so re-running after a partial/interrupted
global finalize is fast and safe. Honors each clip's per-clip "captions" flag via
apply_remotion (clips with "captions": false keep their source captions).

Usage:
    uv run python scripts/finalize_slugs.py <slug> [<slug> ...]
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_remotion as ar  # noqa: E402
import finalize as fz  # noqa: E402

from shortsmith import sfx  # noqa: E402
from shortsmith.config import AUTO_SHORTS_ROOT, Config  # noqa: E402

log = logging.getLogger("finalize_slugs")


def run(slugs: list[str]) -> int:
    cfg = Config()
    style = "xrp-revolution"
    sfx_map = sfx.load_sfx_map()
    if not sfx_map:
        log.error("No SFX pack found; run scripts/build_sfx_pack.py first.")
        return 1

    fz._clear_remotion_cache()

    # ---- Phase 0: Remotion (captions per-clip + auto b-roll) ----
    for slug in slugs:
        src_dir = AUTO_SHORTS_ROOT / slug
        if not src_dir.is_dir():
            log.warning("no such source dir: %s", src_dir)
            continue
        for proj in sorted(p for p in src_dir.glob("short-*") if p.is_dir()):
            try:
                # force=False: skip projects whose final_remotion is already up to
                # date (e.g. an earlier completed batch) and only render fresh ones.
                ar.apply_remotion(proj, style=style, force=False)
            except Exception as e:  # noqa: BLE001
                log.warning("Remotion failed for %s: %s", proj.name, e)

    # ---- Phase 1: SFX ----
    for slug in slugs:
        wd = fz.WORK_ROOT / slug
        cp, cm = wd / "clips.json", wd / "cut_manifests.json"
        if not (cp.exists() and cm.exists()):
            log.warning("missing clips/manifests for %s", slug)
            continue
        clips = json.loads(cp.read_text(encoding="utf-8"))
        mans = json.loads(cm.read_text(encoding="utf-8"))
        wbyrank = {m["rank"]: m.get("words_path") for m in mans}
        for clip in clips:
            rank = clip.get("rank")
            fr = fz.find_render(slug, rank)
            if not fr:
                log.warning("  no render for %s rank %s", slug, rank)
                continue
            proj, final = fr
            wp = wbyrank.get(rank)
            words = []
            if wp and Path(wp).exists():
                try:
                    words = json.loads(Path(wp).read_text(encoding="utf-8"))
                except Exception:
                    words = []
            dur = fz.probe_duration(final)
            events = sfx.plan_events(clip, words, sfx_map, cfg, dur)
            rdir = proj / "renders"
            rdir.mkdir(parents=True, exist_ok=True)
            tmp, out = rdir / "_sfx_tmp.mp4", rdir / "final_sfx.mp4"
            if sfx.apply_sfx(final, events, sfx_map, tmp, cfg):
                tmp.replace(out)
            else:
                tmp.unlink(missing_ok=True)

    # ---- Phase 2: consolidate just these slugs ----
    fz.ALL_DIR.mkdir(parents=True, exist_ok=True)
    copied = 0
    for slug in slugs:
        src_dir = AUTO_SHORTS_ROOT / slug
        if not src_dir.is_dir():
            continue
        for proj in sorted(p for p in src_dir.glob("short-*") if p.is_dir()):
            sfx_mp4 = proj / "renders" / "final_sfx.mp4"
            if not sfx_mp4.exists():
                continue
            base = f"{src_dir.name}__{proj.name}"
            has_audio, w, h = fz._qa_streams(sfx_mp4)
            if not has_audio or (w, h) != (1080, 1920):
                log.warning("  QA FAIL %s: audio=%s dims=%dx%d", base, has_audio, w, h)
            shutil.copy(sfx_mp4, fz.ALL_DIR / f"{base}.mp4")
            cap = proj / "caption.txt"
            if cap.exists():
                shutil.copy(cap, fz.ALL_DIR / f"{base}.txt")
            copied += 1
    log.info("DONE. consolidated %d shorts for slugs %s -> %s", copied, slugs, fz.ALL_DIR)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")
    if len(sys.argv) < 2:
        print("usage: finalize_slugs.py <slug> [<slug> ...]")
        sys.exit(1)
    sys.exit(run(sys.argv[1:]))
