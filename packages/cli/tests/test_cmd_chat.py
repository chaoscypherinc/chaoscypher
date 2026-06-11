# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the interactive ``chaoscypher chat`` REPL command.

Drives the chat command through Click's CliRunner and unit-tests its
helper functions directly. Everything that would otherwise touch the
network (the LLM provider, embeddings, tool execution, retrieval) is
mocked, so no real I/O happens.

Scenarios covered:
- No-LLM-configured branch exits 1 with the install hint.
- A single ``message`` argument runs one Q&A turn through the REAL shared
  chat tool loop (fake chunk-dict provider) and prints the answer.
- Empty / no-response answers print the "No response received" notice.
- ``--context`` resolves node/file text (found and not-found branches).
- ``--source`` / ``--tag`` scope resolution and source-name lookup.
- ``_create_tool_infrastructure`` routes through ``setup_chat_providers``
  (chat_id="cli", scope forwarding, direct-LLM callback override).
- Citation + entity-reference transforms in the stream writer.
- Interactive REPL slash/meta commands: exit, quit, q, clear, /scope,
  help, empty input, plus the clean-exit path and error handling.
- KeyboardInterrupt and generic-exception handling in the command.

The loop internals (tool iteration, duplicate filtering, recovery) are
tested in core's test_loop*.py; REPL tests patch the ``_run_chat_turn``
seam. Console rendering adapters are tested in test_chat_loop_adapter.py.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import chaoscypher_cli.commands.chat as chat_mod
from chaoscypher_cli.commands.chat import (
    ChatTurnError,
    _build_citation_data,
    _build_system_prompt,
    _create_tool_infrastructure,
    _get_context_text,
    _get_source_names,
    _interactive_chat,
    _resolve_citation_text,
    _resolve_source_scope,
    _run_chat_turn,
    _send_message,
    _StreamWriter,
    _summarize_args,
    _transform_entity_refs,
    chat,
)


@pytest.fixture
def chat_loop() -> Any:
    """A fresh event loop for driving _send_message/_interactive_chat directly."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


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


class _FakeChatProvider:
    """Streaming chat provider stand-in matching the shared-loop protocol.

    ``chat(**kwargs)`` records the call kwargs and returns the canned chunk
    streams in order (the last stream is reused if the loop calls again).
    """

    def __init__(self, streams: list[Any]) -> None:
        self._streams = list(streams)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._streams) - 1)
        return self._streams[idx]


def _make_llm_provider(responses: list[Any]) -> MagicMock:
    """Build a mock LLM provider whose ``chat`` returns each response in turn."""
    provider = MagicMock()
    calls: dict[str, Any] = {"messages": None, "kwargs": None, "count": 0}

    async def _chat(**kwargs: Any) -> Any:
        calls["messages"] = kwargs.get("messages")
        calls["kwargs"] = kwargs
        idx = min(calls["count"], len(responses) - 1)
        calls["count"] += 1
        return responses[idx]

    provider.chat = _chat
    provider._calls = calls
    return provider


def _ctx_with_llm(provider: MagicMock | None = None) -> MagicMock:
    """A CLI context configured with an LLM provider.

    The provider only backs the summarize-tool callback now — chat turns go
    through the chat_provider returned by ``_create_tool_infrastructure``.
    """
    ctx = MagicMock()
    ctx.has_llm = True
    ctx.database_name = "default"
    ctx.llm_provider = provider if provider is not None else MagicMock()
    # No spend caps configured — the loop's spend guard is a no-op so the
    # real-loop tests don't hit cap comparisons against MagicMock values.
    ctx.settings.llm.max_tokens_per_source = None
    ctx.settings.llm.max_tokens_per_day = None
    return ctx


def _patch_run_chat_turn(answer: str = "An answer.") -> Any:
    """Patch the shared-loop seam to a coroutine returning ``answer``."""

    async def _fake(*_args: Any, **_kwargs: Any) -> str:
        return answer

    return patch.object(chat_mod, "_run_chat_turn", side_effect=_fake)


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


def test_single_message_prints_answer_and_calls_llm(isolated_settings: Any) -> None:
    """A message argument runs one turn through the real loop and streams the answer."""
    runner = CliRunner()
    ctx = _ctx_with_llm()
    chat_provider = _FakeChatProvider([_text_stream("The answer is 42.")])

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(
            chat_mod,
            "_create_tool_infrastructure",
            return_value=(chat_provider, MagicMock(), []),
        ):
            result = runner.invoke(chat, ["What is the answer?"])

    assert result.exit_code == 0, result.output
    assert "The answer is 42." in result.output
    # The user's message reached the chat provider through the shared loop.
    sent = chat_provider.calls[0]["messages"]
    assert any(m.get("content") == "What is the answer?" for m in sent)


def test_single_message_empty_response_shows_notice(isolated_settings: Any) -> None:
    """An empty assistant response prints the 'No response received' notice."""
    runner = CliRunner()
    ctx = _ctx_with_llm()
    chat_provider = _FakeChatProvider(
        [_make_stream([{"type": "done", "content": "", "tool_calls": []}])]
    )

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(
            chat_mod,
            "_create_tool_infrastructure",
            return_value=(chat_provider, MagicMock(), []),
        ):
            result = runner.invoke(chat, ["Anything?"])

    assert result.exit_code == 0, result.output
    assert "No response received" in result.output


def test_context_id_found_prints_using_context() -> None:
    """--context with resolvable text prints the 'Using context' notice."""
    runner = CliRunner()
    ctx = _ctx_with_llm()

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(
            chat_mod,
            "_create_tool_infrastructure",
            return_value=(MagicMock(), MagicMock(), []),
        ):
            with patch.object(chat_mod, "_get_context_text", return_value="some context"):
                with _patch_run_chat_turn(answer="ok"):
                    result = runner.invoke(chat, ["--context", "node-123", "hi"])

    assert result.exit_code == 0, result.output
    assert "Using context from: node-123" in result.output


def test_context_id_not_found_warns() -> None:
    """--context that resolves to nothing prints a warning but still runs."""
    runner = CliRunner()
    ctx = _ctx_with_llm()

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(
            chat_mod,
            "_create_tool_infrastructure",
            return_value=(MagicMock(), MagicMock(), []),
        ):
            with patch.object(chat_mod, "_get_context_text", return_value=None):
                with _patch_run_chat_turn(answer="ok"):
                    result = runner.invoke(chat, ["--context", "missing-id", "hi"])

    assert result.exit_code == 0, result.output
    assert "Could not find context: missing-id" in result.output


def test_source_scope_prints_count_and_passes_ids() -> None:
    """--source scopes the chat and forwards source IDs to tool infrastructure."""
    runner = CliRunner()
    ctx = _ctx_with_llm()
    ctx.storage_adapter.get_source.return_value = {"title": "My Doc"}

    captured: dict[str, Any] = {}

    def _fake_infra(c: Any, source_ids: Any = None) -> tuple[Any, Any, list[Any]]:
        captured["source_ids"] = source_ids
        return MagicMock(), MagicMock(), []

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(chat_mod, "_create_tool_infrastructure", side_effect=_fake_infra):
            with _patch_run_chat_turn(answer="scoped"):
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
        with patch.object(
            chat_mod,
            "_create_tool_infrastructure",
            return_value=(MagicMock(), MagicMock(), []),
        ):
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


def test_resolve_source_scope_merges_tags_by_name() -> None:
    """--tag values are names; they resolve to tag IDs before the lookup."""
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.list_tags.return_value = [{"id": "tag-1", "name": "Research"}]
    ctx.storage_adapter.get_source_ids_by_tag_ids.return_value = ["b", "c"]
    result = _resolve_source_scope(ctx, ("a",), ("research",))
    assert set(result) == {"a", "b", "c"}
    ctx.storage_adapter.get_source_ids_by_tag_ids.assert_called_once_with(["tag-1"], "default")


def test_resolve_source_scope_accepts_raw_tag_id() -> None:
    """A value matching no name but a real tag ID is used as-is."""
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.list_tags.return_value = [{"id": "tag-1", "name": "research"}]
    ctx.storage_adapter.get_source_ids_by_tag_ids.return_value = ["b"]
    result = _resolve_source_scope(ctx, (), ("tag-1",))
    assert result == ["b"]
    ctx.storage_adapter.get_source_ids_by_tag_ids.assert_called_once_with(["tag-1"], "default")


def test_resolve_source_scope_unknown_tag_exits(capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown tag aborts instead of silently chatting unscoped."""
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.list_tags.return_value = [{"id": "tag-1", "name": "research"}]
    with pytest.raises(SystemExit) as exc_info:
        _resolve_source_scope(ctx, (), ("nope",))
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Unknown tag(s): nope" in out
    assert "research" in out  # available tags listed


def test_resolve_source_scope_tag_without_sources_exits() -> None:
    """Tags that match zero sources abort rather than running unscoped."""
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.list_tags.return_value = [{"id": "tag-1", "name": "research"}]
    ctx.storage_adapter.get_source_ids_by_tag_ids.return_value = []
    with pytest.raises(SystemExit) as exc_info:
        _resolve_source_scope(ctx, (), ("research",))
    assert exc_info.value.code == 1


def test_resolve_source_scope_tag_without_sources_keeps_explicit_sources() -> None:
    """With --source present, an empty tag match warns but keeps the source scope."""
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.list_tags.return_value = [{"id": "tag-1", "name": "research"}]
    ctx.storage_adapter.get_source_ids_by_tag_ids.return_value = []
    result = _resolve_source_scope(ctx, ("a",), ("research",))
    assert result == ["a"]


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


def test_create_tool_infrastructure_routes_through_setup_chat_providers() -> None:
    """The CLI builds its chat stack via the shared setup_chat_providers."""
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()

    fake_provider = MagicMock()
    fake_executor = MagicMock()
    fake_tools = [{"name": "search_nodes"}]

    with patch("chaoscypher_core.app_config.get_settings", return_value=MagicMock()):
        with patch(
            "chaoscypher_core.streaming.chat.setup_chat_providers",
            return_value=(fake_provider, fake_executor, fake_tools),
        ) as setup:
            provider, executor, tools = _create_tool_infrastructure(ctx, source_ids=["s1"])

    assert provider is fake_provider
    assert executor is fake_executor
    assert tools == fake_tools
    args, kwargs = setup.call_args
    assert args[1] is ctx.graph_repository
    assert args[2] is ctx.search_repository
    assert kwargs["chat_id"] == "cli"
    assert kwargs["source_ids"] == ["s1"]
    assert kwargs["indexing_manager"] is ctx.storage_adapter
    assert kwargs["source_storage"] is ctx.storage_adapter
    # An LLM provider is configured, so the direct callback is wired in.
    assert kwargs["llm_chat_callback_override"] is not None


def test_create_tool_infrastructure_no_llm_no_callback_override() -> None:
    """Without an LLM provider the direct-callback override stays None."""
    ctx = MagicMock()
    ctx.llm_provider = None

    with patch("chaoscypher_core.app_config.get_settings", return_value=MagicMock()):
        with patch(
            "chaoscypher_core.streaming.chat.setup_chat_providers",
            return_value=(MagicMock(), MagicMock(), []),
        ) as setup:
            _create_tool_infrastructure(ctx, source_ids=None)

    _, kwargs = setup.call_args
    assert kwargs["source_ids"] is None
    assert kwargs["llm_chat_callback_override"] is None


@pytest.mark.asyncio
async def test_tool_infrastructure_llm_callback_delegates_to_provider() -> None:
    """The direct llm_chat callback calls the CLI provider non-streaming."""
    ctx = MagicMock()
    provider = _make_llm_provider([_NonStreamResponse(content="cb-content")])
    ctx.llm_provider = provider

    captured: dict[str, Any] = {}

    def _capture_setup(*args: Any, **kwargs: Any) -> tuple[Any, Any, list[Any]]:
        captured.update(kwargs)
        return MagicMock(), MagicMock(), []

    with patch("chaoscypher_core.app_config.get_settings", return_value=MagicMock()):
        with patch(
            "chaoscypher_core.streaming.chat.setup_chat_providers",
            side_effect=_capture_setup,
        ):
            _create_tool_infrastructure(ctx, source_ids=None)

    llm_cb = captured["llm_chat_callback_override"]
    out = await llm_cb([{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=10)
    assert out == {"content": "cb-content"}
    # The provider was called non-streaming with the tunables forwarded.
    sent_kwargs = provider._calls["kwargs"]
    assert sent_kwargs["stream"] is False
    assert sent_kwargs["temperature"] == 0.5
    assert sent_kwargs["max_tokens"] == 10


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


def test_stream_writer_malformed_citation_hidden(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mixed-ref markers (chunk alias in the sentence list) are hidden, not printed raw."""
    writer = _StreamWriter(citation_data={})
    writer.write("Son of Vasíli [[cite:C1:S15,C17|war.txt]] end")
    writer.close()
    out = capsys.readouterr().out
    assert "[[cite:" not in out
    assert "Son of Vasíli" in out
    assert "end" in out


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
    chat_loop: Any,
) -> None:
    with _patch_run_chat_turn(answer=""):
        _send_message(MagicMock(), "hi", "system", MagicMock(), [], MagicMock(), chat_loop)
    out = capsys.readouterr().out
    assert "No response received" in out


def test_single_message_turn_error_exits_1() -> None:
    """A failed turn in single-message mode exits non-zero (scripting contract)."""
    runner = CliRunner()
    ctx = _ctx_with_llm()

    async def _raise(*_args: Any, **_kwargs: Any) -> str:
        raise ChatTurnError("The chat turn failed during initial_stream.")

    with patch.object(chat_mod, "get_context", return_value=ctx):
        with patch.object(
            chat_mod,
            "_create_tool_infrastructure",
            return_value=(MagicMock(), MagicMock(), []),
        ):
            with patch.object(chat_mod, "_run_chat_turn", side_effect=_raise):
                result = runner.invoke(chat, ["Anything?"])

    assert result.exit_code == 1
    assert "Error" in result.output


# ===========================================================================
# _run_chat_turn — loop deps wiring (spend guard, error propagation)
# ===========================================================================


def _loop_result(**kwargs: Any) -> Any:
    from chaoscypher_core.streaming.chat.loop import ChatLoopResult

    return ChatLoopResult(**kwargs)


@pytest.mark.asyncio
async def test_run_chat_turn_wires_spend_guard_and_records(
    isolated_settings: Any,
) -> None:
    """The CLI turn passes a spend guard to the loop and records turn spend."""
    ctx = _ctx_with_llm()
    captured: dict[str, Any] = {}

    async def _fake_loop(_messages: Any, deps: Any) -> Any:
        captured["deps"] = deps
        return _loop_result(content="fine")

    with patch(
        "chaoscypher_core.streaming.chat.loop.run_chat_tool_loop",
        side_effect=_fake_loop,
    ):
        with patch.object(chat_mod, "_record_chat_spend") as record:
            content = await _run_chat_turn(
                ctx, MagicMock(), MagicMock(), [], [{"role": "user", "content": "hi"}]
            )

    assert content == "fine"
    assert captured["deps"].spend_guard is not None
    # The guard is a no-op when no caps are configured.
    await captured["deps"].spend_guard()
    record.assert_called_once()


@pytest.mark.asyncio
async def test_run_chat_turn_raises_chat_turn_error_on_loop_error(
    isolated_settings: Any,
) -> None:
    """error_occurred from the shared loop surfaces as ChatTurnError."""
    ctx = _ctx_with_llm()

    async def _fake_loop(_messages: Any, _deps: Any) -> Any:
        return _loop_result(content="", error_occurred=True, error_stage="initial_stream")

    with patch(
        "chaoscypher_core.streaming.chat.loop.run_chat_tool_loop",
        side_effect=_fake_loop,
    ):
        with patch.object(chat_mod, "_record_chat_spend") as record:
            with pytest.raises(ChatTurnError, match="initial_stream"):
                await _run_chat_turn(
                    ctx, MagicMock(), MagicMock(), [], [{"role": "user", "content": "hi"}]
                )

    # Spend is recorded even for a failed turn — tokens were consumed.
    record.assert_called_once()


# ===========================================================================
# _interactive_chat — REPL slash/meta commands
# ===========================================================================


def _run_interactive(inputs: list[str], **kwargs: Any) -> str:
    """Drive the interactive REPL with a scripted sequence of user inputs."""
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    answers = iter(inputs)
    loop = asyncio.new_event_loop()

    try:
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(ctx, "system", MagicMock(), [], MagicMock(), loop, **kwargs)
    finally:
        loop.close()
    return cap.get()


def test_interactive_exit_command() -> None:
    with _patch_run_chat_turn():
        out = _run_interactive(["exit"])
    assert "Goodbye!" in out


def test_interactive_quit_then_eof() -> None:
    with _patch_run_chat_turn():
        out = _run_interactive(["quit"])
    assert "Goodbye!" in out


def test_interactive_blank_input_then_exit() -> None:
    with _patch_run_chat_turn():
        out = _run_interactive(["   ", "exit"])
    assert "Goodbye!" in out


def test_interactive_clear_resets_conversation() -> None:
    with _patch_run_chat_turn():
        out = _run_interactive(["clear", "quit"])
    assert "Conversation cleared." in out


def test_interactive_help_command() -> None:
    with _patch_run_chat_turn():
        out = _run_interactive(["help", "q"])
    assert "Commands:" in out
    assert "End the chat" in out


def test_interactive_help_with_scope(chat_loop: Any) -> None:
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    ctx.storage_adapter.get_source.return_value = {"title": "Doc A"}
    answers = iter(["help", "q"])

    with _patch_run_chat_turn():
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(
                    ctx, "system", MagicMock(), [], MagicMock(), chat_loop, source_ids=["src-1"]
                )
    out = cap.get()
    assert "/scope" in out


def test_interactive_scope_command_with_sources(chat_loop: Any) -> None:
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    ctx.storage_adapter.get_source.return_value = {"title": "Doc A"}
    answers = iter(["/scope", "exit"])

    with _patch_run_chat_turn():
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(
                    ctx, "system", MagicMock(), [], MagicMock(), chat_loop, source_ids=["src-1"]
                )
    out = cap.get()
    assert "Current scope" in out
    assert "Doc A" in out


def test_interactive_scope_command_no_sources() -> None:
    with _patch_run_chat_turn():
        out = _run_interactive(["/scope", "exit"])
    assert "No source scope" in out


def test_interactive_normal_turn_runs_and_sends_input(chat_loop: Any) -> None:
    """A normal turn prints the Assistant header and forwards the user's text."""
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    answers = iter(["What is X?", "exit"])
    seen_messages: dict[str, Any] = {}

    async def _fake(
        _ctx: Any, _provider: Any, _executor: Any, _tools: Any, messages: list[dict[str, Any]]
    ) -> str:
        seen_messages["messages"] = list(messages)
        return "Here is your answer."

    with patch.object(chat_mod, "_run_chat_turn", side_effect=_fake):
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(ctx, "system", MagicMock(), [], MagicMock(), chat_loop)
    out = cap.get()
    assert "Assistant" in out
    # The user's message was passed through to the shared loop.
    assert any(m.get("content") == "What is X?" for m in seen_messages["messages"])


def test_interactive_empty_answer_shows_notice() -> None:
    with _patch_run_chat_turn(answer=""):
        out = _run_interactive(["question", "exit"])
    assert "No response received" in out


def test_interactive_keyboard_interrupt_ends(chat_loop: Any) -> None:
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()

    def _ask(*_a: Any, **_k: Any) -> str:
        raise KeyboardInterrupt

    with patch.object(chat_mod.Prompt, "ask", side_effect=_ask):
        with chat_mod.console.capture() as cap:
            _interactive_chat(ctx, "system", MagicMock(), [], MagicMock(), chat_loop)
    assert "Chat ended." in cap.get()


def test_interactive_exception_is_caught_and_loop_continues(chat_loop: Any) -> None:
    """A turn that raises a generic error prints it and continues to the next turn."""
    ctx = MagicMock()
    ctx.llm_provider = MagicMock()
    answers = iter(["boom-question", "exit"])

    async def _raise(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("turn failed")

    with patch.object(chat_mod, "_run_chat_turn", side_effect=_raise):
        with patch.object(chat_mod.Prompt, "ask", side_effect=lambda *_a, **_k: next(answers)):
            with chat_mod.console.capture() as cap:
                _interactive_chat(ctx, "system", MagicMock(), [], MagicMock(), chat_loop)
    out = cap.get()
    assert "turn failed" in out
    assert "Goodbye!" in out  # reached the exit turn after the error
