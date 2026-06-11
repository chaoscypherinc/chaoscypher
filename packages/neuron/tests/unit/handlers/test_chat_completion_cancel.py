# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Worker-side stop/cancel behavior (Phase 2).

Drives ``_run_chat_completion`` with the cancel seams patched to prove:

* a stale cancel flag is cleared at turn start (an old Stop click can
  never kill a fresh turn);
* a cancelled loop still persists the partial answer + warnings, flips
  the chat back to ``active``, and publishes ``done {status: "cancelled"}``
  — the queue task completes normally (not orphaned);
* an uncancelled turn keeps publishing ``done {status: "completed"}``.

Reuses the idempotency module's real-SQLite harness so persistence is
observable while the LLM/tool/pub-sub seams stay scripted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.services.chat.management.service import ChatService

from .test_chat_completion_idempotency import (
    _patch_streaming_seams,
    _run_kwargs,
    _stream,
)


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def chat_service(tmp_path: Path) -> Iterator[ChatService]:
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


_TOOL_CALL = {"function": {"name": "search", "arguments": "{}"}, "id": "tc-1"}

_CANCEL_MOD = "chaoscypher_core.streaming.chat.cancellation"


def _done_events(monkeypatch_publish: AsyncMock) -> list[dict[str, Any]]:
    """All ``done`` payloads published through the patched pub/sub seam."""
    return [c.args[2] for c in monkeypatch_publish.await_args_list if c.args[1] == "done"]


def _publish_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Re-patch the pub/sub seam with a handle the test can inspect."""
    publish = AsyncMock()
    monkeypatch.setattr("chaoscypher_neuron.handlers.chat_completion.publish_chat_event", publish)
    return publish


@pytest.mark.asyncio
async def test_stale_cancel_flag_cleared_at_turn_start(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker clears any leftover flag before the loop can read it."""
    from chaoscypher_neuron.handlers.chat_completion import _run_chat_completion

    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock(
        side_effect=[_stream({"type": "done", "content": "Answer.", "tool_calls": None})]
    )
    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (chat_provider, MagicMock(), []),
    )
    clear = AsyncMock()
    monkeypatch.setattr(f"{_CANCEL_MOD}.clear_cancel", clear)

    result = await _run_chat_completion(**_run_kwargs(chat_service))

    assert result["success"] is True
    clear.assert_awaited_once_with("chat-1")


@pytest.mark.asyncio
async def test_cancelled_turn_persists_partial_and_publishes_cancelled(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel during the tool loop ends the turn gracefully end-to-end."""
    from chaoscypher_neuron.handlers.chat_completion import _run_chat_completion

    # First call: partial content + a tool request. The cancel flag reads
    # True at the first tool boundary, so the tool never executes and the
    # partial content becomes the answer.
    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock(
        side_effect=[
            _stream(
                {"type": "done", "content": "Partial answer so far.", "tool_calls": [_TOOL_CALL]}
            )
        ]
    )
    tool_executor = MagicMock()
    tool_executor.execute_tool = AsyncMock(return_value={"hits": []})

    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (chat_provider, tool_executor, [_TOOL_CALL]),
    )
    publish = _publish_mock(monkeypatch)
    monkeypatch.setattr(f"{_CANCEL_MOD}.clear_cancel", AsyncMock())
    monkeypatch.setattr(f"{_CANCEL_MOD}.is_cancel_requested", AsyncMock(return_value=True))

    result = await _run_chat_completion(**_run_kwargs(chat_service))

    # The queue task completes normally — nothing orphaned.
    assert result["success"] is True
    tool_executor.execute_tool.assert_not_awaited()

    # Partial answer + the cancelled warning are persisted.
    messages = chat_service.get_chat_messages("chat-1")
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert "Partial answer so far." in assistant["content"]
    warnings = (assistant.get("extra_metadata") or {}).get("warnings") or []
    assert any(w.get("kind") == "cancelled" for w in warnings)

    # The chat is usable again and the done event says cancelled.
    assert chat_service.get_chat("chat-1")["status"] == "active"
    done = _done_events(publish)
    assert len(done) == 1
    assert done[0]["status"] == "cancelled"
    assert "Partial answer so far." in done[0]["content"]


@pytest.mark.asyncio
async def test_uncancelled_turn_publishes_completed(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default done status stays ``completed``."""
    from chaoscypher_neuron.handlers.chat_completion import _run_chat_completion

    chat_provider = MagicMock()
    chat_provider.chat = AsyncMock(
        side_effect=[_stream({"type": "done", "content": "Full answer.", "tool_calls": None})]
    )
    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (chat_provider, MagicMock(), []),
    )
    publish = _publish_mock(monkeypatch)
    monkeypatch.setattr(f"{_CANCEL_MOD}.clear_cancel", AsyncMock())
    monkeypatch.setattr(f"{_CANCEL_MOD}.is_cancel_requested", AsyncMock(return_value=False))

    result = await _run_chat_completion(**_run_kwargs(chat_service))

    assert result["success"] is True
    done = _done_events(publish)
    assert len(done) == 1
    assert done[0]["status"] == "completed"
