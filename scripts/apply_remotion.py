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
    if (not force and out_path.exists()
            and out_path.stat().st_mtime >= base.stat().st_mtime):
        print(f"  skip {project_dir.name}: final_remotion.mp4 already up to date")
        return out_path

    has_words = (project_dir / "assets" / "words.json").exists()

    # (Re)generate the heuristic b-roll. Best-effort: a failure here shouldn't
    # block the captioned render, which still adds value on its own.
    if broll and has_words:
        try:
            gen_broll.generate(project_dir, heuristic=True, cap=6, dry_run=False)
        except SystemExit as e:
            print(f"  b-roll gen skipped for {project_dir.name}: {e}")
        except Exception as e:  # noqa: BLE001 - non-fatal
            print(f"  b-roll gen failed for {project_dir.name}: {e}")

    return render_remotion.render(
        project_dir,
        captions=captions and has_words,
        platform=platform,
        base_mode="hyperframes",
        broll_arg=None,
        output="final_remotion.mp4",
        style=style,
        open_after=False,
    )


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
