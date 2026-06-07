# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: non-minimal ``list_nodes`` must not N+1 lazy-load embeddings.

The list query deliberately excludes the ``embedding`` BLOB via ``load_only``
("not used in list views"). Historically the row->model converter still read
``db_node.embedding``, which — because the column was deferred — fired one
extra SELECT per row. A 50-row page therefore issued ~50 hidden queries, each
deserializing a large vector that the caller then discarded. These tests pin
the query count so the regression cannot return.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event

from chaoscypher_core.adapters.sqlite.models import GraphTemplate
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import Node, NodeCreate


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

NODE_COUNT = 10
_EMBEDDING = [0.1, 0.2, 0.3]


@pytest.fixture
def graph_repo(integration_adapter: SqliteAdapter) -> GraphRepository:
    """A GraphRepository bound to the integration_adapter's session."""
    return GraphRepository(integration_adapter.session, database_name="default")


def _seed_nodes_with_embeddings(repo: GraphRepository, count: int) -> None:
    """Insert ``count`` nodes that each carry an embedding, then forget them.

    ``expire_all`` drops the identity-map state so the subsequent ``list_nodes``
    loads fresh rows with the embedding column deferred — mirroring a real HTTP
    request handled by a brand-new session.
    """
    repo.session.add(
        GraphTemplate(
            id="tpl-1",
            database_name="default",
            name="Person",
            template_type="node",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    repo.session.maybe_commit()

    for i in range(count):
        repo.create_node(
            NodeCreate(template_id="tpl-1", label=f"Node {i}", embedding=list(_EMBEDDING))
        )
    repo.session.maybe_commit()
    repo.session.expire_all()


def _count_node_queries(
    repo: GraphRepository, fn: Callable[[], list[Node]]
) -> tuple[list[Node], list[str]]:
    """Run ``fn`` while capturing every SQL statement that reads graph_nodes."""
    bind = repo.session.get_bind()
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany) -> None:
        if "from graph_nodes" in statement.lower():
            statements.append(statement)

    event.listen(bind, "before_cursor_execute", _capture)
    try:
        result = fn()
    finally:
        event.remove(bind, "before_cursor_execute", _capture)
    return result, statements


def test_full_list_loads_embeddings_in_a_single_query(graph_repo: GraphRepository) -> None:
    """Default non-minimal list returns embeddings without an N+1 lazy-load storm."""
    _seed_nodes_with_embeddings(graph_repo, NODE_COUNT)

    nodes, statements = _count_node_queries(
        graph_repo, lambda: graph_repo.list_nodes(minimal=False)
    )

    assert len(nodes) == NODE_COUNT
    assert all(n.embedding == _EMBEDDING for n in nodes)
    assert len(statements) == 1, (
        f"expected a single graph_nodes query but saw {len(statements)} — "
        "embedding is being lazy-loaded per row (N+1)"
    )


def test_list_view_can_skip_embeddings_entirely(graph_repo: GraphRepository) -> None:
    """List views opt out of embeddings: none loaded, still a single query."""
    _seed_nodes_with_embeddings(graph_repo, NODE_COUNT)

    nodes, statements = _count_node_queries(
        graph_repo,
        lambda: graph_repo.list_nodes(minimal=False, include_embedding=False),
    )

    assert len(nodes) == NODE_COUNT
    assert all(n.embedding is None for n in nodes)
    assert len(statements) == 1
