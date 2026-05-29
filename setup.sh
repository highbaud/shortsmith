#!/usr/bin/env bash
# Shortsmith bootstrap for macOS / Linux.
# Idempotent — re-running is safe.
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/6] Initialising hyperframes-student-kit submodule..."
git submodule update --init --recursive

echo "[2/6] Checking ffmpeg on PATH..."
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ERROR: ffmpeg not on PATH."
    echo "  macOS:    brew install ffmpeg"
    echo "  Ubuntu:   sudo apt install ffmpeg"
    echo "  Other:    https://ffmpeg.org/download.html"
    exit 1
fi

echo "[3/6] Checking npx (needed for Hyperframes render)..."
if ! command -v npx >/dev/null 2>&1; then
    echo "WARN: npx not found. Install Node 18+ if you want to render scaffolded projects."
    echo "      https://nodejs.org/"
fi

echo "[4/6] Checking uv..."
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not on PATH. Install from https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo "[5/6] Syncing main shortsmith venv (this can take a minute on first run)..."
uv sync

echo "[6/6] Syncing audio-enhance venv (ClearerVoice + torch)..."
if [ -d "audio-enhance" ]; then
    ( cd audio-enhance && uv sync )
else
    echo "WARN: audio-enhance/ not found. Skipping. (You can still run with --no-enhance.)"
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "[+]  Created .env from .env.example."
fi

if [ -d "remotion" ] && [ -f "remotion/package.json" ]; then
    if command -v npm >/dev/null 2>&1; then
        echo "[*]  Installing Remotion node deps (one-time, ~600 MB)..."
        ( cd remotion && npm install --silent )
    else
        echo "WARN: npm not found. Skipping Remotion install. (Captions + b-roll layer disabled.)"
    fi
fi

cat <<'EOF'

==========================================================================
Shortsmith bootstrap complete.

Next steps:
  1. Edit .env and set ANTHROPIC_API_KEY (https://console.anthropic.com).

  2. (Optional) For NVIDIA GPU acceleration, install CUDA torch matching
     your card. Examples:
       RTX 50 (Blackwell):  uv pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
       RTX 30/40 (Ada/Ampere): same as above with cu124
       Older NVIDIA:        same with cu121

  3. Smoke test (no API key required):
       uv run python scripts/smoke_test.py

  4. Real run:
       uv run shortsmith run path/to/your/video.mp4
==========================================================================
EOF
