# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cloud per-provider max-output-token caps actually bind (Phase 5).

The ``*_max_output_tokens`` settings rendered as sliders in the UI but
were never read — every provider used the generic ``ai_max_tokens``
alone. The effective request cap is now ``min(ai_max_tokens,
<provider>_max_output_tokens)`` with either knob alone applying as-is.
"""

from __future__ import annotations

from unittest.mock import patch


_BASE_CONFIG: dict = {
    "chat_provider": "test",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": True,
    "llm_request_timeout": 300,
}


def _build_anthropic(extra: dict) -> dict:
    from chaoscypher_core.adapters.llm.providers.anthropic_provider import (
        AnthropicProvider,
    )

    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "chaoscypher_core.adapters.llm.providers.anthropic_provider.ChatAnthropic",
        side_effect=_fake,
    ):
        AnthropicProvider(
            {
                **_BASE_CONFIG,
                "anthropic_api_key": "sk-test",
                "anthropic_chat_model": "claude-fable-5",
                **extra,
            }
        )
    return captured


def test_anthropic_provider_cap_bounds_generic_max() -> None:
    captured = _build_anthropic({"ai_max_tokens": 65536, "anthropic_max_output_tokens": 64000})
    assert captured.get("max_tokens") == 64000


def test_anthropic_cap_above_generic_keeps_generic() -> None:
    captured = _build_anthropic({"ai_max_tokens": 32000, "anthropic_max_output_tokens": 64000})
    assert captured.get("max_tokens") == 32000


def test_anthropic_cap_alone_applies() -> None:
    captured = _build_anthropic({"anthropic_max_output_tokens": 8192})
    assert captured.get("max_tokens") == 8192


def test_anthropic_neither_knob_means_no_limit() -> None:
    captured = _build_anthropic({})
    assert "max_tokens" not in captured


def test_openai_provider_cap_bounds_generic_max() -> None:
    from chaoscypher_core.adapters.llm.providers.openai_provider import OpenAIProvider

    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "chaoscypher_core.adapters.llm.providers.openai_provider.ChatOpenAI",
        side_effect=_fake,
    ):
        OpenAIProvider(
            {
                **_BASE_CONFIG,
                "openai_api_key": "sk-test",
                "openai_base_url": "https://api.openai.com/v1",
                "openai_chat_model": "gpt-4",
                "ai_max_tokens": 65536,
                "openai_max_output_tokens": 32768,
            }
        )

    assert captured.get("max_tokens") == 32768


def test_gemini_provider_cap_bounds_generic_max() -> None:
    from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "chaoscypher_core.adapters.llm.providers.gemini_provider.ChatGoogleGenerativeAI",
        side_effect=_fake,
    ):
        GeminiProvider(
            {
                **_BASE_CONFIG,
                "gemini_api_key": "test-key",
                "gemini_chat_model": "gemini-2.5-pro",
                "ai_max_tokens": 99999,
                "gemini_max_output_tokens": 65536,
            }
        )

    assert captured.get("max_output_tokens") == 65536
