# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the AI Prompt plugin.

Covers the pure module-level helpers (chunk_text, merge_json_results) and
the PromptPlugin internal helpers (_extract_prompt_parts, _extract_json_from_text,
_parse_llm_output, _merge_chunk_results) plus the async ``execute`` orchestration
(no-LLM error path, single-prompt path, and chunking path).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.services.workflows.tools.plugins.ai_prompt_plugin import (
    INSTRUCTION_MARKERS,
    PromptPlugin,
    chunk_text,
    merge_json_results,
)


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------
class TestChunkText:
    """Pure chunking helper — no mocks."""

    def test_short_text_single_chunk(self) -> None:
        text = "hello world"
        # max_tokens * 4 = 40 chars >> len(text)
        assert chunk_text(text, max_tokens=10) == [text]

    def test_paragraph_split_over_max(self) -> None:
        # max_tokens=1 → max_chars=4, so each paragraph exceeds the cap.
        para_a = "aaaaaaaa"
        para_b = "bbbbbbbb"
        text = f"{para_a}\n\n{para_b}"
        chunks = chunk_text(text, max_tokens=1)
        assert len(chunks) == 2
        assert chunks[0] == para_a
        assert chunks[1] == para_b

    def test_accumulates_paragraphs_under_max(self) -> None:
        # Two small paragraphs fit together once max is generous, but the
        # whole text exceeds max so we still enter the split branch.
        text = "p1\n\n" + "x" * 100
        chunks = chunk_text(text, max_tokens=10)  # max_chars=40
        # "p1" accumulates, then the long paragraph flushes it.
        assert chunks[0] == "p1"
        assert "x" * 100 in chunks[1]

    def test_oversized_paragraph_emitted_as_own_chunk(self) -> None:
        # A paragraph larger than max_chars becomes its own chunk after the
        # accumulator (whatever preceded it) is flushed.
        body = "x" * 100
        text = f"p1\n\n{body}"
        chunks = chunk_text(text, max_tokens=10)  # max_chars=40
        assert chunks[0] == "p1"
        assert body in chunks[-1]


# ---------------------------------------------------------------------------
# merge_json_results
# ---------------------------------------------------------------------------
class TestMergeJsonResults:
    """Pure JSON merge helper — no mocks."""

    def test_empty_returns_empty_dict(self) -> None:
        assert merge_json_results([]) == {}

    def test_single_returns_first(self) -> None:
        one = {"entities": [1, 2]}
        assert merge_json_results([one]) is one

    def test_list_values_extend(self) -> None:
        merged = merge_json_results([{"entities": [1, 2]}, {"entities": [3]}])
        assert merged["entities"] == [1, 2, 3]

    def test_dict_values_update(self) -> None:
        merged = merge_json_results([{"meta": {"a": 1}}, {"meta": {"b": 2}}])
        assert merged["meta"] == {"a": 1, "b": 2}

    def test_scalar_first_wins(self) -> None:
        merged = merge_json_results([{"summary": "first"}, {"summary": "second"}])
        assert merged["summary"] == "first"


# ---------------------------------------------------------------------------
# _extract_prompt_parts
# ---------------------------------------------------------------------------
class TestExtractPromptParts:
    """Content-marker splitting for chunking."""

    def setup_method(self) -> None:
        self.plugin = PromptPlugin()

    def test_none_without_content_marker(self) -> None:
        assert self.plugin._extract_prompt_parts("no marker here") is None

    def test_split_via_instruction_markers(self) -> None:
        prompt = "Prefix\nContent:\nTHE BODY\n\nExtract entities now"
        parts = self.plugin._extract_prompt_parts(prompt)
        assert parts is not None
        prefix, content, suffix = parts
        assert prefix.endswith("Content:\n")
        assert content == "THE BODY"
        assert suffix.startswith("\n\nExtract")
        # The chosen marker is one of the configured instruction markers.
        assert any(suffix.startswith(m) for m in INSTRUCTION_MARKERS)

    def test_no_instruction_marker_runs_to_end(self) -> None:
        prompt = "Content:\nbody text with no instruction"
        parts = self.plugin._extract_prompt_parts(prompt)
        assert parts is not None
        _prefix, content, suffix = parts
        assert content == "body text with no instruction"
        assert suffix == ""


# ---------------------------------------------------------------------------
# _extract_json_from_text
# ---------------------------------------------------------------------------
class TestExtractJsonFromText:
    """JSON extraction from raw / fenced / failing text."""

    def setup_method(self) -> None:
        self.plugin = PromptPlugin()

    def test_raw_json(self) -> None:
        assert self.plugin._extract_json_from_text('{"a": 1}') == {"a": 1}

    def test_json_fence(self) -> None:
        text = 'prelude ```json\n{"a": 2}\n``` trailer'
        assert self.plugin._extract_json_from_text(text) == {"a": 2}

    def test_plain_fence(self) -> None:
        text = 'noise ```\n{"b": 3}\n``` more'
        assert self.plugin._extract_json_from_text(text) == {"b": 3}

    def test_failure_returns_text(self) -> None:
        text = "no json at all here"
        assert self.plugin._extract_json_from_text(text) == text


# ---------------------------------------------------------------------------
# _parse_llm_output
# ---------------------------------------------------------------------------
class TestParseLlmOutput:
    """Parsing the LLM result dict into text or JSON."""

    def setup_method(self) -> None:
        self.plugin = PromptPlugin()

    def test_text_passthrough(self) -> None:
        result = {"content": "plain text answer"}
        assert self.plugin._parse_llm_output(result, "text") == "plain text answer"

    def test_json_parsed(self) -> None:
        result = {"content": '{"entities": [1]}'}
        parsed = self.plugin._parse_llm_output(result, "json")
        assert parsed == {"entities": [1]}

    def test_thinking_fallback_when_entities_empty(self) -> None:
        # Main content parses to a dict but has no entities → look at thinking.
        result = {
            "content": '{"entities": []}',
            "thinking": '{"entities": [{"name": "X"}]}',
        }
        parsed = self.plugin._parse_llm_output(result, "json")
        assert parsed == {"entities": [{"name": "X"}]}

    def test_no_thinking_returns_empty_dict(self) -> None:
        # Empty dict, no thinking section → return the (empty) output as-is.
        result = {"content": "{}"}
        parsed = self.plugin._parse_llm_output(result, "json")
        assert parsed == {}


# ---------------------------------------------------------------------------
# _merge_chunk_results
# ---------------------------------------------------------------------------
class TestMergeChunkResults:
    """Merging per-chunk results, sorting by index, attaching _metadata."""

    def setup_method(self) -> None:
        self.plugin = PromptPlugin()

    def test_json_merge_with_metadata_and_sort(self) -> None:
        # Provided out of order to confirm sort-by-index.
        chunk_results = [
            (1, {"entities": ["b"]}, "model-2"),
            (0, {"entities": ["a"]}, "model-1"),
        ]
        merged = self.plugin._merge_chunk_results(
            chunk_results, output_format="json", chunk_strategy="full", chunk_count=2
        )
        # Sorted, so entities are a then b.
        assert merged["entities"] == ["a", "b"]
        assert merged["_metadata"]["model"] == "model-2"  # last after sort
        assert merged["_metadata"]["chunks_processed"] == 2
        assert merged["_metadata"]["chunk_strategy"] == "full"

    def test_text_join(self) -> None:
        chunk_results = [
            (0, "first", "model-1"),
            (1, "second", "model-1"),
        ]
        merged = self.plugin._merge_chunk_results(
            chunk_results, output_format="text", chunk_strategy="quick", chunk_count=2
        )
        assert merged["result"] == "first\n\n---\n\nsecond"
        assert merged["_metadata"]["chunks_processed"] == 2

    def test_empty_results_unknown_model(self) -> None:
        merged = self.plugin._merge_chunk_results(
            [], output_format="text", chunk_strategy="quick", chunk_count=0
        )
        assert merged["_metadata"]["model"] == "unknown"


# ---------------------------------------------------------------------------
# Helpers for the async execute tests
# ---------------------------------------------------------------------------
def _make_llm_service(
    *, content: str = '{"entities": []}', model: str = "test-model", tokens: int = 42
) -> AsyncMock:
    """Build an AsyncMock LLM service mimicking the queue interface."""
    llm = AsyncMock()
    llm.queue_operation = AsyncMock(return_value="task-123")
    llm.wait_for_result = AsyncMock(
        return_value={"content": content, "model": model, "tokens_used": tokens}
    )
    # settings.timeouts.llm_chat_wait is read synchronously.
    llm.settings = MagicMock()
    llm.settings.timeouts.llm_chat_wait = 30
    return llm


def _make_context(llm_service: Any | None) -> MagicMock:
    """Build a MagicMock ToolExecutionContext."""
    ctx = MagicMock()
    ctx.llm_service = llm_service
    ctx.thinking_mode = "quick"
    ctx.settings = MagicMock()
    ctx.settings.llm.ai_temperature = 0.7
    ctx.settings.llm.ai_max_tokens = 2048
    ctx.settings.chunking.small_chunk_overlap = 50
    return ctx


# ---------------------------------------------------------------------------
# execute — no LLM service
# ---------------------------------------------------------------------------
class TestExecuteNoLlm:
    @pytest.mark.asyncio
    async def test_raises_operation_error_without_llm(self) -> None:
        plugin = PromptPlugin()
        ctx = _make_context(llm_service=None)

        with pytest.raises(OperationError) as exc_info:
            await plugin.execute({"prompt": "hi"}, ctx)

        assert exc_info.value.operation == "ai.prompt"


# ---------------------------------------------------------------------------
# execute — single prompt path
# ---------------------------------------------------------------------------
class TestExecuteSinglePrompt:
    @pytest.mark.asyncio
    async def test_text_single_prompt(self) -> None:
        plugin = PromptPlugin()
        llm = _make_llm_service(content="the answer", model="m1", tokens=10)
        ctx = _make_context(llm_service=llm)

        out = await plugin.execute({"prompt": "Question?", "output_format": "text"}, ctx)

        assert out["result"] == "the answer"
        assert out["model"] == "m1"
        assert out["tokens_used"] == 10
        # Queued exactly one operation (no chunking).
        assert llm.queue_operation.await_count == 1

    @pytest.mark.asyncio
    async def test_json_single_prompt_merges_metadata(self) -> None:
        plugin = PromptPlugin()
        llm = _make_llm_service(content='{"entities": [{"name": "A"}]}', model="m2", tokens=20)
        ctx = _make_context(llm_service=llm)

        out = await plugin.execute({"prompt": "Extract", "output_format": "json"}, ctx)

        assert out["entities"] == [{"name": "A"}]
        assert out["_metadata"]["model"] == "m2"
        assert out["_metadata"]["tokens_used"] == 20

    @pytest.mark.asyncio
    async def test_context_prepended_to_prompt(self) -> None:
        plugin = PromptPlugin()
        llm = _make_llm_service(content="ok", model="m", tokens=1)
        ctx = _make_context(llm_service=llm)

        await plugin.execute(
            {"prompt": "DoThing", "context": "BACKGROUND", "output_format": "text"},
            ctx,
        )

        # The user message should carry both the context and the prompt.
        _args, kwargs = llm.queue_operation.await_args
        user_msg = kwargs["messages"][-1]["content"]
        assert "BACKGROUND" in user_msg
        assert "DoThing" in user_msg

    @pytest.mark.asyncio
    async def test_system_prompt_included(self) -> None:
        plugin = PromptPlugin()
        llm = _make_llm_service(content="ok", model="m", tokens=1)
        ctx = _make_context(llm_service=llm)

        await plugin.execute({"prompt": "P", "system_prompt": "SYS", "output_format": "text"}, ctx)

        _args, kwargs = llm.queue_operation.await_args
        roles = [m["role"] for m in kwargs["messages"]]
        assert "system" in roles


# ---------------------------------------------------------------------------
# execute — chunking path
# ---------------------------------------------------------------------------
class TestExecuteChunking:
    @pytest.mark.asyncio
    async def test_chunking_falls_back_when_no_content_marker(self) -> None:
        # No "Content:\n" marker → _extract_prompt_parts returns None and we
        # fall back to a single prompt execution even though chunking enabled.
        plugin = PromptPlugin()
        llm = _make_llm_service(content="answer", model="m", tokens=5)
        ctx = _make_context(llm_service=llm)

        out = await plugin.execute(
            {"prompt": "no marker", "chunk_strategy": "full", "output_format": "text"},
            ctx,
        )

        assert out["result"] == "answer"
        assert llm.queue_operation.await_count == 1

    @pytest.mark.asyncio
    async def test_chunking_processes_multiple_chunks(self) -> None:
        plugin = PromptPlugin()
        llm = _make_llm_service(content="chunk-answer", model="m3", tokens=7)
        ctx = _make_context(llm_service=llm)

        # Force multiple chunks: a content body well over max_chars with a
        # paragraph break, then an instruction marker.
        body = "aaaa\n\n" + "bbbb\n\n" + "cccc"
        prompt = f"Header\nContent:\n{body}\n\nExtract the entities"

        settings_stub = MagicMock()
        settings_stub.workers.operations_max_concurrent = 2

        with patch("chaoscypher_core.app_config.get_settings", return_value=settings_stub):
            out = await plugin.execute(
                {
                    "prompt": prompt,
                    "chunk_strategy": "full",
                    "output_format": "text",
                    "max_tokens": 1,  # tiny → forces splitting
                },
                ctx,
            )

        # More than one chunk queued.
        assert llm.queue_operation.await_count >= 2
        assert out["_metadata"]["chunk_strategy"] == "full"
        assert out["_metadata"]["chunks_processed"] >= 2
        # Text join joins the per-chunk results.
        assert "chunk-answer" in out["result"]

    @pytest.mark.asyncio
    async def test_chunking_json_merges_entities(self) -> None:
        plugin = PromptPlugin()
        llm = _make_llm_service(content='{"entities": [{"name": "E"}]}', model="m4", tokens=9)
        ctx = _make_context(llm_service=llm)

        body = "aaaa\n\nbbbb\n\ncccc"
        prompt = f"H\nContent:\n{body}\n\nExtract"

        settings_stub = MagicMock()
        settings_stub.workers.operations_max_concurrent = 4

        with patch("chaoscypher_core.app_config.get_settings", return_value=settings_stub):
            out = await plugin.execute(
                {
                    "prompt": prompt,
                    "chunk_strategy": "full",
                    "output_format": "json",
                    "max_tokens": 1,
                },
                ctx,
            )

        # Each chunk contributes one entity; merge extends the list.
        assert len(out["entities"]) == llm.queue_operation.await_count
        assert out["_metadata"]["chunk_strategy"] == "full"
