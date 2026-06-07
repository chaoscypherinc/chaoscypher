# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""list_edges should support fetching neighbor nodes inline (kills the get_node-in-loop antipattern).

TDD: Task 8.5+8.6 — add with_nodes=True to list_edges() so callers can hydrate
source_node and target_node in a single batch query rather than calling get_node()
in a loop (O(N) round trips → O(1)).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    GraphEdge,
    GraphNode,
    GraphTemplate,
)
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import (
    GraphRepository,
)
from chaoscypher_core.models import EdgeWithNodes


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


def _seed_template(adapter: SqliteAdapter, tpl_id: str) -> None:
    existing = adapter.session.get(GraphTemplate, tpl_id)
    if existing is not None:
        return
    with adapter.transaction():
        adapter.session.add(
            GraphTemplate(
                id=tpl_id,
                database_name="test",
                name=f"name-{tpl_id}",
                template_type="node",
                properties=[],
            )
        )


def _seed_node(adapter: SqliteAdapter, node_id: str, label: str) -> None:
    _seed_template(adapter, "tpl-node")
    with adapter.transaction():
        adapter.session.add(
            GraphNode(
                id=node_id,
                database_name="test",
                graph_name="knowledge",
                template_id="tpl-node",
                label=label,
                properties={},
            )
        )


def _seed_edge(
    adapter: SqliteAdapter,
    edge_id: str,
    source_node_id: str,
    target_node_id: str,
) -> None:
    _seed_template(adapter, "tpl-edge")
    with adapter.transaction():
        adapter.session.add(
            GraphEdge(
                id=edge_id,
                database_name="test",
                graph_name="knowledge",
                template_id="tpl-edge",
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                label="knows",
                properties={},
            )
        )


# ---------------------------------------------------------------------------
# Tests — EdgeWithNodes model
# ---------------------------------------------------------------------------


def test_edge_with_nodes_has_optional_node_fields() -> None:
    """EdgeWithNodes must exist and carry source_node / target_node."""
    from chaoscypher_core.models import Edge, EdgeWithNodes

    # EdgeWithNodes should be a subtype of Edge
    assert issubclass(EdgeWithNodes, Edge)

    # Fields present and optional
    fields = EdgeWithNodes.model_fields
    assert "source_node" in fields
    assert "target_node" in fields


# ---------------------------------------------------------------------------
# Tests — list_edges(with_nodes=True)
# ---------------------------------------------------------------------------


def test_list_edges_with_nodes_returns_hydrated_neighbors(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Edges returned with with_nodes=True must have source_node / target_node set."""
    _seed_node(adapter, "node-a", "Alice")
    _seed_node(adapter, "node-b", "Bob")
    _seed_edge(adapter, "edge-1", "node-a", "node-b")

    edges = repo.list_edges(with_nodes=True)

    assert len(edges) == 1
    edge = edges[0]

    # Must be an EdgeWithNodes
    assert isinstance(edge, EdgeWithNodes)

    # Source and target nodes must be hydrated
    assert edge.source_node is not None, "source_node must be set when with_nodes=True"
    assert edge.target_node is not None, "target_node must be set when with_nodes=True"

    # And the hydrated nodes should have the right labels
    assert edge.source_node.label == "Alice"
    assert edge.target_node.label == "Bob"

    # Node IDs must match the edge endpoint IDs
    assert edge.source_node.id == edge.source_node_id
    assert edge.target_node.id == edge.target_node_id


def test_list_edges_with_nodes_multiple_edges(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """All edges in the result must be hydrated when with_nodes=True."""
    _seed_node(adapter, "n1", "Alice")
    _seed_node(adapter, "n2", "Bob")
    _seed_node(adapter, "n3", "Carol")
    _seed_edge(adapter, "e1", "n1", "n2")
    _seed_edge(adapter, "e2", "n2", "n3")

    edges = repo.list_edges(with_nodes=True)

    assert len(edges) == 2
    assert all(isinstance(e, EdgeWithNodes) for e in edges)
    assert all(e.source_node is not None for e in edges)
    assert all(e.target_node is not None for e in edges)


def test_list_edges_default_does_not_hydrate(adapter: SqliteAdapter, repo: GraphRepository) -> None:
    """Default list_edges (with_nodes=False) should return plain Edge, not EdgeWithNodes."""
    _seed_node(adapter, "node-x", "Xavier")
    _seed_node(adapter, "node-y", "Yara")
    _seed_edge(adapter, "edge-xy", "node-x", "node-y")

    from chaoscypher_core.models import Edge, EdgeWithNodes

    edges = repo.list_edges()

    assert len(edges) == 1
    edge = edges[0]

    # Default returns Edge, not EdgeWithNodes
    assert type(edge) is Edge

    # source_node / target_node are not present on base Edge
    assert not hasattr(edge, "source_node") or not isinstance(edge, EdgeWithNodes)


def test_list_edges_with_nodes_respects_filters(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """with_nodes=True must respect existing filter params (source_node_id)."""
    _seed_node(adapter, "n-a", "Alpha")
    _seed_node(adapter, "n-b", "Beta")
    _seed_node(adapter, "n-c", "Gamma")
    _seed_edge(adapter, "e-ab", "n-a", "n-b")
    _seed_edge(adapter, "e-bc", "n-b", "n-c")

    # Filter to only edges originating from n-a
    edges = repo.list_edges(source_node_id="n-a", with_nodes=True)

    assert len(edges) == 1
    assert isinstance(edges[0], EdgeWithNodes)
    assert edges[0].source_node is not None
    assert edges[0].source_node.label == "Alpha"
    assert edges[0].target_node is not None
    assert edges[0].target_node.label == "Beta"
