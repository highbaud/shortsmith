# Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `ANTHROPIC_API_KEY env var is not set` | Set in `.env` or shell env. Skip via `--from-step 3` with a hand-crafted `clips.json`. |
| `Hyperframes kit not found at ...` | Submodule wasn't initialised. Run `git submodule update --init --recursive` or `./setup.sh`. |
| `Audio-enhance project not found at ...` | Run `cd audio-enhance && uv sync`, or pass `--no-enhance` / `--engine voicefixer`. |
| `YuNet model not found` | Re-download: `curl -L -o models/face_detection_yunet_2023mar.onnx https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx` |
| `npx hyperframes: command not found` | Run `npm install` inside `hyperframes-student-kit/`. Need Node 18+. |
| `RuntimeError: CUDA out of memory` (Whisper) | Use `SHORTSMITH_WHISPER_MODEL=medium` or move to CPU: `SHORTSMITH_WHISPER_DEVICE=cpu`, `SHORTSMITH_WHISPER_COMPUTE=int8`. |
| `Could not load library libcudnn*.so` | CUDA torch / cudnn mismatch. Reinstall torch with the right `--index-url` for your GPU (see `docs/SETUP.md` CUDA matrix). |
| ClearerVoice import fails with `No module named 'clearvoice'` | Setup didn't run `uv sync` inside `audio-enhance/`. Re-run `./setup.sh`. |
| ClearerVoice errors on torch version | `audio-enhance/` needs Python 3.10 or 3.11 (not 3.12). uv handles this automatically if uv picks up a compatible Python; otherwise install one. |
| Cuts feel jarring / cut mid-word | Open `work/<slug>/cut_manifests.json` and inspect what got snapped where. Boundary snapping has tiered fallbacks; the last resort is "widest gap" which may not be a clean sentence end. Re-pick that clip's boundaries manually in `clips.json` and re-run `--from-step 3`. |
| Captions out of sync | Re-transcribe (step 6) is what produces clean local-timeline timings. If you bypass it via `--from-step 8`, the original transcript's timings won't line up — re-run with `--from-step 6`. |
| Face framing wrong (off-center, cropped, picks up a chat avatar) | Reframe v2 uses biggest-face-wins to dodge avatars, but a moving speaker on 4K source can still confuse it. Look at the reframe log line for that clip — if `filt detections` is much smaller than `raw detections`, the filters were aggressive. Try lowering `yunet_score_threshold` in `shortsmith/config.py`, or pre-trim the source so the chat overlay isn't in frame. |
| Pipeline crashes mid-run | The `work/<slug>/` artifacts from completed steps persist. Resume with `--from-step N`. |
| Whisper transcript drifts (timestamps off by seconds) | Some video containers have weird timestamp metadata. `ffmpeg -i input.mp4 -c copy fixed.mp4` to repackage cleanly, then re-run. |
| `Permission denied` on `models/face_detection_yunet_2023mar.onnx` | Windows path quoting issue if you have spaces somewhere in your repo path. Move the repo to a path without spaces (e.g., `C:\dev\shortsmith\`). |
| `RuntimeError: NumPy 1.x cannot be run with NumPy 2.x` (audio-enhance) | The numpy<2 pin is intentional for ClearerVoice. Re-run `cd audio-enhance && uv sync`. |
| Rendered final.mp4 is silent | The `--no-enhance` path doesn't re-mux audio. Make sure step 5 ran (or re-run with `--from-step 5`). |
| `Hyperframes lint` errors after scaffold | Open the scaffolded `index.html` — most lint errors are template version mismatches. File an issue with the exact error message. |

## When to file an issue vs. fix it yourself

| Domain | Where |
|---|---|
| Shortsmith pipeline (cuts, framing, scaffold, rubric) | This repo. |
| ClearerVoice install / output quality | [modelscope/ClearerVoice-Studio](https://github.com/modelscope/ClearerVoice-Studio) |
| Hyperframes render / preview / lint | the hyperframes-student-kit repo (the submodule) |
| Whisper transcription | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) — but try `SHORTSMITH_WHISPER_MODEL=large-v3` first |
| YuNet face detection accuracy | [opencv/opencv_zoo](https://github.com/opencv/opencv_zoo) — though shortsmith's filter chain is usually the real issue |
