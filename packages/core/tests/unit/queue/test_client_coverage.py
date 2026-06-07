# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage tests for ``queue/client.py``.

These exercise the QueueClient facade end-to-end against a recording fake
``valkey.asyncio`` backend, mirroring the ``QueueClient.__new__`` + manual-attr
+ recording-MagicMock/AsyncMock pattern established in
``test_correlation_id.py`` and ``test_failed_task_retention.py``.

Covered surfaces:
- ``connect`` — disabled-via-settings, ping failure, success path.
- ``_check_queue_depth`` — QueueFullError at the limit; unavailable guard.
- ``enqueue`` — result_ttl defaulting + depth check.
- ``enqueue_tasks_batch`` — empty → [], over-limit → QueueFullError.
- ``get_task`` / ``get_recent_tasks`` / ``get_result`` — None & decode paths.
- ``cancel_task`` — per-status branches (not-found, terminal, queued,
  running-in-set, orphaned-running, unknown).
- ``_persist_cancellation_to_db`` — no-op without db, exception swallowed.
- ``retry_task`` — ValueError when not failed, re-enqueue with retried_from.
- ``task_exists_for_source`` — match + scan-exception re-raise.
- ``in_flight_chunk_task_ids`` — populated set + empty-on-error.
- Heartbeat primitives — QueueUnavailableError when client None.
- ``complete_task_atomic`` — lazy script_load → evalsha + SHA caching.
- ``_decode_record`` — bytes/str + error masking.
- ``is_task_cancelled`` — fast-path / DB-fallback / no-client.
- ``_require_connection`` / ``is_available`` / ``is_enabled`` guards.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS
from chaoscypher_core.exceptions import QueueFullError
from chaoscypher_core.queue.client import QueueClient, QueueUnavailableError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_recorder() -> tuple[MagicMock, list[dict[str, Any]]]:
    """Build a fake pipeline that records hset mapping dicts."""
    recorded: list[dict[str, Any]] = []

    pipeline = MagicMock()

    def _hset(key: str, mapping: dict[str, Any]) -> MagicMock:
        recorded.append({"key": key, "mapping": dict(mapping)})
        return pipeline

    pipeline.hset.side_effect = _hset
    pipeline.zadd.return_value = pipeline
    pipeline.zrem.return_value = pipeline
    pipeline.lpush.return_value = pipeline
    pipeline.ltrim.return_value = pipeline
    pipeline.delete.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[])

    return pipeline, recorded


def _bare_client() -> QueueClient:
    """Construct a QueueClient via __new__ with the manual attribute set."""
    client = QueueClient.__new__(QueueClient)
    client.client = None
    client._connected = True
    client._enabled = True
    client._max_pending_queue_depth = 10000
    client._operations_result_ttl = 7200
    client._llm_result_ttl = 3600
    client._failed_result_ttl = 14 * 86_400
    client._cancel_ttl = 300
    client._atomic_complete_sha = None
    client.monitor = None
    client._handlers = {}
    client._retry_policy = {}
    client._transient_retry_policy = {}
    client._queues = set()
    return client


def _make_queue_client() -> tuple[QueueClient, MagicMock, list[dict[str, Any]]]:
    """Build a connected QueueClient with a recording fake Valkey backend."""
    client = _bare_client()

    pipeline, recorded = _make_pipeline_recorder()

    valkey = MagicMock()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.pipeline = MagicMock(return_value=pipeline)
    valkey.hgetall = AsyncMock(return_value={})
    valkey.get = AsyncMock(return_value=None)
    valkey.set = AsyncMock(return_value=True)
    valkey.exists = AsyncMock(return_value=0)
    valkey.sismember = AsyncMock(return_value=0)
    valkey.srem = AsyncMock(return_value=1)
    valkey.hset = AsyncMock(return_value=1)
    valkey.expire = AsyncMock(return_value=True)
    valkey.delete = AsyncMock(return_value=1)
    valkey.script_load = AsyncMock(return_value="sha-123")
    valkey.evalsha = AsyncMock(return_value=1)

    client.client = valkey
    return client, valkey, recorded


def _hash_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "task_id": "t-1",
        "queue": QUEUE_OPERATIONS,
        "operation": "test_op",
        "status": "queued",
        "priority": "50",
        "created_at": "2026-05-23T00:00:00Z",
        "data": json.dumps({}),
        "metadata": json.dumps({}),
        "result_ttl": "3600",
        "attempts": "0",
        "payload_version": "1",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Fake settings for connect()
# ---------------------------------------------------------------------------


def _make_settings(*, enabled: bool = True) -> MagicMock:
    settings = MagicMock()
    settings.llm.enable_llm_queueing = enabled
    settings.queue.queue_host = "localhost"
    settings.queue.queue_port = 6379
    settings.queue.queue_database = 0
    settings.queue.queue_password = None
    settings.queue.queue_ssl = False
    settings.queue.max_pending_queue_depth = 5000
    settings.timeouts.operations_result_ttl = 1111
    settings.timeouts.llm_result_ttl = 2222
    settings.timeouts.failed_result_ttl = 3333
    settings.timeouts.llm_worker_default = 600
    return settings


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_disabled_via_settings_returns_false() -> None:
    """When queueing is disabled, connect() short-circuits to False and _enabled=False."""
    client = _bare_client()
    settings = _make_settings(enabled=False)

    result = await client.connect(settings)

    assert result is False
    assert client._enabled is False
    assert client.client is None


@pytest.mark.asyncio
async def test_connect_ping_failure_returns_false_and_nulls_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection error on ping leaves client=None and returns False."""
    from valkey.exceptions import ConnectionError as ValkeyConnectionError

    fake_conn = MagicMock()
    fake_conn.ping = AsyncMock(side_effect=ValkeyConnectionError("refused"))

    monkeypatch.setattr(
        "chaoscypher_core.queue.client.Valkey",
        MagicMock(return_value=fake_conn),
    )

    client = _bare_client()
    result = await client.connect(_make_settings())

    assert result is False
    assert client.client is None


@pytest.mark.asyncio
async def test_connect_success_stores_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful ping wires up the monitor and pulls TTLs from settings."""
    fake_conn = MagicMock()
    fake_conn.ping = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "chaoscypher_core.queue.client.Valkey",
        MagicMock(return_value=fake_conn),
    )

    client = _bare_client()
    client._queues = {QUEUE_OPERATIONS}
    settings = _make_settings()

    result = await client.connect(settings)

    assert result is True
    assert client._connected is True
    assert client._enabled is True
    assert client.monitor is not None
    assert client._operations_result_ttl == 1111
    assert client._llm_result_ttl == 2222
    assert client._failed_result_ttl == 3333
    assert client._cancel_ttl == 600 + 300
    assert client._max_pending_queue_depth == 5000


# ---------------------------------------------------------------------------
# _check_queue_depth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_queue_depth_raises_at_limit() -> None:
    """At/over the configured depth, _check_queue_depth raises QueueFullError."""
    client, valkey, _ = _make_queue_client()
    client._max_pending_queue_depth = 10
    valkey.zcard = AsyncMock(return_value=10)

    with pytest.raises(QueueFullError):
        await client._check_queue_depth(QUEUE_OPERATIONS)


@pytest.mark.asyncio
async def test_check_queue_depth_unavailable_when_client_none() -> None:
    """_check_queue_depth raises QueueUnavailableError when not connected."""
    client = _bare_client()
    with pytest.raises(QueueUnavailableError):
        await client._check_queue_depth(QUEUE_OPERATIONS)


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_defaults_operations_result_ttl() -> None:
    """An operations enqueue with no result_ttl uses the operations default."""
    client, _, recorded = _make_queue_client()

    task_id = await client.enqueue(
        queue=QUEUE_OPERATIONS,
        operation="op",
        data={"x": 1},
    )

    assert task_id
    assert recorded[0]["mapping"]["result_ttl"] == "7200"


@pytest.mark.asyncio
async def test_enqueue_defaults_llm_result_ttl() -> None:
    """An llm enqueue with no result_ttl uses the llm default."""
    client, _, recorded = _make_queue_client()

    await client.enqueue(queue=QUEUE_LLM, operation="op", data={})

    assert recorded[0]["mapping"]["result_ttl"] == "3600"


@pytest.mark.asyncio
async def test_enqueue_depth_check_raises_full() -> None:
    """Enqueue runs the depth gate and surfaces QueueFullError."""
    client, valkey, _ = _make_queue_client()
    client._max_pending_queue_depth = 1
    valkey.zcard = AsyncMock(return_value=1)

    with pytest.raises(QueueFullError):
        await client.enqueue(queue=QUEUE_OPERATIONS, operation="op", data={})


# ---------------------------------------------------------------------------
# enqueue_tasks_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_tasks_batch_empty_returns_empty() -> None:
    """An empty batch returns [] without touching the backend."""
    client, _, _ = _make_queue_client()
    assert await client.enqueue_tasks_batch(QUEUE_OPERATIONS, []) == []


@pytest.mark.asyncio
async def test_enqueue_tasks_batch_over_limit_raises() -> None:
    """A batch that would exceed the depth cap raises QueueFullError."""
    client, valkey, _ = _make_queue_client()
    client._max_pending_queue_depth = 2
    valkey.zcard = AsyncMock(return_value=2)

    with pytest.raises(QueueFullError):
        await client.enqueue_tasks_batch(
            QUEUE_OPERATIONS,
            [{"operation": "o", "data": {}, "priority": 50, "metadata": {}}],
        )


@pytest.mark.asyncio
async def test_enqueue_tasks_batch_returns_ids() -> None:
    """A batch under the cap returns one id per task and records each hash."""
    client, _, recorded = _make_queue_client()

    ids = await client.enqueue_tasks_batch(
        QUEUE_OPERATIONS,
        [
            {"operation": "o1", "data": {}, "priority": 50, "metadata": {}},
            {"operation": "o2", "data": {}, "priority": 50, "metadata": {}},
        ],
    )

    assert len(ids) == 2
    assert len(recorded) == 2


# ---------------------------------------------------------------------------
# get_task / get_recent_tasks / get_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_none_when_client_missing() -> None:
    """get_task returns None when there is no client."""
    client = _bare_client()
    assert await client.get_task("t-x") is None


@pytest.mark.asyncio
async def test_get_task_none_when_record_empty() -> None:
    """get_task returns None when the hash is empty."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value={})
    assert await client.get_task("t-x") is None


@pytest.mark.asyncio
async def test_get_task_decodes_record() -> None:
    """get_task decodes the raw bytes hash into a typed dict."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(task_id="t-7", priority="99"))

    task = await client.get_task("t-7")

    assert task is not None
    assert task["task_id"] == "t-7"
    assert task["priority"] == 99


@pytest.mark.asyncio
async def test_get_recent_tasks_empty_without_client() -> None:
    """get_recent_tasks returns [] without a client."""
    client = _bare_client()
    assert await client.get_recent_tasks() == []


@pytest.mark.asyncio
async def test_get_recent_tasks_global_decode() -> None:
    """get_recent_tasks reads the global recent list then decodes each hash."""
    client, valkey, _ = _make_queue_client()
    valkey.lrange = AsyncMock(return_value=[b"t-a"])
    pipeline = MagicMock()
    pipeline.hgetall.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[_hash_payload(task_id="t-a")])
    valkey.pipeline = MagicMock(return_value=pipeline)

    tasks = await client.get_recent_tasks()

    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "t-a"


@pytest.mark.asyncio
async def test_get_recent_tasks_specific_queue_empty_ids() -> None:
    """A per-queue lookup that yields no ids returns [] early."""
    client, valkey, _ = _make_queue_client()
    valkey.lrange = AsyncMock(return_value=[])

    assert await client.get_recent_tasks(queues=[QUEUE_OPERATIONS]) == []


@pytest.mark.asyncio
async def test_get_result_none_when_missing() -> None:
    """get_result returns None when the payload key is absent."""
    client, valkey, _ = _make_queue_client()
    valkey.get = AsyncMock(return_value=None)
    assert await client.get_result("t-x") is None


@pytest.mark.asyncio
async def test_get_result_decodes_json() -> None:
    """get_result JSON-decodes the stored payload."""
    client, valkey, _ = _make_queue_client()
    valkey.get = AsyncMock(return_value=json.dumps({"answer": 42}))
    assert await client.get_result("t-x") == {"answer": 42}


@pytest.mark.asyncio
async def test_get_result_none_without_client() -> None:
    """get_result returns None when there is no client."""
    client = _bare_client()
    assert await client.get_result("t-x") is None


# ---------------------------------------------------------------------------
# cancel_task — per-status branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task_not_found_returns_false() -> None:
    """Cancelling a missing task returns False."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value={})
    assert await client.cancel_task("missing") is False


@pytest.mark.asyncio
async def test_cancel_task_terminal_returns_true() -> None:
    """Cancelling a task already in a terminal state returns True (no-op)."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="completed"))
    assert await client.cancel_task("t-done") is True


@pytest.mark.asyncio
async def test_cancel_task_queued_zrem_and_hset() -> None:
    """A queued task is removed from pending and marked cancelled via pipeline."""
    client, valkey, recorded = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="queued"))

    assert await client.cancel_task("t-q") is True
    # The cancellation hset goes through the pipeline recorder.
    assert any(e["mapping"].get("status") == "cancelled" for e in recorded)


@pytest.mark.asyncio
async def test_cancel_task_running_in_set_sets_flag_and_srem() -> None:
    """A running task in the running set gets the cancel flag, SREM, and DB persist."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="running", data=json.dumps({})))
    valkey.sismember = AsyncMock(return_value=1)
    client._persist_cancellation_to_db = MagicMock()

    assert await client.cancel_task("t-run") is True
    valkey.set.assert_awaited()  # cancel flag set
    valkey.srem.assert_awaited()
    client._persist_cancellation_to_db.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_task_orphaned_running_marks_cancelled() -> None:
    """A running task NOT in the running set is just marked cancelled."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="running"))
    valkey.sismember = AsyncMock(return_value=0)

    assert await client.cancel_task("t-orphan") is True
    valkey.set.assert_not_awaited()  # no cancel flag — orphan path
    valkey.hset.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_task_unknown_status_marks_cancelled() -> None:
    """An unrecognised status falls through to a best-effort cancel mark."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="weird"))

    assert await client.cancel_task("t-weird") is True
    valkey.hset.assert_awaited()


# ---------------------------------------------------------------------------
# _persist_cancellation_to_db
# ---------------------------------------------------------------------------


def test_persist_cancellation_no_db_name_noop() -> None:
    """Without a database_name in data, the DB persist is a silent no-op."""
    client = _bare_client()
    # No raise, no exception — task without database_name in data.
    client._persist_cancellation_to_db("t-1", {"data": {}})


def test_persist_cancellation_swallows_db_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DB error during persistence is caught and logged, not raised."""
    import chaoscypher_core.queue.client as client_mod

    @contextlib.contextmanager
    def _boom(_db_name: str):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(client_mod, "_adapter_db_session", _boom)

    client = _bare_client()
    # Should not raise despite the DB failing.
    client._persist_cancellation_to_db("t-1", {"data": {"database_name": "default"}})


# ---------------------------------------------------------------------------
# retry_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_task_not_found_returns_none() -> None:
    """retry_task returns None when the task hash is missing."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value={})
    assert await client.retry_task("gone") is None


@pytest.mark.asyncio
async def test_retry_task_not_failed_raises_valueerror() -> None:
    """retry_task refuses any non-failed task with ValueError."""
    client, valkey, _ = _make_queue_client()
    valkey.hgetall = AsyncMock(return_value=_hash_payload(status="running"))
    with pytest.raises(ValueError, match="must be 'failed'"):
        await client.retry_task("t-run")


@pytest.mark.asyncio
async def test_retry_task_reenqueues_with_retried_from() -> None:
    """A failed task re-enqueues, stamping retried_from in the new metadata."""
    client, valkey, recorded = _make_queue_client()
    valkey.hgetall = AsyncMock(
        return_value=_hash_payload(
            task_id="t-old",
            status="failed",
            metadata=json.dumps({"source_id": "s1"}),
        )
    )

    new_id = await client.retry_task("t-old")

    assert new_id
    # The enqueue path recorded a new hash carrying retried_from.
    meta = json.loads(recorded[0]["mapping"]["metadata"])
    assert meta["retried_from"] == "t-old"
    assert meta["source_id"] == "s1"


# ---------------------------------------------------------------------------
# task_exists_for_source
# ---------------------------------------------------------------------------


def _scan_iter(keys: list[bytes]) -> Any:
    """Build an async-iterator factory matching client.scan_iter(match=...)."""

    def _factory(*_args: Any, **_kwargs: Any) -> Any:
        async def _gen() -> Any:
            for k in keys:
                yield k

        return _gen()

    return _factory


@pytest.mark.asyncio
async def test_task_exists_for_source_match() -> None:
    """A queued task whose metadata matches the source returns True."""
    client, valkey, _ = _make_queue_client()
    valkey.scan_iter = _scan_iter([b"queue:task:t-match"])
    valkey.hgetall = AsyncMock(
        return_value=_hash_payload(
            task_id="t-match",
            operation="extract_chunk",
            status="queued",
            metadata=json.dumps({"source_id": "s1", "database_name": "default"}),
        )
    )

    result = await client.task_exists_for_source(
        source_id="s1",
        database_name="default",
        operations=["extract_chunk"],
    )
    assert result is True


@pytest.mark.asyncio
async def test_task_exists_for_source_no_client_returns_false() -> None:
    """No client → returns False (no info)."""
    client = _bare_client()
    result = await client.task_exists_for_source(
        source_id="s1", database_name="d", operations=["op"]
    )
    assert result is False


@pytest.mark.asyncio
async def test_task_exists_for_source_scan_exception_reraises() -> None:
    """A scan failure re-raises so SourceRecovery can skip this pass."""
    client, valkey, _ = _make_queue_client()

    def _boom_factory(*_a: Any, **_k: Any) -> Any:
        async def _gen() -> Any:
            raise RuntimeError("scan blip")
            yield  # pragma: no cover

        return _gen()

    valkey.scan_iter = _boom_factory

    with pytest.raises(RuntimeError, match="scan blip"):
        await client.task_exists_for_source(source_id="s1", database_name="d", operations=["op"])


# ---------------------------------------------------------------------------
# in_flight_chunk_task_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_flight_chunk_task_ids_collects_set() -> None:
    """Collects chunk_task_id from each matching queued/running EXTRACT_CHUNK task."""
    client, valkey, _ = _make_queue_client()
    valkey.scan_iter = _scan_iter([b"queue:task:t-c"])
    valkey.hgetall = AsyncMock(
        return_value=_hash_payload(
            task_id="t-c",
            operation="extract_chunk",
            status="running",
            data=json.dumps({"chunk_task_id": "chunk-42"}),
            metadata=json.dumps({"source_id": "s1", "database_name": "default"}),
        )
    )

    ids = await client.in_flight_chunk_task_ids(source_id="s1", database_name="default")
    assert ids == {"chunk-42"}


@pytest.mark.asyncio
async def test_in_flight_chunk_task_ids_empty_on_error() -> None:
    """Any scan exception yields an empty set (treated as 'no information')."""
    client, valkey, _ = _make_queue_client()

    def _boom_factory(*_a: Any, **_k: Any) -> Any:
        async def _gen() -> Any:
            raise RuntimeError("scan down")
            yield  # pragma: no cover

        return _gen()

    valkey.scan_iter = _boom_factory

    ids = await client.in_flight_chunk_task_ids(source_id="s1", database_name="default")
    assert ids == set()


@pytest.mark.asyncio
async def test_in_flight_chunk_task_ids_no_client_empty() -> None:
    """No client → empty set."""
    client = _bare_client()
    ids = await client.in_flight_chunk_task_ids(source_id="s1", database_name="d")
    assert ids == set()


# ---------------------------------------------------------------------------
# Heartbeat primitives — unavailable guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_heartbeat_requires_client() -> None:
    client = _bare_client()
    with pytest.raises(QueueUnavailableError):
        await client.set_heartbeat("t", 30)


@pytest.mark.asyncio
async def test_refresh_heartbeat_requires_client() -> None:
    client = _bare_client()
    with pytest.raises(QueueUnavailableError):
        await client.refresh_heartbeat("t", 30)


@pytest.mark.asyncio
async def test_delete_heartbeat_requires_client() -> None:
    client = _bare_client()
    with pytest.raises(QueueUnavailableError):
        await client.delete_heartbeat("t")


@pytest.mark.asyncio
async def test_heartbeat_exists_requires_client() -> None:
    client = _bare_client()
    with pytest.raises(QueueUnavailableError):
        await client.heartbeat_exists("t")


@pytest.mark.asyncio
async def test_heartbeat_primitives_happy_path() -> None:
    """The heartbeat helpers issue the expected key operations when connected."""
    client, valkey, _ = _make_queue_client()
    await client.set_heartbeat("t", 30)
    await client.refresh_heartbeat("t", 30)
    await client.delete_heartbeat("t")
    exists = await client.heartbeat_exists("t")

    valkey.set.assert_awaited()
    valkey.expire.assert_awaited()
    valkey.delete.assert_awaited()
    assert exists is False  # exists returned 0


@pytest.mark.asyncio
async def test_mark_task_failed_terminal_requires_client() -> None:
    client = _bare_client()
    with pytest.raises(QueueUnavailableError):
        await client.mark_task_failed_terminal("t", {"status": "failed"})


# ---------------------------------------------------------------------------
# complete_task_atomic — lazy script_load → evalsha
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_task_atomic_loads_then_caches_sha() -> None:
    """First call loads the Lua script; subsequent calls reuse the cached SHA."""
    client, valkey, _ = _make_queue_client()
    assert client._atomic_complete_sha is None

    await client.complete_task_atomic(QUEUE_OPERATIONS, "t-1")
    valkey.script_load.assert_awaited_once()
    valkey.evalsha.assert_awaited_once()
    assert client._atomic_complete_sha == "sha-123"

    await client.complete_task_atomic(QUEUE_OPERATIONS, "t-2")
    # No second script_load — SHA cached.
    valkey.script_load.assert_awaited_once()
    assert valkey.evalsha.await_count == 2


@pytest.mark.asyncio
async def test_complete_task_atomic_requires_client() -> None:
    client = _bare_client()
    with pytest.raises(QueueUnavailableError):
        await client.complete_task_atomic(QUEUE_OPERATIONS, "t")


# ---------------------------------------------------------------------------
# _decode_record
# ---------------------------------------------------------------------------


def test_decode_record_handles_bytes_keys() -> None:
    """A bytes-keyed hash decodes into the typed dict."""
    client = _bare_client()
    record = {
        b"task_id": b"t-b",
        b"queue": b"llm",
        b"operation": b"op",
        b"status": b"running",
        b"priority": b"7",
        b"created_at": b"2026-01-01T00:00:00Z",
        b"metadata": b"{}",
        b"data": b"{}",
        b"attempts": b"2",
        b"payload_version": b"1",
        b"started_at": b"2026-01-01T00:00:01Z",
        b"completed_at": b"2026-01-01T00:00:02Z",
        b"error": b"some traceback",
        b"error_type": b"permanent",
    }
    decoded = client._decode_record(record)
    assert decoded["task_id"] == "t-b"
    assert decoded["priority"] == 7
    assert decoded["attempts"] == 2
    assert decoded["started_at"] == "2026-01-01T00:00:01Z"
    assert decoded["completed_at"] == "2026-01-01T00:00:02Z"
    # error is masked to a generic string, never the raw traceback.
    assert decoded["error"] == "Task failed"
    assert decoded["error_type"] == "permanent"


def test_decode_record_handles_str_keys() -> None:
    """A str-keyed hash decodes with sane defaults for missing optional fields."""
    client = _bare_client()
    decoded = client._decode_record(_hash_payload(status="queued"))
    assert decoded["status"] == "queued"
    assert "started_at" not in decoded
    assert "error" not in decoded


# ---------------------------------------------------------------------------
# is_task_cancelled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_task_cancelled_no_client_false() -> None:
    client = _bare_client()
    assert await client.is_task_cancelled("t") is False


@pytest.mark.asyncio
async def test_is_task_cancelled_fast_path_true() -> None:
    """A live Valkey cancel key short-circuits to True with no DB round-trip."""
    client, valkey, _ = _make_queue_client()
    valkey.exists = AsyncMock(return_value=1)
    assert await client.is_task_cancelled("t") is True


@pytest.mark.asyncio
async def test_is_task_cancelled_db_fallback_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the Valkey key is gone, a cancelled_at DB row still reports cancelled."""
    import chaoscypher_core.queue.client as client_mod

    client, valkey, _ = _make_queue_client()
    valkey.exists = AsyncMock(return_value=0)

    db_task = MagicMock()
    db_task.cancelled_at = "2026-05-01T00:00:00Z"
    session = MagicMock()
    session.exec.return_value.first.return_value = db_task

    @contextlib.contextmanager
    def _fake_session(_db_name: str):
        yield session

    monkeypatch.setattr(client_mod, "_adapter_db_session", _fake_session)

    result = await client.is_task_cancelled("t", database_name="default")
    assert result is True


@pytest.mark.asyncio
async def test_is_task_cancelled_db_error_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DB error during the fallback is caught and reported as not-cancelled."""
    import chaoscypher_core.queue.client as client_mod

    client, valkey, _ = _make_queue_client()
    valkey.exists = AsyncMock(return_value=0)

    @contextlib.contextmanager
    def _boom(_db_name: str):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(client_mod, "_adapter_db_session", _boom)

    result = await client.is_task_cancelled("t", database_name="default")
    assert result is False


# ---------------------------------------------------------------------------
# Connection guards / properties
# ---------------------------------------------------------------------------


def test_require_connection_raises_when_unavailable() -> None:
    client = _bare_client()
    client.client = None
    with pytest.raises(QueueUnavailableError):
        client._require_connection()


def test_is_enabled_property() -> None:
    client = _bare_client()
    client._enabled = True
    assert client.is_enabled is True
    client._enabled = False
    assert client.is_enabled is False


def test_is_available_property() -> None:
    client, _, _ = _make_queue_client()
    client._enabled = True
    client._connected = True
    assert client.is_available is True

    client.client = None
    assert client.is_available is False
