"""Step 2: Use Claude to rank viral evergreen clips from a transcript.

Sends the entire transcript with [t=NNs] timestamps every 10s, plus the
prompt from prompts/find_viral_clips.md. Parses returned JSON into a list
of clip dicts and writes to clips.json.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from slugify import slugify

from .config import Config

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "find_viral_clips.md"


def find_clips(
    words: list[dict],
    out_path: Path,
    cfg: Config,
) -> list[dict]:
    """Send transcript to Claude, parse JSON response, write clips.json."""
    import anthropic

    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    transcript_text = _format_transcript(words)
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    log.info("Calling Claude (%s) to rank clips...", cfg.claude_model)
    resp = client.messages.create(
        model=cfg.claude_model,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": transcript_text}],
    )

    raw = resp.content[0].text  # type: ignore[attr-defined]
    clips = _parse_json_response(raw)

    # Validate & normalize
    clips = _normalize(clips, words)

    # Post-filter by viral score — keep only clips at/above the threshold.
    raw_count = len(clips)
    clips = [c for c in clips if c["viral_score"] >= cfg.min_viral_score]
    log.info("Viral-score filter (>=%d): %d kept, %d dropped",
             cfg.min_viral_score, len(clips), raw_count - len(clips))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(clips, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %d clips to %s", len(clips), out_path)
    return clips


def _format_transcript(words: list[dict]) -> str:
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


def _normalize_callouts(raw: list) -> list[dict]:
    out: list[dict] = []
    for c in raw or []:
        try:
            out.append({
                "local_start": float(c["local_start"]),
                "duration": float(c.get("duration", 1.6)),
                "text": str(c["text"]).strip(),
                "accent": list(c.get("accent") or []),
                "eyebrow": str(c.get("eyebrow", "")).strip(),
                "color": str(c.get("color", "cyan")).strip().lower(),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _parse_json_response(raw: str) -> list[dict]:
    """Extract JSON from Claude's response — tolerant of stray prose / fences."""
    raw = raw.strip()
    # Strip fences if present
    if raw.startswith("```"):
        # Strip first line (```json or ```) and last fence
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    # Find the first [ and last ] to handle any trailing prose
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in Claude response:\n{raw[:500]}")
    return json.loads(raw[start : end + 1])


def _normalize(clips: list[dict], words: list[dict]) -> list[dict]:
    """Ensure clips have all required fields. Drop invalid ones."""
    if not words:
        max_t = 0.0
    else:
        max_t = float(words[-1]["end"])

    out = []
    for i, c in enumerate(clips):
        try:
            start = float(c["start"])
            end = float(c["end"])
            if end <= start or end > max_t + 1.0:
                log.warning("Dropping clip %d: bad start/end (%s, %s, max=%s)", i, start, end, max_t)
                continue
            duration = end - start
            if duration < 5.0 or duration > 180.0:
                log.warning("Dropping clip %d: duration %.1fs out of range", i, duration)
                continue
            segments = c.get("segments") or [[start, end]]
            # Validate segments
            valid_segs = []
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
                "rank": c.get("rank", i + 1),
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
            })
        except (KeyError, ValueError, TypeError) as e:
            log.warning("Dropping malformed clip %d: %s", i, e)
            continue

    out.sort(key=lambda c: (-c["viral_score"], c["rank"]))
    # Renumber rank after sort
    for i, c in enumerate(out):
        c["rank"] = i + 1
    return out
