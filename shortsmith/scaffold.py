"""Step 8: Scaffold a Hyperframes short project per clip.

Output structure (per clip):
    hyperframes-student-kit/video-projects/auto-shorts/<source-slug>/short-NN-<hook-slug>/
    ├── index.html              # rendered from templates/index.html.j2
    ├── meta.json               # rendered from meta.json.j2
    ├── hyperframes.json        # registry pointer
    ├── compositions/           # emptied of stale sub-comps; every overlay is
    │                           # inlined into index.html
    ├── assets/
    │   ├── clip-edit.mp4       # the 9:16 reframed clip
    │   └── words.json          # word-level transcript for this clip
    └── renders/                # empty
"""
from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ._files import atomic_write
from ._media import probe_duration
from .captions import normalize_dashes, strip_hashtags
from .config import TEMPLATE_REF, make_output_dir
from .overlay_timing import callout_timing, hook_duration
from .records import ClipManifest, ClipSpec, TranscriptWord, current_media, require_words

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STYLES_DIR = TEMPLATES_DIR / "styles"


def _load_style(name: str) -> dict:
    """Load a style preset's style.json. Falls back to xrp-revolution if missing."""
    style_path = STYLES_DIR / name / "style.json"
    if not style_path.exists():
        log.warning("Style preset %r not found at %s; falling back to xrp-revolution",
                    name, style_path)
        style_path = STYLES_DIR / "xrp-revolution" / "style.json"
    return json.loads(style_path.read_text(encoding="utf-8"))


# Fields the reframe step works out that the downstream caption/render layer
# needs. They are computed from the footage, so they cannot be in the authored
# clips.json, carry them across into the copy that ships with the projects.
_MANIFEST_PASSTHROUGH: tuple[Literal["caption_band", "layout", "layout_preset"], ...] = (
    "caption_band", "layout", "layout_preset",
)


def _write_merged_clips(clips: list[ClipSpec], manifests: list[ClipManifest], dest: Path) -> None:
    """Write _clips.json: the authored clip specs plus the reframe-derived
    fields, joined on rank. Authored values win, so a hand-set caption_band is
    never overwritten by a detected one."""
    by_rank = {m.get("rank"): m for m in manifests}
    merged = []
    for clip in clips:
        out = dict(clip)
        m = by_rank.get(clip.get("rank"))
        if m:
            for key in _MANIFEST_PASSTHROUGH:
                if key in m and key not in out:
                    out[key] = m[key]
        merged.append(out)
    atomic_write(dest, json.dumps(merged, indent=2, ensure_ascii=False))


def scaffold_all(
    clip_manifests: list[ClipManifest],
    clips: list[ClipSpec],
    source_video: Path,
    work_dir: Path,
    style: str,
) -> list[Path]:
    """Build one Hyperframes project per clip. Returns list of project dirs."""
    out_root = make_output_dir(source_video)

    src_transcript = work_dir / "transcript.json"
    src_clips = work_dir / "clips.json"
    if src_transcript.exists():
        shutil.copy(src_transcript, out_root / "_transcript.json")
    if src_clips.exists():
        _write_merged_clips(clips, clip_manifests, out_root / "_clips.json")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        # Match "*.html.j2"; ["html"] matched nothing, so model text went into HTML raw.
        autoescape=select_autoescape(["html.j2"]),
        keep_trailing_newline=True,
    )

    project_dirs = []
    by_rank = {c["rank"]: c for c in clips}
    for m in clip_manifests:
        rank = m["rank"]
        clip = by_rank.get(rank)
        if clip is None:
            log.warning("Clip rank %d in manifest has no matching clip metadata; skipping", rank)
            continue
        project = _scaffold_one(env, m, clip, source_video, out_root, style)
        project_dirs.append(project)

    return project_dirs


def _load_clip_words(manifest: ClipManifest, rank: int) -> list[TranscriptWord]:
    """The clip's aligned word list, or [] when step 6 left none behind.

    Resolved by hand rather than with `Path(manifest.get("words_path", ""))`:
    `Path("")` is `Path(".")`, a directory passes `.exists()`, and reading it
    raises instead of taking the missing-transcript branch. A manifest carries
    no `words_path` whenever the run resumed past step 6.
    """
    ref = str(manifest.get("words_path") or "")
    if ref and Path(ref).is_file():
        return require_words(json.loads(Path(ref).read_text(encoding="utf-8")), ref)
    log.warning("Words JSON missing for clip %d; captions will be empty", rank)
    return []


def _scaffold_one(
    env: Environment,
    manifest: ClipManifest,
    clip: ClipSpec,
    source_video: Path,
    out_root: Path,
    style_name: str,
) -> Path:
    rank = manifest["rank"]
    slug = clip.get("slug") or manifest.get("slug") or f"clip-{rank}"
    project_dir = out_root / f"short-{rank:02d}-{slug}"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "compositions").mkdir(exist_ok=True)
    (project_dir / "assets").mkdir(exist_ok=True)
    (project_dir / "renders").mkdir(exist_ok=True)
    # The template declares this face so HyperFrames 0.9+ cannot swap in League Gothic.
    shutil.copytree(TEMPLATES_DIR / "fonts", project_dir / "assets" / "fonts", dirs_exist_ok=True)

    final_clip = Path(manifest.get("vertical_path") or current_media(manifest))

    clip_dst = project_dir / "assets" / "clip-edit.mp4"
    shutil.copy(final_clip, clip_dst)

    duration = probe_duration(clip_dst)

    words = _load_clip_words(manifest, rank)

    atomic_write(
        project_dir / "assets" / "words.json", json.dumps(words, indent=2, ensure_ascii=False)
    )

    # Composition IDs unique per project
    comp_id_main = f"short-{rank:02d}-main"

    callouts = _build_callouts(clip, rank, duration)

    hook = _build_hook(clip, duration)

    style = _load_style(style_name)
    index_html = env.get_template("index.html.j2").render(
        title=f"Short {rank:02d} — {slug}",
        comp_id_main=comp_id_main,
        duration=duration,
        callouts=callouts,
        hook=hook,
        style=style,
    )
    atomic_write(project_dir / "index.html", index_html)

    # Clean up old sub-comps from prior runs (different style era).
    comps_dir = project_dir / "compositions"
    if comps_dir.exists():
        for p in comps_dir.glob("*.html"):
            try:
                p.unlink()
            except OSError:
                pass

    meta = {
        "id": f"short-{rank:02d}-{slug}",
        "name": f"Short {rank:02d} — {clip.get('hook_text','')[:60]}",
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "_shortsmith": {
            "viral_score": clip.get("viral_score"),
            "hook_text": clip.get("hook_text"),
            "hook_start": clip.get("hook_start"),
            "hook_end": clip.get("hook_end"),
            "reasoning": clip.get("reasoning"),
            "source_video": str(source_video),
            "source_segments": clip.get("segments"),
            "snapped_segments": manifest.get("snapped_segments"),
            "duration": duration,
        },
    }
    atomic_write(
        project_dir / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False)
    )

    src_hf = TEMPLATE_REF / "hyperframes.json"
    if src_hf.exists():
        shutil.copy(src_hf, project_dir / "hyperframes.json")
    else:
        atomic_write(project_dir / "hyperframes.json", json.dumps({
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
            "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        }, indent=2))

    # Instagram caption — write a .txt with the same stem as the project folder,
    # placed at the source-slug parent so all captions are scannable side-by-side.
    caption_text = normalize_dashes(strip_hashtags(clip.get("instagram_caption") or _fallback_caption(clip)))
    caption_path = project_dir.parent / f"{project_dir.name}.txt"
    atomic_write(caption_path, caption_text + "\n")
    # Also drop one inside the project for portability.
    atomic_write(project_dir / "caption.txt", caption_text + "\n")

    log.info("Scaffolded %s (duration=%.2fs, %d callouts, ig-caption=%dch)",
             project_dir.relative_to(project_dir.parent.parent), duration, len(callouts), len(caption_text))
    return project_dir


def _fallback_caption(clip: ClipSpec) -> str:
    """Build a minimal Instagram caption from clip metadata when Claude didn't
    include one (e.g., for clips selected before instagram_caption was in the
    prompt schema, or for hand-crafted smoke-test clips).
    """
    hook = (clip.get("hook_text") or clip.get("slug", "").replace("-", " ")).strip()
    reasoning = (clip.get("reasoning") or "").strip()

    # Heuristic: use the hook line as both the attention-grab and the body
    # opener, then a simple CTA. No hashtags (user adds their own per platform).
    # The user is expected to edit this — it's a fallback, not the primary output.
    headline = hook.upper() if hook and not hook.endswith("?") else hook
    body_blurb = reasoning if reasoning else "Save this for later, it lands harder the second time."

    return (
        f"{headline}\n\n"
        f"{body_blurb}\n\n"
        f"Follow for more."
    )


# Max visible characters for a `bigstat` callout before it overflows the 1080px
# frame at the giant display size. Tuned against real overflows ("$800T-$4Q").
BIGSTAT_MAX_CHARS = 9

# The clips.json callout vocabulary. Both the callouts and the hook accept the
# same colors, so the mapping lives in one place rather than once per builder.
VALID_STYLES = {"caption", "punch", "bigstat", "hero"}
VALID_COLORS = {"gold", "red", "green"}
_LEGACY_COLORS = {"orange": "red", "cyan": "gold"}


def _normalize_color(value: str | None, default: str) -> str:
    """A clips.json color name mapped onto the current palette.

    Anything the palette does not carry falls back to `default`, which is the
    color that builder considers its own baseline.
    """
    color = (value or default).lower()
    color = _LEGACY_COLORS.get(color, color)
    return color if color in VALID_COLORS else default


def _accent_words(raw: Iterable[object] | str | None) -> list[str]:
    """The accent-word list of a clips.json entry, trimmed, blanks dropped.

    A bare string is one accent word; iterating it would accent single letters.
    """
    words = [raw] if isinstance(raw, str) else (raw or [])
    return [str(w).strip() for w in words if str(w).strip()]


def _build_callouts(clip: ClipSpec, rank: int, clip_duration: float) -> list[dict]:
    """Build the per-clip callout list `index.html.j2` renders.

    Each dict carries the timing (`local_start`, `duration`), the presentation
    (`style`, `color`, `eyebrow`) and the pre-escaped `html` / `subline` the
    template inlines, so the template never sees raw clips.json text.

    A clip's `callouts` field in clips.json looks like:
        [{
          "local_start": 12.3,
          "duration": 1.8,
          "text": "BIT ME IN THE ASS",
          "accent": ["BIT"],            # optional, list of words to color-accent
          "eyebrow": "ON GREED",         # optional, small caps label above
          "color": "gold"                # "gold" | "red" | "green" (default gold)
        }]
    """
    raw = clip.get("callouts") or []
    if not raw:
        return []

    out: list[dict] = []
    for i, co in enumerate(raw, start=1):
        timing = callout_timing(co, clip_duration)
        if timing is None:
            log.warning("Clip %d callout %d: bad timestamp; skipping", rank, i)
            continue
        local_start, duration = timing

        raw_text = str(co.get("text", "")).strip()
        if not raw_text:
            continue

        style = (co.get("style") or "caption").lower()
        if style not in VALID_STYLES:
            style = "caption"

        # bigstat renders the main text as a full-width giant number. Past ~9
        # visible characters it overflows both frame edges (e.g. the "$800T-$4Q"
        # clip that clipped at both sides). Warn loudly so it's fixed in
        # clips.json (shorten to the single figure that matters) before render.
        if style == "bigstat":
            visible = len(raw_text.replace(" ", "").replace("\n", ""))
            if visible > BIGSTAT_MAX_CHARS:
                log.warning(
                    "Clip %d callout %d: bigstat text %r is %d chars (> %d) and "
                    "will overflow the frame. Shorten it to the single figure "
                    "that lands (e.g. \"$4Q\", not \"$800T-$4Q\").",
                    rank, i, raw_text, visible, BIGSTAT_MAX_CHARS,
                )

        color = _normalize_color(co.get("color"), "gold")
        accent = _accent_words(co.get("accent"))
        html = _render_text(raw_text, accent, color, style)
        subline_html = ""
        if co.get("subline"):
            subline_html = _render_text(str(co["subline"]), [], color, "caption")

        out.append({
            "local_start": local_start,
            "duration": duration,
            "style": style,
            "color": color,
            "html": html,
            "eyebrow": (co.get("eyebrow") or "").strip(),
            "subline": subline_html,
        })

    return out


def _build_hook(clip: ClipSpec, clip_duration: float) -> dict | None:
    """Build the opening-slam hook from a clip's `hook` field.

    Schema:
        "hook": {
          "overline": "WARNING",            # optional, small caps eyebrow
          "text": "Don't be exit\\nliquidity.",
          "accent": ["liquidity"],
          "color": "red",                    # "red" | "gold" | "green"
          "duration": 2.8                    # optional, default 2.6
        }

    Returns the render-ready dict (with `html` pre-rendered) or None if no hook.
    """
    raw = clip.get("hook")
    if not raw:
        return None
    # clips.json is authored by an LLM (or by hand, when --from-step 3 skips
    # the normalizer), so a hook can arrive as a bare string or with a null
    # duration. One malformed hook must not throw away every scaffolded
    # project in the batch.
    if not isinstance(raw, dict):
        log.warning("Hook is not a {text, color, duration} object (%r); skipping it", raw)
        return None
    if not str(raw.get("text", "")).strip():
        return None

    color = _normalize_color(raw.get("color"), "red")

    duration = hook_duration(raw, clip_duration)

    accent = _accent_words(raw.get("accent"))
    # Hook uses the "slam" style — uppercase like a punch
    html = _render_text(str(raw["text"]), accent, color, style="slam")

    return {
        "color": color,
        "duration": duration,
        "overline": (raw.get("overline") or "").strip(),
        "html": html,
    }


def _render_text(text: str, accent_words: list[str], color: str, style: str) -> str:
    """Convert plain text (with `\\n` newlines) + accent words into safe HTML.

    Accent matching is case-insensitive, ignoring trailing punctuation. Each
    occurrence gets wrapped in a span with class `em-<color>` so the CSS picks
    up the right color for the overlay style.

    Caption style preserves original casing (Inter 700 reads better in sentence
    case at 62px); other styles uppercase everything for visual punch.
    """
    import html as html_escape

    # Scrub em/en dashes + mojibake from on-screen overlay text too (per line, so
    # the \n layout breaks survive) — same rule as captions/metadata, so future
    # videos never bake a stray dash into the hook/callout cards.
    text = "\n".join(normalize_dashes(ln) for ln in text.split("\n"))

    if style != "caption":
        text = text.upper()

    accent_normalized = {a.upper().strip().rstrip(".,!?:;") for a in accent_words}
    color_class = f"em-{color}"

    lines = text.split("\n")
    rendered_lines = []
    for line in lines:
        words = line.split(" ")
        rendered = []
        for w in words:
            key = w.strip().rstrip(".,!?:;").upper()
            if key and key in accent_normalized:
                rendered.append(f'<span class="{color_class}">{html_escape.escape(w)}</span>')
            else:
                rendered.append(html_escape.escape(w))
        rendered_lines.append(" ".join(rendered))
    return "<br>".join(rendered_lines)
