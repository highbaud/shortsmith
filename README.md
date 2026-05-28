# shortsmith

**Long-form video in. Batch of viral 9:16 Hyperframes-ready shorts out.**

Local-first pipeline that turns a multi-hour podcast or livestream into a folder
of polished short-form clips — each one face-tracked to vertical, filler-free,
audio-enhanced, with a slam hook + accent callouts + paste-ready Instagram caption.

```
your-video.mp4 (3hr podcast, talking-head)
       │
       ├─ 1. Transcribe (faster-whisper)
       ├─ 2. Find evergreen viral clips (Claude API)
       ├─ 3. Cut + reorder for hook-first delivery (ffmpeg)
       ├─ 4. Remove silences + filler words
       ├─ 5. Enhance speech audio (ClearerVoice MossFormer2_SE_48K)
       ├─ 6. Re-transcribe each clip (clean timeline)
       ├─ 7. Reframe 9:16 with face tracking (YuNet, biggest-face-wins)
       └─ 8. Scaffold a Hyperframes project per clip
              ↓
       npx hyperframes render  →  final.mp4
```

## Quick start

```bash
git clone --recurse-submodules https://github.com/highbaud/shortsmith
cd shortsmith
./setup.sh                                 # or .\setup.ps1 on Windows
# edit .env to add your ANTHROPIC_API_KEY
uv run shortsmith run path/to/your-video.mp4
```

Forgot `--recurse-submodules`? Run `git submodule update --init --recursive`.

## Requirements

- **Python 3.12** (managed by [`uv`](https://docs.astral.sh/uv/))
- **ffmpeg** on PATH
- **NVIDIA GPU** strongly recommended (Whisper large-v3 + ClearerVoice both prefer CUDA)
- **Anthropic API key** — used once per source video for clip selection
- **Node 18+** if you want to render the scaffolded projects with Hyperframes

See [docs/SETUP.md](docs/SETUP.md) for per-OS install steps, CUDA torch matrix
(RTX 50 / 40 / 30 / older), and first-run model download sizes.

## Clip-selection backend: API vs free local

Step 2 (find viral clips) can run two ways. The first-run wizard asks once and
saves your choice; override anytime with `--clip-engine` or `SHORTSMITH_CLIP_ENGINE`.

| Backend | Quality | Cost | Setup |
|---|---|---|---|
| `anthropic` (default) | Best | $0.10–$2.00 per source video | Just set `ANTHROPIC_API_KEY` |
| `ollama` (experimental) | Lower; spot-check picks | Free | Run an OpenAI-compatible local server |

**For free local picking**, install [Ollama](https://ollama.com/):

```bash
# Pull a model (~40-48 GB VRAM for 70B; smaller models work but produce weaker picks):
ollama pull llama3.1:70b
ollama serve

# Run shortsmith with the local backend:
uv run shortsmith run path/to/video.mp4 --clip-engine ollama
```

The Ollama backend also works against LM Studio or vLLM — point
`SHORTSMITH_LOCAL_LLM_URL` at any OpenAI-compatible endpoint.

The system prompt lives at [`prompts/find_viral_clips.md`](prompts/find_viral_clips.md).
Edit it to tune the rubric for your content. If you'd rather skip step 2
entirely, hand-write a `clips.json` and pass `--from-step 3`.

## Visual style presets

Three preset styles ship in [`templates/styles/`](templates/styles/):

| Preset | Vibe | Fonts | Colors |
|---|---|---|---|
| `xrp-revolution` (default) | Premium, high-energy | Anton + Bebas Neue + Inter | gold #f5c842 / red #ff3653 / green #2dffa8 |
| `minimal` | Clean editorial | Inter only | yellow #facc15 single accent |
| `bold` | Loud, attention-grabby | Bebas Neue + Anton | electric yellow + magenta + cyan |

Pick at run time with `--style` or set `SHORTSMITH_STYLE`. Each preset is a
`style.json` with colors, fonts, hook size, and overlay flags — copy
`templates/styles/xrp-revolution/style.json` to a new directory and tweak it
to make your own preset.

## Configuration

All paths and tunables can be overridden via environment variables or a
project-local `.env` (auto-loaded). See [`.env.example`](.env.example) for the
full surface. The high-traffic knobs:

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Claude API key |
| `SHORTSMITH_WHISPER_MODEL` | `large-v3` | `small` / `medium` / `large-v2` / `large-v3` |
| `SHORTSMITH_WHISPER_DEVICE` | `cuda` | `cuda` / `cpu` |
| `SHORTSMITH_MIN_SCORE` | `7` | Reject clips below this viral score (1–10) |
| `SHORTSMITH_CLIP_ENGINE` | `anthropic` | `anthropic` (Claude API) / `ollama` (local LLM) |
| `SHORTSMITH_LOCAL_LLM_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint when engine=ollama |
| `SHORTSMITH_LOCAL_LLM_MODEL` | `llama3.1:70b` | Model name to request from the local server |
| `SHORTSMITH_STYLE` | `xrp-revolution` | `xrp-revolution` / `minimal` / `bold` |
| `SHORTSMITH_ENHANCE` | `clearvoice` | `clearvoice` / `voicefixer` / `resemble` / `deepfilter` |
| `SHORTSMITH_KIT_ROOT` | `./hyperframes-student-kit` | Override if your kit lives elsewhere |
| `SHORTSMITH_AUDIO_ENHANCE` | `./audio-enhance` | Override if you keep audio-enhance elsewhere |

## Common operations

```bash
# Smoke test (no API key needed)
uv run python scripts/smoke_test.py

# Full pipeline on a single video
uv run shortsmith run path/to/your-video.mp4

# Cap clip count for a faster first run
uv run shortsmith run path/to/your-video.mp4 --max-clips 3

# Resume from a specific step (e.g., re-render after tweaking templates)
uv run shortsmith run path/to/your-video.mp4 --from-step 8

# Skip audio enhancement (faster iteration loop)
uv run shortsmith run path/to/your-video.mp4 --no-enhance

# Override audio engine
uv run shortsmith run path/to/your-video.mp4 --engine voicefixer
```

For batch operations (many source videos), see the scripts under [`scripts/`](scripts/).

## After the pipeline runs

Each clip becomes a self-contained Hyperframes project under
`hyperframes-student-kit/video-projects/auto-shorts/<source-slug>/short-NN-<hook>/`:

```bash
cd hyperframes-student-kit/video-projects/auto-shorts/<source-slug>/short-01-<hook>

# Live preview at http://localhost:3002
npx hyperframes preview

# Render the final 1080×1920 mp4
npx hyperframes render
```

A paste-ready Instagram caption sits at `caption.txt` inside each project (and
at the parent `auto-shorts/<source-slug>/short-NN-<hook>.txt` so you can scan
them all side-by-side).

## What this is NOT (yet)

- Multi-speaker / diarized content — single talking-head only in v0.1.
- A hosted service — this is a local CLI tool. Bring your own GPU.
- A clip-selection tool without an API — the picker uses Claude. (Manual
  clips.json works fine, see `--from-step 3`.)

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the 8-step pipeline, deep-dive.
- [docs/SETUP.md](docs/SETUP.md) — install per OS, CUDA torch matrix, model downloads.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — common errors and fixes.
- [CONTRIBUTING.md](CONTRIBUTING.md) — PR checklist, where to file issues.

## License

[MIT](LICENSE). Use it for whatever, just don't blame me when your shorts go
viral and your DMs become unmanageable.
