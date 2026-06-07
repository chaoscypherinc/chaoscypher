# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage tests for ``llm_queue/queue_service.py`` (``LLMQueueService``).

The module-level ``queue_client`` singleton (imported into ``queue_service`` as a
name) is replaced per-test with an AsyncMock so enqueue/poll/stats calls are
recorded without a real Valkey connection. ``asyncio.sleep`` is patched to a
no-op so the ``wait_for_result`` poll loop spins instantly, and
``get_llm_semaphore`` is patched for the stats path.

Covered behaviors:

- ``queue_operation`` — builds the data payload (messages + task_type + kwargs),
  routes to QUEUE_LLM, and delegates to ``enqueue_task``; ``task_type.value`` vs.
  plain-string handling.
- ``register_handlers`` — registers the chat/tool handler map under QUEUE_LLM.
- ``wait_for_result`` — completed (returns result), failed (raises
  OperationError with the redacted message), cancelled (raises CancelledError),
  not-found (raises ValueError), running-past-timeout (raises TimeoutError),
  and the queued -> running -> completed transition across poll ticks.
- thin delegators — ``cancel_task``, ``get_task_status``, ``list_current_tasks``
  (filters to queued/running), ``cancel_all_tasks``, ``clear_stats``.
- ``get_stats`` — empty-queue stub-entry creation, token-stat merge, depth %,
  and estimated-completion math for both llm and operations queues.

``queue_client`` is patched at the source path
(``chaoscypher_core.llm_queue.queue_service.queue_client``) per the campaign rule
to mock lazy deps at SOURCE.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import chaoscypher_core.llm_queue.queue_service as qs_mod
from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS
from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.llm_queue.queue_service import LLMQueueService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> SimpleNamespace:
    """Build a stub Settings object with the fields the service reads."""
    return SimpleNamespace(
        current_database="testdb",
        timeouts=SimpleNamespace(
            llm_operation_max=120.0,
            queue_poll_interval=0.0,
        ),
        batching=SimpleNamespace(queue_max_depth_display=100),
        llm=SimpleNamespace(
            enable_token_cost_tracking=False,
            token_cost_input_per_million=1.0,
            token_cost_output_per_million=2.0,
        ),
    )


def _make_service() -> LLMQueueService:
    """Construct an LLMQueueService over a stub provider + settings."""
    provider = MagicMock()
    return LLMQueueService(provider=provider, settings=_make_settings())


@pytest.fixture
def patched_queue_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the module-level queue_client singleton with an AsyncMock."""
    qc = MagicMock()
    qc.enqueue_task = AsyncMock(return_value="task-123")
    qc.get_task = AsyncMock(return_value=None)
    qc.get_result = AsyncMock(return_value={"answer": 42})
    qc.cancel_task = AsyncMock(return_value=True)
    qc.get_all_stats = AsyncMock(return_value=[])
    qc.get_token_stats = AsyncMock(
        return_value={
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
        }
    )
    qc.clear_all_stats = AsyncMock(return_value=None)
    qc.clear_old_completed_tasks = AsyncMock(return_value=3)
    qc.get_recent_tasks = AsyncMock(return_value=[])
    qc.cancel_all_tasks = AsyncMock(return_value=5)
    qc.register_handlers = MagicMock()
    monkeypatch.setattr(qs_mod, "queue_client", qc)
    return qc


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make asyncio.sleep instant so the poll loop spins without delay."""

    async def _instant(_secs: float) -> None:
        return None

    monkeypatch.setattr(qs_mod.asyncio, "sleep", _instant)


# ---------------------------------------------------------------------------
# register_handlers
# ---------------------------------------------------------------------------


def test_register_handlers_registers_under_llm_queue(
    patched_queue_client: MagicMock,
) -> None:
    """register_handlers passes the chat/tool handler map to queue_client."""
    service = _make_service()
    service.register_handlers()

    patched_queue_client.register_handlers.assert_called_once()
    queue_arg, handlers_arg = patched_queue_client.register_handlers.call_args.args
    assert queue_arg == QUEUE_LLM
    assert set(handlers_arg) == {"chat_completion", "tool_execution"}


# ---------------------------------------------------------------------------
# queue_operation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_operation_builds_payload_and_routes_to_llm(
    patched_queue_client: MagicMock,
) -> None:
    """queue_operation builds the data payload and enqueues on the LLM queue."""
    service = _make_service()
    task_type = SimpleNamespace(value="chat")  # object exposing .value

    task_id = await service.queue_operation(
        task_type=task_type,
        operation_name="chat_completion",
        messages=[{"role": "user", "content": "hi"}],
        priority=80,
        temperature=0.5,
    )

    assert task_id == "task-123"
    kwargs = patched_queue_client.enqueue_task.await_args.kwargs
    assert kwargs["queue"] == QUEUE_LLM
    assert kwargs["operation"] == "chat_completion"
    assert kwargs["priority"] == 80
    assert kwargs["data"]["task_type"] == "chat"
    assert kwargs["data"]["temperature"] == 0.5
    assert kwargs["data"]["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_queue_operation_plain_string_task_type(
    patched_queue_client: MagicMock,
) -> None:
    """A task_type without a .value attribute is coerced to str."""
    service = _make_service()

    await service.queue_operation(
        task_type="tool",  # plain string, no .value
        operation_name="tool_execution",
    )

    kwargs = patched_queue_client.enqueue_task.await_args.kwargs
    assert kwargs["data"]["task_type"] == "tool"
    # No metadata supplied -> defaults to empty dict.
    assert kwargs["metadata"] == {}


# ---------------------------------------------------------------------------
# wait_for_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_result_completed_returns_result(
    patched_queue_client: MagicMock,
) -> None:
    """A completed task returns the stored result."""
    service = _make_service()
    started = datetime.now(UTC).isoformat()
    completed = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    patched_queue_client.get_task = AsyncMock(
        return_value={
            "status": "completed",
            "started_at": started,
            "completed_at": completed,
        }
    )

    result = await service.wait_for_result("task-1")

    assert result == {"answer": 42}
    patched_queue_client.get_result.assert_awaited_once_with("task-1")


@pytest.mark.asyncio
async def test_wait_for_result_completed_without_started_at(
    patched_queue_client: MagicMock,
) -> None:
    """A completed task missing started_at still returns the result (timing skipped)."""
    service = _make_service()
    patched_queue_client.get_task = AsyncMock(return_value={"status": "completed"})

    result = await service.wait_for_result("task-1")
    assert result == {"answer": 42}


@pytest.mark.asyncio
async def test_wait_for_result_failed_raises_operation_error(
    patched_queue_client: MagicMock,
) -> None:
    """A failed task raises OperationError with the redacted error message."""
    service = _make_service()
    patched_queue_client.get_task = AsyncMock(
        return_value={"status": "failed", "error": "redacted boom"}
    )

    with pytest.raises(OperationError, match="redacted boom"):
        await service.wait_for_result("task-1")


@pytest.mark.asyncio
async def test_wait_for_result_failed_without_error_uses_default(
    patched_queue_client: MagicMock,
) -> None:
    """A failed task with no error message falls back to the generic message."""
    service = _make_service()
    patched_queue_client.get_task = AsyncMock(return_value={"status": "failed"})

    with pytest.raises(OperationError, match="Task failed"):
        await service.wait_for_result("task-1")


@pytest.mark.asyncio
async def test_wait_for_result_cancelled_raises(
    patched_queue_client: MagicMock,
) -> None:
    """A cancelled task raises CancelledError."""
    service = _make_service()
    patched_queue_client.get_task = AsyncMock(return_value={"status": "cancelled"})

    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await service.wait_for_result("task-1")


@pytest.mark.asyncio
async def test_wait_for_result_not_found_raises_value_error(
    patched_queue_client: MagicMock,
) -> None:
    """A missing task raises ValueError."""
    service = _make_service()
    patched_queue_client.get_task = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="not found"):
        await service.wait_for_result("task-1")


@pytest.mark.asyncio
async def test_wait_for_result_running_past_timeout_raises(
    patched_queue_client: MagicMock,
) -> None:
    """A running task whose processing time exceeds the timeout raises TimeoutError."""
    service = _make_service()
    old_start = (datetime.now(UTC) - timedelta(seconds=500)).isoformat()
    patched_queue_client.get_task = AsyncMock(
        return_value={"status": "running", "started_at": old_start}
    )

    with pytest.raises(TimeoutError, match="processing timeout"):
        await service.wait_for_result("task-1", timeout=10.0)


@pytest.mark.asyncio
async def test_wait_for_result_polls_until_complete(
    patched_queue_client: MagicMock,
) -> None:
    """The loop polls through queued -> running -> completed across ticks."""
    service = _make_service()
    fresh_start = datetime.now(UTC).isoformat()
    completed = datetime.now(UTC).isoformat()
    patched_queue_client.get_task = AsyncMock(
        side_effect=[
            {"status": "queued"},
            {"status": "running", "started_at": fresh_start},
            {"status": "completed", "started_at": fresh_start, "completed_at": completed},
        ]
    )

    result = await service.wait_for_result("task-1", timeout=600.0)

    assert result == {"answer": 42}
    assert patched_queue_client.get_task.await_count == 3


@pytest.mark.asyncio
async def test_wait_for_result_uses_settings_default_timeout(
    patched_queue_client: MagicMock,
) -> None:
    """When timeout is None, the settings default (llm_operation_max) is used."""
    service = _make_service()
    # Running for less than the default 120s -> no timeout, then completes.
    fresh_start = datetime.now(UTC).isoformat()
    patched_queue_client.get_task = AsyncMock(
        side_effect=[
            {"status": "running", "started_at": fresh_start},
            {"status": "completed"},
        ]
    )

    result = await service.wait_for_result("task-1", timeout=None)
    assert result == {"answer": 42}


# ---------------------------------------------------------------------------
# thin delegators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_delegates(patched_queue_client: MagicMock) -> None:
    """cancel_task delegates to queue_client.cancel_task."""
    service = _make_service()
    assert await service.cancel_task("task-1") is True
    patched_queue_client.cancel_task.assert_awaited_once_with("task-1")


@pytest.mark.asyncio
async def test_get_task_status_delegates(patched_queue_client: MagicMock) -> None:
    """get_task_status delegates to queue_client.get_task."""
    service = _make_service()
    patched_queue_client.get_task = AsyncMock(return_value={"status": "running"})
    assert await service.get_task_status("task-1") == {"status": "running"}


@pytest.mark.asyncio
async def test_list_current_tasks_filters_active(
    patched_queue_client: MagicMock,
) -> None:
    """list_current_tasks returns only queued/running tasks."""
    service = _make_service()
    patched_queue_client.get_recent_tasks = AsyncMock(
        return_value=[
            {"id": "1", "status": "queued"},
            {"id": "2", "status": "completed"},
            {"id": "3", "status": "running"},
            {"id": "4", "status": "failed"},
        ]
    )

    active = await service.list_current_tasks(limit=50)

    assert {t["id"] for t in active} == {"1", "3"}
    patched_queue_client.get_recent_tasks.assert_awaited_once_with(limit=50, queues=[QUEUE_LLM])


@pytest.mark.asyncio
async def test_cancel_all_tasks_returns_count(
    patched_queue_client: MagicMock,
) -> None:
    """cancel_all_tasks returns the cancelled count from queue_client."""
    service = _make_service()
    result = await service.cancel_all_tasks()
    assert result == {
        "cancelled": 5,
        "message": "Task cancellation requested for LLM queue",
    }
    patched_queue_client.cancel_all_tasks.assert_awaited_once_with(QUEUE_LLM)


@pytest.mark.asyncio
async def test_clear_stats_clears_and_prunes(
    patched_queue_client: MagicMock,
) -> None:
    """clear_stats clears all stats and prunes old completed tasks."""
    service = _make_service()
    await service.clear_stats(older_than_hours=12)
    patched_queue_client.clear_all_stats.assert_awaited_once()
    patched_queue_client.clear_old_completed_tasks.assert_awaited_once_with(
        queue=None, older_than_hours=12
    )


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


def _patch_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_llm_semaphore to a stub returning fixed stats."""
    sem = MagicMock()
    sem.get_stats = MagicMock(return_value={"active_count": 1, "max_concurrent": 1})
    monkeypatch.setattr(qs_mod, "get_llm_semaphore", lambda: sem)


@pytest.mark.asyncio
async def test_get_stats_creates_stub_when_queue_empty(
    patched_queue_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the LLM queue is absent from stats, a stub entry is created."""
    _patch_semaphore(monkeypatch)
    patched_queue_client.get_all_stats = AsyncMock(return_value=[])
    patched_queue_client.get_token_stats = AsyncMock(
        return_value={
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
        }
    )

    service = _make_service()
    stats = await service.get_stats()

    assert len(stats["queues"]) == 1
    assert stats["queues"][0]["queue"] == QUEUE_LLM
    assert stats["total_queued"] == 0
    assert "semaphore_stats" in stats
    assert stats["estimated_completion_time_human"] == "0s"


@pytest.mark.asyncio
async def test_get_stats_computes_estimates_and_tokens(
    patched_queue_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_stats merges token stats, computes depth %, and estimates completion."""
    _patch_semaphore(monkeypatch)
    patched_queue_client.get_all_stats = AsyncMock(
        return_value=[
            {"queue": QUEUE_LLM, "queued": 10, "running": 1, "workers": 2},
            {"queue": QUEUE_OPERATIONS, "queued": 4, "running": 0, "workers": 1},
        ]
    )
    patched_queue_client.get_token_stats = AsyncMock(
        return_value={
            "total_input_tokens": 100,
            "total_output_tokens": 50,
            "total_tokens": 150,
            "total_cost_usd": 1.25,
        }
    )

    service = _make_service()
    stats = await service.get_stats()

    llm = stats["queues"][0]
    assert llm["max_depth"] == 100
    # (10 + 1) / 100 * 100 = 11%
    assert llm["depth_percent"] == pytest.approx(11.0)
    assert stats["total_input_tokens"] == 100
    assert stats["total_cost_usd"] == pytest.approx(1.25)
    # est = (10 queued / 2 workers) * 15s = 75s -> "1m 15s"
    assert stats["estimated_completion_time_seconds"] == 75
    assert stats["estimated_completion_times_human"]["llm"] == "1m 15s"
    # operations est = (4 / 1) * 45 = 180s -> "3m"
    assert stats["estimated_completion_times_human"]["operations"] == "3m"


@pytest.mark.asyncio
async def test_get_stats_custom_token_costs_passed(
    patched_queue_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When token-cost tracking is enabled, custom per-million costs are forwarded."""
    _patch_semaphore(monkeypatch)
    settings = _make_settings()
    settings.llm.enable_token_cost_tracking = True
    provider = MagicMock()
    service = LLMQueueService(provider=provider, settings=settings)

    patched_queue_client.get_all_stats = AsyncMock(
        return_value=[{"queue": QUEUE_LLM, "queued": 0, "running": 0, "workers": 1}]
    )

    await service.get_stats()

    # Custom costs from settings.llm were forwarded to get_token_stats.
    call = patched_queue_client.get_token_stats.await_args
    assert call.args[1] == 1.0  # token_cost_input_per_million
    assert call.args[2] == 2.0  # token_cost_output_per_million


@pytest.mark.asyncio
async def test_get_stats_hours_format_for_large_estimate(
    patched_queue_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large queue depth produces an hours/minutes formatted estimate."""
    _patch_semaphore(monkeypatch)
    patched_queue_client.get_all_stats = AsyncMock(
        return_value=[{"queue": QUEUE_LLM, "queued": 1000, "running": 0, "workers": 1}]
    )

    service = _make_service()
    stats = await service.get_stats()

    # est = 1000 * 15 = 15000s = 4h 10m
    assert stats["estimated_completion_times_human"]["llm"] == "4h 10m"
