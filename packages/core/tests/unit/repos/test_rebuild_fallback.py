# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Task 6.3: semantic search falls back to FTS5 when vec rebuild is in progress."""

from __future__ import annotations

import pytest

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.models import Node


@pytest.fixture
def repo_with_corpus(tmp_path):
    """SearchRepository with a small indexed corpus and model metadata stored."""
    db_path = tmp_path / "app.db"
    engine = get_engine(db_path)

    repo = SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model-v1")

    # Index two nodes so FTS5 has results to return.
    node_a = Node(
        id="node-a",
        label="Albert Einstein physicist",
        template_id="person",
        properties={},
        embedding=[1.0, 0.0, 0.0, 0.0],
    )
    node_b = Node(
        id="node-b",
        label="Niels Bohr physicist",
        template_id="person",
        properties={},
        embedding=[0.0, 1.0, 0.0, 0.0],
    )
    repo.index_node(node_a)
    repo.index_node(node_b)

    yield repo, engine, db_path

    evict_engine(db_path)


def _set_rebuilding(repo: SearchRepository, flag: bool) -> None:
    """Directly write the is_rebuilding flag for test setup."""
    from sqlalchemy import text

    with repo._engine.connect() as conn:
        conn.execute(
            text(
                "INSERT OR REPLACE INTO search_metadata (key, value) VALUES ('is_rebuilding', :v)"
            ),
            {"v": "true" if flag else "false"},
        )
        conn.commit()


# ===========================================================================
# Tests
# ===========================================================================


class TestRebuildFallback:
    """is_rebuilding flag causes semantic_search to delegate to FTS5."""

    @pytest.mark.asyncio
    async def test_semantic_search_returns_fts_results_when_rebuilding(self, repo_with_corpus):
        """With is_rebuilding=true, semantic path delegates to FTS5 keyword search."""
        repo, engine, _ = repo_with_corpus

        # Mark the rebuild as in-progress and empty the per-type vec0 tables.
        _set_rebuilding(repo, True)
        from sqlalchemy import text

        with engine.connect() as conn:
            for table in ("vec_search_chunks", "vec_search_nodes", "vec_search_templates"):
                conn.execute(text(f"DELETE FROM {table}"))
            conn.commit()

        # A callback that would normally supply an embedding — should NOT be called.
        callback_called = False

        async def embedding_callback(query: str):
            nonlocal callback_called
            callback_called = True
            return [1.0, 0.0, 0.0, 0.0]

        results = await repo.semantic_search(
            "physicist",
            k=5,
            embedding_provider_callback=embedding_callback,
        )

        # FTS5 must find at least one result (both nodes contain "physicist").
        assert len(results) > 0, "Expected FTS5 results during rebuild but got none"
        # The embedding callback must NOT have been called — we fell back before the vec path.
        assert not callback_called, "Embedding callback should not be invoked during rebuild"

    def test_is_rebuilding_cleared_after_dim_change_rebuild(self, tmp_path):
        """_check_model_change clears is_rebuilding after recreating the per-type vec0 tables."""
        db_path = tmp_path / "app.db"
        engine = get_engine(db_path)

        # First open — stores metadata.
        repo1 = SearchRepository(engine=engine, vector_dim=4, embedding_model="model-v1")
        # Index a vector so dim-change triggers the recreate path.
        repo1.index_node_embedding("n1", [1.0, 0.0, 0.0, 0.0])

        # Second open with a different dim — triggers DROP/recreate.
        repo2 = SearchRepository(engine=engine, vector_dim=8, embedding_model="model-v2")

        # After __init__ completes, is_rebuilding must be False.
        assert repo2.is_rebuilding is False, "is_rebuilding should be cleared after rebuild"

        evict_engine(db_path)

    def test_is_rebuilding_false_in_normal_state(self, repo_with_corpus):
        """Without triggering a dim change, the flag stays false."""
        repo, _, _ = repo_with_corpus
        assert repo.is_rebuilding is False

    @pytest.mark.asyncio
    async def test_hybrid_search_falls_back_to_fts_when_rebuilding(self, repo_with_corpus):
        """hybrid_search also respects is_rebuilding and skips vec path."""
        repo, engine, _ = repo_with_corpus

        _set_rebuilding(repo, True)
        from sqlalchemy import text

        with engine.connect() as conn:
            for table in ("vec_search_chunks", "vec_search_nodes", "vec_search_templates"):
                conn.execute(text(f"DELETE FROM {table}"))
            conn.commit()

        callback_called = False

        async def embedding_callback(query: str):
            nonlocal callback_called
            callback_called = True
            return [1.0, 0.0, 0.0, 0.0]

        results = await repo.hybrid_search(
            "physicist",
            k=5,
            embedding_provider_callback=embedding_callback,
        )

        assert len(results) > 0, "Expected FTS5 results during rebuild but got none"
        assert not callback_called, "Embedding callback should not be invoked during rebuild"
