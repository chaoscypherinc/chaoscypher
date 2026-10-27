# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pins for the 2026-10-19 llm section audit (rotation #6, second pass).

Each test reproduces a defect the audit found in the provider adapters and
asserts the fixed behaviour:

* a non-streamed Ollama answer whose ``done`` message omits a token count
  (Ollama serialises it as ``None``) no longer crashes on ``None + int`` —
  every provider's sync path reads usage through the one shared reader;
* an Ollama stream with tools reports ``ToolCallingNotSupportedError`` like
  the non-streamed path, not a plain ``LLM_ERROR``;
* a failure inside the thinking-fallback stream is classified and yielded as
  an error chunk instead of escaping the generator raw;
* ``OllamaProvider.close()`` closes the lazily built health-check client;
* the extraction metrics mapper reads a classified Core error's class before
  guessing from its text.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage
from ollama._types import ResponseError

from chaoscypher_core.adapters.llm.error_types import map_exception_to_error_type
from chaoscypher_core.adapters.llm.providers.base import extract_streaming_usage
from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider
from chaoscypher_core.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMServiceError,
    ToolCallingNotSupportedError,
)
from chaoscypher_core.utils.stream_errors import error_from_stream_chunk


_CONFIG: dict[str, Any] = {
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

_TOOLS = [{"type": "function", "function": {"name": "f", "parameters": {}}}]


def _provider() -> OllamaProvider:
    with patch(
        "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama",
        return_value=MagicMock(),
    ):
        return OllamaProvider(_CONFIG)


# ---------------------------------------------------------------------------
# usage: one reader, None-safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_sync_usage_survives_an_omitted_count() -> None:
    """``prompt_eval_count: None`` (Ollama's omitempty) used to raise ``None + int``."""
    response = MagicMock()
    response.content = "hello"
    response.tool_calls = []
    response.additional_kwargs = {}
    response.usage_metadata = None
    response.response_metadata = {"prompt_eval_count": None, "eval_count": 5, "done_reason": "stop"}
    provider = _provider()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=response)
    llm.bind_tools = MagicMock(return_value=llm)
    with patch.object(provider, "_get_llm_for_request", return_value=llm):
        result = await provider.chat(messages=[{"role": "user", "content": "hi"}], stream=False)
    assert result["usage"] == {"prompt_tokens": 0, "completion_tokens": 5, "total_tokens": 5}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            AIMessage(
                content="x",
                usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
            ),
            {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        ),
        (
            AIMessage(
                content="x",
                response_metadata={
                    "token_usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
                },
            ),
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        ),
        (
            AIMessage(content="x", response_metadata={"prompt_eval_count": None, "eval_count": 9}),
            {"prompt_tokens": 0, "completion_tokens": 9, "total_tokens": 9},
        ),
        (AIMessage(content="x"), {}),
    ],
)
def test_the_shared_usage_reader_covers_every_sync_shape(
    message: AIMessage, expected: dict
) -> None:
    """The four providers' hand-written sync readers were folded onto this one (rule 3)."""
    assert extract_streaming_usage(message) == expected


# ---------------------------------------------------------------------------
# streaming verdicts
# ---------------------------------------------------------------------------


async def _stream_after(
    provider: OllamaProvider, raised: Exception, tools: list[dict] | None
) -> list[dict[str, Any]]:
    async def _raising(*_a: Any, **_kw: Any) -> Any:
        raise raised
        yield  # pragma: no cover - makes this an async generator

    with (
        patch.object(provider, "_get_llm_for_request", return_value=MagicMock()),
        patch.object(provider, "_astream_with_timeout", _raising),
    ):
        return [
            chunk
            async for chunk in provider._stream_chat(
                lc_messages=[MagicMock()], tools=tools, enable_thinking=False
            )
        ]


@pytest.mark.asyncio
async def test_a_tools_stream_reports_the_capability_error() -> None:
    chunks = await _stream_after(
        _provider(), ResponseError("registry.ollama.ai/x does not support tools", 400), _TOOLS
    )
    assert [c["type"] for c in chunks] == ["error"]
    error = error_from_stream_chunk(chunks[0])
    assert isinstance(error, ToolCallingNotSupportedError)
    assert error.is_retryable is False


@pytest.mark.asyncio
async def test_a_failure_inside_the_thinking_fallback_is_an_error_chunk() -> None:
    provider = _provider()

    async def _fallback_dies(*_a: Any, **_kw: Any) -> Any:
        raise httpx.ConnectError("All connection attempts failed")
        yield  # pragma: no cover

    with patch.object(provider, "_stream_with_fallback", _fallback_dies):
        chunks = await _stream_after(
            provider, ResponseError("qwen3 does not support thinking", 400), None
        )
    assert [c["type"] for c in chunks] == ["error"]
    error = error_from_stream_chunk(chunks[0])
    assert isinstance(error, LLMServiceError)
    assert error.is_retryable is True


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_releases_the_health_client() -> None:
    provider = _provider()
    client = MagicMock()
    client.aclose = AsyncMock()
    provider._health_client = client
    await provider.close()
    client.aclose.assert_awaited_once()
    assert provider._health_client is None
    await provider.close()  # idempotent


# ---------------------------------------------------------------------------
# metrics error types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            LLMServiceError(provider="ollama", reason="Cannot connect to Ollama at http://x:11434"),
            "connection_error",
        ),
        (LLMServiceError(provider="ollama", reason="slow", is_timeout=True), "timeout_error"),
        (LLMRateLimitError(provider="openai"), "rate_limit_error"),
        (ToolCallingNotSupportedError(model="m", provider="ollama"), "capability_error"),
        (ConnectionError("connection refused"), "connection_error"),
    ],
)
def test_metrics_read_the_core_class_before_the_text(error: Exception, expected: str) -> None:
    assert map_exception_to_error_type(error) == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        # the server answered: a busy 503 or a crashed runner is no connection failure
        (
            LLMServiceError(provider="ollama", reason="busy", details={"status_code": 503}),
            "provider_error",
        ),
        (
            LLMServiceError(provider="ollama", reason="runner died", details={"retry_limit": 1}),
            "provider_error",
        ),
        (LLMServiceError(provider="openai", reason="Unable to connect"), "connection_error"),
    ],
)
def test_metrics_label_a_server_answer_apart_from_a_dropped_connection(
    error: Exception, expected: str
) -> None:
    """Every ``LLMServiceError`` was ``connection_error`` (2026-10-26, #824 review)."""
    assert map_exception_to_error_type(error) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("schema mismatch after 14290 attempts", "provider_error"),
        ("http 429: slow down", "rate_limit_error"),
    ],
)
def test_metrics_read_429_only_as_a_whole_number(message: str, expected: str) -> None:
    """``"429" in e`` labelled any text holding those digits a rate limit."""
    assert map_exception_to_error_type(RuntimeError(message)) == expected


# ---------------------------------------------------------------------------
# balancer: the priority flag rebuilds providers
# ---------------------------------------------------------------------------


def test_the_priority_flag_is_part_of_the_provider_fingerprint() -> None:
    """Every provider stores ``llm_enable_priority``; a flip must rebuild them, not share one."""
    from chaoscypher_core.adapters.llm.load_balancer import (
        _POOL_SIZING_KEYS,
        provider_config_fingerprint,
    )

    assert "llm_enable_priority" not in _POOL_SIZING_KEYS
    on = {"ollama_chat_model": "m", "llm_enable_priority": True, "llm_max_concurrent": 2}
    off = {**on, "llm_enable_priority": False}
    assert provider_config_fingerprint(on) != provider_config_fingerprint(off)
    # the pool-sizing keys still do not
    assert provider_config_fingerprint(on) == provider_config_fingerprint(
        {**on, "llm_max_concurrent": 9}
    )


@pytest.mark.asyncio
async def test_an_empty_pool_raises_the_classified_error() -> None:
    import asyncio

    from chaoscypher_core.adapters.llm.load_balancer import OllamaLoadBalancer

    bal = OllamaLoadBalancer.__new__(OllamaLoadBalancer)
    bal._instances = {}
    bal._providers = {}
    bal._semaphores = {}
    bal._lock = asyncio.Lock()
    with pytest.raises(LLMError) as excinfo:
        async with bal.acquire_instance():
            pass  # pragma: no cover
    assert type(excinfo.value) is LLMError  # classified, not a bare RuntimeError
    assert excinfo.value.is_retryable is True


# ---------------------------------------------------------------------------
# cost: the registry prices what it lists
# ---------------------------------------------------------------------------


def _registry_models() -> list[tuple[str, str, float, float]]:
    from chaoscypher_core.adapters.llm.model_registry import get_model_registry

    registry = get_model_registry()
    rows = []
    for provider in registry.get_providers():
        for model in registry.get_models(provider):
            pricing = model.get("pricing") or {}
            if "input_per_million" in pricing:
                rows.append(
                    (
                        provider,
                        model["id"],
                        float(pricing["input_per_million"]),
                        float(pricing["output_per_million"]),
                    )
                )
    assert rows, "the cloud registry lists no priced model"
    return rows


@pytest.mark.parametrize(("provider", "model", "price_in", "price_out"), _registry_models())
def test_every_registry_model_bills_at_its_registry_price(
    provider: str, model: str, price_in: float, price_out: float
) -> None:
    """cost.py billed opus-4-6/4-7 as opus-4 and every gpt-5.x at $0 while two tables were kept by hand."""
    from chaoscypher_core.adapters.llm.cost import CostTracker

    tracker = CostTracker(custom_input_cost=0.0, custom_output_cost=0.0)
    assert tracker.calculate_cost(provider, model, 1_000_000, 0) == pytest.approx(price_in)
    assert tracker.calculate_cost(provider, model, 0, 1_000_000) == pytest.approx(price_out)


# ---------------------------------------------------------------------------
# extractor: truncated JSON is closed in nesting order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # single line, cut inside the second item's string value
        (
            '{"entities": [{"name": "A", "type": "X"}, {"name": "B',
            {"entities": [{"name": "A", "type": "X"}]},
        ),
        # cut after a complete item: `[{` closes as `]}`, not `}]`
        ('{"entities": [{"name": "A", "type": "X"}', {"entities": [{"name": "A", "type": "X"}]}),
        # three levels, cut mid number
        ('{"a": {"b": [{"c": 1}, {"d": 12', {"a": {"b": [{"c": 1}]}}),
        # a brace inside a string is text
        ('{"a": "x{y", "b": [1, 2', {"a": "x{y", "b": [1, 2]}),
        # a key with no value yet
        ('{"a": 1, "b"', {"a": 1}),
    ],
)
def test_truncated_json_closes_every_opener_innermost_first(text: str, expected: object) -> None:
    import json

    from chaoscypher_core.adapters.llm.schema.extractor import StructuredExtractor

    repaired, truncated = StructuredExtractor(MagicMock())._repair_json(text)
    assert truncated is True
    assert json.loads(repaired) == expected


# ---------------------------------------------------------------------------
# embedding cache: the build follows the model name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_embedding_cache_keys_a_switched_model_on_its_own_build(tmp_path: Any) -> None:
    from chaoscypher_core.cache.embedding import CachedEmbeddingProvider
    from chaoscypher_core.cache.keys import model_build_id
    from chaoscypher_core.cache.sqlite_store import SqliteCacheStore

    inner = MagicMock()
    inner.model_name = "qwen3-embedding:4b"
    type(inner).provider_type = property(lambda _self: "ollama")
    cached = CachedEmbeddingProvider(
        inner, SqliteCacheStore(tmp_path / "e.db", max_bytes=1 << 20), model_build="ollama:4b:sha"
    )
    assert await cached._model_build() == "ollama:4b:sha"
    cached.model_name = "nomic-embed-text"
    assert inner.model_name == "nomic-embed-text"
    assert await cached._model_build() == model_build_id(
        "ollama", "nomic-embed-text", digest=None, server_version=None
    )


# ---------------------------------------------------------------------------
# vectors: every provider logs the cut, once
# ---------------------------------------------------------------------------


def test_the_mrl_cut_is_logged_once_per_model_for_any_provider() -> None:
    from structlog.testing import capture_logs

    from chaoscypher_core.adapters.embedding import vectors

    vectors._TRUNCATION_LOGGED.discard(("local", "big"))
    with capture_logs() as logs:
        for _ in range(3):
            out = vectors.fit_provider_vector(
                [1.0, 2.0, 3.0, 4.0], 2, provider="local", model="big"
            )
    assert len(out) == 2
    cuts = [log for log in logs if log["event"] == "embedding_mrl_truncation"]
    assert len(cuts) == 1
    assert (cuts[0]["native_dimensions"], cuts[0]["vector_dimensions"]) == (4, 2)
