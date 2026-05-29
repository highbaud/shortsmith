# whisperx-align

Sibling uv project that runs [WhisperX](https://github.com/m-bain/whisperx)
forced alignment for shortsmith's step 6.

Lives in its own venv because WhisperX pins torch 2.8.0 + ctranslate2 +
pyannote which conflict with shortsmith's main faster-whisper / torch stack.

## Install

```bash
uv sync
```

First run downloads the wav2vec2 alignment model (~360 MB) to your
HuggingFace cache. Subsequent runs are quick.

GPU build is the default (`pytorch-cu128`) — change the index in
`pyproject.toml` if you need cu124 / cu121 / cpu wheels.

Python 3.10 or 3.11 only (whisperx pins exclude 3.12+).

## Usage

`shortsmith/shortsmith/align.py` invokes this project via subprocess with a
JSON manifest on stdin:

```bash
echo '[{"in": "/abs/path/clip.mp4", "out": "/abs/path/clip.words.json"}]' | \
  uv run --project /path/to/whisperx-align python align_batch.py
```

Outputs status JSON per line on stdout:

```json
{"event": "model_loaded", "seconds": 18.3}
{"event": "ok", "in": "/abs/path/clip.mp4", "out": "/abs/path/clip.words.json", "words": 87, "seconds": 4.9}
```

The model loads once per shortsmith pipeline run (one subprocess invocation
per source video, with the whole clip batch in the manifest). Aligned word
boundaries land at ~20 ms accuracy.
