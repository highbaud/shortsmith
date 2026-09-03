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
import re
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ._media import probe_duration
from .config import TEMPLATE_REF, Config, make_output_dir

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
_MANIFEST_PASSTHROUGH = ("caption_band", "layout", "layout_preset")


def _write_merged_clips(clips: list[dict], manifests: list[dict], dest: Path) -> None:
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
    dest.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")


def scaffold_all(
    clip_manifests: list[dict],
    clips: list[dict],
    source_video: Path,
    work_dir: Path,
    cfg: Config,
) -> list[Path]:
    """Build one Hyperframes project per clip. Returns list of project dirs."""
    out_root = make_output_dir(source_video)

    # Copy whole-source transcript + clips.json into the per-source root
    src_transcript = work_dir / "transcript.json"
    src_clips = work_dir / "clips.json"
    if src_transcript.exists():
        shutil.copy(src_transcript, out_root / "_transcript.json")
    if src_clips.exists():
        _write_merged_clips(clips, clip_manifests, out_root / "_clips.json")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
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
        project = _scaffold_one(env, m, clip, source_video, out_root, cfg)
        project_dirs.append(project)

    return project_dirs


def _load_clip_words(manifest: dict, rank: int) -> list[dict]:
    """The clip's aligned word list, or [] when step 6 left none behind.

    Resolved by hand rather than with `Path(manifest.get("words_path", ""))`:
    `Path("")` is `Path(".")`, a directory passes `.exists()`, and reading it
    raises instead of taking the missing-transcript branch. A manifest carries
    no `words_path` whenever the run resumed past step 6.
    """
    ref = str(manifest.get("words_path") or "")
    if ref and Path(ref).is_file():
        return json.loads(Path(ref).read_text(encoding="utf-8"))
    log.warning("Words JSON missing for clip %d; captions will be empty", rank)
    return []


def _scaffold_one(
    env: Environment,
    manifest: dict,
    clip: dict,
    source_video: Path,
    out_root: Path,
    cfg: Config,
) -> Path:
    rank = manifest["rank"]
    slug = clip.get("slug") or manifest.get("slug") or f"clip-{rank}"
    project_dir = out_root / f"short-{rank:02d}-{slug}"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "compositions").mkdir(exist_ok=True)
    (project_dir / "assets").mkdir(exist_ok=True)
    (project_dir / "renders").mkdir(exist_ok=True)

    # Locate the final clip video
    final_clip = Path(manifest.get("vertical_path")
                      or manifest.get("enhanced_path")
                      or manifest.get("cleaned_path")
                      or manifest["raw_path"])

    # Place the clip into assets/
    clip_dst = project_dir / "assets" / "clip-edit.mp4"
    shutil.copy(final_clip, clip_dst)

    # Probe actual final duration (auto-editor may have shortened it further)
    duration = probe_duration(clip_dst)

    # Load the clip's word-level transcript (from step 6: retranscribe)
    words = _load_clip_words(manifest, rank)

    # Save the clip's transcript alongside (handy for downstream edits)
    (project_dir / "assets" / "words.json").write_text(
        json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Composition IDs unique per project
    comp_id_main = f"short-{rank:02d}-main"

    # Build callouts list — each item has html ready to inline in the template.
    callouts = _build_callouts(clip, rank, duration, cfg)

    # Build the opening hook (thumbnail moment) if present.
    hook = _build_hook(clip, duration)

    # Load style preset and render index.html.
    style = _load_style(cfg.style)
    index_html = env.get_template("index.html.j2").render(
        title=f"Short {rank:02d} — {slug}",
        comp_id_main=comp_id_main,
        duration=duration,
        callouts=callouts,
        hook=hook,
        style=style,
    )
    (project_dir / "index.html").write_text(index_html, encoding="utf-8")

    # Clean up old sub-comps from prior runs (different style era).
    comps_dir = project_dir / "compositions"
    if comps_dir.exists():
        for p in comps_dir.glob("*.html"):
            try:
                p.unlink()
            except OSError:
                pass

    # meta.json
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
    (project_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # hyperframes.json — registry pointer (copy from reference)
    src_hf = TEMPLATE_REF / "hyperframes.json"
    if src_hf.exists():
        shutil.copy(src_hf, project_dir / "hyperframes.json")
    else:
        # Fallback minimal config
        (project_dir / "hyperframes.json").write_text(json.dumps({
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
            "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        }, indent=2), encoding="utf-8")

    # Instagram caption — write a .txt with the same stem as the project folder,
    # placed at the source-slug parent so all captions are scannable side-by-side.
    caption_text = normalize_dashes(_strip_hashtags(clip.get("instagram_caption") or _fallback_caption(clip)))
    caption_path = project_dir.parent / f"{project_dir.name}.txt"
    caption_path.write_text(caption_text + "\n", encoding="utf-8")
    # Also drop one inside the project for portability.
    (project_dir / "caption.txt").write_text(caption_text + "\n", encoding="utf-8")

    log.info("Scaffolded %s (duration=%.2fs, %d callouts, ig-caption=%dch)",
             project_dir.relative_to(project_dir.parent.parent), duration, len(callouts), len(caption_text))
    return project_dir


# Matches a hashtag token (#word, #word-with-dashes) plus any leading whitespace,
# so removing it doesn't leave dangling spaces.
_HASHTAG_RE = re.compile(r"[ \t]*#\w[\w-]*")


# Em / en / figure dashes + U+FFFD (the mojibake a corrupted em-dash becomes in
# some clip-gen output). All get normalized to plain punctuation that always
# renders and doesn't read as AI.
_DASH_CHARS = "—–‒―�"  # em, en, figure, horizontal bar, U+FFFD mojibake
_RANGE_DASH_RE = re.compile(rf"(?<=\d)\s*[{_DASH_CHARS}]\s*(?=\d)")
_PROSE_DASH_RE = re.compile(rf"\s*[{_DASH_CHARS}]\s*")


def normalize_dashes(text: str) -> str:
    """Replace em/en/figure dashes and the U+FFFD mojibake with plain punctuation:
    a hyphen between digits (ranges like 2020-2021), a comma elsewhere (a prose
    pause). Then tidy spacing. Safe to run repeatedly."""
    if not text:
        return text
    text = _RANGE_DASH_RE.sub("-", text)            # numeric ranges -> hyphen
    text = _PROSE_DASH_RE.sub(", ", text)           # prose pause -> comma
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)    # no space before punctuation
    text = re.sub(r",\s*,", ",", text)              # collapse doubled commas
    text = re.sub(r"(^|\n)\s*,\s*", r"\1", text)    # drop a comma left at line start
    text = re.sub(r"[ \t]{2,}", " ", text)          # collapse space runs
    return text


def _strip_hashtags(text: str) -> str:
    """Remove every #hashtag from a caption and tidy the resulting whitespace.

    Captions ship without hashtags (user preference) — the creator adds their
    own per-platform. Drops all '#tag' tokens, then collapses the blank lines
    that an end-of-caption hashtag block leaves behind.
    """
    cleaned = _HASHTAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)   # trailing spaces per line
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)   # collapse 3+ blank lines
    return cleaned.strip()


def _fallback_caption(clip: dict) -> str:
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


def _normalize_color(value: object, default: str) -> str:
    """A clips.json color name mapped onto the current palette.

    Anything the palette does not carry falls back to `default`, which is the
    color that builder considers its own baseline.
    """
    color = (value or default).lower()
    color = _LEGACY_COLORS.get(color, color)
    return color if color in VALID_COLORS else default


def _accent_words(raw: object) -> list[str]:
    """The accent-word list of a clips.json entry, trimmed, blanks dropped."""
    return [str(w).strip() for w in (raw or []) if str(w).strip()]


def _build_callouts(clip: dict, rank: int, clip_duration: float, cfg: Config) -> list[dict]:
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
          "color": "orange"              # "orange" | "cyan" (default cyan)
        }]
    """
    raw = clip.get("callouts") or []
    if not raw:
        return []

    out: list[dict] = []
    for i, co in enumerate(raw, start=1):
        try:
            local_start = float(co["local_start"])
            duration = float(co.get("duration", 2.0))
        except (KeyError, ValueError, TypeError):
            log.warning("Clip %d callout %d: bad timestamp; skipping", rank, i)
            continue

        local_start = max(0.0, min(local_start, max(0.0, clip_duration - 0.5)))
        duration = max(0.6, min(duration, clip_duration - local_start))

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


def _build_hook(clip: dict, clip_duration: float) -> dict | None:
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

    try:
        duration = float(raw.get("duration", 2.6))
    except (TypeError, ValueError):
        log.warning("Hook duration %r is not a number; using the 2.6s default",
                    raw.get("duration"))
        duration = 2.6
    # Clamp: at least 1.5s for legibility, no more than 30% of clip
    duration = max(1.5, min(duration, max(2.0, clip_duration * 0.30)))

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
