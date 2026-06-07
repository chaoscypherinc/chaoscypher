# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Internal-behavior coverage for OllamaProvider.

Covers the previously-untested branches of
``adapters/llm/providers/ollama_provider.py``:

* ``check_health`` — 200 / non-200 / exception paths and lazy client init.
* ``_extract_thinking_from_tags`` — passthrough / extract+strip / discard
  punctuation-only / multiple blocks joined.
* ``_is_tool_calling_error`` — every known pattern and the no-tools short-circuit.
* ``_invoke_with_thinking_fallback`` — success / thinking-not-supported flip
  and retry / tool-error escalation / other ResponseError re-raise.
* ``_make_sync_request`` empty-content + tokens → thinking-as-content fallback.
* ``_stream_chat`` — content / thinking_delta / done normalization, ResponseError
  delegation to fallback, and the generic-exception error chunk.
* ``_astream_with_timeout`` — yields chunks and re-raises TimeoutError.

The ``_BASE_CONFIG`` / ``_FakeChatOllama`` / ``_make_fake_response`` helpers are
copied locally (per the campaign no-cross-import rule) from
``test_ollama_sync_path.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ollama._types import ResponseError

from chaoscypher_core.exceptions import LLMError, ToolCallingNotSupportedError


# ---------------------------------------------------------------------------
# Shared helpers (copied locally — no cross-test imports)
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


def _make_fake_response(
    *,
    content: str = "hello world",
    done_reason: str | None = "stop",
    reasoning_content: str | None = None,
    eval_count: int = 20,
) -> MagicMock:
    """Return a minimal fake LangChain AIMessage for Ollama ainvoke()."""
    response = MagicMock()
    response.content = content
    response.tool_calls = []
    response.additional_kwargs = (
        {"reasoning_content": reasoning_content} if reasoning_content else {}
    )
    response.response_metadata = {
        "prompt_eval_count": 10,
        "eval_count": eval_count,
        "done_reason": done_reason,
    }
    return response


class _FakeChatOllama(MagicMock):
    """ChatOllama stand-in whose ``ainvoke`` returns a configured response."""

    def __init__(self, **_kw: Any) -> None:
        super().__init__()
        self.ainvoke = AsyncMock(return_value=_make_fake_response())

    def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOllama:
        return self


def _make_provider() -> Any:
    """Construct an OllamaProvider with ChatOllama patched to a fake."""
    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        side_effect=_FakeChatOllama,
    ):
        from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

        return OllamaProvider(_BASE_CONFIG)


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_health_200_returns_true() -> None:
    """A 200 from /api/tags means healthy; lazy client is created."""
    provider = _make_provider()
    assert provider._health_client is None

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.httpx.AsyncClient",
        return_value=fake_client,
    ) as mk_client:
        result = await provider.check_health()

    assert result is True
    # Lazy client created exactly once with the configured timeout.
    mk_client.assert_called_once()
    assert provider._health_client is fake_client
    fake_client.get.assert_awaited_once_with("http://localhost:11434/api/tags")


@pytest.mark.asyncio
async def test_check_health_non_200_returns_false() -> None:
    """A non-200 status code means unhealthy."""
    provider = _make_provider()

    fake_resp = MagicMock()
    fake_resp.status_code = 503
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await provider.check_health()

    assert result is False


@pytest.mark.asyncio
async def test_check_health_exception_returns_false() -> None:
    """A transport exception is swallowed and reported as unhealthy."""
    provider = _make_provider()

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.httpx.AsyncClient",
        return_value=fake_client,
    ):
        result = await provider.check_health()

    assert result is False


@pytest.mark.asyncio
async def test_check_health_reuses_cached_client() -> None:
    """Once created, the health client is reused (AsyncClient not re-built)."""
    provider = _make_provider()

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.httpx.AsyncClient",
        return_value=fake_client,
    ) as mk_client:
        await provider.check_health()
        await provider.check_health()

    mk_client.assert_called_once()


# ---------------------------------------------------------------------------
# _extract_thinking_from_tags
# ---------------------------------------------------------------------------


def test_extract_thinking_no_tag_passthrough() -> None:
    """Content without <think> tags is returned unchanged with no thinking."""
    provider = _make_provider()
    cleaned, thinking = provider._extract_thinking_from_tags("plain answer")
    assert cleaned == "plain answer"
    assert thinking is None


def test_extract_thinking_empty_content_passthrough() -> None:
    """Empty content short-circuits to (content, None)."""
    provider = _make_provider()
    cleaned, thinking = provider._extract_thinking_from_tags("")
    assert cleaned == ""
    assert thinking is None


def test_extract_thinking_extracts_and_strips() -> None:
    """A real <think> block is extracted and stripped from the content."""
    provider = _make_provider()
    raw = "<think>let me reason about this</think>Final answer."
    cleaned, thinking = provider._extract_thinking_from_tags(raw)
    assert cleaned == "Final answer."
    assert thinking == "let me reason about this"


def test_extract_thinking_discards_punctuation_only() -> None:
    """Thinking that is only dots/spaces/commas is discarded as garbage."""
    provider = _make_provider()
    raw = "<think> . . , .  </think>Answer"
    cleaned, thinking = provider._extract_thinking_from_tags(raw)
    assert cleaned == "Answer"
    assert thinking is None


def test_extract_thinking_multiple_blocks_joined() -> None:
    """Multiple <think> blocks are joined by newline and all stripped."""
    provider = _make_provider()
    raw = "<think>first</think>middle<think>second</think>end"
    cleaned, thinking = provider._extract_thinking_from_tags(raw)
    assert thinking == "first\nsecond"
    # Both think blocks (and trailing whitespace) are removed from content.
    assert "<think>" not in cleaned
    assert "first" not in cleaned
    assert "second" not in cleaned
    assert "middle" in cleaned
    assert "end" in cleaned


# ---------------------------------------------------------------------------
# _is_tool_calling_error
# ---------------------------------------------------------------------------


def test_is_tool_calling_error_no_tools_short_circuits() -> None:
    """Without tools, any error is classified as not a tool-calling error."""
    provider = _make_provider()
    err = Exception("does not support tools")
    assert provider._is_tool_calling_error(err, has_tools=False) is False


@pytest.mark.parametrize(
    "message",
    [
        "model does not support tools",
        "tool calling not supported by this model",
        "function calling not supported",
        "tools are not supported here",
        "invalid tool definition",
        "unknown tool requested",
    ],
)
def test_is_tool_calling_error_each_pattern_matches(message: str) -> None:
    """Each known tool-error substring is recognized when tools are present."""
    provider = _make_provider()
    assert provider._is_tool_calling_error(Exception(message), has_tools=True) is True


def test_is_tool_calling_error_unrelated_error_false() -> None:
    """An unrelated server error with tools present is NOT a tool-calling error."""
    provider = _make_provider()
    err = Exception("unexpected end of json input")
    assert provider._is_tool_calling_error(err, has_tools=True) is False


# ---------------------------------------------------------------------------
# _invoke_with_thinking_fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_fallback_success_passthrough() -> None:
    """When ainvoke succeeds, the response is returned directly."""
    provider = _make_provider()
    expected = _make_fake_response(content="ok")
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=expected)

    result = await provider._invoke_with_thinking_fallback(
        llm, messages=[MagicMock()], enable_thinking=True
    )
    assert result is expected


@pytest.mark.asyncio
async def test_invoke_fallback_thinking_not_supported_retries_without_reasoning() -> None:
    """A 'does not support thinking' error flips the reasoning flag, rebuilds the
    LLM without reasoning, rebinds tools, and the retry succeeds.
    """
    provider = _make_provider()
    assert provider._model_supports_reasoning is True

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ResponseError("does not support thinking"))

    retry_response = _make_fake_response(content="retried")
    fallback_llm = MagicMock()
    fallback_llm.ainvoke = AsyncMock(return_value=retry_response)
    fallback_llm.bind_tools = MagicMock(return_value=fallback_llm)

    tools = [{"name": "search"}]

    with (
        patch.object(provider, "_get_cached_llm", return_value=fallback_llm) as get_cached,
        patch(
            "chaoscypher_core.adapters.llm.providers.ollama_provider.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = await provider._invoke_with_thinking_fallback(
            llm,
            messages=[MagicMock()],
            enable_thinking=True,
            has_tools=True,
            tools=tools,
        )

    assert result is retry_response
    # Reasoning support is remembered as disabled for future calls.
    assert provider._model_supports_reasoning is False
    # A reasoning-disabled LLM was requested and tools were rebound onto it.
    get_cached.assert_called_once_with(None, None, reasoning=False)
    fallback_llm.bind_tools.assert_called_once_with(tools, tool_choice="any")


@pytest.mark.asyncio
async def test_invoke_fallback_retry_tool_error_raises_tool_not_supported() -> None:
    """If the no-reasoning retry fails with a tool-calling error, escalate to
    ToolCallingNotSupportedError.
    """
    provider = _make_provider()

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ResponseError("does not support thinking"))

    fallback_llm = MagicMock()
    fallback_llm.ainvoke = AsyncMock(side_effect=ResponseError("does not support tools"))
    fallback_llm.bind_tools = MagicMock(return_value=fallback_llm)

    with (
        patch.object(provider, "_get_cached_llm", return_value=fallback_llm),
        patch(
            "chaoscypher_core.adapters.llm.providers.ollama_provider.asyncio.sleep",
            new=AsyncMock(),
        ),
        pytest.raises(ToolCallingNotSupportedError),
    ):
        await provider._invoke_with_thinking_fallback(
            llm,
            messages=[MagicMock()],
            enable_thinking=True,
            has_tools=True,
            tools=[{"name": "search"}],
        )


@pytest.mark.asyncio
async def test_invoke_fallback_retry_non_tool_error_reraised() -> None:
    """A non-tool ResponseError on retry is re-raised unchanged."""
    provider = _make_provider()

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ResponseError("does not support thinking"))

    fallback_llm = MagicMock()
    fallback_llm.ainvoke = AsyncMock(side_effect=ResponseError("internal server error"))
    fallback_llm.bind_tools = MagicMock(return_value=fallback_llm)

    with (
        patch.object(provider, "_get_cached_llm", return_value=fallback_llm),
        patch(
            "chaoscypher_core.adapters.llm.providers.ollama_provider.asyncio.sleep",
            new=AsyncMock(),
        ),
        pytest.raises(ResponseError, match="internal server error"),
    ):
        await provider._invoke_with_thinking_fallback(
            llm,
            messages=[MagicMock()],
            enable_thinking=True,
            has_tools=True,
            tools=[{"name": "search"}],
        )


@pytest.mark.asyncio
async def test_invoke_fallback_first_attempt_tool_error_raises() -> None:
    """A tool-calling error on the FIRST attempt (not a thinking error) escalates
    to ToolCallingNotSupportedError.
    """
    provider = _make_provider()

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ResponseError("does not support tools"))

    with pytest.raises(ToolCallingNotSupportedError):
        await provider._invoke_with_thinking_fallback(
            llm,
            messages=[MagicMock()],
            enable_thinking=True,
            has_tools=True,
            tools=[{"name": "search"}],
        )


@pytest.mark.asyncio
async def test_invoke_fallback_other_response_error_reraised() -> None:
    """A ResponseError that is neither a thinking nor a tool error is re-raised."""
    provider = _make_provider()

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=ResponseError("rate limited"))

    with pytest.raises(ResponseError, match="rate limited"):
        await provider._invoke_with_thinking_fallback(
            llm,
            messages=[MagicMock()],
            enable_thinking=True,
            has_tools=False,
            tools=None,
        )


# ---------------------------------------------------------------------------
# _make_sync_request — thinking-as-content fallback branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_request_uses_thinking_as_content_when_empty() -> None:
    """Empty content + non-zero completion tokens + reasoning_content present
    promotes the thinking text to content and clears the separate thinking field.
    """
    provider = _make_provider()

    response = _make_fake_response(
        content="",
        reasoning_content="this is the actual answer the model reasoned out",
        eval_count=42,
    )
    fake_llm = MagicMock()
    fake_llm.ainvoke = AsyncMock(return_value=response)

    with patch.object(provider, "_get_llm_for_request", return_value=fake_llm):
        result = await provider._make_sync_request(
            lc_messages=[MagicMock()],
            tools=None,
            enable_thinking=False,
        )

    assert result["content"] == "this is the actual answer the model reasoned out"
    assert result["thinking"] is None


# ---------------------------------------------------------------------------
# _astream_with_timeout
# ---------------------------------------------------------------------------


def _async_iter(items: list[Any]) -> Any:
    """Build an async-iterable object whose astream() yields the given items."""

    async def _gen() -> Any:
        for it in items:
            yield it

    llm = MagicMock()
    llm.astream = MagicMock(return_value=_gen())
    return llm


@pytest.mark.asyncio
async def test_astream_with_timeout_yields_all_chunks() -> None:
    """All chunks from the underlying astream are yielded through the wrapper."""
    provider = _make_provider()
    c1, c2 = MagicMock(), MagicMock()
    llm = _async_iter([c1, c2])

    collected = [chunk async for chunk in provider._astream_with_timeout(llm, [MagicMock()])]
    assert collected == [c1, c2]


@pytest.mark.asyncio
async def test_astream_with_timeout_reraises_timeout() -> None:
    """A per-chunk timeout is re-raised (does not hang)."""
    provider = _make_provider()

    class _HangingIter:
        def __aiter__(self) -> _HangingIter:
            return self

        async def __anext__(self) -> Any:
            raise TimeoutError

    llm = MagicMock()
    llm.astream = MagicMock(return_value=_HangingIter())

    with pytest.raises(TimeoutError):
        async for _ in provider._astream_with_timeout(llm, [MagicMock()], timeout=0.01):
            pass


# ---------------------------------------------------------------------------
# _stream_chat
# ---------------------------------------------------------------------------


def _stream_chunk(
    *,
    content: str = "",
    reasoning_content: str | None = None,
    done_reason: str | None = None,
) -> MagicMock:
    """Build a streaming AIMessageChunk stand-in."""
    chunk = MagicMock()
    chunk.content = content
    chunk.tool_calls = []
    chunk.additional_kwargs = {"reasoning_content": reasoning_content} if reasoning_content else {}
    chunk.usage_metadata = None
    chunk.response_metadata = (
        {"done_reason": done_reason, "eval_count": 5, "prompt_eval_count": 3} if done_reason else {}
    )
    return chunk


@pytest.mark.asyncio
async def test_stream_chat_yields_content_thinking_and_done() -> None:
    """_stream_chat emits thinking_delta, content, and a normalized done chunk."""
    provider = _make_provider()

    chunks = [
        _stream_chunk(reasoning_content="thinking hard"),
        _stream_chunk(content="Hello "),
        _stream_chunk(content="world", done_reason="stop"),
    ]
    fake_llm = _async_iter(chunks)

    with patch.object(provider, "_get_llm_for_request", return_value=fake_llm):
        events = [
            ev
            async for ev in provider._stream_chat(
                lc_messages=[MagicMock()], tools=None, enable_thinking=True
            )
        ]

    types = [ev["type"] for ev in events]
    assert "thinking_delta" in types
    assert "content" in types
    assert types[-1] == "done"

    done = events[-1]
    assert done["content"] == "Hello world"
    assert done["finish_reason"] == "stop"
    assert done["provider"] == "ollama"


@pytest.mark.asyncio
async def test_stream_chat_response_error_delegates_to_fallback() -> None:
    """A 'does not support thinking' ResponseError mid-stream delegates to
    _stream_with_fallback (whose chunks are surfaced).
    """
    provider = _make_provider()

    async def _raising_stream(*_a: Any, **_kw: Any) -> Any:
        raise ResponseError("does not support thinking")
        yield  # pragma: no cover - makes this an async generator

    async def _fallback(*_a: Any, **_kw: Any) -> Any:
        yield {"type": "content", "delta": "fb", "accumulated": "fb"}
        yield {"type": "done", "content": "fb", "provider": "ollama"}

    fake_llm = MagicMock()
    with (
        patch.object(provider, "_get_llm_for_request", return_value=fake_llm),
        patch.object(provider, "_astream_with_timeout", _raising_stream),
        patch.object(provider, "_stream_with_fallback", _fallback),
    ):
        events = [
            ev
            async for ev in provider._stream_chat(
                lc_messages=[MagicMock()], tools=None, enable_thinking=True
            )
        ]

    assert {ev["type"] for ev in events} == {"content", "done"}
    assert events[-1]["content"] == "fb"


@pytest.mark.asyncio
async def test_stream_chat_generic_error_yields_error_chunk() -> None:
    """A non-ResponseError exception mid-stream yields a single error chunk."""
    provider = _make_provider()

    async def _raising_stream(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("kaboom")
        yield  # pragma: no cover - makes this an async generator

    fake_llm = MagicMock()
    with (
        patch.object(provider, "_get_llm_for_request", return_value=fake_llm),
        patch.object(provider, "_astream_with_timeout", _raising_stream),
    ):
        events = [
            ev
            async for ev in provider._stream_chat(
                lc_messages=[MagicMock()], tools=None, enable_thinking=False
            )
        ]

    assert events == [{"type": "error", "error": "LLM streaming failed"}]


@pytest.mark.asyncio
async def test_stream_chat_other_response_error_yields_error_chunk() -> None:
    """A ResponseError that is NOT a thinking error yields the error chunk
    (no fallback delegation).
    """
    provider = _make_provider()

    async def _raising_stream(*_a: Any, **_kw: Any) -> Any:
        raise ResponseError("internal server error")
        yield  # pragma: no cover - makes this an async generator

    fake_llm = MagicMock()
    with (
        patch.object(provider, "_get_llm_for_request", return_value=fake_llm),
        patch.object(provider, "_astream_with_timeout", _raising_stream),
    ):
        events = [
            ev
            async for ev in provider._stream_chat(
                lc_messages=[MagicMock()], tools=None, enable_thinking=True
            )
        ]

    assert events == [{"type": "error", "error": "LLM streaming failed"}]


# ---------------------------------------------------------------------------
# chat() error translation (connection-error path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_connection_error_wrapped_as_llmerror() -> None:
    """A connection failure surfaces as an LLMError mentioning the Docker hint."""
    provider = _make_provider()

    with patch.object(
        provider,
        "_make_sync_request",
        new=AsyncMock(side_effect=RuntimeError("Connection refused")),
    ):
        with pytest.raises(LLMError, match="Cannot connect to Ollama"):
            await provider.chat(
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
            )
