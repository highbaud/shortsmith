# Contributing to Shortsmith

Thanks for considering a contribution! This is a solo-built project that turned
into something more useful than expected. PRs welcome.

## Development setup

```bash
git clone --recurse-submodules https://github.com/highbaud/shortsmith
cd shortsmith
./setup.sh           # or .\setup.ps1 on Windows
uv sync --extra dev  # installs ruff, pytest, pytest-mock
```

Edit `.env` to add your `ANTHROPIC_API_KEY` (only needed for full pipeline runs;
the smoke test runs without one).

## Verifying changes

Before submitting a PR, run:

```bash
uv run ruff check .
uv run python scripts/smoke_test.py
```

The smoke test exercises steps 3–8 of the pipeline (the steps that touch ffmpeg,
OpenCV, Jinja, and the Hyperframes scaffold) using `examples/sample_clip.mp4`
and a hand-crafted clips.json. No API key required.

## Code style

- Python 3.12, `from __future__ import annotations` at the top of every module.
- 100-column lines (black-compatible).
- Type hints on public functions.
- One responsibility per file; the pipeline step modules (`cut_clips.py`,
  `clean_clips.py`, etc.) should each cleanly map to one step in the README diagram.

## PR checklist

- [ ] Smoke test passes locally.
- [ ] `ruff check .` is clean.
- [ ] If you changed CLI flags or env vars, README config table updated.
- [ ] If you added a new pipeline step or changed an existing one, `docs/ARCHITECTURE.md` updated.
- [ ] `CHANGELOG.md` entry added under `[Unreleased]`.

## Where to file issues

- **Shortsmith pipeline behavior** (wrong cuts, framing problems, scaffold breaks,
  rubric tuning): this repo.
- **ClearerVoice install / model behavior**:
  [modelscope/ClearerVoice-Studio](https://github.com/modelscope/ClearerVoice-Studio).
- **Hyperframes render / preview / lint issues**: the
  hyperframes-student-kit repo (the submodule).
- **Whisper transcription accuracy**:
  [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) — but check
  if a higher Whisper model helps first (`SHORTSMITH_WHISPER_MODEL=large-v3`).

## What I'm explicitly NOT looking for in v0.x

- Diarization / multi-speaker support (planned for v0.2).
- Web UI (not on the roadmap).
- Vendor-lock changes (e.g., swapping ffmpeg for a hosted service).
- Local-LLM clip-picker as the default (happy to add as an alternative, but
  Anthropic stays the default).
