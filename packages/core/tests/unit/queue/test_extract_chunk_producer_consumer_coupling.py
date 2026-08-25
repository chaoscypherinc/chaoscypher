# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Producer -> consumer coupling for the OP_EXTRACT_CHUNK in-flight guard.

Entry 764: ``QueueClient.in_flight_chunk_task_ids`` filters queued/running
``extract_chunk`` tasks on ``metadata.source_id`` and
``metadata.database_name``. None of the production producers wrote those
keys, so the filter's result set was always empty and SourceRecovery could
re-dispatch a chunk that was already running.

``test_client_coverage.py::test_in_flight_chunk_task_ids_collects_set``
covers the consumer in isolation, but it fabricates a metadata dict no
producer actually emits -- that gap is exactly what let the drift happen.
These tests close it: each one drives a REAL producer code path (not a
hand-built dict) against a single in-memory-store-backed ``QueueClient``,
then calls the REAL ``in_flight_chunk_task_ids`` consumer against
whatever the producer actually wrote. A producer that stops writing these
keys -- or a consumer whose key names drift -- fails one of these tests
immediately instead of leaving the guard silently inert.

Covers the three core-package producers named in the fix:
- ``ChunkExtractionOperationsService.queue_extract_chunk`` (the shared
  choke point for the direct-dispatch AND both in-handler retry paths).
- ``ImportOperationsService._enqueue_chunk_tasks`` (the initial-dispatch
  batch path).
- ``rehydrate._build_extract_chunk_payload`` (the startup rehydration
  path, which additionally has to resolve ``source_id`` off the parent
  ``ChunkExtractionJob`` since ``ChunkExtractionTask`` has no such
  column).
"""

from __future__ import annotations

from itertools import count
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.constants import QUEUE_LLM
from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    ChunkExtractionOperationsService,
)
from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)
from chaoscypher_core.queue.client import QueueClient


# ---------------------------------------------------------------------------
# Shared in-memory-store-backed QueueClient
#
# enqueue()/enqueue_tasks_batch() write into `store` via a fake pipeline's
# hset; in_flight_chunk_task_ids() reads back from the SAME store via fake
# scan_iter/hgetall. A task written by a real producer can therefore be
# read back by the real consumer inside one test, with no re-implemented
# metadata shape on either side of the assertion.
# ---------------------------------------------------------------------------


def _make_store_backed_client() -> tuple[QueueClient, dict[str, dict[str, Any]]]:
    """Build a QueueClient whose enqueue path and scan path share one store."""
    store: dict[str, dict[str, Any]] = {}

    client = QueueClient.__new__(QueueClient)
    client._connected = True
    client._max_pending_queue_depth = 10000
    client._operations_result_ttl = 7200
    client._llm_result_ttl = 3600

    pipeline = MagicMock()

    def _hset(key: str, mapping: dict[str, Any]) -> MagicMock:
        task_id = key.removeprefix("queue:task:")
        store[task_id] = dict(mapping)
        return pipeline

    pipeline.hset.side_effect = _hset
    pipeline.zadd.return_value = pipeline
    pipeline.lpush.return_value = pipeline
    pipeline.ltrim.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[])

    valkey = MagicMock()
    valkey.zcard = AsyncMock(return_value=0)
    valkey.pipeline = MagicMock(return_value=pipeline)
    valkey.exists = AsyncMock(return_value=0)
    # Pending-ZSET seq counter (queue FIFO tiebreaker, 2026-08-15).
    valkey.incr = AsyncMock(side_effect=lambda _key, _c=count(1): next(_c))
    valkey.incrby = AsyncMock(side_effect=lambda _key, amount: amount)

    def _scan_iter_factory(*_args: Any, **_kwargs: Any) -> Any:
        async def _gen() -> Any:
            for task_id in list(store):
                yield f"queue:task:{task_id}".encode()

        return _gen()

    valkey.scan_iter = _scan_iter_factory

    async def _hgetall(key: Any) -> dict[str, Any]:
        raw = key.decode() if isinstance(key, bytes) else key
        task_id = raw.removeprefix("queue:task:")
        return store.get(task_id, {})

    valkey.hgetall = AsyncMock(side_effect=_hgetall)

    client.client = valkey
    return client, store


@pytest.fixture
def patched_singleton(monkeypatch: pytest.MonkeyPatch) -> QueueClient:
    """Point the module-level ``queue_client`` singleton at a fresh store-backed client.

    ``ChunkExtractionOperationsService.queue_extract_chunk`` and
    ``ImportOperationsService._enqueue_chunk_tasks`` both call the
    module-level singleton directly (``from chaoscypher_core.queue import
    queue_client``) rather than an injected instance, so the coupling test
    has to mutate that singleton's connection state in place -- monkeypatch
    restores it after the test.
    """
    from chaoscypher_core.queue import queue_client as singleton

    fresh, _store = _make_store_backed_client()
    monkeypatch.setattr(singleton, "client", fresh.client)
    monkeypatch.setattr(singleton, "_connected", True)
    return singleton


def _make_settings() -> MagicMock:
    """Build a minimal settings mock exposing the priority field producers read."""
    settings = MagicMock()
    settings.priorities.background = 10
    return settings


# ---------------------------------------------------------------------------
# ChunkExtractionOperationsService.queue_extract_chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_extract_chunk_direct_dispatch_satisfies_in_flight_guard(
    patched_singleton: QueueClient,
) -> None:
    """The direct-dispatch producer's metadata matches the consumer predicate."""
    service = ChunkExtractionOperationsService()

    await service.queue_extract_chunk(
        chunk_task_id="chunk-task-1",
        job_id="job-1",
        source_id="src-1",
        database_name="default",
        chunk_index=0,
    )

    ids = await patched_singleton.in_flight_chunk_task_ids(
        source_id="src-1", database_name="default"
    )
    assert ids == {"chunk-task-1"}


@pytest.mark.asyncio
async def test_chunk_cancellation_retry_producer_satisfies_in_flight_guard(
    patched_singleton: QueueClient,
) -> None:
    """The CancelledError retry path (_handle_chunk_cancellation) forwards source_id.

    Mirrors how the real caller (_extract_chunk_handler) invokes this: it
    always supplies source_id, resolved from the job row before the retry
    branch runs.
    """
    service = ChunkExtractionOperationsService()
    adapter = MagicMock()
    adapter.get_chunk_task = MagicMock(return_value={"retry_count": 0})
    adapter.update_chunk_task = MagicMock()
    settings = MagicMock()
    settings.retries.extraction_chunk_max = 3
    settings.priorities.background = 10

    await service._handle_chunk_cancellation(
        adapter=adapter,
        data={"chunk_task_id": "chunk-task-2", "job_id": "job-2", "small_chunk_ids": []},
        chunk_task_id="chunk-task-2",
        job_id="job-2",
        database_name="default",
        chunk_index=1,
        settings=settings,
        source_id="src-2",
    )

    ids = await patched_singleton.in_flight_chunk_task_ids(
        source_id="src-2", database_name="default"
    )
    assert ids == {"chunk-task-2"}


@pytest.mark.asyncio
async def test_chunk_failure_retry_producer_satisfies_in_flight_guard(
    patched_singleton: QueueClient,
) -> None:
    """The retryable-exception path (_handle_chunk_failure) forwards source_id."""
    service = ChunkExtractionOperationsService()
    adapter = MagicMock()
    adapter.get_chunk_task = MagicMock(return_value={"retry_count": 0})
    adapter.update_chunk_task = MagicMock()
    settings = MagicMock()
    settings.retries.extraction_chunk_max = 3
    settings.priorities.background = 10

    class _RetryableError(Exception):
        is_retryable = True

    await service._handle_chunk_failure(
        adapter=adapter,
        exc=_RetryableError("transient"),
        chunk_task_id="chunk-task-3",
        job_id="job-3",
        database_name="default",
        chunk_index=2,
        settings=settings,
        data={"small_chunk_ids": []},
        source_id="src-3",
    )

    ids = await patched_singleton.in_flight_chunk_task_ids(
        source_id="src-3", database_name="default"
    )
    assert ids == {"chunk-task-3"}


# ---------------------------------------------------------------------------
# ImportOperationsService._enqueue_chunk_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_chunk_tasks_batch_satisfies_in_flight_guard(
    patched_singleton: QueueClient,
) -> None:
    """The initial-dispatch batch producer's metadata matches the consumer predicate."""
    service = ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=MagicMock(),
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
        engine_settings=MagicMock(),
    )

    adapter = MagicMock()
    adapter.list_extraction_tasks_for_job.return_value = []
    adapter.create_chunk_tasks_batch.return_value = []
    adapter.list_extraction_tasks_by_status.return_value = [
        {"id": "chunk-task-4", "chunk_index": 0},
    ]

    settings = _make_settings()
    settings.current_database = "default"

    count = await service._enqueue_chunk_tasks(
        adapter=adapter,
        job_id="job-4",
        file_id="src-4",
        hierarchical_groups=[{"id": "grp-0", "small_chunk_ids": ["c1"]}],
        settings=settings,
    )

    assert count == 1
    ids = await patched_singleton.in_flight_chunk_task_ids(
        source_id="src-4", database_name="default"
    )
    assert ids == {"chunk-task-4"}


# ---------------------------------------------------------------------------
# rehydrate._build_extract_chunk_payload (via rehydrate_queue_from_db)
# ---------------------------------------------------------------------------


class _FakeChunkTaskRow:
    """Minimal stand-in for a ``ChunkExtractionTask`` row."""

    def __init__(self, *, task_id: str, job_id: str, database_name: str) -> None:
        """Set the row fields ``_rehydrate_spec``/``_build_extract_chunk_payload`` read."""
        self.id = task_id
        self.job_id = job_id
        self.database_name = database_name
        self.status = "running"
        self.queue_task_id = None
        self.chunk_index = 0
        self.hierarchical_group_id = None
        self.small_chunk_ids = None
        self.cancelled_at = None


class _FakeJobRow:
    """Minimal stand-in for a ``ChunkExtractionJob`` row (only source_id used)."""

    def __init__(self, source_id: str) -> None:
        """Store the source_id the fake ``session.get`` lookup should return."""
        self.source_id = source_id


class _FakeSession:
    """Fake SafeSession: ``exec`` returns canned task rows, ``get`` returns a job by PK."""

    def __init__(self, tasks: list[Any], jobs_by_id: dict[str, Any]) -> None:
        """Seed the canned task rows and the job-by-id lookup table."""
        self._tasks = tasks
        self._jobs_by_id = jobs_by_id

    def exec(self, _stmt: Any) -> Any:
        """Return the canned task rows regardless of statement (single spec in play)."""
        return iter(self._tasks)

    def get(self, _model: Any, pk: str) -> Any:
        """Return the job row registered under ``pk``, mirroring Session.get's PK lookup."""
        return self._jobs_by_id.get(pk)

    def maybe_commit(self) -> None:
        """No-op: nothing to persist against this fake session."""


@pytest.mark.asyncio
async def test_rehydrate_extract_chunk_payload_satisfies_in_flight_guard() -> None:
    """The startup-rehydration producer resolves source_id via the parent job."""
    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

    client, _store = _make_store_backed_client()
    task = _FakeChunkTaskRow(task_id="chunk-task-5", job_id="job-5", database_name="default")
    session = _FakeSession(tasks=[task], jobs_by_id={"job-5": _FakeJobRow(source_id="src-5")})

    count = await rehydrate_queue_from_db(client, session)

    assert count == 1
    ids = await client.in_flight_chunk_task_ids(source_id="src-5", database_name="default")
    assert ids == {"chunk-task-5"}


@pytest.mark.asyncio
async def test_rehydrate_extract_chunk_payload_missing_job_degrades_gracefully() -> None:
    """No matching job row -> source_id=None, guard just doesn't match (no crash)."""
    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

    client, _store = _make_store_backed_client()
    task = _FakeChunkTaskRow(task_id="chunk-task-6", job_id="job-missing", database_name="default")
    session = _FakeSession(tasks=[task], jobs_by_id={})

    count = await rehydrate_queue_from_db(client, session)

    assert count == 1
    ids = await client.in_flight_chunk_task_ids(source_id="src-6", database_name="default")
    assert ids == set()


# ---------------------------------------------------------------------------
# Constant sanity: producers enqueue on the queue the consumer scans
# ---------------------------------------------------------------------------


def test_extract_chunk_is_routed_to_the_llm_queue() -> None:
    """Sanity pin: if this ever drifts, every test above would false-pass on the wrong queue."""
    from chaoscypher_core.constants import OP_EXTRACT_CHUNK, OPERATION_QUEUE_ROUTING

    assert OPERATION_QUEUE_ROUTING[OP_EXTRACT_CHUNK] == QUEUE_LLM
