# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: ``_run_commit`` must not hold the SQLite writer lock across awaits.

The 2026-05-20 in-vivo capture (22:06:31 → 22:07:31) showed an
``import_commit`` task holding the SQLite writer lock during an Ollama
embedding HTTP call. A sibling ``import_analysis`` task on its own
per-task ``session_scope()`` connection waited the full 60s busy_timeout
on ``try_claim_extraction`` and then failed as ``task_failed_permanent``
because ``classify_error`` does not treat "database is locked" as
transient.

Root cause: the outer transaction at
``packages/core/src/chaoscypher_core/operations/importing/import_service.py:1230``
wraps the entire ``commit_service.commit()`` call — including the
"post-transaction phase" at ``commit_service.py:818+`` whose comment
claims "no transaction is held across the await." That claim is true
for the *inner* ``with self.adapter.transaction():`` block; it is
false for the outer one.

These tests drive the production ``ImportOperationsService._run_commit``
entry point with a stubbed ``SourceCommitService`` whose ``.commit()``
records the adapter transaction depth at call time and yields to the
event loop. After the root fix (drop outer txn at import_service.py:1230,
fold ``clear_source_commit_payload`` into the inner commit txn), every
recorded depth is 0 and two concurrent ``_run_commit`` calls run without
contending on the writer lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.utils.id import generate_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """File-backed adapter with all tables created (CC040 forbids ``:memory:``)."""
    db_dir = tmp_path / "chaoscypher-concurrent-commit"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
    """Seed a source row in pre-commit state ("extracted")."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "text",
            "file_size": 10,
            "content_hash": generate_id(),
            "status": "extracted",
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _txn_depth(adapter: SqliteAdapter) -> int:
    """Read ``SafeSession._transaction_depth`` for the current scope's session.

    Returns 0 when no session is active OR when no transaction is open. A
    positive depth means at least one ``with adapter.transaction():`` block
    is on the stack — i.e. the SQLite writer lock is held by this task.
    """
    session = adapter.session
    if session is None:
        return 0
    return int(getattr(session, "_transaction_depth", 0))


def _make_service(adapter: SqliteAdapter) -> Any:
    """Build a minimally-wired ImportOperationsService backed by a real adapter."""
    from chaoscypher_core.operations.importing.import_service import (
        ImportOperationsService,
    )

    service = ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=None,
        llm_service=MagicMock(),
        source_repository=adapter,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
    )
    service.search_repository = MagicMock()
    return service


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.current_database = "default"
    settings.priorities.background = 50
    settings.search.vector_dimensions = 384
    settings.embedding.model = "test-model"
    return settings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_commit_does_not_hold_transaction_across_commit_service_await(
    adapter: SqliteAdapter,
    structlog_for_caplog: Any,
) -> None:
    """``ImportOperationsService._run_commit`` must not wrap ``commit_service.commit()``
    in ``with adapter.transaction():``.

    Drives the real ``_run_commit`` with a fake commit service whose
    ``.commit()`` records the adapter's transaction depth at entry.
    After the root fix, that depth must be 0 — the commit service owns
    its own atomicity boundaries and must not run inside an outer
    transaction held by the import service.
    """
    source_id = generate_id(prefix="src")
    _seed_source(adapter, source_id)

    depth_at_commit_call: list[int] = []

    class _DepthRecordingCommitService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def commit(self, *args: object, **kwargs: object) -> dict[str, Any]:
            depth_at_commit_call.append(_txn_depth(adapter))
            # Yield so siblings (in the concurrent test) can interleave.
            await asyncio.sleep(0)
            return {
                "created_nodes": [],
                "created_edges": [],
                "created_templates": [],
            }

    service = _make_service(adapter)
    settings = _make_settings()

    with (
        patch(
            "chaoscypher_core.services.sources.engine.commit.SourceCommitService",
            _DepthRecordingCommitService,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=MagicMock(),
        ),
        patch("chaoscypher_core.operations.importing.import_service.queue_client") as mock_qc,
    ):
        mock_qc.enqueue_task = AsyncMock(return_value="task-snap-1")
        await service._run_commit(
            file_id=source_id,
            commit_data={},
            file_info_dict={"filename": f"{source_id}.txt"},
            auto_enable=False,
            settings=settings,
        )

    assert depth_at_commit_call == [0], (
        "commit_service.commit() must be invoked OUTSIDE any adapter.transaction() "
        "block — the outer transaction at import_service.py:1230 holds the SQLite "
        "writer lock during the embedding HTTP call inside commit_service. "
        f"observed depths at commit() entry: {depth_at_commit_call!r}"
    )


@pytest.mark.asyncio
async def test_two_concurrent_run_commits_do_not_block_on_writer_lock(
    adapter: SqliteAdapter,
    structlog_for_caplog: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two concurrent ``_run_commit`` calls must complete without contention.

    Reproduces the in-vivo deadlock shape: each task enters its own
    per-task ``session_scope()`` (via ``adapter.session_scope()``), then
    drives ``_run_commit``. ``_run_commit`` wraps ``commit_service.commit()``
    in ``with adapter.transaction():`` (the bug). Inside the patched
    commit service, we record both:
      1. ``_txn_depth(adapter)`` at entry — to catch the structural
         anti-pattern.
      2. An asyncio event barrier — to force overlap. If task A holds
         the writer lock during its await, task B cannot enter its own
         outer ``adapter.transaction()`` block, and the barrier never
         opens. The test deadlocks until the asyncio timeout fires.

    After the root fix:
      - No outer transaction is held during ``commit_service.commit()``.
      - The barrier opens on schedule and both tasks reach completion.
      - Both observed depths at commit() entry are 0.
      - No ``"database is locked"`` shows up in the captured logs.
    """
    source_a = generate_id(prefix="src")
    source_b = generate_id(prefix="src")
    _seed_source(adapter, source_a)
    _seed_source(adapter, source_b)

    observed_depths: list[tuple[str, int]] = []
    barrier = asyncio.Event()
    inside_count = 0
    inside_lock = asyncio.Lock()

    class _OverlappingCommitService:
        """Records txn depth on entry then waits for the sibling to overlap."""

        def __init__(self, **kwargs: object) -> None:
            pass

        async def commit(
            self,
            file_id: str,
            commit_data: dict[str, Any],
            file_info_dict: dict[str, Any],
            *,
            auto_enable: bool = False,
            **kwargs: object,
        ) -> dict[str, Any]:
            nonlocal inside_count
            depth_before = _txn_depth(adapter)
            observed_depths.append((file_id, depth_before))
            async with inside_lock:
                inside_count += 1
                if inside_count == 2:
                    barrier.set()
            # Wait for the sibling task to also be inside commit().
            # If the writer lock is held during the outer-txn-wrapped
            # commit() await, the second task is blocked entering its
            # own outer txn and never reaches this point — the barrier
            # never opens and the wait_for times out.
            await asyncio.wait_for(barrier.wait(), timeout=3.0)
            return {
                "created_nodes": [],
                "created_edges": [],
                "created_templates": [],
            }

    service = _make_service(adapter)
    settings = _make_settings()

    async def drive(file_id: str) -> dict[str, Any]:
        """Wrap ``_run_commit`` in a per-task session_scope (mimics queue worker)."""
        async with adapter.session_scope():
            return await service._run_commit(
                file_id=file_id,
                commit_data={},
                file_info_dict={"filename": f"{file_id}.txt"},
                auto_enable=False,
                settings=settings,
            )

    with (
        patch(
            "chaoscypher_core.services.sources.engine.commit.SourceCommitService",
            _OverlappingCommitService,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=MagicMock(),
        ),
        patch("chaoscypher_core.operations.importing.import_service.queue_client") as mock_qc,
    ):
        mock_qc.enqueue_task = AsyncMock(return_value="task-snap")
        try:
            await asyncio.wait_for(
                asyncio.gather(drive(source_a), drive(source_b)),
                timeout=8.0,
            )
        except TimeoutError:
            pytest.fail(
                "concurrent _run_commit calls deadlocked — second task could "
                "not enter its outer adapter.transaction() block while the first "
                "task held the writer lock during the patched commit_service.commit() "
                "await. This is the 2026-05-20 in-vivo regression. "
                f"observed_depths={observed_depths!r}",
            )

    depth_violations = [(sid, d) for sid, d in observed_depths if d > 0]
    assert not depth_violations, (
        "writer lock was held during external I/O inside commit_service.commit() — "
        "the 2026-05-20 root cause. Fix: drop the outer "
        "`with adapter.transaction():` at import_service.py:1230 and fold "
        "clear_source_commit_payload into the inner commit txn. "
        f"violations: {depth_violations!r}"
    )

    locked_lines = [
        rec for rec in caplog.records if "database is locked" in rec.getMessage().lower()
    ]
    assert not locked_lines, (
        f"unexpected 'database is locked' log line(s): {[r.getMessage() for r in locked_lines]!r}"
    )


@pytest.mark.asyncio
async def test_embed_created_templates_does_not_hold_session_transaction_across_await(
    adapter: SqliteAdapter,
    structlog_for_caplog: Any,
) -> None:
    """``_embed_created_templates`` must not hold the SQLAlchemy session's
    implicit transaction across the per-template embedding HTTP call.

    Regression for the SECOND iteration of the 2026-05-20 writer-lock-
    contention fix: the original audit removed the OUTER explicit
    ``with adapter.transaction():`` at ``import_service.py:1230``, but in-vivo
    verification at 2026-05-21T00:08:45 still surfaced ``OperationalError(
    "database is locked")`` on sibling ``import_analysis`` tasks. Reading
    the deployed code revealed the second cause: the per-template loop in
    ``commit_service._embed_created_templates`` reads + writes through the
    shared session WITHOUT an explicit ``session.commit()`` between
    iterations. SQLAlchemy's autobegin semantics open an implicit
    transaction on the first read/write that stays open until the next
    ``commit()`` — so the writer lock is held across every per-template
    LLM HTTP call in the loop. With ~15 templates at ~4s each (Qwen8B
    embedding) that's ~60s of held writer lock, exactly matching the
    observed busy_timeout exhaustion.

    Note: ``_txn_depth()`` only sees the EXPLICIT depth counter set by
    ``with adapter.transaction():``. This test uses ``session.in_transaction()``
    to catch the IMPLICIT autobegin transaction the original tests miss.
    """
    from unittest.mock import MagicMock as _MagicMock

    from chaoscypher_core.services.sources.engine.commit.service import (
        SourceCommitService,
    )

    in_transaction_during_await: list[bool] = []

    class _TxnSensingEmbeddingService:
        """Fake embedding service that records session.in_transaction() at call time."""

        model_name = "test-model"

        async def embed(self, text: str) -> Any:
            session = adapter.session
            if session is not None:
                # session.in_transaction() returns True iff the implicit
                # SQLAlchemy autobegin transaction is currently open.
                in_transaction_during_await.append(bool(session.in_transaction()))
            return _EmbeddingResult(embedding=[0.0] * 8)

    class _EmbeddingResult:
        def __init__(self, embedding: list[float]) -> None:
            self.embedding = embedding

    # Build SourceCommitService bypassing __init__ — we only need the methods
    # under test, not the full handler/setting wiring.
    svc = SourceCommitService.__new__(SourceCommitService)
    svc.adapter = adapter  # type: ignore[assignment]
    svc.search_repository = _MagicMock()
    svc.graph_repository = _MagicMock()
    svc._embedding_provider = _TxnSensingEmbeddingService()  # type: ignore[assignment]

    # Three fake templates — enough iterations to surface the implicit-txn
    # holding across awaits if the fix regresses.
    template_objs = [_MagicMock(name=f"name-{i}", description=f"desc-{i}") for i in range(3)]
    for i, template in enumerate(template_objs):
        template.name = f"name-{i}"
        template.description = f"desc-{i}"
    svc.graph_repository.get_template = _MagicMock(side_effect=template_objs)

    # Simulate the real call-site context: a prior write inside the
    # post-transaction phase (e.g. mark_search_indexing_indexed) has left
    # the session with an open implicit autobegin transaction. Without an
    # explicit session.commit() inside _embed_created_templates, that
    # transaction would stay open across every per-template embedding await.
    from sqlalchemy import text as _sql_text

    session_for_test = adapter.session
    assert session_for_test is not None, "fixture must yield a connected adapter"
    session_for_test.execute(_sql_text("SELECT 1"))  # opens implicit txn
    assert session_for_test.in_transaction(), (
        "test precondition: implicit autobegin txn must be open before "
        "the function-under-test runs — otherwise the test cannot detect "
        "the in-transaction-during-await regression"
    )

    await svc._embed_created_templates(
        ["tpl-a", "tpl-b", "tpl-c"],
        session=session_for_test,
    )

    assert in_transaction_during_await == [False, False, False], (
        "session was inside an open SQLAlchemy transaction during one or "
        "more per-template embedding awaits — this holds the SQLite "
        "writer lock across the LLM HTTP call and starves sibling handlers' "
        "writes. Fix: ``session.commit()`` before the await loop and after "
        "each per-iteration write so the implicit autobegin transaction "
        "is released between LLM calls. "
        f"observed in_transaction snapshots: {in_transaction_during_await!r}"
    )
