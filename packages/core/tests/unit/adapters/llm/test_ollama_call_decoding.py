# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The Ollama provider resolves a call's whole decoding when ``chat()`` is called (audit wave 6).

Until wave 6 the temperature was read in ``chat()`` and the seed when the
request was built, which for a stream is when it is first iterated: a stream
consumed after its ``decoding_override`` block closed sent the new temperature
with the configured seed. The repeat penalty (the extraction retry ladder's
second rung, audit wave 5) rides the same override and is sent only when
overridden. These tests drive the real provider with ``ChatOllama`` captured.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from chaoscypher_core.adapters.llm.providers.ollama_provider import (
    OllamaProvider,
    ollama_runtime_options_record,
)
from chaoscypher_core.utils.decoding import (
    decoding_override,
    effective_repeat_penalty,
    effective_seed,
    effective_temperature,
    seed_override,
    turn_decoding,
)


_CONFIG: dict[str, Any] = {
    "chat_provider": "ollama",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": False,
    "llm_request_timeout": 30,
    "base_url": "http://localhost:11434",
    "ollama_chat_model": "qwen3:0.6b",
    "ai_temperature": 0.0,
    "seed": 42,
    "stream_chunk_timeout": 30.0,
    "ollama_health_check_timeout": 5.0,
    "ollama_recovery_delay": 0.0,
}

_PATCH = "chaoscypher_core.adapters.llm.providers.ollama_provider.ChatOllama"


async def _chunks() -> Any:
    yield AIMessageChunk(content="an answer")


def _capturing(built: list[dict[str, Any]]) -> Any:
    """A ``ChatOllama`` stand-in that records its kwargs and answers both ways."""

    def _make(**kwargs: Any) -> MagicMock:
        built.append(kwargs)
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="an answer"))
        llm.astream = MagicMock(side_effect=lambda _messages: _chunks())
        return llm

    return _make


async def _drain(stream: Any) -> list[dict[str, Any]]:
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_a_stream_consumed_after_the_block_sends_the_pair_the_call_was_made_under() -> None:
    """Seed and temperature together, both from the moment chat() was called."""
    built: list[dict[str, Any]] = []
    decoding = turn_decoding(
        base_seed=42, configured_temperature=0.0, regenerate_temperature=0.7, attempt=2
    )
    with patch(_PATCH, side_effect=_capturing(built)):
        provider = OllamaProvider(_CONFIG)
        with decoding_override(decoding):
            stream = await provider.chat([{"role": "user", "content": "q"}], stream=True)
        # the block is closed: the configured decoding is back for any new call
        assert effective_seed(42) == 42 and effective_temperature(None) is None
        chunks = await _drain(stream)
    assert chunks[-1]["type"] == "done" and chunks[-1]["content"] == "an answer"
    assert (built[-1]["seed"], built[-1]["temperature"]) == (44, 0.7)
    assert "repeat_penalty" not in built[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_the_repeat_penalty_is_sent_when_overridden_and_never_otherwise(
    stream: bool,
) -> None:
    built: list[dict[str, Any]] = []
    messages = [{"role": "user", "content": "q"}]
    with patch(_PATCH, side_effect=_capturing(built)):
        provider = OllamaProvider(_CONFIG)
        plain = await provider.chat(messages, stream=stream)
        if stream:
            await _drain(plain)
        assert "repeat_penalty" not in built[-1]
        with decoding_override(seed=44, temperature=0.3, repeat_penalty=1.1):
            rung = await provider.chat(messages, stream=stream)
        if stream:
            await _drain(rung)
        assert (built[-1]["seed"], built[-1]["temperature"], built[-1]["repeat_penalty"]) == (
            44,
            0.3,
            1.1,
        )
        again = await provider.chat(messages, stream=stream)
        if stream:
            await _drain(again)
    # the call after the block reuses the default instance: no penalty, the configured seed
    assert all("repeat_penalty" not in kw for kw in built if kw.get("seed") == 42)
    assert sum("repeat_penalty" in kw for kw in built) == 1


def test_the_record_says_what_a_call_made_now_sends() -> None:
    """``options_sent`` reads the same per-call options the provider sends."""
    assert ollama_runtime_options_record({})["repeat_penalty"] == {"value": None, "sent": False}
    with decoding_override(repeat_penalty=1.1):
        assert ollama_runtime_options_record({})["repeat_penalty"] == {"value": 1.1, "sent": True}


def test_a_part_left_out_keeps_the_enclosing_override() -> None:
    with decoding_override(temperature=0.3, repeat_penalty=1.1):
        with seed_override(44):
            assert (effective_seed(42), effective_temperature(None)) == (44, 0.3)
            assert effective_repeat_penalty() == 1.1
        with decoding_override(repeat_penalty=None):
            assert effective_repeat_penalty() is None and effective_temperature(None) == 0.3
        assert effective_seed(42) == 42
    assert effective_repeat_penalty() is None and effective_temperature(None) is None
    decoding = turn_decoding(
        base_seed=42, configured_temperature=0.0, regenerate_temperature=0.7, attempt=1
    )
    with pytest.raises(TypeError, match="not both"), decoding_override(decoding, seed=1):
        pass
