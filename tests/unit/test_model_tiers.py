"""Tests for the OpenRouter model-tier registry and legacy key resolution."""

from __future__ import annotations

import config


def _new_config() -> config.Config:
    return config.Config(_env_file=None)


def test_registry_contains_three_openrouter_tiers() -> None:
    cfg = _new_config()
    assert set(cfg.AVAILABLE_MODELS) == {"fast", "standard", "deep"}
    for info in cfg.AVAILABLE_MODELS.values():
        assert info["provider"] == "openrouter"
        assert info["id"].count("/") == 1
        assert info["display_name"]


def test_default_model_key_is_standard() -> None:
    cfg = _new_config()
    assert cfg.DEFAULT_MODEL_KEY == "standard"
    assert cfg.DEFAULT_MODEL_KEY in cfg.AVAILABLE_MODELS


def test_resolve_model_key_passthrough_case_insensitive() -> None:
    cfg = _new_config()
    assert cfg.resolve_model_key("standard") == "standard"
    assert cfg.resolve_model_key("FAST") == "fast"
    assert cfg.resolve_model_key("Deep") == "deep"


def test_resolve_model_key_maps_legacy_keys() -> None:
    cfg = _new_config()
    expected = {
        "gemini_3_1_pro": "standard",
        "gemini_3_5_flash": "fast",
        "gemini_3_1_flash_lite": "fast",
        "anthropic_claude_opus_4_8": "deep",
        "anthropic_claude_sonnet_4_6": "standard",
        "anthropic_claude_haiku_4_5": "fast",
        "openai_gpt_5_5": "standard",
        "openai_gpt_5_5_pro": "deep",
        "openai_gpt_5_4_mini": "fast",
        "xai_grok_4_3": "standard",
        "xai_grok_4_20": "standard",
        "openrouter_glm_5_2": "standard",
        "openrouter_glm_5_1": "fast",
        "openrouter_qwen3_7_max": "standard",
        "openrouter_qwen3_7_plus": "fast",
    }
    for legacy_key, tier in expected.items():
        assert cfg.resolve_model_key(legacy_key) == tier, legacy_key


def test_resolve_model_key_rejects_unknown() -> None:
    cfg = _new_config()
    assert cfg.resolve_model_key("bogus") is None
    assert cfg.resolve_model_key("") is None
    assert cfg.resolve_model_key(None) is None


def test_openrouter_model_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_MODEL_DEEP", "x-ai/grok-4.6")
    monkeypatch.setenv("OPENROUTER_MODEL_FAST", "  ")
    cfg = _new_config()
    assert cfg.AVAILABLE_MODELS["deep"]["id"] == "x-ai/grok-4.6"
    # Blank override leaves the pinned default untouched.
    assert cfg.AVAILABLE_MODELS["fast"]["id"] != ""
