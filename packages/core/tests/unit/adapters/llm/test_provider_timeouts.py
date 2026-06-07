# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests that LLM providers set a bounded request timeout.

Without a timeout, a hung upstream ties up the caller's event loop
indefinitely. The queue worker has its own asyncio.wait_for guard, but
non-queued paths (ChaosCypher.chat_sync, direct provider calls from
services) have no such guard.
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


def test_openai_provider_passes_timeout_to_chat_openai() -> None:
    from chaoscypher_core.adapters.llm.providers.openai_provider import OpenAIProvider

    captured: dict = {}

    def _fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "chaoscypher_core.adapters.llm.providers.openai_provider.ChatOpenAI",
        side_effect=_fake_chat_openai,
    ):
        OpenAIProvider(
            {
                **_BASE_CONFIG,
                "openai_api_key": "sk-test",
                "openai_base_url": "https://api.openai.com/v1",
                "openai_chat_model": "gpt-4",
            }
        )

    assert captured.get("timeout") == 300


def test_anthropic_provider_passes_timeout_to_chat_anthropic() -> None:
    from chaoscypher_core.adapters.llm.providers.anthropic_provider import (
        AnthropicProvider,
    )

    captured: dict = {}

    def _fake_chat_anthropic(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "chaoscypher_core.adapters.llm.providers.anthropic_provider.ChatAnthropic",
        side_effect=_fake_chat_anthropic,
    ):
        AnthropicProvider(
            {
                **_BASE_CONFIG,
                "anthropic_api_key": "sk-ant-test",
                "anthropic_chat_model": "claude-opus-4-7",
            }
        )

    assert captured.get("timeout") == 300


def test_gemini_provider_passes_timeout_to_chat_gemini() -> None:
    from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

    captured: dict = {}

    def _fake_chat_gemini(**kwargs):
        captured.update(kwargs)
        return object()

    with patch(
        "chaoscypher_core.adapters.llm.providers.gemini_provider.ChatGoogleGenerativeAI",
        side_effect=_fake_chat_gemini,
    ):
        GeminiProvider(
            {
                **_BASE_CONFIG,
                "gemini_api_key": "g-test",
                "gemini_chat_model": "gemini-2.0-flash",
            }
        )

    assert captured.get("timeout") == 300
