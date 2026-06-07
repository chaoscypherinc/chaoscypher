# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Gemini sync path: finish_reason propagation.

Verifies that:
1. finish_reason is present in the _make_sync_request return dict and is
   normalized to the stable vocabulary.
2. Gemini's uppercase enum tokens (STOP, MAX_TOKENS) round-trip through
   normalize_finish_reason to the project's stable vocabulary.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG: dict[str, Any] = {
    "chat_provider": "gemini",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,
    "llm_request_timeout": 30,
    "gemini_api_key": "AIza-test",
    "gemini_chat_model": "gemini-1.5-pro",
    "ai_temperature": None,
    "ai_max_tokens": None,
}


def _make_fake_response(finish_reason: str | None = "STOP") -> MagicMock:
    """Return a minimal fake LangChain AIMessage for Gemini ainvoke().

    Gemini surfaces finish_reason under response_metadata["candidates"][0]["finish_reason"]
    as uppercase enum strings (STOP, MAX_TOKENS, SAFETY, …).
    """
    response = MagicMock()
    response.content = "E|Alice|Person|||\n"
    response.tool_calls = []
    response.additional_kwargs = {}
    # Gemini nests finish_reason under candidates[0]
    if finish_reason is not None:
        response.response_metadata = {"candidates": [{"finish_reason": finish_reason}]}
    else:
        response.response_metadata = {}
    response.usage_metadata = {
        "prompt_token_count": 10,
        "candidates_token_count": 20,
        "total_token_count": 30,
    }
    return response


# ---------------------------------------------------------------------------
# Test: finish_reason surfaces on sync path return dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_path_returns_finish_reason_stop() -> None:
    """_make_sync_request must normalize candidates[0].finish_reason='STOP' -> finish_reason='stop'."""
    fake_response = _make_fake_response(finish_reason="STOP")

    class _FakeChatGoogleGenerativeAI(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatGoogleGenerativeAI:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.gemini_provider.ChatGoogleGenerativeAI",
        side_effect=_FakeChatGoogleGenerativeAI,
    ):
        from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

        provider = GeminiProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert "finish_reason" in result
    assert result["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_sync_path_returns_finish_reason_length() -> None:
    """_make_sync_request must normalize candidates[0].finish_reason='MAX_TOKENS' -> finish_reason='length'."""
    fake_response = _make_fake_response(finish_reason="MAX_TOKENS")

    class _FakeChatGoogleGenerativeAI(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatGoogleGenerativeAI:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.gemini_provider.ChatGoogleGenerativeAI",
        side_effect=_FakeChatGoogleGenerativeAI,
    ):
        from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

        provider = GeminiProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert result["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_sync_path_finish_reason_missing_becomes_unknown() -> None:
    """When response_metadata has no candidates key, normalize_finish_reason maps None -> 'unknown'."""
    fake_response = _make_fake_response(finish_reason=None)

    class _FakeChatGoogleGenerativeAI(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatGoogleGenerativeAI:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.gemini_provider.ChatGoogleGenerativeAI",
        side_effect=_FakeChatGoogleGenerativeAI,
    ):
        from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

        provider = GeminiProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
        )

    assert result["finish_reason"] == "unknown"
