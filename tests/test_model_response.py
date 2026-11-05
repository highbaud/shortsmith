"""Model JSON parsing stays consistent across clip and b-roll backends."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shortsmith.model_response import parse_json_array, text_from_blocks


@pytest.mark.parametrize("raw", [
    '[{"text": "clip", "segments": [1, 2]}]',
    '```json\n[{"text": "clip", "segments": [1, 2]}]\n```',
    'The result follows.\n[{"text": "clip", "segments": [1, 2]}]\nDone.',
])
def test_model_array_accepts_existing_wrappers(raw: str) -> None:
    assert parse_json_array(raw) == [{"text": "clip", "segments": [1, 2]}]


@pytest.mark.parametrize("raw", ["", "```", "No picks."])
def test_missing_array_has_a_clear_error(raw: str) -> None:
    with pytest.raises(ValueError, match="No JSON array found"):
        parse_json_array(raw)


def test_invalid_array_is_rejected() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json_array('[{"text": broken}]')


def test_response_reads_only_text_blocks() -> None:
    assert text_from_blocks([
        SimpleNamespace(type="tool_use", text="not model text"),
        SimpleNamespace(type="text", text="first"),
        SimpleNamespace(type="text", text="second"),
    ]) == "first\nsecond"


@pytest.mark.parametrize("content", [
    None, "[]", [], [SimpleNamespace(type="tool_use")],
    [SimpleNamespace(type="text", text=42)], [SimpleNamespace(type="text", text=" ")],
])
def test_response_rejects_missing_or_invalid_text(content: object) -> None:
    with pytest.raises(ValueError, match="Model response"):
        text_from_blocks(content)


@pytest.mark.parametrize("backend", ["clips", "broll"])
def test_anthropic_clients_accept_text_after_a_nontext_block(
    backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shortsmith.config import Config
    from shortsmith.find_clips import _common, anthropic
    from shortsmith.rendering import gen_broll

    response = SimpleNamespace(stop_reason="end_turn", content=[
        SimpleNamespace(type="tool_use"), SimpleNamespace(type="text", text="[]"),
    ])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: response))
    monkeypatch.setattr("anthropic.Anthropic", lambda **kwargs: client)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Return JSON.", encoding="utf-8")
    monkeypatch.setattr(_common, "PROMPT_PATH", prompt)
    monkeypatch.setattr(gen_broll, "PROMPT_PATH", prompt)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = (anthropic.select_clips([], Config(anthropic_api_key="test-key"))
              if backend == "clips" else gen_broll._gen_claude([], []))
    assert result == []


def test_array_elements_are_returned_unchecked_for_callers_to_filter() -> None:
    assert parse_json_array('[{"text": "clip"}, 1, "stray"]') == [{"text": "clip"}, 1, "stray"]
