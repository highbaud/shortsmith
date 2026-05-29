# Changelog

All notable changes to this project will be documented in this file.

## [0.6.0] — Unreleased

### Added
- **Token-paste guardrail (pre-commit hooks).** `setup.sh` / `setup.ps1` now
  install `pre-commit` + Yelp's `detect-secrets` + a custom
  `scripts/check_no_tokens.py` scanner that catches Metricool OAuth client
  IDs, bare Bearer tokens, and Anthropic key shapes detect-secrets' built-in
  plugins miss. Every staged file (and the commit message itself) is scanned
  before each commit — token-shaped strings fail the commit locally, so a
  credential never reaches GitHub. Inline opt-out per-line via
  `# pragma: allowlist secret` for legitimate examples. New dev deps:
  `pre-commit>=3.7`, `detect-secrets>=1.5`. New files: `.pre-commit-config.yaml`,
  `.secrets.baseline`, `scripts/check_no_tokens.py`. `.gitignore` extended
  for the upcoming Metricool publish phase's local state files.
- **Visual transitions (VFX) layer** in Remotion — Capcut-style **Glare**
  (diagonal light sweep across the frame), **ZoomPunch** (~4% scale bump,
  bell-curve eased), and **Flash** (~90ms full-frame color tint).
  Triggered in lockstep with the 4 high-impact SFX slots:
    * `hook-impact` (t=0) → glare + zoom-punch + flash (white)
    * `ding` (bigstat $ callout) → glare (gold)
    * `cash-register` (first money word) → glare + flash (gold)
    * `wrong-answer` (first negative word) → flash + zoom-punch (red)
  Per-slot effect-set and color tint live in `Config.vfx_triggers` /
  `Config.vfx_colors`; wholesale disable via `SHORTSMITH_VFX=off`;
  global intensity via `SHORTSMITH_VFX_INTENSITY`. Multiple overlapping
  zoom-punches take the max scale (not the sum) so stacked hooks don't
  compound into a noticeable zoom. New `shortsmith/vfx.py`,
  `remotion/src/VFX.tsx`, `remotion/src/types.ts` adds `VFXEvent`,
  `scripts/render_remotion.py` passes `vfxEvents` in props. **12 new tests**
  cover the trigger taxonomy (sparing/every/off modes, intensity
  propagation, effect-duration defaults, prop shape). Total now 61.

## [0.5.1] — Unreleased

### Added
- **SFX pack ships with the repo.** Whitelisted `assets/sfx/**` (raw drops
  and normalized pack/ alike). Fresh clones now get a working sound-effect
  library out of the box; no need to run `scripts/build_sfx_pack.py` before
  `scripts/add_sfx.py` or `scripts/finalize.py` produce audio.
- **`scripts/build_sfx_index.py`** — scans every file in `assets/sfx/`
  (raw drops + pack/), ffprobes duration / sample rate / channels and
  ffmpeg-volumedetects peak + mean dBFS, heuristically categorizes by
  filename (whoosh / whip / ding / impact / money / magic / click / pop /
  ui / riser / camera / error / beep), suggests one or more slot mappings
  per file, and writes `assets/sfx/index.json` (structured) +
  `assets/sfx/CATALOG.md` (human-readable, grouped by slot). Makes
  choosing which raw drop fills which slot a lookup rather than a listen.
- **Expanded SFX pack** — `scripts/build_sfx_pack.py` curation now ships
  **46** normalized variants (was 11) across 7 slots. Every raw drop
  with a clean source peak (≤ -10 dBFS) is now in rotation; unusable
  drops (very-quiet whips/icons, redundant mouse-clicks) were deleted
  from `assets/sfx/`. Only `gong.mp3` stays excluded with a documented
  reason (needs a future "stinger" slot).
- **New `wrong-answer` SFX slot** — semantic trigger that fires on the
  first negative-outcome word in a clip (crashed / scammed / rugged /
  bankrupt / rekt / ...). Tuned for crypto-finance content; configurable
  via `Config.negative_keywords`. Quiz-show buzz feel — punctuates "the
  bad thing happened" without piling on. Respects `sfx_semantic_mode`
  (sparing/every/off). Shipped variants: `Errror.wav` + `windows error.mp3`.
  4 new tests; total now 49.

### Changed
- `.gitignore` excludes `.claude/` (local Claude Code session state).

## [0.5.0] — Unreleased

### Added
- **`shortsmith doctor`** — new CLI command that prints a green/red health
  checklist (ffmpeg, uv, npm, sibling venvs, Hyperframes kit, Remotion node_modules,
  SFX pack, YuNet model, API key). Returns non-zero exit if any required check
  fails. Run after `setup.sh` or when a pipeline misbehaves.
- **Network hardening in `scripts/gen_broll.py`**: on-disk cache
  (`.cache/broll-fetch/<sha1>.<ext>`), polite throttle (≥0.5s between hits with
  jitter), exponential backoff on 429/503, identifying User-Agent
  (`shortsmith/0.5 (+https://github.com/highbaud/shortsmith)`). New CLI flags
  `--offline` and `--no-cache`. Env vars `SHORTSMITH_BROLL_OFFLINE` and
  `SHORTSMITH_BROLL_NOCACHE`. Catches the rate-limit cliff a 1000-clip reprocess
  would otherwise trip on.
- **`scripts/finalize.py --skip-remotion` and `--skip-sfx` flags** plus
  `--offline`. Phase failures stay non-fatal (one short failing Remotion no
  longer kills the run).
- **`whisperx-align/` bundled in-tree** as a sibling uv project (same pattern
  as `audio-enhance/`). `setup.sh` / `setup.ps1` now `uv sync` it too. Public
  clones get the WhisperX quality improvement instead of silently falling back
  to faster-whisper.
- **14 new tests** — 8 for the gen_broll HTTP layer (cache hits, offline,
  nocache, retry on 429, fail-fast on 404, etc.) and 6 for finalize.py arg
  handling (--skip-remotion / --skip-sfx / --offline routing, empty pack
  error path). Total now 45 tests, still <0.5s.

### Changed
- **README rewritten** for v0.4+ reality. Shows the actual 11-phase pipeline
  (Phase A clip selection → Phase B audio/alignment/face → Phase C
  scaffold/render/caption/b-roll/SFX) and the `finalize.py` deliverable.
- **docs/ARCHITECTURE.md rewritten** with the 11-phase breakdown, including
  the asymmetric boundary snap, stutter repair, loudnorm pass, biggest-face-wins
  reframe, Remotion + SFX layers, and crash-recovery checkpoints.
- **docs/SFX.md and docs/REMOTION.md** added — subsystem-level docs.
- `.gitignore` excludes `whisperx-align/.venv/`, `whisperx-align/checkpoints/`,
  and the new `.cache/` directory.

## [0.4.0] — Unreleased

### Added
- **Sound effects (SFX) overlay pass** (`shortsmith/sfx.py` + `scripts/add_sfx.py`).
  Post-render mixer that lays approved one-shots on top of the speech:
  structural triggers (hook impact at t=0, swipe-in/out on callouts) and
  semantic triggers (cash-register on first money word, ding on bigstat
  numbers). Non-destructive — writes `final_sfx.mp4` beside the input.
- **Curated SFX pack** at `assets/sfx/pack/` with `pack.json` mapping each slot
  to one or more rotated variant files. `scripts/build_sfx_pack.py` builds the
  pack from raw drops in `assets/sfx/`. Pack is level-normalized (-9 dBFS peak)
  and sits 10-16 dB under speech.
- **Remotion render layer** (`remotion/` + `scripts/render_remotion.py` +
  `scripts/apply_remotion.py`). Layers word-level captions and AI-selected
  b-roll over the Hyperframes base render. Produces `final_remotion.mp4`.
- **Heuristic + LLM b-roll engine** (`scripts/gen_broll.py` +
  `prompts/gen_broll.md`). Builds `broll.auto.json` listing logo / chart /
  stock-image picks tied to spoken keywords, sourced from public-domain CC
  feeds (Wikimedia Commons, Openverse, Wikipedia).
- **`scripts/finalize.py`** — three-phase finisher: Phase 0 (Remotion) →
  Phase 1 (SFX) → Phase 2 (consolidate all `final_sfx.mp4` + `caption.txt`
  into `<kit>/renders/_all/`). Idempotent and authoritative.
- **`PROJECT_STATE.md`** — top-level resume document for picking the project
  back up in a fresh session.
- **SFX config knobs** in `shortsmith/config.py`: `sfx_enabled`, `sfx_gain`,
  `sfx_limit`, `sfx_slot_gain` per-slot dict, `sfx_semantic_mode`
  (`sparing`/`every`/`off`), money-word list. `SFX_DIR` resolves to
  `assets/sfx/pack/` by default.
- **9 new SFX tests** in `tests/test_sfx.py`. Total test count now 31.

### Changed
- `.gitignore` excludes `remotion/node_modules/`, `node_modules/`, and
  generated `broll.auto.json` files.

## [0.3.0] — Unreleased

### Added
- **WhisperX forced alignment** (`shortsmith/align.py`) — re-transcribes each
  enhanced clip via wav2vec2 to ~20ms word boundaries. Replaces step 6's
  in-process faster-whisper retranscribe. Sharper karaoke captions, cleaner
  cut seams. Runs in sibling `whisperx-align/` uv project; falls back to
  faster-whisper if unavailable.
- **Loudness normalization** (`shortsmith/normalize.py`) — two-pass ffmpeg
  `loudnorm` after step 5 enhancement. Default target -14 LUFS (TikTok /
  Instagram / YouTube short-form playback standard).
- **Stutter / immediate-repetition repair** in clean step. Collapses runs of
  identical adjacent stems separated by <350ms (configurable). Preserves
  deliberate emphasis with normal pacing.
- **Crash-recovery checkpoints** (`shortsmith/checkpoint.py`). Per-step
  `.progress.json` in each work dir. Resume picks up where the last successful
  step ended instead of re-running everything.
- **Better Whisper error messages** — OOM, CUDA, and compute-type failures now
  print actionable hints (e.g., "set SHORTSMITH_WHISPER_MODEL=medium") instead
  of raw torch stack traces.
- **Unit tests** (`tests/`): boundary snap, normalize, scaffold callouts +
  hook, stutter repair. 22 tests, runs in <0.1s, no GPU or API key required.
- **`scripts/redo_outdated.py`** — re-process work dirs whose `cut_manifests.json`
  predates a quality-fix epoch.
- **`run_everything.ps1`** — Windows wrapper to chain `batch_pipeline.py` +
  `redo_outdated.py` in sequence.

### Changed
- CI workflow now runs `pytest tests/` on all three OSes.
- `.env.example` documents `SHORTSMITH_LUFS`, `SHORTSMITH_ALIGN`,
  `SHORTSMITH_WHISPERX_ALIGN`.

## [0.2.0] — Unreleased

### Added
- **First-run wizard** in `shortsmith run`. If `SHORTSMITH_CLIP_ENGINE` or
  `SHORTSMITH_STYLE` aren't set, prompt the user interactively (terminal only)
  and persist their choices to `.env`.
- **Local-LLM clip selection backend** (`--clip-engine ollama`). Works with
  any OpenAI-compatible local endpoint — Ollama, LM Studio, vLLM. Marked
  EXPERIMENTAL; expect lower-quality picks vs Claude Opus.
- **`shortsmith/find_clips/` package**: dispatcher + `anthropic.py` + `ollama.py` +
  shared `_common.py` (transcript formatting, JSON parsing, normalization).
- **Visual style presets** (`templates/styles/<name>/style.json`):
  - `xrp-revolution` (default) — premium, gold/red/green, Anton display.
  - `minimal` — clean editorial, Inter only, single yellow accent.
  - `bold` — loud high-contrast, electric yellow + magenta + cyan.
- `--clip-engine`, `--style`, `SHORTSMITH_CLIP_ENGINE`, `SHORTSMITH_STYLE`,
  `SHORTSMITH_LOCAL_LLM_URL`, `SHORTSMITH_LOCAL_LLM_MODEL`,
  `SHORTSMITH_LOCAL_LLM_TEMP` env vars.

### Changed
- `Config.validate()` no longer demands `ANTHROPIC_API_KEY` when
  `clip_engine == "ollama"`.
- README + `.env.example` document both backends and all three styles.

## [0.1.0]

Initial public release.

### Added
- **8-step pipeline**: transcribe → find clips (Claude API) → cut + reorder → clean
  (filler + silence) → enhance audio → retranscribe → reframe 9:16 → scaffold
  Hyperframes project.
- **ClearerVoice-Studio MossFormer2_SE_48K** as the default audio engine. Runs in
  the in-tree `audio-enhance/` sibling uv project to avoid torch/numpy version
  conflicts with the main shortsmith venv.
- **YuNet face tracking** for 9:16 reframing, with biggest-face-wins filtering
  that survives PIP cameras and chat overlays on 4K source footage.
- **Hyperframes scaffold** targeting the `hyperframes-student-kit` git submodule.
- **Configurable** via `SHORTSMITH_*` environment variables and `.env`.
- **Cross-platform setup**: `setup.sh` (macOS/Linux), `setup.ps1` (Windows).
- **Bundled smoke test** (`scripts/smoke_test.py`) — runs end-to-end without an
  API key against `examples/sample_clip.mp4`.
- **YuNet face detection model** bundled (`models/face_detection_yunet_2023mar.onnx`, ~230 KB).
- **MIT license**.

### Known limitations
- Single-speaker assumption (no diarization). Multi-speaker content frames
  whoever the largest detected face is.
- Anthropic API required for step 2. Estimated cost ~$0.50–$2.00 per 3-hour
  source video. Manual / local-LLM clip selection is a planned future feature.
- ClearerVoice installation requires Python 3.10–3.11 inside `audio-enhance/`
  (separate from the main venv's Python 3.12).
- Tested on Windows + Linux with NVIDIA CUDA. macOS / MPS untested.
