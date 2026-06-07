# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: Neuron background loops run each pass in its own session scope.

The periodic loops in ``worker.py`` (and ``search_sweep.py``) used to touch
``adapter.session`` without entering ``adapter.session_scope()``, so every
pass resolved to the singleton ``_fallback_session``. Overlapping timers —
plus the two loops that run DB work inside ``asyncio.to_thread`` — then drove
concurrent access to one non-thread-safe SQLAlchemy ``Session``: the same
silent-data-loss race that was fixed for queue handlers on 2026-05-20 but
left open for the loops.

These tests pin the fix: each loop pass must run inside a fresh per-task
``SafeSession`` (installed by ``session_scope()`` in a ``ContextVar`` that
``adapter.session`` resolves to), never the shared fallback. For the
``to_thread`` loops the scope is inherited through the context copy that
``asyncio.to_thread`` performs, so the work sees the scoped session inside
the worker thread too.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


async def _run_until_observed(
    task: asyncio.Task[None],
    observed: dict[str, Any],
    key: str,
    timeout: float = 3.0,
) -> None:
    """Let a periodic-loop task run until it records ``key``, then cancel it."""
    elapsed = 0.0
    step = 0.01
    while key not in observed and elapsed < timeout:
        await asyncio.sleep(step)
        elapsed += step
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture
def adapter(tmp_path: Path) -> SqliteAdapter:
    """A connected real adapter so ``session_scope()`` is the genuine CM."""
    a = SqliteAdapter(str(tmp_path / "app.db"), database_name="default")
    a.connect()
    yield a
    a.disconnect()


@pytest.mark.asyncio
async def test_orphan_task_cleanup_loop_runs_in_session_scope(adapter: SqliteAdapter) -> None:
    """``_orphan_task_cleanup_loop`` must run its DELETE in a scoped session."""
    from chaoscypher_neuron.worker import _orphan_task_cleanup_loop

    fallback = adapter._fallback_session
    observed: dict[str, Any] = {}

    def spy(older_than_seconds: int) -> int:
        observed["is_fallback"] = adapter.session is fallback
        observed["is_none"] = adapter.session is None
        return 0

    adapter.cleanup_orphaned_chunk_tasks = spy  # type: ignore[method-assign]

    task = asyncio.create_task(
        _orphan_task_cleanup_loop(adapter=adapter, retention_days=7, interval_seconds=0.01)
    )
    await _run_until_observed(task, observed, "is_fallback")

    assert observed.get("is_none") is False, "scope must install a real session"
    assert observed.get("is_fallback") is False, (
        "cleanup ran against the shared _fallback_session instead of a per-pass scope"
    )


@pytest.mark.asyncio
async def test_orphan_files_cleanup_loop_runs_in_session_scope(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_orphan_files_cleanup_loop`` must run its sweep in a scoped session."""
    from chaoscypher_neuron.worker import _orphan_files_cleanup_loop

    fallback = adapter._fallback_session
    observed: dict[str, Any] = {}

    def spy(
        *, staging_dir: Path, adapter: SqliteAdapter, database_name: str, retention_seconds: int
    ) -> int:
        observed["is_fallback"] = adapter.session is fallback
        return 0

    monkeypatch.setattr(
        "chaoscypher_core.services.sources.orphan_files.cleanup_orphan_source_files", spy
    )

    task = asyncio.create_task(
        _orphan_files_cleanup_loop(
            adapter=adapter,
            staging_dir=tmp_path,
            database_name="default",
            retention_days=1,
            interval_seconds=0.01,
            pass_timeout_seconds=60,
        )
    )
    await _run_until_observed(task, observed, "is_fallback")

    assert observed.get("is_fallback") is False, (
        "orphan-files sweep ran against the shared _fallback_session instead of a per-pass scope"
    )


@pytest.mark.asyncio
async def test_search_sweep_loop_runs_in_session_scope(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_search_sweep_loop`` must run ``sweep_search_indexes`` in a scoped session."""
    from chaoscypher_neuron.search_sweep import _search_sweep_loop

    fallback = adapter._fallback_session
    observed: dict[str, Any] = {}

    def spy(
        passed_adapter: SqliteAdapter, search_repo: Any, *, max_attempts: int
    ) -> dict[str, int]:
        observed["is_fallback"] = passed_adapter.session is fallback
        return {}

    monkeypatch.setattr("chaoscypher_neuron.search_sweep.sweep_search_indexes", spy)

    task = asyncio.create_task(
        _search_sweep_loop(
            adapter=adapter,
            search_repo=MagicMock(),
            interval_seconds=0.01,
            max_attempts=5,
        )
    )
    await _run_until_observed(task, observed, "is_fallback")

    assert observed.get("is_fallback") is False, (
        "search sweep ran against the shared _fallback_session instead of a per-pass scope"
    )


@pytest.mark.asyncio
async def test_source_recovery_loop_runs_in_session_scope(adapter: SqliteAdapter) -> None:
    """``_source_recovery_loop`` must run reconcile in a scoped session."""
    from chaoscypher_neuron.worker import _source_recovery_loop

    fallback = adapter._fallback_session
    observed: dict[str, Any] = {}

    class _Stats:
        recovered = 0
        skipped_paused = 0
        total_scanned = 0

        def to_dict(self) -> dict[str, int]:
            return {"recovered": 0, "skipped_paused": 0, "total_scanned": 0}

    async def reconcile(database_name: str) -> _Stats:
        observed["is_fallback"] = adapter.session is fallback
        return _Stats()

    recovery = MagicMock()
    recovery.reconcile_database = reconcile

    task = asyncio.create_task(
        _source_recovery_loop(
            recovery=recovery,
            adapter=adapter,
            database_name="default",
            interval_seconds=0.01,
            reconcile_timeout_seconds=10,
        )
    )
    await _run_until_observed(task, observed, "is_fallback")

    assert observed.get("is_fallback") is False, (
        "source reconcile ran against the shared _fallback_session instead of a per-pass scope"
    )
