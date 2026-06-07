# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit coverage for the chat-completion handler pipeline.

Complements ``test_chat_completion_idempotency.py`` (which drives the whole
``_run_chat_completion`` pipeline against a real SQLite ``ChatService`` to prove
buffered-then-flushed persistence) by exercising the individual stages and the
outer ``chat_completion_handler`` wrapper directly:

* ``chat_completion_handler`` — missing ``chat_id`` guard, the success
  delegation that flips status to ``processing``, and the error arm that flips
  status to ``error``, publishes a ``WORKER_ERROR`` event, and re-raises.
* ``_run_chat_completion`` — chat-not-found, source-scope metadata building,
  and the two early-return error bail-outs (stream / tool-loop).
* ``_consume_llm_stream`` — the pure async-generator state machine (content /
  thinking / error / done chunks, ``aclose`` in ``finally``), with no DB.
* ``_handle_tool_loop`` — the tool-call ceiling, a follow-up error break, and
  the multi-iteration thinking-parts join.
* ``_finalize_and_publish`` — empty-content apology fallback, the
  thinking + tool_calls metadata/done-event keys, and citation/entity
  enrichment when tool results are present (citation helpers patched to
  no-ops to isolate this module's branching).

The streaming/persistence seams reuse the idempotency module's real-SQLite
harness (``chat_service`` fixture, ``_patch_streaming_seams``, ``_run_kwargs``,
``_stream``) so the pure-unit stages here stay focused on this file's logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel
from structlog.testing import capture_logs

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.chat.management.service import ChatService
from chaoscypher_neuron.handlers import chat_completion as cc

# Reuse the idempotency harness verbatim — same seams, same scripted async stream.
from .test_chat_completion_idempotency import (
    _patch_streaming_seams,
    _run_kwargs,
    _stream,
)


if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def chat_service(tmp_path: Path) -> Iterator[ChatService]:
    """A real SQLite-backed ChatService with one chat already created.

    A local copy of the idempotency module's fixture — kept here (rather than
    imported) so the test parameter name does not shadow the imported fixture
    symbol, which the linter flags as a redefinition.
    """
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    service = ChatService(storage=adapter, database_name="default")
    service.create_chat(chat_id="chat-1", title="Test")
    yield service
    adapter.disconnect()


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


class _ClosableStream:
    """Async iterator over scripted chunks that records ``aclose``.

    Mirrors a real provider stream: ``_consume_llm_stream`` must call
    ``aclose()`` in its ``finally`` block, and this records that it did.
    """

    def __init__(self, *chunks: dict[str, Any]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self) -> _ClosableStream:
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._it)
        except StopIteration:  # pragma: no cover - loop terminator
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


def _make_llm_debug() -> Any:
    """A real ``LLMDebugInfo`` so ``_finalize_and_publish`` timing/dict work."""
    from chaoscypher_core.streaming.chat import LLMDebugInfo

    return LLMDebugInfo(provider="test", model="m", initial_messages=[], tools=[])


def _assistant_message(persisted: list[dict[str, Any]]) -> dict[str, Any]:
    """The single assistant message from a flushed ``persist_messages`` batch."""
    return next(m for m in persisted if m["role"] == "assistant")


def _done_event(publish: AsyncMock) -> dict[str, Any]:
    """The payload of the ``done`` event from a ``publish_chat_event`` mock."""
    return next(c.args[2] for c in publish.await_args_list if c.args[1] == "done")


_CITATION_HELPERS = (
    "correct_mismatched_citations",
    "inject_citations_into_blockquotes",
    "inject_citations_for_uncited_paragraphs",
    "normalize_chunk_references",
    "strip_duplicated_citation_text",
)


def _patch_citation_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the ~10 citation helpers ``_finalize_and_publish`` imports.

    Content transformers become identity (``content, *_ -> content``);
    extractors/enrichers are stubbed so the finalize branching — not the core
    citation algorithms — is what these tests exercise.
    """
    mod = "chaoscypher_core.streaming.chat"
    for name in _CITATION_HELPERS:
        monkeypatch.setattr(f"{mod}.{name}", lambda content, *a, **k: content)
    # strip_thinking_tags: identity on the raw content.
    monkeypatch.setattr(f"{mod}.strip_thinking_tags", lambda content, *a, **k: content)
    # Extractors return empty (no citations/entities) unless a test overrides.
    monkeypatch.setattr(f"{mod}.extract_chunk_citations", lambda *a, **k: {})
    monkeypatch.setattr(f"{mod}.extract_entity_references", lambda *a, **k: {})
    monkeypatch.setattr(f"{mod}.enrich_chunk_citations_from_tool_results", lambda c, *a, **k: c)
    monkeypatch.setattr(f"{mod}.enrich_entity_references_from_tool_results", lambda e, *a, **k: e)
    # extract_thinking_from_tags lives in .utils.
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.utils.extract_thinking_from_tags",
        lambda *a, **k: None,
    )


# ===========================================================================
# chat_completion_handler (outer wrapper) via register_chat_completion_handler
# ===========================================================================


def _register_and_capture_handler() -> Any:
    """Register the handler against a recording queue and return the callable."""
    captured: dict[str, Any] = {}

    def _fake_register(queue_name: str, handlers: dict[str, Any]) -> None:
        captured.update(handlers)

    storage = MagicMock()
    settings = get_settings()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cc.queue_client, "register_handlers", _fake_register)
        cc.register_chat_completion_handler(
            storage_adapter=storage,
            settings=settings,
            config_manager=MagicMock(),
            graph_repository=MagicMock(),
            search_repository=MagicMock(),
            current_database="default",
        )
    return captured["chat_background"], storage


@pytest.mark.asyncio
async def test_handler_missing_chat_id_raises_value_error() -> None:
    """Outer handler rejects task data with no ``chat_id``."""
    handler, _storage = _register_and_capture_handler()

    with pytest.raises(ValueError, match="chat_id is required"):
        await handler({}, None, "task-1")


@pytest.mark.asyncio
async def test_handler_success_sets_processing_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: status flips to ``processing`` and ``_run_chat_completion`` runs."""
    handler, _storage = _register_and_capture_handler()

    created: dict[str, Any] = {}

    class _FakeService:
        def __init__(self, **kwargs: Any) -> None:
            created["kwargs"] = kwargs
            self.statuses: list[str] = []

        def update_chat_status(self, chat_id: str, status: str) -> None:
            self.statuses.append(status)

    # ChatService is lazily imported inside the handler — patch at source path.
    monkeypatch.setattr(
        "chaoscypher_core.services.chat.management.service.ChatService", _FakeService
    )
    run_mock = AsyncMock(return_value={"success": True, "chat_id": "c1"})
    monkeypatch.setattr(cc, "_run_chat_completion", run_mock)

    result = await handler({"chat_id": "c1"}, None, "task-1")

    assert result == {"success": True, "chat_id": "c1"}
    # Status was set to "processing" before delegating.
    assert run_mock.await_count == 1
    assert run_mock.await_args.kwargs["chat_id"] == "c1"


@pytest.mark.asyncio
async def test_handler_processing_status_set_before_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``processing`` status precedes the delegated run call."""
    handler, _storage = _register_and_capture_handler()
    order: list[str] = []

    class _FakeService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def update_chat_status(self, chat_id: str, status: str) -> None:
            order.append(f"status:{status}")

    async def _fake_run(**kwargs: Any) -> dict[str, Any]:
        order.append("run")
        return {"success": True}

    monkeypatch.setattr(
        "chaoscypher_core.services.chat.management.service.ChatService", _FakeService
    )
    monkeypatch.setattr(cc, "_run_chat_completion", _fake_run)

    await handler({"chat_id": "c1"}, None, None)

    assert order == ["status:processing", "run"]


@pytest.mark.asyncio
async def test_handler_exception_sets_error_publishes_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error arm: status -> error, ``WORKER_ERROR`` event published, re-raise."""
    handler, _storage = _register_and_capture_handler()
    statuses: list[str] = []

    class _FakeService:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def update_chat_status(self, chat_id: str, status: str) -> None:
            statuses.append(status)

    monkeypatch.setattr(
        "chaoscypher_core.services.chat.management.service.ChatService", _FakeService
    )
    monkeypatch.setattr(cc, "_run_chat_completion", AsyncMock(side_effect=RuntimeError("boom")))
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    with pytest.raises(RuntimeError, match="boom"):
        await handler({"chat_id": "c1"}, None, "task-1")

    # processing (pre-run) then error (in the except arm).
    assert statuses == ["processing", "error"]
    publish.assert_awaited_once()
    args = publish.await_args.args
    assert args[0] == "c1"
    assert args[1] == "error"
    assert args[2]["error_code"] == "WORKER_ERROR"


# ===========================================================================
# _run_chat_completion: chat-not-found, source scope, early error bail-outs
# ===========================================================================


@pytest.mark.asyncio
async def test_run_chat_not_found_publishes_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing chat publishes ``CHAT_NOT_FOUND`` then raises ``ValueError``."""
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    chat_service = MagicMock()
    chat_service.get_chat.return_value = None

    kwargs = {
        "chat_id": "missing",
        "chat_service": chat_service,
        "storage_adapter": MagicMock(),
        "settings": get_settings(),
        "config_manager": MagicMock(),
        "graph_repository": MagicMock(),
        "search_repository": MagicMock(),
    }

    with pytest.raises(ValueError, match="Chat missing not found"):
        await cc._run_chat_completion(**kwargs)

    publish.assert_awaited_once()
    assert publish.await_args.args[2]["error_code"] == "CHAT_NOT_FOUND"


@pytest.mark.asyncio
async def test_run_source_scope_builds_source_metadata(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """source_ids on the chat drives ``get_source`` lookups into source_metadata.

    A scoped chat is created; ``build_messages_for_llm`` is captured so we can
    assert the resolved ``source_metadata`` (one resolvable source, one missing)
    was threaded through. The LLM stream answers cleanly with no tools.
    """
    chat_service.create_chat(chat_id="scoped", title="Scoped", source_ids=["s1", "s2"])

    storage = chat_service.storage
    real_get_source = MagicMock(
        side_effect=lambda sid, db: {"title": "Doc One"} if sid == "s1" else None
    )
    monkeypatch.setattr(storage, "get_source", real_get_source)

    captured: dict[str, Any] = {}
    build_result = MagicMock()
    build_result.messages_for_llm = []
    build_result.context_info = MagicMock()

    def _capture_build(chat: Any, chat_id: str, settings: Any, **kwargs: Any) -> Any:
        captured["source_metadata"] = kwargs.get("source_metadata")
        return build_result

    provider = MagicMock()
    provider.chat = AsyncMock(
        side_effect=[_stream({"type": "done", "content": "Answer", "tool_calls": None})]
    )
    tool_executor = MagicMock()

    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (provider, tool_executor, []),
    )
    monkeypatch.setattr("chaoscypher_core.streaming.chat.build_messages_for_llm", _capture_build)

    kwargs = _run_kwargs(chat_service)
    kwargs["chat_id"] = "scoped"
    result = await cc._run_chat_completion(**kwargs)

    assert result["success"] is True
    # Only the resolvable source made it into the metadata.
    assert captured["source_metadata"] == [{"id": "s1", "title": "Doc One"}]
    # get_source was queried for both declared source_ids.
    assert real_get_source.call_count == 2


@pytest.mark.asyncio
async def test_run_stream_error_returns_failure_status_error(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ``error`` chunk on the first stream bails out with success=False."""
    provider = MagicMock()
    provider.chat = AsyncMock(
        side_effect=[_stream({"type": "error", "error": "kaboom", "error_code": "LLM_ERROR"})]
    )
    tool_executor = MagicMock()

    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (provider, tool_executor, []),
    )

    result = await cc._run_chat_completion(**_run_kwargs(chat_service))

    assert result == {
        "success": False,
        "chat_id": "chat-1",
        "error": "LLM streaming failed",
    }
    assert chat_service.get_chat("chat-1")["status"] == "error"


@pytest.mark.asyncio
async def test_run_tool_loop_error_returns_failure_status_error(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A follow-up error inside the tool loop bails with success=False."""
    tool_call = {"function": {"name": "search", "arguments": "{}"}, "id": "tc-1"}
    provider = MagicMock()
    provider.chat = AsyncMock(
        side_effect=[
            _stream({"type": "done", "content": "", "tool_calls": [tool_call]}),
            _stream({"type": "error", "error": "followup down", "error_code": "LLM_ERROR"}),
        ]
    )
    tool_executor = MagicMock()
    tool_executor.execute_tool = AsyncMock(return_value={"hits": []})

    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (provider, tool_executor, [tool_call]),
    )

    result = await cc._run_chat_completion(**_run_kwargs(chat_service))

    assert result == {
        "success": False,
        "chat_id": "chat-1",
        "error": "LLM failed during tool processing",
    }
    assert chat_service.get_chat("chat-1")["status"] == "error"


# ===========================================================================
# _consume_llm_stream (pure async-generator, no DB)
# ===========================================================================


@pytest.mark.asyncio
async def test_consume_stream_content_thinking_then_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content + thinking deltas accumulate; ``done`` yields tool_calls; aclose runs."""
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    tool_call = {"function": {"name": "x", "arguments": "{}"}, "id": "tc"}
    stream = _ClosableStream(
        {"type": "content", "delta": "Hel", "accumulated": "Hel"},
        {"type": "content", "delta": "lo", "accumulated": "Hello"},
        {"type": "thinking_delta", "accumulated": "pondering"},
        {"type": "done", "content": "Hello world", "tool_calls": [tool_call]},
    )

    content, thinking, tool_calls, stream_error = await cc._consume_llm_stream(stream, "c1")

    assert content == "Hello world"
    assert thinking == "pondering"
    assert tool_calls == [tool_call]
    assert stream_error is False
    # aclose() was invoked in finally.
    assert stream.closed is True
    # content x2 + thinking_delta x1 published (done publishes nothing here).
    published_types = [call.args[1] for call in publish.await_args_list]
    assert published_types == ["content", "content", "thinking_delta"]


@pytest.mark.asyncio
async def test_consume_stream_error_chunk_sets_flag_and_breaks(
    monkeypatch: pytest.MonkeyPatch,
    structlog_for_caplog: None,
) -> None:
    """An ``error`` chunk sets ``stream_error`` True, breaks, and logs."""
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    stream = _ClosableStream(
        {"type": "error", "error": "bad", "error_code": "OOPS"},
        # This chunk must never be consumed because the loop breaks first.
        {"type": "content", "delta": "should-not-see", "accumulated": "x"},
    )

    with capture_logs() as captured:
        content, thinking, tool_calls, stream_error = await cc._consume_llm_stream(stream, "c1")

    assert stream_error is True
    assert content == ""
    assert tool_calls is None
    assert stream.closed is True
    # error event published; trailing content chunk never reached.
    types = [call.args[1] for call in publish.await_args_list]
    assert types == ["error"]
    assert any(e["event"] == "chat_completion_stream_error" for e in captured)


@pytest.mark.asyncio
async def test_consume_stream_no_aclose_attr_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare async generator (no ``aclose``) still finalises cleanly."""
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    async def _gen() -> Any:
        yield {"type": "done", "content": "ok", "tool_calls": None}

    gen = _gen()
    # Async generators DO have aclose; wrap to strip it and hit the hasattr guard.
    content, thinking, tool_calls, stream_error = await cc._consume_llm_stream(gen, "c1")
    assert content == "ok"
    assert stream_error is False


# ===========================================================================
# _handle_tool_loop
# ===========================================================================


def _tool_loop_kwargs(
    *,
    tool_calls: list[Any],
    chat_provider: Any,
    tool_executor: Any,
    messages_for_llm: list[Any] | None = None,
    content: str = "",
    thinking: str | None = None,
) -> dict[str, Any]:
    settings = MagicMock()
    settings.llm.thinking_for_tools = False
    return {
        "tool_calls": tool_calls,
        "content": content,
        "thinking": thinking,
        "chat_id": "c1",
        "chat_service": MagicMock(),
        "chat_provider": chat_provider,
        "tool_executor": tool_executor,
        "available_tools": [],
        "messages_for_llm": messages_for_llm if messages_for_llm is not None else [],
        "settings": settings,
        "pending_messages": [],
    }


@pytest.mark.asyncio
async def test_tool_loop_exceeds_total_limit_publishes_warning_and_breaks(
    monkeypatch: pytest.MonkeyPatch,
    structlog_for_caplog: None,
) -> None:
    """A batch over ``MAX_TOTAL_TOOL_CALLS`` publishes a warning and breaks.

    The batch is sized one above the ceiling so the very first iteration trips
    the guard before any tool executes or any follow-up LLM call is made.
    """
    from chaoscypher_core.services.chat.engine.constants import MAX_TOTAL_TOOL_CALLS

    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.strip_thinking_tags",
        lambda content, *a, **k: content,
    )

    big_batch = [
        {"function": {"name": "search", "arguments": "{}"}, "id": f"tc-{i}"}
        for i in range(MAX_TOTAL_TOOL_CALLS + 1)
    ]
    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock()  # must NOT be called
    tool_executor = MagicMock()
    tool_executor.execute_tool = AsyncMock()  # must NOT be called

    with capture_logs() as captured:
        final_content, final_thinking, total, error = await cc._handle_tool_loop(
            **_tool_loop_kwargs(
                tool_calls=big_batch,
                chat_provider=chat_provider,
                tool_executor=tool_executor,
                content="partial",
            )
        )

    assert total == MAX_TOTAL_TOOL_CALLS + 1
    assert error is False
    # No follow-up call, no tool execution — the guard broke the loop first.
    chat_provider.chat.assert_not_called()
    tool_executor.execute_tool.assert_not_called()
    # Warning event published.
    warn_events = [c for c in publish.await_args_list if c.args[1] == "warning"]
    assert len(warn_events) == 1
    assert warn_events[0].args[2]["limit"] == MAX_TOTAL_TOOL_CALLS
    assert any(e["event"] == "chat_completion_tool_limit_reached" for e in captured)


@pytest.mark.asyncio
async def test_tool_loop_followup_error_breaks_with_error_occurred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A follow-up stream error stops the loop and returns error_occurred=True."""
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.strip_thinking_tags",
        lambda content, *a, **k: content,
    )
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.parse_tool_arguments",
        lambda raw, name, chat_id: {},
    )

    tool_call = {"function": {"name": "search", "arguments": "{}"}, "id": "tc-1"}
    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock(
        side_effect=[_stream({"type": "error", "error": "down", "error_code": "X"})]
    )
    tool_executor = MagicMock()
    tool_executor.execute_tool = AsyncMock(return_value={"ok": True})

    final_content, final_thinking, total, error = await cc._handle_tool_loop(
        **_tool_loop_kwargs(
            tool_calls=[tool_call],
            chat_provider=chat_provider,
            tool_executor=tool_executor,
            content="seed",
        )
    )

    assert error is True
    assert total == 1
    # The one tool executed before the failed follow-up.
    tool_executor.execute_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_loop_multi_iteration_joins_thinking_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thinking from each iteration is joined with the ``---`` separator."""
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.strip_thinking_tags",
        lambda content, *a, **k: content,
    )
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.parse_tool_arguments",
        lambda raw, name, chat_id: {},
    )

    tc1 = {"function": {"name": "search", "arguments": "{}"}, "id": "tc-1"}
    tc2 = {"function": {"name": "search", "arguments": "{}"}, "id": "tc-2"}
    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock(
        side_effect=[
            # Iteration 1 follow-up: more thinking + asks for another tool.
            _stream(
                {
                    "type": "done",
                    "content": "mid",
                    "thinking": "second-thought",
                    "tool_calls": [tc2],
                }
            ),
            # Iteration 2 follow-up: final answer, no more tools.
            _stream(
                {
                    "type": "done",
                    "content": "final",
                    "thinking": "third-thought",
                    "tool_calls": None,
                }
            ),
        ]
    )
    tool_executor = MagicMock()
    tool_executor.execute_tool = AsyncMock(return_value={"ok": True})

    final_content, final_thinking, total, error = await cc._handle_tool_loop(
        **_tool_loop_kwargs(
            tool_calls=[tc1],
            chat_provider=chat_provider,
            tool_executor=tool_executor,
            content="start",
            thinking="first-thought",
        )
    )

    assert error is False
    assert final_content == "final"
    assert total == 2  # one tool in each of two iterations
    assert final_thinking == "first-thought\n\n---\n\nsecond-thought\n\n---\n\nthird-thought"


# ===========================================================================
# _finalize_and_publish
# ===========================================================================


def _finalize_kwargs(
    *,
    content: str,
    thinking: str | None,
    messages_for_llm: list[Any],
    total_tool_calls: int = 0,
) -> dict[str, Any]:
    chat_service = MagicMock()
    chat_service.build_message.side_effect = lambda chat_id, role, body, meta: {
        "chat_id": chat_id,
        "role": role,
        "content": body,
        "extra_metadata": meta,
    }
    return {
        "content": content,
        "thinking": thinking,
        "chat_id": "c1",
        "chat_service": chat_service,
        "messages_for_llm": messages_for_llm,
        "llm_debug": _make_llm_debug(),
        "stream_start": 0.0,
        "total_tool_calls": total_tool_calls,
        "pending_messages": [],
    }


@pytest.mark.asyncio
async def test_finalize_empty_content_uses_apology_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank cleaned content is replaced with the apology fallback string."""
    _patch_citation_helpers(monkeypatch)
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    kwargs = _finalize_kwargs(content="", thinking=None, messages_for_llm=[])
    result = await cc._finalize_and_publish(**kwargs)

    assert result["success"] is True
    apology = "I apologize, but I was unable to generate a response. Please try again."
    # The persisted assistant message carried the apology.
    persisted = kwargs["chat_service"].persist_messages.call_args.args[0]
    assistant = _assistant_message(persisted)
    assert assistant["content"] == apology
    # done event content is the apology too.
    assert _done_event(publish)["content"] == apology
    assert result["content_length"] == len(apology)


@pytest.mark.asyncio
async def test_finalize_thinking_and_tool_calls_in_metadata_and_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thinking + assistant tool_calls surface in metadata and the done event."""
    _patch_citation_helpers(monkeypatch)
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    tc = {"function": {"name": "search", "arguments": "{}"}, "id": "tc-1"}
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [tc]},
        {"role": "tool", "content": "{}", "tool_call_id": "tc-1", "name": "search"},
    ]

    kwargs = _finalize_kwargs(
        content="The answer.",
        thinking="my reasoning",
        messages_for_llm=messages,
        total_tool_calls=1,
    )
    result = await cc._finalize_and_publish(**kwargs)

    assert result["success"] is True
    # Assistant metadata carries thinking + tool_calls.
    persisted = kwargs["chat_service"].persist_messages.call_args.args[0]
    meta = _assistant_message(persisted)["extra_metadata"]
    assert meta["thinking"] == "my reasoning"
    assert meta["tool_calls"] == [tc]
    assert "llm_debug" in meta
    # done event has matching keys.
    done = _done_event(publish)
    assert done["thinking"] == "my reasoning"
    assert done["tool_calls"] == [tc]
    assert done["status"] == "completed"


@pytest.mark.asyncio
async def test_finalize_citation_and_entity_enrichment_with_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With tool results present, enriched citations/entities reach the done event.

    The citation helpers are patched: extractors return non-empty structures and
    the tool-result-aware enrichers tag them so we can assert the enriched
    payload (not the raw markers) lands in metadata and the done event.
    """
    mod = "chaoscypher_core.streaming.chat"
    # Content transformers: identity.
    for name in _CITATION_HELPERS:
        monkeypatch.setattr(f"{mod}.{name}", lambda content, *a, **k: content)
    monkeypatch.setattr(f"{mod}.strip_thinking_tags", lambda content, *a, **k: content)
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.utils.extract_thinking_from_tags",
        lambda *a, **k: None,
    )
    # Extractors return raw markers.
    monkeypatch.setattr(f"{mod}.extract_chunk_citations", lambda *a, **k: {"chunk-1": {}})
    monkeypatch.setattr(f"{mod}.extract_entity_references", lambda *a, **k: {"ent-1": {}})
    # Enrichers (tool-result aware) attach the supporting text.
    monkeypatch.setattr(
        f"{mod}.enrich_chunk_citations_from_tool_results",
        lambda citations, tool_results: {"chunk-1": {"sentence_text": "quote"}},
    )
    monkeypatch.setattr(
        f"{mod}.enrich_entity_references_from_tool_results",
        lambda entities, tool_results: {"ent-1": {"label": "Entity One"}},
    )

    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    messages = [
        {"role": "tool", "content": '{"chunks": []}', "name": "search"},
    ]
    kwargs = _finalize_kwargs(
        content="Body with [[cite:chunk-1]].",
        thinking=None,
        messages_for_llm=messages,
        total_tool_calls=1,
    )
    result = await cc._finalize_and_publish(**kwargs)

    assert result["success"] is True
    persisted = kwargs["chat_service"].persist_messages.call_args.args[0]
    meta = _assistant_message(persisted)["extra_metadata"]
    assert meta["chunk_citations"] == {"chunk-1": {"sentence_text": "quote"}}
    assert meta["entity_references"] == {"ent-1": {"label": "Entity One"}}
    done = _done_event(publish)
    assert done["chunk_citations"] == {"chunk-1": {"sentence_text": "quote"}}
    assert done["entity_references"] == {"ent-1": {"label": "Entity One"}}
