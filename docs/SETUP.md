# Setup

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| **uv** | Python venv + dep manager | https://docs.astral.sh/uv/getting-started/installation/ |
| **ffmpeg** | Cutting + remuxing | macOS: `brew install ffmpeg` · Ubuntu: `sudo apt install ffmpeg` · Windows: `winget install Gyan.FFmpeg` |
| **Node 18+** | Hyperframes render | https://nodejs.org/ (or `nvm`/`volta`) |
| **git** | Cloning + submodule | Pre-installed on macOS/Linux; `winget install Git.Git` on Windows |
| **NVIDIA GPU** (recommended) | Whisper + ClearerVoice both prefer CUDA | — |

## First-time install

```bash
git clone --recurse-submodules https://github.com/highbaud/shortsmith
cd shortsmith
./setup.sh        # macOS/Linux
# or
.\setup.ps1       # Windows PowerShell
```

The setup script:
1. Initialises the `hyperframes-student-kit` submodule.
2. Verifies ffmpeg + uv + npx.
3. Runs `uv sync` in the repo root (main shortsmith venv).
4. Runs `uv sync` in `audio-enhance/` (separate venv for ClearerVoice).
5. Copies `.env.example` to `.env` if it doesn't exist.

After that, edit `.env` and add your `ANTHROPIC_API_KEY`.

## CUDA torch (NVIDIA GPU)

The base `uv sync` installs CPU-only torch. To accelerate Whisper + ClearerVoice
on your GPU, install the matching CUDA wheel:

| GPU family | Index URL |
|---|---|
| **RTX 50 (Blackwell, sm_120)** | `https://download.pytorch.org/whl/cu128` |
| **RTX 40 (Ada), RTX 30 (Ampere)** | `https://download.pytorch.org/whl/cu124` |
| **RTX 20 (Turing), RTX 10 (Pascal)** | `https://download.pytorch.org/whl/cu121` |
| **No NVIDIA / CPU only** | (skip, default works) |

```bash
uv pip install --upgrade torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu128
```

Do this for both the main venv **and** inside `audio-enhance/`:

```bash
cd audio-enhance
uv pip install --upgrade torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu128
cd ..
```

To run on CPU only, set in `.env`:

```
SHORTSMITH_WHISPER_DEVICE=cpu
SHORTSMITH_WHISPER_COMPUTE=int8
```

CPU pipeline works but Whisper + reframe are ~5–10× slower.

## First-run model downloads

These happen transparently the first time you invoke a feature. Sizes:

| Model | Size | Where it lands |
|---|---|---|
| faster-whisper large-v3 | ~2.9 GB | `~/.cache/huggingface/hub/` |
| ClearerVoice MossFormer2_SE_48K | ~600 MB | `audio-enhance/checkpoints/` |
| YuNet face detection | 230 KB | **bundled** in `models/` |
| voicefixer (fallback engine) | ~625 MB | `~/.cache/voicefixer/` (only if used) |

Allow ~5 GB of disk for the cached weights.

## Submodule recovery

If you cloned without `--recurse-submodules` (the `hyperframes-student-kit/`
directory is empty):

```bash
git submodule update --init --recursive
```

## Hyperframes Node deps

The submodule needs its own `npm install` before `npx hyperframes preview`
works:

```bash
cd hyperframes-student-kit
npm install
cd ..
```

## Per-machine `.env` overrides

The default paths assume the layout shipped by the repo (everything in-tree as
sibling directories). If you want shortsmith to point at sibling projects
elsewhere on your machine — e.g., a kit checked out at `/home/me/work/kit/` —
edit `.env`:

```
SHORTSMITH_KIT_ROOT=/home/me/work/kit
SHORTSMITH_AUDIO_ENHANCE=/home/me/work/audio-enhance
SHORTSMITH_VIDEO_DIR=/home/me/Videos/podcast-archive
```

Env vars always win over `.env`.

## Verifying the install

```bash
uv run python scripts/smoke_test.py
```

The smoke test exercises steps 1, 3–8 on the bundled `examples/sample_clip.mp4`,
no API key required. Passes iff `work/<slug>/vertical/short-01.mp4` is
1080×1920 with audio.

For a full pipeline test (uses your Anthropic credits):

```bash
uv run shortsmith run path/to/your-video.mp4 --max-clips 1
```
