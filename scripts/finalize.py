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
import tempfile
from collections.abc import Iterator
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
            check=True, capture_output=True, text=True, encoding="utf-8")
        return float(out.stdout.strip())
    except Exception as e:  # noqa: BLE001 - a duration we cannot read is not fatal
        # Say so: 0.0 silently shortens every SFX plan built on it, and a
        # missing ffprobe would otherwise ship a whole batch of cue-less finals.
        log.warning("  ffprobe could not read a duration for %s (%s); using 0.0",
                    p.name, e)
        return 0.0


def best_render(proj: Path) -> Path | None:
    """The best non-SFX render for one short, or None if it has none.

    Prefers the Remotion output (captions + b-roll) so whatever consumes it
    carries everything; falls back to the raw newest render, project-level or
    kit-level.
    """
    remotion = proj / "renders" / "final_remotion.mp4"
    if remotion.exists():
        return remotion
    cands: list[Path] = []
    rdir = proj / "renders"
    if rdir.is_dir():
        cands += [p for p in rdir.glob("*.mp4")
                  if p.stem != "final_sfx" and not p.stem.startswith("_")]
    if KIT_RENDERS.is_dir():
        cands += list(KIT_RENDERS.glob(f"{proj.name}_*.mp4"))
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def scoped_projects(slugs: set[str] | None) -> Iterator[tuple[Path, Path]]:
    """(source folder, short project) for every scaffolded short under
    auto-shorts/, in name order.

    `slugs` is the --slug filter: None means the whole library, a set keeps
    only those source folders. Phases 0 and 2 walk the same tree with the same
    filter, so the walk lives here rather than in each of them.
    """
    for src_dir in sorted(AUTO_SHORTS_ROOT.iterdir()):
        if not src_dir.is_dir():
            continue
        if slugs is not None and src_dir.name not in slugs:
            continue
        for proj in sorted(src_dir.glob("short-*")):
            if proj.is_dir():
                yield src_dir, proj


def find_render(work_slug: str, rank: int) -> tuple[Path, Path] | None:
    src_dir = AUTO_SHORTS_ROOT / work_slug
    if not src_dir.exists():
        return None
    for proj in src_dir.glob(f"short-{rank:02d}-*"):
        found = best_render(proj)
        if found:
            return proj, found
    return None


def _clear_remotion_cache() -> None:
    """Delete Remotion's webpack bundle + asset caches so the first render of the
    run recompiles remotion/src from scratch.

    Remotion reuses a persistent compiled bundle in %TEMP%/remotion-* and
    remotion/node_modules/.cache. It honors fresh inputProps every render but can
    silently keep STALE compiled .tsx — so a caption/transition code change won't
    take effect until the cache is cleared. Clearing once up front means every
    short in this run renders with the current code. (See PROJECT_STATE bundle
    gotcha — this cost ~5 renders to diagnose.)
    """
    removed = 0
    for d in Path(tempfile.gettempdir()).glob("remotion-*"):
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    cache = SHORTSMITH_ROOT / "remotion" / "node_modules" / ".cache"
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)
        removed += 1
    log.info("Cleared %d Remotion cache dir(s) so captions/transitions recompile fresh", removed)


def _disk_guard(min_warn_gb: float = 20.0, min_abort_gb: float = 5.0) -> None:
    """Refuse to start a full finalize run when the renders drive is nearly full.
    Re-renders + freeze re-encodes + _all/ copies need headroom; running out
    mid-batch leaves a half-finished mess. Abort < min_abort_gb, warn < min_warn_gb."""
    KIT_RENDERS.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(KIT_RENDERS).free / 2 ** 30
    if free_gb < min_abort_gb:
        log.error("Only %.1f GB free on the renders drive (need >= %.0f GB). "
                  "Free space / run cleanup, then retry.", free_gb, min_abort_gb)
        sys.exit(1)
    if free_gb < min_warn_gb:
        log.warning("Low disk: %.1f GB free (< %.0f GB) — finalize may run tight.",
                    free_gb, min_warn_gb)
    else:
        log.info("Disk OK: %.1f GB free on the renders drive.", free_gb)


def phase0_remotion(style: str, force: bool = False,
                    slugs: set[str] | None = None) -> int:
    """Layer Remotion captions + auto b-roll onto every scaffolded short.

    When `slugs` is given, only source folders whose name is in the set are
    processed — so a partial/interrupted global run can be finished (or a single
    batch re-rendered) without re-touching the whole library.
    """
    sys.path.insert(0, str(SHORTSMITH_ROOT / "scripts"))
    try:
        import apply_remotion as ar
    except Exception as e:  # noqa: BLE001
        log.error("Cannot import apply_remotion (%s); skipping Remotion phase", e)
        return 0
    _clear_remotion_cache()  # once per run, before the first render bundles
    ar.reset_stats()
    applied = skipped = 0
    for _src_dir, proj in scoped_projects(slugs):
        try:
            out = ar.apply_remotion(proj, style=style, force=force)
        except Exception as e:  # noqa: BLE001 - one short shouldn't sink the run
            log.warning("  Remotion failed for %s: %s", proj.name, e)
            out = None
        if out:
            applied += 1
            if applied % 25 == 0:
                log.info("  Remotion applied to %d shorts so far...", applied)
        else:
            skipped += 1
    stats = ar.RUN_STATS
    log.info("Phase 0 done: rendered=%d current=%d legacy(untouched)=%d skipped(no base)=%d "
             "b-roll failures=%d", stats["rendered"], stats["current"],
             stats["legacy_skipped"], skipped, stats["broll_failures"])
    if stats["legacy_skipped"]:
        log.info("  %d short(s) predate render stamps and were left as they are; "
                 "pass --force-remotion to rebuild them with the current code.",
                 stats["legacy_skipped"])
    if stats["broll_failures"]:
        log.warning("  %d short(s) rendered with a stale or empty b-roll list because "
                    "generation failed; see the '!!' lines above.", stats["broll_failures"])
    return applied


def phase1_sfx(cfg: Config, sfx_map, slugs: set[str] | None = None) -> int:
    applied = skipped = 0
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        if slugs is not None and wd.name not in slugs:
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


def _qa_streams(p: Path) -> tuple[bool, int, int]:
    """Quick QA probe: (has_audio, width, height). Cheap ffprobe, ~50ms."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height", "-of", "json", str(p)],
            check=True, capture_output=True, text=True, encoding="utf-8")
        streams = json.loads(out.stdout).get("streams", [])
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        vid = next((s for s in streams if s.get("codec_type") == "video"), {})
        return has_audio, int(vid.get("width", 0) or 0), int(vid.get("height", 0) or 0)
    except Exception:
        return False, 0, 0


def phase2_consolidate(slugs: set[str] | None = None,
                       allow_unmixed: bool = False) -> int:
    """Copy every deliverable into _all/.

    `allow_unmixed` is what --skip-sfx promises: with no SFX phase there is no
    final_sfx.mp4 to find, so consolidate the best render the short does have
    (final_remotion.mp4, else its newest base render). Without it a --skip-sfx
    run consolidated nothing and still reported success.
    """
    ALL_DIR.mkdir(parents=True, exist_ok=True)
    copied = bad = 0
    for src_dir, proj in scoped_projects(slugs):
        sfx_mp4 = proj / "renders" / "final_sfx.mp4"
        if not sfx_mp4.exists():
            if not allow_unmixed:
                continue
            fallback = best_render(proj)
            if fallback is None:
                continue
            log.info("  %s: no final_sfx.mp4; consolidating %s",
                     proj.name, fallback.name)
            sfx_mp4 = fallback
        base = f"{src_dir.name}__{proj.name}"
        # QA: a deliverable must have audio and be a 1080x1920 vertical video.
        has_audio, w, h = _qa_streams(sfx_mp4)
        if not has_audio or (w, h) != (1080, 1920):
            log.warning("  QA FAIL %s: has_audio=%s dims=%dx%d (still consolidating)",
                        base, has_audio, w, h)
            bad += 1
        shutil.copy(sfx_mp4, ALL_DIR / f"{base}.mp4")
        cap = proj / "caption.txt"
        if cap.exists():
            shutil.copy(cap, ALL_DIR / f"{base}.txt")
        copied += 1
    log.info("Phase 2 done: consolidated %d shorts -> %s (%d QA warnings)",
             copied, ALL_DIR, bad)
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
             "no live HTTP to Wikidata / Commons / Simple Icons.",
    )
    ap.add_argument(
        "--force-remotion", action="store_true",
        help="Re-render every short's captions/b-roll even if final_remotion.mp4 looks "
             "up to date. Use after changing Remotion/caption code without re-rendering "
             "the Hyperframes base (the bundle cache is always cleared regardless).",
    )
    ap.add_argument(
        "--slug", action="append", metavar="SOURCE_SLUG", default=None,
        help="Scope this finalize to one source-slug folder under auto-shorts/ "
             "(repeatable). Use to finish a partial/interrupted run or re-finalize a "
             "single batch without re-touching the whole library. Omit for all.",
    )
    args = ap.parse_args()
    slugs = set(args.slug) if args.slug else None
    if slugs:
        log.info("Scoped finalize to slugs: %s", ", ".join(sorted(slugs)))

    _disk_guard()

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
        phase0_remotion(style, force=args.force_remotion, slugs=slugs)

    if args.skip_sfx:
        log.info("Phase 1 skipped (--skip-sfx). Going straight to consolidation.")
    else:
        phase1_sfx(cfg, sfx_map, slugs=slugs)

    n = phase2_consolidate(slugs=slugs, allow_unmixed=args.skip_sfx)
    log.info("FINALIZE COMPLETE. %d shorts consolidated to %s", n, ALL_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
