# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Keyword search over chunks, the any-word query, rank fusion and unit vectors (audit R2, R3, R7).

Until 2026-10-06 the full-text index held nodes only and every query word had
to match, so "hybrid" search had no keyword half; its two arms were merged by
``max()`` of incomparable scores; a search for chunks shared its budget with
nodes and templates; and vectors were ranked by L2 on non-unit vectors.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.search import (
    SearchRepository,
    _fts5_query,
    _fuse_rankings,
)
from chaoscypher_core.models import Node


DIM = 4


def _db(tmp_path: Path) -> Path:
    """A fresh app.db with every SQLModel table (document_chunks among them)."""
    path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(path)), checkfirst=True)
    return path


def _repo(path: Path) -> SearchRepository:
    """A SearchRepository over ``path``."""
    return SearchRepository(get_engine(str(path)), vector_dim=DIM, embedding_model="m")


def _write_chunks(path: Path, rows: list[tuple[str, str]]) -> None:
    """Insert ``(id, content)`` chunks the way any chunk writer does: straight into document_chunks."""
    with sqlite3.connect(path) as con:  # no foreign-key pragma: no source row needed
        con.executemany(
            "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, content, "
            "status, created_at, citation_offset_method) "
            "VALUES (?, 'db', 's1', 0, ?, 'indexed', '2026-10-06', 'none')",
            rows,
        )


def _node(node_id: str, label: str, definition: str = "") -> Node:
    """A node with a label and a description."""
    return Node(id=node_id, label=label, template_id="t", properties={"definition": definition})


@pytest.fixture
def db(tmp_path: Path) -> Any:
    """The database path; its cached engine is evicted afterwards."""
    path = _db(tmp_path)
    yield path
    evict_engine(path)


class TestQuery:
    """The FTS5 expression: any significant word, phrases kept, syntax-safe."""

    def test_question_words_are_dropped_and_the_rest_ored(self) -> None:
        assert _fts5_query("Who is the father of Pierre?") == '"father" OR "Pierre"'

    def test_a_quoted_phrase_stays_a_phrase(self) -> None:
        assert _fts5_query('Did "old prince" Bolkonski') == '"old prince" OR "Bolkonski"'

    def test_operators_and_special_characters_cannot_break_the_syntax(self) -> None:
        assert _fts5_query('NOT Kutuzov* AND (Bagration:)^ "') == '"Kutuzov" OR "Bagration"'

    def test_nothing_significant_is_empty(self) -> None:
        assert _fts5_query("who is it?") == ""
        assert _fts5_query("") == ""


class TestChunkFullText:
    """document_chunks feeds the chunk index through triggers and a backfill."""

    def test_written_updated_and_deleted_chunks_follow(self, db: Path) -> None:
        repo = _repo(db)
        _write_chunks(db, [("c1", "Dolokhov drank the rum on the window ledge.")])
        assert repo.keyword_search("rum ledge", item_type="chunk")[0][0] == "chunk:c1"

        with sqlite3.connect(db) as con:
            con.execute("UPDATE document_chunks SET content = 'Natasha danced.' WHERE id = 'c1'")
        assert repo.keyword_search("rum", item_type="chunk") == []
        assert [i for i, _ in repo.keyword_search("danced", item_type="chunk")] == ["chunk:c1"]

        with sqlite3.connect(db) as con:
            con.execute("DELETE FROM document_chunks WHERE id = 'c1'")
        assert repo.keyword_search("danced", item_type="chunk") == []
        assert repo.check_fts5_integrity() == (True, None)

    def test_chunks_written_before_the_index_existed_are_backfilled_once(self, db: Path) -> None:
        _write_chunks(db, [("c1", "Kutuzov slept at the council."), ("c2", "Anna's soiree.")])
        repo = _repo(db)
        assert [i for i, _ in repo.keyword_search("Kutuzov", item_type="chunk")] == ["chunk:c1"]
        _repo(db)  # a second open finds the counts equal and does not duplicate
        assert repo.get_index_stats()["fulltext"]["chunk_count"] == 2

    def test_a_question_naming_an_entity_and_a_chunk_finds_both(self, db: Path) -> None:
        repo = _repo(db)
        repo.index_node(_node("n-dolokhov", "Dolokhov", "an officer of the Semenov regiment"))
        repo.index_node(_node("n-other", "Natasha Rostova", "the youngest daughter"))
        _write_chunks(db, [("c1", "He bet the Englishman he would drink rum on the ledge.")])

        both = repo.keyword_search("Did Dolokhov drink rum on the ledge?", item_type=None)
        assert {"n-dolokhov", "chunk:c1"} <= {i for i, _ in both}

    @pytest.mark.asyncio
    async def test_hybrid_search_has_a_keyword_half_for_chunks(self, db: Path) -> None:
        """No embedding at all: hybrid's chunk search still finds the chunk by its words."""
        repo = _repo(db)
        _write_chunks(db, [("c1", "Pierre inherited the Bezukhov fortune."), ("c2", "Rain.")])
        hits = await repo.hybrid_search("Who inherited the fortune?", k=5, item_type="chunk")
        assert [i for i, _ in hits] == ["chunk:c1"]


class TestFusion:
    """Rank fusion replaces the max() of BM25 and cosine."""

    def test_an_item_both_arms_found_outranks_either_arms_top(self) -> None:
        keyword = [("a", 25.0), ("both", 3.0)]
        vector = [("v", 0.9), ("both", 0.8)]
        fused = _fuse_rankings(keyword, vector, limit=10)
        assert fused[0][0] == "both"
        assert {i for i, _ in fused} == {"a", "both", "v"}
        assert all(0.0 < s <= 1.0 for _, s in fused)

    def test_the_top_of_a_single_arm_scores_one(self) -> None:
        assert _fuse_rankings([("x", 99.0)], [], limit=5) == [("x", 1.0)]
        assert _fuse_rankings([], [], limit=5) == []


class TestChunkBudget:
    """A search for chunks fills its budget with chunks (audit R3)."""

    @pytest.mark.asyncio
    async def test_higher_scoring_nodes_and_templates_do_not_take_chunk_slots(
        self, db: Path
    ) -> None:
        repo = _repo(db)
        query = [1.0, 0.0, 0.0, 0.0]
        repo.index_embeddings_batch(
            [(f"n{i}", query) for i in range(6)], item_type="node"
        )  # identical to the query
        for i in range(6):
            repo.index_template(f"t{i}", query)
        repo.index_embeddings_batch(
            [(f"chunk:c{i}", [1.0, 0.2 + i / 10, 0.0, 0.0]) for i in range(5)], item_type="chunk"
        )

        async def embed(_text: str) -> dict[str, list[float]]:
            return {"embedding": query}

        hits = await repo.hybrid_search(
            "a question",
            k=5,
            embedding_provider_callback=embed,
            min_similarity=0.3,
            item_type="chunk",
        )
        assert [i for i, _ in hits] == [f"chunk:c{i}" for i in range(5)]


class TestUnitVectors:
    """Stored vectors are unit length in cosine-metric tables (audit R7)."""

    def test_stored_vectors_are_unit_length_and_tables_are_cosine(self, db: Path) -> None:
        repo = _repo(db)
        repo.index_embeddings_batch([("n1", [3.0, 4.0, 0.0, 0.0])], item_type="node")
        with repo._engine.connect() as conn:
            blob = conn.execute(text("SELECT embedding FROM vec_search_nodes")).scalar()
            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'vec_search_nodes'")
            ).scalar()
        values = struct.unpack(f"<{DIM}f", blob)
        assert math.isclose(math.sqrt(sum(v * v for v in values)), 1.0, rel_tol=1e-6)
        assert values[:2] == pytest.approx((0.6, 0.8))
        assert "distance_metric=cosine" in ddl

    def test_a_legacy_l2_table_is_rebuilt_cosine_with_its_vectors_normalised(
        self, db: Path
    ) -> None:
        engine = get_engine(str(db))
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"CREATE VIRTUAL TABLE vec_search_chunks USING vec0("
                    f"embedding float[{DIM}], +item_id TEXT)"
                )
            )
            conn.exec_driver_sql(
                "INSERT INTO vec_search_chunks(item_id, embedding) VALUES (?, ?)",
                ("chunk:old", struct.pack(f"<{DIM}f", 10.0, 0.0, 0.0, 0.0)),
            )
            conn.exec_driver_sql(
                "INSERT INTO vec_search_chunks(item_id, embedding) VALUES (?, ?)",
                ("chunk:near", struct.pack(f"<{DIM}f", 0.7, 0.7, 0.0, 0.0)),
            )
        repo = _repo(db)
        with repo._engine.connect() as conn:
            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = 'vec_search_chunks'")
            ).scalar()
        assert "distance_metric=cosine" in ddl
        hits = repo.vector_search([1.0, 0.0, 0.0, 0.0], k=2, item_type="chunk")
        # L2 on the raw vectors put "near" (distance ~0.76) ahead of "old" (9.0)
        assert [i for i, _ in hits] == ["chunk:old", "chunk:near"]
        assert hits[0][1] == pytest.approx(1.0)
