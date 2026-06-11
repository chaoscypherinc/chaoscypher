# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the CLI adapters feeding the shared chat tool loop.

- ``CliMessageBuilder`` shapes message dicts (the loop's chat_service seam;
  nothing is persisted).
- ``RichConsoleSink`` renders loop events: streamed deltas through the
  citation-resolving ``_StreamWriter`` (rebuilt per LLM phase so citations
  from this turn's tool results resolve), dim tool lines, approval and
  rejection notices, warnings and errors — and never raises out of
  ``emit()``.
- ``PromptApprovalBroker`` turns approval waits into a y/N Confirm prompt,
  failing closed on EOF, interrupt, or any prompt error.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import chaoscypher_cli.commands.chat_loop_adapter as adapter_mod
from chaoscypher_cli.commands.chat_loop_adapter import (
    CliMessageBuilder,
    PromptApprovalBroker,
    RichConsoleSink,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_tool_message() -> dict[str, Any]:
    """A tool-result message carrying one citable chunk (alias C0)."""
    return {
        "role": "tool",
        "name": "search_chunks",
        "content": json.dumps(
            {
                "chunks": [
                    {
                        "chunk_alias": "c0",
                        "original_content": "Quoted sentence here.",
                        "chunk_metadata": {"sentence_offsets": [{"start": 0, "end": 21}]},
                        "filename": "doc.txt",
                    }
                ]
            }
        ),
    }


# ===========================================================================
# CliMessageBuilder
# ===========================================================================


def test_message_builder_shapes_message() -> None:
    msg = CliMessageBuilder().build_message("cli", "tool", "result text", {"tool_name": "x"})
    assert msg == {
        "chat_id": "cli",
        "role": "tool",
        "content": "result text",
        "extra_metadata": {"tool_name": "x"},
    }


def test_message_builder_defaults_metadata_to_empty_dict() -> None:
    msg = CliMessageBuilder().build_message("cli", "user", "hi")
    assert msg["extra_metadata"] == {}


# ===========================================================================
# RichConsoleSink
# ===========================================================================


@pytest.mark.asyncio
async def test_sink_streams_content_deltas(capsys: pytest.CaptureFixture[str]) -> None:
    """Content deltas stream through the writer and finish() flushes them."""
    sink = RichConsoleSink(MagicMock(), messages=[])
    await sink.emit("content", {"delta": "Hello ", "accumulated": "Hello "})
    await sink.emit("content", {"delta": "world", "accumulated": "Hello world"})
    sink.finish()
    out = capsys.readouterr().out
    assert "Hello world" in out


@pytest.mark.asyncio
async def test_sink_resolves_citations_from_prior_tool_results(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Citation markers resolve against chunks already in the message list."""
    sink = RichConsoleSink(MagicMock(), messages=[_chunk_tool_message()])
    await sink.emit(
        "content",
        {"delta": "See [[cite:C0:S1|label]].", "accumulated": "See [[cite:C0:S1|label]]."},
    )
    sink.finish()
    out = capsys.readouterr().out
    assert "Quoted sentence here." in out
    assert "[[cite:" not in out


@pytest.mark.asyncio
async def test_sink_rebuilds_citation_data_per_llm_phase(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tool phase closes the writer; the next phase sees the new chunks."""
    messages: list[dict[str, Any]] = []
    sink = RichConsoleSink(MagicMock(), messages)

    # Phase 1: no citable chunks exist yet.
    await sink.emit("content", {"delta": "Searching... ", "accumulated": "Searching... "})
    # Tool phase begins — the loop appends the tool result to messages.
    await sink.emit("tool_calls", {"count": 1})
    messages.append(_chunk_tool_message())
    # Phase 2: a fresh writer is built from the updated messages.
    await sink.emit(
        "content",
        {"delta": "Answer [[cite:C0:S1|src]].", "accumulated": "Answer [[cite:C0:S1|src]]."},
    )
    sink.finish()

    out = capsys.readouterr().out
    assert "Searching..." in out
    assert "Quoted sentence here." in out


@pytest.mark.asyncio
async def test_sink_renders_tool_start_line() -> None:
    console = MagicMock()
    sink = RichConsoleSink(console, messages=[])
    await sink.emit("tool_start", {"tool": "search_nodes", "arguments": {"query": "pierre"}})
    printed = console.print.call_args[0][0]
    assert "search_nodes" in printed
    assert "query=pierre" in printed


@pytest.mark.asyncio
async def test_sink_renders_approval_required_line() -> None:
    console = MagicMock()
    sink = RichConsoleSink(console, messages=[])
    await sink.emit(
        "tool_approval_required",
        {"tool_call_id": "tc1", "tool_name": "create_node", "arguments": {"name": "X"}},
    )
    printed = console.print.call_args[0][0]
    assert "Approval required" in printed
    assert "create_node" in printed


@pytest.mark.asyncio
async def test_sink_renders_tool_rejected_line() -> None:
    console = MagicMock()
    sink = RichConsoleSink(console, messages=[])
    await sink.emit("tool_rejected", {"tool_name": "create_node", "decision": "timeout"})
    printed = console.print.call_args[0][0]
    assert "create_node" in printed
    assert "timeout" in printed


@pytest.mark.asyncio
async def test_sink_renders_warning_and_error() -> None:
    console = MagicMock()
    sink = RichConsoleSink(console, messages=[])
    await sink.emit("warning", {"message": "answer was truncated"})
    await sink.emit("error", {"error": "provider unavailable"})
    printed = [call.args[0] for call in console.print.call_args_list]
    assert any("answer was truncated" in line for line in printed)
    assert any("provider unavailable" in line for line in printed)


@pytest.mark.asyncio
async def test_sink_ignores_unknown_event_types() -> None:
    console = MagicMock()
    sink = RichConsoleSink(console, messages=[])
    await sink.emit("iteration_progress", {"iteration": 2})
    console.print.assert_not_called()


@pytest.mark.asyncio
async def test_sink_emit_never_raises_on_render_error() -> None:
    console = MagicMock()
    console.print.side_effect = RuntimeError("render boom")
    sink = RichConsoleSink(console, messages=[])
    await sink.emit("warning", {"message": "w"})  # must not raise


# ===========================================================================
# PromptApprovalBroker
# ===========================================================================


@pytest.mark.asyncio
async def test_prompt_broker_approves_on_yes() -> None:
    broker = PromptApprovalBroker(MagicMock())
    await broker.request("cli", "tc1", "create_node", {"name": "X"}, 1)

    with patch.object(adapter_mod.Confirm, "ask", return_value=True) as ask:
        decision = await broker.wait("cli", "tc1", 120.0)

    assert decision == "approve"
    prompt = ask.call_args[0][0]
    assert "create_node" in prompt
    # Fail-closed default: plain Enter denies.
    assert ask.call_args.kwargs["default"] is False


@pytest.mark.asyncio
async def test_prompt_broker_rejects_on_no() -> None:
    broker = PromptApprovalBroker(MagicMock())
    await broker.request("cli", "tc1", "delete_node", {}, 1)

    with patch.object(adapter_mod.Confirm, "ask", return_value=False):
        decision = await broker.wait("cli", "tc1", 120.0)

    assert decision == "reject"


@pytest.mark.asyncio
async def test_prompt_broker_fails_closed_on_eof() -> None:
    """A non-interactive stdin (EOF) denies instead of hanging or approving."""
    broker = PromptApprovalBroker(MagicMock())
    await broker.request("cli", "tc1", "create_edge", {}, 1)

    with patch.object(adapter_mod.Confirm, "ask", side_effect=EOFError):
        decision = await broker.wait("cli", "tc1", 120.0)

    assert decision == "reject"


@pytest.mark.asyncio
async def test_prompt_broker_fails_closed_on_prompt_error() -> None:
    broker = PromptApprovalBroker(MagicMock())
    await broker.request("cli", "tc1", "update_node", {}, 1)

    with patch.object(adapter_mod.Confirm, "ask", side_effect=RuntimeError("tty gone")):
        decision = await broker.wait("cli", "tc1", 120.0)

    assert decision == "reject"


@pytest.mark.asyncio
async def test_prompt_broker_unknown_call_id_uses_generic_name() -> None:
    """wait() without a prior request() still prompts (generic tool name)."""
    broker = PromptApprovalBroker(MagicMock())

    with patch.object(adapter_mod.Confirm, "ask", return_value=True) as ask:
        decision = await broker.wait("cli", "never-requested", 120.0)

    assert decision == "approve"
    assert "this tool" in ask.call_args[0][0]
