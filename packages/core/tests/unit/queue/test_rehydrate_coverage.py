# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage tests for ``queue/rehydrate.py``.

Drives ``rehydrate_queue_from_db`` and its ``_rehydrate_spec`` helper without a
real database or Valkey by substituting a custom ``REHYDRATION_REGISTRY`` whose
``table_factory`` returns a lightweight fake SQLModel-shaped class and whose
``build_payload`` / ``reset_row`` are plain Python callables. ``sqlmodel.select``
is stubbed to a chainable no-op so the statement build never touches SQLAlchemy,
and ``session.exec`` simply returns the fake rows.

Covered behaviors:

- Row whose Valkey hash is GONE -> re-enqueued, ``reset_row`` applied, per-row
  ``maybe_commit`` called, counted.
- Row whose Valkey hash STILL EXISTS -> skipped (no duplicate enqueue).
- Row with ``queue_task_id is None`` (never enqueued) -> unconditionally
  re-enqueued.
- ``cancelled_at`` column present -> the IS NULL filter ``.where`` is added.
- Per-spec exception isolation: one spec raising does not abort the others, and
  the total reflects only the specs that succeeded.
- Empty registry / empty result set -> returns 0.

Module-level ``REHYDRATION_REGISTRY`` and ``sqlmodel.select`` are patched via
monkeypatch so each test runs against a controlled, import-light fixture.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import chaoscypher_core.queue.rehydrate as rehydrate_mod
from chaoscypher_core.constants import OP_EXTRACT_CHUNK
from chaoscypher_core.queue.rehydrate import (
    RehydrationSpec,
    _rehydrate_spec,
    rehydrate_queue_from_db,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRow:
    """A minimal stand-in for a ``ChunkExtractionTask`` row."""

    def __init__(self, *, id: str, status: str, queue_task_id: str | None) -> None:  # noqa: A002 - mirrors model field
        self.id = id
        self.status = status
        self.queue_task_id = queue_task_id
        self.started_at = "2026-01-01T00:00:00+00:00"


class _ChainableStmt:
    """A chainable stub that swallows ``.where(...)`` calls."""

    def where(self, *_args: Any, **_kwargs: Any) -> _ChainableStmt:
        """Return self so chained ``.where`` calls are no-ops."""
        return self


def _make_table(*, with_cancelled_at: bool) -> type:
    """Build a fake table class exposing the attributes the rehydrator touches."""
    attrs: dict[str, Any] = {
        # ``status.in_(...)`` and ``cancelled_at.is_(None)`` are called on the
        # class attributes during statement build; MagicMock swallows both.
        "status": MagicMock(),
    }
    if with_cancelled_at:
        attrs["cancelled_at"] = MagicMock()
    return type("FakeTable", (), attrs)


def _make_session(rows: list[_FakeRow]) -> MagicMock:
    """Build a fake SafeSession whose exec() yields the supplied rows."""
    session = MagicMock()
    session.exec = MagicMock(return_value=iter(rows))
    session.maybe_commit = MagicMock()
    return session


def _make_queue_client(*, hash_exists: bool) -> MagicMock:
    """Build a fake QueueClient with a live Valkey and enqueue returning a new id."""
    client = MagicMock()
    client.client = MagicMock()
    client.client.exists = AsyncMock(return_value=1 if hash_exists else 0)
    client.enqueue = AsyncMock(return_value="new-task-id")
    return client


def _make_spec(
    *,
    with_cancelled_at: bool = False,
    build_payload: Any = None,
    reset_row: Any = None,
) -> RehydrationSpec:
    """Build a RehydrationSpec bound to a fake table for the chunk operation."""
    table = _make_table(with_cancelled_at=with_cancelled_at)

    def _default_payload(_row: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return ({"k": "v"}, {"rehydrated": True})

    def _default_reset(row: Any, new_id: str) -> None:
        row.status = "queued"
        row.started_at = None
        row.queue_task_id = new_id

    return RehydrationSpec(
        operation=OP_EXTRACT_CHUNK,
        table_factory=lambda: table,
        non_terminal_statuses=("pending", "queued", "running"),
        build_payload=build_payload or _default_payload,
        reset_row=reset_row or _default_reset,
    )


@pytest.fixture(autouse=True)
def _stub_select(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``sqlmodel.select`` with a chainable no-op stub."""
    import sqlmodel

    monkeypatch.setattr(sqlmodel, "select", lambda *_a, **_k: _ChainableStmt())


# ---------------------------------------------------------------------------
# _rehydrate_spec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehydrate_spec_reenqueues_missing_hash() -> None:
    """A row whose Valkey hash is gone is re-enqueued and reset."""
    row = _FakeRow(id="r1", status="running", queue_task_id="old-task")
    spec = _make_spec()
    session = _make_session([row])
    qc = _make_queue_client(hash_exists=False)

    rehydrated, scanned = await _rehydrate_spec(qc, session, spec)

    assert (rehydrated, scanned) == (1, 1)
    qc.enqueue.assert_awaited_once()
    # reset_row applied the fresh task id and queued status.
    assert row.status == "queued"
    assert row.queue_task_id == "new-task-id"
    assert row.started_at is None
    session.maybe_commit.assert_called_once()


@pytest.mark.asyncio
async def test_rehydrate_spec_skips_when_hash_present() -> None:
    """A row whose Valkey hash still exists is skipped (no duplicate enqueue)."""
    row = _FakeRow(id="r2", status="queued", queue_task_id="live-task")
    spec = _make_spec()
    session = _make_session([row])
    qc = _make_queue_client(hash_exists=True)

    rehydrated, scanned = await _rehydrate_spec(qc, session, spec)

    assert (rehydrated, scanned) == (0, 1)
    qc.enqueue.assert_not_awaited()
    session.maybe_commit.assert_not_called()


@pytest.mark.asyncio
async def test_rehydrate_spec_reenqueues_when_never_enqueued() -> None:
    """A row with queue_task_id=None (never enqueued) is unconditionally re-enqueued."""
    row = _FakeRow(id="r3", status="pending", queue_task_id=None)
    spec = _make_spec()
    session = _make_session([row])
    # hash_exists True is irrelevant: the exists() fast-path is skipped when
    # queue_task_id is None.
    qc = _make_queue_client(hash_exists=True)

    rehydrated, scanned = await _rehydrate_spec(qc, session, spec)

    assert (rehydrated, scanned) == (1, 1)
    qc.enqueue.assert_awaited_once()
    qc.client.exists.assert_not_awaited()


@pytest.mark.asyncio
async def test_rehydrate_spec_applies_cancelled_at_filter() -> None:
    """When the table exposes cancelled_at, the IS NULL filter is added without error."""
    row = _FakeRow(id="r4", status="running", queue_task_id=None)
    spec = _make_spec(with_cancelled_at=True)
    session = _make_session([row])
    qc = _make_queue_client(hash_exists=False)

    rehydrated, scanned = await _rehydrate_spec(qc, session, spec)

    # The .where chaining for cancelled_at executed and the row still rehydrated.
    assert (rehydrated, scanned) == (1, 1)


@pytest.mark.asyncio
async def test_rehydrate_spec_empty_result_returns_zero() -> None:
    """An empty result set rehydrates nothing."""
    spec = _make_spec()
    session = _make_session([])
    qc = _make_queue_client(hash_exists=False)

    rehydrated, scanned = await _rehydrate_spec(qc, session, spec)

    assert (rehydrated, scanned) == (0, 0)
    qc.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_rehydrate_spec_passes_built_payload_to_enqueue() -> None:
    """build_payload's (data, metadata) tuple flows through to enqueue()."""
    row = _FakeRow(id="r5", status="running", queue_task_id=None)

    def _payload(_row: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return ({"chunk_task_id": _row.id}, {"prior_status": _row.status})

    spec = _make_spec(build_payload=_payload)
    session = _make_session([row])
    qc = _make_queue_client(hash_exists=False)

    await _rehydrate_spec(qc, session, spec)

    kwargs = qc.enqueue.await_args.kwargs
    assert kwargs["data"] == {"chunk_task_id": "r5"}
    assert kwargs["metadata"] == {"prior_status": "running"}
    assert kwargs["priority"] == 0


# ---------------------------------------------------------------------------
# rehydrate_queue_from_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rehydrate_queue_from_db_sums_across_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The top-level entry point sums rehydrated counts across all registry specs."""
    rows_a = [_FakeRow(id="a1", status="running", queue_task_id=None)]
    rows_b = [_FakeRow(id="b1", status="running", queue_task_id=None)]

    spec_a = _make_spec()
    spec_b = _make_spec()

    monkeypatch.setattr(rehydrate_mod, "REHYDRATION_REGISTRY", (spec_a, spec_b))

    qc = _make_queue_client(hash_exists=False)

    # Two specs, each scanning a distinct row set. session.exec returns the next
    # configured iterator on each call.
    session = MagicMock()
    session.exec = MagicMock(side_effect=[iter(rows_a), iter(rows_b)])
    session.maybe_commit = MagicMock()

    total = await rehydrate_queue_from_db(qc, session)

    assert total == 2
    assert qc.enqueue.await_count == 2


@pytest.mark.asyncio
async def test_rehydrate_queue_from_db_isolates_spec_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spec that raises is swallowed; later specs still rehydrate."""
    good_rows = [_FakeRow(id="g1", status="running", queue_task_id=None)]

    # First spec's table_factory raises during statement build.
    def _boom_factory() -> type:
        raise RuntimeError("table import blew up")

    bad_spec = RehydrationSpec(
        operation=OP_EXTRACT_CHUNK,
        table_factory=_boom_factory,
        non_terminal_statuses=("running",),
        build_payload=lambda _r: ({}, {}),
        reset_row=lambda _r, _i: None,
    )
    good_spec = _make_spec()

    monkeypatch.setattr(rehydrate_mod, "REHYDRATION_REGISTRY", (bad_spec, good_spec))

    qc = _make_queue_client(hash_exists=False)
    session = MagicMock()
    session.exec = MagicMock(return_value=iter(good_rows))
    session.maybe_commit = MagicMock()

    total = await rehydrate_queue_from_db(qc, session)

    # bad_spec contributed 0 (swallowed); good_spec rehydrated its one row.
    assert total == 1


@pytest.mark.asyncio
async def test_rehydrate_queue_from_db_empty_registry_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty registry rehydrates nothing and returns 0."""
    monkeypatch.setattr(rehydrate_mod, "REHYDRATION_REGISTRY", ())
    qc = _make_queue_client(hash_exists=False)
    session = _make_session([])

    total = await rehydrate_queue_from_db(qc, session)

    assert total == 0
