"""Parse JSON arrays returned by transcript and b-roll models."""
from __future__ import annotations

import json


def text_from_blocks(content: object) -> str:
    """Read text blocks while leaving tool and other response blocks alone."""
    if not isinstance(content, list):
        raise ValueError("Model response content must be a list of blocks")
    parts = []
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            raise ValueError("Model response text block has no text string")
        parts.append(text)
    result = "\n".join(parts)
    if not result.strip():
        raise ValueError("Model response has no text block")
    return result


def parse_json_array(raw: str) -> list[object]:
    """Read a JSON array with optional surrounding prose or Markdown fences.

    Elements are returned unchecked; callers drop entries that are not valid items.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in model response:\n{raw[:500]}")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError(f"Model response is not a JSON array:\n{raw[:500]}")
    return parsed
