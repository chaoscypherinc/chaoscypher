# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A search scoped by document tags: the filter runs inside SQL, before the ``LIMIT``/``k``.

A tag set that matches no document returns nothing; it never widens to an
unscoped search. Tags and a source list both narrow (they intersect), and a
switched-off source stays hidden under a tag scope.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.models import Node, TagMatch
from tests.unit.adapters.sqlite.repos.conftest import open_search_repo


def _source(path: Path, sid: str, tags: tuple[str, ...] = ()) -> None:
    with SafeSession(get_engine(str(path))) as session:
        session.add(SourceRow(id=sid, database_name="db", filename=f"{sid}.txt", filepath=sid))
        session.commit()
    with sqlite3.connect(path) as con:
        for name in tags:
            con.execute(
                "INSERT OR IGNORE INTO source_tags (id, database_name, name, created_at) "
                "VALUES (?, 'db', ?, '2026-11-03')",
                (f"tag-{name}", name),
            )
            con.execute(
                "INSERT INTO source_tag_assignments (id, source_id, tag_id, database_name, "
                "assigned_at) VALUES (?, ?, ?, 'db', '2026-11-03')",
                (f"as-{sid}-{name}", sid, f"tag-{name}"),
            )


def _chunk(path: Path, cid: str, source: str, content: str) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, content, "
            "status, created_at, citation_offset_method) "
            "VALUES (?, 'db', ?, 0, ?, 'indexed', '2026-11-03', 'none')",
            (cid, source, content),
        )


def _ids(hits: list[tuple[str, float]]) -> set[str]:
    return {item for item, _ in hits}


QUERY_VECTOR = [1.0, 0.0, 0.0, 0.0]


async def _embed(_text: str) -> dict[str, list[float]]:
    return {"embedding": QUERY_VECTOR}


def _world(db: Path, layout: dict[str, tuple[str, ...]]) -> Any:
    """One ``falcon`` chunk per source of ``layout`` (source id -> its tags), with a vector each."""
    for sid, tags in layout.items():
        _source(db, sid, tags)
        _chunk(db, f"{sid}-c", sid, f"falcon notes of {sid}")
    repo = open_search_repo(db)
    repo.index_embeddings_batch(
        [(f"chunk:{sid}-c", [1.0, 0.1 * i, 0.0, 0.0]) for i, sid in enumerate(layout)],
        item_type="chunk",
    )
    return repo


async def _every_arm(repo: Any, **scope: Any) -> dict[str, set[str]]:
    """The chunk ids the keyword, vector and hybrid search return in ``scope``."""
    return {
        "keyword": _ids(repo.keyword_search("falcon", 20, "chunk", **scope)),
        "vector": _ids(repo.vector_search(QUERY_VECTOR, 20, "chunk", **scope)),
        "hybrid": _ids(
            await repo.hybrid_search("falcon", 20, _embed, 0.0, item_type="chunk", **scope)
        ),
    }


async def test_tagged_chunks_outside_the_untagged_top_n_are_returned(db: Path) -> None:
    _source(db, "plain")
    _source(db, "doc-a", ("ref",))
    for i in range(30):
        _chunk(db, f"u{i:02d}", "plain", "falcon falcon falcon")
    tagged = {f"chunk:t{i}" for i in range(3)}
    for i in range(3):
        _chunk(db, f"t{i}", "doc-a", f"falcon among many other quiet words of record {i}")
    repo = open_search_repo(db)
    # every untagged chunk nearer the query than any tagged one
    repo.index_embeddings_batch(
        [(f"chunk:u{i:02d}", [1.0, 0.01 * i, 0.0, 0.0]) for i in range(30)]
        + [(f"chunk:t{i}", [0.6, 0.8, 0.01 * i, 0.0]) for i in range(3)],
        item_type="chunk",
    )
    # unscoped, the tagged chunks are outside the top 5 of both arms
    assert not _ids(repo.keyword_search("falcon", limit=5, item_type="chunk")) & tagged
    assert not _ids(repo.vector_search(QUERY_VECTOR, k=5, item_type="chunk")) & tagged

    keyword = repo.keyword_search("falcon", limit=5, item_type="chunk", tags=["ref"])
    assert _ids(keyword) == tagged
    assert len(keyword) == 3
    hybrid = await repo.hybrid_search(
        "falcon", k=5, embedding_provider_callback=_embed, item_type="chunk", tags=["ref"]
    )
    assert _ids(hybrid) == tagged
    vector = repo.vector_search(QUERY_VECTOR, k=5, item_type="chunk", tags=["ref"])
    assert _ids(vector) == tagged


async def test_match_all_needs_every_tag(db: Path) -> None:
    repo = _world(db, {"both": ("alpha", "beta"), "only-a": ("alpha",), "none": ()})
    arms = await _every_arm(repo, tags=["alpha", "beta"], tag_match=TagMatch.ALL)
    assert arms == {arm: {"chunk:both-c"} for arm in arms}


async def test_match_any_takes_either(db: Path) -> None:
    repo = _world(db, {"doc-a": ("alpha",), "doc-b": ("beta",), "none": ()})
    arms = await _every_arm(repo, tags=["alpha", "beta"], tag_match=TagMatch.ANY)
    assert arms == {arm: {"chunk:doc-a-c", "chunk:doc-b-c"} for arm in arms}
    assert repo.count_in_scope("chunk", tags=["alpha", "beta"]) == 2


async def test_tags_and_source_ids_intersect(db: Path) -> None:
    repo = _world(db, {"doc-a": ("ref",), "doc-b": ("ref",), "doc-c": ()})
    arms = await _every_arm(repo, tags=["ref"], source_ids=["doc-b", "doc-c"])
    assert arms == {arm: {"chunk:doc-b-c"} for arm in arms}
    assert repo.count_in_scope("chunk", ["doc-b", "doc-c"], tags=["ref"]) == 1


async def test_tags_matching_no_document_return_nothing(db: Path) -> None:
    repo = _world(db, {"doc-a": ("ref",), "doc-b": ()})
    unscoped = await _every_arm(repo)
    assert all(unscoped.values())
    arms = await _every_arm(repo, tags=["nobody"])
    assert arms == {arm: set() for arm in arms}
    assert repo.count_in_scope("chunk", tags=["nobody"]) == 0
    # with a source list too: the intersection is empty, never the source list alone
    arms = await _every_arm(repo, tags=["nobody"], source_ids=["doc-a"])
    assert arms == {arm: set() for arm in arms}


async def test_tag_scope_still_hides_switched_off_sources(db: Path) -> None:
    repo = _world(db, {"doc-a": ("ref",), "doc-b": ("ref",)})
    with sqlite3.connect(db) as con:
        con.execute("UPDATE sources SET enabled = 0 WHERE id = 'doc-b'")
    arms = await _every_arm(repo, tags=["ref"])
    assert arms == {arm: {"chunk:doc-a-c"} for arm in arms}
    assert repo.count_in_scope("chunk", tags=["ref"]) == 1


def test_tagged_source_ids_reads_the_one_builder(db: Path) -> None:
    _world(db, {"doc-a": ("alpha", "beta"), "doc-b": ("alpha",), "doc-c": ()})
    repo = open_search_repo(db)
    assert sorted(repo.tagged_source_ids(["alpha"], TagMatch.ANY)) == ["doc-a", "doc-b"]
    assert repo.tagged_source_ids(["alpha", "beta"], TagMatch.ALL) == ["doc-a"]
    assert repo.tagged_source_ids(["nobody"], TagMatch.ANY) == []
    assert repo.tagged_source_ids([], TagMatch.ANY) == []


def test_tag_scope_reaches_node_search(db: Path) -> None:
    _source(db, "doc-a", ("ref",))
    _source(db, "doc-b")
    with sqlite3.connect(db) as con:
        for nid, sid in (("na", "doc-a"), ("nb", "doc-b")):
            con.execute(
                "INSERT INTO graph_nodes (id, database_name, graph_name, template_id, label, "
                "properties, source_id, created_at, updated_at) "
                "VALUES (?, 'db', 'knowledge', 't', ?, '{}', ?, '2026-11-03', '2026-11-03')",
                (nid, f"Falcon {nid}", sid),
            )
    repo = open_search_repo(db)
    repo.index_nodes_batch(
        [
            Node(id=nid, label=f"Falcon {nid}", template_id="t", source_id=sid)
            for nid, sid in (("na", "doc-a"), ("nb", "doc-b"))
        ]
    )
    assert _ids(repo.keyword_search("falcon", 10, "node")) == {"na", "nb"}
    assert _ids(repo.keyword_search("falcon", 10, "node", tags=["ref"])) == {"na"}
    assert repo.keyword_search("falcon", 10, "node", tags=["nobody"]) == []
    assert repo.count_in_scope("node", tags=["ref"]) == 1
