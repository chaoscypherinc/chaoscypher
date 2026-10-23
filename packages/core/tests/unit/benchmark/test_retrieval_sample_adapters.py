# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The retrieval sample reads its world through the product's own SQLite repositories.

Audit wave 4: the citation lookup returns its rows by ``entity_uri`` (SQLite's
``IN`` walk over the index), not in the order asked for, so the provenance arm
the handler had ordered by PageRank reached rank fusion ordered by node id and
moved 83 of 120 benchmark chunk sets at ``vector_reserve`` 8. The sample's
citation fake returned the rows in the order asked for, which the adapter never
does, so the recipe could not see it. Now the sample runs the real adapters,
and these guards hold:

- reversing every row list the repositories return leaves every retrieval
  unchanged (the handler never ranks by a repository's row order);
- plain fakes that return rows in the order asked for retrieve exactly what the
  adapters do (fake and adapter agree, so a fake elsewhere is a fair stand-in);
- the adapter really does return citations out of lookup order on the sample,
  so the first guard is not vacuous.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from chaoscypher_core.benchmark import retrieval_sample as rs
from chaoscypher_core.ports.search import node_in_source_scope, source_visible
from tests.fakes.graph_edges import EdgesByNode


_ROW_READERS = frozenset(
    {
        "get_citations_batch",
        "get_chunks_by_ids_batch",
        "get_nodes_batch",
        "list_edges",
        "list_nodes",
        "list_nodes_minimal",
        "list_edges_minimal",
    }
)
# Readers that return each key's rows (``list_edges_by_node``: node id -> edges); each list is
# reversed. The graph context reads its edges here since 2026-10-16, so leaving it out of the
# guard would have stopped checking edge order without failing.
_ROWS_BY_KEY_READERS = frozenset({"list_edges_by_node"})


class _Reversed:
    """A store whose row-list readers return their rows reversed."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._store, name)
        if name in _ROWS_BY_KEY_READERS:

            def reversed_rows_by_key(*args: Any, **kwargs: Any) -> dict[Any, list[Any]]:
                return {k: list(reversed(list(v))) for k, v in attr(*args, **kwargs).items()}

            return reversed_rows_by_key
        if name not in _ROW_READERS:
            return attr

        def reversed_rows(*args: Any, **kwargs: Any) -> list[Any]:
            return list(reversed(list(attr(*args, **kwargs))))

        return reversed_rows


def _reversing(_role: str, store: Any) -> Any:
    return _Reversed(store)


class _FakeGraph(EdgesByNode):
    """The graph surface the handlers read, over the sample, rows in sample order.

    A switched-off source's nodes, and the minimal listing's edges from them
    (the edge listing reads the edge's own source), are left out by the
    Python form of the listings' predicate (``source_visible``), reading which
    sources the run switched off from the real store's database.
    """

    def __init__(self, real: Any) -> None:
        self.nodes = rs._nodes()
        self.edges = rs._edges()
        self._by_id = {n.id: n for n in self.nodes}
        self._real = real

    def _disabled(self) -> set[str]:
        rows = self._real.session.exec(text("SELECT id FROM sources WHERE enabled = 0"))
        return {sid for (sid,) in rows}

    def _visible(self) -> list[Any]:
        disabled = self._disabled()
        return [n for n in self.nodes if source_visible(n.source_id, disabled)]

    def _visible_edges(self) -> list[Any]:
        shown = {n.id for n in self._visible()}
        return [e for e in self.edges if e.source_node_id in shown]

    def list_nodes(self, limit: int, source_ids: list[str] | None = None, **_: Any) -> list[Any]:
        return [n for n in self._visible() if not source_ids or n.source_id in source_ids][:limit]

    def list_edges(
        self,
        source_node_id: str | None = None,
        target_node_id: str | None = None,
        limit: int = 1000,
        **_: Any,
    ) -> list[Any]:
        return [  # the edge's own source decides here (every sample edge is ``s1``'s)
            e
            for e in self.edges
            if (source_node_id is None or e.source_node_id == source_node_id)
            and (target_node_id is None or e.target_node_id == target_node_id)
        ][:limit]

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        return [self._by_id[i] for i in ids if i in self._by_id]

    def get_template(self, template_id: str) -> Any:
        return SimpleNamespace(id=template_id, name=template_id)


class _FakeMinimalGraph(_FakeGraph):
    def list_nodes_minimal(self, limit: int, source_ids: list[str] | None = None) -> list[Any]:
        return [
            n
            for n in self._visible()
            if not source_ids or node_in_source_scope(n.source_id, source_ids)
        ][:limit]

    def list_edges_minimal(self, limit: int, source_ids: list[str] | None = None) -> list[Any]:
        return self._visible_edges()[:limit]


class _FakeStorage:
    """Chunks and citations in the order asked for; sources from the real adapter."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self._rows = {
            cid: {
                "id": cid,
                "content": text,
                "source_id": rs._chunk_source(cid),
                "database_name": "sample",
                "chunk_index": i,
            }
            for i, (cid, text, _) in enumerate(rs.SAMPLE_CHUNKS)
        }

    def get_chunks_by_ids_batch(self, ids: list[str], **_: Any) -> list[dict[str, Any]]:
        return [dict(self._rows[i]) for i in ids if i in self._rows]

    def get_citations_batch(
        self, database_name: str, entity_uris: list[str], source_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "entity_uri": uri,
                "chunk_id": cid,
                "confidence": rs.SAMPLE_CITATION_CONFIDENCE.get((uri, cid), 1.0),
            }
            for uri in entity_uris
            for cid in sorted(rs.SAMPLE_CITATIONS.get(uri, ()))
        ]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _fakes(role: str, store: Any) -> Any:
    if role == "storage":
        return _FakeStorage(store)
    return _FakeMinimalGraph(store) if hasattr(store, "list_nodes_minimal") else _FakeGraph(store)


@pytest.fixture(scope="module")
def adapters_read() -> list[str]:
    return rs.sample_retrievals()


def test_reversing_every_repositorys_row_order_leaves_every_retrieval_unchanged(
    adapters_read: list[str],
) -> None:
    assert rs.sample_retrievals(_reversing) == adapters_read


def test_fakes_in_lookup_order_retrieve_what_the_adapters_do(adapters_read: list[str]) -> None:
    assert rs.sample_retrievals(_fakes) == adapters_read


def test_the_citation_adapter_returns_rows_out_of_lookup_order() -> None:
    """The sample's citation table is read back in an order the lookup did not ask for."""
    from chaoscypher_core.adapters.sqlite.repos.graph import GraphRepository

    engine = rs._memory_engine()
    try:
        rs._store_world(engine)
        storage = rs._storage_adapter(engine)
        lookup = ["velmar", "corvin", "teodor", "ann"]
        rows = storage.get_citations_batch("sample", entity_uris=lookup)
        assert {r["entity_uri"] for r in rows} == set(lookup)
        firsts = list(dict.fromkeys(r["entity_uri"] for r in rows))
        assert firsts != lookup
        # and the graph repository reads the same world
        graph = GraphRepository(storage.session, "sample")
        assert len(graph.list_nodes_minimal(limit=1000)) == len(rs.SAMPLE_NODES)
        assert len(graph.list_edges_minimal(limit=1000)) == len(rs.SAMPLE_EDGES)
    finally:
        engine.dispose()


def test_the_sample_stores_its_relationship_citations_with_their_sentences() -> None:
    """Each listed edge's citations read back by edge id, quoting the chunk's sentence at the index.

    And the rows serve what they were chosen for: two cited edges meet at one entity (a walk
    crosses both), and one cited edge is one of several of its source's edges of that label
    (an aggregate group with a row of evidence and a row without).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )

    edge_ids = {(e.source_node_id, e.label, e.target_node_id): e.id for e in rs._edges()}
    texts = {cid: text for cid, text, _ in rs.SAMPLE_CHUNKS}
    expected = sorted(
        (edge_ids[key], chunk, split_into_sentences(texts[chunk])[idx], idx)
        for key, pairs in rs.SAMPLE_RELATIONSHIP_CITATIONS.items()
        for chunk, idx in pairs
    )
    engine = rs._memory_engine()
    try:
        rs._store_world(engine)
        storage = rs._storage_adapter(engine)
        rows = storage.get_relationship_citations_batch("sample", sorted(set(edge_ids.values())))
    finally:
        engine.dispose()
    got = [
        (r["edge_id"], r["chunk_id"], s["text"], s["local_index"])
        for r in rows
        for s in r["sentences"]
    ]
    assert got == expected
    assert all(r["source_id"] == rs._chunk_source(r["chunk_id"]) for r in rows)

    cited = list(rs.SAMPLE_RELATIONSHIP_CITATIONS)
    ends = [{s, t} for s, _, t in cited]
    assert any(a & b for i, a in enumerate(ends) for b in ends[i + 1 :])
    assert any(
        sum(1 for s, rel, _ in rs.SAMPLE_EDGES if (s, rel) == (src, label)) >= 2
        for src, label, _ in cited
    )
