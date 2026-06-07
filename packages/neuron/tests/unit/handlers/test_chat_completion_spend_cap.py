# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The background chat handler must enforce the LLM daily spend cap.

Regression: ``_run_chat_completion`` calls the chat provider directly — it
bypasses ``LLMQueueService._chat_handler_wrapper`` (the queued chat path that
enforces the cap), so a configured ``max_tokens_per_day`` was silently ignored
on the primary chat surface. A tool-calling loop could run unbounded hosted-LLM
spend and the daily total was never recorded.

The fix mirrors the interactive ``stream_chat_response`` path: ``_spend_check``
before the first LLM call (raising the permanent ``LLMSpendCapExceededError``
when a cap is reached) and ``_spend_record`` after a successful turn. Both open a
short-lived adapter on the active database's ``app.db`` — the same pattern the
LLM-queue worker already uses in ``_chat_handler_wrapper``.

The helper tests mirror ``core .../streaming/chat/test_spend_enforcement.py``;
the wiring tests drive ``_run_chat_completion`` against the idempotency module's
real-SQLite harness to prove the gate blocks before, and records after, the call.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import LLMSpendCapExceededError
from chaoscypher_core.services.chat.management.service import ChatService
from chaoscypher_core.services.llm.spend import _reset_tracker_for_tests
from chaoscypher_neuron.handlers import chat_completion as cc

# Reuse the idempotency harness verbatim — same seams, same scripted async stream.
from .test_chat_completion_idempotency import (
    _patch_streaming_seams,
    _run_kwargs,
    _stream,
)


if TYPE_CHECKING:
    from collections.abc import Iterator


_ADAPTER_FACTORY = "chaoscypher_core.database.adapter_factory.get_sqlite_adapter"


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


@pytest.fixture(autouse=True)
def _fresh_tracker() -> Iterator[None]:
    """Each test starts with a clean process-wide spend tracker."""
    _reset_tracker_for_tests()
    yield
    _reset_tracker_for_tests()


def _settings(per_day: int | None = None) -> MagicMock:
    settings = MagicMock()
    settings.current_database = "test_db"
    settings.llm.max_tokens_per_day = per_day
    settings.llm.max_tokens_per_source = None
    return settings


# ===========================================================================
# _spend_check / _spend_record helpers (mirror the streaming-path unit tests)
# ===========================================================================


@patch(_ADAPTER_FACTORY)
def test_spend_check_raises_when_daily_cap_reached(mock_get_adapter: MagicMock) -> None:
    """The pre-call gate raises (and disconnects its adapter) once the cap is hit."""
    adapter = MagicMock()
    adapter.get_daily_token_spend.return_value = 10_000
    mock_get_adapter.return_value = adapter

    with pytest.raises(LLMSpendCapExceededError):
        cc._spend_check(_settings(per_day=5_000))

    adapter.disconnect.assert_called_once()


@patch(_ADAPTER_FACTORY)
def test_spend_check_passes_when_under_cap(mock_get_adapter: MagicMock) -> None:
    """Under the cap, the gate does not raise but still disconnects its adapter."""
    adapter = MagicMock()
    adapter.get_daily_token_spend.return_value = 100
    mock_get_adapter.return_value = adapter

    cc._spend_check(_settings(per_day=5_000))  # no raise

    adapter.disconnect.assert_called_once()


@patch(_ADAPTER_FACTORY)
def test_spend_record_adds_turn_tokens_to_daily_total(mock_get_adapter: MagicMock) -> None:
    """A completed turn's tokens are written to the persisted daily total."""
    adapter = MagicMock()
    mock_get_adapter.return_value = adapter

    cc._spend_record(_settings(per_day=5_000), 1234)

    adapter.add_daily_token_spend.assert_called_once()
    assert adapter.add_daily_token_spend.call_args.kwargs["tokens"] == 1234
    adapter.disconnect.assert_called_once()


# ===========================================================================
# _run_chat_completion wiring: gate blocks before, records after
# ===========================================================================


@pytest.mark.asyncio
async def test_run_chat_completion_blocks_llm_when_cap_reached(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the daily cap is reached, the turn never calls the provider.

    ``_spend_check`` raises the permanent ``LLMSpendCapExceededError`` before the
    first LLM call, so no tokens are spent and the error propagates to the outer
    handler (which marks the chat failed without retry).
    """
    provider = MagicMock()
    provider.chat = AsyncMock()  # must NOT be awaited — the cap blocks first
    tool_executor = MagicMock()

    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (provider, tool_executor, []),
    )

    def _raise(_settings: Any) -> None:
        raise LLMSpendCapExceededError(scope="day", cap_tokens=5_000, consumed_tokens=10_000)

    monkeypatch.setattr(cc, "_spend_check", _raise)

    with pytest.raises(LLMSpendCapExceededError):
        await cc._run_chat_completion(**_run_kwargs(chat_service))

    provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_chat_completion_records_spend_on_success(
    chat_service: ChatService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful turn records its estimated tokens against the daily cap."""
    provider = MagicMock()
    provider.chat = AsyncMock(
        side_effect=[_stream({"type": "done", "content": "Final answer", "tool_calls": None})]
    )
    tool_executor = MagicMock()

    _patch_streaming_seams(
        monkeypatch,
        setup_chat_providers=lambda *a, **k: (provider, tool_executor, []),
    )
    # Neutralise the pre-call gate so it doesn't touch a real adapter; the
    # recording seam is what this test asserts.
    monkeypatch.setattr(cc, "_spend_check", lambda settings: None)
    record = MagicMock()
    monkeypatch.setattr(cc, "_spend_record", record)

    result = await cc._run_chat_completion(**_run_kwargs(chat_service))

    assert result["success"] is True
    record.assert_called_once()
    # Second positional arg is the estimated input+output token total.
    assert record.call_args.args[1] > 0
