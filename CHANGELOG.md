# Changelog

All notable changes to this project will be documented in this file.

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
