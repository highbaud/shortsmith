# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] — Unreleased

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
