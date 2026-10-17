"""Story checks must use the configured provider and fail on incomplete output."""
from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from shortsmith.config import Config
from shortsmith.find_clips import anthropic as claude_backend
from shortsmith.find_clips import ollama

REVIEWS = [{"clip_id": 1, "decision": "pass"}]
REVIEWS_JSON = json.dumps(REVIEWS)
WORDS = [{"word": "A complete thought.", "start": 0.0, "end": 8.0}]


@pytest.fixture
def cfg():
    return Config(
        anthropic_api_key="test-key",
        claude_model="test-claude",
        local_llm_url="http://localhost:11434/v1/",
        local_llm_model="test-local",
        local_llm_temperature=0.3,
        clip_max_tokens=1234,
    )


def claude_response(text=REVIEWS_JSON, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
    )


def mock_claude(monkeypatch, response=None, error=None):
    create = Mock(return_value=response, side_effect=error)
    client = Mock()
    client.messages.create = create
    factory = Mock(return_value=client)
    monkeypatch.setattr("anthropic.Anthropic", factory)
    return factory, create


def mock_local(monkeypatch, response, error=None):
    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        if error is not None:
            raise error
        return io.BytesIO(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr(ollama.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ollama.time, "sleep", lambda _: None)
    return requests


def local_response(content=REVIEWS_JSON, finish_reason="stop"):
    return {"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]}


def test_claude_review_uses_separate_prompt_and_config(monkeypatch, cfg):
    response = claude_response()
    response.content = [
        SimpleNamespace(type="thinking", text="do not parse this"),
        SimpleNamespace(type="text", text="```json\n["),
        SimpleNamespace(type="text", text='{"clip_id": 1, "decision": "pass"}]\n```'),
    ]
    factory, create = mock_claude(monkeypatch, response)

    assert claude_backend.review("Review the story.", "Exact source words", cfg) == REVIEWS
    factory.assert_called_once_with(api_key="test-key")
    create.assert_called_once_with(
        model="test-claude",
        max_tokens=1234,
        system="Review the story.",
        messages=[{"role": "user", "content": "Exact source words"}],
    )


@pytest.mark.parametrize("reason", ["max_tokens", "tool_use", "refusal", "pause_turn", None])
def test_claude_review_rejects_unfinished_response(monkeypatch, cfg, reason):
    mock_claude(monkeypatch, claude_response(stop_reason=reason))
    with pytest.raises(RuntimeError, match="did not finish"):
        claude_backend.review("Review", "Words", cfg)


@pytest.mark.parametrize("text", ["", "[]", "{}", "[{}", "no review"])
def test_claude_review_rejects_empty_or_bad_json(monkeypatch, cfg, text):
    mock_claude(monkeypatch, claude_response(text))
    with pytest.raises(RuntimeError, match="Story check"):
        claude_backend.review("Review", "Words", cfg)


def test_claude_review_rejects_missing_content(monkeypatch, cfg):
    mock_claude(monkeypatch, SimpleNamespace(stop_reason="end_turn"))
    with pytest.raises(RuntimeError, match="invalid JSON"):
        claude_backend.review("Review", "Words", cfg)


def test_claude_review_wraps_api_failure(monkeypatch, cfg):
    mock_claude(monkeypatch, error=OSError("offline"))
    with pytest.raises(RuntimeError, match="Claude request failed"):
        claude_backend.review("Review", "Words", cfg)


def test_claude_review_missing_key_does_not_call_sdk(monkeypatch, cfg):
    factory, _ = mock_claude(monkeypatch)
    cfg.anthropic_api_key = ""
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        claude_backend.review("Review", "Words", cfg)
    factory.assert_not_called()


def test_claude_clip_picking_call_still_accepts_original_response(monkeypatch, cfg):
    _, create = mock_claude(monkeypatch, claude_response())
    assert claude_backend.call(WORDS, cfg) == REVIEWS
    sent = create.call_args.kwargs
    assert sent["model"] == cfg.claude_model
    assert sent["max_tokens"] == cfg.clip_max_tokens
    assert "A complete thought." in sent["messages"][0]["content"]


def test_local_review_forwards_prompt_model_and_output_limit(monkeypatch, cfg):
    requests = mock_local(monkeypatch, local_response("```json\n" + json.dumps(REVIEWS) + "\n```"))
    assert ollama.review("Review the story.", "Exact source words", cfg) == REVIEWS
    assert len(requests) == 1
    request, _ = requests[0]
    assert request.full_url == "http://localhost:11434/v1/chat/completions"
    body = json.loads(request.data)
    assert body["model"] == cfg.local_llm_model
    assert body["max_tokens"] == cfg.clip_max_tokens
    assert body["temperature"] == cfg.local_llm_temperature
    assert body["stream"] is False
    assert body["messages"][0] == {"role": "system", "content": "Review the story."}
    assert body["messages"][1]["content"].startswith("Exact source words\n\n")


@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls", None])
def test_local_review_rejects_unfinished_response(monkeypatch, cfg, reason):
    requests = mock_local(monkeypatch, local_response(finish_reason=reason))
    with pytest.raises(RuntimeError, match="did not finish"):
        ollama.review("Review", "Words", cfg)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        local_response(""),
        local_response(None),
        local_response("[]"),
        local_response("[{}"),
        local_response("{}"),
        {},
        {"choices": []},
        {"choices": ["bad completion"]},
        {"choices": [{"finish_reason": "stop"}]},
    ],
)
def test_local_review_rejects_empty_bad_or_incomplete_response(monkeypatch, cfg, response):
    requests = mock_local(monkeypatch, response)
    with pytest.raises(RuntimeError, match="no usable review"):
        ollama.review("Review", "Words", cfg)
    assert len(requests) == ollama.DEFAULT_MAX_RETRIES


def test_local_review_wraps_network_failure(monkeypatch, cfg):
    mock_local(monkeypatch, None, error=urllib.error.URLError("offline"))
    with pytest.raises(RuntimeError, match="failed to reach"):
        ollama.review("Review", "Words", cfg)


def test_local_clip_picking_keeps_original_api_without_finish_reason(monkeypatch, cfg):
    requests = mock_local(monkeypatch, {"choices": [{"message": {"content": json.dumps(REVIEWS)}}]})
    assert ollama.call(WORDS, cfg) == REVIEWS
    body = json.loads(requests[0][0].data)
    assert body["model"] == cfg.local_llm_model
    assert "A complete thought." in body["messages"][1]["content"]
