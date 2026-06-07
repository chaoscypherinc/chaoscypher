# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Spend cap is enforced at the chat handler wrapper boundary.

P0 2026-05-21. The interactive chat hot path
(``LLMQueueService._chat_handler_wrapper``) must consult the spend tracker
BEFORE delegating to ``provider.chat(...)`` so a daily-budget-exceeded operator
can't keep racking bills via the interactive UI.

Chat calls are non-source-scoped (interactive), so only the daily cap applies
(source_id=None). The daily counter is persisted per-database via the storage
adapter; the worker opens one for the active database, so these tests patch the
``get_sqlite_adapter`` factory to a fake store and assert the wiring.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import LLMSpendCapExceededError
from chaoscypher_core.llm_queue.queue_service import LLMQueueService
from chaoscypher_core.services.llm.spend import _reset_tracker_for_tests, get_llm_spend_tracker


class _FakeAdapter:
    """Fake daily-spend store standing in for the per-database SqliteAdapter."""

    def __init__(self) -> None:
        self._totals: dict[tuple[str, str], int] = {}
        self.disconnected = False

    def get_daily_token_spend(self, *, database_name: str, spend_date: str) -> int:
        return self._totals.get((database_name, spend_date), 0)

    def add_daily_token_spend(self, *, database_name: str, spend_date: str, tokens: int) -> None:
        if tokens <= 0:
            return
        key = (database_name, spend_date)
        self._totals[key] = self._totals.get(key, 0) + tokens

    def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture(autouse=True)
def _fresh_tracker():
    _reset_tracker_for_tests()
    yield
    _reset_tracker_for_tests()


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> _FakeAdapter:
    """Patch the adapter factory the chat handler uses to a shared fake store."""
    adapter = _FakeAdapter()
    monkeypatch.setattr(
        "chaoscypher_core.database.adapter_factory.get_sqlite_adapter",
        lambda database_name="default": adapter,
    )
    return adapter


def _make_settings(*, per_day: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        current_database="default",
        llm=SimpleNamespace(
            max_tokens_per_source=None,
            max_tokens_per_day=per_day,
        ),
    )


def _make_service(settings: SimpleNamespace, provider: MagicMock) -> LLMQueueService:
    """Construct without firing __init__ so we don't need real provider settings."""
    service = LLMQueueService.__new__(LLMQueueService)
    service.provider = provider
    service.settings = settings
    return service


@pytest.mark.asyncio
async def test_daily_cap_exceeded_blocks_chat_call(fake_adapter: _FakeAdapter) -> None:
    """When the daily token budget is reached the chat call is refused."""
    tracker = get_llm_spend_tracker()
    tracker.record(None, 10_000, adapter=fake_adapter, database_name="default")
    settings = _make_settings(per_day=10_000)

    provider = MagicMock()
    provider.chat = AsyncMock()
    service = _make_service(settings, provider)

    with pytest.raises(LLMSpendCapExceededError) as exc_info:
        await service._chat_handler_wrapper(
            data={"messages": [{"role": "user", "content": "hi"}]},
            metadata=None,
            task_id="t1",
        )

    # Verify the provider was NEVER called — the pre-check fired first.
    provider.chat.assert_not_awaited()
    assert exc_info.value.scope == "day"
    assert exc_info.value.code == "LLM_SPEND_CAP_EXCEEDED"
    assert exc_info.value.is_retryable is False
    # The per-call adapter was cleaned up even on the raise path.
    assert fake_adapter.disconnected is True


@pytest.mark.asyncio
async def test_uncapped_chat_call_passes_through_and_records(fake_adapter: _FakeAdapter) -> None:
    """Default uncapped settings: chat runs and the daily total accumulates."""
    settings = _make_settings(per_day=None)

    provider = MagicMock()
    fake_response = MagicMock()
    fake_response.usage = SimpleNamespace(input_tokens=100, output_tokens=200)
    fake_response.model_dump = MagicMock(return_value={"content": "ok"})
    provider.chat = AsyncMock(return_value=fake_response)

    service = _make_service(settings, provider)

    result = await service._chat_handler_wrapper(
        data={"messages": [{"role": "user", "content": "hi"}]},
        metadata=None,
        task_id="t1",
    )

    provider.chat.assert_awaited_once()
    assert result == {"content": "ok"}

    # The persisted daily total recorded this interactive (source_id=None) call.
    tracker = get_llm_spend_tracker()
    assert tracker.tokens_today(adapter=fake_adapter, database_name="default") == 300
    assert fake_adapter.disconnected is True


@pytest.mark.asyncio
async def test_chat_does_not_record_when_usage_missing(fake_adapter: _FakeAdapter) -> None:
    """A provider response without usage (streaming, edge cases) must not crash."""
    settings = _make_settings(per_day=None)

    provider = MagicMock()
    fake_response = MagicMock()
    fake_response.usage = None
    fake_response.model_dump = MagicMock(return_value={"content": "ok"})
    provider.chat = AsyncMock(return_value=fake_response)

    service = _make_service(settings, provider)

    result = await service._chat_handler_wrapper(
        data={"messages": [{"role": "user", "content": "hi"}]},
        metadata=None,
        task_id="t1",
    )

    assert result == {"content": "ok"}
    tracker = get_llm_spend_tracker()
    assert tracker.tokens_today(adapter=fake_adapter, database_name="default") == 0
