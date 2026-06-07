# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the interactive ``chaoscypher chat`` REPL command.

Drives the chat command through Click's CliRunner and unit-tests its
helper functions directly. Everything that would otherwise touch the
network (the LLM provider, embeddings, tool execution, retrieval) is
mocked, so no real I/O happens.

Scenarios covered:
- No-LLM-configured branch exits 1 with the install hint.
- A single ``message`` argument runs one Q&A turn and prints the answer.
- Empty / no-response answers print the "No response received" notice.
- ``--context`` resolves node/file text (found and not-found branches).
- ``--source`` / ``--tag`` scope resolution and source-name lookup.
- The streaming consumer handles content, done, and error chunks.
- Tool calls are executed and looped until a final text response.
- Citation + entity-reference transforms in the stream writer.
- Interactive REPL slash/meta commands: exit, quit, q, clear, /scope,
  help, empty input, plus the clean-exit path and error handling.
- KeyboardInterrupt and generic-exception handling in the command.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import chaoscypher_cli.commands.chat as chat_mod
from chaoscypher_cli.commands.chat import (
    _build_citation_data,
    _build_system_prompt,
    _chat_with_tools,
    _consume_stream,
    _create_tool_infrastructure,
    _get_context_text,
    _get_source_names,
    _interactive_chat,
    _resolve_citation_text,
    _resolve_source_scope,
    _send_message,
    _StreamWriter,
    _summarize_args,
    _transform_entity_refs,
    chat,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _StreamResponse:
    """Minimal stand-in for a streaming LLMChatResponse."""

    def __init__(self, stream: Any) -> None:
        self.is_stream = True
        self.stream = stream
        self.content = ""
        self.tool_calls: list[Any] = []


class _NonStreamResponse:
    """Minimal stand-in for a non-streaming LLMChatResponse."""

    def __init__(self, content: str, tool_calls: list[Any] | None = None) -> None:
        self.is_stream = False
        self.stream = None
        self.content = content
        self.tool_calls = tool_calls or []


async def _make_stream(chunks: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Yield the given chunk dicts as an async generator."""
    for chunk in chunks:
        yield chunk


def _text_stream(text: str) -> Any:
    """A stream that emits ``text`` as a content delta then a done chunk."""
    return _make_stream(
        [
            {"type": "content", "delta": text, "accumulated": text},
            {"type": "done", "content": text, "tool_calls": []},
        ]
    )


def _make_llm_provider(responses: list[Any]) -> MagicMock:
    """Build a mock LLM provider whose ``chat`` returns each response in turn."""
    provider = MagicMock()
    calls: dict[str, Any] = {"messages": None, "count": 0}

    async def _chat(**kwargs: Any) -> Any:
        calls["messages"] = kwargs.get("messages")
        idx = min(calls["count"], len(responses) - 1)
        calls["count"] += 1
        return responses[idx]

    provider.chat = _chat
    provider._calls = calls
    return provider


def _ctx_with_llm(provider: MagicMock | None = None) -> MagicMock:
    """A CLI context configured with an LLM provider."""
    ctx = MagicMock()
    ctx.has_llm = True
    ctx.database_name = "default"
    ctx.llm_provider = (
        provider
        if provider is not None
        else _make_llm_provider([_StreamResponse(_text_stream("Hello there."))])
    )
    return ctx


# ===========================================================================
# chat command — top-level branches
# ===========================================================================


def test_no_llm_exits_1_with_hint() -> None:
    """When no LLM is configured the command exits 1 and prints the install hint."""
    runner = CliRunner()
    ctx = MagicMock()
    ctx.has_llm = False

    with patch.object(chat_mod, "get_context", return_value=ctx):
        result = runner.invoke(chat, ["Hello?"])

    assert result.exit_code == 1
    assert "LLM" in result.output
    assert "Ollama" in result.output


def test_single_message_prints_answer_and_calls_llm() -> None:
    """A message argument runs one turn and streams the assistant answer."""
    runner = CliRunner()
    provider = _make_llm_provider([_StreamResponse(_text_stream("The answer is 42."))])
    ctx = _ctx_with_llm(provider)

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", return_value=(MagicMock(), [])):
            result = runner.invoke(chat, ["What is the answer?"])

    assert result.exit_code == 0, result.output
    assert "The answer is 42." in result.output
    # The user's message reached the LLM provider.
    sent = provider._calls["messages"]
    assert any(m.get("content") == "What is the answer?" for m in sent)


def test_single_message_empty_response_shows_notice() -> None:
    """An empty assistant response prints the 'No response received' notice."""
    runner = CliRunner()
    provider = _make_llm_provider([_NonStreamResponse(content="")])
    ctx = _ctx_with_llm(provider)

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", return_value=(MagicMock(), [])):
            result = runner.invoke(chat, ["Anything?"])

    assert result.exit_code == 0, result.output
    assert "No response received" in result.output


def test_context_id_found_prints_using_context() -> None:
    """--context with resolvable text prints the 'Using context' notice."""
    runner = CliRunner()
    provider = _make_llm_provider([_StreamResponse(_text_stream("ok"))])
    ctx = _ctx_with_llm(provider)

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", return_value=(MagicMock(), [])):
            with patch.object(chat_mod, "_get_context_text", return_value="some context"):
                result = runner.invoke(chat, ["--context", "node-123", "hi"])

    assert result.exit_code == 0, result.output
    assert "Using context from: node-123" in result.output


def test_context_id_not_found_warns() -> None:
    """--context that resolves to nothing prints a warning but still runs."""
    runner = CliRunner()
    provider = _make_llm_provider([_StreamResponse(_text_stream("ok"))])
    ctx = _ctx_with_llm(provider)

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", return_value=(MagicMock(), [])):
            with patch.object(chat_mod, "_get_context_text", return_value=None):
                result = runner.invoke(chat, ["--context", "missing-id", "hi"])

    assert result.exit_code == 0, result.output
    assert "Could not find context: missing-id" in result.output


def test_source_scope_prints_count_and_passes_ids() -> None:
    """--source scopes the chat and forwards source IDs to tool infrastructure."""
    runner = CliRunner()
    provider = _make_llm_provider([_StreamResponse(_text_stream("scoped"))])
    ctx = _ctx_with_llm(provider)
    ctx.storage_adapter.get_source.return_value = {"title": "My Doc"}

    captured: dict[str, Any] = {}

    def _fake_infra(c: Any, source_ids: Any = None) -> tuple[Any, list[Any]]:
        captured["source_ids"] = source_ids
        return MagicMock(), []

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", side_effect=_fake_infra):
            result = runner.invoke(chat, ["--source", "src-1", "--source", "src-2", "hi"])

    assert result.exit_code == 0, result.output
    assert "Scoped to 2 source(s)" in result.output
    assert set(captured["source_ids"]) == {"src-1", "src-2"}


def test_keyboard_interrupt_ends_cleanly() -> None:
    """A KeyboardInterrupt during the run prints 'Chat ended.' and exits 0."""
    runner = CliRunner()
    ctx = _ctx_with_llm()

    def _boom(*_a: Any, **_k: Any) -> None:
        raise KeyboardInterrupt

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", side_effect=_boom):
            result = runner.invoke(chat, ["hi"])

    assert result.exit_code == 0, result.output
    assert "Chat ended." in result.output


def test_generic_exception_exits_1() -> None:
    """A generic error is reported and the command exits 1."""
    runner = CliRunner()
    ctx = _ctx_with_llm()

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("kaboom")

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", side_effect=_boom):
            result = runner.invoke(chat, ["hi"])

    assert result.exit_code == 1
    assert "kaboom" in result.output


def test_no_message_enters_interactive_mode() -> None:
    """Omitting MESSAGE routes into the interactive REPL helper."""
    runner = CliRunner()
    ctx = _ctx_with_llm()
    captured: dict[str, Any] = {}

    def _fake_interactive(c: Any, *_a: Any, **kwargs: Any) -> None:
        captured["called"] = True
        captured["source_ids"] = kwargs.get("source_ids")

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", return_value=(MagicMock(), [])):
            with patch.object(chat_mod, "_interactive_chat", side_effect=_fake_interactive):
                result = runner.invoke(chat, [])

    assert result.exit_code == 0, result.output
    assert captured["called"] is True


# ===========================================================================
# _resolve_source_scope
# ===========================================================================


def test_resolve_source_scope_none_when_empty() -> None:
    ctx = MagicMock()
    assert _resolve_source_scope(ctx, (), ()) is None


def test_resolve_source_scope_sources_only() -> None:
    ctx = MagicMock()
    result = _resolve_source_scope(ctx, ("a", "b"), ())
    assert set(result) == {"a", "b"}


def test_resolve_source_scope_merges_tags() -> None:
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.get_source_ids_by_tag_ids.return_value = ["b", "c"]
    result = _resolve_source_scope(ctx, ("a",), ("tag-1",))
    assert set(result) == {"a", "b", "c"}
    ctx.storage_adapter.get_source_ids_by_tag_ids.assert_called_once_with(["tag-1"], "default")


# ===========================================================================
# _get_source_names
# ===========================================================================


def test_get_source_names_uses_title_then_filename_then_id() -> None:
    ctx = MagicMock()
    ctx.database_name = "default"

    def _get_source(sid: str, _db: str) -> dict[str, Any] | None:
        return {
            "s1": {"title": "Titled"},
            "s2": {"filename": "file.pdf"},
            "s3": None,
        }[sid]

    ctx.storage_adapter.get_source.side_effect = _get_source
    names = _get_source_names(ctx, ["s1", "s2", "s3"])
    assert names == ["Titled", "file.pdf", "s3"]


# ===========================================================================
# _get_context_text
# ===========================================================================


def test_get_context_text_from_node() -> None:
    ctx = MagicMock()
    ctx.node_service.get_node.return_value = {
        "name": "Pierre",
        "description": "A person",
        "properties": {"role": "CEO"},
    }
    text = _get_context_text(ctx, "node-1")
    assert "Name: Pierre" in text
    assert "Description: A person" in text
    assert "Properties:" in text


def test_get_context_text_from_file_chunks() -> None:
    ctx = MagicMock()
    ctx.node_service.get_node.return_value = None
    ctx.database_name = "default"
    ctx.storage_adapter.get_file.return_value = {"id": "f1"}
    ctx.storage_adapter.list_chunks.return_value = [
        {"content": "chunk one"},
        {"content": "chunk two"},
    ]
    text = _get_context_text(ctx, "f1")
    assert "chunk one" in text
    assert "chunk two" in text


def test_get_context_text_returns_none_when_nothing_found() -> None:
    ctx = MagicMock()
    ctx.node_service.get_node.return_value = None
    ctx.storage_adapter.get_file.return_value = None
    assert _get_context_text(ctx, "nope") is None


def test_get_context_text_swallows_node_lookup_error() -> None:
    ctx = MagicMock()
    ctx.node_service.get_node.side_effect = RuntimeError("db down")
    ctx.storage_adapter.get_file.return_value = None
    assert _get_context_text(ctx, "x") is None


def test_get_context_text_swallows_file_lookup_error() -> None:
    ctx = MagicMock()
    ctx.node_service.get_node.return_value = None
    ctx.storage_adapter.get_file.side_effect = RuntimeError("storage down")
    assert _get_context_text(ctx, "x") is None


# ===========================================================================
# _build_system_prompt
# ===========================================================================


def test_build_system_prompt_default() -> None:
    from chaoscypher_core.services.chat.engine.constants import SYSTEM_PROMPT

    assert _build_system_prompt() == SYSTEM_PROMPT


def test_build_system_prompt_custom() -> None:
    prompt = _build_system_prompt(custom="Be terse")
    assert prompt == "Be terse"


def test_build_system_prompt_with_context_and_scope() -> None:
    prompt = _build_system_prompt(
        context_text="extra ctx",
        custom="Base",
        source_names=["Doc A", "Doc B"],
    )
    assert "Additional context:" in prompt
    assert "extra ctx" in prompt
    assert "SOURCE SCOPE" in prompt
    assert "- Doc A" in prompt
    assert "- Doc B" in prompt


def test_build_system_prompt_truncates_long_context() -> None:
    long_text = "x" * 10000
    prompt = _build_system_prompt(context_text=long_text, custom="Base")
    # Only _MAX_SYSTEM_PROMPT_CONTEXT_CHARS chars of context are injected.
    assert "x" * chat_mod._MAX_SYSTEM_PROMPT_CONTEXT_CHARS in prompt
    assert "x" * (chat_mod._MAX_SYSTEM_PROMPT_CONTEXT_CHARS + 1) not in prompt


# ===========================================================================
# _create_tool_infrastructure
# ===========================================================================


def test_create_tool_infrastructure_builds_executor_and_tools() -> None:
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()

    fake_executor = MagicMock()
    fake_tools = [{"name": "search_nodes"}]

    with patch.object(chat_mod, "MAX_TOOL_ITERATIONS", 10):
        with patch(
            "chaoscypher_core.services.workflows.tools.engine.executor.ToolExecutorService",
            return_value=fake_executor,
        ) as exec_cls:
            with patch(
                "chaoscypher_core.services.workflows.tools.engine.schema_registry.get_essential_tool_schemas",
                return_value=fake_tools,
            ):
                executor, tools = _create_tool_infrastructure(ctx, source_ids=["s1"])

    assert executor is fake_executor
    assert tools == fake_tools
    # Scope was forwarded to the executor.
    _, kwargs = exec_cls.call_args
    assert kwargs["scope"] == {"source_ids": ["s1"]}


def test_create_tool_infrastructure_no_scope_no_llm() -> None:
    ctx = MagicMock()
    ctx.llm_provider = None  # llm_chat_callback should be None

    with patch(
        "chaoscypher_core.services.workflows.tools.engine.executor.ToolExecutorService",
        return_value=MagicMock(),
    ) as exec_cls:
        with patch(
            "chaoscypher_core.services.workflows.tools.engine.schema_registry.get_essential_tool_schemas",
            return_value=[],
        ):
            _create_tool_infrastructure(ctx, source_ids=None)

    _, kwargs = exec_cls.call_args
    assert kwargs["scope"] is None
    assert kwargs["llm_chat_callback"] is None


@pytest.mark.asyncio
async def test_tool_infrastructure_callbacks_invoke_provider_and_embedder() -> None:
    """The wrapped llm_chat and embedding callbacks delegate to the ctx services."""
    ctx = MagicMock()
    provider = _make_llm_provider([_NonStreamResponse(content="cb-content")])
    ctx.llm_provider = provider

    async def _embed(text: str) -> list[float]:
        return [0.1, 0.2]

    ctx.embedding_service.embed = _embed

    captured: dict[str, Any] = {}

    def _capture_executor(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return MagicMock()

    with patch(
        "chaoscypher_core.services.workflows.tools.engine.executor.ToolExecutorService",
        side_effect=_capture_executor,
    ):
        with patch(
            "chaoscypher_core.services.workflows.tools.engine.schema_registry.get_essential_tool_schemas",
            return_value=[],
        ):
            _create_tool_infrastructure(ctx, source_ids=None)

    llm_cb = captured["llm_chat_callback"]
    out = await llm_cb([{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=10)
    assert out == {"content": "cb-content"}

    embed_cb = captured["embedding_callback"]
    assert await embed_cb("text") == [0.1, 0.2]


# ===========================================================================
# _chat_with_tools / _consume_stream
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_with_tools_streams_text() -> None:
    provider = _make_llm_provider([_StreamResponse(_text_stream("Final answer."))])
    messages: list[dict[str, Any]] = [{"role": "user", "content": "q"}]
    content = await _chat_with_tools(provider, messages, [], MagicMock())
    assert content == "Final answer."


@pytest.mark.asyncio
async def test_chat_with_tools_non_streaming_fallback() -> None:
    provider = _make_llm_provider([_NonStreamResponse(content="Plain content")])
    content = await _chat_with_tools(provider, [{"role": "user", "content": "q"}], [], MagicMock())
    assert content == "Plain content"


@pytest.mark.asyncio
async def test_chat_with_tools_executes_tool_then_finishes() -> None:
    """A tool call is executed and the loop continues to a final text answer."""
    tool_call = {
        "id": "call-1",
        "function": {"name": "search_nodes", "arguments": json.dumps({"query": "x"})},
    }
    first = _StreamResponse(
        _make_stream([{"type": "done", "content": "", "tool_calls": [tool_call]}])
    )
    second = _StreamResponse(_text_stream("Done after tool."))
    provider = _make_llm_provider([first, second])

    executor = MagicMock()

    async def _execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"matched": name, "args": args}

    executor.execute_tool = _execute

    messages: list[dict[str, Any]] = [{"role": "user", "content": "q"}]
    content = await _chat_with_tools(provider, messages, [], executor)

    assert content == "Done after tool."
    # An assistant tool_calls message and a tool result message were appended.
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in messages)
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["name"] == "search_nodes"


@pytest.mark.asyncio
async def test_chat_with_tools_handles_string_and_bad_json_arguments() -> None:
    """String arguments are JSON-decoded; invalid JSON falls back to ``{}``."""
    bad_call = {"id": "c", "function": {"name": "search_nodes", "arguments": "{not json"}}
    first = _StreamResponse(
        _make_stream([{"type": "done", "content": "", "tool_calls": [bad_call]}])
    )
    second = _StreamResponse(_text_stream("ok"))
    provider = _make_llm_provider([first, second])

    seen_args: dict[str, Any] = {}

    async def _execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        seen_args["args"] = args
        return {}

    executor = MagicMock()
    executor.execute_tool = _execute

    await _chat_with_tools(provider, [{"role": "user", "content": "q"}], [], executor)
    assert seen_args["args"] == {}


@pytest.mark.asyncio
async def test_chat_with_tools_streamed_text_then_tool_call() -> None:
    """Text streamed before a tool call triggers the mid-stream newline (line 368)."""
    tool_call = {"id": "c", "function": {"name": "search_nodes", "arguments": {}}}
    first = _StreamResponse(
        _make_stream(
            [
                {"type": "content", "delta": "thinking...", "accumulated": "thinking..."},
                {"type": "done", "content": "thinking...", "tool_calls": [tool_call]},
            ]
        )
    )
    second = _StreamResponse(_text_stream("Final."))
    provider = _make_llm_provider([first, second])

    executor = MagicMock()

    async def _execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    executor.execute_tool = _execute

    content = await _chat_with_tools(provider, [{"role": "user", "content": "q"}], [], executor)
    assert content == "Final."


@pytest.mark.asyncio
async def test_chat_with_tools_iteration_limit_with_streamed_text() -> None:
    """Streamed text + tool call across the iteration cap (exercises line 368)."""
    tool_call = {"id": "c", "function": {"name": "search_nodes", "arguments": {}}}

    def _streamed_tool() -> Any:
        return _StreamResponse(
            _make_stream(
                [
                    {"type": "content", "delta": "partial", "accumulated": "partial"},
                    {"type": "done", "content": "partial", "tool_calls": [tool_call]},
                ]
            )
        )

    provider = MagicMock()

    async def _chat(**_kwargs: Any) -> Any:
        return _streamed_tool()

    provider.chat = _chat

    executor = MagicMock()

    async def _execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    executor.execute_tool = _execute

    content = await _chat_with_tools(
        provider, [{"role": "user", "content": "q"}], [], executor, max_iterations=1
    )
    assert content == "partial"


@pytest.mark.asyncio
async def test_chat_with_tools_iteration_limit() -> None:
    """When the LLM keeps requesting tools, the loop stops at max_iterations."""
    tool_call = {"id": "c", "function": {"name": "search_nodes", "arguments": {}}}

    def _always_tool() -> Any:
        return _StreamResponse(
            _make_stream([{"type": "done", "content": "loop", "tool_calls": [tool_call]}])
        )

    provider = MagicMock()

    async def _chat(**_kwargs: Any) -> Any:
        return _always_tool()

    provider.chat = _chat

    executor = MagicMock()

    async def _execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    executor.execute_tool = _execute

    content = await _chat_with_tools(
        provider, [{"role": "user", "content": "q"}], [], executor, max_iterations=2
    )
    assert content == "loop"


@pytest.mark.asyncio
async def test_consume_stream_error_chunk_prints_error() -> None:
    stream = _make_stream(
        [
            {"type": "content", "delta": "partial", "accumulated": "partial"},
            {"type": "error", "error": "stream blew up"},
        ]
    )
    content, tool_calls, did_stream = await _consume_stream(stream)
    assert did_stream is True
    assert tool_calls == []


@pytest.mark.asyncio
async def test_consume_stream_collects_done_tool_calls() -> None:
    tc = {"id": "c", "function": {"name": "x", "arguments": {}}}
    stream = _make_stream([{"type": "done", "content": "final", "tool_calls": [tc]}])
    content, tool_calls, did_stream = await _consume_stream(stream)
    assert content == "final"
    assert tool_calls == [tc]
    assert did_stream is False


# ===========================================================================
# Citation helpers
# ===========================================================================


def test_build_citation_data_extracts_chunks() -> None:
    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "tool",
            "name": "search_chunks",
            "content": json.dumps(
                {
                    "chunks": [
                        {
                            "chunk_alias": "c0",
                            "original_content": "Hello world.",
                            "chunk_metadata": {"sentence_offsets": [{"start": 0, "end": 12}]},
                            "filename": "doc.txt",
                        }
                    ]
                }
            ),
        },
    ]
    data = _build_citation_data(messages)
    assert "C0" in data
    assert data["C0"]["filename"] == "doc.txt"
    assert data["C0"]["original_content"] == "Hello world."


def test_build_citation_data_skips_bad_payloads() -> None:
    messages = [
        {"role": "tool", "content": ""},  # empty content skipped
        {"role": "tool", "content": "{not json"},  # JSON error skipped
        {"role": "tool", "content": json.dumps(["not", "a", "dict"])},  # not a dict
        {"role": "assistant", "content": "ignored"},  # not a tool message
    ]
    assert _build_citation_data(messages) == {}


def test_build_citation_data_skips_non_dict_chunks() -> None:
    """Chunks that aren't dicts or lack a chunk_alias are skipped (line 498)."""
    messages = [
        {
            "role": "tool",
            "content": json.dumps(
                {
                    "chunks": [
                        "not-a-dict",
                        {"no_alias": True},
                        {
                            "chunk_alias": "c1",
                            "original_content": "Text.",
                            "chunk_metadata": {"sentence_offsets": []},
                            "filename": "f.txt",
                        },
                    ]
                }
            ),
        },
    ]
    data = _build_citation_data(messages)
    # Only the valid chunk survives.
    assert set(data.keys()) == {"C1"}


def test_build_citation_data_ignores_non_list_chunk_field() -> None:
    """A 'chunks' value that isn't a list is ignored (line 494-495)."""
    messages = [{"role": "tool", "content": json.dumps({"chunks": "oops"})}]
    assert _build_citation_data(messages) == {}


def test_resolve_citation_text_resolves_sentences() -> None:
    citation_data = {
        "C0": {
            "original_content": "First sentence. Second sentence.",
            "sentence_offsets": [{"start": 0, "end": 15}, {"start": 16, "end": 32}],
            "filename": "doc.txt",
        }
    }
    text, filename = _resolve_citation_text("c0", "S1", citation_data)
    assert text == "First sentence."
    assert filename == "doc.txt"


def test_resolve_citation_text_unknown_alias() -> None:
    text, filename = _resolve_citation_text("ZZ", "S1", {})
    assert text is None
    assert filename == "source"


def test_resolve_citation_text_missing_offsets() -> None:
    citation_data = {"C0": {"original_content": "", "sentence_offsets": [], "filename": "f"}}
    text, filename = _resolve_citation_text("C0", "S1", citation_data)
    assert text is None
    assert filename == "f"


# ===========================================================================
# _StreamWriter and transforms
# ===========================================================================


def test_stream_writer_plain_text(capsys: pytest.CaptureFixture[str]) -> None:
    writer = _StreamWriter()
    writer.write("hello ")
    writer.write("world")
    writer.close()
    out = capsys.readouterr().out
    assert "hello world" in out


def test_stream_writer_close_flushes_dangling_buffer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reference left incomplete at close is flushed as-is (lines 573-574)."""
    writer = _StreamWriter()
    writer.write("trailing [[node")  # incomplete ref stays buffered
    capsys.readouterr()  # drain whatever streamed so far
    writer.close()
    out = capsys.readouterr().out
    assert "[[node" in out  # the leftover buffer was flushed verbatim


def test_stream_writer_buffers_incomplete_reference(capsys: pytest.CaptureFixture[str]) -> None:
    """A dangling ``[[`` is held back until the closing ``]]`` arrives."""
    writer = _StreamWriter()
    writer.write("see [[node")  # incomplete — should buffer from "[["
    mid = capsys.readouterr().out
    assert "see " in mid
    assert "[[node" not in mid  # the incomplete ref is still buffered
    writer.write(":node_abc123|Pierre]] done")
    writer.close()
    final = capsys.readouterr().out
    assert "Pierre" in final
    assert "done" in final


def test_stream_writer_resolves_citation_blockquote(capsys: pytest.CaptureFixture[str]) -> None:
    citation_data = {
        "C0": {
            "original_content": "Quoted sentence here.",
            "sentence_offsets": [{"start": 0, "end": 21}],
            "filename": "doc.txt",
        }
    }
    writer = _StreamWriter(citation_data=citation_data)
    writer.write("As shown [[cite:C0:S1|My Label]].")
    writer.close()
    out = capsys.readouterr().out
    assert "Quoted sentence here." in out
    assert "My Label" in out


def test_stream_writer_unresolved_citation_shows_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    writer = _StreamWriter(citation_data={})
    writer.write("ref [[cite:ZZ:S1|Fallback]] end")
    writer.close()
    out = capsys.readouterr().out
    assert "Fallback" in out


def test_transform_entity_refs_strips_to_label() -> None:
    # console.is_terminal is False under capture, so refs become plain labels.
    text = "Meet [[node:node_abc123|Pierre]] today"
    out = _transform_entity_refs(text)
    assert "Pierre" in out
    assert "[[node" not in out


def _terminal_console() -> MagicMock:
    """A fake console where is_terminal is True and writes are captured."""
    fake = MagicMock()
    fake.is_terminal = True
    buf: list[str] = []
    fake._buf = buf
    fake.file.write = buf.append
    fake.file.flush = MagicMock()
    return fake


def test_transform_entity_refs_bold_in_terminal() -> None:
    """In a terminal, entity refs are wrapped with ANSI bold codes (line 657)."""
    fake = _terminal_console()
    with patch.object(chat_mod, "console", fake):
        out = _transform_entity_refs("Meet [[node:node_abc123|Pierre]] today")
    assert "Pierre" in out
    assert "\033[1m" in out  # bold escape applied


def test_stream_writer_citation_blockquote_in_terminal() -> None:
    """A resolved citation renders as an ANSI blockquote in a terminal (lines 630-635)."""
    fake = _terminal_console()
    citation_data = {
        "C0": {
            "original_content": "Quoted sentence here.",
            "sentence_offsets": [{"start": 0, "end": 21}],
            "filename": "doc.txt",
        }
    }
    with patch.object(chat_mod, "console", fake):
        writer = _StreamWriter(citation_data=citation_data)
        writer.write("As shown [[cite:C0:S1|My Label]].")
        writer.close()
    out = "".join(fake._buf)
    assert "Quoted sentence here." in out
    assert "│" in out  # blockquote bar rendered


def test_stream_writer_unresolved_citation_in_terminal() -> None:
    """An unresolvable citation in a terminal shows a dim label (line 624)."""
    fake = _terminal_console()
    with patch.object(chat_mod, "console", fake):
        writer = _StreamWriter(citation_data={})
        writer.write("ref [[cite:ZZ:S1|Fallback]] end")
        writer.close()
    out = "".join(fake._buf)
    assert "Fallback" in out
    assert "\033[2m" in out  # dim escape applied


# ===========================================================================
# _summarize_args
# ===========================================================================


def test_summarize_args_empty() -> None:
    assert _summarize_args({}) == ""


def test_summarize_args_truncates_and_limits() -> None:
    args = {"a": "x" * 100, "b": 2, "c": 3, "d": 4}
    summary = _summarize_args(args)
    # Only the first three keys are summarized.
    assert "a=" in summary and "b=" in summary and "c=" in summary
    assert "d=" not in summary
    # Long values are truncated with an ellipsis.
    assert "..." in summary


# ===========================================================================
# _send_message
# ===========================================================================


def test_send_message_prints_no_response_when_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctx = MagicMock()
    ctx.llm_provider = _make_llm_provider([_NonStreamResponse(content="")])
    _send_message(ctx, "hi", "system", [], MagicMock())
    out = capsys.readouterr().out
    assert "No response received" in out


# ===========================================================================
# _interactive_chat — REPL slash/meta commands
# ===========================================================================


def _patch_chat_with_tools(answer: str = "An answer.") -> Any:
    """Patch _chat_with_tools to a coroutine returning ``answer`` synchronously."""

    async def _fake(*_args: Any, **_kwargs: Any) -> str:
        return answer

    return patch.object(chat_mod, "_chat_with_tools", side_effect=_fake)


def _run_interactive(inputs: list[str], **kwargs: Any) -> str:
    """Drive the interactive REPL with a scripted sequence of user inputs."""
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    answers = iter(inputs)

    with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
        with chat_mod.console.capture() as cap:
            _interactive_chat(ctx, "system", [], MagicMock(), **kwargs)
    return cap.get()


def test_interactive_exit_command() -> None:
    with _patch_chat_with_tools():
        out = _run_interactive(["exit"])
    assert "Goodbye!" in out


def test_interactive_quit_then_eof() -> None:
    with _patch_chat_with_tools():
        out = _run_interactive(["quit"])
    assert "Goodbye!" in out


def test_interactive_blank_input_then_exit() -> None:
    with _patch_chat_with_tools():
        out = _run_interactive(["   ", "exit"])
    assert "Goodbye!" in out


def test_interactive_clear_resets_conversation() -> None:
    with _patch_chat_with_tools():
        out = _run_interactive(["clear", "quit"])
    assert "Conversation cleared." in out


def test_interactive_help_command() -> None:
    with _patch_chat_with_tools():
        out = _run_interactive(["help", "q"])
    assert "Commands:" in out
    assert "End the chat" in out


def test_interactive_help_with_scope() -> None:
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    ctx.storage_adapter.get_source.return_value = {"title": "Doc A"}
    answers = iter(["help", "q"])

    with _patch_chat_with_tools():
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(ctx, "system", [], MagicMock(), source_ids=["src-1"])
    out = cap.get()
    assert "/scope" in out


def test_interactive_scope_command_with_sources() -> None:
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    ctx.storage_adapter.get_source.return_value = {"title": "Doc A"}
    answers = iter(["/scope", "exit"])

    with _patch_chat_with_tools():
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(ctx, "system", [], MagicMock(), source_ids=["src-1"])
    out = cap.get()
    assert "Current scope" in out
    assert "Doc A" in out


def test_interactive_scope_command_no_sources() -> None:
    with _patch_chat_with_tools():
        out = _run_interactive(["/scope", "exit"])
    assert "No source scope" in out


def test_interactive_normal_turn_runs_and_sends_input() -> None:
    """A normal turn prints the Assistant header and forwards the user's text."""
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    answers = iter(["What is X?", "exit"])
    seen_messages: dict[str, Any] = {}

    async def _fake(_provider: Any, messages: list[dict[str, Any]], *_a: Any, **_k: Any) -> str:
        seen_messages["messages"] = list(messages)
        return "Here is your answer."

    with patch.object(chat_mod, "_chat_with_tools", side_effect=_fake):
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(ctx, "system", [], MagicMock())
    out = cap.get()
    assert "Assistant" in out
    # The user's message was passed through to the chat engine.
    assert any(m.get("content") == "What is X?" for m in seen_messages["messages"])


def test_interactive_empty_answer_shows_notice() -> None:
    with _patch_chat_with_tools(answer=""):
        out = _run_interactive(["question", "exit"])
    assert "No response received" in out


def test_interactive_keyboard_interrupt_ends() -> None:
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()

    def _ask(*_a: Any, **_k: Any) -> str:
        raise KeyboardInterrupt

    with patch.object(chat_mod.Prompt, "ask", side_effect=_ask):
        with chat_mod.console.capture() as cap:
            _interactive_chat(ctx, "system", [], MagicMock())
    assert "Chat ended." in cap.get()


def test_interactive_exception_is_caught_and_loop_continues() -> None:
    """A turn that raises a generic error prints it and continues to the next turn."""
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    answers = iter(["boom-question", "exit"])

    async def _raise(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("turn failed")

    with patch.object(chat_mod, "_chat_with_tools", side_effect=_raise):
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(ctx, "system", [], MagicMock())
    out = cap.get()
    assert "turn failed" in out
    assert "Goodbye!" in out  # reached the exit turn after the error
