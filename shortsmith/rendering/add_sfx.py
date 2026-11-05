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
import shutil
import subprocess
import sys
from pathlib import Path

from shortsmith import sfx
from shortsmith._media import probe_duration as _probe_duration
from shortsmith.config import AUTO_SHORTS_ROOT, REPO_ROOT, SFX_DIR, Config
from shortsmith.records import ClipSpec, TranscriptWord

from . import project_inputs

log = logging.getLogger("add_sfx")

SHORTSMITH_ROOT = REPO_ROOT
WORK_ROOT = SHORTSMITH_ROOT / "work"


def load_sfx_state(work_dir: Path) -> tuple[list[ClipSpec], dict[int, str | None]]:
    """Read required picks and cut manifests without treating bad state as empty.

    Only each clip's rank is checked; SFX planning reads the optional hook and
    callout fields with .get, so the other ClipSpec keys are not required here.
    """
    clips_path, manifests_path = work_dir / "clips.json", work_dir / "cut_manifests.json"
    clips = json.loads(clips_path.read_text(encoding="utf-8"))
    if not isinstance(clips, list) or any(
        not isinstance(clip, dict) or type(clip.get("rank")) is not int or clip["rank"] < 1
        for clip in clips
    ):
        raise ValueError(f"Invalid picks in {clips_path}: expected clip objects with positive integer ranks")
    if not clips:
        return [], {}
    manifests = json.loads(manifests_path.read_text(encoding="utf-8"))
    if not isinstance(manifests, list) or any(
        not isinstance(item, dict) or type(item.get("rank")) is not int or item["rank"] < 1
        or (item.get("words_path") is not None and not isinstance(item["words_path"], str))
        for item in manifests
    ):
        raise ValueError(f"Invalid cut manifests in {manifests_path}: expected ranked objects and optional word paths")
    return clips, {item["rank"]: item.get("words_path") for item in manifests}


def load_optional_manifest_words(path: str | None) -> list[TranscriptWord]:
    """Read a saved word list; an omitted word path means no semantic cues."""
    if path is None:
        return []
    words = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(words, list) or any(not isinstance(word, dict) for word in words):
        raise ValueError(f"Invalid word list in {path}: expected word objects")
    return words


KIT_RENDERS = AUTO_SHORTS_ROOT.parent.parent / "renders"  # <kit>/renders


def find_latest_non_sfx_render(work_slug: str, rank: int) -> tuple[Path, Path] | None:
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
        cands = project_inputs.non_sfx_renders(proj, KIT_RENDERS)
        if cands:
            newest = max(cands, key=lambda p: p.stat().st_mtime)
            return proj, newest
    return None


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")

    import argparse

    parser = argparse.ArgumentParser(description="Mix sound effects over every rendered short.")
    parser.add_argument("--overwrite", action="store_true", help="Replace project renders in place.")
    args, _unknown = parser.parse_known_args()
    overwrite = args.overwrite
    missing_tools = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing_tools:
        log.error("Not on PATH: %s. Run `uv run shortsmith doctor` for install hints.",
                  ", ".join(missing_tools))
        return 1
    if not WORK_ROOT.is_dir():
        log.error("No work dir yet: %s. Run the pipeline first.", WORK_ROOT)
        return 1
    cfg = Config()
    sfx_map = sfx.load_sfx_map(SFX_DIR)
    if not sfx_map:
        log.error("No SFX files found in %s; drop sounds there first (see README).", SFX_DIR)
        return 1
    log.info("SFX slots available: %s", ", ".join(sorted(sfx_map)))

    done = applied = skipped = failed = 0
    for wd in sorted(WORK_ROOT.iterdir()):
        if not wd.is_dir():
            continue
        clips_p, cm_p = wd / "clips.json", wd / "cut_manifests.json"
        if not any(p.exists() or p.is_symlink() for p in (clips_p, cm_p)):
            continue
        try:
            clips, words_by_rank = load_sfx_state(wd)
        except (OSError, ValueError) as exc:
            log.error("Cannot load SFX state for %s: %s", wd, exc)
            failed += 1
            continue
        if not clips:
            continue

        for clip in clips:
            rank = clip.get("rank")
            if not isinstance(rank, int):
                skipped += 1
                continue
            found = find_latest_non_sfx_render(wd.name, rank)
            if not found:
                skipped += 1
                continue
            proj, final = found
            wp = words_by_rank.get(rank)
            try:
                words = load_optional_manifest_words(wp)
            except (OSError, ValueError) as exc:
                log.error("Cannot load words for %s/short-%02d from %s: %s", wd.name, rank, wp, exc)
                failed += 1
                continue
            try:
                dur = _probe_duration(final)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                # A guessed 0.0 would stack every SFX on frame 1, so the clip fails instead.
                log.error("Cannot read the duration of %s: %s", final, exc)
                failed += 1
                continue
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
