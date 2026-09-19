# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The two node counters must measure the row sets they claim to.

The snapshot's persisted ``total_nodes`` is summed from
``count_nodes_per_source`` and therefore excludes rows whose ``source_id``
is NULL. ``count_all_nodes`` applies no source filter, so comparing the two
subtracted different row sets: one manual/legacy node pinned the snapshot
permanently stale and every ``GET /graph/snapshot`` enqueued another
whole-database rebuild that could never clear the drift.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from chaoscypher_core.adapters.sqlite.models import GraphNode
from chaoscypher_core.adapters.sqlite.repos.graph_breakdown import (
    GraphBreakdownQueryRepository,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _node(node_id: str, source_id: str | None) -> GraphNode:
    return GraphNode(
        id=node_id,
        database_name="default",
        graph_name="knowledge",
        template_id="tpl-1",
        label=node_id,
        source_id=source_id,
    )


def test_source_attributed_count_excludes_null_source_nodes(session) -> None:
    session.add(_node("n1", "src-a"))
    session.add(_node("n2", "src-a"))
    session.add(_node("n3", None))  # manual/legacy node
    session.commit()

    repo = GraphBreakdownQueryRepository(session)

    assert repo.count_all_nodes("default") == 3
    assert repo.count_source_attributed_nodes("default") == 2


def test_counts_agree_when_every_node_has_a_source(session) -> None:
    session.add(_node("n1", "src-a"))
    session.add(_node("n2", "src-b"))
    session.commit()

    repo = GraphBreakdownQueryRepository(session)

    assert repo.count_all_nodes("default") == repo.count_source_attributed_nodes("default")


def test_other_databases_are_not_counted(session) -> None:
    node = _node("n1", "src-a")
    node.database_name = "other"
    session.add(node)
    session.commit()

    repo = GraphBreakdownQueryRepository(session)

    assert repo.count_source_attributed_nodes("default") == 0
