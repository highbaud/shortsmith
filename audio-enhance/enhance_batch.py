"""Batch speech enhancement via ClearerVoice-Studio MossFormer2_SE_48K.

Reads stdin as JSON: [{"in": "path/to/input.wav", "out": "path/to/output.wav"}, ...]
Loads the model once, enhances each file, writes output.wav at the requested path.
Prints status JSON per line to stdout.

ClearVoice writes output to <out_path_dir>/<model_name>/<input_basename>.wav,
not directly to the requested output path. We post-process by moving the
generated file to the actual requested path.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path


def main() -> int:
    payload = sys.stdin.read()
    jobs = json.loads(payload)
    if not isinstance(jobs, list) or not jobs:
        print(json.dumps({"error": "expected JSON array of {in, out} jobs"}), flush=True)
        return 2

    from clearvoice import ClearVoice

    t0 = time.time()
    cv = ClearVoice(task="speech_enhancement", model_names=["MossFormer2_SE_48K"])
    print(json.dumps({"event": "model_loaded", "seconds": round(time.time() - t0, 1)}), flush=True)

    for i, job in enumerate(jobs):
        in_path = Path(job["in"]).resolve()
        out_path = Path(job["out"]).resolve()
        if not in_path.exists():
            print(json.dumps({"event": "skip", "in": str(in_path), "reason": "missing"}), flush=True)
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)

        # ClearVoice will write to out_dir/MossFormer2_SE_48K/<input_stem>.wav
        # Use a unique temp dir per job to avoid name collisions.
        tmp_dir = out_path.parent / f"_cv_tmp_{i}"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            t1 = time.time()
            cv(input_path=str(in_path), online_write=True, output_path=str(tmp_dir / out_path.name))
            generated = tmp_dir / out_path.name / "MossFormer2_SE_48K" / in_path.name
            if not generated.exists():
                # Fallback: locate any .wav in the tmp_dir
                hits = list(tmp_dir.rglob("*.wav"))
                if not hits:
                    print(json.dumps({"event": "fail", "in": str(in_path), "reason": "no_output"}), flush=True)
                    continue
                generated = hits[0]
            shutil.move(str(generated), str(out_path))
            shutil.rmtree(tmp_dir, ignore_errors=True)
            elapsed = round(time.time() - t1, 1)
            print(json.dumps({"event": "ok", "in": str(in_path), "out": str(out_path), "seconds": elapsed}), flush=True)
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(json.dumps({"event": "fail", "in": str(in_path), "error": repr(e)}), flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
