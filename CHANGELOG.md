# Changelog

All notable changes to this project will be documented in this file.

## [0.6.0] - Unreleased

### Fixed

**Hardening pass before merge.** Six parallel reviews across the whole tree, each
bug reproduced before it was fixed and pinned by a test that fails without the
fix. 258 tests to 353.

Render-killing crashes:

- **One malformed b-roll slide destroyed the whole render.** A `list` slide with
  no `items` or a `logo`/`person` slide with no `src` reached the Remotion
  composition intact and threw (`.map` of undefined, `staticFile(undefined)`),
  exit 1, losing every cutaway in the short rather than the one bad slide.
  Slides are LLM-written, and Python only ever checked `start` and `end`. Now
  validated at both stages, since the contract differs: `gen_broll._normalize`
  requires the identity key a slide is resolved by, and
  `render_remotion._validate_broll` requires the `src` that resolution produced.
  `Short.tsx` drops the same shapes as a second line of defense.
- **A malformed hook in one clip threw away every rendered project in the batch**
  (`scaffold._build_hook`), as did a string `rank` from the model at
  `find_clips._common.normalize_clips`, whose sort sits outside the per-clip
  `try`. A bad `broll.json` did the same via `sys.exit` through finalize's
  handler, now a catchable `BrollSpecError`.

Wrong output that reported success:

- **A stale video could ship.** The render stamp hashed `int(st_mtime)`, so two
  different base renders of the same byte size inside one second read as
  identical. Now nanoseconds. Three real inputs were also outside the code
  digest (`config.py`, `sfx.py`, `templates/styles/*/style.json`), a
  `STAMP_VERSION` bump did not force a re-render, and a corrupt stamp reverted
  to the weaker mtime rule instead of re-rendering.
- **Transcript reuse could caption a short from the wrong transcript.** The tag
  match was a substring test, so `transcript-xrp.json` matched
  `xrp-deaton-interview.mp4`; with both present the winner depended on directory
  order. Longest match now wins.
- **A person photo was downloaded, then discarded.** `_resolve_assets` popped the
  `person`/`brand` key it had just resolved from, so `verify_person_slides` saw
  an empty name and dropped the slide reporting `no verified photo for ''`.
- **`--skip-sfx` consolidated nothing and printed success**, because phase 2 only
  ever looked for `final_sfx.mp4`.
- **A deleted Wikidata pin looked resolved.** `wbgetentities` returns a truthy
  `missing` stub, which defeated the documented fall-through to search.

Windows correctness:

- **24 `subprocess` calls decoded output without `encoding="utf-8"`.** On Windows
  `text=True` decodes as cp1252, and a byte such as `0x81` (present in Á, Í, Ð,
  kana) kills the reader thread so `stdout` comes back as `None`. That crashed
  the SFX index scan and, worse, silently gave `add_sfx` a duration of `0.0`, so
  every sound effect fired at `t=0` and the run reported success.
- `names.fix_words` wrote `"end": null` into words.json when merging two tokens
  that both lacked an end, turning a skippable word into one that crashed
  captions. `align` crashed with `KeyError` on `--from-step 6` after a run that
  stopped at step 3. `checkpoint` crashed on exactly the half-written file it
  exists to recover from. A whisperx timeout orphaned the process, which then
  contended for the GPU with its own fallback.

### Changed

- **CI now discovers modules instead of listing them.** The hand-written import
  list had gone stale: `shortsmith.names`, `.layouts`, `.gallery` and every
  `scripts/` module were never imported by it, so an import error reached a
  render run before it reached CI. `tests/test_import_smoke.py` walks the tree
  and also asserts no module does work at import time.
- Failures that used to be swallowed now report: ffmpeg reasons keep their
  stderr instead of `returned non-zero exit status 1`, a malformed layout preset
  raises `ValueError` so its own handler can fire, and `probe_duration` warns
  rather than returning `0.0` in silence.

### Changed
- **Split-stack shorts get their b-roll back.** They lost all of it when the
  layout landed, because the logo badge's upper-center spot is the top
  speaker's face. The badge now sits as a mark-only tile on the blurred
  backdrop beside the top square (`render_remotion._logo_badge_anchor`, passed
  to Remotion as `logoBadgeAnchor`), below every platform's top bar and outside
  both squares and the caption band. Full-frame cutaways play as on any other
  short. A preset with no backdrop beside the squares drops the badges only.
- **Nobody on camera gets a cutaway.** `gen_broll` drops a person slide for
  anyone in the clip spec's `speakers` list, matched on the full name or the
  surname.
- **Unpinned names rank by notability.** `person_photos.resolve_identity` asks
  Wikidata for sitelinks and scores +1 per five (capped at +10); an exact label
  match is worth one point, down from two. A human with fewer than five
  sitelinks resolves only when the role hint matched. This is what stops
  "Michael Saylor" resolving to a substitute teacher whose label is exact.
- **Failures are loud.** A split-stack clip whose layout preset cannot load
  raises `LayoutPresetError` instead of silently falling back to face-aware
  caption placement (which on a stacked frame means captions on a face).
  A b-roll generation failure is printed with a `!!` prefix and counted in
  finalize's Phase 0 summary instead of vanishing in the scroll.
- `scripts/calibrate.py` gained its first tests (analytics normalization, the
  ledger join, the emitted calibration files) and uses timezone-aware UTC.
- **Person detection hears surnames and mishearings.** Across 382 shorts, 53
  transcripts mentioned a curated person and the full-name match fired in 28
  ("Trump comes out with that", "Sailor lost $6 billion", "CZ said" got no
  cutaway). Each person now carries surname aliases (`PERSON_ALIASES`) where
  nothing else in finance talk shares the word, plus ASR variants
  (`ASR_VARIANTS`: "Sailor", "Larson") that count only when capitalized
  mid-sentence or preceded by the first name. Common-word surnames (Wood, Fink,
  Powell, Armstrong, Huang, Schwartz) stay full-name only, a surname after
  someone else's first name ("Barron Trump") is not a match, and possessives no
  longer hide a mention ("Gensler's"). Brand mentions get the same possessive
  handling, so "Ripple's" now times its logo badge instead of dropping it.
- **Remotion 4.0.468 → 4.0.499, and `<OffthreadVideo>` → `@remotion/media`.**
  `@remotion/media` became stable and recommended in 4.0.491; its `<Video>` is
  the successor to core's `<OffthreadVideo>` and skips the per-frame headless
  screenshot round-trip. All `@remotion/*` packages are now pinned exactly (they
  must move in lockstep) rather than floating on `4.0.*`. B-roll `<Img>` slides
  premount 15 frames early so a photo is decoded before its cutaway starts
  instead of popping in a frame late. Verified by rendering a still through the
  real composition.
- **Hyperframes CLI is pinned** to `0.7.71` via `config.HYPERFRAMES_SPEC` /
  `config.hyperframes_cmd()`, and all four render call sites go through it.
  Bare `npx hyperframes` resolved to whatever was latest at that moment, so an
  upstream release could change every render with no diff and no warning, and
  the renders are the product. `shortsmith doctor` reports the pin; set
  `SHORTSMITH_HYPERFRAMES_VERSION=latest` to float off it deliberately.
- Wikidata/Commons API plumbing shared by the b-roll resolvers now lives in
  `scripts/wikidata.py`.

### Fixed
- **Person cutaways from before identity verification were still being
  delivered.** The verification fix changed how a photo is fetched, but every
  short generated earlier kept its keyword-search `assets/broll/person-*.jpg`
  and a `broll.auto.json` pointing at it, and `render_remotion` trusted that
  `src`. `finalize.py` then re-used those Remotion renders (its up-to-date
  check compares mtimes against the base render, so a resolver fix never
  invalidated them) and copied them to `_all/` on July 31: "David Schwartz"
  was a photo of Anna Schwartz, "Michael Burry" was a coastal landscape. Now
  `_merge_broll` passes every auto person slide through the new
  `gen_broll.verify_person_slides()`, which re-resolves the name (cache first,
  Wikidata otherwise), rewrites `src` to the verified file, and drops a person
  with no verified photo. Hand-authored `broll.json` slides pass through as
  written. The three delivered shorts were re-rendered.
- **Logo b-roll slides were missing for 12 of 41 curated brands**, including
  BlackRock and JPMorgan, two of the most-mentioned institutions in this
  channel's content. Simple Icons covers 27 of the 41 and vectorlogo.zone
  answered for only 2 more; the rest resolved to nothing and the slide was
  dropped without comment.

  New [`scripts/brand_logos.py`](scripts/brand_logos.py) adds a verified middle
  tier (Wikidata's P154 "logo image") taking coverage to 34 of 41 with every
  mark verified and vectorlogo.zone demoted to a last resort it no longer
  reaches. Building it surfaced five wrong logos that the naive version would
  have shipped, each now a named regression test:

  * `Quant` resolved to the TV series **Quantico**, and `XDC` to Sony **XDCAM**.
    Fixed by matching Wikidata labels on a word boundary.
  * `Kraken` resolved to a **Colombian metal band** genuinely labelled "Kraken".
    Fixed by requiring company properties (industry / HQ / legal form).
  * `Microsoft` resolved to its **1980 wordmark**, because P154 statements run
    chronologically and the code took claims[0]. Fixed by ordering on Wikidata
    rank and demoting claims with an end-time qualifier.
  * `Fidelity` resolved to **Fidelity International**, a different company from
    the Fidelity Investments US financial content means. Fixed by pinning to
    the right QID, which has no logo, so no slide.

  Common-noun brands (`swift`, `circle`, `flare`, `quant`, `anchorage`, `xdc`)
  now never resolve by search; only a pinned QID can supply them. Simple Icons
  results are checked against the mark's own `<title>` so an upstream slug
  reassignment can't serve another brand's icon. New:
  `gen_broll.py --audit-brands`.
- **Person b-roll slides showed the wrong person.** `gen_broll.py` picked photos
  by keyword-searching Wikimedia Commons / Openverse / Wikipedia for the name,
  shuffling the results, and downloading the first one that decoded. Nothing
  ever checked that the image was of that person. Commons file search matches
  any file whose *description* mentions the words, so "David Schwartz" returned
  a photo of Anna Schwartz, and the Wikipedia fallback resolved to an American
  composer. Because the pool was shuffled, *which* wrong person appeared changed
  between runs.

  Replaced with identity verification in the new
  [`scripts/person_photos.py`](scripts/person_photos.py): the name (plus the
  slide's `role` as a disambiguating hint) resolves to a **Wikidata human
  entity** first, and only images bound to that entity are eligible: its
  designated portrait (P18), Commons files whose structured data says they
  *depict* it (P180), and members of its own Commons category (P373). Candidates
  are ranked to prefer P18 and solo portraits, and reject signatures, graves,
  plaques, logos and group shots. **If identity can't be established the slide is
  dropped** rather than guessed at.

  29 curated people are pinned to verified QIDs, which matters more than it
  sounds: unpinned, "Michael Saylor" resolves to a substitute teacher in
  Kentucky (exact label match) and "Jim Rickards" resolves to nothing (his item
  is "James G. Rickards"). Four people (Michael Burry, David Schwartz, Jed
  McCaleb, Satoshi Nakamoto) have no free portrait at all and now correctly
  produce no slide.

  Verified photos are cached repo-wide in `assets/people/` instead of per-short,
  so a person looks the same in every short; `assets/people/people.json` records
  name → QID → Commons file as an audit trail. New:
  `gen_broll.py --audit-people` prints the resolved identity and chosen photo
  for every curated person, `--fresh-photo` bypasses the cache, and
  `PERSON_PHOTO_OVERRIDES` forces a specific file. Openverse is no longer used
  for people (broad keyword match, drifts to the wrong subject).

### Added
- **Names are spelled right in captions.** New `shortsmith/names.py` holds a
  glossary of the people and terms this channel says (Saylor, Garlinghouse,
  Gensler, Deaton, XRPL, RLUSD...) and the mishearings Whisper produces for
  them. The glossary is handed to faster-whisper as `initial_prompt`
  (`Config.whisper_initial_prompt`, env `SHORTSMITH_WHISPER_PROMPT`) and to the
  WhisperX worker as `WHISPERX_INITIAL_PROMPT`; `names.fix_words` then respells
  what survives ("Sailor" -> Saylor, "Larson" -> Larsen, "Garling house" ->
  Garlinghouse, "xrpl" -> XRPL), at transcription and again after alignment.
  A mishearing that is a real word is corrected only when capitalized or
  preceded by the first name. Across the 382 existing transcripts it would
  change six tokens, all of them right.
- **Render stamps.** New `scripts/render_stamp.py`. `apply_remotion` used to
  compare one pair of file dates (the Remotion output against the Hyperframes
  base), so a resolver fix, a caption change or a new photo never re-rendered
  anything. Each render now writes a digest of every input beside itself and
  is skipped only when the digest still matches; a mismatch re-renders and
  says which inputs changed. Shorts rendered before stamps existed keep the
  old rule until `--force-remotion`, so an unscoped finalize does not rebuild
  the library unasked, and the phase summary counts them.
- **Provenance beside manual photos.** A `<slug>.json` next to
  `assets/people/manual/<slug>.jpg` (`source_url`, `image_url`, `license`,
  `added`) is copied into `people.json` and shown by `--audit-people`.
- **Operator-supplied person photos.** `assets/people/manual/<slug>.<jpg|png|webp>`
  is checked before the cache and before Wikidata, so a person with no free
  portrait (Michael Burry, Jed McCaleb, David Schwartz) can still get a cutaway
  from a photo you provide, and it wins even against `--fresh-photo`.
  `--audit-people` lists such a person as `[manual]`; the manifest records the
  file. Nothing checks the license.
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

## [0.5.1] - 2026-05-28

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

## [0.5.0] - 2026-05-28

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

## [0.4.0] - 2026-05-28

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

## [0.3.0] - 2026-05-28

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

## [0.2.0] - 2026-05-28

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
