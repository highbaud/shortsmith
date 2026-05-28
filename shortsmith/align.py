"""Step 6: word-level alignment of each edited clip.

Default engine "whisperx": re-transcribes + force-aligns each enhanced clip to
~20ms word boundaries (wav2vec2) in the sibling uv project WHISPERX_ALIGN_PROJECT.
This is what makes karaoke captions tight and reframe seams land on word gaps.

Falls back to the in-process faster-whisper re-transcribe (transcribe.py) for any
clip whisperx can't handle, or if the whisperx project/GPU is unavailable.

Why a sibling venv: whisperx pins torch 2.8 + ctranslate2 + pyannote, which we
don't want colliding with shortsmith's own faster-whisper / torch stack.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from . import transcribe
from .config import WHISPERX_ALIGN_PROJECT, Config

log = logging.getLogger(__name__)


def align_all(manifests: list[dict], cfg: Config) -> list[dict]:
    """Produce a `.words.json` next to each clip; set m['words_path']."""
    # Build the job list (clip mp4 -> sibling words.json).
    jobs: list[tuple[dict, Path, Path]] = []
    for m in manifests:
        clip_path = Path(m.get("enhanced_path") or m["cleaned_path"])
        words_out = clip_path.with_suffix(".words.json")
        jobs.append((m, clip_path, words_out))

    engine = (getattr(cfg, "align_engine", "whisperx") or "whisperx").lower()
    done_whisperx: set[str] = set()

    if engine == "whisperx":
        try:
            done_whisperx = _run_whisperx_batch(
                [(c, o) for _, c, o in jobs], cfg
            )
            log.info("WhisperX aligned %d/%d clips", len(done_whisperx), len(jobs))
        except Exception as e:  # noqa: BLE001
            log.warning(
                "WhisperX batch failed (%s). Falling back to faster-whisper "
                "re-transcribe for all clips.", e,
            )

    # Fallback per clip for anything whisperx didn't produce.
    for m, clip_path, words_out in jobs:
        # Fresh whisperx output from THIS run.
        if str(clip_path) in done_whisperx and words_out.exists():
            m["words_path"] = str(words_out)
            continue
        # Crash-recovery reuse: an existing alignment is only valid if it's
        # NEWER than the clip mp4. Step 5 rewrites the clip on every
        # re-process, so a stale words.json (older than the clip) must be
        # regenerated — otherwise captions drift against re-cut audio.
        if (words_out.exists() and words_out.stat().st_size > 2
                and clip_path.exists()
                and words_out.stat().st_mtime >= clip_path.stat().st_mtime):
            m["words_path"] = str(words_out)
            continue
        try:
            transcribe.transcribe(clip_path, words_out, cfg, reuse_existing=False)
            m["words_path"] = str(words_out)
        except Exception as e:  # noqa: BLE001
            log.error(
                "Alignment failed for %s via both whisperx and faster-whisper: %s. "
                "Captions for this clip may be loosely timed.", clip_path.name, e,
            )
            # Last resort: empty word list so downstream steps don't crash.
            words_out.write_text("[]", encoding="utf-8")
            m["words_path"] = str(words_out)

    return manifests


def _run_whisperx_batch(jobs: list[tuple[Path, Path]], cfg: Config) -> set[str]:
    """Invoke whisperx-align/align_batch.py over all clips in one subprocess.

    Returns the set of input-clip path strings that aligned successfully.
    """
    if not WHISPERX_ALIGN_PROJECT.exists():
        raise FileNotFoundError(
            f"whisperx-align project not found at {WHISPERX_ALIGN_PROJECT}. "
            "Run setup, set SHORTSMITH_WHISPERX_ALIGN, or set SHORTSMITH_ALIGN=faster-whisper."
        )
    align_batch_py = WHISPERX_ALIGN_PROJECT / "align_batch.py"
    if not align_batch_py.exists():
        raise FileNotFoundError(f"align_batch.py not found at {align_batch_py}")

    manifest = [{"in": str(src), "out": str(dst)} for src, dst in jobs]
    proc = subprocess.Popen(
        ["uv", "run", "--project", str(WHISPERX_ALIGN_PROJECT), "python", str(align_batch_py)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    stdout, stderr = proc.communicate(input=json.dumps(manifest), timeout=7200)

    ok: set[str] = set()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = evt.get("event")
        if ev == "ok":
            ok.add(evt.get("in", ""))
            log.info("whisperx: %s (%d words, %.1fs)",
                     Path(evt.get("in", "?")).parent.parent.name, evt.get("words", 0),
                     evt.get("seconds", 0))
        elif ev == "fail":
            log.warning("whisperx fail: %s: %s", evt.get("in", "?"), evt.get("error"))
        elif ev == "model_loaded":
            log.info("whisperx: model loaded in %.1fs", evt.get("seconds", 0))

    if proc.returncode != 0 and not ok:
        raise RuntimeError(f"whisperx subprocess exit={proc.returncode}: {stderr[-300:]}")
    return ok
