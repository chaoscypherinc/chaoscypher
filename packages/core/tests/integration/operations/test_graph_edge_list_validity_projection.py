# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: the edge lists must load the validity columns with the row, not per edge.

``list_edges`` and ``list_edges_by_node`` project the edge columns with ``load_only``.
Migration 0020 added ``valid_from``, ``valid_to`` and ``closed_by_source_id``, which the
row-to-model converter reads; left out of the projection, each one was a deferred column
and every listed edge fired its own SELECT. Graph retrieval lists edges per seed node, so
the benchmark's retrieval sample ran about four times slower on CI. These tests pin one
query per list and the window read through it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event

from chaoscypher_core.adapters.sqlite.models import GraphTemplate
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

EDGE_COUNT = 10
CLOSED = date(2026, 10, 1)


@pytest.fixture
def graph_repo(integration_adapter: SqliteAdapter) -> GraphRepository:
    """A GraphRepository bound to the integration_adapter's session."""
    return GraphRepository(integration_adapter.session, database_name="default")


def _seed_edges(repo: GraphRepository) -> str:
    """One hub node with ``EDGE_COUNT`` outgoing edges, the first one closed; the hub's id."""
    now = datetime.now(UTC)
    for tid, kind in (("tpl-n", "node"), ("tpl-e", "edge")):
        repo.session.add(
            GraphTemplate(
                id=tid,
                database_name="default",
                name=tid,
                template_type=kind,
                created_at=now,
                updated_at=now,
            )
        )
    repo.session.maybe_commit()
    hub = repo.create_node(NodeCreate(template_id="tpl-n", label="Hub"))
    edge_ids = []
    for i in range(EDGE_COUNT):
        leaf = repo.create_node(NodeCreate(template_id="tpl-n", label=f"Leaf {i}"))
        edge = repo.create_edge(
            EdgeCreate(
                template_id="tpl-e",
                source_node_id=hub.id,
                target_node_id=leaf.id,
                label="related_to",
            )
        )
        edge_ids.append(edge.id)
    repo.set_edge_validity(
        edge_ids[0], valid_from=date(2026, 9, 1), valid_to=CLOSED, closed_by_source_id="s2"
    )
    repo.session.maybe_commit()
    repo.session.expire_all()
    return hub.id


def _count_edge_queries(repo: GraphRepository, fn: Callable[[], Any]) -> tuple[Any, list[str]]:
    """Run ``fn`` while capturing every SQL statement that reads graph_edges."""
    bind = repo.session.get_bind()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany) -> None:
        if "from graph_edges" in statement.lower():
            statements.append(statement)

    event.listen(bind, "before_cursor_execute", _capture)
    try:
        result = fn()
    finally:
        event.remove(bind, "before_cursor_execute", _capture)
    return result, statements


def test_list_edges_reads_the_windows_in_one_query(graph_repo: GraphRepository) -> None:
    hub = _seed_edges(graph_repo)
    edges, statements = _count_edge_queries(
        graph_repo, lambda: graph_repo.list_edges(source_node_id=hub)
    )
    assert len(edges) == EDGE_COUNT
    assert sorted(e.valid_to for e in edges if e.valid_to) == [CLOSED]
    assert len(statements) == 1, f"{len(statements)} graph_edges queries: a column loads per edge"


def test_list_edges_by_node_reads_the_windows_in_one_query(graph_repo: GraphRepository) -> None:
    hub = _seed_edges(graph_repo)
    by_node, statements = _count_edge_queries(
        graph_repo,
        lambda: graph_repo.list_edges_by_node([hub], outgoing=True, limit_per_node=50),
    )
    edges = by_node[hub]
    assert len(edges) == EDGE_COUNT
    assert [e.closed_by_source_id for e in edges if e.valid_to] == ["s2"]
    assert len(statements) == 1, f"{len(statements)} graph_edges queries: a column loads per edge"


# Columns the edge lists leave out on purpose: the row-to-model converter never reads them.
NOT_PROJECTED = {"ccx_iri"}


def test_every_new_edge_column_is_projected_or_named_here() -> None:
    """A column added to graph_edges joins ``_EDGE_COLUMNS`` or ``NOT_PROJECTED``, never neither."""
    from sqlalchemy import inspect

    from chaoscypher_core.adapters.sqlite.models import GraphEdge
    from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_edge_ops import _EDGE_COLUMNS

    columns = {a.key for a in inspect(GraphEdge).column_attrs}
    assert columns - {c.key for c in _EDGE_COLUMNS} == NOT_PROJECTED
