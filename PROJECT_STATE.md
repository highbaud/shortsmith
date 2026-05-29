# Shortsmith — project state / resume handoff

_Last updated: 2026-05-28. Read this first to resume work in a fresh session._

## What it is
Local-first pipeline: long Jake Claver livestreams → batches of viral 9:16 shorts,
each scaffolded as a Hyperframes project (HTML+GSAP) the user polishes/renders.

## Layout (Windows, RTX 5090)
- `F:/Claude Code/shortsmith/` — main uv project (this repo). Package: `shortsmith/`.
- `F:/Claude Code/audio-enhance/` — sibling uv venv, ClearerVoice MossFormer2_SE_48K (CPU). `.env` points here.
- `F:/Claude Code/whisperx-align/` — sibling uv venv, WhisperX (torch 2.8.0+cu128, CUDA). `.env` points here.
- `F:/Claude Code/hyperframes-student-kit/video-projects/auto-shorts/<source-slug>/short-NN-<slug>/` — output projects + `renders/final.mp4`.
- `F:/Claude Code/video resources/` — source videos (SHORTSMITH_VIDEO_DIR).
- `.env` pins sibling-project + kit + video paths (see it for the exact overrides).

## Pipeline (shortsmith/pipeline.py, `uv run shortsmith run <video> [--from-step N]`)
1 transcribe (faster-whisper large-v3, CUDA) · 2 find_clips (Claude API OR done by
me/subagents writing clips.json) · 3 cut_clips (asymmetric end-boundary snap) ·
4 clean_clips (filler + stutter repair + silence) · 5 enhance_audio (ClearerVoice
+ −14 LUFS two-pass loudnorm) · 6 align (WhisperX forced alignment, faster-whisper
fallback) · 7 reframe (YuNet biggest-face, 9:16, face_target_y=0.40/height=0.32) ·
8 scaffold (Hyperframes project). Checkpoints in `work/<slug>/.progress.json`.

## Quality knobs that were tuned (all in shortsmith/config.py)
- Filler list trimmed to pure stammers + "you know" (dropped "like" etc. — was chopping content words).
- silence_min_to_cut 0.80s, silence_margin 0.30s (preserve word tails / breaths).
- Cut end-boundary snaps FORWARD to a sentence end (no chopped thoughts).
- Reframe: biggest-face-wins + IQR + resolution-aware min-face (fixes 4K PIP/avatar misframes). Composition target UNCHANGED (0.40 / 0.32).
- Loudness target −14 LUFS. Stutter repair on (gap<0.35s, exact-repeat only).

## Sound effects (post-render, shortsmith/sfx.py + scripts/add_sfx.py)
- Curated/normalized pack at `assets/sfx/pack/` (pack.json). Built by `scripts/build_sfx_pack.py` from raw drops in `assets/sfx/`.
- Levels APPROVED by user: all SFX peak −9 dB normalized, mixed at per-slot gain × sfx_gain 0.7, limiter. Sit ~10–16 dB under speech. Do not raise without asking.
- Sparse: hook whoosh @0; swipe-in on callouts (rotates 4 variants); bigstat → ding INSTEAD of swipe; cash-register on FIRST money word only; swipe-out OFF.
- Run AFTER renders: `uv run python scripts/add_sfx.py` → writes `final_sfx.mp4` beside each `final.mp4` (non-destructive, re-runnable). ~30 min for all.

## RUNNING NOW (background)
- `scripts/reprocess_all.py` — re-processing ALL 87 work dirs (originals + new) from step 3 with the full upgraded pipeline + re-render. Log: `work/reprocess_all.log`. Resumable via `.reprocessed_v2` marker per work dir (written only after pipeline + all renders succeed). ETA ~30–36h from 2026-05-28 17:20.
  - If relaunching Claude Code: run `uv run python scripts/reprocess_all.py` in a standalone PowerShell window FIRST (it skips done dirs), then relaunch. Marker resume = zero lost work.

## RENDER LOCATION GOTCHA (important for SFX + consolidation)
`npx hyperframes render "<project>"` writes the render to the KIT-LEVEL
`<kit>/renders/<project-name>_<timestamp>.mp4`, NOT the project's own
`renders/final.mp4`. Some projects also have a manual `renders/final_remotion.mp4`.
So to locate a short's render: scan BOTH the project `renders/*.mp4` AND
`<kit>/renders/<project-name>_*.mp4`, take newest mtime. scripts/add_sfx.py
`find_render()` already does this; reuse that logic for consolidation.

## REMOTION LAYER (captions + auto b-roll) — added in a parallel session
Between the Hyperframes base render and SFX there is now a Remotion pass:
`scripts/apply_remotion.py` wraps `gen_broll.py` (heuristic b-roll -> broll.auto.json)
+ `render_remotion.py` (word captions + b-roll over the Hyperframes base) ->
`<project>/renders/final_remotion.mp4`. Remotion project at `remotion/` (installed).
B-roll decisions (memory: shortsmith_broll_decisions.md): Claude+heuristic engine,
full-color->mono logos, multi-source CC person photos (Commons/Openverse/Wikipedia,
shuffled), no credit. Captions default ON (word captions); `--no-captions` to disable.

## NEXT STEP after reprocess_all finishes (THE FINAL VERSION)
Run ONE command: `uv run python scripts/finalize.py` — 3 phases:
  Phase 0 (Remotion): layer captions + auto b-roll on every short -> final_remotion.mp4.
  Phase 1 (SFX): mix approved SFX onto that -> final_sfx.mp4 (find_render prefers final_remotion).
  Phase 2 (consolidate): every final_sfx.mp4 + caption.txt -> <kit>/renders/_all/<source>__<short>.(mp4|txt).
User APPROVED SFX on all (2026-05-28). finalize is idempotent + authoritative —
it regenerates the 28 shorts SFX'd earlier (which were pre-Remotion) correctly.
Run once reprocess_all is fully done, spot-check, report totals.

## DISK CLEANUP — DEFERRED until after finalize (do NOT run mid-reprocess)
`<kit>/renders/` was ~19 GB on 2026-05-28. After finalize completes + _all/ is
verified, reclaim it:
  - Dedup loose top-level `<kit>/renders/<short>_<timestamp>.mp4`: each re-render
    left older timestamped copies; only the newest per short matters (and once
    every short has renders/final_remotion.mp4, the loose top-level ones are
    unneeded entirely — deliverables live in _all/ + per-project renders/).
  - Remove `<kit>/renders/_all-50/` (2.1 GB) — superseded by `_all/`.
Already cleaned (safe throwaway): previews/, work/*.jpg, audio-enhance/test/,
whisperx-align/test_words.json. Stale work/*.log left as history (tiny).

## Tests
`uv run --with pytest pytest tests/ -q` — 31 passing (boundary_snap, normalize, stutter, scaffold overlays, sfx).

## Counts (pre-reprocess)
~560 shorts across 78 new source videos + the original 11. All being regenerated.
