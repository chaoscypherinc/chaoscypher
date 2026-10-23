# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The search repository under audit wave 5: one write transaction, the scope before the cut.

- The open-time node index check read ``graph_nodes`` on a read connection,
  rebuilt in a second transaction and stamped in a third: a node committed in
  between was deleted from the index and the broken state stamped as checked.
  ``rebuild_node_fulltext(nodes)`` trusted the caller's list the same way.
- A source scope was applied to the hybrid arm after its cut (41 of 120
  scoped benchmark questions got 6 to 9 chunks instead of 10).
- The rank-1 integrity check ran on every open (1.13 s of 1.48 s at 179,000
  chunks); BM25 ties came in the index's row order; an unreadable
  ``needs_full_reindex`` flag read as an index in step; an NFD question
  split its words at the accents.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.adapters.sqlite.repos import search as search_mod
from chaoscypher_core.utils.retrieval_faults import (
    NEEDS_FULL_REINDEX_UNREADABLE,
    collect_retrieval_faults,
)
from tests.unit.adapters.sqlite.repos.conftest import (
    add_chunk,
    add_graph_node,
    keyword,
    open_search_repo,
    reopen_search_repo,
)


def _graph_row(nid: str, label: str, aliases: list[str]) -> tuple[Any, ...]:
    return (nid, label, json.dumps({"aliases": aliases}))


def _commit_node_later(path: Path, nid: str, label: str) -> threading.Thread:
    """A writer thread that commits a node and its full-text row as soon as it can."""

    def write() -> None:
        con = sqlite3.connect(path, timeout=10)
        try:
            con.execute(
                "INSERT INTO graph_nodes (id, database_name, graph_name, template_id, label, "
                "properties, created_at, updated_at) VALUES (?, 'db', 'knowledge', 't', ?, ?, "
                "'2026-10-06', '2026-10-06')",
                _graph_row(nid, label, []),
            )
            row = search_mod.node_fulltext_row({"id": nid, "label": label, "properties": {}})
            con.execute(search_mod._NODE_FTS_UPSERT, row)
            con.commit()
        finally:
            con.close()

    thread = threading.Thread(target=write)
    thread.start()
    return thread


def _racing_reader(monkeypatch: pytest.MonkeyPatch, path: Path, threads: list[Any]) -> None:
    """Start a committing writer the moment the index check reads the graph, and give it time."""
    original = search_mod._graph_node_records

    def reading(conn: Any) -> Any:
        if not threads:
            threads.append(_commit_node_later(path, "zebulon", "Zebulon Writer"))
            time.sleep(0.3)  # the writer commits now, unless the read holds the write lock
        return original(conn)

    monkeypatch.setattr(search_mod, "_graph_node_records", reading)


def test_a_node_committed_during_the_open_check_is_never_lost(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer waits for the check's lock; its node is indexed, and no open stamps it away."""
    add_graph_node(db, "n1", "Anatole", [])
    threads: list[Any] = []
    _racing_reader(monkeypatch, db, threads)
    repo = open_search_repo(db)  # the node index is stale (never indexed): the check rebuilds
    threads[0].join(timeout=10)
    monkeypatch.undo()
    assert keyword(repo, "Anatole") == ["n1"]
    assert keyword(repo, "Zebulon") == ["zebulon"]
    reopened = reopen_search_repo(db)
    assert keyword(reopened, "Zebulon") == ["zebulon"]
    assert reopened.check_fts5_integrity() == (True, None)


def test_a_node_committed_during_a_rebuild_is_never_lost(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``rebuild_node_fulltext`` reads the graph inside its own transaction, never the caller's list alone."""
    add_graph_node(db, "n1", "Anatole", [])
    repo = open_search_repo(db)
    stale_list = [{"id": "n1", "label": "Anatole", "properties": {}}]
    threads: list[Any] = []
    _racing_reader(monkeypatch, db, threads)
    assert repo.rebuild_node_fulltext(stale_list) in (1, 2)
    threads[0].join(timeout=10)
    monkeypatch.undo()
    assert keyword(repo, "Zebulon") == ["zebulon"]
    assert keyword(reopen_search_repo(db), "Zebulon") == ["zebulon"]


def test_a_rebuild_indexes_the_graph_not_a_stale_caller_list(db: Path) -> None:
    """A list missing a node the graph holds: the graph's nodes are indexed, the difference logged."""
    add_graph_node(db, "n1", "Anatole", [])
    add_graph_node(db, "n2", "Hélène", ["Ellen"])
    repo = open_search_repo(db)
    with capture_logs() as logs:
        assert repo.rebuild_node_fulltext([{"id": "n1", "label": "Anatole", "properties": {}}]) == 2
    assert keyword(repo, "Ellen") == ["n2"]
    assert any(e["event"] == "node_fulltext_caller_list_differs_from_graph" for e in logs)
    # stamped as checked: the next open compares nothing
    with capture_logs() as logs:
        reopen_search_repo(db)
    assert not any(e["event"] == "node_fulltext_stale_on_open" for e in logs)


def test_an_index_with_no_graph_behind_it_is_built_from_the_callers_nodes(db: Path) -> None:
    repo = open_search_repo(db)
    assert repo.rebuild_node_fulltext([{"id": "x", "label": "Corvin", "properties": {}}]) == 1
    assert keyword(repo, "Corvin") == ["x"]


def test_the_full_integrity_check_runs_only_when_the_index_moved(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stamped after a passing rank-1 check; an unchanged index gets the count check only."""
    add_graph_node(db, "n1", "Anatole", [])
    add_chunk(db, "c1", "Anatole rode out.")
    open_search_repo(db)  # first open: the full check, then the stamps
    calls: list[tuple[str, bool]] = []
    original = search_mod.SearchRepository._fulltext_index_problem

    def spy(self: Any, fx: Any, *, deep: bool = True) -> Any:
        calls.append((fx.fts, deep))
        return original(self, fx, deep=deep)

    monkeypatch.setattr(search_mod.SearchRepository, "_fulltext_index_problem", spy)
    reopen_search_repo(db)
    assert calls == [("fulltext_index", False), ("fulltext_index_chunks", False)]
    calls.clear()
    add_chunk(db, "c2", "Hélène smiled.")  # the triggers write the chunk index: it moved
    reopen_search_repo(db)
    assert ("fulltext_index_chunks", True) in calls
    assert ("fulltext_index", False) in calls


def test_bm25_ties_are_ordered_by_item_id(db: Path) -> None:
    """Two chunks of one text score alike: the id decides, whatever order they were written in."""
    add_chunk(db, "zz", "Dólokhov drank rum on the window ledge.")
    add_chunk(db, "aa", "Dólokhov drank rum on the window ledge.")
    repo = open_search_repo(db)
    assert keyword(repo, "rum ledge", "chunk") == ["chunk:aa", "chunk:zz"]


def _chunk(path: Path, cid: str, source: str, content: str) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, content, "
            "status, created_at, citation_offset_method) "
            "VALUES (?, 'db', ?, 0, ?, 'indexed', '2026-10-06', 'none')",
            (cid, source, content),
        )


@pytest.fixture
def two_sources(db: Path) -> Any:
    """Twelve chunks of source ``a`` that match best, three of ``b``; vectors to match."""
    for i in range(12):
        _chunk(db, f"a{i:02d}", "a", f"Kessel Kessel Kessel regiment {i}")
    for i in range(3):
        _chunk(db, f"b{i}", "b", f"Kessel letter {i}")
    repo = open_search_repo(db)
    repo.index_embeddings_batch(
        [(f"chunk:a{i:02d}", [1.0, 0.01 * i, 0.0, 0.0]) for i in range(12)]
        + [(f"chunk:b{i}", [0.5, 0.5 + 0.01 * i, 0.0, 0.0]) for i in range(3)],
        item_type="chunk",
    )
    return repo


def test_the_scope_is_applied_before_the_keyword_cut(two_sources: Any) -> None:
    hits = two_sources.keyword_search("Kessel", limit=3, item_type="chunk", source_ids=["b"])
    assert sorted(i for i, _ in hits) == ["chunk:b0", "chunk:b1", "chunk:b2"]
    assert len(two_sources.keyword_search("Kessel", limit=3, item_type="chunk")) == 3


def test_the_scope_is_applied_before_the_vector_cut(two_sources: Any) -> None:
    """The KNN is asked again for more until the scope fills ``k`` or the table is read."""
    hits = two_sources.vector_search([1.0, 0.0, 0.0, 0.0], k=2, item_type="chunk", source_ids=["b"])
    assert [i for i, _ in hits] == ["chunk:b0", "chunk:b1"]
    all_b = two_sources.vector_search(
        [1.0, 0.0, 0.0, 0.0], k=10, item_type="chunk", source_ids=["b"]
    )
    assert len(all_b) == 3  # the scope is exhausted


def test_past_the_knn_limit_the_scope_is_scanned(
    two_sources: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_mod, "VEC_KNN_MAX", 2)
    with capture_logs() as logs:
        hits = two_sources.vector_search(
            [1.0, 0.0, 0.0, 0.0], k=3, item_type="chunk", source_ids=["b"]
        )
    assert [i for i, _ in hits] == ["chunk:b0", "chunk:b1", "chunk:b2"]
    assert any(e["event"] == "vector_search_scope_scanned" for e in logs)


@pytest.mark.asyncio
async def test_the_hybrid_search_fills_its_budget_from_the_scope(two_sources: Any) -> None:
    async def embed(_text: str) -> dict[str, list[float]]:
        return {"embedding": [1.0, 0.0, 0.0, 0.0]}

    hits = await two_sources.hybrid_search(
        "Kessel regiment",
        k=3,
        embedding_provider_callback=embed,
        min_similarity=0.0,
        item_type="chunk",
        source_ids=["b"],
    )
    assert sorted(i for i, _ in hits) == ["chunk:b0", "chunk:b1", "chunk:b2"]
    assert two_sources.count_in_scope("chunk", ["b"]) == 3
    assert two_sources.count_in_scope("chunk") == 15


def test_an_unreadable_reindex_flag_is_a_fault(db: Path) -> None:
    repo = open_search_repo(db)
    with sqlite3.connect(db) as con:
        con.execute("DROP TABLE search_metadata")
    with collect_retrieval_faults() as faults:
        assert repo.needs_full_reindex is False
    assert faults == [NEEDS_FULL_REINDEX_UNREADABLE]


def test_a_decomposed_query_finds_what_the_composed_one_does(db: Path) -> None:
    add_graph_node(db, "pierre", "Pierre Bezúkhov", ["Count Bezúkhov"])
    add_chunk(db, "c1", "Pierre Bezúkhov stood by the window.")
    repo = open_search_repo(db)
    nfc = "Where did Bezúkhov stand?"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfd != nfc
    assert keyword(repo, nfd) == keyword(repo, nfc) == ["pierre"]
    assert keyword(repo, nfd, "chunk") == keyword(repo, nfc, "chunk") == ["chunk:c1"]
