"""Shared helpers used by every find_clips backend.

The transcript formatting, JSON parsing tolerance, and clip normalization
logic are identical regardless of which model picks the clips. Only the
"send the prompt and get a JSON response" step varies between backends.
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from slugify import slugify

from ..config import REPO_ROOT
from ..model_response import parse_json_array
from ..records import ClipCallout, ClipSpec, TranscriptWord

parse_json_response = parse_json_array

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "find_viral_clips.md"


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _load_json(path: Path, default):
    """Parsed JSON at `path`, or `default` when it is absent or unreadable.

    Every steer block below is optional: a missing, half-written or
    hand-mangled sidecar must cost the run nothing more than that block.
    """
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


_SHORT_SLUG_RE = re.compile(r"__short-\d+-(.+)$")


def covered_topics_block(limit: int = 40) -> str:
    """Build an ALREADY-COVERED TOPICS block from the scheduled ledger.

    Reads `scheduled_ledger.json` at the repo root (written by the scheduling
    tooling), pulls the topic slug out of each scheduled project key
    (`<source>__short-NN-<topic-slug>`), dedupes to the most recent `limit`
    distinct topics, and formats them as readable phrases. Returns "" when the
    ledger is absent or empty, so callers can append unconditionally.
    """
    ledger = _load_json(REPO_ROOT / "scheduled_ledger.json", {})

    # (date, topic_phrase) across every brand map in the ledger.
    seen: dict[str, str] = {}  # topic_phrase -> most-recent date
    for brand_map in ledger.values():
        if not isinstance(brand_map, dict):
            continue
        for key, entries in brand_map.items():
            m = _SHORT_SLUG_RE.search(str(key))
            if not m:
                continue
            topic = m.group(1).replace("-", " ").strip()
            if not topic:
                continue
            date = ""
            if isinstance(entries, list) and entries and isinstance(entries[0], dict):
                date = str(entries[0].get("date", ""))
            if topic not in seen or date > seen[topic]:
                seen[topic] = date

    if not seen:
        return ""
    # Most recent first, capped.
    topics = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    lines = "\n".join(f"- {t}" for t, _ in topics)
    return (
        "\n\nALREADY-COVERED TOPICS (recently published; avoid near-duplicates "
        "unless the take is materially different):\n" + lines
    )


def performance_block(limit: int = 12) -> str:
    """Inject real post-performance calibration into the clip-finding prompt.

    Reads `calibration/top_topics.json` + `calibration/weak_topics.json` (written
    by scripts/calibrate.py from Metricool analytics) and formats them as a
    steer: which past angles actually performed vs. which fell flat. Returns ""
    when no calibration exists, so this is a no-op until the feedback loop is
    seeded. This is the mechanism that turns viral_score from taste into the
    audience's revealed preference.
    """
    cal = REPO_ROOT / "calibration"

    def _load(name: str) -> list[str]:
        data = _load_json(cal / name, None)
        return [str(x) for x in data][:limit] if isinstance(data, list) else []

    top = _load("top_topics.json")
    weak = _load("weak_topics.json")
    if not top and not weak:
        return ""

    parts = ["\n\nPERFORMANCE CALIBRATION (from real published-post analytics — "
             "use as a tie-breaker, not a hard filter):"]
    if top:
        parts.append("TOP-PERFORMING past angles (this audience rewards these — "
                     "lean toward clips in this territory):")
        parts.append("\n".join(f"- {t}" for t in top))
    if weak:
        parts.append("UNDERPERFORMING past angles (these fell flat — only include "
                     "a similar clip if it is clearly stronger):")
        parts.append("\n".join(f"- {t}" for t in weak))
    return "\n".join(parts)


def format_transcript(words: Sequence[TranscriptWord]) -> str:
    """Build a readable transcript with [t=NNs] markers every 10 seconds."""
    if not words:
        return ""

    parts: list[str] = []
    last_marker = -10.0
    line_buf: list[str] = []

    for w in words:
        text = w.get("text") or w.get("word") or ""
        start = float(w["start"])
        if start - last_marker >= 10.0:
            if line_buf:
                parts.append(" ".join(line_buf))
                line_buf = []
            parts.append(f"\n[t={int(start)}s]")
            last_marker = start
        line_buf.append(text)

    if line_buf:
        parts.append(" ".join(line_buf))

    return "\n".join(parts).strip()


def transcript_with_context(words: Sequence[TranscriptWord]) -> str:
    """The transcript plus every steer block, in the order the backends send.

    Both backends feed the model the same thing; only the transport differs, so
    a block added here reaches Claude and the local endpoint together.
    """
    return format_transcript(words) + covered_topics_block() + performance_block()


_VALID_CALLOUT_STYLES = {"caption", "punch", "bigstat", "hero"}


def _normalize_callouts(raw: object) -> list[ClipCallout]:
    if not isinstance(raw, list):
        log.warning("Ignoring callouts: expected a list")
        return []
    out: list[ClipCallout] = []
    for c in raw:
        if not isinstance(c, dict):
            log.warning("Ignoring callout: expected an object")
            continue
        try:
            start = float(c["local_start"])
            duration = float(c.get("duration", 1.6))
            if not math.isfinite(start) or not math.isfinite(duration):
                raise ValueError("callout timing must be finite")
            style = str(c.get("style", "caption")).strip().lower()
            if style not in _VALID_CALLOUT_STYLES:
                style = "caption"
            out.append({
                "local_start": start,
                "duration": duration,
                "text": str(c["text"]).strip(),
                "style": style,
                "subline": str(c.get("subline", "")).strip(),
                "accent": list(c.get("accent") or []),
                "eyebrow": str(c.get("eyebrow", "")).strip(),
                "color": str(c.get("color", "gold")).strip().lower(),
            })
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("Ignoring malformed callout: %s", exc)
            continue
    return out


def _as_rank(value: Any, fallback: int) -> int:
    """A model-supplied rank, coerced to an int.

    Rank only breaks ties in the sort below and is reassigned straight after it,
    so it is never worth dropping a clip over. Left uncoerced, one string rank
    from the model made `sort` compare str to int and raise, outside the per-clip
    try, which lost every clip in the batch rather than the one bad field.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def normalize_clips(clips: list[object], words: list[TranscriptWord]) -> list[ClipSpec]:
    """Validate, deduplicate, normalize structure. Drop invalid clips."""
    max_t = float(words[-1]["end"]) if words else 0.0

    out: list[ClipSpec] = []
    for i, c in enumerate(clips):
        if not isinstance(c, dict):
            log.warning("Dropping clip %d: not a JSON object (%r)", i, c)
            continue
        try:
            start = float(c["start"])
            end = float(c["end"])
            if end <= start or end > max_t + 1.0:
                log.warning("Dropping clip %d: bad start/end (%s, %s, max=%s)",
                            i, start, end, max_t)
                continue
            duration = end - start
            if duration < 5.0 or duration > 180.0:
                log.warning("Dropping clip %d: duration %.1fs out of range", i, duration)
                continue

            segments = c.get("segments") or [[start, end]]
            valid_segs: list[list[float]] = []
            for s in segments:
                if isinstance(s, dict):
                    s = [s.get("start"), s.get("end")]
                if not s or len(s) != 2:
                    continue
                s_start, s_end = float(s[0]), float(s[1])
                if s_end > s_start:
                    valid_segs.append([s_start, s_end])
            if not valid_segs:
                valid_segs = [[start, end]]

            slug_text = c.get("slug") or c.get("hook_text") or f"clip-{i+1}"
            slug = slugify(str(slug_text))[:40] or f"clip-{i+1}"

            out.append({
                "rank": _as_rank(c.get("rank"), i + 1),
                "start": start,
                "end": end,
                "hook_start": float(c.get("hook_start", start)),
                "hook_end": float(c.get("hook_end", min(start + 10, end))),
                "hook_text": c.get("hook_text", ""),
                "viral_score": int(c.get("viral_score", 5)),
                "reasoning": c.get("reasoning", ""),
                "segments": valid_segs,
                "slug": slug,
                "instagram_caption": (c.get("instagram_caption") or "").strip(),
                "callouts": _normalize_callouts(c.get("callouts") or []),
                "hook": c.get("hook"),
            })
        except (KeyError, ValueError, TypeError) as e:
            log.warning("Dropping malformed clip %d: %s", i, e)
            continue

    out.sort(key=lambda c: (-c["viral_score"], c["rank"]))
    for i, clip in enumerate(out):
        clip["rank"] = i + 1
    return out
