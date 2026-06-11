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
* ``_finalize_and_publish`` — empty-content apology fallback, the
  thinking + tool_calls metadata/done-event keys, and citation/entity
  enrichment when tool results are present (citation helpers patched to
  no-ops to isolate this module's branching).

The streaming/persistence seams reuse the idempotency module's real-SQLite
harness (``chat_service`` fixture, ``_patch_streaming_seams``, ``_run_kwargs``,
``_stream``) so the pure-unit stages here stay focused on this file's logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel

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
    "strip_malformed_citations",
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
    # The UI's Retry button keys off this: a worker failure persisted nothing
    # (buffered-flush idempotency), so the turn is safely re-runnable.
    assert args[2]["error_details"]["is_retryable"] is True
    assert "Retry" in args[2]["error_details"]["suggested_action"]


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
    # Key MUST be referenced_entities — the frontend and the SSE path read
    # that key; the worker writing entity_references silently dropped all
    # entity enrichment on the web path (2026-06-10 audit P1).
    assert meta["referenced_entities"] == {"ent-1": {"label": "Entity One"}}
    done = _done_event(publish)
    assert done["chunk_citations"] == {"chunk-1": {"sentence_text": "quote"}}
    assert done["referenced_entities"] == {"ent-1": {"label": "Entity One"}}


@pytest.mark.asyncio
async def test_finalize_strips_blockquote_duplicating_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parity with the SSE path: an LLM blockquote next to a [[cite:]] marker
    is stripped (the citation renders its own blockquote, so the prose copy
    would display the quoted text twice).
    """
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    chunk = {
        "chunk_id": "aaaa1111-2222-3333-4444-555566667777",
        "chunk_alias": "C0",
        "filename": "war.txt",
        "original_content": "Quoted sentence from the source text here.",
    }
    messages = [
        {"role": "tool", "content": json.dumps({"chunks": [chunk]}), "name": "search"},
    ]
    kwargs = _finalize_kwargs(
        content='> "Quoted sentence from the source text here." [[cite:C0:S1|war.txt]]',
        thinking=None,
        messages_for_llm=messages,
        total_tool_calls=1,
    )
    await cc._finalize_and_publish(**kwargs)

    persisted = kwargs["chat_service"].persist_messages.call_args.args[0]
    final = _assistant_message(persisted)["content"]
    # The blockquote prose is gone; only the (alias-resolved) marker remains.
    assert "Quoted sentence" not in final
    assert "[[cite:aaaa1111-2222-3333-4444-555566667777:S1|war.txt]]" in final


@pytest.mark.asyncio
async def test_finalize_reflows_punctuation_after_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trailing punctuation moves before the citation marker (SSE parity)."""
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    chunk = {
        "chunk_id": "bbbb1111-2222-3333-4444-555566667777",
        "chunk_alias": "C0",
        "filename": "war.txt",
        "original_content": "body",
    }
    messages = [
        {"role": "tool", "content": json.dumps({"chunks": [chunk]}), "name": "search"},
    ]
    kwargs = _finalize_kwargs(
        content="The result is clear [[cite:C0:S1|war.txt]].",
        thinking=None,
        messages_for_llm=messages,
        total_tool_calls=1,
    )
    await cc._finalize_and_publish(**kwargs)

    persisted = kwargs["chat_service"].persist_messages.call_args.args[0]
    final = _assistant_message(persisted)["content"]
    assert final.index(".") < final.index("[[cite:")


@pytest.mark.asyncio
async def test_finalize_salvages_and_scrubs_malformed_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed-ref markers are salvaged and unusable refs scrubbed (real helpers).

    Live bug 2026-06-10: the model crammed a chunk alias into the sentence
    list (``[[cite:C1:S15,C17|f]]``), no pattern matched, and the raw marker
    text rendered in the UI. Finalize must keep the salvageable part (C1
    resolved to its UUID) and drop the hallucinated ref (C17 unknown).
    """
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    chunk = {
        "chunk_id": "aaaa1111-2222-3333-4444-555566667777",
        "chunk_alias": "C1",
        "filename": "war_and_peace.txt",
        "original_content": "body",
    }
    messages = [
        {"role": "tool", "content": json.dumps({"chunks": [chunk]}), "name": "search"},
    ]
    kwargs = _finalize_kwargs(
        content="Son of Vasíli [[cite:C1:S15,C17|war_and_peace.txt]] indeed.",
        thinking=None,
        messages_for_llm=messages,
        total_tool_calls=1,
    )
    result = await cc._finalize_and_publish(**kwargs)

    assert result["success"] is True
    persisted = kwargs["chat_service"].persist_messages.call_args.args[0]
    final = _assistant_message(persisted)["content"]
    assert "[[cite:aaaa1111-2222-3333-4444-555566667777:S15|war_and_peace.txt]]" in final
    assert "C17" not in final
    assert "S15," not in final
    assert _done_event(publish)["content"] == final


@pytest.mark.asyncio
async def test_finalize_persists_warnings_in_metadata_and_done_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collected warnings ride extra_metadata and the done event payload."""
    _patch_citation_helpers(monkeypatch)
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    chat_service = MagicMock()
    persisted: list[dict[str, Any]] = []
    chat_service.build_message.side_effect = lambda chat_id, role, body, meta: {
        "chat_id": chat_id,
        "role": role,
        "content": body,
        "extra_metadata": meta,
    }
    chat_service.persist_messages.side_effect = persisted.extend

    warnings = [{"kind": "context_overflow", "message": "window overflowed"}]
    import time

    result = await cc._finalize_and_publish(
        content="answer",
        thinking=None,
        chat_id="c1",
        chat_service=chat_service,
        messages_for_llm=[],
        llm_debug=MagicMock(timing={}, to_dict=dict),
        stream_start=time.monotonic(),
        total_tool_calls=0,
        pending_messages=[],
        warnings=warnings,
    )

    assert result["success"] is True
    assistant = next(m for m in persisted if m["role"] == "assistant")
    assert assistant["extra_metadata"]["warnings"] == warnings
    done_payload = next(c.args[2] for c in publish.await_args_list if c.args[1] == "done")
    assert done_payload["warnings"] == warnings


@pytest.mark.asyncio
async def test_finalize_ships_validation_verdicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation verdicts ride extra_metadata and the done event (1b).

    Web chat never received verdicts before the loop unification — the
    validator only ran on the consumer-less /stream path.
    """
    _patch_citation_helpers(monkeypatch)
    validator = AsyncMock(return_value={"verdict": "correct", "reason": "ok", "per_citation": {}})
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.finalize.validate_finalized_answer", validator
    )
    publish = AsyncMock()
    monkeypatch.setattr(cc, "publish_chat_event", publish)

    kwargs = _finalize_kwargs(content="The answer.", thinking=None, messages_for_llm=[])
    kwargs["settings"] = get_settings()
    result = await cc._finalize_and_publish(**kwargs)

    assert result["success"] is True
    validator.assert_awaited_once()
    persisted = kwargs["chat_service"].persist_messages.call_args.args[0]
    meta = _assistant_message(persisted)["extra_metadata"]
    assert meta["validation"]["verdict"] == "correct"
    assert _done_event(publish)["validation"]["verdict"] == "correct"
