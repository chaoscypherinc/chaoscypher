# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Task 6.4: end-to-end verification that commit-failed indexing lands in
PendingSearchIndex and the sweep worker drains it.

Confirms Task 4.1 (_enqueue_search_retry) and Task 6.2
(sweep_search_indexes) interoperate end-to-end: a failed post-txn
indexing writes recoverable rows into PendingSearchIndex, and a
subsequent sweep call either drains them (success) or bumps attempts
+ records last_error (persistent failure).

Lives in packages/neuron/tests/unit/ because it imports
chaoscypher_neuron.search_sweep (Task 6.2) and requires PYTHONPATH=src
to resolve the local neuron package correctly. Named
test_commit_sweep_integration.py to signal cross-package scope.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sqlalchemy import text
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphNode, GraphTemplate, PendingSearchIndex
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository


# ---------------------------------------------------------------------------
# Helpers (lifted from test_search_sweep.py)
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


class _FakeAdapter:
    """Minimal adapter stub for _enqueue_search_retry.

    Provides the three attributes SourceCommitService._enqueue_search_retry
    needs: ``session`` (for SQLAlchemy execute), ``transaction()`` (context
    manager that commits on exit), and ``enqueue_pending_search_index``
    (the search-retry-queue mixin's INSERT OR IGNORE write).
    """

    def __init__(self, session):
        """Wrap an existing SQLModel session.

        Args:
            session: Open SQLModel session to use for writes.

        """
        self.session = session

    @contextlib.contextmanager
    def transaction(self):
        """Commit session on successful exit.

        Yields:
            None

        """
        yield
        self.session.commit()

    def enqueue_pending_search_index(self, *, rows: list[dict[str, Any]]) -> None:
        """INSERT OR IGNORE rows into pending_search_index.

        Mirrors SearchRetryQueueMixin.enqueue_pending_search_index — the test
        wraps a raw SQLModel session that doesn't carry the mixin, so we
        write rows via the ORM model directly. INSERT OR IGNORE is approximated
        by catching the unique-constraint failure (the only kind enqueued in
        these tests is fresh, so collisions never happen — kept for parity).
        """
        if not rows:
            return
        from chaoscypher_core.adapters.sqlite.models import (
            PendingSearchIndex as _PendingSearchIndex,
        )

        for row in rows:
            self.session.add(
                _PendingSearchIndex(
                    id=f"{row['kind']}:{row['item_id']}",
                    kind=row["kind"],
                    item_id=row["item_id"],
                    source_id=row.get("source_id"),
                )
            )


def _make_enqueue_service(adapter: _FakeAdapter):
    """Return a SourceCommitService instance wired only with adapter.

    Uses the same __new__ + manual-attribute pattern as the Task 4.1 unit
    tests so the heavy constructor (graph_repo, search_repo, settings, …)
    is never called.

    Args:
        adapter: Fake adapter whose session is used for writes.

    Returns:
        SourceCommitService with adapter injected.

    """
    from chaoscypher_core.services.sources.engine.commit.service import (
        SourceCommitService,
    )

    service = SourceCommitService.__new__(SourceCommitService)
    service.adapter = adapter
    return service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_enqueue_then_sweep_drains_pending_node_entries(tmp_path: Path) -> None:
    """A node id written by _enqueue_search_retry is drained by the next sweep."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        # Seed a real GraphNode so sweep re-indexing has a live target.
        _make_node(adapter, "n1")

        # Simulate what the commit pipeline's post-txn except-block does.
        fake = _FakeAdapter(adapter.session)
        service = _make_enqueue_service(fake)
        service._enqueue_search_retry(["n1"], source_id="src-1", kind="node")

        # Pre-condition: one pending row, correct metadata.
        rows = adapter.session.exec(select(PendingSearchIndex)).all()
        assert len(rows) == 1
        assert rows[0].kind == "node"
        assert rows[0].item_id == "n1"
        assert rows[0].source_id == "src-1"

        # Act: run one sweep cycle.
        stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        # Row drained.
        assert stats["pending_drained"] == 1
        assert stats["pending_failed"] == 0

        remaining = adapter.session.exec(select(PendingSearchIndex)).all()
        assert remaining == [], "PendingSearchIndex should be empty after a successful drain"

        # Node must now appear in FTS5.
        fts_row = adapter.session.exec(  # type: ignore[call-overload]
            text("SELECT node_id FROM fulltext_content WHERE node_id = :nid"),
            params={"nid": "n1"},
        ).fetchone()
        assert fts_row is not None, "Node should appear in FTS5 after drain"
    finally:
        adapter.disconnect()


def test_sweep_records_failure_when_indexing_raises(tmp_path: Path) -> None:
    """If re-indexing raises, the row survives with attempts bumped + last_error set."""
    from chaoscypher_neuron.search_sweep import sweep_search_indexes

    adapter, search_repo = _make_db(tmp_path)
    try:
        _make_node(adapter, "n2")

        fake = _FakeAdapter(adapter.session)
        service = _make_enqueue_service(fake)
        service._enqueue_search_retry(["n2"], source_id="src-2", kind="node")

        # Pre-condition: attempts is 0 on a freshly enqueued row.
        rows = adapter.session.exec(select(PendingSearchIndex)).all()
        assert len(rows) == 1
        assert rows[0].attempts == 0

        with patch.object(
            search_repo, "index_node", side_effect=RuntimeError("intentional failure")
        ):
            stats = sweep_search_indexes(adapter, search_repo, max_attempts=5)

        assert stats["pending_failed"] == 1
        assert stats["pending_drained"] == 0

        # Row must still exist with bumped counter and recorded error.
        adapter.session.expire_all()
        updated = adapter.session.get(PendingSearchIndex, "node:n2")
        assert updated is not None, "Row should survive a re-index failure"
        assert updated.attempts == 1, f"attempts should be bumped to 1, got {updated.attempts}"
        assert updated.last_error is not None
        assert "intentional failure" in updated.last_error
    finally:
        adapter.disconnect()
