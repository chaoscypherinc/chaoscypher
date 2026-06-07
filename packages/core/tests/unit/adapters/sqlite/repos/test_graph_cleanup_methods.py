# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 12 — orphan-finders + batch-deletes on GraphRepositoryProtocol."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphNode,
    GraphTemplate,
)
from chaoscypher_core.adapters.sqlite.repos.graph.cleanup import remove_corrupt_nodes
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import (
    GraphRepository,
)


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


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
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
    database_name: str = "test",
) -> None:
    """Create *tpl_id* if missing so nodes/edges can satisfy FK on template_id."""
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
    src: str,
    tgt: str,
    *,
    database_name: str = "test",
) -> None:
    _ensure_template(adapter, "tpl", database_name=database_name)
    with adapter.transaction():
        adapter.session.add(
            GraphEdge(
                id=edge_id,
                database_name=database_name,
                graph_name="knowledge",
                source_node_id=src,
                target_node_id=tgt,
                template_id="tpl",
                label=f"edge-{edge_id}",
                properties={},
            )
        )


def _seed_template(
    adapter: SqliteAdapter,
    tpl_id: str,
    *,
    is_system: bool = False,
    source_id: str | None = None,
    database_name: str = "test",
) -> None:
    with adapter.transaction():
        adapter.session.add(
            GraphTemplate(
                id=tpl_id,
                database_name=database_name,
                name=f"name-{tpl_id}",
                template_type="node",
                is_system=is_system,
                source_id=source_id,
                properties=[],
            )
        )


from sqlalchemy import text as _sql_text


def _disable_fk(adapter: SqliteAdapter) -> None:
    """Disable FK constraints so tests can manufacture referential drift."""
    adapter.session.execute(_sql_text("PRAGMA foreign_keys = OFF"))


def _enable_fk(adapter: SqliteAdapter) -> None:
    adapter.session.execute(_sql_text("PRAGMA foreign_keys = ON"))


def test_find_orphaned_edges_by_source_node(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    _seed_node(adapter, "n1")
    _seed_edge(adapter, "e-good", "n1", "n1")
    # Manufacture orphan by seeding edge with non-existent source_node_id.
    _disable_fk(adapter)
    _seed_edge(adapter, "e-orphan", "missing", "n1")
    _enable_fk(adapter)
    orphans = repo.find_orphaned_edges_by_source_node(database_name="test")
    assert orphans == ["e-orphan"]


def test_find_orphaned_edges_by_target_node(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    _seed_node(adapter, "n1")
    _disable_fk(adapter)
    _seed_edge(adapter, "e-bad", "n1", "missing")
    _enable_fk(adapter)
    orphans = repo.find_orphaned_edges_by_target_node(database_name="test")
    assert orphans == ["e-bad"]


def test_delete_edges_batch(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    _seed_node(adapter, "n1")
    _seed_edge(adapter, "e0", "n1", "n1")
    _seed_edge(adapter, "e1", "n1", "n1")
    _seed_edge(adapter, "e2", "n1", "n1")
    assert repo.delete_edges_batch(edge_ids=["e0", "e2"]) == 2


def test_delete_edges_batch_empty_noop(repo: GraphRepository) -> None:
    assert repo.delete_edges_batch(edge_ids=[]) == 0


def test_find_orphaned_nodes_by_source(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    _seed_source(adapter, "s1")
    _seed_node(adapter, "n-good", source_id="s1")
    _disable_fk(adapter)
    _seed_node(adapter, "n-orphan", source_id="missing")
    _enable_fk(adapter)
    _seed_node(adapter, "n-null")  # source_id IS NULL - not orphaned
    orphans = repo.find_orphaned_nodes_by_source(database_name="test")
    assert orphans == ["n-orphan"]


def test_delete_nodes_batch(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    _seed_node(adapter, "n0")
    _seed_node(adapter, "n1")
    _seed_node(adapter, "n2")
    assert repo.delete_nodes_batch(node_ids=["n0", "n2"]) == 2


def test_find_orphaned_templates_by_source(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    _seed_source(adapter, "s1")
    _seed_template(adapter, "tpl-good", source_id="s1")
    _disable_fk(adapter)
    _seed_template(adapter, "tpl-orphan", source_id="missing")
    _enable_fk(adapter)
    _seed_template(adapter, "tpl-system", is_system=True)  # never orphaned
    orphans = repo.find_orphaned_templates_by_source(database_name="test")
    assert orphans == ["tpl-orphan"]


def test_delete_templates_batch(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    _seed_template(adapter, "tpl0")
    _seed_template(adapter, "tpl1")
    assert repo.delete_templates_batch(template_ids=["tpl0"]) == 1


def test_count_templates_scoped(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    _seed_template(adapter, "tpl1", database_name="test")
    _seed_template(adapter, "tpl2", database_name="other")
    assert repo.count_templates(database_name="test") == 1


def test_count_templates_empty(repo: GraphRepository) -> None:
    assert repo.count_templates(database_name="empty-db") == 0


def test_remove_corrupt_nodes_scans_all_pages() -> None:
    page_one = [
        SimpleNamespace(id=f"ok-{idx}", template_id="tpl", label=f"node-{idx}")
        for idx in range(500)
    ]
    page_two = [
        SimpleNamespace(id="bad-template", template_id="", label="has-label"),
        SimpleNamespace(id="bad-label", template_id="tpl", label=None),
        SimpleNamespace(id="ok-last", template_id="tpl", label="last"),
    ]
    repo = MagicMock()
    repo.list_nodes.side_effect = [page_one, page_two]
    repo.delete_node.return_value = True

    result = remove_corrupt_nodes(repo)

    assert result == {"nodes_removed": 2, "edges_removed": 0}
    assert repo.list_nodes.call_args_list == [
        call(
            skip=0,
            limit=500,
            include_disabled_sources=True,
            include_embedding=False,
        ),
        call(
            skip=500,
            limit=500,
            include_disabled_sources=True,
            include_embedding=False,
        ),
    ]
    assert repo.delete_node.call_args_list == [
        call("bad-template"),
        call("bad-label"),
    ]
