<!-- shortsmith-ansi-logo -->
<div align="center">
<pre>
░▒▓▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▓▒░

███████╗██╗  ██╗ ██████╗ ██████╗ ████████╗███████╗███╗   ███╗██╗████████╗██╗  ██╗
██╔════╝██║  ██║██╔═══██╗██╔══██╗╚══██╔══╝██╔════╝████╗ ████║██║╚══██╔══╝██║  ██║
███████╗███████║██║   ██║██████╔╝   ██║   ███████╗██╔████╔██║██║   ██║   ███████║
╚════██║██╔══██║██║   ██║██╔══██╗   ██║   ╚════██║██║╚██╔╝██║██║   ██║   ██╔══██║
███████║██║  ██║╚██████╔╝██║  ██║   ██║   ███████║██║ ╚═╝ ██║██║   ██║   ██║  ██║
╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝     ╚═╝╚═╝   ╚═╝   ╚═╝  ╚═╝

             ─────────────  ◄  l o n g   v i d e o   ►  ─────────────            
                    v i r a l   s h o r t s   p i p e l i n e                    

░▒▓▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▓▒░
</pre>
</div>

# shortsmith

**Long-form video in. Batch of viral 9:16 captioned-with-b-roll-with-SFX shorts out.**

Local-first pipeline that turns a multi-hour podcast or livestream into a folder
of polished short-form clips — face-tracked vertical, filler-free, audio-enhanced
to broadcast loudness, word-aligned karaoke captions, AI-selected b-roll, and a
curated sound-effect overlay. End-to-end, your machine. Three command sequence
from raw mp4 to publish-ready folder.

```
your-video.mp4 (3hr podcast, talking-head)
       │
       │ ┌─── PHASE A: pick + cut + clean clips ───┐
       ├─┤ 1. Transcribe (faster-whisper)          │
       │ │ 2. Find evergreen viral clips (Claude or Ollama) │
       │ │ 3. Cut + reorder for hook-first delivery │
       │ │ 4. Remove silences, fillers, stutters   │
       │ └────────────────────────────────────────┘
       │ ┌─── PHASE B: audio + alignment + face ───┐
       ├─┤ 5. Enhance speech (ClearerVoice MossFormer2_SE_48K) │
       │ │    + two-pass loudnorm to -14 LUFS       │
       │ │ 6. Force-align words (WhisperX wav2vec2, ~20ms) │
       │ │ 7. Reframe 9:16 (YuNet biggest-face)     │
       │ └────────────────────────────────────────┘
       │ ┌─── PHASE C: render + caption + b-roll + SFX ───┐
       ├─┤ 8. Scaffold Hyperframes project          │
       │ │ 9. Hyperframes base render (slam hook + callouts) │
       │ │ 10. Remotion layer (word captions + AI b-roll) │
       │ │ 11. SFX overlay (swipe-in / cash / ding) │
       │ └────────────────────────────────────────┘
       ↓
  hyperframes-student-kit/renders/_all/
    <source>__short-01-<hook>.mp4    (publish-ready 1080×1920)
    <source>__short-01-<hook>.txt    (paste-ready Instagram caption)
    <source>__short-02-<hook>.mp4
    ...
```

## Quick start

```bash
git clone --recurse-submodules https://github.com/highbaud/shortsmith
cd shortsmith
./setup.sh                                  # or .\setup.ps1 on Windows
# edit .env to add your ANTHROPIC_API_KEY (or pick --clip-engine ollama)
uv run shortsmith run path/to/your-video.mp4
uv run python scripts/finalize.py           # captions + b-roll + SFX + consolidate
```

Forgot `--recurse-submodules`? Run `git submodule update --init --recursive`.

## Requirements

- **Python 3.12** (managed by [`uv`](https://docs.astral.sh/uv/))
- **ffmpeg** on PATH
- **NVIDIA GPU strongly recommended** (Whisper + ClearerVoice + WhisperX all prefer CUDA)
- **Node 18+** for Hyperframes render + Remotion captions layer
- **Anthropic API key** for clip selection (or run Ollama locally for free)
- **Sibling uv projects** for the heavy lifters — `audio-enhance/`, `whisperx-align/` (Python 3.10/3.11 each), set up by `setup.sh`

See [docs/SETUP.md](docs/SETUP.md) for per-OS install, CUDA torch matrix, model download sizes, and what `setup.sh` actually does.

## Cost note (clip selection)

Step 2 calls an LLM once per source video with the full transcript:

| Source length | Approx. cost (Claude Opus 4) | Free alternative |
|---|---|---|
| 30 min | $0.10 | Ollama llama3.1:70b |
| 1 hr | $0.20 | LM Studio + any 70B |
| 2 hr | $0.50 | vLLM + any OpenAI-compatible |
| 3 hr | $0.80 | Hand-write `clips.json`, run `--from-step 3` |

Switch backends with `--clip-engine ollama` or `SHORTSMITH_CLIP_ENGINE=ollama`. The rubric is at [`prompts/find_viral_clips.md`](prompts/find_viral_clips.md) — edit it for your content.

## The 11-phase pipeline (what each step does)

**1. Transcribe** — faster-whisper large-v3 on GPU, word-level timestamps. Reuses a sibling `transcript-<stem>.json` if present.

**2. Find viral clips** — Claude (or local LLM) reads the transcript and returns a `clips.json` with `viral_score`, `hook_text`, `callouts`, `instagram_caption`, and a `segments` list that can reorder a clip to lead with the hook.

**3. Cut + reorder** — ffmpeg cuts with tiered boundary snap (sentence-end → breath → any-gap). `prefer_after=True` on the end-of-clip snap so we extend forward to a clean sentence end instead of chopping a thought. 80 ms xfade at every reorder seam.

**4. Clean** — word-aware. Removes fillers (only pure stammers + "you know" by default; "like" / "basically" / "literally" left alone), collapses adjacent stutters (e.g. `I-I-I think` → `I think`), and trims silences > 0.8s. Cuts never land inside a word.

**5. Enhance audio** — ClearerVoice MossFormer2_SE_48K in a sibling uv venv. Two-pass ffmpeg `loudnorm` to **-14 LUFS** (TikTok / Instagram / YouTube short-form playback standard).

**6. Force-align** — WhisperX wav2vec2 re-aligns word boundaries to ~20 ms in a sibling uv venv (CUDA). Falls back to in-process faster-whisper retranscribe if WhisperX isn't installed.

**7. Reframe 9:16** — YuNet face detection. Biggest-face-wins filter (rejects PIP cameras + chat avatars on 4K source). IQR outlier rejection, EMA smoothing, single static crop per clip. Face center at 40% from top, occupies ~32% of vertical.

**8. Scaffold** — Self-contained Hyperframes project per clip. Slam hook (opening 2.6s), accent callouts (`caption` / `punch` / `bigstat` / `hero`), ambient bg with vignette + grain. Visual style driven by [one of three preset `style.json` files](templates/styles/).

**9. Hyperframes render** — `npx hyperframes render` produces the base mp4 with slam hook + callouts + Ken Burns on the face cam.

**10. Remotion layer** — `scripts/apply_remotion.py` overlays word-level karaoke captions on top of the base render, plus AI-selected b-roll (logos when a brand is named, CC photos when a person is named, charts when a number is cited) sourced from Wikimedia Commons + Openverse + Wikipedia. Output: `final_remotion.mp4`.

**11. SFX overlay** — `scripts/add_sfx.py` mixes a curated SFX pack onto the speech. Structural triggers (hook impact at t=0, swipe-in on callouts) + semantic triggers (cash register on first money word, ding on bigstat numbers). Levels approved: peaks at -9 dBFS, sits ~10–16 dB under voice, limiter at the end. Output: `final_sfx.mp4`.

**Consolidation** — `scripts/finalize.py` runs all three render phases and copies `final_sfx.mp4` + matching `caption.txt` into `<kit>/renders/_all/<source>__<short>.{mp4,txt}` with a flat naming scheme. Idempotent — safe to re-run.

## Configuration

All paths and tunables override via env vars or a project-local `.env` (auto-loaded). See [`.env.example`](.env.example) for the full surface. High-traffic knobs:

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required for anthropic engine) | Claude API key |
| `SHORTSMITH_CLIP_ENGINE` | `anthropic` | `anthropic` (Claude API) / `ollama` (local LLM) |
| `SHORTSMITH_STYLE` | `xrp-revolution` | `xrp-revolution` / `minimal` / `bold` |
| `SHORTSMITH_ENHANCE` | `clearvoice` | Audio enhancement engine |
| `SHORTSMITH_ALIGN` | `whisperx` | Word alignment (`whisperx` / `faster-whisper`) |
| `SHORTSMITH_LUFS` | `-14.0` | Loudness normalization target |
| `SHORTSMITH_SFX_SEMANTIC` | `sparing` | SFX mode: `sparing` / `every` / `off` |
| `SHORTSMITH_WHISPER_MODEL` | `large-v3` | `small` / `medium` / `large-v3` |
| `SHORTSMITH_MIN_SCORE` | `7` | Reject clips below this viral score (1–10) |

## Common operations

```bash
# Smoke test (no API key, no GPU required)
uv run python scripts/smoke_test.py

# Full pipeline on a single video, all 11 phases
uv run shortsmith run path/to/video.mp4
uv run python scripts/finalize.py

# Cap clips for a fast first run
uv run shortsmith run path/to/video.mp4 --max-clips 3

# Resume from a specific step (uses on-disk artifacts from previous steps)
uv run shortsmith run path/to/video.mp4 --from-step 5

# Skip audio enhancement (faster iteration loop)
uv run shortsmith run path/to/video.mp4 --no-enhance

# Free clip selection via local LLM
uv run shortsmith run path/to/video.mp4 --clip-engine ollama

# Different visual style
uv run shortsmith run path/to/video.mp4 --style minimal

# Re-process every existing work dir with the latest pipeline
uv run python scripts/reprocess_all.py
```

For batch operations across many source videos, see [`scripts/batch_pipeline.py`](scripts/batch_pipeline.py) and [`scripts/reprocess_all.py`](scripts/reprocess_all.py).

## Visual style presets

Three preset styles ship at [`templates/styles/`](templates/styles/) — each a `style.json` driving one parameterized template:

| Preset | Vibe | Fonts | Colors |
|---|---|---|---|
| `xrp-revolution` (default) | Premium, high-energy | Anton + Bebas Neue + Inter | gold #f5c842 / red #ff3653 / green #2dffa8 |
| `minimal` | Clean editorial | Inter only | yellow #facc15 single accent |
| `bold` | Loud, attention-grabby | Bebas Neue + Anton | electric yellow + magenta + cyan |

To make your own: copy any preset directory, edit `style.json`, set `SHORTSMITH_STYLE=<name>`.

## Sound-effect pack

A curated, level-normalized pack lives at [`assets/sfx/pack/`](assets/sfx/) with [`pack.json`](assets/sfx/) mapping each slot (`swipe-in`, `swipe-out`, `hook-impact`, `cash-register`, `ding`, `whoosh`) to one or more rotated variant files. Drop your own one-shots into `assets/sfx/`, run `uv run python scripts/build_sfx_pack.py`, and the rebuilt pack is normalized + ready to use. See [`docs/SFX.md`](docs/SFX.md) for the trigger logic.

## What this is NOT (yet)

- Multi-speaker / diarized — single talking-head only. Multi-speaker is on the v0.6 roadmap.
- A hosted service — local CLI tool. Bring your own GPU.
- Without an LLM — clip selection needs Claude API or a local Ollama-compatible model. Or hand-write `clips.json` and `--from-step 3`.

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the 11-phase pipeline, deep-dive.
- [docs/SETUP.md](docs/SETUP.md) — install per OS, CUDA torch matrix, model downloads.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — common errors and fixes.
- [docs/SFX.md](docs/SFX.md) — sound-effect pack format, triggers, level approval.
- [docs/REMOTION.md](docs/REMOTION.md) — captions layer + b-roll engine.
- [CONTRIBUTING.md](CONTRIBUTING.md) — PR checklist, where to file issues.
- [PROJECT_STATE.md](PROJECT_STATE.md) — current development state (read this first if you're picking the project back up after a break).

## License

[MIT](LICENSE). Use it however you want.
