"""Apply the Remotion layer (auto b-roll + word captions) to a scaffolded short.

One reusable step that wraps the two pieces we built:
  1. (re)generate heuristic b-roll          -> broll.auto.json
  2. render captions + b-roll on the short's Hyperframes base render
                                             -> renders/final_remotion.mp4

It is non-destructive (the Hyperframes render is read, never modified) and
re-runnable: it no-ops when the short has no Hyperframes base render yet, and
skips the render when final_remotion.mp4 is already newer than that base.

This is the entry point the pipeline finishing pass (scripts/finalize.py
Phase 0) calls for every short before the SFX phase, so the canonical
deliverable carries Hyperframes + captions + b-roll + SFX.

Usage:
    uv run python scripts/apply_remotion.py <short-folder> [--style NAME]
                                            [--platform P] [--no-captions]
                                            [--no-broll] [--force]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_broll  # noqa: E402
import render_remotion  # noqa: E402
import render_stamp  # noqa: E402

# Counters for the caller's summary (finalize prints them per phase).
RUN_STATS: dict[str, int] = {"rendered": 0, "current": 0, "legacy_skipped": 0,
                             "broll_failures": 0}


def reset_stats() -> None:
    for key in RUN_STATS:
        RUN_STATS[key] = 0


def apply_remotion(project_dir: Path, *, style: str = "xrp-revolution",
                   platform: str = "generic", captions: bool = True,
                   broll: bool = True, force: bool = False) -> Path | None:
    """Render renders/final_remotion.mp4 for one short. Returns its path, or
    None if there's no Hyperframes base render to layer onto."""
    project_dir = Path(project_dir).resolve()

    hf = render_remotion._hyperframes_renders(project_dir)
    if not hf:
        print(f"  skip {project_dir.name}: no Hyperframes base render yet")
        return None
    base = hf[-1]

    out_path = project_dir / "renders" / "final_remotion.mp4"
    has_words = (project_dir / "assets" / "words.json").exists()

    # Per-clip caption opt-out: a clip with "captions": false in _clips.json
    # keeps the source's own (e.g. burned-in) captions and skips shortsmith's
    # caption layer. Anything else defaults to captions on.
    clip = render_remotion._clip_for(project_dir)
    if clip is not None and clip.get("captions") is False:
        if captions:
            print(f"  {project_dir.name}: captions off (clip opted out, keeping source captions)")
        captions = False
    captions = captions and has_words

    def stamp() -> dict:
        return render_stamp.compute_stamp(project_dir, base=base, style=style,
                                          platform=platform, captions=captions)

    # Up to date means: rendered from exactly these inputs (render_stamp). A
    # short rendered before stamps existed keeps the old rule, newer than its
    # base render, so an unscoped finalize does not rebuild the whole library
    # unasked; --force rebuilds it.
    if not force and out_path.exists():
        prior = render_stamp.read_stamp(project_dir)
        current = stamp()
        if prior is None:
            if out_path.stat().st_mtime >= base.stat().st_mtime:
                RUN_STATS["legacy_skipped"] += 1
                print(f"  skip {project_dir.name}: rendered before render stamps existed "
                      "and newer than its base (--force to rebuild)")
                return out_path
        elif render_stamp.is_current(project_dir, current, out_path):
            RUN_STATS["current"] += 1
            print(f"  skip {project_dir.name}: final_remotion.mp4 is current")
            return out_path
        else:
            print(f"  {project_dir.name}: changed since the last render: "
                  f"{', '.join(render_stamp.changed_inputs(prior, current))}")

    # (Re)generate the heuristic b-roll. A failure does not block the captioned
    # render, which still adds value on its own, but it is counted and shouted:
    # the render then uses the previous broll.auto.json, if any.
    if broll and has_words:
        try:
            gen_broll.generate(project_dir, heuristic=True, cap=6, dry_run=False)
        except SystemExit as e:
            RUN_STATS["broll_failures"] += 1
            print(f"  !! b-roll generation FAILED for {project_dir.name}: {e} "
                  "(rendering with the previous broll.auto.json, if any)")
        except Exception as e:  # noqa: BLE001 - counted, not fatal
            RUN_STATS["broll_failures"] += 1
            print(f"  !! b-roll generation FAILED for {project_dir.name}: {e!r} "
                  "(rendering with the previous broll.auto.json, if any)")

    out = render_remotion.render(
        project_dir,
        captions=captions,
        platform=platform,
        base_mode="hyperframes",
        broll_arg=None,
        output="final_remotion.mp4",
        style=style,
        open_after=False,
    )
    # Stamped after b-roll generation so the photo state it records is the one
    # the render used (a photo fetched during generation is part of it).
    render_stamp.write_stamp(project_dir, stamp())
    RUN_STATS["rendered"] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Apply Remotion captions + auto b-roll to a short (-> final_remotion.mp4).")
    ap.add_argument("short_dir", type=Path, help="Path to a short-NN-<slug> folder")
    ap.add_argument("--style", default="xrp-revolution",
                    help="Style preset whose palette colors the b-roll")
    ap.add_argument("--platform", default="generic",
                    help="Caption safe-band preset (tiktok|instagram|youtube|generic)")
    ap.add_argument("--no-captions", dest="captions", action="store_false",
                    help="Render without word captions")
    ap.add_argument("--no-broll", dest="broll", action="store_false",
                    help="Don't regenerate b-roll (use existing broll.auto.json)")
    ap.add_argument("--force", action="store_true",
                    help="Re-render even if final_remotion.mp4 is already up to date")
    args = ap.parse_args()
    apply_remotion(args.short_dir, style=args.style, platform=args.platform,
                   captions=args.captions, broll=args.broll, force=args.force)


if __name__ == "__main__":
    main()
