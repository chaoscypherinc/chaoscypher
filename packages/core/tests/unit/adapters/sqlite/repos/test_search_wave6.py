# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The search repository under audit wave 6: a switched-off source never answers; one KNN size.

- A source set ``enabled = 0`` still fed both chunk arms, unscoped and when
  scoped to it (120 of 120 benchmark contexts), while the graph listings
  left its nodes out: one visibility predicate now serves the scope SQL,
  both arms, ``count_in_scope`` and the node listings.
- The node scope read ``''`` as no source and the listings NULL only.
- The scoped KNN asked again for four times as many on each short round,
  every round a full scan (eight statements for a five-chunk scope).
"""

from __future__ import annotations

import math
import random
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, text
from structlog.testing import capture_logs

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.repos import search as search_mod
from chaoscypher_core.adapters.sqlite.repos.graph import GraphRepository
from chaoscypher_core.adapters.sqlite.repos.source_visibility import (
    no_source_sql,
    source_visible_sql,
)
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.models import Node
from chaoscypher_core.ports.search import (
    SearchHits,
    has_no_source,
    node_in_source_scope,
    source_visible,
)
from tests.unit.adapters.sqlite.repos.conftest import open_search_repo


_ALL_SCOPES = [None, ["b"], ["a", "b"]]
"""Unscoped, scoped to the switched-off source alone, and scoped to both."""


def _source(path: Path, sid: str, *, enabled: bool = True) -> None:
    with SafeSession(get_engine(str(path))) as session:
        session.add(
            SourceRow(
                id=sid, database_name="db", filename=f"{sid}.txt", filepath=sid, enabled=enabled
            )
        )
        session.commit()


def _set_enabled(path: Path, sid: str, *, enabled: bool) -> None:
    with sqlite3.connect(path) as con:
        con.execute("UPDATE sources SET enabled = ? WHERE id = ?", (int(enabled), sid))


def _chunk(path: Path, cid: str, source: str, content: str) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, content, "
            "status, created_at, citation_offset_method) "
            "VALUES (?, 'db', ?, 0, ?, 'indexed', '2026-10-06', 'none')",
            (cid, source, content),
        )


def _graph_node(path: Path, nid: str, source: str | None, label: str) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO graph_nodes (id, database_name, graph_name, template_id, label, "
            "properties, source_id, created_at, updated_at) "
            "VALUES (?, 'db', 'knowledge', 't', ?, '{}', ?, '2026-10-06', '2026-10-06')",
            (nid, label, source),
        )


_NODES = {"na": "a", "nb": "b", "nnull": None, "nempty": ""}
"""A node of each source, one with a NULL source and one with ``''``."""


@pytest.fixture
def split(db: Path) -> tuple[Path, Any]:
    """Source ``a``: twelve chunks; ``b``: three that match best; nodes of each kind; vectors."""
    _source(db, "a")
    _source(db, "b")
    for i in range(12):
        _chunk(db, f"a{i:02d}", "a", f"Kessel regiment {i}")
    for i in range(3):
        _chunk(db, f"b{i}", "b", f"Kessel Kessel Kessel letter {i}")
    for nid, sid in _NODES.items():
        _graph_node(db, nid, sid, f"Kessel {nid}")
    repo = open_search_repo(db)
    repo.index_embeddings_batch(
        [(f"chunk:a{i:02d}", [0.5, 0.5 + 0.01 * i, 0.0, 0.0]) for i in range(12)]
        + [(f"chunk:b{i}", [1.0, 0.01 * i, 0.0, 0.0]) for i in range(3)],
        item_type="chunk",
    )
    repo.index_nodes_batch(
        [
            Node(id=nid, label=f"Kessel {nid}", template_id="t", source_id=sid)
            for nid, sid in _NODES.items()
        ]
    )
    repo.index_embeddings_batch(
        [(nid, [1.0, 0.1 * i, 0.0, 0.0]) for i, nid in enumerate(_NODES)], item_type="node"
    )
    return db, repo


def _b_items(hits: list[tuple[str, float]]) -> list[str]:
    return [i for i, _ in hits if i.startswith("chunk:b") or i == "nb"]


async def _embed(_text: str) -> dict[str, list[float]]:
    return {"embedding": [1.0, 0.0, 0.0, 0.0]}


async def _every_arm(repo: Any, scope: list[str] | None) -> dict[str, list[tuple[str, float]]]:
    """Every search arm's hits for the same question in ``scope``."""
    query = [1.0, 0.0, 0.0, 0.0]
    return {
        "keyword chunk": repo.keyword_search("Kessel", 20, "chunk", scope),
        "keyword node": repo.keyword_search("Kessel", 20, "node", scope),
        "keyword both": repo.keyword_search("Kessel", 20, None, scope),
        "vector chunk": repo.vector_search(query, 20, "chunk", scope),
        "vector node": repo.vector_search(query, 20, "node", scope),
        "vector both": repo.vector_search(query, 20, None, scope),
        "hybrid chunk": await repo.hybrid_search(
            "Kessel", 20, _embed, 0.0, item_type="chunk", source_ids=scope
        ),
        "hybrid both": await repo.hybrid_search("Kessel", 20, _embed, 0.0, source_ids=scope),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", _ALL_SCOPES, ids=["unscoped", "scoped-to-it", "scoped-to-both"])
async def test_a_switched_off_source_answers_in_no_arm_and_returns_when_switched_on(
    split: tuple[Path, Any], scope: list[str] | None
) -> None:
    db, repo = split
    before = await _every_arm(repo, scope)
    assert all(_b_items(hits) for hits in before.values())  # the fixture's b items do answer
    _set_enabled(db, "b", enabled=False)
    hidden = await _every_arm(repo, scope)
    assert {arm: _b_items(hits) for arm, hits in hidden.items()} == {arm: [] for arm in hidden}
    if scope == ["b"]:  # only the source-less nodes are left in its scope
        assert {i for hits in hidden.values() for i, _ in hits} <= {"nnull", "nempty"}
    else:
        assert all(hidden.values())
    _set_enabled(db, "b", enabled=True)
    assert await _every_arm(repo, scope) == before


def test_the_scope_count_leaves_a_switched_off_source_out(split: tuple[Path, Any]) -> None:
    db, repo = split
    assert (repo.count_in_scope("chunk", ["b"]), repo.count_in_scope("chunk")) == (3, 15)
    assert (repo.count_in_scope("node", ["b"]), repo.count_in_scope("node")) == (3, 4)
    _set_enabled(db, "b", enabled=False)
    assert (repo.count_in_scope("chunk", ["b"]), repo.count_in_scope("chunk")) == (0, 12)
    assert (repo.count_in_scope("node", ["b"]), repo.count_in_scope("node")) == (2, 3)
    _set_enabled(db, "b", enabled=True)
    assert repo.count_in_scope("chunk") == 15


def test_the_node_listings_and_the_search_agree_on_what_may_answer(
    split: tuple[Path, Any],
) -> None:
    """The graph listings read the predicate the search does: the same nodes, scoped or not."""
    db, repo = split
    _set_enabled(db, "b", enabled=False)
    graph = GraphRepository(SafeSession(get_engine(str(db))), "db")
    query = [1.0, 0.0, 0.0, 0.0]
    for scope in (None, ["a"], ["b"]):
        listed = {n.id for n in graph.list_nodes_minimal(limit=100, source_ids=scope)}
        searched = {i for i, _ in repo.vector_search(query, 20, "node", scope)}
        assert listed == searched, scope
    assert {n.id for n in graph.list_nodes(limit=100)} == {"na", "nnull", "nempty"}
    assert {n.id for n in graph.list_nodes_minimal(limit=100, include_disabled_sources=True)} == {
        *_NODES
    }


def test_null_and_empty_read_alike_as_no_source(split: tuple[Path, Any]) -> None:
    """Both are in every scope, in the listing and the search (until wave 6 the listing kept NULL only)."""
    db, repo = split
    graph = GraphRepository(SafeSession(get_engine(str(db))), "db")
    listed = {n.id for n in graph.list_nodes_minimal(limit=100, source_ids=["a"])}
    searched = {i for i, _ in repo.keyword_search("Kessel", 20, "node", ["a"])}
    expected = {nid for nid, sid in _NODES.items() if node_in_source_scope(sid, ["a"])}
    assert listed == searched == expected == {"na", "nnull", "nempty"}


@pytest.mark.parametrize("source_id", [None, "", "a", "b", "ghost"])
def test_the_sql_predicates_read_as_the_python_ones(db: Path, source_id: str | None) -> None:
    """``source_visible_sql``/``no_source_sql`` and their Python forms, on every kind of source id."""
    _source(db, "a")
    _source(db, "b", enabled=False)
    with get_engine(str(db)).connect() as conn:
        visible, none = conn.execute(
            text(f"SELECT {source_visible_sql(':s')}, {no_source_sql(':s')}"), {"s": source_id}
        ).one()
    assert bool(visible) is source_visible(source_id, {"b"})
    assert bool(none) is has_no_source(source_id)


def test_an_index_without_the_graph_tables_hides_nothing(tmp_path: Path) -> None:
    """A search-only database (no ``sources``) searches as before: there is nothing to hide."""
    repo = search_mod.SearchRepository(
        get_engine(str(tmp_path / "bare.db")), vector_dim=4, embedding_model="m"
    )
    repo.index_embeddings_batch([("chunk:x", [1.0, 0.0, 0.0, 0.0])], item_type="chunk")
    assert [i for i, _ in repo.vector_search([1.0, 0.0, 0.0, 0.0], 3, "chunk")] == ["chunk:x"]


# -- one KNN size -------------------------------------------------------------


_BIG, _TINY = 600, 5


@pytest.fixture
def synthetic(db: Path) -> tuple[Path, Any, dict[str, list[float]]]:
    """600 chunks of source ``big``, 5 of ``tiny``, 40 of ``off`` (switched off); random vectors."""
    rng = random.Random(6)
    sizes = {"big": _BIG, "tiny": _TINY, "off": 40}
    vectors: dict[str, list[float]] = {}
    for sid in sizes:
        _source(db, sid, enabled=sid != "off")
    with sqlite3.connect(db) as con:
        for sid, n in sizes.items():
            for i in range(n):
                cid = f"{sid}{i:04d}"
                con.execute(
                    "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, "
                    "content, status, created_at, citation_offset_method) "
                    "VALUES (?, 'db', ?, ?, 'x', 'indexed', '2026-10-06', 'none')",
                    (cid, sid, i),
                )
                vectors[cid] = [rng.gauss(0, 1) for _ in range(4)]
    repo = open_search_repo(db)
    repo.index_embeddings_batch(
        [(f"chunk:{cid}", v) for cid, v in vectors.items()], item_type="chunk"
    )
    return db, repo, vectors


def _brute_force(
    vectors: dict[str, list[float]], query: list[float], k: int, sources: set[str]
) -> list[str]:
    """The ``k`` nearest chunks of ``sources`` by cosine distance, ties by id."""

    def distance(v: list[float]) -> float:
        dot = sum(a * b for a, b in zip(v, query, strict=True))
        return 1.0 - dot / (math.hypot(*v) * math.hypot(*query))

    ranked = sorted(
        (distance(v), f"chunk:{cid}")
        for cid, v in vectors.items()
        if cid.rstrip("0123456789") in sources
    )
    return [cid for _, cid in ranked[:k]]


class _Statements:
    """Counts the statements an engine runs while open."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.count = 0

    def _seen(self, *_: Any) -> None:
        self.count += 1

    def __enter__(self) -> _Statements:
        event.listen(self.engine, "before_cursor_execute", self._seen)
        return self

    def __exit__(self, *_: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._seen)


@pytest.mark.parametrize("knn_max", [4096, 64], ids=["knn", "past-the-knn-limit"])
def test_a_tiny_scope_takes_at_most_three_statements(
    synthetic: tuple[Path, Any, dict[str, list[float]]],
    monkeypatch: pytest.MonkeyPatch,
    knn_max: int,
) -> None:
    """The visibility check, one count of the table and the scope, one KNN or scan.

    Until wave 6 each short round asked the KNN again for four times as many:
    five statements here, eight on a 179,000-chunk index.
    """
    db, repo, vectors = synthetic
    monkeypatch.setattr(search_mod, "VEC_KNN_MAX", knn_max)
    query = [0.3, -1.0, 0.2, 0.5]
    with _Statements(get_engine(str(db))) as seen, capture_logs() as logs:
        hits = repo.vector_search(query, k=10, item_type="chunk", source_ids=["tiny"])
    assert seen.count <= 3
    assert [i for i, _ in hits] == _brute_force(vectors, query, 10, {"tiny"})
    scanned = any(e["event"] == "vector_search_scope_scanned" for e in logs)
    assert scanned is (knn_max == 64)


@pytest.mark.parametrize(
    ("scope", "sources"),
    [
        (["tiny"], {"tiny"}),
        (["big"], {"big"}),
        (["big", "tiny"], {"big", "tiny"}),
        (["off"], set()),
        (["off", "tiny"], {"tiny"}),
        (None, {"big", "tiny"}),
    ],
)
@pytest.mark.parametrize("k", [1, 7, 50])
def test_the_filtered_knn_is_the_brute_force_ranking(
    synthetic: tuple[Path, Any, dict[str, list[float]]],
    scope: list[str] | None,
    sources: set[str],
    k: int,
) -> None:
    """Every scope, the switched-off source's included: the exact top ``k``, never more statements."""
    db, repo, vectors = synthetic
    rng = random.Random(k)
    for _ in range(3):
        query = [rng.gauss(0, 1) for _ in range(4)]
        with _Statements(get_engine(str(db))) as seen:
            hits = repo.vector_search(query, k=k, item_type="chunk", source_ids=scope)
        assert [i for i, _ in hits] == _brute_force(vectors, query, k, sources)
        assert seen.count <= 4  # check, counts, KNN, and the exact scan only if the KNN came short


@pytest.mark.asyncio
async def test_the_hybrid_search_says_what_it_saw_before_the_cut(split: tuple[Path, Any]) -> None:
    """``candidates_seen`` counts both arms' distinct hits above the floor; under ``k`` the scope is read."""
    _, repo = split
    hits = await repo.hybrid_search(
        "Kessel", k=10, embedding_provider_callback=_embed, min_similarity=0.0,
        item_type="chunk", source_ids=["b"],
    )  # fmt: skip
    assert isinstance(hits, SearchHits)
    assert (len(hits), hits.candidates_seen, hits.scope_exhausted) == (3, 3, True)
    wide = await repo.hybrid_search(
        "Kessel", k=4, embedding_provider_callback=_embed, min_similarity=0.0, item_type="chunk"
    )
    assert (len(wide), wide.scope_exhausted) == (4, False)
    assert wide.candidates_seen is not None and wide.candidates_seen >= 4
    floored = await repo.hybrid_search(
        "Xyzzy Plugh", k=10, embedding_provider_callback=_embed, min_similarity=0.999,
        item_type="chunk", source_ids=["a"],
    )  # fmt: skip
    assert (list(floored), floored.candidates_seen, floored.scope_exhausted) == ([], 0, True)
