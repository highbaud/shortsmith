"""Batch forced-alignment worker for shortsmith (WhisperX).

Reads stdin JSON: [{"in": "audio_or_video_path", "out": "words.json"}, ...]

For each input:
  1. Transcribe with faster-whisper (via whisperx).
  2. Force-align word boundaries to ~20ms using wav2vec2.
  3. Write a flat list [{"text","start","end"}, ...] (shortsmith schema) to `out`.

Loads the ASR model and the (English) alignment model ONCE and reuses them
across every job. Emits one status JSON per line on stdout.

Env:
  WHISPERX_MODEL    (default "large-v3")
  WHISPERX_DEVICE   (default "cuda")
  WHISPERX_COMPUTE  (default "float16")
  WHISPERX_LANG     (default "en")
  WHISPERX_BATCH    (default "16")
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _log(**kw):
    print(json.dumps(kw), flush=True)


def main() -> int:
    payload = sys.stdin.read()
    jobs = json.loads(payload)
    if not isinstance(jobs, list) or not jobs:
        _log(event="error", reason="expected JSON array of {in, out} jobs")
        return 2

    import whisperx

    device = os.environ.get("WHISPERX_DEVICE", "cuda")
    compute = os.environ.get("WHISPERX_COMPUTE", "float16")
    model_name = os.environ.get("WHISPERX_MODEL", "large-v3")
    lang = os.environ.get("WHISPERX_LANG", "en")
    batch_size = int(os.environ.get("WHISPERX_BATCH", "16"))

    t0 = time.time()
    asr = whisperx.load_model(model_name, device, compute_type=compute, language=lang)
    align_model, align_meta = whisperx.load_align_model(language_code=lang, device=device)
    _log(event="model_loaded", seconds=round(time.time() - t0, 1),
         model=model_name, device=device)

    for job in jobs:
        in_path = Path(job["in"]).resolve()
        out_path = Path(job["out"]).resolve()
        if not in_path.exists():
            _log(event="skip", **{"in": str(in_path)}, reason="missing")
            continue
        try:
            t1 = time.time()
            audio = whisperx.load_audio(str(in_path))
            result = asr.transcribe(audio, batch_size=batch_size, language=lang)
            segments = result.get("segments", [])
            if not segments:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text("[]", encoding="utf-8")
                _log(event="ok", **{"in": str(in_path), "out": str(out_path)},
                     words=0, seconds=round(time.time() - t1, 1))
                continue

            aligned = whisperx.align(
                segments, align_model, align_meta, audio, device,
                return_char_alignments=False,
            )

            words: list[dict] = []
            for seg in aligned.get("segments", []):
                for w in seg.get("words", []):
                    # Aligned words always carry start/end; the rare unaligned
                    # token (e.g. pure punctuation) is skipped.
                    if "start" not in w or "end" not in w:
                        continue
                    text = str(w.get("word", "")).strip()
                    if not text:
                        continue
                    words.append({
                        "text": text,
                        "start": round(float(w["start"]), 3),
                        "end": round(float(w["end"]), 3),
                    })

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            _log(event="ok", **{"in": str(in_path), "out": str(out_path)},
                 words=len(words), seconds=round(time.time() - t1, 1))
        except Exception as e:  # noqa: BLE001
            _log(event="fail", **{"in": str(in_path)}, error=repr(e))

    return 0


if __name__ == "__main__":
    sys.exit(main())
