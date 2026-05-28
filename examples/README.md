# Smoke-test sample clip

The smoke test at `scripts/smoke_test.py` looks for `examples/sample_clip.mp4`.

It should be a **short (~10 second), 1080p (or higher) talking-head clip** with
clear speech and a visible face. Any topic works — the smoke test doesn't care
what's said, only that:

- Whisper can transcribe at least a few words from it.
- YuNet can detect a face in it.
- The pipeline can produce a valid 1080×1920 output with audio.

## Generating one from your own footage

```bash
# Take 10 seconds starting at the 1-minute mark of any 1080p talking-head video.
ffmpeg -ss 60 -t 10 -i path/to/your-video.mp4 \
    -c:v libx264 -preset slow -crf 22 -an \
    -c:a aac -b:a 128k \
    examples/sample_clip.mp4
```

Keep the file under ~5 MB so it lives comfortably in git. If you don't want to
commit a real clip, see the .gitignore — `examples/sample_clip.mp4` is
explicitly allow-listed for committing, but you can leave it out and the smoke
test will print a helpful error.

## Why not bundle one in the repo by default?

Sample clips are creator-specific. The repo ships without one so contributors
can drop in whatever talking-head footage they're allowed to redistribute under
their own license.
