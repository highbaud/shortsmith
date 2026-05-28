"""Step 5: Speech enhancement.

Default engine: ClearerVoice-Studio MossFormer2_SE_48K (48 kHz full-band speech
enhancement, SOTA on the DNS Challenge). Runs in a sibling uv project located
at `AUDIO_ENHANCE_PROJECT` (default: `<repo>/audio-enhance`) so its torch/numpy
don't collide with shortsmith's.

For each cleaned clip we:
1. Extract audio to 48 kHz mono WAV.
2. Batch-call ClearerVoice over every clip in one subprocess (single model load).
3. Mux the enhanced WAV back over each cleaned video.

Fallback engines (voicefixer, resemble, deepfilter) are kept for any clip the
primary engine fails on.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from .config import AUDIO_ENHANCE_PROJECT, Config

log = logging.getLogger(__name__)


def enhance_all(
    clip_manifests: list[dict],
    work_dir: Path,
    cfg: Config,
) -> list[dict]:
    """Enhance audio for every cleaned clip. Updates manifests with enhanced_path."""
    enhanced_dir = work_dir / "enhanced"
    enhanced_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = enhanced_dir / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Plan extractions
    plan = []  # list of (rank, cleaned_video, src_wav, enh_wav, out_video)
    for m in clip_manifests:
        rank = m["rank"]
        cleaned = Path(m.get("cleaned_path") or m["raw_path"])
        out_video = enhanced_dir / f"short-{rank:02d}.mp4"
        src_wav = tmp_dir / f"short-{rank:02d}_src.wav"
        enh_wav = tmp_dir / f"short-{rank:02d}_enh.wav"
        plan.append((rank, cleaned, src_wav, enh_wav, out_video))

    # 1. Extract all source WAVs at 48 kHz mono
    for rank, cleaned, src_wav, _, _ in plan:
        log.debug("Extract audio: short-%02d", rank)
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(cleaned),
            "-vn", "-ac", "1", "-ar", "48000",
            str(src_wav),
        ], check=True, capture_output=True)

    # 2. Run primary engine in batch
    engine = cfg.enhance_engine.lower()
    enhanced_ranks: set[int] = set()
    if engine == "clearvoice":
        try:
            enhanced_ranks = _run_clearvoice_batch(
                [(p[2], p[3]) for p in plan], log_prefix="clearvoice"
            )
            log.info("ClearerVoice enhanced %d/%d clips", len(enhanced_ranks), len(plan))
        except Exception as e:
            log.warning("ClearerVoice batch failed: %s. Falling back per-clip.", e)

    # 3. Fall back per-clip for any not yet enhanced
    fallback_engines = [e for e in ("voicefixer", "resemble", "deepfilter") if e != engine]
    for rank, _cleaned, src_wav, enh_wav, _ in plan:
        if enh_wav.exists():
            continue
        for eng in [engine, *fallback_engines]:
            try:
                if eng == "voicefixer":
                    _run_voicefixer(src_wav, enh_wav)
                elif eng == "resemble":
                    _run_resemble(src_wav, enh_wav)
                elif eng == "deepfilter":
                    _run_deepfilter(src_wav, enh_wav)
                else:
                    continue
                log.info("Fallback %s enhanced short-%02d", eng, rank)
                break
            except Exception as e:
                log.warning("short-%02d fallback %s failed: %s", rank, eng, e)
        if not enh_wav.exists():
            log.warning("short-%02d: all engines failed; using original audio", rank)
            shutil.copy(src_wav, enh_wav)

    # 4. Mux enhanced audio back over each cleaned video
    for rank, cleaned, _src_wav, enh_wav, out_video in plan:
        log.debug("Mux audio: short-%02d", rank)
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(cleaned),
            "-i", str(enh_wav),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(out_video),
        ], check=True, capture_output=True)
        # Update manifest
        for m in clip_manifests:
            if m["rank"] == rank:
                m["enhanced_path"] = str(out_video)
                break

    # Cleanup tmp wavs (keep the dir for inspection if anything failed)
    try:
        for _rank, _, src_wav, enh_wav, _ in plan:
            src_wav.unlink(missing_ok=True)
            enh_wav.unlink(missing_ok=True)
        tmp_dir.rmdir()
    except OSError:
        pass

    return clip_manifests


def _run_clearvoice_batch(jobs: list[tuple[Path, Path]], log_prefix: str = "cv") -> set[int]:
    """Invoke audio-enhance/enhance_batch.py with all jobs in one subprocess.

    Returns the set of indices that succeeded (0-based, matching jobs order),
    though callers care more about whether out_wav exists afterwards.
    """
    if not AUDIO_ENHANCE_PROJECT.exists():
        raise FileNotFoundError(f"audio-enhance project not found at {AUDIO_ENHANCE_PROJECT}")
    enhance_batch_py = AUDIO_ENHANCE_PROJECT / "enhance_batch.py"
    if not enhance_batch_py.exists():
        raise FileNotFoundError(f"enhance_batch.py not found at {enhance_batch_py}")

    manifest = [{"in": str(src), "out": str(dst)} for src, dst in jobs]
    payload = json.dumps(manifest)

    proc = subprocess.Popen(
        [
            "uv", "run",
            "--project", str(AUDIO_ENHANCE_PROJECT),
            "python", str(enhance_batch_py),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(input=payload, timeout=3600)

    ok_indices: set[int] = set()
    by_path = {str(src): i for i, (src, _) in enumerate(jobs)}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("event") == "ok":
            idx = by_path.get(evt.get("in", ""))
            if idx is not None:
                ok_indices.add(idx)
            log.info("%s: short %s -> %s (%.1fs)", log_prefix,
                     Path(evt.get("in", "?")).stem, Path(evt.get("out", "?")).stem,
                     evt.get("seconds", 0))
        elif evt.get("event") == "fail":
            log.warning("%s: %s failed: %s", log_prefix, evt.get("in", "?"), evt.get("error") or evt.get("reason"))
        elif evt.get("event") == "model_loaded":
            log.info("%s: model loaded in %.1fs", log_prefix, evt.get("seconds", 0))

    if proc.returncode != 0:
        log.warning("clearvoice subprocess exit=%d stderr=%s", proc.returncode, stderr[-400:])
        if not ok_indices:
            raise RuntimeError(f"clearvoice subprocess failed: {stderr[-200:]}")

    return ok_indices


def _run_voicefixer(src_wav: Path, out_wav: Path) -> None:
    import torch
    from voicefixer import VoiceFixer

    vf = VoiceFixer()
    cuda = torch.cuda.is_available()
    vf.restore(input=str(src_wav), output=str(out_wav), cuda=cuda, mode=0)
    if not out_wav.exists():
        raise RuntimeError("voicefixer produced no output")


def _run_resemble(src_wav: Path, out_wav: Path) -> None:
    tmp_in = src_wav.parent / "_re_in"
    tmp_out = src_wav.parent / "_re_out"
    tmp_in.mkdir(exist_ok=True)
    tmp_out.mkdir(exist_ok=True)
    staged = tmp_in / src_wav.name
    shutil.copy(src_wav, staged)
    subprocess.run([
        "resemble-enhance", str(tmp_in), str(tmp_out),
    ], check=True, capture_output=True)
    produced = list(tmp_out.glob(f"{src_wav.stem}*.wav"))
    if not produced:
        raise RuntimeError("Resemble Enhance produced no output")
    shutil.copy(produced[0], out_wav)
    shutil.rmtree(tmp_in, ignore_errors=True)
    shutil.rmtree(tmp_out, ignore_errors=True)


def _run_deepfilter(src_wav: Path, out_wav: Path) -> None:
    tmp_out = src_wav.parent / "_df_out"
    tmp_out.mkdir(exist_ok=True)
    subprocess.run([
        "deepFilter", str(src_wav), "-o", str(tmp_out),
    ], check=True, capture_output=True)
    produced = list(tmp_out.glob(f"{src_wav.stem}*.wav"))
    if not produced:
        raise RuntimeError("DeepFilterNet produced no output")
    shutil.copy(produced[0], out_wav)
    shutil.rmtree(tmp_out, ignore_errors=True)
