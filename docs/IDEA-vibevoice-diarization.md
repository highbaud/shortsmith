# Idea: VibeVoice (microsoft/VibeVoice) for real speaker diarization

_Status: unevaluated idea, nothing built. Noted 2026-07-21._

Source: https://github.com/microsoft/VibeVoice (MIT license)

## Why this is interesting for shortsmith

Today shortsmith has **no voice-based speaker ID**. Two paths exist and both
work around that gap rather than solving it:

- Static crop (`shortsmith/reframe.py`, single-speaker talking head): one crop
  for the whole clip, biggest-face-wins.
- Cut-aware reframe (`--cut-aware` / `"multicam": true`): follows the editor's
  **camera cuts**, not voices. Explicitly documented as "no diarization needed"
  (reframe.py:21, README "Multicam / two-speaker sources").

The known failure case, already called out in README:216 and CHANGELOG:218, is
**two speakers inside one static wide shot** — no camera cuts to follow, so the
whole clip gets a single averaged crop and neither speaker is framed well.

## What VibeVoice offers

Repo ships both a TTS and an ASR family; only the ASR side is relevant here.

- **VibeVoice-ASR (7B)** — outputs *who / when / what*: speaker labels,
  timestamps, and transcript in one pass. That is diarization + ASR fused,
  rather than pyannote-style clustering bolted onto Whisper.
- Handles up to ~60 min of continuous audio in a 64K token context, so a full
  podcast episode fits in a single call instead of chunk-and-stitch.
- MIT licensed (no pyannote-style gated HF weights / token dance — see
  `shortsmith/align.py:10` for the whisperx+pyannote sibling-venv pain).
- TTS variants (1.5B, 0.5B streaming, up to 4 speakers) are not needed for
  this; ignore them unless a synthetic-VO feature ever shows up.

## Where it would plug in

1. **Reframe (biggest win).** A speaker timeline (`speaker, t_start, t_end`)
   lets `reframe.py` gain a third mode: crop follows the *active* speaker,
   matching a detected face to the active label. This fixes the single-wide-shot
   two-speaker case that `--cut-aware` cannot. It could also serve as a
   cross-check on cut-aware runs, where a camera cut does not actually change
   who is talking.
2. **Clip selection.** Speaker labels let clip scoring prefer segments where the
   host (or the guest) is the one talking, instead of treating the transcript as
   one voice.
3. **Captions.** Per-speaker caption styling / name tags in the Remotion layer
   for podcast sources.

## Open questions before anyone builds this

- VRAM/latency for the 7B ASR on the 5090, and whether it must be another
  sibling venv (torch pin conflicts) like `whisperx-align` and `audio-enhance`.
  Assume yes until proven otherwise — do NOT install it into the shortsmith venv.
- Does it replace WhisperX, or run alongside it? WhisperX is currently the
  source of word-level alignment for captions; VibeVoice timestamps may be
  segment-level and not precise enough to replace that. Most likely shape:
  WhisperX keeps word alignment, VibeVoice only supplies the speaker timeline,
  and the two are merged by timestamp overlap.
- Diarization accuracy on the actual sources (livestreams, webinars, the Brad
  Lea style podcast) versus plain pyannote — needs a bake-off on one known clip
  before committing.
- Voice-label → on-screen-face mapping is a separate problem VibeVoice does not
  solve. Audio says "speaker 1 is talking"; something still has to decide which
  detected face that is (mouth-motion correlation, or a one-time manual map per
  source).

## Verdict

Worth a spike, not a scheduled task. Highest-value target is the wide-shot
two-speaker reframe gap. Prototype standalone (own venv, one test clip, dump the
speaker timeline to JSON) before touching the pipeline.
