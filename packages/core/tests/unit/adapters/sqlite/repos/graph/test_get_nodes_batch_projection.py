# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``get_nodes_batch`` embedding projection.

``include_embedding=False`` must skip the 1024-float JSON column (and
return ``Node.embedding is None``); the default keeps embeddings for the
import path that genuinely needs them.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphNode, GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository


@pytest.fixture
def _adapter(tmp_path: Path) -> Generator[SqliteAdapter, Any]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


@pytest.fixture
def graph_repo(_adapter: SqliteAdapter) -> GraphRepository:
    session = _adapter.session
    assert session is not None
    session.add(
        GraphTemplate(
            id="tpl_node_person",
            database_name="default",
            name="Person",
            template_type="node",
            color="#aaaaaa",
        )
    )
    session.add(
        SourceRow(
            id="src_x",
            database_name="default",
            filename="s.txt",
            filepath="/tmp/s.txt",
            title="S",
            source_type="text",
            status="committed",
            enabled=True,
        )
    )
    session.flush()
    for node_id, label in [("n_1", "One"), ("n_2", "Two")]:
        session.add(
            GraphNode(
                id=node_id,
                database_name="default",
                graph_name="knowledge",
                template_id="tpl_node_person",
                label=label,
                source_id="src_x",
                embedding=[0.5] * 8,
            )
        )
    session.commit()
    return GraphRepository(session, database_name="default")


def test_default_includes_embedding(graph_repo: GraphRepository) -> None:
    nodes = graph_repo.get_nodes_batch(["n_1", "n_2"])
    assert len(nodes) == 2
    assert all(n.embedding == [0.5] * 8 for n in nodes)


def test_opt_out_skips_embedding(graph_repo: GraphRepository) -> None:
    nodes = graph_repo.get_nodes_batch(["n_1", "n_2"], include_embedding=False)
    assert len(nodes) == 2
    assert all(n.embedding is None for n in nodes)
    # Non-embedding fields are intact under the projection.
    assert {n.label for n in nodes} == {"One", "Two"}
    assert all(n.source_id == "src_x" for n in nodes)
