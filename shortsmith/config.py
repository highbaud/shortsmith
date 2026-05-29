"""Central config: paths, env vars, tunables.

All paths can be overridden via environment variables (see SHORTSMITH_* below)
or a project-local `.env` at the repo root (auto-loaded on import). Defaults
resolve relative to the shortsmith repo root, which is the layout used when
you clone the repo as-is from GitHub.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root = parent of the shortsmith/ package directory.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(env_var: str, default_rel: str) -> Path:
    """Read an absolute path from an env var, else fall back to REPO_ROOT/<default_rel>.

    Lets the same codebase work both as a bundled repo (defaults) and against
    sibling projects on a developer's machine (env-var overrides via .env).
    """
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser().resolve()
    return (REPO_ROOT / default_rel).resolve()


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from .env at the shortsmith project root.

    Existing environment variables take precedence — a real env var won't be
    overwritten by the file. Comments (`#`) and blank lines are ignored.
    """
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()

# Path constants. Evaluated AFTER _load_dotenv() so .env entries are visible.
KIT_ROOT              = _resolve_path("SHORTSMITH_KIT_ROOT",        "hyperframes-student-kit")
AUTO_SHORTS_ROOT      = KIT_ROOT / "video-projects" / "auto-shorts"
# TEMPLATE_REF derives from KIT_ROOT by default. Override via SHORTSMITH_TEMPLATE_REF
# only if your template project lives outside the kit.
_template_override = os.environ.get("SHORTSMITH_TEMPLATE_REF")
TEMPLATE_REF          = Path(_template_override).expanduser().resolve() \
                        if _template_override \
                        else KIT_ROOT / "video-projects" / "may-shorts-19"
AUDIO_ENHANCE_PROJECT = _resolve_path("SHORTSMITH_AUDIO_ENHANCE",   "audio-enhance")
WHISPERX_ALIGN_PROJECT = _resolve_path("SHORTSMITH_WHISPERX_ALIGN", "whisperx-align")
VIDEO_DIR             = _resolve_path("SHORTSMITH_VIDEO_DIR",       "videos")
SFX_DIR               = _resolve_path("SHORTSMITH_SFX_DIR",        "assets/sfx")

DEFAULT_FILLERS = [
    # Pure stammers — never content.
    "um", "uh", "uhm", "erm", "mm",
    # Discourse fillers that are almost always filler in conversational
    # podcast speech.
    "you know",
    # NOT in this list (too often content words — chopping them sounds wrong):
    #   "like"        — verb, preposition, simile marker
    #   "basically"   — sometimes a real adverb
    #   "literally"   — sometimes a real adverb
    #   "sort of" / "kind of" — softeners but often meaningful
    #   "right?"      — tag question, often a real beat
]

@dataclass
class Config:
    # Whisper
    whisper_model: str = os.environ.get("SHORTSMITH_WHISPER_MODEL", "large-v3")
    whisper_device: str = os.environ.get("SHORTSMITH_WHISPER_DEVICE", "cuda")
    whisper_compute_type: str = os.environ.get("SHORTSMITH_WHISPER_COMPUTE", "float16")

    # Clip engine. "anthropic" (default, best quality, costs API credits) or
    # "ollama" (local OpenAI-compatible endpoint, free, experimental, requires
    # a running Ollama / LM Studio / vLLM server).
    clip_engine: str = os.environ.get("SHORTSMITH_CLIP_ENGINE", "anthropic")

    # Anthropic
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    claude_model: str = os.environ.get("SHORTSMITH_CLAUDE_MODEL", "claude-opus-4-7")

    # Local LLM (Ollama / LM Studio / vLLM — any OpenAI-compatible endpoint)
    local_llm_url: str = os.environ.get("SHORTSMITH_LOCAL_LLM_URL", "http://localhost:11434/v1")
    local_llm_model: str = os.environ.get("SHORTSMITH_LOCAL_LLM_MODEL", "llama3.1:70b")
    local_llm_temperature: float = float(os.environ.get("SHORTSMITH_LOCAL_LLM_TEMP", "0.3"))

    # Clip selection
    min_clip_seconds: float = 30.0
    max_clip_seconds: float = 120.0
    # Post-filter: drop any clip Claude returned with viral_score below this.
    # 7 = "would stake credibility on >100k views". Lower → more clips, lower
    # average quality. Higher → fewer clips, only the standouts.
    min_viral_score: int = int(os.environ.get("SHORTSMITH_MIN_SCORE", "7"))

    # Boundary snapping
    boundary_snap_window: float = 0.6
    boundary_min_silence: float = 0.20
    boundary_breath_silence: float = 0.35  # silence treated as natural breath even without punctuation

    # Reorder seam
    seam_xfade_seconds: float = 0.08

    # Filler & silence
    fillers: list[str] = field(default_factory=lambda: list(DEFAULT_FILLERS))
    filler_pad_seconds: float = 0.06
    silence_threshold: float = 0.04
    # Was 0.20 — bumped to 0.30 so even when Whisper's word-end timing is
    # slightly early, the silence cut preserves the audio tail of the spoken
    # word rather than clipping its trailing consonant.
    silence_margin: float = 0.30

    # Stutter / immediate word-repetition repair (clean step).
    # When the speaker stammers ("I-I-I think", "the the wealth"), keep only the
    # final occurrence. Conservative: only collapses identical adjacent stems
    # separated by a gap shorter than stutter_max_gap, so deliberate emphasis
    # ("no, no, no") with normal pacing survives.
    stutter_repair: bool = True
    stutter_max_gap: float = 0.35          # seconds between repeats to count as a stammer
    stutter_min_repeats: int = 2           # 2 = collapse any immediate doubling

    # Loudness normalization (after enhance). Two-pass ffmpeg loudnorm to a
    # consistent integrated loudness so clips don't get scroll-past'd (too quiet)
    # or clipped (too hot). -14 LUFS = the TikTok/Instagram/YouTube playback
    # normalization target for short-form vertical.
    loudness_enabled: bool = True
    loudness_target_lufs: float = float(os.environ.get("SHORTSMITH_LUFS", "-14.0"))
    loudness_true_peak: float = -1.5       # dBTP ceiling — headroom for lossy re-encode
    loudness_range: float = 11.0           # target LRA

    # Forced alignment (step 6). "whisperx" (default) re-transcribes then aligns
    # word boundaries to ~20ms via wav2vec2 — tight captions + clean cut seams.
    # Runs in sibling uv project WHISPERX_ALIGN_PROJECT. Falls back to
    # "faster-whisper" (the in-process re-transcribe) if whisperx is unavailable.
    align_engine: str = os.environ.get("SHORTSMITH_ALIGN", "whisperx")

    # Sound effects (post-render overlay pass — scripts/add_sfx.py).
    # A curated, level-normalized pack lives in SFX_DIR/pack/ with a pack.json
    # mapping each slot to one or more variant files (rotated for variety).
    # If pack.json is absent, load_sfx_map falls back to slot-named files.
    sfx_enabled: bool = True
    sfx_gain: float = 0.7              # global multiplier on top of per-slot gain
    sfx_limit: float = 0.97           # output limiter ceiling to avoid clipping
    sfx_swipe_out: bool = False       # also play a swipe when a callout LEAVES (denser)
    # Per-slot gain — swipes sit further under the voice than the money/stat
    # accents, which are meant to "pop". Multiplied by sfx_gain at mix time.
    sfx_slot_gain: dict = field(default_factory=lambda: {
        "swipe-in": 0.55,
        "swipe-out": 0.45,
        "hook-impact": 0.7,
        "cash-register": 0.85,
        "wrong-answer": 0.7,
        "ding": 0.7,
        "whoosh": 0.55,
    })
    # "sparing" = cash-register only on first money mention, ding only on
    # bigstat $ callouts, wrong-answer only on first negative word.
    # "every" = every match. "off" = structural swipes only.
    sfx_semantic_mode: str = os.environ.get("SHORTSMITH_SFX_SEMANTIC", "sparing")
    money_keywords: list[str] = field(default_factory=lambda: [
        "money", "cash", "dollar", "dollars", "rich", "wealth", "wealthy",
        "thousand", "thousands", "million", "millions", "millionaire",
        "billion", "billions", "billionaire", "trillion", "trillions",
        "fortune", "profit", "profits", "payday",
    ])
    # Visual transitions (Remotion VFX layer).
    # A family of three primitives — glare (Capcut-style horizontal light
    # sweep), zoom-punch (brief 1.0 -> 1.04 scale bump), flash (~80ms
    # full-frame color tint) — each fires on the same 4 high-impact slots
    # the audio SFX already uses, so audio + visual punctuation sync.
    # Wholesale disable via cfg.vfx_enabled = False (or SHORTSMITH_VFX=off).
    vfx_enabled: bool = (os.environ.get("SHORTSMITH_VFX", "on").lower()
                         not in ("off", "0", "false", "no"))
    vfx_intensity: float = float(os.environ.get("SHORTSMITH_VFX_INTENSITY", "1.0"))
    # Slot -> list of effect names. Default mapping is "sparing" (4 punchy
    # beats only — never on every callout). Override per-slot to taste.
    vfx_triggers: dict = field(default_factory=lambda: {
        "hook-impact":  ["glare", "zoom-punch", "flash"],  # big slam
        "ding":         ["glare"],                          # gold accent
        "cash-register":["glare", "flash"],                 # gold money pop
        "wrong-answer": ["flash", "zoom-punch"],            # red error punch
    })
    # Per-slot color tint (hex). Glare/flash use the tint as the gradient/
    # overlay color; zoom-punch ignores it (geometry-only effect).
    vfx_colors: dict = field(default_factory=lambda: {
        "hook-impact":   "#ffffff",  # white
        "ding":          "#f5c842",  # gold (xrp-revolution primary)
        "cash-register": "#f5c842",  # gold
        "wrong-answer":  "#ff3653",  # red (xrp-revolution danger)
    })

    # Negative-outcome words that trigger the wrong-answer slot. Tuned for
    # crypto/finance livestream content — the moment a clip names the bad
    # outcome ("crashed", "rugged", "scammed", "bankrupt") the error buzz
    # punctuates the beat. Sparring/quiz-show feel rather than mean-spirited.
    negative_keywords: list[str] = field(default_factory=lambda: [
        "wrong", "incorrect",
        "lose", "lost", "loses", "losing", "loss", "losses",
        "crash", "crashed", "crashes", "crashing",
        "rug", "rugged", "rugpull", "rugpulled",
        "scam", "scams", "scammed", "scammer", "scammers",
        "fail", "failed", "fails", "failing", "failure",
        "bankrupt", "bankruptcy", "broke", "busted",
        "dumped", "dumping",
        "rekt", "rip",
    ])

    # Audio enhancement.
    # "clearvoice" (default) = ClearerVoice-Studio MossFormer2_SE_48K, SOTA
    #   48 kHz speech enhancement. Runs in the sibling uv project at
    #   AUDIO_ENHANCE_PROJECT (default: <repo>/audio-enhance).
    # "voicefixer" = fallback (works out-of-the-box, lower quality).
    # "resemble" / "deepfilter" = legacy options, harder Windows install.
    enhance_engine: str = os.environ.get("SHORTSMITH_ENHANCE", "clearvoice")

    # Reframe
    yunet_model_path: Path = Path(__file__).parent.parent / "models" / "face_detection_yunet_2023mar.onnx"
    yunet_score_threshold: float = 0.7   # was 0.6 — bump to drop weak misfires
    reframe_smooth_alpha: float = 0.1    # EMA alpha on face x/y (legacy; reframe v2 uses median)
    reframe_sample_every: int = 3        # was 5 — sample 10/sec at 30fps for IQR stats
    reframe_min_face_h: float = 180.0    # absolute floor for bbox height (logo/avatar reject)
    reframe_min_face_h_frac: float = 0.08  # relative floor: 8% of source height (auto-scales for 4K)
    # Target face placement in the 1080x1920 output frame, expressed as
    # fractions of the 1920px vertical. Defaults follow social-media-safe
    # framing: face center near the top-third line, face takes ~42% of vertical
    # so the speaker reads as prominent without the mouth landing in IG/TikTok's
    # bottom UI overlay zone (which typically covers y=1500-1920).
    # xrp-revolution-style framing: face occupies ~32% vertical, eyes a bit
    # below top-third. Chest and shoulders visible. Generous headroom above
    # head so overlays at top of frame don't compete with hairline.
    face_target_y: float = 0.40       # face center at 40% from top (eyes ~y=720)
    face_target_height: float = 0.32  # face bbox is ~32% of vertical (~615px)

    # Scaffold
    enable_captions: bool = False  # karaoke captions inclusion in index.html
    enable_callouts: bool = True   # big-text scene overlays at key moments
    # Visual style preset. Ships with: "xrp-revolution" (default),
    # "minimal", "bold". Each preset is a templates/styles/<name>/style.json
    # with colors, fonts, hook sizing, and overlay flags.
    style: str = os.environ.get("SHORTSMITH_STYLE", "xrp-revolution")

    # Captions
    phrase_gap_seconds: float = 0.45
    phrase_max_words: int = 4

    def validate(self) -> list[str]:
        problems = []
        # API key is only required when the Anthropic engine is selected.
        if self.clip_engine == "anthropic" and not self.anthropic_api_key:
            problems.append(
                "ANTHROPIC_API_KEY env var is not set. Set it in .env, "
                "or switch to --clip-engine ollama for free local picking."
            )
        if not KIT_ROOT.exists():
            problems.append(
                f"Hyperframes kit not found at {KIT_ROOT}. "
                "Run setup.sh / setup.ps1 to initialise the submodule, "
                "or set SHORTSMITH_KIT_ROOT to an existing kit path."
            )
        if not TEMPLATE_REF.exists():
            problems.append(
                f"Template reference not found at {TEMPLATE_REF}. "
                "Set SHORTSMITH_TEMPLATE_REF to a valid template project under the kit."
            )
        if self.enhance_engine == "clearvoice" and not AUDIO_ENHANCE_PROJECT.exists():
            problems.append(
                f"Audio-enhance project not found at {AUDIO_ENHANCE_PROJECT}. "
                "Run setup.sh / setup.ps1, set SHORTSMITH_AUDIO_ENHANCE, "
                "or pass --no-enhance / --engine voicefixer."
            )
        return problems


def make_work_dir(source_video: Path) -> Path:
    """Per-source-video working directory under shortsmith/work/<source-slug>/."""
    from slugify import slugify
    slug = slugify(source_video.stem)[:60]
    work = Path(__file__).parent.parent / "work" / slug
    work.mkdir(parents=True, exist_ok=True)
    return work


def make_output_dir(source_video: Path) -> Path:
    """Per-source-video output dir under hyperframes-student-kit/video-projects/auto-shorts/<source-slug>/."""
    from slugify import slugify
    slug = slugify(source_video.stem)[:60]
    out = AUTO_SHORTS_ROOT / slug
    out.mkdir(parents=True, exist_ok=True)
    return out
