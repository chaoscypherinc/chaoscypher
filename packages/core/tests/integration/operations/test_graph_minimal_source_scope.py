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

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos import GraphRepository


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
