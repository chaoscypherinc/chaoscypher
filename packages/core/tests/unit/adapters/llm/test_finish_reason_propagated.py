# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every built-in LLM provider includes finish_reason on the done chunk.

Each provider's raw value (OpenAI ``length``, Anthropic ``max_tokens``,
Ollama ``length``, Gemini ``MAX_TOKENS``) normalizes to a stable
vocabulary so downstream observability code never needs to know which
provider produced the stream.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


_BASE_CONFIG: dict[str, Any] = {
    "chat_provider": "test",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,  # bypass semaphore for the test
    "llm_request_timeout": 30,
}

_VALID_FINISH_REASONS = {
    "length",
    "stop",
    "content_filter",
    "tool_calls",
    "error",
    "unknown",
}


class _FakeStreamChunk:
    """Minimal AIMessageChunk stand-in for streaming tests."""

    def __init__(
        self,
        content: str = "",
        response_metadata: dict[str, Any] | None = None,
        usage_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.response_metadata = response_metadata or {}
        self.usage_metadata = usage_metadata
        self.tool_calls: list[Any] = []
        self.additional_kwargs: dict[str, Any] = {}


class _FakeAsyncStream:
    """Yields the supplied chunks via async-iterator protocol."""

    def __init__(self, chunks: list[_FakeStreamChunk]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> _FakeAsyncStream:
        return self

    async def __anext__(self) -> _FakeStreamChunk:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _FakeChatModel:
    """LangChain BaseChatModel stand-in producing a fixed stream."""

    def __init__(self, chunks: list[_FakeStreamChunk]) -> None:
        self._chunks = chunks

    def bind(self, **_kwargs: Any) -> _FakeChatModel:
        return self

    def bind_tools(self, *_args: Any, **_kwargs: Any) -> _FakeChatModel:
        return self

    def astream(self, _messages: Any) -> _FakeAsyncStream:
        return _FakeAsyncStream(list(self._chunks))


async def _collect_done(provider: Any) -> dict[str, Any]:
    """Run a streaming chat and return the done-chunk dict."""
    stream = await provider.chat(messages=[{"role": "user", "content": "hi"}], stream=True)
    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
    return next(c for c in chunks if c.get("type") == "done")


@pytest.mark.asyncio
async def test_openai_done_chunk_carries_finish_reason() -> None:
    from chaoscypher_core.adapters.llm.providers.openai_provider import OpenAIProvider

    fake = _FakeChatModel(
        [
            _FakeStreamChunk(content="E|Alice|Person|||\n"),
            _FakeStreamChunk(
                content="",
                response_metadata={"finish_reason": "length"},
            ),
        ]
    )
    with patch(
        "chaoscypher_core.adapters.llm.providers.openai_provider.ChatOpenAI",
        return_value=fake,
    ):
        provider = OpenAIProvider(
            {
                **_BASE_CONFIG,
                "openai_api_key": "sk-test",
                "openai_base_url": "https://api.openai.com/v1",
                "openai_chat_model": "gpt-4",
            }
        )
        done = await _collect_done(provider)
    assert "finish_reason" in done
    assert done["finish_reason"] in _VALID_FINISH_REASONS
    assert done["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_anthropic_done_chunk_carries_finish_reason() -> None:
    from chaoscypher_core.adapters.llm.providers.anthropic_provider import (
        AnthropicProvider,
    )

    fake = _FakeChatModel(
        [
            _FakeStreamChunk(content="E|Alice|Person|||\n"),
            _FakeStreamChunk(
                content="",
                response_metadata={"stop_reason": "max_tokens"},
            ),
        ]
    )
    with patch(
        "chaoscypher_core.adapters.llm.providers.anthropic_provider.ChatAnthropic",
        return_value=fake,
    ):
        provider = AnthropicProvider(
            {
                **_BASE_CONFIG,
                "anthropic_api_key": "sk-ant-test",
                "anthropic_chat_model": "claude-opus-4-7",
            }
        )
        done = await _collect_done(provider)
    assert "finish_reason" in done
    assert done["finish_reason"] in _VALID_FINISH_REASONS
    assert done["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_ollama_done_chunk_carries_finish_reason() -> None:
    from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

    fake = _FakeChatModel(
        [
            _FakeStreamChunk(content="E|Alice|Person|||\n"),
            _FakeStreamChunk(
                content="",
                response_metadata={"done_reason": "length"},
            ),
        ]
    )
    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        return_value=fake,
    ):
        provider = OllamaProvider(
            {
                **_BASE_CONFIG,
                "base_url": "http://localhost:11434",
                "ollama_chat_model": "qwen3:0.6b",
                "stream_chunk_timeout": 30.0,
                "ollama_health_check_timeout": 5.0,
                "ollama_recovery_delay": 0.0,
            }
        )
        done = await _collect_done(provider)
    assert "finish_reason" in done
    assert done["finish_reason"] in _VALID_FINISH_REASONS
    assert done["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_gemini_done_chunk_carries_finish_reason() -> None:
    from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

    fake = _FakeChatModel(
        [
            _FakeStreamChunk(content="E|Alice|Person|||\n"),
            _FakeStreamChunk(
                content="",
                response_metadata={
                    "candidates": [{"finish_reason": "MAX_TOKENS"}],
                },
            ),
        ]
    )
    with patch(
        "chaoscypher_core.adapters.llm.providers.gemini_provider.ChatGoogleGenerativeAI",
        return_value=fake,
    ):
        provider = GeminiProvider(
            {
                **_BASE_CONFIG,
                "gemini_api_key": "g-test",
                "gemini_chat_model": "gemini-2.0-flash",
            }
        )
        done = await _collect_done(provider)
    assert "finish_reason" in done
    assert done["finish_reason"] in _VALID_FINISH_REASONS
    assert done["finish_reason"] == "length"


def test_normalize_finish_reason_maps_known_aliases() -> None:
    from chaoscypher_core.adapters.llm.providers.base import normalize_finish_reason

    # OpenAI
    assert normalize_finish_reason("stop") == "stop"
    assert normalize_finish_reason("length") == "length"
    assert normalize_finish_reason("tool_calls") == "tool_calls"
    assert normalize_finish_reason("content_filter") == "content_filter"
    # Anthropic
    assert normalize_finish_reason("max_tokens") == "length"
    assert normalize_finish_reason("end_turn") == "stop"
    assert normalize_finish_reason("tool_use") == "tool_calls"
    # Gemini (uppercase enum tokens)
    assert normalize_finish_reason("MAX_TOKENS") == "length"
    assert normalize_finish_reason("STOP") == "stop"
    assert normalize_finish_reason("SAFETY") == "content_filter"
    assert normalize_finish_reason("RECITATION") == "content_filter"
    # Defensive defaults
    assert normalize_finish_reason(None) == "unknown"
    assert normalize_finish_reason("totally-not-a-reason") == "unknown"
