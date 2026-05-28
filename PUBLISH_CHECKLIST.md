# Pre-publish checklist

What's left to do before `git push`. Everything else (path refactor, docs,
LICENSE, setup scripts, CI, smoke test) is already done.

## 1. Wait for the running pipeline batch to finish

`scripts/batch_pipeline.py` (was `batch_pipeline_new.py`) is still chewing
through ~40 source videos. Check `work/batch_pipeline_new.log`. When it shows
`DONE` at the bottom, move on.

## 2. Move `audio-enhance/` in-tree

Currently `audio-enhance/` lives at `F:/Claude Code/audio-enhance/` and the
running batch reaches it via the `SHORTSMITH_AUDIO_ENHANCE` entry in `.env`.
Once the batch is done:

```powershell
# From shortsmith repo root (PowerShell):
Copy-Item -Recurse "F:/Claude Code/audio-enhance" ".\audio-enhance"
Remove-Item -Recurse -Force .\audio-enhance\.venv -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .\audio-enhance\checkpoints -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .\audio-enhance\test -ErrorAction SilentlyContinue
```

Or bash equivalent:

```bash
cp -r "F:/Claude Code/audio-enhance" ./audio-enhance
rm -rf audio-enhance/.venv audio-enhance/checkpoints audio-enhance/test
```

## 3. Remove transition `.env` overrides

The `.env` currently pins paths to the absolute Windows locations so the
running batch doesn't break. Once everything is in-tree, those overrides
should go so the defaults (sibling-relative) take over:

```powershell
# Just delete .env and let setup.sh / setup.ps1 regenerate it from .env.example
Remove-Item .env
```

Or selectively:

```bash
# Linux/macOS
sed -i '/SHORTSMITH_KIT_ROOT=/d;/SHORTSMITH_AUDIO_ENHANCE=/d;/SHORTSMITH_VIDEO_DIR=/d' .env
```

## 4. Add the hyperframes-student-kit submodule

```bash
git submodule add https://github.com/nateherkai/hyperframes-student-kit hyperframes-student-kit
```

This will replace the empty `hyperframes-student-kit/` directory (if you
delete it first) with the submodule reference. If git complains:

```bash
rm -rf hyperframes-student-kit                       # delete any local copy
git submodule add https://github.com/nateherkai/hyperframes-student-kit hyperframes-student-kit
git submodule update --init --recursive
```

## 5. Run the smoke test on the fresh layout

```bash
uv run python scripts/smoke_test.py
```

Should print `[smoke] PASS`. If it fails on the kit/template ref, the
submodule didn't initialize — re-run `git submodule update --init --recursive`.

## 6. Initial commit + push

```bash
git init
git branch -m main
git add .
git commit -m "v0.1.0 — initial public release"
git tag v0.1.0
git remote add origin https://github.com/highbaud/shortsmith.git
git push -u origin main
git push --tags
```

## 7. On GitHub itself

- Repo description: **"Long-form video in. Batch of viral Hyperframes-ready 9:16 shorts out."**
- Topics: `video`, `shorts`, `whisper`, `viral`, `ffmpeg`, `hyperframes`, `cli`, `python`, `xrp`, `crypto`.
- Enable Issues.
- Set the default branch to `main`.
- Pin the README's quickstart section in the About sidebar.

## 8. Optional next-ups

- Record a 30-second demo gif at `examples/demo.gif`, link from README.
- Open a PR template under `.github/PULL_REQUEST_TEMPLATE.md` mirroring the CONTRIBUTING.md checklist.
- Submit to relevant directories: Awesome AI Video, Awesome Whisper, etc.
- Tweet/post the launch with a sample short rendered by the tool.
