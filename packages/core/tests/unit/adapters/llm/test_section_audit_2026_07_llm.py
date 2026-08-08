# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression coverage for the 2026-07-23 llm section-audit fixes.

Pins the fixed behaviors so they cannot silently regress:

* ``OllamaProvider.chat`` re-raises ``LLMError`` subclasses unchanged instead
  of re-wrapping them into a generic ``LLMError`` (which destroyed the
  fatal/no-retry classification of ``ToolCallingNotSupportedError``).
* ``convert_to_langchain_messages`` preserves assistant ``tool_calls`` (cloud
  providers reject a ToolMessage whose preceding assistant turn doesn't carry
  the matching tool_calls).
* ``LLMProvider.chat`` forwards ``temperature`` / ``max_tokens`` /
  ``high_priority`` to the underlying provider instead of silently dropping
  them (which disabled the vision output-token cap).
* Gemini/Anthropic sync paths tolerate ``AIMessage.usage_metadata is None``
  (the attribute always exists; the value is None when usage is absent).
* ``OllamaLoadBalancer.reload_config`` produces a per-instance config that the
  real ``OllamaProvider`` can consume when ``llm_enable_priority=False``
  (the missing ``chat_provider`` key crashed the warning branch).

Helpers are copied locally (per the campaign no-cross-import rule).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import ToolCallingNotSupportedError


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


# ---------------------------------------------------------------------------
# OllamaProvider.chat: LLMError pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_chat_reraises_llm_error_unwrapped() -> None:
    """An LLMError subclass raised inside chat() must surface unchanged."""
    from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

    provider = OllamaProvider(_BASE_CONFIG)
    original = ToolCallingNotSupportedError(model="qwen3:0.6b", provider="ollama")

    with (
        patch.object(provider, "_make_sync_request", AsyncMock(side_effect=original)),
        pytest.raises(ToolCallingNotSupportedError) as exc_info,
    ):
        await provider.chat(messages=[{"role": "user", "content": "hi"}], stream=False)

    assert exc_info.value is original


# ---------------------------------------------------------------------------
# convert_to_langchain_messages: assistant tool_calls preserved
# ---------------------------------------------------------------------------


def test_assistant_tool_calls_mapped_onto_aimessage() -> None:
    """Standard-format tool_calls must land on AIMessage.tool_calls."""
    from chaoscypher_core.adapters.llm.utils import convert_to_langchain_messages

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": {"query": "test"}},
                }
            ],
        },
        {"role": "tool", "content": "result", "tool_call_id": "call_1"},
    ]

    lc_messages = convert_to_langchain_messages(messages)

    assert lc_messages[0].tool_calls == [
        {"id": "call_1", "name": "search", "args": {"query": "test"}, "type": "tool_call"}
    ]
    assert lc_messages[1].tool_call_id == "call_1"


def test_assistant_tool_calls_json_string_arguments_parsed() -> None:
    """OpenAI-wire-format string arguments must be parsed into a dict."""
    from chaoscypher_core.adapters.llm.utils import convert_to_langchain_messages

    messages = [
        {
            "role": "assistant",
            "content": "x",
            "tool_calls": [{"id": "c2", "function": {"name": "f", "arguments": '{"a": 1}'}}],
        }
    ]

    (ai_msg,) = convert_to_langchain_messages(messages)
    assert ai_msg.tool_calls[0]["args"] == {"a": 1}


def test_assistant_without_tool_calls_unchanged() -> None:
    """A plain assistant message still converts with empty tool_calls."""
    from chaoscypher_core.adapters.llm.utils import convert_to_langchain_messages

    (ai_msg,) = convert_to_langchain_messages([{"role": "assistant", "content": "hi"}])
    assert ai_msg.content == "hi"
    assert ai_msg.tool_calls == []


# ---------------------------------------------------------------------------
# LLMProvider.chat: tuning kwargs forwarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_provider_forwards_temperature_and_max_tokens() -> None:
    """chat(temperature=..., max_tokens=...) must reach the concrete provider."""
    from chaoscypher_core.adapters.llm.provider import LLMProvider

    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock(
        return_value={
            "content": "ok",
            "tool_calls": None,
            "thinking": None,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )

    provider = LLMProvider.__new__(LLMProvider)
    provider.settings = SimpleNamespace(
        llm=SimpleNamespace(
            chat_provider="ollama",
            ollama_chat_model="m",
            enable_token_cost_tracking=False,
        )
    )
    provider._provider_factory = MagicMock()
    provider._provider_factory.get_chat_provider.return_value = chat_provider
    provider.managers = {}

    with patch.object(LLMProvider, "_uses_load_balancer", return_value=False):
        await provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.1,
            max_tokens=256,
            high_priority=True,
        )

    call_kwargs = chat_provider.chat.await_args.kwargs
    assert call_kwargs["temperature"] == 0.1
    assert call_kwargs["max_tokens"] == 256
    assert call_kwargs["high_priority"] is True


# ---------------------------------------------------------------------------
# Gemini / Anthropic: usage_metadata=None tolerated
# ---------------------------------------------------------------------------


def _fake_lc_response(usage_metadata: Any) -> MagicMock:
    response = MagicMock()
    response.content = "hello"
    response.tool_calls = []
    response.usage_metadata = usage_metadata
    response.response_metadata = {}
    return response


@pytest.mark.asyncio
async def test_gemini_sync_request_tolerates_none_usage_metadata() -> None:
    """usage_metadata=None (usage absent) must yield empty usage, not crash."""
    from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.chat_model = "gemini-test"
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_fake_lc_response(None))
    provider.llm = llm

    result = await provider._make_sync_request(lc_messages=[MagicMock()], tools=None)

    assert result["usage"] == {}


@pytest.mark.asyncio
async def test_gemini_sync_request_reads_standardized_usage_keys() -> None:
    """LangChain's input_tokens/output_tokens keys must be the ones read."""
    from chaoscypher_core.adapters.llm.providers.gemini_provider import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.chat_model = "gemini-test"
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=_fake_lc_response({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})
    )
    provider.llm = llm

    result = await provider._make_sync_request(lc_messages=[MagicMock()], tools=None)

    assert result["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }


@pytest.mark.asyncio
async def test_anthropic_sync_request_tolerates_none_usage_metadata() -> None:
    """Anthropic's sync path has the same None-usage guard."""
    from chaoscypher_core.adapters.llm.providers.anthropic_provider import (
        AnthropicProvider,
    )

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.chat_model = "claude-test"
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_fake_lc_response(None))
    provider.llm = llm

    result = await provider._make_sync_request(lc_messages=[MagicMock()], tools=None)

    assert result["usage"] == {}


# ---------------------------------------------------------------------------
# OllamaLoadBalancer: generated instance config feeds the real provider
# ---------------------------------------------------------------------------


def _bare_balancer() -> Any:
    from chaoscypher_core.adapters.llm.load_balancer import OllamaLoadBalancer

    bal = OllamaLoadBalancer.__new__(OllamaLoadBalancer)
    bal._instances = {}
    bal._providers = {}
    bal._semaphores = {}
    bal._strategy = "round_robin"
    bal._round_robin_index = 0
    bal._lock = asyncio.Lock()
    bal._config_version = 0
    bal._global_config = {}
    bal._drain_max_wait = 3
    bal._drain_check_interval = 0.0
    return bal


@pytest.mark.asyncio
async def test_reload_config_produces_provider_consumable_config() -> None:
    """With llm_enable_priority=False the REAL OllamaProvider must construct
    from the balancer's generated per-instance config (regression: the
    missing chat_provider key raised KeyError in reload_config).
    """
    import chaoscypher_core.adapters.llm.load_balancer as lb_mod

    bal = _bare_balancer()
    instance_data = {"id": "a", "base_url": "http://a:11434", "enabled": True, "name": "A"}
    settings = SimpleNamespace(
        ollama_instances=[SimpleNamespace(model_dump=lambda d=instance_data: dict(d))],
        ollama_chat_model="qwen3:0.6b",
        ollama_num_batch=None,
        ollama_num_ctx=None,
        ollama_num_parallel=None,
        ollama_num_thread=None,
        ai_temperature=None,
        ai_max_tokens=None,
        stream_chunk_timeout=30.0,
        ollama_health_check_timeout=5.0,
        ollama_recovery_delay=0.0,
        llm_reserved_interactive=0,
        llm_enable_priority=False,
        ollama_load_balancing="round_robin",
    )

    with (
        patch.object(lb_mod, "update_llm_semaphore_config", AsyncMock()),
        patch("chaoscypher_core.adapters.llm.providers.base.get_llm_semaphore", MagicMock()),
    ):
        await bal.reload_config(settings)

    assert bal._global_config["chat_provider"] == "ollama"
    assert "a" in bal._providers  # the real OllamaProvider constructed
