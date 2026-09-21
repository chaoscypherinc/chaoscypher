# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the stuck-chat sweeper (`reconcile_stuck_chats`).

Entry 812: the sweeper's only liveness signal used to be
``chat.updated_at``, which nothing bumps during a turn, checked against
a 300s threshold while the worker has a 3600s budget per turn — a
healthy in-flight turn running past 5 minutes got flipped to "error"
while the worker was still processing it. The fix consults the queue
for a live ``chat_background`` task before declaring a chat dead,
falling back to the timestamp check only when there's no queue
evidence either way.

These are this function's first tests — it previously had zero test
references.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.adapters.sqlite.models import Chat
from chaoscypher_core.services.chat.recovery import (
    DEFAULT_STUCK_THRESHOLD_SECONDS,
    reconcile_stuck_chats,
)


def _seed_processing_chat(
    adapter: Any,
    *,
    chat_id: str,
    age_seconds: int,
) -> None:
    """Seed a chat in 'processing' status with a controlled updated_at age.

    Args:
        adapter: Connected SqliteAdapter (in_memory_adapter fixture).
        chat_id: Id for the new chat row.
        age_seconds: How far in the past to backdate updated_at, simulating
            a turn that has been running (or has been dead) this long.
    """
    adapter.create_chat(
        {
            "id": chat_id,
            "database_name": adapter.database_name,
            "title": "test chat",
            "status": "processing",
        }
    )
    # create_chat's default_factory stamps "now" — backdate it directly on
    # the row, mirroring the SourceRow-backdating pattern used by the
    # sibling sources/test_recovery_robustness.py tests.
    assert adapter.session is not None
    row = adapter.session.get(Chat, chat_id)
    assert row is not None
    row.updated_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    adapter.session.add(row)
    adapter.session.commit()


# ============================================================
# (a) Live queue task survives the sweep
# ============================================================


@pytest.mark.asyncio
async def test_processing_chat_with_live_task_survives_sweep(in_memory_adapter) -> None:
    """A chat well past the 300s threshold is NOT flipped when the queue
    reports a live task for its turn — this is the core bug fix: the old
    code only ever looked at ``updated_at``, which nothing bumps mid-turn,
    so any turn running longer than 5 minutes (out of a 3600s worker
    budget) got killed regardless of whether the worker was still on it.
    """
    queue = AsyncMock()
    queue.task_exists_for_chat = AsyncMock(return_value=True)

    # Old enough to be a stuck-sweep candidate under the timestamp check
    # alone, but well inside the worker's 3600s turn budget.
    _seed_processing_chat(
        in_memory_adapter,
        chat_id="chat-alive",
        age_seconds=DEFAULT_STUCK_THRESHOLD_SECONDS + 60,
    )

    recovered = await reconcile_stuck_chats(
        in_memory_adapter,
        in_memory_adapter.database_name,
        queue_client=queue,
    )

    assert recovered == 0
    chat = in_memory_adapter.get_chat("chat-alive", in_memory_adapter.database_name)
    assert chat is not None
    assert chat["status"] == "processing"
    queue.task_exists_for_chat.assert_awaited_once_with(
        chat_id="chat-alive",
        database_name=in_memory_adapter.database_name,
    )


# ============================================================
# (b) Genuinely dead chat still flips
# ============================================================


@pytest.mark.asyncio
async def test_genuinely_dead_chat_still_flips_to_error(in_memory_adapter) -> None:
    """A stuck chat with NO live queue task still gets recovered.

    This is the sweeper's original job — a worker that crashed mid-turn
    leaves the chat wedged in "processing" forever unless something
    moves it to "error". The liveness check must not defeat that.
    """
    queue = AsyncMock()
    queue.task_exists_for_chat = AsyncMock(return_value=False)

    _seed_processing_chat(
        in_memory_adapter,
        chat_id="chat-dead",
        age_seconds=DEFAULT_STUCK_THRESHOLD_SECONDS + 60,
    )

    recovered = await reconcile_stuck_chats(
        in_memory_adapter,
        in_memory_adapter.database_name,
        queue_client=queue,
    )

    assert recovered == 1
    chat = in_memory_adapter.get_chat("chat-dead", in_memory_adapter.database_name)
    assert chat is not None
    assert chat["status"] == "error"


# ============================================================
# Fresh chats are never touched, live task or not
# ============================================================


@pytest.mark.asyncio
async def test_fresh_processing_chat_untouched_regardless_of_queue(in_memory_adapter) -> None:
    """A chat inside the threshold window is skipped without even
    consulting the queue — it isn't a stuck-sweep candidate yet.
    """
    queue = AsyncMock()
    queue.task_exists_for_chat = AsyncMock(return_value=False)

    _seed_processing_chat(
        in_memory_adapter,
        chat_id="chat-fresh",
        age_seconds=5,
    )

    recovered = await reconcile_stuck_chats(
        in_memory_adapter,
        in_memory_adapter.database_name,
        queue_client=queue,
    )

    assert recovered == 0
    chat = in_memory_adapter.get_chat("chat-fresh", in_memory_adapter.database_name)
    assert chat is not None
    assert chat["status"] == "processing"
    queue.task_exists_for_chat.assert_not_awaited()


# ============================================================
# Queue unreachable: fail toward leaving the chat alone
# ============================================================


@pytest.mark.asyncio
async def test_queue_error_does_not_flip_healthy_chat(in_memory_adapter) -> None:
    """A Valkey blip during the liveness scan must not flip a chat to
    "error" out from under a possibly-still-running worker. This is the
    flip-flop harm the entry calls out: sweeper writes "error", the
    worker's own completion path later overwrites it with "active", and
    a user retry in between double-enqueues the turn.
    """
    queue = AsyncMock()
    queue.task_exists_for_chat = AsyncMock(side_effect=ConnectionError("valkey unreachable"))

    _seed_processing_chat(
        in_memory_adapter,
        chat_id="chat-queue-error",
        age_seconds=DEFAULT_STUCK_THRESHOLD_SECONDS + 60,
    )

    recovered = await reconcile_stuck_chats(
        in_memory_adapter,
        in_memory_adapter.database_name,
        queue_client=queue,
    )

    assert recovered == 0
    chat = in_memory_adapter.get_chat("chat-queue-error", in_memory_adapter.database_name)
    assert chat is not None
    assert chat["status"] == "processing"


# ============================================================
# No queue_client wired: falls back to the plain timestamp check
# ============================================================


@pytest.mark.asyncio
async def test_no_queue_client_falls_back_to_timestamp_check(in_memory_adapter) -> None:
    """Without a queue_client at all (offline/CLI-only context), the
    sweeper falls back to its original timestamp-only behavior rather
    than refusing to ever recover anything.
    """
    _seed_processing_chat(
        in_memory_adapter,
        chat_id="chat-no-queue",
        age_seconds=DEFAULT_STUCK_THRESHOLD_SECONDS + 60,
    )

    recovered = await reconcile_stuck_chats(
        in_memory_adapter,
        in_memory_adapter.database_name,
    )

    assert recovered == 1
    chat = in_memory_adapter.get_chat("chat-no-queue", in_memory_adapter.database_name)
    assert chat is not None
    assert chat["status"] == "error"


# ============================================================
# Blocking adapter I/O stays off the event-loop thread
# ============================================================


@pytest.mark.asyncio
async def test_adapter_calls_run_off_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sweeper runs on the Cortex API event loop; its blocking SQLite
    adapter calls must be offloaded via ``asyncio.to_thread`` so a
    10,000-row scan plus per-chat writes cannot stall request handling.

    ``event_bus.emit`` is in that set and was the one call left inline. It
    is not a log line: ``record_system_event`` is an INSERT plus a commit,
    then a ``SELECT COUNT(*)`` and a conditional prune DELETE plus a second
    commit — two commits whose busy-retry backoff sleeps synchronously, once
    per recovered chat, on the loop serving every ``/api/`` request. Its
    ``except Exception`` swallow does not help: that covers failures, not the
    latency of a *successful* commit that sits in ``time.sleep`` under
    SQLITE_BUSY.
    """
    loop_thread = threading.get_ident()
    call_threads: dict[str, int] = {}

    class _RecordingAdapter:
        def list_chats(self, database_name: str, **kwargs: Any) -> list[dict[str, Any]]:
            call_threads["list_chats"] = threading.get_ident()
            stuck = datetime.now(UTC) - timedelta(seconds=DEFAULT_STUCK_THRESHOLD_SECONDS + 60)
            return [{"id": "chat-threaded", "updated_at": stuck}]

        def mark_chat_error_if_processing(self, chat_id: str) -> bool:
            call_threads["mark_chat_error_if_processing"] = threading.get_ident()
            return True

    def _recording_emit(*_args: Any, **_kwargs: Any) -> None:
        call_threads["event_bus.emit"] = threading.get_ident()

    monkeypatch.setattr("chaoscypher_core.services.chat.recovery.event_bus.emit", _recording_emit)

    adapter = cast("Any", _RecordingAdapter())
    recovered = await reconcile_stuck_chats(adapter, "threading-db")

    assert recovered == 1
    assert call_threads["list_chats"] != loop_thread
    assert call_threads["mark_chat_error_if_processing"] != loop_thread
    assert call_threads["event_bus.emit"] != loop_thread


# ============================================================
# The flip is a CAS: completed-during-sweep chats stay 'active'
# ============================================================


@pytest.mark.asyncio
async def test_chat_completing_during_sweep_keeps_active_status(in_memory_adapter) -> None:
    """A worker finishing between the snapshot and the flip must win.

    The sweeper snapshots up to 10k processing rows, then runs the
    liveness gate per chat; a turn that completes in that window sets
    'active'. A blind update_chat stamped 'error' over the finished
    answer — the guarded CAS must lose the race instead.
    """
    _seed_processing_chat(
        in_memory_adapter,
        chat_id="chat-finishing",
        age_seconds=DEFAULT_STUCK_THRESHOLD_SECONDS + 60,
    )

    queue = AsyncMock()

    async def _task_gone_and_chat_completed(**_kwargs: Any) -> bool:
        # Model the worker completing exactly in the gate window: the
        # queue task is gone AND the row now reads 'active'.
        in_memory_adapter.update_chat("chat-finishing", {"status": "active"})
        return False

    queue.task_exists_for_chat = AsyncMock(side_effect=_task_gone_and_chat_completed)

    recovered = await reconcile_stuck_chats(
        in_memory_adapter,
        in_memory_adapter.database_name,
        queue_client=queue,
    )

    assert recovered == 0
    chat = in_memory_adapter.get_chat("chat-finishing", in_memory_adapter.database_name)
    assert chat is not None
    assert chat["status"] == "active", (
        "sweeper stamped 'error' over a successfully-completed answer"
    )


@pytest.mark.asyncio
async def test_mark_chat_error_if_processing_is_guarded(in_memory_adapter) -> None:
    """Mixin-level contract: flips only processing rows, reports the race."""
    _seed_processing_chat(in_memory_adapter, chat_id="chat-cas", age_seconds=10)

    assert in_memory_adapter.mark_chat_error_if_processing("chat-cas") is True
    chat = in_memory_adapter.get_chat("chat-cas", in_memory_adapter.database_name)
    assert chat is not None and chat["status"] == "error"

    # Already error -> guard refuses; unknown id -> refuses.
    assert in_memory_adapter.mark_chat_error_if_processing("chat-cas") is False
    assert in_memory_adapter.mark_chat_error_if_processing("nope") is False
