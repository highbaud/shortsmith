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
VIDEO_DIR             = _resolve_path("SHORTSMITH_VIDEO_DIR",       "videos")

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
