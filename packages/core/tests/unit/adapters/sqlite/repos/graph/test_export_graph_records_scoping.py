# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""export_graph_records source-scoping: scoped export returns only in-scope edges.

Regression guard for the source-scoped à-la-carte CCX export path. The edge
query must be filtered by the in-scope node set at the SQL level and honour the
both-endpoints consistency rule, so a scoped export never leaks another source's
edges (and never drops its own edges just because other sources' edges sort
first under the max_items cap).
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
    """Fresh file-backed adapter with schema created."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


@pytest.fixture
def seeded_repo(_adapter: SqliteAdapter):
    """Two sources, two nodes each, one intra-source edge each.

    src_a: nodes a1, a2 with edge a1 -> a2
    src_b: nodes b1, b2 with edge b1 -> b2
    """
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
        GraphTemplate(
            id="t_knows",
            database_name="default",
            name="knows",
            template_type="edge",
            color="#bbbbbb",
        )
    )
    for src_id in ("src_a", "src_b"):
        session.add(
            SourceRow(
                id=src_id,
                database_name="default",
                filename=f"{src_id}.txt",
                filepath=f"/tmp/{src_id}.txt",
                title=src_id,
                source_type="text",
                status="committed",
                enabled=True,
            )
        )
    # Flush parents (templates + sources) so the FK targets exist before the
    # node inserts — the ORM does not track these DB-level FKs for insert
    # ordering (mirrors test_edge_upsert_dedup's fixture).
    session.flush()
    for node_id, src_id in [
        ("a1", "src_a"),
        ("a2", "src_a"),
        ("b1", "src_b"),
        ("b2", "src_b"),
    ]:
        session.add(
            GraphNode(
                id=node_id,
                database_name="default",
                graph_name="knowledge",
                template_id="tpl_node_person",
                label=node_id,
                source_id=src_id,
            )
        )
    session.commit()

    return GraphRepository(session, database_name="default")


@pytest.mark.asyncio
async def test_scoped_export_returns_only_in_scope_edges(seeded_repo: GraphRepository) -> None:
    """Exporting scoped to src_a returns only src_a's nodes and its intra-source edge."""
    from chaoscypher_core.models import EdgeCreate

    await seeded_repo.upsert_edges_batch(
        [
            EdgeCreate(
                template_id="t_knows",
                source_node_id="a1",
                target_node_id="a2",
                label="knows",
                properties={},
                source_id="src_a",
            ),
            EdgeCreate(
                template_id="t_knows",
                source_node_id="b1",
                target_node_id="b2",
                label="knows",
                properties={},
                source_id="src_b",
            ),
        ]
    )

    result = seeded_repo.export_graph_records(source_ids=["src_a"])

    node_ids = {n["id"] for n in result["nodes"]}
    assert node_ids == {"a1", "a2"}

    # Only src_a's edge survives; src_b's edge (b1 -> b2) is filtered out because
    # neither endpoint is in the exported node set.
    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    assert edge["source_node_id"] == "a1"
    assert edge["target_node_id"] == "a2"


def test_unscoped_export_returns_all_records(seeded_repo: GraphRepository) -> None:
    """With no source_ids, the whole graph is exported (nodes present, no edge filter)."""
    result = seeded_repo.export_graph_records()
    node_ids = {n["id"] for n in result["nodes"]}
    assert node_ids == {"a1", "a2", "b1", "b2"}
