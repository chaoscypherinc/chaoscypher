# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A keyword-only search repository (``vectors=False``) never touches another process's vectors.

With vector search off a process opens the database with its configured (often
default) dimensions. The open-time checks would drop and recreate the vector
tables at those dimensions, flag a reindex, or upgrade an old L2 table; a
keyword-only process does none of that.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos import SearchRepository


@pytest.fixture
def db_path(tmp_path: Path) -> Generator[Path]:
    path = tmp_path / "app.db"
    yield path
    evict_engine(path)


def _unit(dim: int) -> list[float]:
    return [1.0] + [0.0] * (dim - 1)


def _stored(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT key, value FROM search_metadata "
                "WHERE key IN ('embedding_model', 'vector_dim')"
            )
        ).fetchall()
    return {str(key): str(value) for key, value in rows}


def _ddl(engine: Engine, table: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table},
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_a_keyword_only_process_leaves_the_vector_tables_alone(db_path: Path) -> None:
    engine = get_engine(db_path)
    on = SearchRepository(engine, vector_dim=8, embedding_model="model-a")
    on.index_embeddings_batch([("chunk:c1", _unit(8)), ("chunk:c2", _unit(8))], item_type="chunk")

    off = SearchRepository(engine, vector_dim=1024, embedding_model="model-b", vectors=False)

    assert off.vectors is False
    assert off.count_vectors("chunk") == 2
    assert _stored(engine) == {"embedding_model": "model-a", "vector_dim": "8"}
    assert "float[8]" in _ddl(engine, "vec_search_chunks")
    assert off.needs_full_reindex is False
    assert off.is_rebuilding is False


def test_a_database_created_keyword_only_takes_vectors_at_another_dimension_later(
    db_path: Path,
) -> None:
    engine = get_engine(db_path)
    SearchRepository(engine, vector_dim=1024, embedding_model="model-a", vectors=False)

    on = SearchRepository(
        engine, vector_dim=8, embedding_model="model-b"
    )  # no SchemaIntegrityError

    assert "float[8]" in _ddl(engine, "vec_search_chunks")
    assert on.needs_full_reindex is False
    on.index_embeddings_batch([("chunk:c1", _unit(8))], item_type="chunk")
    assert on.count_vectors("chunk") == 1


def test_a_keyword_only_process_never_upgrades_an_l2_table(db_path: Path) -> None:
    engine = get_engine(db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE vec_search_chunks USING vec0(embedding float[8],+item_id TEXT)"
            )
        )
        conn.exec_driver_sql(
            "INSERT INTO vec_search_chunks(item_id, embedding) VALUES (?, ?)",
            ("chunk:c1", b"\x00\x00\x00\x40" + b"\x00" * 28),
        )
    before = _ddl(engine, "vec_search_chunks")

    off = SearchRepository(engine, vector_dim=8, embedding_model="model-a", vectors=False)

    assert _ddl(engine, "vec_search_chunks") == before
    assert "distance_metric=cosine" not in before
    assert off.count_vectors("chunk") == 1


def test_a_keyword_only_process_creates_missing_vector_tables_at_the_existing_dimension(
    db_path: Path,
) -> None:
    engine = get_engine(db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE vec_search_chunks USING vec0("
                "embedding float[8] distance_metric=cosine,+item_id TEXT)"
            )
        )

    off = SearchRepository(engine, vector_dim=1024, embedding_model="model-b", vectors=False)

    assert off.vector_dim == 8
    assert "float[8]" in _ddl(engine, "vec_search_nodes")
    assert "float[8]" in _ddl(engine, "vec_search_templates")


def test_a_keyword_only_process_indexes_a_nodes_stored_vector_and_queues_nothing(
    db_path: Path,
) -> None:
    from chaoscypher_core.models import Node

    engine = get_engine(db_path)
    SearchRepository(engine, vector_dim=8, embedding_model="model-a")
    off = SearchRepository(engine, vector_dim=1024, embedding_model="model-b", vectors=False)
    node = Node(id="n1", template_id="t", label="Lyon", properties={}, embedding=_unit(8))
    stray = Node(id="n2", template_id="t", label="Paris", properties={}, embedding=_unit(4))

    off.index_nodes_batch([node, stray])
    off.index_node(stray)

    assert off.count_vectors("node") == 1
    assert off.has_pending_reindex is False
    assert [i for i, _ in off.keyword_search("Paris", item_type="node")] == ["n2"]
