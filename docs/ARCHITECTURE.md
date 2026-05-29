# Architecture

Eleven sequential phases organized into three groups. Each phase reads from the previous phase's outputs (in `work/<slug>/` for the per-source-video pipeline, or `<project>/renders/` for the per-short render passes), so any phase can be resumed independently via `--from-step N` or by re-running `finalize.py` (which is idempotent).

```
source.mp4 (3-hour podcast)
       │
       ├─ Phase A — clip selection (per source video, work/<slug>/) ───────
       │   1. transcribe       → transcript.json
       │   2. find_clips       → clips.json          (Claude / Ollama)
       │   3. cut              → raw/short-NN.mp4    (+ cut_manifests.json)
       │   4. clean            → cleaned/short-NN.mp4
       │
       ├─ Phase B — audio + word alignment + face crop ───────────────────
       │   5. enhance + loudnorm → enhanced/short-NN.mp4   (-14 LUFS)
       │   6. force-align        → enhanced/short-NN.words.json (~20ms)
       │   7. reframe            → vertical/short-NN.mp4 (1080×1920)
       │
       ├─ Phase C — scaffold + render + caption + b-roll + SFX (per clip)
       │   8. scaffold        → <kit>/auto-shorts/<slug>/short-NN-<hook>/index.html
       │   9. Hyperframes     → <project>/renders/final.mp4    (slam hook + callouts)
       │  10. Remotion        → <project>/renders/final_remotion.mp4 (captions + b-roll)
       │  11. SFX             → <project>/renders/final_sfx.mp4   (overlay one-shots)
       │
       └─ Consolidate ─────────────────────────────────────────────────────
           finalize.py copies every final_sfx.mp4 + caption.txt into
           <kit>/renders/_all/<source-slug>__<short-slug>.{mp4,txt}
```

`shortsmith run <video>` covers phases A + B + scaffold (steps 1-8). The render-and-overlay tail (steps 9-11 + consolidate) is driven by `finalize.py`.

---

## Phase A — clip selection

### Step 1: Transcribe

`shortsmith/transcribe.py`. faster-whisper large-v3, CUDA, word-level timestamps. If a sibling `transcript-<stem>.json` exists next to the source video, reuse it. Output schema:

```json
[{"text": str, "start": float, "end": float}, ...]
```

Errors get actionable hints (`SHORTSMITH_WHISPER_MODEL=medium` for OOM, `SHORTSMITH_WHISPER_DEVICE=cpu` for CUDA mismatch) instead of raw torch stacks.

### Step 2: Find clips

`shortsmith/find_clips/`. Dispatcher selects between two backends:

- **`anthropic`** (default) — single Claude Opus call with the full transcript collapsed into `[t=NNs]`-marked sentences. Best quality; costs $0.10–$2.00 per source video.
- **`ollama`** (experimental) — any OpenAI-compatible local endpoint (Ollama / LM Studio / vLLM). Free; expect lower picking quality. Retries on bad JSON with falling temperature.

Both produce the same schema. The system prompt at [`prompts/find_viral_clips.md`](../prompts/find_viral_clips.md) enforces:

- **Topical-clarity gate** — every clip must have a one-sentence statable topic with a concrete anchor (number, name, story, framework). Mood pieces are hard-rejected.
- **Evergreen gate** — no dates / prices / current admin / "this week".
- **Hook isolation** — Claude marks the strongest 5–10 seconds within each clip.
- **Reorder for hook-first delivery** — if the killer line lands at 0:18 inside a 0:30 clip, output `[[18, 28], [0, 18], [28, 30]]`.
- **Viral score 1–10**, reject floor `SHORTSMITH_MIN_SCORE` (default 7).

### Step 3: Cut + reorder

`shortsmith/cut_clips.py`. ffmpeg with **tiered, asymmetric boundary snap**. Each cut point gets snapped to the nearest acceptable boundary:

- **Tier 0**: sentence-end punctuation + ≥ 200 ms silence
- **Tier 1**: ≥ 350 ms breath silence
- **Tier 2**: any word gap up to 1.5 s
- **Tier 3**: any word gap up to 3 s
- **Last resort**: widest available gap

For **end-of-clip** snaps (`prefer_after=True`), the forward search window is **3× wider** than backward. Clips extend forward to a clean sentence end rather than truncate a thought. Reordered segments get an 80 ms `xfade` crossfade at every seam.

### Step 4: Clean (filler + stutter + silence)

`shortsmith/clean_clips.py`. Three passes:

1. **Fillers** — only pure stammers (`um`, `uh`, `mm`) + `you know`. `like` / `basically` / `literally` / `right?` were dropped from the auto-cut list (too often content words).
2. **Stutters** — collapse adjacent identical stems separated by < 350 ms (`I-I-I think` → `I think`). Deliberate emphasis with normal pacing (`no, no, no` with normal pauses) is preserved.
3. **Silences** — gaps > 800 ms get cut down to a `silence_margin=0.30 s` breath around each side.

Cuts NEVER land inside a word — enforced in code, not handed off to a silence detector.

---

## Phase B — audio + word alignment + face crop

### Step 5: Enhance audio + loudnorm

`shortsmith/enhance_audio.py` + `shortsmith/normalize.py`.

**Enhance** — default engine **ClearerVoice MossFormer2_SE_48K** (SOTA on DNS Challenge). Runs in a sibling uv venv at `audio-enhance/` because ClearerVoice has tight torch + numpy<2 pins. Single subprocess processes every clip in one model load. Fallback chain: clearvoice → voicefixer → resemble → deepfilter → original.

**Loudnorm** — two-pass ffmpeg `loudnorm` after enhancement. Pass 1 measures integrated loudness / true peak / LRA. Pass 2 applies a linear gain. Target **-14 LUFS** (TikTok / Instagram / YouTube short-form playback standard).

### Step 6: Force-align

`shortsmith/align.py`. **WhisperX wav2vec2** alignment in a sibling uv venv. Re-transcribes each enhanced clip, then aligns word boundaries to **~20 ms**. This is what makes karaoke captions feel tight and seam-cuts land cleanly.

Falls back to in-process faster-whisper re-transcribe if WhisperX is unavailable (no sibling venv, or the project failed to spawn).

### Step 7: Reframe 9:16

`shortsmith/reframe.py`. **YuNet face detection** every 3rd frame (10 detections/sec at 30 fps). Each detection passes through five filters:

1. **Confidence ≥ 0.7** (drops weak misfires).
2. **Resolution-aware absolute height floor** (8% of source height — catches logos / avatars on 4K).
3. **Biggest-face-wins** (drop detections below 70% of the 90th-percentile face height in this clip — rejects PIP cameras and chat thumbnails on 4K source).
4. **IQR outlier rejection** on x/y centers.
5. **Spatial sanity clamp** (if median lands within 10% of any edge, clamp toward safe zone).

After filtering, the median x/y/height drives a single static crop window for the whole clip (talking heads barely move horizontally; static crop avoids "tracked zoom" sickness). Target framing: face center at 40% from top, occupies ~32% of vertical.

---

## Phase C — scaffold + render + caption + b-roll + SFX

### Step 8: Scaffold

`shortsmith/scaffold.py`. Generates a self-contained Hyperframes project per clip. Each project has:

- `index.html` — single composition. Video element + audio sibling + inline GSAP timeline.
- `assets/clip-edit.mp4` — the 9:16 vertical clip (audio-enhanced, filler-free, normalized).
- `assets/words.json` — word-aligned transcript on local clip timeline.
- `compositions/ambient-bg.html` — vignette + grain overlay (copied from `may-shorts-19` reference).
- `meta.json` — `_shortsmith` field with viral score, hook text, reasoning, snapped cut points.
- `caption.txt` — paste-ready Instagram caption.

The opening 2.6 s is a **slam hook** — full-screen accent type with scale-in + blur-out. Then 1–3 **callouts** play at the local timestamps Claude marked. Style preset (`SHORTSMITH_STYLE`) drives fonts, colors, and overlay visibility via a `style.json`.

### Step 9: Hyperframes base render

`npx hyperframes render <project>` produces the base `final.mp4`. Driven by the GSAP timeline in `index.html`. Slam hook plays at t=0, callouts hit on their `local_start`, ambient bg overlay throughout.

### Step 10: Remotion captions + b-roll

`scripts/apply_remotion.py` orchestrates two sub-steps:

**B-roll selection** — `scripts/gen_broll.py` reads the clip's words.json, identifies named brands (logo from Simple Icons / vectorlogo.zone) and persons (CC photos from Wikimedia Commons / Openverse / Wikipedia, shuffled across sources), times them to the spoken word, and writes `broll.auto.json`.

**Render** — `scripts/render_remotion.py` invokes the Remotion project at `remotion/` (React + Remotion 4.0). Loads the Hyperframes `final.mp4` as a base layer, overlays word-by-word captions tied to `assets/words.json` timings, and inserts b-roll picks at their `broll.auto.json` timestamps. Output: `<project>/renders/final_remotion.mp4`.

### Step 11: SFX overlay

`shortsmith/sfx.py` + `scripts/add_sfx.py`. Mixes a curated SFX pack onto the speech track. Triggers:

**Structural** (deterministic, tied to on-screen motion):
- `hook-impact` at t=0 (opening slam)
- `swipe-in` at each callout `local_start`
- `swipe-out` at each callout end (optional, off by default)

**Semantic** (sparing by default, tied to spoken words):
- `cash-register` on the **first** money word in the clip (`money` / `cash` / `dollar` / `rich` / `wealth` ...)
- `ding` on each `bigstat` callout whose text contains a number or `$`

Levels: SFX peaks at -9 dBFS, sit ~10–16 dB under speech (per-slot trim × `sfx_gain=0.7`), output limiter at -0.3 dBFS prevents clipping.

Non-destructive: writes `final_sfx.mp4` next to the input. Re-runnable.

### Consolidate

`scripts/finalize.py` is the authoritative orchestrator. Three phases:

- **Phase 0 (Remotion)**: for every short with a Hyperframes base render, regenerate `broll.auto.json` and produce `final_remotion.mp4`. Skips up-to-date and ungrounded shorts.
- **Phase 1 (SFX)**: for every short with `clips.json` + `cut_manifests.json`, locate the SFX base (`final_remotion.mp4` if present, else newest `final.mp4`), mix SFX → `final_sfx.mp4`.
- **Phase 2 (Consolidate)**: copy every `final_sfx.mp4` and matching `caption.txt` into `<kit>/renders/_all/<source-slug>__<short-slug>.{mp4,txt}`.

Idempotent. Safe to re-run on partial state.

---

## Folder layout

```
work/<source-slug>/
├── transcript.json                   # step 1
├── transcript.formatted.txt          # human-readable [t=Ns]-marked
├── clips.json                        # step 2
├── cut_manifests.json                # step 3
├── .progress.json                    # checkpoint markers (steps + rendered slugs)
├── raw/short-NN.mp4                  # step 3 output
├── cleaned/short-NN.mp4              # step 4 output
├── enhanced/short-NN.mp4             # step 5 output (-14 LUFS)
├── enhanced/short-NN.words.json      # step 6 output (~20ms)
└── vertical/short-NN.mp4             # step 7 output (1080×1920)
```

```
hyperframes-student-kit/video-projects/auto-shorts/<source-slug>/
└── short-NN-<hook>/
    ├── index.html                    # scaffold output (step 8)
    ├── meta.json
    ├── caption.txt
    ├── assets/clip-edit.mp4
    ├── assets/words.json
    ├── assets/broll/*.{svg,jpg,png}  # b-roll downloads (step 10)
    ├── broll.auto.json               # b-roll timing (step 10)
    ├── compositions/ambient-bg.html
    └── renders/
        ├── final.mp4                 # step 9 Hyperframes
        ├── final_remotion.mp4        # step 10 Remotion
        └── final_sfx.mp4             # step 11 SFX

hyperframes-student-kit/renders/_all/
├── <source>__short-01-<hook>.mp4     # final consolidated outputs
├── <source>__short-01-<hook>.txt
├── <source>__short-02-<hook>.mp4
└── <source>__short-02-<hook>.txt
```

## Crash recovery

`shortsmith/checkpoint.py` writes per-step markers into `work/<slug>/.progress.json`. A pipeline crash during step 6 of a 12-clip video resumes at step 6 instead of re-running steps 3–5. `finalize.py` skips shorts that already have an up-to-date `final_sfx.mp4`. `reprocess_all.py` uses a per-work-dir `.reprocessed_v2` marker so a re-run skips fully-finished sources.
