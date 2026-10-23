# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The repetition guard: a looping stream is stopped, and the loop's tail trimmed."""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.streaming.chat.loop import consume_llm_stream, guarded_chat
from chaoscypher_core.streaming.chat.repetition import (
    RepetitionGuard,
    normalise_line,
    trim_repeats,
)
from chaoscypher_core.streaming.chat.sinks import CollectingSink


def test_normalise_line_drops_list_markers_and_folds_whitespace() -> None:
    assert normalise_line("    *   Okay.  ") == "Okay."
    assert normalise_line("3)  Okay,   I'll answer") == "Okay, I'll answer"


def test_the_guard_trips_on_a_line_repeated_in_a_row() -> None:
    """Qwen3.5 9B's '*   Okay.' run (2026-10-04), fed as the model streamed it: a few chars at a time."""
    guard = RepetitionGuard(max_consecutive=8, max_occurrences=25)
    text = "Let me think about this.\n" + "    *   Okay.\n" * 7
    for i in range(0, len(text), 3):
        assert not guard.feed(text[i : i + 3]), "seven repeats are not yet a loop"
    assert guard.feed("    *   Okay.\n") is True
    assert guard.tripped and "8 times in a row" in guard.reason
    assert guard.feed("anything after") is True


def test_the_guard_trips_on_a_line_seen_too_often_in_all() -> None:
    guard = RepetitionGuard(max_consecutive=8, max_occurrences=5)
    for _ in range(4):
        assert not guard.feed("I will answer: X.\nSome other line here.\n")
    assert guard.feed("I will answer: X.\n") is True
    assert "seen 5 times" in guard.reason


def test_short_lines_never_count() -> None:
    guard = RepetitionGuard(max_consecutive=3, max_occurrences=3)
    assert not guard.feed("-\n-\n-\n-\n* \n* \n* \n")


def test_trim_repeats_cuts_the_repeated_tail_and_keeps_the_answer_before_it() -> None:
    text = "Borís is the officer.\nNicholas is the student.\n" + "*   Okay.\n" * 12
    assert trim_repeats(text) == "Borís is the officer.\nNicholas is the student."
    # no run at the end: untouched
    plain = "Borís is the officer.\nNicholas is the student."
    assert trim_repeats(plain) == plain


async def _stream(*chunks: dict[str, Any]) -> Any:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_consume_stops_a_looping_answer_and_trims_it() -> None:
    sink = CollectingSink()
    acc = ""
    chunks = []
    for piece in ["Sónya is the niece.\n", *["*   Okay.\n"] * 9]:
        acc += piece
        chunks.append({"type": "content", "delta": piece, "accumulated": acc})
    chunks.append({"type": "done", "content": acc, "finish_reason": "stop"})
    guard = RepetitionGuard(max_consecutive=8)
    content, _, _, err, done = await consume_llm_stream(_stream(*chunks), sink, "c1", guard)
    assert not err
    assert content == "Sónya is the niece."
    assert done is not None and done["finish_reason"] == "loop" and done["loop_phase"] == "content"
    # the sink saw the answer, never the loop's lines past the trip
    assert len(sink.of_type("content")) == 8


@pytest.mark.asyncio
async def test_consume_stops_a_looping_thinking_phase() -> None:
    sink = CollectingSink()
    thinking = ""
    chunks = []
    for _ in range(9):
        thinking += "Okay, let me check the graph again.\n"
        chunks.append({"type": "thinking_delta", "accumulated": thinking})
    chunks.append({"type": "content", "delta": "never", "accumulated": "never"})
    content, _, _, _, done = await consume_llm_stream(
        _stream(*chunks), sink, "c1", RepetitionGuard(max_consecutive=8)
    )
    assert content == "" and done is not None and done["loop_phase"] == "thinking"
    assert sink.of_type("content") == []


class _Provider:
    """Loops in the thinking phase with thinking on; answers plainly with it off."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def chat(self, *, messages: Any, tools: Any, stream: bool, enable_thinking: bool) -> Any:
        self.calls.append(enable_thinking)
        if enable_thinking:
            thinking = ""
            chunks = []
            for _ in range(9):
                thinking += "Hmm, let me look again.\n"
                chunks.append({"type": "thinking_delta", "accumulated": thinking})
            return _stream(*chunks)
        return _stream(
            {"type": "content", "delta": "Borís.", "accumulated": "Borís."},
            {"type": "done", "content": "Borís.", "finish_reason": "stop"},
        )


@pytest.mark.asyncio
async def test_guarded_chat_retries_a_thinking_loop_once_without_thinking() -> None:
    provider = _Provider()
    content, _, _, _, done = await guarded_chat(
        provider,
        [{"role": "user", "content": "q"}],
        sink=CollectingSink(),
        chat_id="c1",
        settings=None,
        enable_thinking=True,
    )
    assert provider.calls == [True, False]
    assert content == "Borís." and done is not None and done["loop_retried"] is True
    assert done["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_guarded_chat_does_not_retry_when_thinking_was_already_off() -> None:
    provider = _Provider()
    await guarded_chat(
        provider,
        [{"role": "user", "content": "q"}],
        sink=CollectingSink(),
        chat_id="c1",
        settings=None,
        enable_thinking=False,
    )
    assert provider.calls == [False]


class _WrappingProvider(_Provider):
    """LLMProvider's shape: the stream inside an object with ``is_stream`` and ``stream``."""

    async def chat(self, **kw: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(is_stream=True, stream=await super().chat(**kw), content="")


@pytest.mark.asyncio
async def test_guarded_chat_accepts_a_wrapped_stream_response() -> None:
    """The benchmark's LLMProvider wraps the generator; the first live check errored on it."""
    provider = _WrappingProvider()
    content, _, _, err, done = await guarded_chat(
        provider,
        [{"role": "user", "content": "q"}],
        sink=CollectingSink(),
        chat_id="c1",
        settings=None,
        enable_thinking=True,
    )
    assert not err and content == "Borís." and provider.calls == [True, False]


class _ContentLoopProvider:
    """Loops in the answer text with thinking on (optionally opening with <think>); plain off."""

    def __init__(self, opening: str) -> None:
        self.opening = opening
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if kw["enable_thinking"]:
            acc = ""
            chunks = []
            for piece in [self.opening, *["Let me look at the context again.\n"] * 9]:
                acc += piece
                chunks.append({"type": "content", "delta": piece, "accumulated": acc})
            return _stream(*chunks)
        return _stream(
            {"type": "content", "delta": "Borís.", "accumulated": "Borís."},
            {"type": "done", "content": "Borís.", "finish_reason": "stop"},
        )


async def _guarded(provider: Any, **kw: Any) -> Any:
    return await guarded_chat(
        provider,
        [{"role": "user", "content": "q"}],
        sink=CollectingSink(),
        chat_id="c1",
        settings=None,
        enable_thinking=True,
        **kw,
    )


@pytest.mark.asyncio
async def test_a_content_loop_that_opens_with_think_takes_the_thinking_retry() -> None:
    """Models whose reasoning lands in content got finish_reason=loop and no retry (H10)."""
    provider = _ContentLoopProvider("<think>\n")
    content, _, _, _, done = await _guarded(provider)
    assert [c["enable_thinking"] for c in provider.calls] == [True, False]
    assert content == "Borís." and done is not None
    assert done["loop_retried"] is True and done["thinking_retried"] is True
    first = done["first_loop"]
    assert first["phase"] == "content" and "in a row" in first["reason"]
    assert first["thinking_chars"] > 0  # the reasoning before the trip, kept for an audit


@pytest.mark.asyncio
async def test_a_loop_in_a_plain_answer_is_kept_trimmed_without_a_retry() -> None:
    provider = _ContentLoopProvider("Borís is the officer.\n")
    content, _, _, _, done = await _guarded(provider)
    assert len(provider.calls) == 1
    assert content == "Borís is the officer." and done is not None
    assert done["finish_reason"] == "loop" and done["loop_reason"]
    assert done["thinking_chars"] == 0 and "loop_retried" not in done


@pytest.mark.asyncio
async def test_a_model_known_to_stream_its_thinking_as_content_retries_a_content_loop() -> None:
    provider = _ContentLoopProvider("Okay, so the question asks about Borís.\n")
    content, _, _, _, done = await _guarded(provider, reasoning_as_content=True)
    assert [c["enable_thinking"] for c in provider.calls] == [True, False]
    assert content == "Borís." and done is not None and done["thinking_retried"] is True


@pytest.mark.asyncio
async def test_the_output_cap_reaches_both_calls_and_none_sends_no_cap() -> None:
    """The benchmark caps num_predict at the window the prompt leaves (H9)."""
    provider = _ContentLoopProvider("<think>\n")
    await _guarded(provider, max_tokens=4096)
    assert [c["max_tokens"] for c in provider.calls] == [4096, 4096]
    uncapped = _ContentLoopProvider("<think>\n")
    await _guarded(uncapped)
    assert all("max_tokens" not in c for c in uncapped.calls)
