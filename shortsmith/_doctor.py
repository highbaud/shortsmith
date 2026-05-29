"""`shortsmith doctor` — health-check the local install.

Prints a green/red checklist:
- ffmpeg / ffprobe on PATH
- uv / npm available
- Anthropic API key set (if engine=anthropic)
- Sibling uv projects present and synced: audio-enhance, whisperx-align
- Hyperframes submodule initialised
- Remotion node_modules installed
- SFX pack populated (assets/sfx/pack/pack.json)
- YuNet face detection model present

Each row prints ✅ / ⚠️  / ❌ with a one-line hint on how to fix it. Designed to
be the first thing a user runs when a pipeline behaves unexpectedly.
"""
from __future__ import annotations

import json
import shutil
import sys

import click

from .config import (
    AUDIO_ENHANCE_PROJECT,
    KIT_ROOT,
    REPO_ROOT,
    TEMPLATE_REF,
    WHISPERX_ALIGN_PROJECT,
    Config,
)

# Anchor symbols. Plain text on Windows terminals that don't speak emoji cleanly.
OK = "[ok]   "
WARN = "[warn] "
FAIL = "[fail] "


def _row(status: str, label: str, hint: str = "") -> None:
    click.echo(f"  {status}{label}")
    if hint:
        click.echo(f"         {hint}")


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def doctor() -> int:
    """Print health-check rows. Returns 0 if no FAILs, 1 otherwise."""
    cfg = Config()
    fails = 0

    click.echo("")
    click.echo("shortsmith doctor")
    click.echo("=" * 60)

    # --- External binaries -------------------------------------------------
    click.echo("\nExternal tools")
    if _has("ffmpeg") and _has("ffprobe"):
        _row(OK, "ffmpeg / ffprobe on PATH")
    else:
        fails += 1
        _row(FAIL, "ffmpeg or ffprobe not found",
             "Install: macOS `brew install ffmpeg` | Ubuntu `apt install ffmpeg` "
             "| Windows `winget install Gyan.FFmpeg`")

    if _has("uv"):
        _row(OK, "uv available")
    else:
        fails += 1
        _row(FAIL, "uv not on PATH",
             "Install from https://docs.astral.sh/uv/getting-started/installation/")

    if _has("npx") and _has("npm"):
        _row(OK, "Node 18+ (npm + npx)")
    else:
        _row(WARN, "npm / npx not on PATH",
             "Optional: needed for Hyperframes render + Remotion captions/b-roll layer.")

    # --- Sibling uv projects -----------------------------------------------
    click.echo("\nSibling projects")
    if AUDIO_ENHANCE_PROJECT.exists():
        venv = AUDIO_ENHANCE_PROJECT / ".venv"
        if venv.exists():
            _row(OK, f"audio-enhance ({AUDIO_ENHANCE_PROJECT})",
                 ".venv ready (ClearerVoice MossFormer2_SE_48K)")
        else:
            _row(WARN, f"audio-enhance found but .venv missing ({AUDIO_ENHANCE_PROJECT})",
                 f"Run: cd '{AUDIO_ENHANCE_PROJECT}' && uv sync")
    else:
        if cfg.enhance_engine == "clearvoice":
            fails += 1
            _row(FAIL, f"audio-enhance project missing at {AUDIO_ENHANCE_PROJECT}",
                 "Either run setup.sh, set SHORTSMITH_AUDIO_ENHANCE, "
                 "or pass --no-enhance / --engine voicefixer.")
        else:
            _row(WARN, f"audio-enhance missing (not needed for engine={cfg.enhance_engine})")

    if WHISPERX_ALIGN_PROJECT.exists():
        venv = WHISPERX_ALIGN_PROJECT / ".venv"
        if venv.exists():
            _row(OK, f"whisperx-align ({WHISPERX_ALIGN_PROJECT})",
                 ".venv ready (~20ms forced alignment)")
        else:
            _row(WARN, "whisperx-align found but .venv missing",
                 f"Run: cd '{WHISPERX_ALIGN_PROJECT}' && uv sync")
    else:
        _row(WARN, f"whisperx-align missing at {WHISPERX_ALIGN_PROJECT}",
             "Step 6 will fall back to faster-whisper retranscribe "
             "(lower-quality word timings). Set SHORTSMITH_WHISPERX_ALIGN to override.")

    # --- Hyperframes kit ---------------------------------------------------
    click.echo("\nHyperframes kit")
    if KIT_ROOT.exists() and (KIT_ROOT / "package.json").exists():
        _row(OK, f"kit submodule initialised ({KIT_ROOT})")
        node_modules = KIT_ROOT / "node_modules"
        if node_modules.exists():
            _row(OK, "kit node_modules installed")
        else:
            _row(WARN, "kit node_modules missing",
                 f"Run: cd '{KIT_ROOT}' && npm install")
    else:
        fails += 1
        _row(FAIL, f"kit submodule not initialised at {KIT_ROOT}",
             "Run: git submodule update --init --recursive")
    if TEMPLATE_REF.exists():
        _row(OK, "may-shorts-19 template reference present")
    else:
        _row(WARN, f"template reference missing at {TEMPLATE_REF}",
             "Set SHORTSMITH_TEMPLATE_REF to a valid template project under the kit.")

    # --- Remotion ----------------------------------------------------------
    click.echo("\nRemotion (captions + b-roll layer)")
    remotion = REPO_ROOT / "remotion"
    if (remotion / "package.json").exists():
        _row(OK, "remotion/package.json present")
        if (remotion / "node_modules").exists():
            _row(OK, "remotion node_modules installed")
        else:
            _row(WARN, "remotion node_modules missing",
                 "Run: cd remotion && npm install   (~600 MB one-time)")
    else:
        _row(WARN, "remotion/ directory missing",
             "Phase 0 of finalize.py will be skipped.")

    # --- SFX pack ----------------------------------------------------------
    click.echo("\nSound effects")
    pack_json = REPO_ROOT / "assets" / "sfx" / "pack" / "pack.json"
    if pack_json.exists():
        try:
            data = json.loads(pack_json.read_text(encoding="utf-8"))
            filled = sum(1 for v in data.values() if v)
            total = len(data)
            _row(OK if filled == total else WARN,
                 f"SFX pack: {filled}/{total} slots populated",
                 f"({pack_json.relative_to(REPO_ROOT)})")
        except (OSError, json.JSONDecodeError) as e:
            _row(WARN, f"SFX pack.json unreadable: {e}",
                 "Rebuild with: uv run python scripts/build_sfx_pack.py")
    else:
        _row(WARN, "SFX pack not built",
             "Run: uv run python scripts/build_sfx_pack.py")

    # --- API key + LLM backend --------------------------------------------
    click.echo("\nClip selection backend")
    if cfg.clip_engine == "anthropic":
        if cfg.anthropic_api_key:
            _row(OK, "ANTHROPIC_API_KEY set (Claude clip selection)")
        else:
            fails += 1
            _row(FAIL, "ANTHROPIC_API_KEY missing (engine=anthropic)",
                 "Add to .env, or pick a different engine: "
                 "SHORTSMITH_CLIP_ENGINE=ollama")
    else:
        _row(OK, f"engine={cfg.clip_engine} (no Anthropic key required)")
        _row(WARN, f"local-LLM target: {cfg.local_llm_url} model={cfg.local_llm_model}",
             "Make sure your Ollama / LM Studio / vLLM server is running there.")

    # --- YuNet model -------------------------------------------------------
    click.echo("\nModels")
    yunet = cfg.yunet_model_path
    if yunet.exists():
        size = yunet.stat().st_size
        _row(OK, f"YuNet face detection model ({_bytes(size)})",
             str(yunet.relative_to(REPO_ROOT)))
    else:
        fails += 1
        _row(FAIL, f"YuNet model missing at {yunet}",
             "curl -L -o models/face_detection_yunet_2023mar.onnx "
             "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx")

    # --- Footer ------------------------------------------------------------
    click.echo("")
    click.echo("=" * 60)
    if fails == 0:
        click.echo(click.style("All required checks passed. Pipeline is ready.", fg="green"))
    else:
        click.echo(click.style(
            f"{fails} required check(s) failed. Fix the [fail] rows above before running the pipeline.",
            fg="red",
        ))
    click.echo("")
    return 0 if fails == 0 else 1


def main() -> int:
    return doctor()


if __name__ == "__main__":
    sys.exit(main())
