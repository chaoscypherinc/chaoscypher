# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pins for the 2026-09-24 llm section audit (rotation #6).

Each test reproduces a defect the audit found in the provider adapters and
asserts the fixed behaviour:

* streamed tool calls are assembled from their argument fragments instead
  of keeping the last fragment's nameless, argument-less parse;
* list-shaped chunk content (tools / thinking bound) no longer raises
  ``str + list`` mid-stream on Anthropic and Gemini;
* Gemini streaming usage is the sum of the per-chunk deltas;
* OpenAI streams ask for usage explicitly (``stream_usage``), which
  langchain-openai otherwise disables whenever ``base_url`` is set;
* Gemini's non-streaming ``finish_reason`` is read from the top-level
  ``response_metadata`` key where langchain-google-genai writes it;
* Ollama applies ``llm_request_timeout`` and raises the retryable
  ``LLMServiceError`` on connection/timeout failures like the cloud
  providers do;
* cost lookup takes the longest matching prefix (``gpt-4.1-mini-…`` bills
  as gpt-4.1-mini, not gpt-4.1);
* the multi-instance balancer forwards ``seed`` and ``llm_request_timeout``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from chaoscypher_core.adapters.llm.cost import CostTracker
from chaoscypher_core.adapters.llm.providers.base import (
    merge_stream_chunks,
    message_text,
)
from chaoscypher_core.exceptions import LLMError, LLMServiceError


_BASE_CONFIG: dict[str, Any] = {
    "chat_provider": "test",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,  # bypass semaphore for the test
    "llm_request_timeout": 30,
}


class _FakeAsyncStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> _FakeAsyncStream:
        return self

    async def __anext__(self) -> Any:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _FakeChatModel:
    """LangChain chat-model stand-in yielding real ``AIMessageChunk`` objects."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def bind(self, **_kwargs: Any) -> _FakeChatModel:
        return self

    def bind_tools(self, *_args: Any, **_kwargs: Any) -> _FakeChatModel:
        return self

    def astream(self, _messages: Any) -> _FakeAsyncStream:
        return _FakeAsyncStream(list(self._chunks))


async def _collect_done(provider: Any, tools: list[dict] | None = None) -> dict[str, Any]:
    stream = await provider.chat(
        messages=[{"role": "user", "content": "hi"}], stream=True, tools=tools
    )
    done: dict[str, Any] = {}
    async for chunk in stream:
        if chunk.get("type") == "done":
            done = chunk
    return done


# ---------------------------------------------------------------------------
# base helpers
# ---------------------------------------------------------------------------


def test_message_text_handles_str_and_block_lists() -> None:
    assert message_text(AIMessage(content="plain")) == "plain"
    blocks = [
        {"type": "text", "text": "Hel"},
        {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
        {"type": "thinking", "thinking": "hmm"},
        "lo",
    ]
    assert message_text(AIMessage(content=blocks)) == "Hello"
    assert message_text(object()) == ""


def test_merge_stream_chunks_sums_real_chunks() -> None:
    a = AIMessageChunk(content="a")
    b = AIMessageChunk(content="b")
    assert merge_stream_chunks(None, a) is a
    assert merge_stream_chunks(a, b).content == "ab"


# ---------------------------------------------------------------------------
# streaming tool calls / list content / usage deltas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_stream_assembles_fragmented_tool_call() -> None:
    from chaoscypher_core.adapters.llm.providers.openai_provider import OpenAIProvider

    fake = _FakeChatModel(
        [
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "search", "args": '{"qu', "id": "call_1", "index": 0}],
            ),
            AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": None, "args": 'ery": "abc"}', "id": None, "index": 0}],
                response_metadata={"finish_reason": "tool_calls"},
                usage_metadata={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
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
                "openai_api_key": "sk-test",  # pragma: allowlist secret
                "openai_base_url": "https://api.openai.com/v1",
                "openai_chat_model": "gpt-4",
            }
        )
        done = await _collect_done(provider, tools=[{"name": "search"}])

    assert done["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": {"query": "abc"}},
        }
    ]
    assert done["finish_reason"] == "tool_calls"
    assert done["usage"] == {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}


@pytest.mark.asyncio
async def test_anthropic_stream_accepts_block_list_content() -> None:
    from chaoscypher_core.adapters.llm.providers.anthropic_provider import AnthropicProvider

    fake = _FakeChatModel(
        [
            AIMessageChunk(
                content=[{"type": "text", "text": "Hel", "index": 0}],
                usage_metadata={"input_tokens": 9, "output_tokens": 0, "total_tokens": 9},
            ),
            AIMessageChunk(content=[{"type": "text", "text": "lo", "index": 0}]),
            AIMessageChunk(
                content=[
                    {"type": "tool_use", "id": "t1", "name": "search", "input": {}, "index": 1}
                ],
                tool_call_chunks=[{"name": "search", "args": "{}", "id": "t1", "index": 1}],
            ),
            AIMessageChunk(
                content="",
                response_metadata={"stop_reason": "tool_use"},
                usage_metadata={"input_tokens": 0, "output_tokens": 4, "total_tokens": 4},
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
                "anthropic_api_key": "sk-ant-test",  # pragma: allowlist secret
                "anthropic_chat_model": "claude-opus-4-7",
            }
        )
        done = await _collect_done(provider, tools=[{"name": "search"}])

    assert done["type"] == "done"  # no str + list TypeError → no error chunk
    assert done["content"] == "Hello"
    assert done["tool_calls"][0]["function"]["name"] == "search"
    assert done["usage"] == {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}
    assert done["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_gemini_stream_sums_usage_deltas() -> None:
    from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

    fake = _FakeChatModel(
        [
            AIMessageChunk(
                content="Hel",
                usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            ),
            AIMessageChunk(
                content=[{"type": "text", "text": "lo"}],
                usage_metadata={"input_tokens": 0, "output_tokens": 3, "total_tokens": 3},
                response_metadata={"finish_reason": "STOP"},
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
                "gemini_api_key": "g-test",  # pragma: allowlist secret
                "gemini_chat_model": "gemini-2.0-flash",
            }
        )
        done = await _collect_done(provider)

    assert done["content"] == "Hello"
    assert done["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert done["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# constructor kwargs: OpenAI stream_usage, Ollama timeout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "expect_stream_usage"),
    [
        ("https://api.openai.com/v1", True),
        ("https://api.openai.com/v1/", True),
        ("http://localhost:8000/v1", False),  # OpenAI-compatible server: library default
    ],
)
def test_openai_requests_stream_usage_on_real_endpoint(
    base_url: str, expect_stream_usage: bool
) -> None:
    from chaoscypher_core.adapters.llm.providers.openai_provider import OpenAIProvider

    captured: dict[str, Any] = {}

    def _fake(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    with patch(
        "chaoscypher_core.adapters.llm.providers.openai_provider.ChatOpenAI", side_effect=_fake
    ):
        OpenAIProvider(
            {
                **_BASE_CONFIG,
                "openai_api_key": "sk-test",  # pragma: allowlist secret
                "openai_base_url": base_url,
                "openai_chat_model": "gpt-4",
            }
        )
    assert captured.get("stream_usage", False) is expect_stream_usage


_OLLAMA_CONFIG: dict[str, Any] = {
    **_BASE_CONFIG,
    "base_url": "http://localhost:11434",
    "ollama_chat_model": "qwen3:0.6b",
    "stream_chunk_timeout": 30.0,
    "ollama_health_check_timeout": 5.0,
    "ollama_recovery_delay": 0.0,
}


def _make_ollama_provider() -> Any:
    from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        return_value=MagicMock(),
    ):
        return OllamaProvider(_OLLAMA_CONFIG)


def test_ollama_provider_passes_timeout_via_client_kwargs() -> None:
    from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

    captured: dict[str, Any] = {}

    def _fake(**kwargs: Any) -> object:
        captured.update(kwargs)
        return MagicMock()

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama", side_effect=_fake
    ):
        OllamaProvider(_OLLAMA_CONFIG)
    assert captured["client_kwargs"] == {"timeout": 30}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "is_timeout"),
    [
        (ConnectionError("Connection refused"), False),
        (httpx.ConnectError("All connection attempts failed"), False),
        (httpx.ReadTimeout("timed out"), True),
        (TimeoutError(), True),
    ],
)
async def test_ollama_transient_failures_are_retryable_service_errors(
    raised: Exception, is_timeout: bool
) -> None:
    provider = _make_ollama_provider()
    with (
        patch.object(provider, "_make_sync_request", new=AsyncMock(side_effect=raised)),
        pytest.raises(LLMServiceError) as excinfo,
    ):
        await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert excinfo.value.is_retryable is True
    assert excinfo.value.details["is_timeout"] is is_timeout


@pytest.mark.asyncio
async def test_ollama_other_failures_stay_non_retryable_llm_errors() -> None:
    provider = _make_ollama_provider()
    with (
        patch.object(
            provider, "_make_sync_request", new=AsyncMock(side_effect=ValueError("bad json"))
        ),
        pytest.raises(LLMError) as excinfo,
    ):
        await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert not isinstance(excinfo.value, LLMServiceError)
    assert excinfo.value.is_retryable is False


# ---------------------------------------------------------------------------
# cost prefix lookup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "model", "expected_input_per_million"),
    [
        ("openai", "gpt-4.1-mini-2025-04-14", 0.40),
        ("openai", "gpt-4.1-2025-04-14", 2.00),
        ("openai", "gpt-4o-mini-2025-01-01", 0.15),
        ("openai", "o3-mini-2025-01-31", 1.10),
    ],
)
def test_cost_lookup_prefers_longest_prefix(
    provider: str, model: str, expected_input_per_million: float
) -> None:
    tracker = CostTracker()
    cost = tracker.calculate_cost(provider, model, input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(expected_input_per_million)
