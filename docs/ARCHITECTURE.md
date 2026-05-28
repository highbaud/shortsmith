# Architecture

The shortsmith pipeline is 8 sequential steps. Each step reads from the previous
step's outputs in a per-source `work/<slug>/` directory, so any step can be
resumed independently via `--from-step N`.

```
your-video.mp4 (3hr podcast)
       │
       ├─ 1. transcribe     → work/<slug>/transcript.json
       ├─ 2. find_clips     → work/<slug>/clips.json                (Claude API)
       ├─ 3. cut_clips      → work/<slug>/raw/short-NN.mp4          (+ cut_manifests.json)
       ├─ 4. clean_clips    → work/<slug>/cleaned/short-NN.mp4
       ├─ 5. enhance_audio  → work/<slug>/enhanced/short-NN.mp4
       ├─ 6. retranscribe   → work/<slug>/enhanced/short-NN.words.json
       ├─ 7. reframe        → work/<slug>/vertical/short-NN.mp4     (1080×1920)
       └─ 8. scaffold       → hyperframes-student-kit/video-projects/auto-shorts/<slug>/short-NN-<hook>/
```

## Step 1 — Transcribe

`shortsmith/transcribe.py`. faster-whisper large-v3, CUDA, word-level timestamps.
If a sibling `transcript-<stem>.json` exists next to the source video, reuse it.
Output schema: `[{"text": str, "start": float_sec, "end": float_sec}, ...]`.

## Step 2 — Find clips

`shortsmith/find_clips.py`. Single Claude API call with the full transcript
collapsed into `[t=NNs]`-marked sentences. The system prompt at
[`prompts/find_viral_clips.md`](../prompts/find_viral_clips.md) enforces:

- **Hard topical-clarity gate** — every clip must have a one-sentence statable topic with a concrete anchor (number, name, story, framework). Mood pieces are rejected.
- **Hard evergreen gate** — no specific dates, prices, current admin, "this week".
- **Viral score 1–10**, rubric-scored across topical clarity, hook strength, payoff clarity, quotability/emotion.
- **Hook isolation** — Claude flags the strongest 5–10 seconds within each clip.
- **Reorder for hook-first delivery** — if the killer line lands at 0:18 inside a 0:30 clip, Claude emits `[[18, 28], [0, 18], [28, 30]]` so the physical concat leads with the hook.

Reject floor: `viral_score < SHORTSMITH_MIN_SCORE` (default 7).

## Step 3 — Cut + reorder

`shortsmith/cut_clips.py`. ffmpeg with safe-boundary snapping. Each cut point
gets snapped to the nearest sentence-end + ≥200ms silence within ±0.6s. Tiered
fallback: sentence-end → breath-silence → any 1.5s word gap → any 3s gap →
widest gap. Reordered segments get an 80ms `xfade` crossfade at every seam so
the joins read as cuts, not jumps.

## Step 4 — Clean (filler + silence)

`shortsmith/clean_clips.py`. Word-aware. Reads the per-clip transcript, computes
the set of word ranges to remove (configurable filler list + silence gaps
>550ms), and applies them via ffmpeg `select` / `aselect` filters. Cuts NEVER
land inside a word — the boundary check is enforced in code, not handed to a
black-box silence detector.

## Step 5 — Enhance audio

`shortsmith/enhance_audio.py`. Default engine: **ClearerVoice-Studio
MossFormer2_SE_48K** (SOTA on DNS Challenge, 48 kHz full-band). Runs in a
sibling uv project at `audio-enhance/` because ClearerVoice has tight torch +
numpy<2 pins that conflict with shortsmith's deps.

Communication is a single subprocess invocation with a JSON manifest on stdin
listing all `(input, output)` wav pairs for the source video. Model loads once,
processes all clips, exits. ~10s of compute per minute of audio on a 5090.

Fallback chain: clearvoice → voicefixer → resemble → deepfilter → original.

## Step 6 — Retranscribe

`shortsmith/transcribe.py` again. After steps 4 + 5 alter the audio timeline,
the original transcript's timings no longer match. Re-running Whisper on each
~60s enhanced clip takes ~5s on a 5090 and produces a clean word-level transcript
in the clip's local timeline.

## Step 7 — Reframe 9:16

`shortsmith/reframe.py`. **YuNet face detection** every Nth frame (default
every 3 at 30fps = 10/sec). Each detection is filtered through:

1. **Confidence threshold** (`yunet_score_threshold`, default 0.7).
2. **Resolution-aware absolute height floor** — reject anything < 8% of source
   height (catches logos/avatars on any resolution).
3. **Biggest-face-wins** — discard detections below 70% of the 90th-percentile
   face height in the clip. This rules out PIP cameras, chat thumbnails, and
   any graphic faces that snuck through the height floor.
4. **IQR outlier rejection** on the remaining detections' x/y centers.
5. **Spatial sanity clamp** — if the median lands within 10% of any edge, clamp
   toward the safe zone.

After filtering, the median x/y/height drives a single static crop window for
the whole clip (talking heads barely move horizontally, so a static crop avoids
the seasick "tracked zoom" look). Target framing: face center at 40% from top,
face occupies ~32% of the 1920px vertical — chest and shoulders visible,
headroom for social-platform overlays.

## Step 8 — Scaffold

`shortsmith/scaffold.py`. Generates a self-contained Hyperframes project per
clip. Each project has:

- `index.html` — single composition. Video element with `data-track-index=0`,
  audio element pulling from the same mp4. Inline GSAP timeline.
- `assets/clip-edit.mp4` — the 9:16 vertical clip (audio-enhanced, filler-free).
- `assets/words.json` — word-level transcript on local clip timeline.
- `compositions/captions.html` — karaoke captions (off by default).
- `compositions/ambient-bg.html` — vignette + grain overlay.
- `meta.json` — Hyperframes project metadata + a `_shortsmith` field with the
  viral score, hook text, snapped cut points, and rationale.
- `caption.txt` — the Instagram caption.

The opening 2.6s is a **slam hook** — full-screen accent type with the clip's
hook line, scale-in + blur-out animation. Then 1–3 **callouts** play at the
local timestamps Claude marked in the original clip selection (typically:
`bigstat` for hard numbers, `punch` for top-of-frame statements, `caption` for
lower-third labels, `hero` for climax headlines).

## Folder layout

```
work/<source-slug>/
├── transcript.json
├── transcript.formatted.txt          # human-readable [t=Ns]-marked
├── clips.json
├── cut_manifests.json
├── raw/short-NN.mp4                  # step 3 output
├── cleaned/short-NN.mp4              # step 4 output
├── enhanced/short-NN.mp4             # step 5 output
├── enhanced/short-NN.words.json      # step 6 output
└── vertical/short-NN.mp4             # step 7 output (1080×1920)
```

```
hyperframes-student-kit/video-projects/auto-shorts/<source-slug>/
├── short-01-<hook>.txt               # Instagram caption (parent copy)
└── short-01-<hook>/
    ├── index.html
    ├── meta.json
    ├── caption.txt
    ├── assets/
    │   ├── clip-edit.mp4
    │   └── words.json
    ├── compositions/
    │   ├── ambient-bg.html
    │   └── captions.html
    └── renders/                      # populated by `npx hyperframes render`
        └── final.mp4
```

## `meta.json._shortsmith` schema

Each scaffolded project's `meta.json` carries a non-spec `_shortsmith` field for
post-hoc review:

```json
{
  "_shortsmith": {
    "viral_score": 9,
    "hook_text": "70-year-olds own 17% of the stock market.",
    "reasoning": "ONE-SENTENCE TOPIC: Generational wealth-transfer concentration...",
    "source_video": "April 23, 2026 livestream",
    "reorder_advisory": "Linear cut, no reorder needed.",
    "snapped_cut_points": [[720.4, 800.1]]
  }
}
```

Useful when deciding which clips deserve more polish time in Studio.
