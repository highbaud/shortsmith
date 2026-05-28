"""shortsmith CLI orchestrator.

Usage:
    uv run shortsmith run <video.mp4> [--no-enhance] [--max-clips N]
    uv run shortsmith run <video.mp4> --from-step 3   # resume from a step
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import click

from shortsmith import (
    clean_clips,
    config,
    cut_clips,
    enhance_audio,
    find_clips,
    reframe,
    scaffold,
    transcribe,
)

log = logging.getLogger("shortsmith")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


@click.group()
def cli() -> None:
    """shortsmith — long-form video -> Hyperframes-ready viral shorts."""


@cli.command(name="transcribe")
@click.argument("video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def transcribe_only(video: Path) -> None:
    """Run only step 1 (transcribe) and exit.

    Use when you want Claude Code (or another LLM) to do the clip-finding
    step interactively. After this completes, the resulting transcript.json
    can be inspected, fed to Claude in chat, and the resulting clips.json
    saved alongside it. Then resume with `shortsmith run <video> --from-step 3`.
    """
    _setup_logging()
    cfg = config.Config()
    work_dir = config.make_work_dir(video)
    out = work_dir / "transcript.json"
    log.info("Work dir: %s", work_dir)
    log.info("Transcribing %s ...", video.name)
    words = transcribe.transcribe(video, out, cfg, reuse_existing=True)
    log.info("DONE. %d words written to %s", len(words), out)
    log.info("Next: Claude Code will read this transcript and write clips.json")
    log.info("      Then resume:  uv run shortsmith run \"%s\" --from-step 3", video)


@cli.command()
@click.argument("video", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--max-clips", type=int, default=None,
              help="Cap number of clips processed (handy for smoke tests).")
@click.option("--from-step", type=int, default=1,
              help="Resume from step N (1-8). Earlier outputs must already exist on disk.")
@click.option("--enhance/--no-enhance", default=True,
              help="Run audio enhancement (Resemble Enhance). Default: on.")
@click.option("--engine", type=click.Choice(["clearvoice", "voicefixer", "resemble", "deepfilter"]), default=None,
              help="Override the enhancement engine. Default: clearvoice (ClearerVoice-Studio MossFormer2_SE_48K).")
@click.option("--min-score", type=int, default=None,
              help="Drop clips below this viral_score (1-10). Default 7. Use 8+ for stricter, 5 for permissive.")
def run(video: Path, max_clips: int | None, from_step: int, enhance: bool,
        engine: str | None, min_score: int | None) -> None:
    """Run the full pipeline on a single source video."""
    _setup_logging()

    cfg = config.Config()
    if engine:
        cfg.enhance_engine = engine
    if min_score is not None:
        cfg.min_viral_score = min_score

    problems = cfg.validate()
    # Filter problems by what the upcoming steps actually need.
    needs_api_key = from_step <= 2
    needs_audio_enhance = enhance and from_step <= 5 and cfg.enhance_engine == "clearvoice"
    needs_kit = from_step <= 8  # scaffold targets kit; final step always runs
    if not needs_api_key:
        problems = [p for p in problems if "ANTHROPIC_API_KEY" not in p]
    if not needs_audio_enhance:
        problems = [p for p in problems if "Audio-enhance project" not in p]
    if not needs_kit:
        problems = [p for p in problems if "Hyperframes kit" not in p
                                      and "Template reference" not in p]
    if problems:
        for p in problems:
            log.error(p)
        sys.exit(1)

    work_dir = config.make_work_dir(video)
    log.info("Work dir: %s", work_dir)

    transcript_path = work_dir / "transcript.json"
    clips_path = work_dir / "clips.json"

    # ---- Step 1: transcribe ----
    if from_step <= 1:
        t0 = time.time()
        log.info("Step 1: transcribing %s", video.name)
        words = transcribe.transcribe(video, transcript_path, cfg, reuse_existing=True)
        log.info("Step 1 done (%.1fs, %d words)", time.time() - t0, len(words))
    else:
        words = transcribe.load_words(transcript_path)
        log.info("Step 1 skipped — loaded %d words from %s", len(words), transcript_path)

    # ---- Step 2: find clips ----
    if from_step <= 2:
        t0 = time.time()
        log.info("Step 2: finding viral clips via Claude")
        clips = find_clips.find_clips(words, clips_path, cfg)
        log.info("Step 2 done (%.1fs, %d clips)", time.time() - t0, len(clips))
    else:
        clips = json.loads(clips_path.read_text(encoding="utf-8"))
        log.info("Step 2 skipped — loaded %d clips from %s", len(clips), clips_path)

    if max_clips:
        clips = clips[:max_clips]
        log.info("Capped to first %d clips for this run", len(clips))

    if not clips:
        log.error("No clips to process — aborting")
        sys.exit(1)

    # ---- Step 3: cut ----
    if from_step <= 3:
        t0 = time.time()
        log.info("Step 3: cutting %d clips (with reorder + xfade seams)", len(clips))
        manifests = cut_clips.cut_all(clips, words, video, work_dir, cfg)
        log.info("Step 3 done (%.1fs)", time.time() - t0)
    else:
        manifests = json.loads((work_dir / "cut_manifests.json").read_text(encoding="utf-8"))

    # ---- Step 4: clean (word-aware) ----
    if from_step <= 4:
        t0 = time.time()
        log.info("Step 4: cleaning %d clips (word-aware silence + filler removal)", len(manifests))
        manifests = clean_clips.clean_all(manifests, words, work_dir, cfg)
        _save_manifests(manifests, work_dir)
        log.info("Step 4 done (%.1fs)", time.time() - t0)

    # ---- Step 5: enhance audio ----
    if enhance and from_step <= 5:
        t0 = time.time()
        log.info("Step 5: enhancing speech audio (%s)", cfg.enhance_engine)
        manifests = enhance_audio.enhance_all(manifests, work_dir, cfg)
        _save_manifests(manifests, work_dir)
        log.info("Step 5 done (%.1fs)", time.time() - t0)
    elif not enhance:
        for m in manifests:
            m["enhanced_path"] = m.get("cleaned_path") or m["raw_path"]
        log.info("Step 5 skipped (--no-enhance)")

    # ---- Step 6: retranscribe each cleaned/enhanced clip ----
    if from_step <= 6:
        t0 = time.time()
        log.info("Step 6: re-transcribing each clip after edits")
        for m in manifests:
            clip_path = Path(m.get("enhanced_path") or m["cleaned_path"])
            words_out = clip_path.with_suffix(".words.json")
            transcribe.transcribe(clip_path, words_out, cfg, reuse_existing=False)
            m["words_path"] = str(words_out)
        _save_manifests(manifests, work_dir)
        log.info("Step 6 done (%.1fs)", time.time() - t0)

    # ---- Step 7: reframe to 9:16 ----
    if from_step <= 7:
        t0 = time.time()
        log.info("Step 7: reframing to 9:16 with face tracking")
        manifests = reframe.reframe_all(manifests, work_dir, cfg)
        _save_manifests(manifests, work_dir)
        log.info("Step 7 done (%.1fs)", time.time() - t0)

    # ---- Step 8: scaffold Hyperframes projects ----
    t0 = time.time()
    log.info("Step 8: scaffolding Hyperframes projects")
    project_dirs = scaffold.scaffold_all(manifests, clips, video, work_dir, cfg)
    log.info("Step 8 done (%.1fs, %d projects)", time.time() - t0, len(project_dirs))

    log.info("DONE.")
    log.info("Output: %s", config.make_output_dir(video))
    log.info("Try:  cd %s/short-01-* && npx hyperframes preview", config.make_output_dir(video))


def _save_manifests(manifests: list[dict], work_dir: Path) -> None:
    (work_dir / "cut_manifests.json").write_text(
        json.dumps(manifests, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
