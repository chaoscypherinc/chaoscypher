# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Minimal graph list queries apply source scope in SQL, before the LIMIT:
out-of-scope rows are seeded first with a limit smaller than their count,
so a filter-after-LIMIT implementation returns zero in-scope rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    INVERSE_OF_KEY,
    UNEVIDENCED_KEY,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


@pytest.fixture
def graph_repo(integration_adapter: SqliteAdapter) -> GraphRepository:
    """A GraphRepository bound to the integration_adapter's session, seeded."""
    repo = GraphRepository(integration_adapter.session, database_name="default")
    now = datetime.now(UTC)
    template = {"template_type": "node", "created_at": now, "updated_at": now}
    repo.session.add(GraphTemplate(id="tpl", database_name="default", name="T", **template))
    for src in ("src-a", "src-b"):
        repo.session.add(
            SourceRow(
                id=src, database_name="default", filename=f"{src}.txt", filepath=src, enabled=True
            )
        )
    repo.session.maybe_commit()
    common = {"database_name": "default", "graph_name": "knowledge", "template_id": "tpl"}
    for src in ("src-a", "src-b"):  # src-a first -> src-a rows own the lower rowids
        for i in range(6):
            repo.session.add(
                GraphNode(id=f"{src}-n{i}", label=f"{src} {i}", source_id=src, **common)
            )
    repo.session.maybe_commit()
    for src in ("src-a", "src-b"):
        for i in range(3):
            repo.session.add(
                GraphEdge(
                    id=f"{src}-e{i}",
                    source_node_id=f"{src}-n{i}",
                    target_node_id=f"{src}-n{i + 1}",
                    label="linked",
                    **common,
                )
            )
    repo.session.maybe_commit()
    repo.session.expire_all()
    return repo


def test_nodes_minimal_source_scope_applies_before_limit(graph_repo: GraphRepository) -> None:
    """limit=4 with 6 out-of-scope rows ahead: only a pre-limit WHERE passes."""
    nodes = graph_repo.list_nodes_minimal(
        limit=4, include_disabled_sources=True, source_ids=["src-b"]
    )
    assert len(nodes) == 4
    assert all(n.source_id == "src-b" for n in nodes)

    unscoped = graph_repo.list_nodes_minimal(limit=100, include_disabled_sources=True)
    assert len(unscoped) == 12  # omitting source_ids keeps the unscoped behavior


def test_edges_minimal_source_scope_applies_before_limit(graph_repo: GraphRepository) -> None:
    """Edge scope keys off the SOURCE node's source_id, applied before the limit."""
    edges = graph_repo.list_edges_minimal(
        limit=2, include_disabled_sources=True, source_ids=["src-b"]
    )
    assert len(edges) == 2
    assert all(e.source_node_id.startswith("src-b") for e in edges)


def test_minimal_lists_select_columns_not_orm_entities(graph_repo: GraphRepository) -> None:
    """The whole-graph PPR/analytics loads must not hydrate ORM entities.

    Entity construction + identity-map registration per row cost ~4x the SQL
    on a 50k-node / 200k-edge load; a column select yields the same values.
    """
    hydrated: list[object] = []

    def _on_load(target: object, _context: object) -> None:
        hydrated.append(target)

    event.listen(GraphNode, "load", _on_load)
    event.listen(GraphEdge, "load", _on_load)
    try:
        nodes = graph_repo.list_nodes_minimal(limit=100)
        edges = graph_repo.list_edges_minimal(limit=100)
    finally:
        event.remove(GraphNode, "load", _on_load)
        event.remove(GraphEdge, "load", _on_load)

    assert len(nodes) == 12
    assert len(edges) == 6
    assert ("src-b-n0", "src-b-n1", "linked", "tpl") in {
        (e.source_node_id, e.target_node_id, e.label, e.template_id) for e in edges
    }
    assert ("src-a-n5", "src-a 5", "tpl", "src-a") in {
        (n.id, n.label, n.template_id, n.source_id) for n in nodes
    }
    assert hydrated == []


def test_the_minimal_listings_are_ordered_by_id_under_a_limit(graph_repo: GraphRepository) -> None:
    """A LIMIT with no ORDER BY lets the planner pick the rows (audit wave 4): both listings order by id."""
    nodes = graph_repo.list_nodes_minimal(limit=5, include_disabled_sources=True)
    assert [n.id for n in nodes] == sorted(
        n.id for n in graph_repo.list_nodes_minimal(limit=100, include_disabled_sources=True)
    )[:5]
    edges = graph_repo.list_edges_minimal(limit=4, include_disabled_sources=True)
    assert [e.id for e in edges] == sorted(
        e.id for e in graph_repo.list_edges_minimal(limit=100, include_disabled_sources=True)
    )[:4]


def test_the_minimal_edges_carry_the_inverse_marker_the_walk_folds_by(
    graph_repo: GraphRepository,
) -> None:
    """A synthetic inverse is told from its forward twin on the minimal listing (``inverse_of``).

    The walk (``graphrag_blocks.walk_chains``) reads the whole graph through
    this listing and folds an inverse onto its twin by the marker; without
    it every inverse would be walked as a second copy of its fact.
    """
    common = {"database_name": "default", "graph_name": "knowledge", "template_id": "tpl"}
    graph_repo.session.add(
        GraphEdge(
            id="src-a-e0i",
            source_node_id="src-a-n1",
            target_node_id="src-a-n0",
            label="linked_from",
            properties={INVERSE_OF_KEY: "linked", UNEVIDENCED_KEY: True, "confidence": 0.9},
            **common,
        )
    )
    graph_repo.session.maybe_commit()
    by_id = {
        e.id: e for e in graph_repo.list_edges_minimal(limit=100, include_disabled_sources=True)
    }
    assert by_id["src-a-e0i"].properties == {UNEVIDENCED_KEY: True, INVERSE_OF_KEY: "linked"}
    assert by_id["src-a-e0"].properties == {}
