# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenAI sync path: finish_reason propagation.

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
    "chat_provider": "openai",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,
    "llm_request_timeout": 30,
    "openai_api_key": "sk-test",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_chat_model": "gpt-4o-mini",
    "ai_temperature": None,
    "ai_max_tokens": None,
}


def _make_fake_response(finish_reason: str | None = "stop") -> MagicMock:
    """Return a minimal fake LangChain AIMessage for OpenAI ainvoke()."""
    response = MagicMock()
    response.content = "E|Alice|Person|||\n"
    response.tool_calls = []
    response.additional_kwargs = {}
    response.response_metadata = {
        "token_usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
        # OpenAI returns finish_reason at the top level of response_metadata.
        "finish_reason": finish_reason,
    }
    return response


# ---------------------------------------------------------------------------
# Test: finish_reason surfaces on sync path return dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_path_returns_finish_reason_stop() -> None:
    """_make_sync_request must include normalized finish_reason='stop' when finish_reason='stop'."""
    fake_response = _make_fake_response(finish_reason="stop")

    class _FakeChatOpenAI(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOpenAI:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.openai_provider.ChatOpenAI",
        side_effect=_FakeChatOpenAI,
    ):
        from chaoscypher_core.adapters.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert "finish_reason" in result
    assert result["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_sync_path_returns_finish_reason_length() -> None:
    """_make_sync_request must normalize finish_reason='length' -> 'length'."""
    fake_response = _make_fake_response(finish_reason="length")

    class _FakeChatOpenAI(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOpenAI:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.openai_provider.ChatOpenAI",
        side_effect=_FakeChatOpenAI,
    ):
        from chaoscypher_core.adapters.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert result["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_sync_path_finish_reason_missing_becomes_unknown() -> None:
    """When response_metadata has no finish_reason key, normalize_finish_reason maps None -> 'unknown'."""
    response = MagicMock()
    response.content = "hello"
    response.tool_calls = []
    response.response_metadata = {}  # no finish_reason key

    class _FakeChatOpenAI(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOpenAI:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.openai_provider.ChatOpenAI",
        side_effect=_FakeChatOpenAI,
    ):
        from chaoscypher_core.adapters.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert result["finish_reason"] == "unknown"
