"""Each run reads environment settings when its Config is created."""
from __future__ import annotations

import pytest

from shortsmith.config import Config


@pytest.mark.parametrize(("setting", "field", "raw", "expected"), [
    ("WHISPER_MODEL", "whisper_model", "small", "small"),
    ("WHISPER_DEVICE", "whisper_device", "cpu", "cpu"),
    ("WHISPER_COMPUTE", "whisper_compute_type", "int8", "int8"),
    ("WHISPER_PROMPT", "whisper_initial_prompt", "Speaker names", "Speaker names"),
    ("CLIP_ENGINE", "clip_engine", "ollama", "ollama"),
    ("CLAUDE_MODEL", "claude_model", "selected-model", "selected-model"),
    ("CLIP_MAX_TOKENS", "clip_max_tokens", "4096", 4096),
    ("LOCAL_LLM_URL", "local_llm_url", "http://localhost:1234/v1", "http://localhost:1234/v1"),
    ("LOCAL_LLM_MODEL", "local_llm_model", "local-model", "local-model"),
    ("LOCAL_LLM_TEMP", "local_llm_temperature", "0.2", 0.2),
    ("MIN_SCORE", "min_viral_score", "8", 8),
    ("LUFS", "loudness_target_lufs", "-16.0", -16.0),
    ("ALIGN", "align_engine", "faster-whisper", "faster-whisper"),
    ("SFX_SEMANTIC", "sfx_semantic_mode", "every", "every"),
    ("VFX", "vfx_enabled", "off", False),
    ("VFX_INTENSITY", "vfx_intensity", "0.5", 0.5),
    ("ENHANCE", "enhance_engine", "deepfilter", "deepfilter"),
    ("PUNCH", "punch_interrupt_enabled", "off", False),
    ("PUNCH_EVERY", "punch_interrupt_every", "12.0", 12.0),
    ("REFRAME_CUTAWARE", "reframe_cut_aware", "on", True),
    ("SCENE_THRESHOLD", "reframe_scene_threshold", "0.4", 0.4),
    ("REFRAME_LAYOUT", "reframe_layout", "SPLIT-STACK", "split-stack"),
    ("LAYOUT_PRESET", "split_stack_preset", "alternate", "alternate"),
    ("STYLE", "style", "minimal", "minimal"),
])
def test_environment_is_read_after_module_import(
    monkeypatch: pytest.MonkeyPatch, setting: str, field: str, raw: str, expected: object,
) -> None:
    before = Config()
    previous = getattr(before, field)
    monkeypatch.setenv("SHORTSMITH_" + setting, raw)

    assert getattr(Config(), field) == expected
    assert getattr(before, field) == previous


def test_explicit_settings_take_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTSMITH_CLAUDE_MODEL", "environment-model")
    assert Config(claude_model="explicit-model").claude_model == "explicit-model"


def test_config_debug_text_does_not_include_api_credentials() -> None:
    assert "test-secret-value" not in repr(Config(anthropic_api_key="test-secret-value"))
