# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``list_edges_by_node`` is ``list_edges`` asked node by node, read in one query per batch.

The graph context listed every seed's edges one node at a time, two queries a node: 9,248 for
one retrieval sample run, 40% of its time (2026-10-16). The batched read must return exactly
what the per-node listing does (the filter, the per-node cap, the order by edge id, edges with
no source and from a disabled source), which these tests compare on a real SQLite graph.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.adapters.sqlite.utils import SQL_IN_BATCH


DB = "test"


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name=DB)
    a.connect()
    yield a
    a.disconnect()


@pytest.fixture
def repo(adapter: SqliteAdapter) -> GraphRepository:
    """A graph: a hub with more edges than the cap, sources on, off and none, and a loner."""
    session = adapter.session
    with adapter.transaction():
        session.add(GraphTemplate(id="t", database_name=DB, name="T", template_type="node"))
        for sid, enabled in (("on", True), ("off", False)):
            session.add(
                SourceRow(
                    id=sid,
                    database_name=DB,
                    filename=f"{sid}.txt",
                    filepath=f"/data/{sid}.txt",
                    enabled=enabled,
                )
            )
    with adapter.transaction():  # each table after the ones its foreign keys name
        for nid in ("hub", "a", "b", "c", "loner"):
            session.add(
                GraphNode(
                    id=nid, database_name=DB, graph_name="knowledge", template_id="t", label=nid
                )
            )
    with adapter.transaction():
        # Edge ids out of insertion order, so "first by id" is not "first inserted".
        rows = [
            ("e09", "hub", "a", "on"),
            ("e03", "hub", "b", None),
            ("e07", "hub", "c", "off"),
            ("e01", "hub", "a", "on"),
            ("e05", "a", "hub", "on"),
            ("e02", "b", "hub", None),
            ("e08", "c", "hub", "on"),
            ("e04", "a", "b", "off"),
            ("e06", "b", "c", "on"),
        ]
        for eid, source, target, source_id in rows:
            session.add(
                GraphEdge(
                    id=eid,
                    database_name=DB,
                    graph_name="knowledge",
                    template_id="t",
                    source_node_id=source,
                    target_node_id=target,
                    label="knows",
                    properties={},
                    source_id=source_id,
                )
            )
    return GraphRepository(session, database_name=DB)


def _per_node(repo: GraphRepository, ids: list[str], *, outgoing: bool, **kw: object) -> dict:
    key = "source_node_id" if outgoing else "target_node_id"
    return {nid: repo.list_edges(**{key: nid}, **kw) for nid in ids}  # type: ignore[arg-type]


@pytest.mark.parametrize("outgoing", [True, False])
@pytest.mark.parametrize("limit", [1, 2, 100])
@pytest.mark.parametrize("include_disabled", [False, True])
def test_the_batch_is_the_per_node_listing(
    repo: GraphRepository, outgoing: bool, limit: int, include_disabled: bool
) -> None:
    ids = ["hub", "a", "b", "c", "loner", "missing", "a"]  # a repeat and an unknown id too
    batched = repo.list_edges_by_node(
        ids, outgoing=outgoing, limit_per_node=limit, include_disabled_sources=include_disabled
    )
    expected = _per_node(
        repo, ids, outgoing=outgoing, limit=limit, include_disabled_sources=include_disabled
    )
    assert batched == expected
    assert set(batched) == set(ids)
    assert batched["loner"] == [] and batched["missing"] == []


def test_the_cap_keeps_each_nodes_first_edges_by_id(repo: GraphRepository) -> None:
    out = repo.list_edges_by_node(["hub"], outgoing=True, limit_per_node=2)
    assert [e.id for e in out["hub"]] == ["e01", "e03"]  # e07 is from a disabled source
    everything = repo.list_edges_by_node(
        ["hub"], outgoing=True, limit_per_node=10, include_disabled_sources=True
    )
    assert [e.id for e in everything["hub"]] == ["e01", "e03", "e07", "e09"]


def test_more_ids_than_one_in_batch_holds(repo: GraphRepository) -> None:
    ids = [f"ghost{i}" for i in range(SQL_IN_BATCH + 5)] + ["hub", "b"]
    batched = repo.list_edges_by_node(ids, outgoing=False, limit_per_node=100)
    assert batched == _per_node(repo, ids, outgoing=False, limit=100)
    assert [e.id for e in batched["hub"]] == ["e02", "e05", "e08"]
