# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: delete_source_artifacts removes graph nodes/edges/templates only.

The source row, its chunks, and its embeddings must survive — that is
the difference from delete_source, which is a full cascade.

Templates whose source_id is NULL (global/manually created) must also
survive because they do not belong to any single source.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphNode,
    GraphTemplate,
    SourceRow,
)
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


@pytest.fixture
def repo(adapter: SqliteAdapter) -> GraphRepository:
    return GraphRepository(adapter.session, database_name="test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": f"{source_id}.pdf",
            "filepath": f"/data/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "committed",
        }
    )


def _ensure_template(
    adapter: SqliteAdapter,
    tpl_id: str,
    *,
    source_id: str | None = None,
    database_name: str = "test",
) -> None:
    existing = adapter.session.get(GraphTemplate, tpl_id)
    if existing is not None:
        return
    with adapter.transaction():
        adapter.session.add(
            GraphTemplate(
                id=tpl_id,
                database_name=database_name,
                name=f"name-{tpl_id}",
                template_type="node",
                source_id=source_id,
                properties=[],
            )
        )


def _seed_node(
    adapter: SqliteAdapter,
    node_id: str,
    *,
    template_id: str = "tpl",
    source_id: str | None = None,
    database_name: str = "test",
) -> None:
    _ensure_template(adapter, template_id, database_name=database_name)
    with adapter.transaction():
        adapter.session.add(
            GraphNode(
                id=node_id,
                database_name=database_name,
                graph_name="knowledge",
                template_id=template_id,
                label=f"label-{node_id}",
                source_id=source_id,
                properties={},
            )
        )


def _seed_edge(
    adapter: SqliteAdapter,
    edge_id: str,
    src_node: str,
    tgt_node: str,
    *,
    source_id: str | None = None,
    database_name: str = "test",
) -> None:
    _ensure_template(adapter, "tpl-edge", database_name=database_name)
    with adapter.transaction():
        adapter.session.add(
            GraphEdge(
                id=edge_id,
                database_name=database_name,
                graph_name="knowledge",
                source_node_id=src_node,
                target_node_id=tgt_node,
                template_id="tpl-edge",
                label=f"edge-{edge_id}",
                source_id=source_id,
                properties={},
            )
        )


def _seed_template_with_source(
    adapter: SqliteAdapter,
    tpl_id: str,
    *,
    source_id: str | None,
    database_name: str = "test",
) -> None:
    with adapter.transaction():
        adapter.session.add(
            GraphTemplate(
                id=tpl_id,
                database_name=database_name,
                name=f"name-{tpl_id}",
                template_type="node",
                source_id=source_id,
                properties=[],
            )
        )


def _count_rows(adapter: SqliteAdapter, model: type, **filters: object) -> int:
    stmt = select(model)  # type: ignore[arg-type]
    for attr, val in filters.items():
        stmt = stmt.where(getattr(model, attr) == val)
    return len(adapter.session.exec(stmt).all())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_deletes_nodes_edges_templates_for_source(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Artifacts linked to src_1 are gone; src_1 row and src_other are intact."""
    _seed_source(adapter, "src_1")
    _seed_source(adapter, "src_other")

    # src_1 artifacts
    _seed_node(adapter, "n1", source_id="src_1")
    _seed_node(adapter, "n2", source_id="src_1")
    _seed_edge(adapter, "e1", "n1", "n2", source_id="src_1")
    _seed_template_with_source(adapter, "tpl-src1", source_id="src_1")

    # src_other artifacts (must survive)
    _seed_node(adapter, "n_other", source_id="src_other")
    _seed_edge(adapter, "e_other", "n_other", "n_other", source_id="src_other")
    _seed_template_with_source(adapter, "tpl-other", source_id="src_other")

    result = repo.delete_source_artifacts("src_1")

    # Correct return shape and counts
    assert result == {"nodes_deleted": 2, "edges_deleted": 1, "templates_deleted": 1}

    # src_1 graph artifacts gone
    assert _count_rows(adapter, GraphNode, source_id="src_1") == 0
    assert _count_rows(adapter, GraphEdge, source_id="src_1") == 0
    assert _count_rows(adapter, GraphTemplate, source_id="src_1") == 0

    # src_1 source row still present
    assert adapter.session.get(SourceRow, "src_1") is not None

    # src_other artifacts untouched
    assert _count_rows(adapter, GraphNode, source_id="src_other") == 1
    assert _count_rows(adapter, GraphEdge, source_id="src_other") == 1
    assert _count_rows(adapter, GraphTemplate, source_id="src_other") == 1


def test_global_templates_are_preserved(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    """Templates with source_id=NULL (global) are untouched by delete_source_artifacts."""
    _seed_source(adapter, "src_1")
    _seed_template_with_source(adapter, "tpl-global", source_id=None)
    _seed_template_with_source(adapter, "tpl-src1", source_id="src_1")

    repo.delete_source_artifacts("src_1")

    assert adapter.session.get(GraphTemplate, "tpl-global") is not None
    assert adapter.session.get(GraphTemplate, "tpl-src1") is None


def test_returns_zero_counts_for_no_artifacts(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Source with no graph artifacts returns all-zero counts."""
    _seed_source(adapter, "src_empty")

    result = repo.delete_source_artifacts("src_empty")

    assert result == {"nodes_deleted": 0, "edges_deleted": 0, "templates_deleted": 0}


def test_zero_counts_for_nonexistent_source(repo: GraphRepository) -> None:
    """Calling with a source_id that does not exist returns zeros without error."""
    result = repo.delete_source_artifacts("nonexistent-source-id")

    assert result == {"nodes_deleted": 0, "edges_deleted": 0, "templates_deleted": 0}


def test_idempotent(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    """Calling delete_source_artifacts twice does not raise; second call is a no-op."""
    _seed_source(adapter, "src_1")
    _seed_node(adapter, "n1", source_id="src_1")
    _seed_template_with_source(adapter, "tpl-src1", source_id="src_1")

    first = repo.delete_source_artifacts("src_1")
    second = repo.delete_source_artifacts("src_1")

    assert first["nodes_deleted"] == 1
    assert second == {"nodes_deleted": 0, "edges_deleted": 0, "templates_deleted": 0}


def test_delete_graph_data_by_source_still_works(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Regression: refactoring helpers must not break the existing cascade method."""
    _seed_source(adapter, "src_1")
    _seed_node(adapter, "n1", source_id="src_1")
    _seed_node(adapter, "n2", source_id="src_1")
    _seed_edge(adapter, "e1", "n1", "n2", source_id="src_1")
    _seed_template_with_source(adapter, "tpl-src1", source_id="src_1")

    result = repo.delete_graph_data_by_source("src_1")

    assert result["nodes_deleted"] == 2
    assert result["edges_deleted"] == 1
    assert result["templates_deleted"] == 1
    assert set(result["deleted_node_ids"]) == {"n1", "n2"}

    assert _count_rows(adapter, GraphNode, source_id="src_1") == 0
    assert _count_rows(adapter, GraphEdge, source_id="src_1") == 0
    assert _count_rows(adapter, GraphTemplate, source_id="src_1") == 0
