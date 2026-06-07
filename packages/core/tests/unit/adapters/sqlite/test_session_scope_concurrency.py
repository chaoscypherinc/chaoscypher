# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: concurrent queue handlers must not share session state.

User-reported 2026-05-20: parallel import of three sources (a06022a8,
e9afa92b, 8c200857) lost the extraction job + chunk tasks for two of
them. Logs showed ``chunk_extraction_job_created`` +
``import_analysis_chunks_queued chunks_queued=5`` firing successfully,
but no rows persisted in ``chunk_extraction_jobs`` /
``chunk_extraction_tasks`` and an empty LLM queue. The source-recovery
reconciler eventually re-dispatched them at the +9-minute interval and
they completed cleanly.

Root cause: the neuron worker shared a singleton ``SafeSession`` across
all queue handlers. With ``OPERATIONS`` queue concurrency=8, two async
handlers interleaved on the same session, and pre-dispatch
``_expire_worker_sessions()`` calls disturbed in-flight transactions.

Fix (this test pins): per-task session scoping. ``SqliteAdapter`` exposes
an async ``session_scope()`` context manager that installs a fresh
``SafeSession`` in a ``ContextVar``. ``SqliteAdapter.session`` becomes a
property that reads the ContextVar first, falling back to a private
``_fallback_session`` for callers outside any scope (Cortex tests,
startup paths). ``GraphRepository.session`` follows the same ContextVar
so adapter + graph_repo stay on one session per task — necessary because
the commit handler relies on them sharing a session to avoid
self-deadlocking on the SQLite writer lock.

The queue worker wraps every ``_execute_handler`` call in a fresh scope
so concurrent handlers never share session state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.utils.id import generate_id


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed ``SqliteAdapter`` (CC040 forbids ``:memory:``)."""
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
    """Seed a minimal source row so create_extraction_job has a FK target."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "text",
            "file_size": 10,
            "content_hash": generate_id(),
            "status": "extracting",
        }
    )


@pytest.mark.asyncio
async def test_concurrent_handlers_persist_both_writes(adapter: SqliteAdapter) -> None:
    """Two async handlers writing concurrently must both persist their rows.

    Spawns two async tasks that each enter ``adapter.session_scope()`` and
    call ``adapter.create_extraction_job``, with a ``yield`` between
    ``session.add`` and the implicit commit (modelled by ``await
    asyncio.sleep(0)`` inside the scope). Both rows must persist.

    On main today this test fails: ``SqliteAdapter`` has no
    ``session_scope`` method, so the test raises ``AttributeError`` —
    the failure mode for "feature not implemented yet" per the TDD
    skill. After the per-task session scoping fix lands, each task gets
    a fresh ``SafeSession`` and the test passes.
    """
    source_a = generate_id(prefix="src")
    source_b = generate_id(prefix="src")
    job_a = generate_id(prefix="job")
    job_b = generate_id(prefix="job")
    _seed_source(adapter, source_a)
    _seed_source(adapter, source_b)

    async def handler(source_id: str, job_id: str) -> None:
        async with adapter.session_scope():
            adapter.create_extraction_job(
                job_id=job_id,
                source_id=source_id,
                database_name="default",
            )
            # Yield mid-handler to let the sibling task interleave on the
            # event loop. Without per-task scoping the sibling would
            # observe shared session state; with the fix each task has
            # its own SafeSession and no interleaving is visible.
            await asyncio.sleep(0)

    await asyncio.gather(
        handler(source_a, job_a),
        handler(source_b, job_b),
    )

    assert adapter.get_extraction_job(job_a) is not None, (
        "extraction job A must persist after concurrent handler completes"
    )
    assert adapter.get_extraction_job(job_b) is not None, (
        "extraction job B must persist after concurrent handler completes"
    )


@pytest.mark.asyncio
async def test_session_scope_returns_distinct_sessions_per_task(
    adapter: SqliteAdapter,
) -> None:
    """Each concurrent task's ``session_scope`` must yield its own session.

    Pins the core invariant: ``SqliteAdapter.session`` inside one task's
    scope is a different ``SafeSession`` instance from inside a sibling
    task's scope, even when running on the same event loop. This is
    what eliminates the shared-state class of bugs entirely — not just
    the specific rollback symptom the 2026-05-20 hotfix addressed.
    """
    captured: dict[str, object] = {}
    barrier = asyncio.Event()

    async def task_a() -> None:
        async with adapter.session_scope():
            captured["a"] = adapter.session
            barrier.set()
            await asyncio.sleep(0.01)

    async def task_b() -> None:
        await barrier.wait()
        async with adapter.session_scope():
            captured["b"] = adapter.session

    await asyncio.gather(task_a(), task_b())

    assert captured["a"] is not None
    assert captured["b"] is not None
    assert captured["a"] is not captured["b"], (
        "concurrent tasks must each receive their own SafeSession instance — "
        "shared sessions are the root cause of the 2026-05-20 data-loss race"
    )


@pytest.mark.asyncio
async def test_commit_handler_invariants_hold_inside_session_scope(
    adapter: SqliteAdapter,
) -> None:
    """Commit-pipeline invariants survive inside ``session_scope()``.

    The commit handler relies on three behaviours that must NOT regress
    when ``_execute_handler`` wraps every dispatch in a fresh scope:

    1. **Nested ``adapter.transaction()`` depth tracking.** Up to four
       nested ``with adapter.transaction():`` blocks exist in
       ``commit_service.commit()`` (lines 351, 587, 989, 1556). Only the
       outermost should commit; inner exits must flush only. The depth
       counter lives on the session, so swapping in a per-task session
       must continue to coordinate depth correctly.

    2. **Adapter writes + graph-repo writes share one session.** The
       commit handler interleaves ``adapter.update_*`` calls with
       ``graph_repository.create_node`` / ``create_edge`` calls inside
       the same ``with adapter.transaction():`` block. If the repo
       wrote through a different session those writes would land on a
       separate SQLite connection and self-deadlock against the
       adapter's writer-lock holder.

    3. **Commit at the outer exit, not the inner exits.** Rows must NOT
       be visible to a fresh ``SafeSession`` until the outermost
       ``transaction()`` block has exited.
    """
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.adapter import _current_session
    from chaoscypher_core.adapters.sqlite.models import ChunkExtractionJob
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession

    source_id = generate_id(prefix="src")
    job_id = generate_id(prefix="job")
    _seed_source(adapter, source_id)

    # Build a GraphRepository on the adapter's fallback session — same
    # construction shape as ``chaoscypher_neuron.setup.shared.setup_shared``.
    fallback = adapter.session
    assert fallback is not None, "adapter.connect() must populate fallback session"
    graph_repo = GraphRepository(fallback, "default")

    async with adapter.session_scope():
        # The scoped session is in scope — both adapter and graph_repo
        # must resolve ``.session`` to it.
        scoped = _current_session.get()
        assert scoped is not None, "session_scope must install a session in the ContextVar"
        assert adapter.session is scoped, "adapter.session must read the scoped ContextVar"
        assert graph_repo.session is scoped, (
            "GraphRepository.session must read the same ContextVar so adapter "
            "and graph repo share one session per handler dispatch"
        )

        # Depth invariant: outer + inner transactions, only the outer commits.
        with adapter.transaction():
            assert scoped._transaction_depth == 1

            with adapter.transaction():
                assert scoped._transaction_depth == 2
                adapter.create_extraction_job(
                    job_id=job_id,
                    source_id=source_id,
                    database_name="default",
                )

            assert scoped._transaction_depth == 1, (
                "inner transaction exit must NOT commit; depth returns to 1"
            )

        assert scoped._transaction_depth == 0, "outer transaction exit must release depth to 0"

    # After the scope closes, a fresh SafeSession on the same engine must
    # observe the committed row — confirms outer-exit commit fired.
    assert adapter._engine is not None
    verify = SafeSession(adapter._engine)
    try:
        found = verify.exec(
            select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
        ).first()
        assert found is not None, (
            "outer transaction()'s commit must persist writes that subsequent sessions can observe"
        )
    finally:
        verify.close()


def test_current_session_returns_none_outside_any_scope() -> None:
    """``_current_session`` must default to ``None`` and reset on scope exit.

    Pins the scope-leak guarantee: queue handlers that exit cleanly OR
    via exception must not leave a session bound to the ContextVar — a
    leaked binding would mean the NEXT handler dispatched on this task
    starts from a stale scope instead of getting its own fresh session.
    """
    from chaoscypher_core.adapters.sqlite.adapter import _current_session

    assert _current_session.get() is None, (
        "no scope is active at import-time / between tests — ContextVar must read None"
    )


@pytest.mark.asyncio
async def test_session_scope_clears_contextvar_on_exit(
    adapter: SqliteAdapter,
) -> None:
    """``session_scope`` must reset the ContextVar on both clean and error exit.

    Sister of ``test_current_session_returns_none_outside_any_scope``.
    Verifies that the reset happens via the scope's ``finally`` — so an
    exception inside the handler doesn't leak the session binding into
    later code on the same task.
    """
    from chaoscypher_core.adapters.sqlite.adapter import _current_session

    assert _current_session.get() is None, "must start outside any scope"

    async with adapter.session_scope():
        assert _current_session.get() is not None, "scope must install a session"

    assert _current_session.get() is None, "clean exit must reset the ContextVar"

    with pytest.raises(RuntimeError, match="probe"):  # noqa: PT012 - asserts session state mid-scope
        async with adapter.session_scope():
            assert _current_session.get() is not None
            msg = "probe"
            raise RuntimeError(msg)

    assert _current_session.get() is None, (
        "exception exit must still reset the ContextVar — otherwise the next "
        "handler dispatch on this task would inherit a stale scope"
    )
