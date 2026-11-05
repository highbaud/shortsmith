"""Plan and generate timed B-roll slides for a scaffolded short."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path

from shortsmith._files import atomic_write
from shortsmith.config import REPO_ROOT
from shortsmith.model_response import parse_json_array as _parse_json_array
from shortsmith.model_response import text_from_blocks
from shortsmith.records import TranscriptWord

from . import broll_assets, mentions, project_inputs, source_broll

PROMPT_PATH = REPO_ROOT / "prompts" / "gen_broll.md"

GAP_MARGIN = 0.2  # keep slides this far inside each free gap edge

MIN_GAP = 2.4  # ignore free gaps shorter than this (no room for a slide)


def _duration(short_dir: Path) -> float:
    meta = short_dir / "meta.json"
    if meta.exists():
        try:
            d = json.loads(meta.read_text(encoding="utf-8")).get("_shortsmith", {}).get("duration")
            if d:
                return float(d)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return project_inputs.probe_duration(project_inputs.pick_base(short_dir, "auto"))


def _free_gaps(overlays: list[dict], duration: float) -> list[tuple[float, float]]:
    """Complement of the overlay windows within [0, duration], minus margins."""
    spans = sorted(((w["start"], w["end"]) for w in overlays), key=lambda x: x[0])
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in spans:
        if a - cursor >= MIN_GAP:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if duration - cursor >= MIN_GAP:
        gaps.append((cursor, duration))
    out = []
    for a, b in gaps:
        a2, b2 = a + GAP_MARGIN, b - GAP_MARGIN
        if b2 - a2 >= MIN_GAP - 2 * GAP_MARGIN:
            out.append((round(a2, 2), round(b2, 2)))
    return out


def _gap_for(t: float, gaps: list[tuple[float, float]]) -> tuple[float, float] | None:
    for a, b in gaps:
        if a <= t <= b:
            return (a, b)
    return None


def _fit_into_gap(
    start: float, end: float, gaps: list[tuple[float, float]]
) -> tuple[float, float] | None:
    """Clamp a desired [start,end] into whichever gap contains its midpoint."""
    mid = (start + end) / 2
    g = _gap_for(mid, gaps) or _gap_for(start, gaps) or _gap_for(end, gaps)
    if not g:
        return None
    ga, gb = g
    dur = min(max(end - start, 2.0), 4.5, gb - ga)
    s = max(ga, min(start, gb - dur))
    return (round(s, 2), round(s + dur, 2))


def _load_words(short_dir: Path) -> list[TranscriptWord]:
    p = short_dir / "assets" / "words.json"
    if not p.exists():
        raise FileNotFoundError(f"No transcript at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _transcript_with_markers(words: Sequence[TranscriptWord]) -> str:
    parts: list[str] = []
    last = -10.0
    buf: list[str] = []
    for w in words:
        txt = w.get("text") or w.get("word") or ""
        st = float(w["start"])
        if st - last >= 8.0:
            if buf:
                parts.append(" ".join(buf))
                buf = []
            parts.append(f"\n[t={st:.0f}s]")
            last = st
        buf.append(txt)
    if buf:
        parts.append(" ".join(buf))
    return "\n".join(parts).strip()


def _gen_claude(words: Sequence[TranscriptWord], gaps: list[tuple[float, float]]) -> list[object]:
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    model = os.environ.get("SHORTSMITH_CLAUDE_MODEL", "claude-opus-4-7")

    gaps_str = "\n".join(f"  - [{a:.1f}, {b:.1f}]" for a, b in gaps) or "  (none)"
    user = (
        f"FREE GAPS (place slides only inside these, in seconds):\n{gaps_str}\n\n"
        f"TRANSCRIPT:\n{_transcript_with_markers(words)}"
    )
    client = anthropic.Anthropic(api_key=key)
    print(f"  calling Claude ({model}) for b-roll slides...")
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=PROMPT_PATH.read_text(encoding="utf-8"),
        messages=[{"role": "user", "content": user}],
    )
    raw = text_from_blocks(resp.content)
    return _parse_json_array(raw)


def _gen_heuristic(words: Sequence[TranscriptWord], gaps: list[tuple[float, float]], cap: int) -> list[dict]:
    # NOTE: stat slides are intentionally NOT generated — numbers/stats are left
    # to Hyperframes overlays (its bigstat callouts). The heuristic only emits
    # logo and person cutaways.
    slides: list[dict] = []
    used_gaps: set[tuple[float, float]] = set()
    text_join = " ".join((w.get("text") or w.get("word") or "") for w in words)

    def add(slide: dict, t: float, dur: float = 3.5) -> None:
        if len(slides) >= cap:
            return
        fit = _fit_into_gap(t, t + dur, gaps)
        if not fit or fit in used_gaps:
            return
        slide["start"], slide["end"] = fit
        used_gaps.add(fit)
        slides.append(slide)

    # Brands & people: first mention only.
    lower = text_join.lower()
    seen_brand: set[str] = set()
    for key, brand in mentions.KNOWN_BRANDS.items():
        if brand in seen_brand:
            continue
        m = re.search(rf"\b{re.escape(key)}\b", lower)
        if m:
            t = mentions.mention_time(words, key)
            if t is not None:
                add({"type": "logo", "brand": brand, "name": brand, "mode": "badge"}, t, dur=2.2)
                seen_brand.add(brand)

    for name, role, t in mentions.find_person_mentions(words):
        add({"type": "person", "person": name, "name": name, "role": role, "motion": "in"}, t)

    slides.sort(key=lambda s: s["start"])
    return slides


VALID_TYPES = {"text", "list", "logo", "person"}


def _normalize(slides: Sequence[object], gaps: list[tuple[float, float]]) -> list[dict]:
    out: list[dict] = []
    for s in slides:
        # The model engine hands back whatever parsed out of its JSON array, so
        # a stray string or nested list reaches here as a slide.
        if not isinstance(s, dict):
            continue
        t = s.get("type")
        if t not in VALID_TYPES:
            continue
        try:
            start, end = float(s["start"]), float(s["end"])
        except (KeyError, ValueError, TypeError):
            continue
        missing = broll_assets.missing_identity(s, t)
        if missing:
            print(f"  ! dropping {t} {start}-{end}: no {missing}")
            continue
        fit = _fit_into_gap(start, end, gaps)
        if not fit:
            print(f"  ! dropping {t} {start}-{end}: not inside any free gap")
            continue
        s = dict(s)
        s["start"], s["end"] = fit
        out.append(s)
    out.sort(key=lambda s: s["start"])
    deduped: list[dict] = []
    for s in out:
        if deduped and s["start"] < deduped[-1]["end"]:
            continue
        deduped.append(s)
    return deduped


def on_camera_names(short_dir: Path) -> list[str]:
    """People the clip spec lists as on screen (`speakers`)."""
    clip = project_inputs.clip_for(short_dir)
    listed = (clip.get("speakers") if clip else None) or []
    if not isinstance(listed, list):
        return []
    return [str(n).strip() for n in listed if str(n).strip()]


def _same_person(a: str, b: str) -> bool:
    ta = [t for t in re.split(r"[^a-z']+", a.lower()) if t]
    tb = [t for t in re.split(r"[^a-z']+", b.lower()) if t]
    return bool(ta) and bool(tb) and (ta == tb or ta[-1] == tb[-1])


def drop_on_camera_people(slides: list[dict], speakers: list[str]) -> list[dict]:
    """Drop person slides for anyone who is on camera. Cutting to a stock photo
    of the guest while the guest is talking is a mistake no editor would make."""
    if not speakers:
        return slides
    kept: list[dict] = []
    for s in slides:
        name = broll_assets.slide_identity(s, "person") if s.get("type") == "person" else ""
        if name and any(_same_person(name, sp) for sp in speakers):
            print(f"  ! {name} is on camera; dropping the person slide")
            continue
        kept.append(s)
    return kept


def generate(
    short_dir: Path,
    *,
    heuristic: bool,
    cap: int,
    dry_run: bool,
    photo_seed: int | None = None,
    fresh_photo: bool = False,
    sources: str | None = None,
) -> Path | None:
    short_dir = short_dir.resolve()
    enabled = source_broll.enabled_sources(short_dir, sources)
    words = _load_words(short_dir)
    duration = _duration(short_dir)
    overlays = project_inputs.overlay_windows(short_dir, duration)
    gaps = _free_gaps(overlays, duration)

    print(f"{short_dir.name}: {duration:.1f}s, {len(overlays)} overlays, {len(gaps)} free gaps")
    if not gaps:
        print("  no free gaps; nothing to do")
        return None

    use_claude = not heuristic and bool(os.environ.get("ANTHROPIC_API_KEY"))
    raw: Sequence[object]
    if use_claude:
        try:
            raw = _gen_claude(words, gaps)
        except Exception as e:  # noqa: BLE001 - fall back to heuristic
            print(f"  Claude failed ({e}); falling back to heuristic")
            raw = _gen_heuristic(words, gaps, cap)
    else:
        if not heuristic:
            print("  ANTHROPIC_API_KEY not set; using heuristic engine")
        raw = _gen_heuristic(words, gaps, cap)

    slides = drop_on_camera_people(_normalize(raw, gaps), on_camera_names(short_dir))[:cap]
    source_slides = []
    if enabled:
        source_slides = source_broll.generate(
            short_dir, words, gaps, sources=sources, dry_run=dry_run
        )
        slides = [
            s
            for s in slides
            if not any(
                s["start"] < extra["end"] and s["end"] > extra["start"] for extra in source_slides
            )
        ]
    slides = broll_assets.resolve_assets(
        slides, short_dir, dry_run, photo_seed=photo_seed, fresh_photo=fresh_photo
    )
    if source_slides:
        slides += source_slides
        slides.sort(key=lambda s: s["start"])

    print(f"  -> {len(slides)} slide(s):")
    for s in slides:
        extra = s.get("value") or s.get("title") or s.get("name") or ""
        print(f"     {s['start']:.1f}-{s['end']:.1f}  {s['type']:6} {extra}")

    if dry_run:
        print(json.dumps(slides, ensure_ascii=False, indent=2))
        return None

    out = short_dir / "broll.auto.json"
    atomic_write(out, json.dumps(slides, ensure_ascii=False, indent=2))
    print(f"  wrote {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate auto b-roll slides for a short.")
    source_broll.add_source_argument(ap)
    ap.add_argument("short_dir", type=Path, nargs="?", help="Path to a short-NN-<slug> folder")
    ap.add_argument("--heuristic", action="store_true", help="Force the no-API heuristic engine")
    ap.add_argument("--max", dest="cap", type=int, default=6, help="Max slides (default 6)")
    ap.add_argument(
        "--dry-run", action="store_true", help="Print proposed slides; don't download/write"
    )
    ap.add_argument(
        "--photo-seed",
        type=int,
        default=None,
        help="Seed the person-photo shuffle for reproducible picks (default: random variety)",
    )
    ap.add_argument(
        "--fresh-photo",
        action="store_true",
        help="Ignore the repo-wide assets/people cache and re-pick every person photo",
    )
    ap.add_argument(
        "--audit-people",
        nargs="*",
        metavar="NAME",
        default=None,
        help="Resolve curated people to Wikidata + photo and print the table, then exit. "
        "Pass names to audit only those.",
    )
    ap.add_argument(
        "--audit-brands",
        nargs="*",
        metavar="BRAND",
        default=None,
        help="Resolve curated brands to a logo source and print the table, then exit. "
        "Pass brands to audit only those.",
    )
    ap.add_argument(
        "--offline",
        action="store_true",
        help="Disable all network. Uses on-disk cache only; uncached URLs return nothing.",
    )
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the on-disk fetch cache. Every URL re-hits the network.",
    )
    args = ap.parse_args()
    if args.offline:
        os.environ["SHORTSMITH_BROLL_OFFLINE"] = "1"
    if args.no_cache:
        os.environ["SHORTSMITH_BROLL_NOCACHE"] = "1"
    if args.audit_people is not None:
        # Informational report. A few people (Burry, McCaleb) legitimately have
        # no free portrait, so "unusable > 0" is not a failure.
        broll_assets.audit_people(args.audit_people)
        return
    if args.audit_brands is not None:
        broll_assets.audit_brands(args.audit_brands)
        return
    if args.short_dir is None:
        ap.error("short_dir is required (or use --audit-people)")
    try:
        generate(
            args.short_dir,
            heuristic=args.heuristic,
            cap=args.cap,
            dry_run=args.dry_run,
            photo_seed=args.photo_seed,
            fresh_photo=args.fresh_photo,
            sources=args.sources,
        )
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
