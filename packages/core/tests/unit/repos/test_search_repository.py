# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for unified SearchRepository (sqlite-vec + FTS5 in app.db)."""

import tempfile
from pathlib import Path

import pytest

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos import SearchRepository
from chaoscypher_core.models import Node


@pytest.fixture
def search_repo():
    """Create a SearchRepository backed by a temp app.db."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "app.db"
        engine = get_engine(db_path)

        repo = SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model")

        yield repo

        evict_engine(db_path)


def _make_node(
    node_id: str,
    label: str,
    embedding: list[float] | None = None,
) -> Node:
    """Helper to create a test Node."""
    return Node(
        id=node_id,
        label=label,
        template_id="test-template",
        properties={"role": "test"},
        embedding=embedding,
    )


class TestSchemaCreation:
    """Test that tables are created on init."""

    def test_per_type_vec_search_tables_exist(self, search_repo):
        """Per-type vec0 virtual tables should be created."""
        from sqlalchemy import text

        with search_repo._engine.connect() as conn:
            for table in ("vec_search_chunks", "vec_search_nodes", "vec_search_templates"):
                result = conn.execute(
                    text(f"SELECT name FROM sqlite_master WHERE name='{table}'")
                ).fetchone()
                assert result is not None, f"{table} not created"

    def test_legacy_vec_search_table_absent(self, search_repo):
        """Legacy mixed vec_search must not be (re)created."""
        from sqlalchemy import text

        with search_repo._engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE name='vec_search'")
            ).fetchone()
            assert result is None

    def test_fts5_tables_exist(self, search_repo):
        """fulltext_content table should be created."""
        from sqlalchemy import text

        with search_repo._engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE name='fulltext_content'")
            ).fetchone()
            assert result is not None


class TestVectorOperations:
    """Test vector indexing and search."""

    def test_index_and_search_embedding(self, search_repo):
        """Index a vector and search for it."""
        embedding = [1.0, 0.0, 0.0, 0.0]
        search_repo.index_node_embedding("node-1", embedding)

        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(results) >= 1
        assert results[0][0] == "node-1"
        assert results[0][1] > 0.9

    def test_index_embeddings_batch(self, search_repo):
        """Batch index multiple embeddings."""
        embeddings = [
            ("node-1", [1.0, 0.0, 0.0, 0.0]),
            ("node-2", [0.0, 1.0, 0.0, 0.0]),
        ]
        count = search_repo.index_embeddings_batch(embeddings, item_type="node")
        assert count == 2

        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert results[0][0] == "node-1"

    def test_remove_embedding(self, search_repo):
        """Remove an embedding from the per-type index."""
        search_repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])
        search_repo.remove_embedding("node-1", "node")

        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(results) == 0

    def test_chunk_prefix_convention(self, search_repo):
        """Chunk IDs use 'chunk:' prefix in return values."""
        embeddings = [("chunk:abc123", [1.0, 0.0, 0.0, 0.0])]
        search_repo.index_embeddings_batch(embeddings, item_type="chunk")

        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert results[0][0] == "chunk:abc123"

    def test_vector_search_with_item_type_filter(self, search_repo):
        """item_type filter narrows results."""
        search_repo.index_embeddings_batch([("node-1", [1.0, 0.0, 0.0, 0.0])], item_type="node")
        search_repo.index_embeddings_batch([("chunk:c1", [1.0, 0.0, 0.0, 0.0])], item_type="chunk")

        all_results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=10)
        assert len(all_results) == 2

        node_results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=10, item_type="node")
        assert len(node_results) == 1
        assert node_results[0][0] == "node-1"

    def test_cosine_similarity_clamped(self, search_repo):
        """Similarity scores clamped to [0.0, 1.0]."""
        search_repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])
        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        score = results[0][1]
        assert 0.0 <= score <= 1.0

    def test_upsert_replaces_existing(self, search_repo):
        """Re-indexing same ID replaces the embedding."""
        search_repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])
        search_repo.index_node_embedding("node-1", [0.0, 1.0, 0.0, 0.0])

        results = search_repo.vector_search([0.0, 1.0, 0.0, 0.0], k=5)
        assert results[0][0] == "node-1"
        assert results[0][1] > 0.9

    def test_dimension_mismatch_skipped(self, search_repo):
        """Wrong dimension embeddings are skipped silently."""
        count = search_repo.index_embeddings_batch(
            [("node-1", [1.0, 0.0])],
            item_type="node",  # dim=2, expect 4
        )
        assert count == 0


class TestPerTypeVectorDispatch:
    """Tests that vector ops route to the per-type vec0 tables and use KNN MATCH."""

    def test_node_write_lands_in_nodes_table_only(self, search_repo):
        """Indexing a node embedding writes to vec_search_nodes only."""
        from sqlalchemy import text

        search_repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])

        with search_repo._engine.connect() as conn:
            nodes = conn.execute(
                text("SELECT COUNT(*) FROM vec_search_nodes WHERE item_id = 'node-1'")
            ).scalar()
            chunks = conn.execute(
                text("SELECT COUNT(*) FROM vec_search_chunks WHERE item_id = 'node-1'")
            ).scalar()
            templates = conn.execute(
                text("SELECT COUNT(*) FROM vec_search_templates WHERE item_id = 'node-1'")
            ).scalar()

        assert nodes == 1
        assert chunks == 0
        assert templates == 0

    def test_chunk_batch_lands_in_chunks_table_only(self, search_repo):
        """index_embeddings_batch with item_type='chunk' targets vec_search_chunks."""
        from sqlalchemy import text

        search_repo.index_embeddings_batch(
            [("chunk:c1", [1.0, 0.0, 0.0, 0.0]), ("chunk:c2", [0.0, 1.0, 0.0, 0.0])],
            item_type="chunk",
        )

        with search_repo._engine.connect() as conn:
            chunks = conn.execute(text("SELECT COUNT(*) FROM vec_search_chunks")).scalar()
            nodes = conn.execute(text("SELECT COUNT(*) FROM vec_search_nodes")).scalar()

        assert chunks == 2
        assert nodes == 0

    def test_template_index_lands_in_templates_table_only(self, search_repo):
        """index_template targets vec_search_templates."""
        from sqlalchemy import text

        search_repo.index_template("tmpl-1", [1.0, 0.0, 0.0, 0.0])

        with search_repo._engine.connect() as conn:
            templates = conn.execute(text("SELECT COUNT(*) FROM vec_search_templates")).scalar()
            nodes = conn.execute(text("SELECT COUNT(*) FROM vec_search_nodes")).scalar()

        assert templates == 1
        assert nodes == 0

    def test_remove_embedding_routes_by_item_type(self, search_repo):
        """remove_embedding only deletes from the table matching item_type."""
        # Seed the same ID in two tables to prove the dispatch is precise.
        search_repo.index_node_embedding("dup-id", [1.0, 0.0, 0.0, 0.0])
        search_repo.index_embeddings_batch([("dup-id", [0.0, 1.0, 0.0, 0.0])], item_type="chunk")

        search_repo.remove_embedding("dup-id", "node")

        node_hits = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5, item_type="node")
        chunk_hits = search_repo.vector_search([0.0, 1.0, 0.0, 0.0], k=5, item_type="chunk")

        assert all(item_id != "dup-id" for item_id, _ in node_hits)
        assert any(item_id == "dup-id" for item_id, _ in chunk_hits)

    def test_remove_embeddings_batch_routes_by_item_type(self, search_repo):
        """remove_embeddings_batch only deletes from the matching per-type table."""
        ids = ["chunk:c1", "chunk:c2"]
        search_repo.index_embeddings_batch(
            [(cid, [1.0, 0.0, 0.0, 0.0]) for cid in ids],
            item_type="chunk",
        )
        # Node-flavoured copy of the same IDs (unusual, but proves routing).
        search_repo.index_embeddings_batch(
            [(cid, [1.0, 0.0, 0.0, 0.0]) for cid in ids],
            item_type="node",
        )

        removed = search_repo.remove_embeddings_batch(ids, "chunk")

        assert removed == 2
        chunk_hits = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5, item_type="chunk")
        node_hits = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5, item_type="node")
        assert chunk_hits == []
        assert {item_id for item_id, _ in node_hits} == set(ids)

    def test_unknown_item_type_raises(self, search_repo):
        """An invalid item_type raises ValueError instead of silently scanning."""
        with pytest.raises(ValueError, match="unknown vector item_type"):
            search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5, item_type="bogus")

    def test_filtered_search_uses_knn_match_plan(self, search_repo):
        """vector_search(item_type='chunk') uses the sqlite-vec KNN MATCH plan.

        EXPLAIN QUERY PLAN for the per-type table should mention ``vec0``
        (the virtual-table KNN scan), proving we are NOT computing
        ``vec_distance_cosine`` once per row in a sequential scan.
        """
        from sqlalchemy import text

        search_repo.index_embeddings_batch([("chunk:c1", [1.0, 0.0, 0.0, 0.0])], item_type="chunk")

        with search_repo._engine.connect() as conn:
            plan = conn.execute(
                text(
                    "EXPLAIN QUERY PLAN "
                    "SELECT item_id, vec_distance_cosine(embedding, X'00000000') "
                    "FROM vec_search_chunks "
                    "WHERE embedding MATCH X'00000000' AND k = 5"
                )
            ).fetchall()

        # The KNN MATCH path goes through the vec0 virtual table; sequential
        # scans report "SCAN <table>" without "USING" or "vec0".
        plan_text = " ".join(str(row) for row in plan).lower()
        assert "vec_search_chunks" in plan_text
        assert "vec0" in plan_text or "virtual table" in plan_text, (
            f"Expected KNN/virtual-table plan, got: {plan_text}"
        )

    def test_mixed_item_type_query_merges_top_k_across_tables(self, search_repo):
        """item_type=None returns the global top-k across all per-type tables."""
        # Identical embedding in all three tables — global top-3 must include one of each.
        search_repo.index_embeddings_batch([("node-1", [1.0, 0.0, 0.0, 0.0])], item_type="node")
        search_repo.index_embeddings_batch([("chunk:c1", [1.0, 0.0, 0.0, 0.0])], item_type="chunk")
        search_repo.index_template("tmpl-1", [1.0, 0.0, 0.0, 0.0])

        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=10)
        ids = {item_id for item_id, _ in results}

        assert "node-1" in ids
        assert "chunk:c1" in ids
        # template_semantic_search prefixes with ``template:`` so the raw
        # vector_search() returns the prefixed ID.
        assert "template:tmpl-1" in ids

    def test_mixed_item_type_top_k_sorted_descending(self, search_repo):
        """Merged results across per-type tables come back sorted by similarity."""
        # Higher-similarity vector lives in chunks; lower-similarity in nodes.
        search_repo.index_embeddings_batch(
            [("chunk:c-best", [1.0, 0.0, 0.0, 0.0])], item_type="chunk"
        )
        search_repo.index_embeddings_batch([("node-worse", [0.5, 0.5, 0.5, 0.5])], item_type="node")

        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)

        # Sorted descending by similarity; chunk:c-best must outrank node-worse.
        assert len(results) >= 2
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)
        assert results[0][0] == "chunk:c-best"

    def test_mixed_item_type_clamps_to_k(self, search_repo):
        """item_type=None must clamp the merged result to k entries."""
        for cid in ("chunk:a", "chunk:b", "chunk:c"):
            search_repo.index_embeddings_batch([(cid, [1.0, 0.0, 0.0, 0.0])], item_type="chunk")
        for nid in ("node-1", "node-2", "node-3"):
            search_repo.index_embeddings_batch([(nid, [1.0, 0.0, 0.0, 0.0])], item_type="node")

        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=2)
        assert len(results) == 2


class TestFulltextOperations:
    """Test FTS5 keyword search."""

    def test_index_and_keyword_search(self, search_repo):
        """Index a node and find it by keyword."""
        node = _make_node("node-1", "Albert Einstein")
        search_repo.index_node(node)

        results = search_repo.keyword_search("Einstein", limit=5)
        assert len(results) >= 1
        assert results[0][0] == "node-1"

    def test_index_nodes_batch(self, search_repo):
        """Batch index nodes for keyword search."""
        nodes = [
            _make_node("n1", "Albert Einstein"),
            _make_node("n2", "Isaac Newton"),
        ]
        search_repo.index_nodes_batch(nodes)

        results = search_repo.keyword_search("Newton", limit=5)
        assert len(results) >= 1
        assert results[0][0] == "n2"

    def test_delete_node_removes_from_fts(self, search_repo):
        """Deleting a node removes it from keyword search."""
        node = _make_node("node-1", "Albert Einstein")
        search_repo.index_node(node)
        search_repo.delete_node("node-1")

        results = search_repo.keyword_search("Einstein", limit=5)
        assert len(results) == 0

    def test_empty_query_returns_empty(self, search_repo):
        """Empty query returns no results."""
        results = search_repo.keyword_search("", limit=5)
        assert results == []


class TestCombinedOperations:
    """Test operations that touch both FTS5 and the per-type vec0 tables."""

    def test_index_node_with_embedding_indexes_both(self, search_repo):
        """index_node indexes in both FTS5 and vector."""
        node = _make_node("node-1", "Albert Einstein", embedding=[1.0, 0.0, 0.0, 0.0])
        search_repo.index_node(node)

        kw_results = search_repo.keyword_search("Einstein", limit=5)
        vec_results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(kw_results) >= 1
        assert len(vec_results) >= 1

    def test_delete_node_removes_from_both(self, search_repo):
        """delete_node removes from both FTS5 and vector."""
        node = _make_node("node-1", "Albert Einstein", embedding=[1.0, 0.0, 0.0, 0.0])
        search_repo.index_node(node)
        search_repo.delete_node("node-1")

        kw_results = search_repo.keyword_search("Einstein", limit=5)
        vec_results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(kw_results) == 0
        assert len(vec_results) == 0

    def test_clear_all_indices(self, search_repo):
        """clear_all_indices empties both indexes."""
        node = _make_node("node-1", "Einstein", embedding=[1.0, 0.0, 0.0, 0.0])
        search_repo.index_node(node)
        search_repo.clear_all_indices()

        stats = search_repo.get_index_stats()
        assert stats["fulltext"]["document_count"] == 0
        assert stats["vector"]["vector_count"] == 0


class TestTemplateSearch:
    """Test template-specific vector operations."""

    def test_index_and_search_template(self, search_repo):
        """Index a template and search for it."""
        search_repo.index_template("tmpl-1", [1.0, 0.0, 0.0, 0.0])

        results = search_repo.template_semantic_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(results) >= 1
        assert results[0][0] == "tmpl-1"

    def test_template_search_excludes_nodes(self, search_repo):
        """Template search only returns templates, not nodes."""
        search_repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])
        search_repo.index_template("tmpl-1", [1.0, 0.0, 0.0, 0.0])

        results = search_repo.template_semantic_search([1.0, 0.0, 0.0, 0.0], k=5)
        ids = [r[0] for r in results]
        assert "tmpl-1" in ids
        assert "node-1" not in ids


class TestStats:
    """Test index statistics."""

    def test_get_index_stats(self, search_repo):
        """Stats reflect indexed data."""
        node = _make_node("node-1", "Einstein", embedding=[1.0, 0.0, 0.0, 0.0])
        search_repo.index_node(node)

        stats = search_repo.get_index_stats()
        assert stats["fulltext"]["document_count"] == 1
        assert stats["vector"]["vector_count"] == 1
        assert stats["vector"]["dimensions"] == 4


# ========================================================================
# Fixtures for model-aware tests
# ========================================================================


@pytest.fixture
def search_repo_with_model():
    """Create a SearchRepository with explicit model name."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "app.db"
        engine = get_engine(db_path)

        repo = SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model-v1")

        yield repo, engine, db_path

        evict_engine(db_path)


# ========================================================================
# Model change detection tests
# ========================================================================


class TestModelChangeDetection:
    """Test search_metadata tracking and model change detection."""

    def test_metadata_stored_on_init(self, search_repo_with_model):
        """First init stores model and dim in search_metadata."""
        repo, engine, _ = search_repo_with_model
        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM search_metadata WHERE key = 'embedding_model'")
            ).fetchone()
            assert row is not None
            assert row[0] == "test-model-v1"

            row = conn.execute(
                text("SELECT value FROM search_metadata WHERE key = 'vector_dim'")
            ).fetchone()
            assert row is not None
            assert row[0] == "4"

    def test_same_model_no_reindex_flag(self, search_repo_with_model):
        """Re-opening with same model does not set needs_full_reindex."""
        repo, engine, db_path = search_repo_with_model
        repo2 = SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model-v1")
        assert repo2.needs_full_reindex is False

    def test_model_name_change_sets_flag(self, search_repo_with_model):
        """Different model name (same dim) sets needs_full_reindex."""
        repo, engine, db_path = search_repo_with_model
        repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])

        repo2 = SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model-v2")
        assert repo2.needs_full_reindex is True

    def test_dimension_change_recreates_table(self, search_repo_with_model):
        """Different dimensions drops and recreates the per-type vec0 tables."""
        repo, engine, db_path = search_repo_with_model
        repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])

        repo2 = SearchRepository(engine=engine, vector_dim=8, embedding_model="test-model-v2")
        assert repo2.vector_dim == 8
        assert repo2.needs_full_reindex is True

        results = repo2.vector_search([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], k=5)
        assert results == []

    def test_no_metadata_first_time(self):
        """Brand new database has no stale flag."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            engine = get_engine(db_path)
            repo = SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model-v1")
            assert repo.needs_full_reindex is False
            evict_engine(db_path)

    def test_no_reindex_when_no_vectors_exist(self, search_repo_with_model):
        """Model change with empty vec0 tables does not set reindex flag."""
        repo, engine, db_path = search_repo_with_model
        repo2 = SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model-v2")
        assert repo2.needs_full_reindex is False


# ========================================================================
# Reindex queue tests
# ========================================================================


class TestReindexQueue:
    """Test the reindex queue for per-item dimension mismatches."""

    def test_schedule_reindex_adds_to_queue(self, search_repo):
        """schedule_reindex stores item_id, text, and item_type."""
        search_repo.schedule_reindex("node-1", "Albert Einstein physicist", "node")

        assert len(search_repo._reindex_queue) == 1
        item = search_repo._reindex_queue[0]
        assert item["item_id"] == "node-1"
        assert item["text"] == "Albert Einstein physicist"
        assert item["item_type"] == "node"

    def test_reindex_queue_starts_empty(self, search_repo):
        """Queue is empty on init."""
        assert search_repo._reindex_queue == []

    @pytest.mark.asyncio
    async def test_flush_reindex_embeds_and_indexes(self, search_repo):
        """flush_reindex calls embed_fn, indexes correct-dim vectors, clears queue."""
        search_repo.schedule_reindex("node-1", "Einstein", "node")

        async def mock_embed(texts):
            return [[1.0, 0.0, 0.0, 0.0]] * len(texts)

        count = await search_repo.flush_reindex(mock_embed)

        assert count == 1
        assert search_repo._reindex_queue == []
        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(results) == 1
        assert results[0][0] == "node-1"

    @pytest.mark.asyncio
    async def test_flush_reindex_empty_queue_returns_zero(self, search_repo):
        """Flushing an empty queue is a no-op."""

        async def mock_embed(texts):
            return [[1.0, 0.0, 0.0, 0.0]] * len(texts)

        count = await search_repo.flush_reindex(mock_embed)
        assert count == 0

    @pytest.mark.asyncio
    async def test_flush_reindex_with_service(self, search_repo):
        """flush_reindex_with_service convenience wrapper works."""
        search_repo.schedule_reindex("node-1", "Einstein", "node")

        class MockEmbeddingService:
            async def batch_embed(self, texts):
                class Result:
                    embeddings = [[1.0, 0.0, 0.0, 0.0]] * len(texts)

                return Result()

        count = await search_repo.flush_reindex_with_service(MockEmbeddingService())
        assert count == 1


# ========================================================================
# Indexing mismatch detection tests
# ========================================================================


class TestIndexingWithMismatch:
    """Test that indexing methods queue mismatches with text."""

    def test_index_node_queues_mismatch_with_text(self, search_repo):
        """Node with wrong-dim embedding gets queued with searchable text."""
        node = _make_node("node-1", "Albert Einstein", embedding=[1.0, 0.0])
        search_repo.index_node(node)

        # FTS5 works immediately
        kw = search_repo.keyword_search("Einstein", limit=5)
        assert len(kw) >= 1

        # Not in vector index
        vec = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(vec) == 0

        # Queued with text
        queued = [q for q in search_repo._reindex_queue if q.get("text")]
        assert len(queued) == 1
        assert queued[0]["item_id"] == "node-1"
        assert "Einstein" in queued[0]["text"]

    def test_index_nodes_batch_queues_mismatches(self, search_repo):
        """Batch indexing queues mismatched nodes, indexes correct ones."""
        nodes = [
            _make_node("n1", "Albert Einstein", embedding=[1.0, 0.0]),  # wrong dim
            _make_node("n2", "Isaac Newton", embedding=[0.0, 1.0, 0.0, 0.0]),  # correct
        ]
        search_repo.index_nodes_batch(nodes)

        # Both keyword searchable
        assert len(search_repo.keyword_search("Einstein", limit=5)) >= 1
        assert len(search_repo.keyword_search("Newton", limit=5)) >= 1

        # Only Newton in vector index
        vec = search_repo.vector_search([0.0, 1.0, 0.0, 0.0], k=5)
        assert len(vec) == 1
        assert vec[0][0] == "n2"

        # Einstein queued
        queued = [q for q in search_repo._reindex_queue if q.get("text")]
        assert len(queued) == 1
        assert "Einstein" in queued[0]["text"]

    def test_batch_index_embeddings_with_text_lookup(self, search_repo):
        """Mismatched embeddings are queued when text_lookup is provided."""
        embeddings = [
            ("chunk:c1", [1.0, 0.0]),  # wrong dim
            ("chunk:c2", [0.0, 1.0, 0.0, 0.0]),  # correct
        ]
        text_lookup = {"chunk:c1": "quantum entanglement"}
        count = search_repo.index_embeddings_batch(
            embeddings, item_type="chunk", text_lookup=text_lookup
        )
        assert count == 1

        queued = [q for q in search_repo._reindex_queue if q.get("text")]
        assert len(queued) == 1
        assert queued[0]["text"] == "quantum entanglement"

    def test_batch_index_embeddings_without_text_skips(self, search_repo):
        """Without text_lookup, mismatched items are logged and skipped (not queued)."""
        count = search_repo.index_embeddings_batch([("chunk:c1", [1.0, 0.0])], item_type="chunk")
        assert count == 0
        # No text = not queued (can't re-embed)
        assert len(search_repo._reindex_queue) == 0

    def test_index_node_embedding_wrong_dim_skips(self, search_repo):
        """index_node_embedding with wrong dim logs and skips."""
        search_repo.index_node_embedding("node-1", [1.0, 0.0])
        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(results) == 0
        # Not queued (no text context)
        assert len(search_repo._reindex_queue) == 0


# ========================================================================
# Vector search dimension validation tests
# ========================================================================


class TestVectorSearchValidation:
    """Test that vector_search validates query dimensions."""

    def test_wrong_query_dimension_returns_empty(self, search_repo):
        """Query with wrong dimensions returns empty instead of crashing."""
        search_repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])
        results = search_repo.vector_search([1.0, 0.0], k=5)
        assert results == []

    def test_correct_query_dimension_works(self, search_repo):
        """Query with correct dimensions still works normally."""
        search_repo.index_node_embedding("node-1", [1.0, 0.0, 0.0, 0.0])
        results = search_repo.vector_search([1.0, 0.0, 0.0, 0.0], k=5)
        assert len(results) == 1


# ========================================================================
# Session-mode (transactional) tests
#
# When a caller passes session=<some Session>, SearchRepository is
# expected to:
#   1. Route writes through that session's connection instead of opening
#      its own engine.connect() — so the writes join the caller's
#      transaction.
#   2. Not auto-commit. The caller owns transaction lifecycle; writes
#      stay buffered in the session's transaction until the caller
#      commits or rolls back.
#   3. Propagate exceptions instead of swallowing (unlike session=None
#      which keeps historical best-effort semantics). This lets the
#      caller roll back cleanly when a write fails inside their
#      transaction scope.
# ========================================================================


@pytest.fixture
def search_repo_with_session():
    """SearchRepository + a Session bound to the same engine.

    Yields ``(repo, session)``. Callers close the session on teardown via
    the context manager, and we evict the engine so the next test gets a
    fresh database.
    """
    from sqlmodel import Session

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "app.db"
        engine = get_engine(db_path)

        repo = SearchRepository(engine=engine, vector_dim=4, embedding_model="test-model")
        session = Session(engine)

        try:
            yield repo, session
        finally:
            session.close()
            evict_engine(db_path)


class TestSessionMode:
    """Verify the session-aware write path."""

    def test_write_visible_in_caller_session_before_commit(self, search_repo_with_session):
        """After a session-mode write, the row is visible via the caller's
        session (shared connection) but NOT via a separate engine connection
        (caller's transaction not yet committed).
        """
        from sqlalchemy import text

        repo, session = search_repo_with_session
        repo.index_node_embedding("node-shared", [1.0, 0.0, 0.0, 0.0], session=session)

        # Visible via the same session's connection
        conn_same = session.connection()
        row = conn_same.execute(
            text("SELECT item_id FROM vec_search_nodes WHERE item_id = :id"),
            {"id": "node-shared"},
        ).first()
        assert row is not None

        # NOT visible via a separate engine connection (different transaction)
        with repo._engine.connect() as conn_separate:
            row = conn_separate.execute(
                text("SELECT item_id FROM vec_search_nodes WHERE item_id = :id"),
                {"id": "node-shared"},
            ).first()
            assert row is None, (
                "Write should be buffered in caller's transaction, "
                "not yet visible to outside connections"
            )

    def test_write_visible_to_others_after_caller_commits(self, search_repo_with_session):
        """After the caller commits their session, the row becomes visible
        to separate connections. This proves we don't auto-commit on the
        caller's behalf.
        """
        from sqlalchemy import text

        repo, session = search_repo_with_session
        repo.index_node_embedding("node-committed", [1.0, 0.0, 0.0, 0.0], session=session)

        session.commit()

        with repo._engine.connect() as conn:
            row = conn.execute(
                text("SELECT item_id FROM vec_search_nodes WHERE item_id = :id"),
                {"id": "node-committed"},
            ).first()
            assert row is not None

    def test_write_rolled_back_if_caller_rolls_back(self, search_repo_with_session):
        """If the caller rolls back, the session-mode write is rolled back
        with them — no orphaned search index entries.
        """
        from sqlalchemy import text

        repo, session = search_repo_with_session
        repo.index_node_embedding("node-rollback", [1.0, 0.0, 0.0, 0.0], session=session)

        session.rollback()

        with repo._engine.connect() as conn:
            row = conn.execute(
                text("SELECT item_id FROM vec_search_nodes WHERE item_id = :id"),
                {"id": "node-rollback"},
            ).first()
            assert row is None, "Rollback on caller's session must also revert session-mode writes"

    def test_session_mode_exception_propagates(self, search_repo_with_session, monkeypatch):
        """In session mode, a write failure raises instead of being swallowed.

        Contrast with the no-session path, which logs and returns normally
        to preserve historical best-effort semantics. When the caller has
        opted into a shared transaction they need the exception so they
        can roll back.
        """
        repo, session = search_repo_with_session

        def broken_upsert(*args, **kwargs):
            raise RuntimeError("injected-upsert-failure")

        monkeypatch.setattr(repo, "_upsert_vector", broken_upsert)

        with pytest.raises(RuntimeError, match="injected-upsert-failure"):
            repo.index_node_embedding("node-err", [1.0, 0.0, 0.0, 0.0], session=session)

    def test_no_session_path_still_swallows_on_error(self, search_repo, monkeypatch):
        """Regression guard: the original no-session code path keeps its
        best-effort / swallow-and-log behavior so existing callers aren't
        retroactively forced to handle exceptions.
        """

        def broken_upsert(*args, **kwargs):
            raise RuntimeError("injected")

        monkeypatch.setattr(search_repo, "_upsert_vector", broken_upsert)

        # Should not raise — historical behavior preserved.
        search_repo.index_node_embedding("node-no-session", [1.0, 0.0, 0.0, 0.0])

    def test_batch_write_with_session_is_atomic_with_caller(self, search_repo_with_session):
        """index_nodes_batch in session mode participates in the caller's
        transaction just like the single-row path.
        """
        from sqlalchemy import text

        repo, session = search_repo_with_session
        nodes = [_make_node(f"batch-{i}", f"Label {i}", [1.0, 0.0, 0.0, 0.0]) for i in range(3)]

        repo.index_nodes_batch(nodes, session=session)

        # Rollback reverts all three writes together
        session.rollback()

        with repo._engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM fulltext_content WHERE node_id LIKE 'batch-%'"),
            ).scalar()
            assert row == 0

    def test_delete_with_session_participates_in_transaction(self, search_repo_with_session):
        """delete_nodes_batch also respects the session contract."""
        from sqlalchemy import text

        repo, session = search_repo_with_session

        # Seed via the no-session path so the row is committed independently
        node = _make_node("will-delete", "To Delete", [1.0, 0.0, 0.0, 0.0])
        repo.index_node(node)

        # Delete via session mode, then roll back — the row should survive
        repo.delete_nodes_batch(["will-delete"], session=session)
        session.rollback()

        with repo._engine.connect() as conn:
            row = conn.execute(
                text("SELECT node_id FROM fulltext_content WHERE node_id = :id"),
                {"id": "will-delete"},
            ).first()
            assert row is not None, "Rollback should have reverted the delete"


# ========================================================================
# Task 4.4: Bulk vec upsert query-count tests
# ========================================================================


class TestBulkVecUpsert:
    """Verify that batch upsert issues O(1) SQL statements, not O(N)."""

    def test_index_embeddings_batch_issues_constant_statements(self, search_repo):
        """index_embeddings_batch for N items must issue a bounded number of
        DELETEs and INSERTs — not one pair per item.

        With 100 vectors and a 500-item chunk size we expect at most 1 DELETE
        and 1 executemany INSERT (both counts should be <=3 to allow any
        internal bookkeeping overhead).
        """
        from sqlalchemy import event

        deletes: list[str] = []
        inserts: list[tuple[str, bool]] = []

        @event.listens_for(search_repo._engine, "before_cursor_execute")
        def _log(conn, cursor, statement, parameters, context, executemany):
            stripped = statement.lstrip().upper()
            if stripped.startswith("DELETE FROM VEC_SEARCH"):
                deletes.append(statement)
            elif stripped.startswith("INSERT INTO VEC_SEARCH"):
                inserts.append((statement, executemany))

        n = 100
        dim = 4  # matches search_repo fixture (vector_dim=4)
        import random

        embeddings = [(f"node-{i}", [random.random() for _ in range(dim)]) for i in range(n)]

        count = search_repo.index_embeddings_batch(embeddings, item_type="node")
        assert count == n

        # Must be constant (at most a few), NOT O(N)
        assert len(deletes) <= 3, (
            f"Too many DELETE statements: {len(deletes)} (expected <=3 for {n} items)"
        )
        assert len(inserts) <= 3, (
            f"Too many INSERT statements: {len(inserts)} (expected <=3 for {n} items)"
        )

        # Sanity check: data is actually in the index
        results = search_repo.vector_search(embeddings[0][1], k=5)
        assert len(results) > 0

    def test_index_nodes_batch_vec_issues_constant_statements(self, search_repo):
        """index_nodes_batch vector portion must also be O(1) statements."""
        from sqlalchemy import event

        deletes: list[str] = []
        inserts: list[tuple[str, bool]] = []

        @event.listens_for(search_repo._engine, "before_cursor_execute")
        def _log(conn, cursor, statement, parameters, context, executemany):
            stripped = statement.lstrip().upper()
            if stripped.startswith("DELETE FROM VEC_SEARCH"):
                deletes.append(statement)
            elif stripped.startswith("INSERT INTO VEC_SEARCH"):
                inserts.append((statement, executemany))

        import random

        n = 50
        nodes = [
            _make_node(f"bulk-{i}", f"Label {i}", [random.random() for _ in range(4)])
            for i in range(n)
        ]
        search_repo.index_nodes_batch(nodes)

        assert len(deletes) <= 3, (
            f"Too many DELETE statements: {len(deletes)} (expected <=3 for {n} nodes)"
        )
        assert len(inserts) <= 3, (
            f"Too many INSERT statements: {len(inserts)} (expected <=3 for {n} nodes)"
        )
