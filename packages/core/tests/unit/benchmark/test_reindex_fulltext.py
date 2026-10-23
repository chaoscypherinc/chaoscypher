# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""After an identity-resolver merge, the reindex leaves the node keyword index equal to the graph.

Until 2026-10-06 ``reindex_graph`` re-embedded vectors only: the node
full-text rows the extraction wrote survived the resolver, so a merged-away
node stayed findable by keyword (and seeded PageRank as an id no longer in
the graph) and the kept node's new aliases were not searchable (audit R11,
wave 2).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.benchmark.reindex import reindex_graph
from chaoscypher_core.models import NodeCreate, TemplateCreate
from chaoscypher_core.services.graph.resolution.apply import apply_resolution
from chaoscypher_core.services.graph.resolution.plan import Merge, ResolutionPlan
from chaoscypher_core.testing.session_fakes import empty_session_scope


DIM = 4


@pytest.fixture
def world(tmp_path: Path) -> Generator[SimpleNamespace]:
    """A real graph and a real search index over one app.db, indexed as the extraction leaves it."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    graph = GraphRepository(adapter.session, database_name="test")
    graph.create_template(TemplateCreate(name="character", template_type="node"), custom_id="t")
    for nid, label, aliases in (
        ("anna", "Anna Pávlovna Schérer", ["Annette"]),
        ("mlle", "Mlle Schérer", ["the hostess"]),
        ("pierre", "Pierre", []),
    ):
        graph.create_node(
            NodeCreate(template_id="t", label=label, properties={"aliases": aliases}),
            custom_id=nid,
        )
    search = SearchRepository(engine, vector_dim=DIM, embedding_model="m")
    search.index_nodes_batch(graph.list_nodes(limit=100))
    yield SimpleNamespace(db=db_path, adapter=adapter, graph=graph, search=search)
    adapter.disconnect()
    evict_engine(db_path)


def _fulltext(db: Path) -> dict[str, list[str]]:
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT node_id, properties FROM fulltext_content").fetchall()
    return {nid: json.loads(props).get("aliases", []) for nid, props in rows}


def _graph_nodes(db: Path) -> dict[str, list[str]]:
    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT id, properties FROM graph_nodes").fetchall()
    return {nid: json.loads(props or "{}").get("aliases", []) for nid, props in rows}


@pytest.mark.asyncio
async def test_after_a_merge_and_a_reindex_the_keyword_index_is_the_graph(
    world: SimpleNamespace,
) -> None:
    apply_resolution(world.graph, ResolutionPlan(merges=[Merge(keep="anna", drop="mlle", why="t")]))
    # the resolver changed the graph, not the index: the merged-away node is still found
    assert "mlle" in _fulltext(world.db)
    world.search._set_reindex_flag(True)  # a model change asked for a full re-embed
    assert world.search.needs_full_reindex is True

    provider = MagicMock()
    provider.batch_embed = AsyncMock(
        side_effect=lambda texts: SimpleNamespace(embeddings=[[1.0, 0.0, 0.0, 0.0]] * len(texts))
    )
    storage = MagicMock()
    storage.list_sources.return_value = ([], 0)
    storage.session_scope = empty_session_scope
    target = SimpleNamespace(
        graph_repository=world.graph, search_repository=world.search, storage_adapter=storage
    )
    settings: Any = SimpleNamespace(batching=SimpleNamespace(chunk_fetch_limit=500))
    await reindex_graph(target, provider, settings=settings, node_limit=100)

    assert _fulltext(world.db) == _graph_nodes(world.db)
    assert set(_fulltext(world.db)) == {"anna", "pierre"}
    assert "the hostess" in _fulltext(world.db)["anna"]
    assert [nid for nid, _ in world.search.keyword_search("hostess")] == ["anna"]
    assert world.search.check_fts5_integrity() == (True, None)
    # every vector now comes from the provider: the stale-index flag is cleared
    assert world.search.needs_full_reindex is False


def test_the_resolver_can_rebuild_the_index_from_its_own_node_records(
    world: SimpleNamespace,
) -> None:
    """A full rebuild from the resolver's ``load_graph`` records (the reindex's form) is enough."""
    from chaoscypher_core.services.graph.resolution.apply import load_graph

    apply_resolution(world.graph, ResolutionPlan(merges=[Merge(keep="anna", drop="mlle", why="t")]))
    assert world.search.rebuild_node_fulltext(load_graph(world.graph)[0]) == 2
    assert _fulltext(world.db) == _graph_nodes(world.db)
    assert [nid for nid, _ in world.search.keyword_search("hostess")] == ["anna"]


def test_a_merge_rewrites_only_the_rows_of_the_nodes_it_changed(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolver's index step is scoped: the kept node's row, the merged-away node's deletion.

    Until the #795 follow-up a merge rebuilt the whole node full-text table
    from a whole-graph read inside one write transaction, so a delete that
    triggered a merge held the writer lock for an FTS rewrite of every node.
    A node outside the merge keeps its row as it was (the marker below), and
    the graph is never read whole.
    """
    from chaoscypher_core.adapters.sqlite.repos import search as search_mod

    with sqlite3.connect(world.db) as con:
        con.execute(
            "UPDATE fulltext_content SET searchable_text = 'untouched marker' "
            "WHERE node_id = 'pierre'"
        )
    whole_graph_reads: list[int] = []
    original = search_mod._graph_node_records
    monkeypatch.setattr(
        search_mod,
        "_graph_node_records",
        lambda conn: whole_graph_reads.append(1) or original(conn),
    )

    apply_resolution(
        world.graph,
        ResolutionPlan(merges=[Merge(keep="anna", drop="mlle", why="t")]),
        search=world.search,
    )

    with sqlite3.connect(world.db) as con:
        rows = dict(con.execute("SELECT node_id, searchable_text FROM fulltext_content"))
    assert rows["pierre"] == "untouched marker", "a node outside the merge was rewritten"
    assert "mlle" not in rows
    assert "the hostess" in _fulltext(world.db)["anna"]
    assert [nid for nid, _ in world.search.keyword_search("hostess")] == ["anna"]
    assert whole_graph_reads == []
    assert world.search.check_fts5_integrity() == (True, None)
