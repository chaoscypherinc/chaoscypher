# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Task 5.4: queue rehydration from SQLite on startup.

Verifies that rehydrate_queue_from_db scans operation-bearing DB tables for
rows in non-terminal states and re-enqueues any that have no corresponding
Valkey hash -- closing two durability gaps:

1. Enqueue atomicity: crash between DB INSERT and Valkey enqueue.
2. AOF wipe recovery: Valkey rebuilt empty while DB still has running/queued rows.

The rehydrator is registry-driven (``REHYDRATION_REGISTRY`` in
``chaoscypher_core.queue.rehydrate``); each spec maps a SQLModel table onto
a queue operation drawn from ``OPERATION_QUEUE_ROUTING``. The first set of
tests pin the existing ``OP_EXTRACT_CHUNK`` / ``ChunkExtractionTask`` flow;
the second set pins the registry contract itself so adding a new spec is
caught by the test suite, and the third set exercises multi-spec dispatch
on a synthetic registry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk_task(
    task_id: str,
    *,
    status: str,
    queue_task_id: str | None = None,
    started_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    database_name: str = "default",
    job_id: str = "job-abc",
    chunk_index: int = 0,
    hierarchical_group_id: str | None = None,
    small_chunk_ids: list[str] | None = None,
) -> MagicMock:
    """Build a minimal ChunkExtractionTask mock.

    Args:
        task_id: The ChunkExtractionTask primary key.
        status: Current status string.
        queue_task_id: Valkey task ID (None = never enqueued).
        started_at: When the task was picked up by a worker.
        cancelled_at: Set if task has been cancelled.
        database_name: Database context.
        job_id: Parent extraction job ID.
        chunk_index: Chunk index within the job.
        hierarchical_group_id: Optional hierarchical group reference.
        small_chunk_ids: Optional list of small chunk IDs in this group.
    """
    task = MagicMock()
    task.id = task_id
    task.status = status
    task.queue_task_id = queue_task_id
    task.started_at = started_at
    task.cancelled_at = cancelled_at
    task.database_name = database_name
    task.job_id = job_id
    task.chunk_index = chunk_index
    task.hierarchical_group_id = hierarchical_group_id
    task.small_chunk_ids = small_chunk_ids
    return task


def _make_queue_client(*, existing_task_ids: set[str]) -> MagicMock:
    """Build a minimal async queue client mock.

    Args:
        existing_task_ids: Valkey task IDs that already have a hash (queue:task:{id}).
    """
    client = MagicMock()
    valkey = MagicMock()
    client.client = valkey

    async def _exists(key: str) -> int:
        if isinstance(key, bytes):
            key = key.decode()
        # Strip the prefix to get the bare task ID used for membership check
        task_id = key.removeprefix("queue:task:")
        return 1 if task_id in existing_task_ids else 0

    valkey.exists = AsyncMock(side_effect=_exists)
    client.enqueue = AsyncMock(return_value="new-valkey-task-id")
    # enqueue_task is an alias on QueueClient; wire it to the same mock
    client.enqueue_task = client.enqueue
    return client


def _make_session(tasks: list[MagicMock]) -> MagicMock:
    """Build a minimal synchronous session mock that returns the given tasks.

    Args:
        tasks: ChunkExtractionTask mocks to return from exec().
    """
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    session.exec = MagicMock(return_value=iter(tasks))
    session.maybe_commit = MagicMock()
    return session


# ---------------------------------------------------------------------------
# Test 1 - re-enqueues running/queued tasks with no Valkey hash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehydrate_enqueues_tasks_without_valkey_entry() -> None:
    """ChunkExtractionTask rows in non-terminal states with no Valkey hash
    are re-enqueued and status is reset to queued; started_at is cleared.

    Seeds two tasks:
    - running: simulates worker crash (DB row persisted, Valkey state gone).
    - queued: simulates enqueue-atomicity failure (DB row written, Valkey
      enqueue never happened).

    Neither task has a matching Valkey hash. Both must be re-enqueued.
    """
    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

    task_running = _make_chunk_task(
        "task-run-001",
        status="running",
        queue_task_id="old-valkey-id-001",
        started_at=datetime.now(UTC),
    )
    task_queued = _make_chunk_task(
        "task-q-002",
        status="queued",
        queue_task_id=None,  # never made it to Valkey
    )

    queue_client = _make_queue_client(existing_task_ids=set())  # Valkey is empty
    session = _make_session([task_running, task_queued])

    count = await rehydrate_queue_from_db(queue_client, session)

    assert count == 2, f"Expected 2 rehydrated, got {count}"
    # Both must now be marked queued
    assert task_running.status == "queued"
    assert task_queued.status == "queued"
    # started_at on the previously-running task must be cleared
    assert task_running.started_at is None
    # enqueue must have been called twice
    assert queue_client.enqueue.call_count == 2
    # maybe_commit fires once per rehydrated row (per-enqueue commit) so a
    # mid-loop crash can't roll back rows already repointed at fresh tasks.
    assert session.maybe_commit.call_count == 2


# ---------------------------------------------------------------------------
# Test 2 - skips tasks already present in Valkey
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehydrate_skips_tasks_already_in_valkey() -> None:
    """Tasks whose Valkey hash already exists must not be re-enqueued.

    This prevents duplicate queue entries when the worker is mid-startup
    and has already re-inserted some tasks before rehydration ran.
    """
    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

    existing_valkey_id = "existing-valkey-task-999"
    task_healthy = _make_chunk_task(
        "task-ok-999",
        status="running",
        queue_task_id=existing_valkey_id,
        started_at=datetime.now(UTC),
    )

    queue_client = _make_queue_client(existing_task_ids={existing_valkey_id})
    session = _make_session([task_healthy])

    count = await rehydrate_queue_from_db(queue_client, session)

    assert count == 0, f"Expected 0 rehydrated (Valkey hash present), got {count}"
    queue_client.enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 3 - skips tasks with cancelled_at set (SQL filters them out)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehydrate_skips_cancelled_tasks() -> None:
    """Tasks with cancelled_at set must not be re-enqueued.

    The SQL WHERE clause filters cancelled tasks via cancelled_at IS NULL,
    so they never appear in the result set. Simulate by passing an empty list.
    """
    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

    queue_client = _make_queue_client(existing_task_ids=set())
    # Cancelled tasks are excluded by SQL; the result set is empty
    session = _make_session([])

    count = await rehydrate_queue_from_db(queue_client, session)

    assert count == 0
    queue_client.enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 4 - skips terminal states (completed, failed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehydrate_skips_terminal_states() -> None:
    """completed/failed tasks are NOT rehydrated.

    The SQL WHERE clause restricts to (pending, queued, running) only.
    Simulate by passing an empty result set (the DB would return nothing
    for a completed/failed task).
    """
    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

    queue_client = _make_queue_client(existing_task_ids=set())
    # Terminal-state tasks are excluded by SQL; the result set is empty
    session = _make_session([])

    count = await rehydrate_queue_from_db(queue_client, session)

    assert count == 0
    queue_client.enqueue.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 5 - enqueue is called with the correct data shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehydrate_enqueue_data_shape() -> None:
    """Verify the enqueue call uses the correct queue, operation, and data keys.

    The handler reconstructs chunk content from the DB, so chunk_content
    must be passed as an empty string. chunk_task_id must be the DB PK
    (task.id), not the queue_task_id column.
    """
    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

    task = _make_chunk_task(
        "db-task-id-001",
        status="pending",
        queue_task_id=None,
        job_id="job-xyz",
        database_name="test_db",
        chunk_index=3,
        hierarchical_group_id="hg-007",
        small_chunk_ids=["s1", "s2"],
    )

    queue_client = _make_queue_client(existing_task_ids=set())
    session = _make_session([task])

    await rehydrate_queue_from_db(queue_client, session)

    queue_client.enqueue.assert_awaited_once()
    call_kwargs: dict[str, Any] = queue_client.enqueue.call_args.kwargs

    assert call_kwargs["queue"] == "llm"
    assert call_kwargs["operation"] == "extract_chunk"

    data = call_kwargs["data"]
    assert data["chunk_task_id"] == "db-task-id-001"
    assert data["job_id"] == "job-xyz"
    assert data["database_name"] == "test_db"
    assert data["chunk_content"] == ""
    assert data["chunk_index"] == 3
    assert data["hierarchical_group_id"] == "hg-007"
    assert data["small_chunk_ids"] == ["s1", "s2"]


# ---------------------------------------------------------------------------
# Test 6 - per-enqueue commit survives a mid-loop crash (no duplicate re-enqueue)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehydrate_commits_each_row_before_next_enqueue() -> None:
    """A row re-enqueued successfully is committed before the next enqueue runs.

    Regression for the non-atomic-rehydration bug: the loop used to enqueue a
    fresh Valkey task per row but commit the row mutations only once at the
    end. A crash mid-loop (e.g. Valkey connection drop on a later row) then
    rolled back the ``queue_task_id`` repoint for the rows already enqueued —
    so the next startup saw their *old* (missing) hash and re-enqueued them
    again, producing duplicate EXTRACT_CHUNK tasks.

    With per-row commit, the first row's repoint is durable before the second
    row's enqueue is attempted, so a restart sees the fresh hash and skips it.

    Seeds two missing-hash rows; the second ``enqueue`` raises. The first row
    must already be repointed (status='queued', fresh queue_task_id) AND
    committed by the time the failure propagates.
    """
    from chaoscypher_core.queue.rehydrate import rehydrate_queue_from_db

    task_one = _make_chunk_task("task-1", status="running", queue_task_id="old-1")
    task_two = _make_chunk_task("task-2", status="running", queue_task_id="old-2")

    queue_client = _make_queue_client(existing_task_ids=set())  # both hashes gone
    # First enqueue succeeds; second simulates a transient Valkey failure.
    queue_client.enqueue = AsyncMock(
        side_effect=["fresh-task-1", ConnectionError("valkey dropped mid-rehydration")]
    )
    queue_client.enqueue_task = queue_client.enqueue

    # Record the row state captured at each commit so we can prove the first
    # row was durably repointed *before* the second enqueue was attempted.
    committed_snapshots: list[tuple[str, str | None]] = []
    session = _make_session([task_one, task_two])
    session.maybe_commit = MagicMock(
        side_effect=lambda: committed_snapshots.append((task_one.status, task_one.queue_task_id))
    )

    # The per-spec best-effort guard swallows the ConnectionError; the call returns.
    await rehydrate_queue_from_db(queue_client, session)

    # The first row was repointed to its fresh task id and reset to queued.
    assert task_one.status == "queued"
    assert task_one.queue_task_id == "fresh-task-1"
    # ...and that state was committed before the crash on the second row.
    assert committed_snapshots, "first row was never committed before the mid-loop crash"
    assert committed_snapshots[0] == ("queued", "fresh-task-1")
    # Both enqueues were attempted (second raised).
    assert queue_client.enqueue.call_count == 2


# ---------------------------------------------------------------------------
# Registry contract — invariants every spec must hold
# ---------------------------------------------------------------------------


def test_registry_operations_are_in_canonical_routing_table() -> None:
    """Every spec's operation must be registered in OPERATION_QUEUE_ROUTING.

    The rehydrator looks up the queue name via the canonical mapping; a
    spec whose op-name isn't in the routing table would raise KeyError at
    enqueue time. CC044 enforces the same invariant on the production
    handler-registration side; this pins it on the rehydration side too.
    """
    from chaoscypher_core.constants import OPERATION_QUEUE_ROUTING
    from chaoscypher_core.queue.rehydrate import REHYDRATION_REGISTRY

    for spec in REHYDRATION_REGISTRY:
        assert spec.operation in OPERATION_QUEUE_ROUTING, (
            f"spec for {spec.operation!r} is not registered in OPERATION_QUEUE_ROUTING"
        )


def test_registry_each_operation_appears_at_most_once() -> None:
    """No two specs may target the same operation.

    Two specs writing to the same op would create duplicate enqueues for
    every rehydrated row. Distinct ops over distinct tables is the
    contract.
    """
    from chaoscypher_core.queue.rehydrate import REHYDRATION_REGISTRY

    ops = [spec.operation for spec in REHYDRATION_REGISTRY]
    assert len(ops) == len(set(ops)), f"duplicate operation in REHYDRATION_REGISTRY: {ops}"


def test_registry_each_table_appears_at_most_once() -> None:
    """No two specs may target the same SQLModel table.

    A second spec on the same table would re-enqueue every row twice on a
    single rehydration pass. Distinct tables per spec is the contract.
    """
    from chaoscypher_core.queue.rehydrate import REHYDRATION_REGISTRY

    tables = [spec.table_factory() for spec in REHYDRATION_REGISTRY]
    table_names = [t.__name__ for t in tables]
    assert len(table_names) == len(set(table_names)), (
        f"duplicate table in REHYDRATION_REGISTRY: {table_names}"
    )


def test_registry_includes_chunk_extraction_task() -> None:
    """ChunkExtractionTask / OP_EXTRACT_CHUNK is the baseline spec.

    Removing it would silently drop the original rehydration use case
    (Task 5.4). This is the canary that catches a refactor accidentally
    emptying the registry.
    """
    from chaoscypher_core.adapters.sqlite.models import ChunkExtractionTask
    from chaoscypher_core.constants import OP_EXTRACT_CHUNK
    from chaoscypher_core.queue.rehydrate import REHYDRATION_REGISTRY

    chunk_specs = [s for s in REHYDRATION_REGISTRY if s.operation == OP_EXTRACT_CHUNK]
    assert len(chunk_specs) == 1, (
        f"expected exactly one spec for {OP_EXTRACT_CHUNK!r}, got {len(chunk_specs)}"
    )
    assert chunk_specs[0].table_factory() is ChunkExtractionTask


# ---------------------------------------------------------------------------
# Multi-spec dispatch — synthetic registry exercises the generalization
# ---------------------------------------------------------------------------


def _make_generic_task_row(
    task_id: str,
    *,
    status: str,
    queue_task_id: str | None = None,
    cancelled_at: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a generic task row with status / queue_task_id / cancelled_at.

    The extras dict is attached as attributes so spec ``build_payload``
    callbacks can read them.
    """
    row = MagicMock()
    row.id = task_id
    row.status = status
    row.queue_task_id = queue_task_id
    row.cancelled_at = cancelled_at
    for k, v in (extra or {}).items():
        setattr(row, k, v)
    return row


def _make_multispec_session(row_batches: list[list[MagicMock]]) -> MagicMock:
    """Build a session whose ``exec`` returns row batches in FIFO order.

    Each call to ``session.exec`` consumes one batch. With the
    registry-driven rehydrator, that's exactly one exec per spec.
    """
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    call_state = {"index": 0}

    def _exec(_stmt: Any) -> Any:
        idx = call_state["index"]
        call_state["index"] += 1
        if idx >= len(row_batches):
            return iter([])
        return iter(row_batches[idx])

    session.exec = MagicMock(side_effect=_exec)
    session.maybe_commit = MagicMock()
    return session


@pytest.mark.asyncio
async def test_multispec_rehydrates_each_table_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry with two specs re-enqueues rows from BOTH tables.

    Synthesizes a second spec on a different op/table; verifies the
    rehydrator calls enqueue once per missing-hash row for each spec and
    that the per-row state-reset callback fires on each.

    The synthetic spec patches ``_rehydrate_spec``'s SELECT construction
    by replacing ``sqlmodel.select`` for the test scope — the real
    SQLAlchemy machinery only accepts column-bearing classes, but for the
    purposes of this test we only care that ``session.exec`` returns the
    canned rows and the per-row callbacks fire.
    """
    from chaoscypher_core.constants import OP_EMBED_CHUNKS
    from chaoscypher_core.queue import rehydrate as rehydrate_mod
    from chaoscypher_core.queue.rehydrate import RehydrationSpec, rehydrate_queue_from_db

    # ---- synthetic second spec ------------------------------------------
    class _FakeEmbedTask:
        """Stand-in SQLModel table; SELECT is bypassed via the mock below."""

        # ``_rehydrate_spec`` evaluates ``table.status.in_(...)`` before the
        # mocked ``select`` is called -- a MagicMock here satisfies the call.
        status = MagicMock()

    fake_embed_resets: list[tuple[Any, str]] = []

    def _build_embed_payload(row: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return ({"source_id": row.source_id}, {"operation_type": OP_EMBED_CHUNKS})

    def _reset_embed_row(row: Any, new_qtid: str) -> None:
        row.status = "queued"
        row.queue_task_id = new_qtid
        fake_embed_resets.append((row.id, new_qtid))

    synthetic_spec = RehydrationSpec(
        operation=OP_EMBED_CHUNKS,
        table_factory=lambda: _FakeEmbedTask,
        non_terminal_statuses=("pending", "queued", "running"),
        build_payload=_build_embed_payload,
        reset_row=_reset_embed_row,
    )

    monkeypatch.setattr(
        rehydrate_mod,
        "REHYDRATION_REGISTRY",
        (rehydrate_mod.REHYDRATION_REGISTRY[0], synthetic_spec),
    )

    # Stub the SELECT builder so neither spec hits real SQLAlchemy: each
    # call returns a sentinel that ``session.exec`` ignores.
    import sqlmodel

    monkeypatch.setattr(sqlmodel, "select", lambda *_a, **_kw: MagicMock(name="stub-stmt"))

    # ---- rows ----------------------------------------------------------
    chunk_row = _make_chunk_task("chunk-row-1", status="running", queue_task_id=None)
    embed_row = _make_generic_task_row(
        "embed-row-1",
        status="pending",
        queue_task_id=None,
        extra={"source_id": "src-001"},
    )

    queue_client = _make_queue_client(existing_task_ids=set())
    session = _make_multispec_session([[chunk_row], [embed_row]])

    count = await rehydrate_queue_from_db(queue_client, session)

    assert count == 2, f"expected 2 rehydrated across both specs, got {count}"
    assert queue_client.enqueue.call_count == 2
    assert fake_embed_resets == [("embed-row-1", "new-valkey-task-id")]
    # maybe_commit fires once per rehydrated row (per-enqueue commit): one for
    # the chunk row, one for the embed row.
    assert session.maybe_commit.call_count == 2


@pytest.mark.asyncio
async def test_multispec_failure_in_one_spec_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing spec is logged and skipped; later specs still run.

    A spec whose table_factory raises must not abort the whole rehydration
    pass — best-effort recovery. The healthy spec's rows must still be
    re-enqueued.
    """
    from chaoscypher_core.constants import OP_EMBED_CHUNKS
    from chaoscypher_core.queue import rehydrate as rehydrate_mod
    from chaoscypher_core.queue.rehydrate import RehydrationSpec, rehydrate_queue_from_db

    def _exploding_factory() -> type:
        raise RuntimeError("simulated factory failure")

    exploding_spec = RehydrationSpec(
        operation=OP_EMBED_CHUNKS,
        table_factory=_exploding_factory,
        non_terminal_statuses=("pending",),
        build_payload=lambda _row: ({}, {}),
        reset_row=lambda _row, _qtid: None,
    )

    # Put the exploding spec FIRST so we can prove the second spec still runs.
    monkeypatch.setattr(
        rehydrate_mod,
        "REHYDRATION_REGISTRY",
        (exploding_spec, rehydrate_mod.REHYDRATION_REGISTRY[0]),
    )

    chunk_row = _make_chunk_task("chunk-2", status="running", queue_task_id=None)

    queue_client = _make_queue_client(existing_task_ids=set())
    # Only one healthy spec consumes from session.exec; the exploding one
    # raises before reaching the exec call.
    session = _make_session([chunk_row])

    count = await rehydrate_queue_from_db(queue_client, session)

    assert count == 1, f"healthy spec should still re-enqueue 1 row; got {count}"
    queue_client.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_multispec_cancelled_at_filter_skipped_when_column_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tables without ``cancelled_at`` must not have the filter applied.

    The rehydrator's hasattr() guard makes the generalization safe for
    tables (like a hypothetical EmbedTask) that don't have cancellation
    semantics. This pins that branch by registering a spec on a class
    without the column and verifying the SELECT proceeds without raising.
    """
    from chaoscypher_core.constants import OP_EMBED_CHUNKS
    from chaoscypher_core.queue import rehydrate as rehydrate_mod
    from chaoscypher_core.queue.rehydrate import RehydrationSpec, rehydrate_queue_from_db

    class _NoCancelledAtTask:
        """Stand-in table with no ``cancelled_at`` attribute."""

        # status needs to support ``.in_(...)``; cancelled_at is deliberately absent.
        status = MagicMock()

    captured_payload: dict[str, Any] = {}

    def _build(row: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        captured_payload["called"] = True
        return ({"id": row.id}, {})

    spec = RehydrationSpec(
        operation=OP_EMBED_CHUNKS,
        table_factory=lambda: _NoCancelledAtTask,
        non_terminal_statuses=("pending",),
        build_payload=_build,
        reset_row=lambda r, q: setattr(r, "queue_task_id", q),
    )

    monkeypatch.setattr(rehydrate_mod, "REHYDRATION_REGISTRY", (spec,))

    # Stub the SELECT builder; we only need exec() to return the canned row.
    import sqlmodel

    monkeypatch.setattr(sqlmodel, "select", lambda *_a, **_kw: MagicMock(name="stub-stmt"))

    row = _make_generic_task_row("no-cancel-1", status="pending", queue_task_id=None)

    queue_client = _make_queue_client(existing_task_ids=set())
    session = _make_session([row])

    count = await rehydrate_queue_from_db(queue_client, session)

    # Verify the spec ran end-to-end (build_payload was reached, meaning the
    # SELECT-with-no-cancelled_at-column branch executed cleanly).
    assert count == 1
    assert captured_payload.get("called") is True
