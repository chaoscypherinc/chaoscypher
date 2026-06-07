# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama sync path: max_tokens threading and finish_reason propagation.

Verifies that:
1. The max_tokens kwarg flows through to num_predict on the ChatOllama binding
   (vision handler will call chat(..., max_tokens=vision_max_output_tokens)).
2. finish_reason is present in the _make_sync_request return dict and is
   normalized to the stable vocabulary.
3. The normalized finish_reason propagates all the way up to LLMChatResponse
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
    "chat_provider": "ollama",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,
    "llm_request_timeout": 30,
    "base_url": "http://localhost:11434",
    "ollama_chat_model": "qwen3:0.6b",
    "stream_chunk_timeout": 30.0,
    "ollama_health_check_timeout": 5.0,
    "ollama_recovery_delay": 0.0,
    "ai_temperature": None,
    "ai_max_tokens": None,
}


def _make_fake_response(done_reason: str | None = "stop") -> MagicMock:
    """Return a minimal fake LangChain AIMessage for Ollama ainvoke()."""
    response = MagicMock()
    response.content = "E|Alice|Person|||\n"
    response.tool_calls = []
    response.additional_kwargs = {}
    response.response_metadata = {
        "prompt_eval_count": 10,
        "eval_count": 20,
        "done_reason": done_reason,
    }
    return response


# ---------------------------------------------------------------------------
# Test: max_tokens kwarg reaches num_predict on the ChatOllama instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_tokens_sets_num_predict() -> None:
    """max_tokens=N passed to OllamaProvider.chat() must create a ChatOllama with num_predict=N."""
    captured_kwargs: dict[str, Any] = {}

    class _FakeChatOllama(MagicMock):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            captured_kwargs.update(kwargs)
            self.ainvoke = AsyncMock(return_value=_make_fake_response())

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOllama:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        side_effect=_FakeChatOllama,
    ):
        from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider(_BASE_CONFIG)
        # Clear the cache so the call with max_tokens=128 creates a new instance
        provider._llm_cache.clear()

        await provider.chat(
            messages=[{"role": "user", "content": "describe this image"}],
            stream=False,
            max_tokens=128,
        )

    # The ChatOllama constructor called with num_predict=128
    assert captured_kwargs.get("num_predict") == 128, (
        f"Expected num_predict=128 but got captured_kwargs={captured_kwargs}"
    )


# ---------------------------------------------------------------------------
# Test: finish_reason surfaces on sync path return dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_path_returns_finish_reason_stop() -> None:
    """_make_sync_request must include normalized finish_reason='stop' when done_reason='stop'."""
    fake_response = _make_fake_response(done_reason="stop")

    class _FakeChatOllama(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOllama:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        side_effect=_FakeChatOllama,
    ):
        from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
            enable_thinking=False,
        )

    assert "finish_reason" in result
    assert result["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_sync_path_returns_finish_reason_length() -> None:
    """_make_sync_request must normalize done_reason='length' -> finish_reason='length'."""
    fake_response = _make_fake_response(done_reason="length")

    class _FakeChatOllama(MagicMock):
        def __init__(self, **_kw: Any) -> None:
            super().__init__()
            self.ainvoke = AsyncMock(return_value=fake_response)

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOllama:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        side_effect=_FakeChatOllama,
    ):
        from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider(_BASE_CONFIG)
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
            enable_thinking=False,
        )

    assert result["finish_reason"] == "length"


# ---------------------------------------------------------------------------
# Test: finish_reason propagates through LLMProvider._build_chat_response
# ---------------------------------------------------------------------------


def test_build_chat_response_propagates_finish_reason() -> None:
    """_build_chat_response must copy finish_reason from the provider dict into LLMChatResponse."""
    from chaoscypher_core.adapters.llm.provider import LLMProvider
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    settings.llm.chat_provider = "ollama"  # type: ignore[assignment]
    provider = LLMProvider(settings=settings)

    raw_response: dict[str, Any] = {
        "content": "hello",
        "tool_calls": None,
        "thinking": None,
        "usage": {},
        "finish_reason": "length",
    }

    result = provider._build_chat_response(raw_response)
    assert result.finish_reason == "length"


def test_build_chat_response_finish_reason_defaults_none() -> None:
    """When the provider dict omits finish_reason, LLMChatResponse.finish_reason is None."""
    from chaoscypher_core.adapters.llm.provider import LLMProvider
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    settings.llm.chat_provider = "ollama"  # type: ignore[assignment]
    provider = LLMProvider(settings=settings)

    raw_response: dict[str, Any] = {
        "content": "hello",
        "tool_calls": None,
        "thinking": None,
        "usage": {},
    }

    result = provider._build_chat_response(raw_response)
    assert result.finish_reason is None


# ---------------------------------------------------------------------------
# Test: enable_thinking=False must send reasoning=False to ChatOllama
# (regression: LangChain defaults to reasoning=None, which sends think=null
# and lets thinking-capable models like Qwen3 keep thinking. We must pass
# reasoning=False explicitly to actually disable thinking.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_thinking_false_passes_reasoning_false_to_chatollama() -> None:
    """enable_thinking=False must construct ChatOllama with reasoning=False, not omit it."""
    captured_kwargs_list: list[dict[str, Any]] = []

    class _FakeChatOllama(MagicMock):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            captured_kwargs_list.append(dict(kwargs))
            self.ainvoke = AsyncMock(return_value=_make_fake_response())

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOllama:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        side_effect=_FakeChatOllama,
    ):
        from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider(_BASE_CONFIG)
        await provider.chat(
            messages=[{"role": "user", "content": "extract entities"}],
            stream=False,
            enable_thinking=False,
        )

    # Find the ChatOllama instance built for the actual request (reasoning=False).
    no_reasoning_kwargs = [k for k in captured_kwargs_list if k.get("reasoning") is False]
    assert no_reasoning_kwargs, (
        "Expected at least one ChatOllama constructed with reasoning=False; "
        f"got: {captured_kwargs_list}"
    )


@pytest.mark.asyncio
async def test_enable_thinking_true_passes_reasoning_true_to_chatollama() -> None:
    """enable_thinking=True must construct ChatOllama with reasoning=True."""
    captured_kwargs_list: list[dict[str, Any]] = []

    class _FakeChatOllama(MagicMock):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            captured_kwargs_list.append(dict(kwargs))
            self.ainvoke = AsyncMock(return_value=_make_fake_response())

        def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOllama:
            return self

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        side_effect=_FakeChatOllama,
    ):
        from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider(_BASE_CONFIG)
        await provider.chat(
            messages=[{"role": "user", "content": "think it through"}],
            stream=False,
            enable_thinking=True,
        )

    reasoning_true_kwargs = [k for k in captured_kwargs_list if k.get("reasoning") is True]
    assert reasoning_true_kwargs, (
        "Expected at least one ChatOllama constructed with reasoning=True; "
        f"got: {captured_kwargs_list}"
    )
