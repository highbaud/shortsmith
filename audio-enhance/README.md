# audio-enhance

Sibling uv project that runs [ClearerVoice-Studio](https://github.com/modelscope/ClearerVoice-Studio)
MossFormer2_SE_48K speech enhancement for shortsmith's step 5.

Lives in its own venv because ClearerVoice pins `numpy<2` and a specific
torch range that conflict with shortsmith's deps.

## Install

```bash
uv sync
```

First run downloads the ~600 MB model checkpoint to `audio-enhance/checkpoints/`.

## Usage

`shortsmith/shortsmith/enhance_audio.py` invokes this project via subprocess
with a JSON manifest on stdin:

```bash
echo '[{"in": "/abs/path/input.wav", "out": "/abs/path/output.wav"}]' | \
  uv run --project /path/to/audio-enhance python enhance_batch.py
```

Outputs status JSON per line on stdout:

```json
{"event": "model_loaded", "seconds": 18.6}
{"event": "ok", "in": "/abs/path/input.wav", "out": "/abs/path/output.wav", "seconds": 9.2}
```

This subprocess design lets shortsmith call ClearerVoice without polluting the
main shortsmith venv with the conflicting numpy/torch pins. The model loads
once per shortsmith run (one subprocess invocation per source video, with the
whole clip batch in the manifest).
