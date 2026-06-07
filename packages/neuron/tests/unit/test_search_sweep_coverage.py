# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Additional coverage for the search orphan-sweep worker.

Complements ``test_search_sweep.py`` by exercising the chunk-drain path,
the node-target-gone branch, unknown-kind handling, orphan-cleanup SQL
failure recovery, ``_graph_node_to_pydantic`` position conversion, and the
``_search_sweep_loop`` cancellation / exception-swallow behaviour.

Reuses the real-SQLite helpers (``_make_db`` / ``_make_node`` /
``_make_chunk`` / ``_seed_source``) from ``test_search_sweep.py``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import text
from sqlmodel import SQLModel
from structlog.testing import capture_logs

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphNode,
    GraphTemplate,
    PendingSearchIndex,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository


# ---------------------------------------------------------------------------
# Real-SQLite helpers — copied from test_search_sweep.py. Sibling test modules
# are NOT importable by bare name under --import-mode=importlib (the official
# run mode), so we keep this file self-contained.
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> tuple[SqliteAdapter, SearchRepository]:
    """Return a connected adapter + search repo sharing the same app.db."""
    db_dir = tmp_path / "test-db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()

    search_repo = SearchRepository(engine, vector_dim=4)
    return adapter, search_repo


def _ensure_template(adapter: SqliteAdapter, template_id: str = "tmpl-1") -> None:
    """Insert the template a test node will reference (FK graph_nodes.template_id)."""
    existing = adapter.session.get(GraphTemplate, template_id)
    if existing is not None:
        return
    adapter.session.add(
        GraphTemplate(
            id=template_id,
            database_name="default",
            name="Test",
            template_type="node",
        )
    )
    adapter.session.commit()


def _make_node(adapter: SqliteAdapter, node_id: str) -> GraphNode:
    """Insert a minimal GraphNode and return it."""
    _ensure_template(adapter)
    node = GraphNode(
        id=node_id,
        database_name="default",
        graph_name="knowledge",
        template_id="tmpl-1",
        label="Test Node",
    )
    adapter.session.add(node)
    adapter.session.commit()
    return node


def _make_embedding_blob(dim: int = 4) -> bytes:
    """Return a base64-encoded float32 numpy array for DocumentChunk.embedding."""
    arr = np.ones(dim, dtype=np.float32)
    return base64.b64encode(arr.tobytes())


def _make_chunk(adapter: SqliteAdapter, chunk_id: str, source_id: str) -> DocumentChunk:
    """Insert a minimal DocumentChunk with a valid embedding blob."""
    chunk = DocumentChunk(
        id=chunk_id,
        database_name="default",
        source_id=source_id,
        chunk_index=0,
        content="hello world",
        embedding=_make_embedding_blob(4),
        embedding_dimensions=4,
    )
    adapter.session.add(chunk)
    adapter.session.commit()
    return chunk


def _seed_source(adapter: SqliteAdapter, source_id: str, status: str) -> None:
    """Insert a SourceRow with a specified vector_indexing_status."""
    adapter.session.add(
        SourceRow(
            id=source_id,
            database_name="default",
            filename=f"{source_id}.txt",
            filepath=f"/tmp/{source_id}.txt",
            file_type="text",
            file_size=10,
            content_hash=f"hash-{source_id}",
            status="indexed",
            vector_indexing_status=status,
        )
    )
    adapter.session.commit()


# ---------------------------------------------------------------------------
# kind="chunk" drain
# ---------------------------------------------------------------------------


def test_sweep_drains_chunk_pending_and_reindexes(tmp_path: Path) -> None:
    """A pending chunk entry re-indexes the source's chunks and is drained."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        source_id = "chunk-src-1"
        # document_chunks.source_id has an FK to sources.id — seed it first.
        _seed_source(adapter, source_id, status="degraded")
        _make_chunk(adapter, "chunk-1", source_id)

        pending = PendingSearchIndex(
            id="chunk:chunk-src-1",
            kind="chunk",
            item_id=source_id,
            source_id=None,
            attempts=0,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        remaining = adapter.session.get(PendingSearchIndex, "chunk:chunk-src-1")
        assert remaining is None, "Chunk pending row should be drained after re-index"
        assert stats["pending_drained"] == 1
        assert stats["pending_failed"] == 0

        # The chunk should now be present in vec_search_chunks.
        row = adapter.session.exec(  # type: ignore[call-overload]
            text("SELECT item_id FROM vec_search_chunks WHERE item_id = :iid"),
            params={"iid": "chunk:chunk-1"},
        ).fetchone()
        assert row is not None, "Chunk should be indexed into vec_search_chunks"
    finally:
        adapter.disconnect()


def test_reindex_chunks_no_chunks_returns_early_but_drains(tmp_path: Path) -> None:
    """A chunk pending entry with no matching chunks returns early yet is drained."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        # No DocumentChunk rows for this source.
        pending = PendingSearchIndex(
            id="chunk:empty-src",
            kind="chunk",
            item_id="empty-src",
            source_id=None,
            attempts=0,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        remaining = adapter.session.get(PendingSearchIndex, "chunk:empty-src")
        assert remaining is None, "Empty-chunk pending row should still be drained"
        assert stats["pending_drained"] == 1
        assert stats["pending_failed"] == 0
    finally:
        adapter.disconnect()


# ---------------------------------------------------------------------------
# node target gone
# ---------------------------------------------------------------------------


def test_sweep_drains_node_when_target_gone(tmp_path: Path) -> None:
    """A node pending entry whose GraphNode is absent is removed + drained."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        _seed_source(adapter, "gone-src-1", status="degraded")
        pending = PendingSearchIndex(
            id="node:missing-node",
            kind="node",
            item_id="missing-node",  # no matching GraphNode
            source_id="gone-src-1",
            attempts=0,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        with patch.object(
            search_repo, "index_node", side_effect=AssertionError("must not index a gone node")
        ):
            stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        remaining = adapter.session.get(PendingSearchIndex, "node:missing-node")
        assert remaining is None, "Stale node pending row should be deleted"
        assert stats["pending_drained"] == 1
        assert stats["pending_failed"] == 0
    finally:
        adapter.disconnect()


# ---------------------------------------------------------------------------
# unknown kind
# ---------------------------------------------------------------------------


def test_sweep_warns_and_skips_unknown_kind(tmp_path: Path, structlog_for_caplog: Any) -> None:
    """A pending entry with an unknown kind logs a warning and survives."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        pending = PendingSearchIndex(
            id="bogus:thing",
            kind="bogus",
            item_id="thing",
            source_id=None,
            attempts=0,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        with capture_logs() as captured:
            stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        # Pending row with an unknown kind is left in place (not drained).
        remaining = adapter.session.get(PendingSearchIndex, "bogus:thing")
        assert remaining is not None, "Unknown-kind pending row must survive"
        assert stats["pending_drained"] == 0
        assert stats["pending_failed"] == 0

        events = [e["event"] for e in captured]
        assert "search_sweep_unknown_kind" in events
    finally:
        adapter.disconnect()


# ---------------------------------------------------------------------------
# orphan-cleanup SQL failure
# ---------------------------------------------------------------------------


def test_sweep_survives_orphan_cleanup_sql_failure(
    tmp_path: Path, structlog_for_caplog: Any
) -> None:
    """A failing DELETE in orphan cleanup is logged; the sweep still completes."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        # Live node + pending entry that should still drain after the failed DELETE.
        _make_node(adapter, "post-cleanup-node")
        pending = PendingSearchIndex(
            id="node:post-cleanup-node",
            kind="node",
            item_id="post-cleanup-node",
            source_id=None,
            attempts=0,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        session = adapter.session
        real_exec = session.exec

        def exploding_exec(statement: Any, *args: Any, **kwargs: Any) -> Any:
            sql = str(getattr(statement, "text", statement))
            if "DELETE FROM fulltext_content" in sql:
                raise RuntimeError("simulated cleanup failure")
            return real_exec(statement, *args, **kwargs)

        with (
            patch.object(session, "exec", side_effect=exploding_exec),
            capture_logs() as captured,
        ):
            stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        events = [e["event"] for e in captured]
        assert "search_sweep_fts_orphan_cleanup_failed" in events
        # Despite the cleanup failure, the pending drain still ran.
        remaining = adapter.session.get(PendingSearchIndex, "node:post-cleanup-node")
        assert remaining is None
        assert stats["pending_drained"] == 1
    finally:
        adapter.disconnect()


# ---------------------------------------------------------------------------
# _graph_node_to_pydantic position conversion
# ---------------------------------------------------------------------------


def test_graph_node_to_pydantic_builds_position() -> None:
    """When position_x/y are set, a NodePosition is created on the Node."""
    from chaoscypher_neuron.search_sweep import _graph_node_to_pydantic

    db_node = GraphNode(
        id="n-pos",
        database_name="default",
        graph_name="knowledge",
        template_id="tmpl-1",
        label="Positioned",
        position_x=12.5,
        position_y=-3.0,
    )

    node = _graph_node_to_pydantic(db_node)

    assert node.position is not None
    assert node.position.x == 12.5
    assert node.position.y == -3.0


def test_graph_node_to_pydantic_no_position() -> None:
    """With no coordinates, the resulting Node has no position."""
    from chaoscypher_neuron.search_sweep import _graph_node_to_pydantic

    db_node = GraphNode(
        id="n-nopos",
        database_name="default",
        graph_name="knowledge",
        template_id="tmpl-1",
        label="Unpositioned",
    )

    node = _graph_node_to_pydantic(db_node)

    assert node.position is None


# ---------------------------------------------------------------------------
# _search_sweep_loop — cancellation + exception swallow
# ---------------------------------------------------------------------------


def _loop_adapter() -> MagicMock:
    """A MagicMock adapter exposing an async session_scope CM."""
    adapter = MagicMock()

    @contextlib.asynccontextmanager
    async def _scope() -> Any:
        yield None

    adapter.session_scope = _scope
    return adapter


@pytest.mark.asyncio
async def test_search_sweep_loop_cancel_clean() -> None:
    """Cancelling the loop while sleeping exits cleanly without error."""
    from chaoscypher_neuron.search_sweep import _search_sweep_loop

    adapter = _loop_adapter()

    with patch(
        "chaoscypher_neuron.search_sweep.sweep_search_indexes",
        return_value={},
    ):
        task = asyncio.create_task(
            _search_sweep_loop(
                adapter=adapter,
                search_repo=MagicMock(),
                interval_seconds=0.01,
                max_attempts=5,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        # A clean cancel returns rather than re-raising.
        await task


@pytest.mark.asyncio
async def test_search_sweep_loop_swallows_sweep_exception() -> None:
    """An exception inside sweep_search_indexes is logged and the loop continues."""
    from chaoscypher_neuron.search_sweep import _search_sweep_loop

    adapter = _loop_adapter()
    call_count = 0

    def boom(*args: Any, **kwargs: Any) -> dict[str, int]:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("sweep exploded")

    with patch("chaoscypher_neuron.search_sweep.sweep_search_indexes", side_effect=boom):
        task = asyncio.create_task(
            _search_sweep_loop(
                adapter=adapter,
                search_repo=MagicMock(),
                interval_seconds=0.001,
                max_attempts=5,
            )
        )
        # Poll until the loop has demonstrably continued past the first raise
        # (rich-traceback logging makes wall-clock timing flaky, so we wait on
        # the observable side-effect instead).
        elapsed = 0.0
        while call_count < 2 and elapsed < 3.0:
            await asyncio.sleep(0.01)
            elapsed += 0.01
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # The loop swallowed the exception and kept firing across iterations.
    assert call_count >= 2, f"loop did not continue after a sweep error; got {call_count}"
