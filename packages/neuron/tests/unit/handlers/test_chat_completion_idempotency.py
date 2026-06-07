# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: chat completion is idempotent under transient-error retry.

The chat handler used to persist tool and assistant messages the moment it
produced them. When a later step raised a transient LLM/network error, the
handler re-raised so the queue worker retried the *same* task_id, re-running
``_run_chat_completion`` from the top — which re-persisted the already-saved
tool/assistant rows (``add_message`` mints fresh ids, no dedup) and polluted
the rebuilt LLM context with the orphaned partial messages.

The fix buffers every message the run produces and flushes the buffer only on
success. A run that raises before finishing therefore persists nothing, so a
retry starts from a clean slate and cannot duplicate rows.

These tests drive ``_run_chat_completion`` directly against a real
SQLite-backed ``ChatService`` (so persistence is observable) while mocking the
LLM provider / tool executor / streaming + pub-sub seams.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.services.chat.management.service import ChatService


async def _stream(*chunks: dict[str, Any]) -> Any:
    """An async iterator over LLM stream chunks (what chat_provider.chat returns)."""
    for chunk in chunks:
        yield chunk


@pytest.fixture
def chat_service(tmp_path: Path) -> ChatService:
    """A real SQLite-backed ChatService with one chat already created."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    service = ChatService(storage=adapter, database_name="default")
    service.create_chat(chat_id="chat-1", title="Test")
    yield service
    adapter.disconnect()


def _patch_streaming_seams(monkeypatch: pytest.MonkeyPatch, *, setup_chat_providers: Any) -> None:
    """Patch the streaming / tool / pub-sub seams ``_run_chat_completion`` pulls in.

    Everything except persistence is mocked; ``setup_chat_providers`` is the
    per-test hook that supplies the scripted provider/tool behaviour.
    """
    build_result = MagicMock()
    build_result.messages_for_llm = []
    build_result.context_info = MagicMock()

    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.build_messages_for_llm",
        lambda *a, **k: build_result,
    )
    monkeypatch.setattr("chaoscypher_core.streaming.chat.get_model_name", lambda *a, **k: "m")
    monkeypatch.setattr(
        "chaoscypher_core.streaming.chat.setup_chat_providers", setup_chat_providers
    )
    monkeypatch.setattr(
        "chaoscypher_core.services.workflows.tools.get_tool_discovery", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "chaoscypher_core.llm_queue.factory.get_provider_factory", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "chaoscypher_neuron.handlers.chat_completion.publish_chat_event", AsyncMock()
    )


def _run_kwargs(chat_service: ChatService) -> dict[str, Any]:
    """Common keyword args for ``_run_chat_completion``."""
    return {
        "chat_id": "chat-1",
        "chat_service": chat_service,
        "storage_adapter": chat_service.storage,
        "settings": get_settings(),
        "config_manager": MagicMock(),
        "graph_repository": MagicMock(),
        "search_repository": MagicMock(),
    }


_TOOL_CALL = {"function": {"name": "search", "arguments": "{}"}, "id": "tc-1"}


@pytest.mark.asyncio
async def test_transient_failure_mid_tool_loop_persists_nothing(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that raises during the tool loop must persist no messages.

    The first LLM call asks for a tool; the tool runs (its result is buffered);
    the follow-up LLM call raises a transient error. Pre-fix, the tool message
    was already committed by the time the error propagated — leaving an orphan
    row a retry would duplicate. Post-fix, nothing is flushed.
    """
    from chaoscypher_neuron.handlers.chat_completion import _run_chat_completion

    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock(
        side_effect=[
            _stream({"type": "done", "content": "", "tool_calls": [_TOOL_CALL]}),
            ConnectionError("transient LLM outage on the follow-up call"),
        ]
    )
    tool_executor = MagicMock()
    tool_executor.execute_tool = AsyncMock(return_value={"hits": []})

    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (chat_provider, tool_executor, [_TOOL_CALL]),
    )

    with pytest.raises(ConnectionError):
        await _run_chat_completion(**_run_kwargs(chat_service))

    assert chat_service.get_chat_messages("chat-1") == [], (
        "a failed run must persist nothing — the buffered tool message was committed early"
    )


@pytest.mark.asyncio
async def test_retry_after_transient_failure_yields_no_duplicates(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running after a mid-loop failure leaves exactly the successful output.

    Simulates the queue worker's same-task_id retry: attempt 1 fails in the
    tool loop, attempt 2 answers cleanly. The persisted history must contain
    only attempt 2's single assistant message — no leftover tool row, no
    duplicate assistant.
    """
    from chaoscypher_neuron.handlers.chat_completion import _run_chat_completion

    # Attempt 1: tool call, then the follow-up LLM call dies transiently.
    provider_1 = MagicMock()
    provider_1.chat = AsyncMock(
        side_effect=[
            _stream({"type": "done", "content": "", "tool_calls": [_TOOL_CALL]}),
            ConnectionError("transient"),
        ]
    )
    # Attempt 2: a clean answer, no tools.
    provider_2 = MagicMock()
    provider_2.chat = AsyncMock(
        side_effect=[_stream({"type": "done", "content": "Final answer", "tool_calls": None})]
    )
    providers = iter([provider_1, provider_2])
    tool_executor = MagicMock()
    tool_executor.execute_tool = AsyncMock(return_value={"hits": []})

    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (next(providers), tool_executor, [_TOOL_CALL]),
    )

    with pytest.raises(ConnectionError):
        await _run_chat_completion(**_run_kwargs(chat_service))

    result = await _run_chat_completion(**_run_kwargs(chat_service))
    assert result["success"] is True

    messages = chat_service.get_chat_messages("chat-1")
    assert [m["role"] for m in messages] == ["assistant"], (
        f"retry must not duplicate or orphan messages; got {[m['role'] for m in messages]}"
    )
    assert messages[0]["content"] == "Final answer"
