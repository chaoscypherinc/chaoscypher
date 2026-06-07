# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Task 6.2: search orphan-sweep worker.

Uses a real SQLite engine (file-backed via tmp_path) and the real models so
schema constraints, triggers, and FTS5 virtual-table behaviour match
production.  Each test constructs a minimal adapter + search_repo pair,
seeds the required rows, calls sweep_search_indexes() directly, and asserts
the expected side-effects.
"""

from __future__ import annotations

import base64
import struct
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest  # noqa: F401 — kept for pytest collection
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    GraphNode,
    GraphTemplate,
    PendingSearchIndex,
)
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository


# ---------------------------------------------------------------------------
# Helpers
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
    """Insert the template a test node will reference.

    Migration 0014 added the FK graph_nodes.template_id → graph_templates.id;
    SQLModel.metadata.create_all wires that FK in fresh schemas, so node
    inserts must follow a template insert.
    """
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
    """Return a base64-encoded float32 numpy array suitable for DocumentChunk.embedding."""
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


def _insert_fts_row(adapter: SqliteAdapter, node_id: str) -> None:
    """Insert a fulltext_content row directly (bypassing ORM)."""
    adapter.session.exec(  # type: ignore[call-overload]
        text(
            "INSERT OR REPLACE INTO fulltext_content "
            "(node_id, label, properties, searchable_text) "
            "VALUES (:nid, 'orphan', '{}', 'orphan text')"
        ),
        params={"nid": node_id},
    )
    adapter.session.commit()


def _insert_vec_row(adapter: SqliteAdapter, item_id: str) -> None:
    """Insert a vec_search_nodes row directly for an orphaned node."""
    blob = struct.pack("4f", 0.1, 0.2, 0.3, 0.4)
    adapter.session.exec(  # type: ignore[call-overload]
        text("INSERT INTO vec_search_nodes (embedding, item_id) VALUES (:emb, :iid)"),
        params={"emb": blob, "iid": item_id},
    )
    adapter.session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sweep_deletes_fts_rows_with_no_graph_node(tmp_path: Path) -> None:
    """FTS5 rows whose node_id is not in graph_nodes must be removed."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        orphan_id = "orphan-fts-node"
        _insert_fts_row(adapter, orphan_id)

        row = adapter.session.exec(  # type: ignore[call-overload]
            text("SELECT node_id FROM fulltext_content WHERE node_id = :nid"),
            params={"nid": orphan_id},
        ).fetchone()
        assert row is not None, "Pre-condition: FTS row should exist"

        stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        row_after = adapter.session.exec(  # type: ignore[call-overload]
            text("SELECT node_id FROM fulltext_content WHERE node_id = :nid"),
            params={"nid": orphan_id},
        ).fetchone()
        assert row_after is None, "Orphaned FTS row should be deleted after sweep"
        assert stats["fts_orphans"] >= 1
    finally:
        adapter.disconnect()


def test_sweep_deletes_vec_rows_with_no_graph_node(tmp_path: Path) -> None:
    """vec_search_nodes rows whose item_id is gone must be removed."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        orphan_id = "orphan-vec-node"
        _insert_vec_row(adapter, orphan_id)

        row = adapter.session.exec(  # type: ignore[call-overload]
            text("SELECT item_id FROM vec_search_nodes WHERE item_id = :iid"),
            params={"iid": orphan_id},
        ).fetchone()
        assert row is not None, "Pre-condition: vec row should exist"

        stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        row_after = adapter.session.exec(  # type: ignore[call-overload]
            text("SELECT item_id FROM vec_search_nodes WHERE item_id = :iid"),
            params={"iid": orphan_id},
        ).fetchone()
        assert row_after is None, "Orphaned vec row should be deleted after sweep"
        assert stats["vec_orphans"] >= 1
    finally:
        adapter.disconnect()


def test_sweep_drains_pending_search_index_success(tmp_path: Path) -> None:
    """PendingSearchIndex rows whose target node exists are re-indexed and removed."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        node = _make_node(adapter, "live-node-001")

        pending = PendingSearchIndex(
            id="node:live-node-001",
            kind="node",
            item_id="live-node-001",
            source_id=None,
            attempts=0,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        remaining = adapter.session.get(PendingSearchIndex, "node:live-node-001")
        assert remaining is None, "Pending row should be deleted after successful re-index"
        assert stats["pending_drained"] == 1
        assert stats["pending_failed"] == 0

        fts_row = adapter.session.exec(  # type: ignore[call-overload]
            text("SELECT node_id FROM fulltext_content WHERE node_id = :nid"),
            params={"nid": node.id},
        ).fetchone()
        assert fts_row is not None, "Node should be in FTS5 after drain"
    finally:
        adapter.disconnect()


def test_sweep_records_error_on_persistent_failure(tmp_path: Path) -> None:
    """PendingSearchIndex rows that fail to index: attempts bumped, last_error set."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        _make_node(adapter, "error-node-001")

        pending = PendingSearchIndex(
            id="node:error-node-001",
            kind="node",
            item_id="error-node-001",
            source_id=None,
            attempts=2,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        with patch.object(search_repo, "index_node", side_effect=RuntimeError("index exploded")):
            stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        adapter.session.expire_all()
        updated = adapter.session.get(PendingSearchIndex, "node:error-node-001")
        assert updated is not None, "Pending row should survive a failure"
        assert updated.attempts == 3, f"attempts should be bumped to 3, got {updated.attempts}"
        assert updated.last_error is not None
        assert "index exploded" in updated.last_error
        assert stats["pending_failed"] == 1
        assert stats["pending_drained"] == 0
    finally:
        adapter.disconnect()


# ---------------------------------------------------------------------------
# Workstream 10 — vector_indexing_status transitions driven by the sweep.
# ---------------------------------------------------------------------------


def _seed_source(adapter: SqliteAdapter, source_id: str, status: str) -> None:
    """Insert a SourceRow with a specified vector_indexing_status."""
    from chaoscypher_core.adapters.sqlite.models import SourceRow

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


def test_sweep_flips_source_to_indexed_on_successful_drain(tmp_path: Path) -> None:
    """After a degraded source's pending entry drains, status flips to 'indexed'."""
    from chaoscypher_core.adapters.sqlite.models import SourceRow
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        _make_node(adapter, "live-node-002")
        _seed_source(adapter, "src-degraded-1", status="degraded")

        pending = PendingSearchIndex(
            id="node:live-node-002",
            kind="node",
            item_id="live-node-002",
            source_id="src-degraded-1",
            attempts=1,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        sweep_search_indexes(adapter, search_repo, max_attempts=5)

        adapter.session.expire_all()
        row = adapter.session.get(SourceRow, "src-degraded-1")
        assert row is not None
        assert row.vector_indexing_status == "indexed", (
            f"sweep success on a degraded source must promote it to 'indexed'; "
            f"got {row.vector_indexing_status!r}"
        )
        assert row.vector_indexed_at is not None, (
            "vector_indexed_at must be timestamped when the sweep recovers indexing"
        )
    finally:
        adapter.disconnect()


def test_sweep_flips_source_to_failed_when_retries_exhausted(tmp_path: Path) -> None:
    """After max_attempts failures, the pending row is removed and source -> 'failed'."""
    from chaoscypher_core.adapters.sqlite.models import SourceRow
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        _make_node(adapter, "doomed-node-001")
        _seed_source(adapter, "src-doomed-1", status="degraded")

        # attempts=4 means the next failure (attempts -> 5) hits the
        # default max_attempts cap and triggers the retry-exhausted path.
        pending = PendingSearchIndex(
            id="node:doomed-node-001",
            kind="node",
            item_id="doomed-node-001",
            source_id="src-doomed-1",
            attempts=4,
        )
        adapter.session.add(pending)
        adapter.session.commit()

        with patch.object(search_repo, "index_node", side_effect=RuntimeError("permanent failure")):
            stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        adapter.session.expire_all()
        # Pending entry should be evicted from the queue.
        remaining = adapter.session.get(PendingSearchIndex, "node:doomed-node-001")
        assert remaining is None, "Exhausted pending entry must be removed"

        # Source row should reflect the terminal failure state.
        row = adapter.session.get(SourceRow, "src-doomed-1")
        assert row is not None
        assert row.vector_indexing_status == "failed", (
            f"retry-exhausted source must transition to 'failed'; "
            f"got {row.vector_indexing_status!r}"
        )
        assert stats["pending_exhausted"] >= 1
    finally:
        adapter.disconnect()
