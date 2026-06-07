# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Task 5.3: durable cancellation via SQLite + Valkey.

Verifies that:
1. cancel_task writes ChunkExtractionTask.cancelled_at to SQLite.
2. is_task_cancelled returns True when the DB record has cancelled_at set
   and the Valkey key is absent (TTL-expiry fallback).
3. is_task_cancelled returns True on Valkey hit alone (fast path preserved).
4. cancel_task uses a TTL >= settings.timeouts.llm_worker_default + 300.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LLM_WORKER_DEFAULT = 3600  # matches settings default


def _make_settings(llm_worker_default: int = _LLM_WORKER_DEFAULT) -> SimpleNamespace:
    """Return a minimal settings object with the fields cancel_task reads."""
    return SimpleNamespace(
        timeouts=SimpleNamespace(
            llm_worker_default=llm_worker_default,
            operations_result_ttl=7200,
            llm_result_ttl=3600,
        ),
        queue=SimpleNamespace(
            max_pending_queue_depth=10000,
        ),
    )


def _make_valkey(*, cancel_key_exists: bool = False) -> MagicMock:
    """Build a minimal async Valkey mock.

    Args:
        cancel_key_exists: Whether ``queue:cancel:<id>`` returns 1.
    """
    valkey = MagicMock()
    valkey.set = AsyncMock(return_value=True)
    valkey.exists = AsyncMock(return_value=1 if cancel_key_exists else 0)
    valkey.sismember = AsyncMock(return_value=True)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hset = AsyncMock(return_value=1)
    valkey.pipeline = MagicMock(return_value=MagicMock())
    return valkey


def _extraction_task_hash(task_id: str, database_name: str = "default") -> dict[str, Any]:
    """Return a decoded queue task dict for an extraction chunk task.

    Mimics the output of ``QueueClient.get_task()`` (post ``_decode_record``):
    ``data`` is already a dict, not a JSON string.
    """
    return {
        "task_id": task_id,
        "queue": "llm",
        "operation": "OP_EXTRACT_CHUNK",
        "status": "running",
        "priority": 50,
        "created_at": datetime.now(UTC).isoformat(),
        "metadata": {},
        "data": {
            "chunk_task_id": "db-task-abc",
            "job_id": "job-xyz",
            "database_name": database_name,
            "chunk_index": 0,
        },
        "attempts": 1,
    }


# ---------------------------------------------------------------------------
# Test 1 — cancel_task persists cancelled_at to SQLite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_persists_to_sqlite() -> None:
    """cancel_task must write ChunkExtractionTask.cancelled_at to the DB.

    The mock DB task has ``queue_task_id == task_id`` and is found via
    ``get_db_session``.  After the call the mock task's ``cancelled_at``
    attribute must be set to a non-None datetime.
    """
    from chaoscypher_core.queue.client import QueueClient

    task_id = "valkey-task-001"
    db_task = MagicMock()
    db_task.cancelled_at = None

    # Fake synchronous get_db_session context manager
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec = MagicMock(return_value=MagicMock(first=MagicMock(return_value=db_task)))
    mock_session.maybe_commit = MagicMock()

    valkey = _make_valkey()

    client = QueueClient()
    client.client = valkey
    client._connected = True
    client._cancel_ttl = _LLM_WORKER_DEFAULT + 300

    # Patch get_task to return a running extraction task
    client.get_task = AsyncMock(return_value=_extraction_task_hash(task_id))

    with patch(
        "chaoscypher_core.queue.client._adapter_db_session",
        return_value=mock_session,
    ):
        result = await client.cancel_task(task_id)

    assert result is True
    assert db_task.cancelled_at is not None, "cancelled_at should be set in the DB record"
    mock_session.maybe_commit.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2 — is_task_cancelled falls back to DB when Valkey key is gone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_cancelled_returns_true_when_valkey_ttl_expired() -> None:
    """When the Valkey cancel key is absent but DB has cancelled_at, return True.

    This is the TTL-expiry fallback: the 5-minute key has expired but the
    handler is still polling at t=6m. The DB record must be the authoritative
    source of truth.
    """
    from chaoscypher_core.queue.client import QueueClient

    task_id = "valkey-task-002"
    database_name = "default"

    # DB task with cancelled_at set
    db_task = MagicMock()
    db_task.cancelled_at = datetime.now(UTC)

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec = MagicMock(return_value=MagicMock(first=MagicMock(return_value=db_task)))

    # Valkey key is absent
    valkey = _make_valkey(cancel_key_exists=False)

    client = QueueClient()
    client.client = valkey
    client._connected = True

    with patch(
        "chaoscypher_core.queue.client._adapter_db_session",
        return_value=mock_session,
    ):
        result = await client.is_task_cancelled(task_id, database_name=database_name)

    assert result is True, "DB fallback must return True when cancelled_at is set"


# ---------------------------------------------------------------------------
# Test 3 — is_task_cancelled returns True from Valkey fast path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_cancelled_returns_true_when_valkey_set() -> None:
    """When the Valkey cancel key is present, return True immediately.

    No DB call should be made — the Valkey check is the fast path.
    """
    from chaoscypher_core.queue.client import QueueClient

    task_id = "valkey-task-003"

    # Valkey key is present
    valkey = _make_valkey(cancel_key_exists=True)

    client = QueueClient()
    client.client = valkey
    client._connected = True

    mock_get_db = MagicMock(side_effect=AssertionError("DB should not be called on Valkey hit"))

    with patch(
        "chaoscypher_core.queue.client._adapter_db_session",
        mock_get_db,
    ):
        result = await client.is_task_cancelled(task_id, database_name="default")

    assert result is True
    mock_get_db.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4 — cancel_task uses extended TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_uses_extended_ttl() -> None:
    """TTL passed to Valkey set() must be >= llm_worker_default + 300.

    Previously the TTL was hardcoded to 300 s (5 min). A long-running
    LLM handler can run for up to llm_worker_default seconds, so the
    cancel flag must outlive the handler.
    """
    from chaoscypher_core.queue.client import QueueClient

    task_id = "valkey-task-004"
    llm_worker_default = _LLM_WORKER_DEFAULT  # 3600

    db_task = MagicMock()
    db_task.cancelled_at = None

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec = MagicMock(return_value=MagicMock(first=MagicMock(return_value=db_task)))
    mock_session.maybe_commit = MagicMock()

    valkey = _make_valkey()

    client = QueueClient()
    client.client = valkey
    client._connected = True
    client._cancel_ttl = llm_worker_default + 300  # set by connect() from settings

    client.get_task = AsyncMock(return_value=_extraction_task_hash(task_id))

    with patch(
        "chaoscypher_core.queue.client._adapter_db_session",
        return_value=mock_session,
    ):
        await client.cancel_task(task_id)

    # Inspect the `set` call arguments for the cancel flag
    set_calls = valkey.set.call_args_list
    cancel_set_calls = [c for c in set_calls if f"queue:cancel:{task_id}" in str(c)]
    assert cancel_set_calls, "Expected a Valkey set() call for the cancel flag"

    # Verify ex keyword argument is extended
    for c in cancel_set_calls:
        ex_arg = c.kwargs.get("ex") or (c.args[2] if len(c.args) > 2 else None)
        assert ex_arg is not None, "set() must include an ex= TTL argument"
        assert ex_arg >= llm_worker_default + 300, (
            f"TTL {ex_arg} should be >= {llm_worker_default + 300}"
        )


# ---------------------------------------------------------------------------
# Test 5 — cancel_tasks_batch persists cancelled_at to SQLite for all tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_tasks_batch_persists_to_sqlite() -> None:
    """cancel_tasks_batch must set cancelled_at on every ChunkExtractionTask in the batch.

    Verifies the parity gap between single-task and batch cancellation:
    after commit 84ee9ec5 the single-task path gained durable SQLite
    persistence but cancel_tasks_batch did not.  Past the Valkey TTL,
    is_task_cancelled would return False for bulk-cancelled tasks.

    Seeds three ChunkExtractionTask mocks across two databases and asserts
    that all three have non-None cancelled_at after the batch call.
    """
    from chaoscypher_core.queue.client import QueueClient

    # Three tasks across two databases
    task_id_1 = "batch-task-001"
    task_id_2 = "batch-task-002"
    task_id_3 = "batch-task-003"

    db_task_1 = MagicMock()
    db_task_1.cancelled_at = None
    db_task_2 = MagicMock()
    db_task_2.cancelled_at = None
    db_task_3 = MagicMock()
    db_task_3.cancelled_at = None

    # Map task_id -> (db_task_mock, database_name)
    task_map = {
        task_id_1: (db_task_1, "db_alpha"),
        task_id_2: (db_task_2, "db_alpha"),
        task_id_3: (db_task_3, "db_beta"),
    }

    # get_task returns appropriately shaped hashes
    async def _fake_get_task(tid: str) -> dict[str, Any]:
        _, db_name = task_map[tid]
        return _extraction_task_hash(tid, database_name=db_name)

    # get_db_session must return the right mock session per database_name.
    # _persist_cancellation_to_db calls session.exec(...).first() to fetch
    # the row; we wire each session to return the correct db_task mock.
    def _make_session_for(db_name: str) -> MagicMock:
        matching_tasks = [dt for tid, (dt, dn) in task_map.items() if dn == db_name]

        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.maybe_commit = MagicMock()

        # exec() is called once per task_id lookup; return each matching task
        # in sequence so both tasks for db_alpha are resolved correctly.
        first_results = iter([MagicMock(first=MagicMock(return_value=t)) for t in matching_tasks])
        session.exec = MagicMock(side_effect=lambda _stmt: next(first_results))
        return session

    sessions: dict[str, MagicMock] = {
        "db_alpha": _make_session_for("db_alpha"),
        "db_beta": _make_session_for("db_beta"),
    }

    def _get_db_session(db_name: str) -> MagicMock:
        return sessions[db_name]

    # Valkey pipeline mock
    pipeline_mock = MagicMock()
    pipeline_mock.hset = MagicMock()
    pipeline_mock.zrem = MagicMock()
    pipeline_mock.execute = AsyncMock(return_value=[1, 1, 1, 1, 1, 1])

    valkey = _make_valkey()
    valkey.pipeline = MagicMock(return_value=pipeline_mock)

    client = QueueClient()
    client.client = valkey
    client._connected = True
    client._cancel_ttl = _LLM_WORKER_DEFAULT + 300
    client.get_task = AsyncMock(side_effect=_fake_get_task)

    with patch(
        "chaoscypher_core.queue.client._adapter_db_session",
        side_effect=_get_db_session,
    ):
        result = await client.cancel_tasks_batch([task_id_1, task_id_2, task_id_3])

    assert result["cancelled"] == 3, f"Expected 3 cancelled, got {result}"
    assert result["failed"] == []

    # All three DB task rows must have been stamped
    assert db_task_1.cancelled_at is not None, "task 1 cancelled_at not persisted"
    assert db_task_2.cancelled_at is not None, "task 2 cancelled_at not persisted"
    assert db_task_3.cancelled_at is not None, "task 3 cancelled_at not persisted"
