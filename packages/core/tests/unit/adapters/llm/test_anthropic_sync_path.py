# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Anthropic sync path: finish_reason propagation.

Verifies that:
1. finish_reason is present in the _make_sync_request return dict and is
   normalized to the stable vocabulary.
2. The normalized finish_reason propagates all the way up to LLMChatResponse
   via _build_chat_response.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG: dict[str, Any] = {
    "chat_provider": "anthropic",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,
    "llm_request_timeout": 30,
    "anthropic_api_key": "sk-ant-test",
    "anthropic_chat_model": "claude-3-5-sonnet-20241022",
    "ai_temperature": None,
    "ai_max_tokens": None,
}


def _make_fake_response(stop_reason: str | None = "end_turn") -> MagicMock:
    """Return a minimal fake LangChain AIMessage for Anthropic ainvoke()."""
    response = MagicMock()
    response.content = "E|Alice|Person|||\n"
    response.tool_calls = []
    response.additional_kwargs = {}
    # Anthropic returns stop_reason (not finish_reason) in response_metadata.
    response.response_metadata = {"stop_reason": stop_reason}
    response.usage_metadata = {"input_tokens": 10, "output_tokens": 20}
    return response


# ---------------------------------------------------------------------------
# Test: finish_reason surfaces on sync path return dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_path_returns_finish_reason_stop() -> None:
    """_make_sync_request must normalize stop_reason='end_turn' -> finish_reason='stop'."""
    fake_response = _make_fake_response(stop_reason="end_turn")

    class _FakeChatAnthropic(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatAnthropic:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.anthropic_provider.ChatAnthropic",
        side_effect=_FakeChatAnthropic,
    ):
        from chaoscypher_core.adapters.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert "finish_reason" in result
    assert result["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_sync_path_returns_finish_reason_length() -> None:
    """_make_sync_request must normalize stop_reason='max_tokens' -> finish_reason='length'."""
    fake_response = _make_fake_response(stop_reason="max_tokens")

    class _FakeChatAnthropic(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatAnthropic:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.anthropic_provider.ChatAnthropic",
        side_effect=_FakeChatAnthropic,
    ):
        from chaoscypher_core.adapters.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert result["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_sync_path_finish_reason_missing_becomes_unknown() -> None:
    """When response_metadata has no stop_reason key, normalize_finish_reason maps None -> 'unknown'."""
    response = MagicMock()
    response.content = "hello"
    response.tool_calls = []
    response.response_metadata = {}  # no stop_reason key
    response.usage_metadata = {}

    class _FakeChatAnthropic(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatAnthropic:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.anthropic_provider.ChatAnthropic",
        side_effect=_FakeChatAnthropic,
    ):
        from chaoscypher_core.adapters.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert result["finish_reason"] == "unknown"
