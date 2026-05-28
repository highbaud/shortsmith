# Shortsmith bootstrap for Windows (PowerShell).
# Idempotent - re-running is safe.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "[1/6] Initialising hyperframes-student-kit submodule..."
git submodule update --init --recursive

Write-Host "[2/6] Checking ffmpeg on PATH..."
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: ffmpeg not on PATH." -ForegroundColor Red
    Write-Host "  Install:  winget install Gyan.FFmpeg"
    Write-Host "  Or:       https://ffmpeg.org/download.html"
    exit 1
}

Write-Host "[3/6] Checking npx (needed for Hyperframes render)..."
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    Write-Host "WARN: npx not found. Install Node 18+ if you want to render scaffolded projects." -ForegroundColor Yellow
    Write-Host "      https://nodejs.org/"
}

Write-Host "[4/6] Checking uv..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: uv not on PATH. Install from https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor Red
    exit 1
}

Write-Host "[5/6] Syncing main shortsmith venv (this can take a minute on first run)..."
uv sync

Write-Host "[6/6] Syncing audio-enhance venv (ClearerVoice + torch)..."
if (Test-Path "audio-enhance") {
    Push-Location audio-enhance
    try { uv sync } finally { Pop-Location }
} else {
    Write-Host "WARN: audio-enhance/ not found. Skipping. (You can still run with --no-enhance.)" -ForegroundColor Yellow
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[+]  Created .env from .env.example."
}

Write-Host ""
Write-Host "=========================================================================="
Write-Host "Shortsmith bootstrap complete."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit .env and set ANTHROPIC_API_KEY (https://console.anthropic.com)."
Write-Host ""
Write-Host "  2. (Optional) For NVIDIA GPU acceleration, install CUDA torch matching"
Write-Host "     your card. Examples:"
Write-Host "       RTX 50 (Blackwell):  uv pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128"
Write-Host "       RTX 30/40 (Ada/Ampere): same as above with cu124"
Write-Host "       Older NVIDIA:        same with cu121"
Write-Host ""
Write-Host "  3. Smoke test (no API key required):"
Write-Host "       uv run python scripts/smoke_test.py"
Write-Host ""
Write-Host "  4. Real run:"
Write-Host "       uv run shortsmith run path/to/your/video.mp4"
Write-Host "=========================================================================="
