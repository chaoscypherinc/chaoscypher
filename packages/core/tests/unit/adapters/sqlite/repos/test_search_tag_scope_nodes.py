# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Under a tag scope, a node search answers only nodes a tagged document attests.

A person-made node and an unattested (orphaned) node are in every plain
source scope (the UI's source lens), and stay so; under a tag scope they
are out, as is a node of an untagged document (owner decision 2026-11-05).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chaoscypher_core.models import Node
from tests.fakes.tag_scope_world import NODES, QUERY_VECTOR, build_world
from tests.unit.adapters.sqlite.repos.conftest import open_search_repo


DOC_A_NODES = {"node-a", "node-a2"}
"""The nodes ``doc-a`` (tag ``alpha``) attests."""


async def _embed(_text: str) -> dict[str, list[float]]:
    return {"embedding": QUERY_VECTOR}


def _repo(db: Path) -> Any:
    build_world(db)
    repo = open_search_repo(db)
    repo.index_nodes_batch(
        [
            Node(id=nid, label=str(spec["label"]), template_id="place", properties={})
            for nid, spec in NODES.items()
        ]
    )
    repo.index_embeddings_batch(
        [(nid, list(spec["vector"])) for nid, spec in NODES.items()],  # type: ignore[call-overload]
        item_type="node",
    )
    return repo


async def _every_arm(repo: Any, **scope: Any) -> dict[str, set[str]]:
    """The node ids the keyword, vector and hybrid search return in ``scope``."""

    def ids(hits: Any) -> set[str]:
        return {str(item) for item, _ in hits}

    return {
        "keyword": ids(repo.keyword_search("falcon", 20, "node", **scope)),
        "vector": ids(repo.vector_search(QUERY_VECTOR, 20, "node", **scope)),
        "hybrid": ids(
            await repo.hybrid_search("falcon", 20, _embed, 0.0, item_type="node", **scope)
        ),
    }


async def test_a_tag_scope_answers_only_nodes_a_tagged_document_attests(db: Path) -> None:
    repo = _repo(db)
    arms = await _every_arm(repo, tags=["alpha"])
    assert arms == dict.fromkeys(arms, DOC_A_NODES)
    assert repo.count_in_scope("node", tags=["alpha"]) == 2


async def test_a_tag_scope_with_source_ids_reads_their_intersection(db: Path) -> None:
    repo = _repo(db)
    arms = await _every_arm(repo, tags=["alpha"], source_ids=["doc-a", "doc-b"])
    assert arms == dict.fromkeys(arms, DOC_A_NODES)
    # a source list holding no tagged document: nothing, never the person-made node
    arms = await _every_arm(repo, tags=["alpha"], source_ids=["doc-b"])
    assert arms == {arm: set() for arm in arms}
    assert repo.count_in_scope("node", ["doc-b"], tags=["alpha"]) == 0


async def test_a_plain_source_scope_still_admits_person_made_and_unattested_nodes(
    db: Path,
) -> None:
    repo = _repo(db)
    arms = await _every_arm(repo, source_ids=["doc-a"])
    expected = {*DOC_A_NODES, "node-person", "node-orphan"}
    assert arms == dict.fromkeys(arms, expected)
    assert repo.count_in_scope("node", ["doc-a"]) == 4


def test_the_minimal_edge_listing_under_attested_only_reads_the_edge_s_own_sources(
    db: Path,
) -> None:
    """``edge-a-a2`` joins two ``doc-a`` nodes but only ``doc-b`` states it: out of a strict ``doc-a`` scope."""
    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.adapters.sqlite.repos.graph import GraphRepository
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
    from tests.fakes.tag_scope_world import DATABASE

    build_world(db)
    with SafeSession(get_engine(str(db))) as session:
        graph = GraphRepository(session, DATABASE)
        lenient = graph.list_edges_minimal(limit=100, source_ids=["doc-a"])
        strict = graph.list_edges_minimal(limit=100, source_ids=["doc-a"], attested_only=True)
    assert {e.id for e in lenient} == {"edge-a-person", "edge-a-b", "edge-a-a2", "edge-a2-a"}
    # edge-a2-a is homed at doc-b but doc-a states it too: in, by its members
    assert {e.id for e in strict} == {"edge-a-person", "edge-a-b", "edge-a2-a"}
    assert {e.id: e.member_source_ids for e in strict}["edge-a-b"] == ["doc-a"]


def test_edge_members_are_read_only_for_a_strict_scope(db: Path) -> None:
    """The members subquery costs every retrieval: only an ``attested_only`` read selects it."""
    from sqlalchemy import event

    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.adapters.sqlite.repos.graph import GraphRepository
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
    from tests.fakes.tag_scope_world import DATABASE

    build_world(db)
    engine = get_engine(str(db))
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        statements.append(statement)

    def members_read(read: Any) -> tuple[bool, Any]:
        statements.clear()
        result = read()
        return any("graph_edge_sources mid" in s for s in statements), result

    event.listen(engine, "before_cursor_execute", record)
    try:
        with SafeSession(engine) as session:
            graph = GraphRepository(session, DATABASE)
            plain, edges = members_read(
                lambda: graph.list_edges_minimal(limit=100, source_ids=["doc-a"])
            )
            assert not plain
            assert all(e.member_source_ids == [] for e in edges)
            strict, edges = members_read(
                lambda: graph.list_edges_minimal(
                    limit=100, source_ids=["doc-a"], attested_only=True
                )
            )
            assert strict
            assert {e.id: e.member_source_ids for e in edges}["edge-a-b"] == ["doc-a"]

            plain, by_node = members_read(
                lambda: graph.list_edges_by_node(["node-a"], outgoing=True, limit_per_node=10)
            )
            assert not plain
            assert all(e.member_source_ids == [] for e in by_node["node-a"])
            loaded, by_node = members_read(
                lambda: graph.list_edges_by_node(
                    ["node-a"], outgoing=True, limit_per_node=10, with_members=True
                )
            )
            assert loaded
            members = {e.id: e.member_source_ids for e in by_node["node-a"]}
            assert members["edge-a-a2"] == ["doc-b"]
    finally:
        event.remove(engine, "before_cursor_execute", record)
