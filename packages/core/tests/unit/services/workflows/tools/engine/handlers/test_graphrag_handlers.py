# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for GraphRAGToolHandlers.

Covers degradation paths, pipeline mechanics, deduplication, and edge cases
using mocked repositories and callbacks.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.graph_edges import edges_by_node_via
from tests.fakes.graphrag_world import batch_lookup, make_chunk


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def make_node(nid: str, label: str, template_id: str = "default") -> SimpleNamespace:
    """Create a minimal node object for mocking graph repository returns.

    Args:
        nid: Node identifier.
        label: Human-readable label.
        template_id: Template / type identifier.

    Returns:
        A ``SimpleNamespace`` mimicking a node entity.

    """
    return SimpleNamespace(
        id=nid,
        label=label,
        template_id=template_id,
        source_id=None,
        created_at=None,
        updated_at=None,
        properties={},
    )


def make_edge(
    eid: str,
    source_id: str,
    target_id: str,
    label: str = "related_to",
) -> SimpleNamespace:
    """Create a minimal edge object for mocking graph repository returns.

    Args:
        eid: Edge identifier.
        source_id: Source node identifier.
        target_id: Target node identifier.
        label: Relationship label.

    Returns:
        A ``SimpleNamespace`` mimicking an edge entity.

    """
    return SimpleNamespace(
        id=eid,
        source_node_id=source_id,
        target_node_id=target_id,
        label=label,
        template_id="default",
    )


def _configure_graph_repo(
    repo: MagicMock,
    nodes: list[SimpleNamespace],
    edges: list[SimpleNamespace],
    batch_nodes: list[SimpleNamespace] | None = None,
) -> None:
    """Configure a graph repo mock with both standard and minimal method variants.

    ``GraphRAGToolHandlers`` uses ``hasattr`` to prefer ``list_nodes_minimal``
    and ``list_edges_minimal`` over their standard equivalents.  Because
    ``MagicMock`` auto-creates any attribute, ``hasattr`` always returns
    ``True``, so both variants must be configured explicitly.

    Args:
        repo: The ``MagicMock`` graph repository to configure.
        nodes: Node list to return from both ``list_nodes`` variants.
        edges: Edge list to return from both ``list_edges`` variants.
        batch_nodes: Optional node list for ``get_nodes_batch``; defaults to
            ``nodes`` when not provided.

    """
    repo.list_nodes.return_value = nodes
    repo.list_nodes_minimal.return_value = nodes
    repo.list_edges.return_value = edges
    repo.list_edges_by_node.side_effect = edges_by_node_via(repo.list_edges)
    repo.list_edges_minimal.return_value = edges
    repo.get_nodes_batch.return_value = batch_nodes if batch_nodes is not None else nodes


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> EngineSettings:
    """Provide default EngineSettings (all graphrag defaults are acceptable)."""
    return EngineSettings()


@pytest.fixture
def graph_repo() -> MagicMock:
    """Mock graph repository with all required methods.

    Both the standard and *_minimal variants are configured because
    GraphRAGToolHandlers uses ``hasattr`` to prefer minimal variants, and
    ``MagicMock`` auto-creates any attribute, making ``hasattr`` always
    return ``True``.
    """
    repo = MagicMock()
    _configure_graph_repo(repo, nodes=[], edges=[])
    return repo


@pytest.fixture
def search_repo() -> MagicMock:
    """Mock search repository with async hybrid_search."""
    repo = MagicMock()
    repo.vector_search.return_value = []
    repo.hybrid_search = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def indexing_repo() -> MagicMock:
    """Mock indexing repository."""
    repo = MagicMock()
    repo.get_chunks_by_ids_batch.return_value = []
    repo.get_source_filenames_by_ids.return_value = {}
    return repo


@pytest.fixture
def source_storage() -> MagicMock:
    """Mock source storage for citation lookups."""
    storage = MagicMock()
    storage.get_citations_batch.return_value = []
    return storage


@pytest.fixture
def embedding_callback() -> AsyncMock:
    """Async embedding callback returning a simple vector."""
    cb = AsyncMock()
    cb.return_value = SimpleNamespace(embedding=[0.1, 0.2, 0.3])
    return cb


def _make_handler(
    graph_repo: MagicMock,
    search_repo: MagicMock,
    settings: EngineSettings,
    indexing_repo: MagicMock | None = None,
    source_storage: MagicMock | None = None,
    embedding_callback: AsyncMock | None = None,
) -> GraphRAGToolHandlers:
    """Construct a ``GraphRAGToolHandlers`` instance from mocks.

    Args:
        graph_repo: Mock graph repository.
        search_repo: Mock search repository.
        settings: Engine settings.
        indexing_repo: Optional mock indexing repository.
        source_storage: Optional mock source storage.
        embedding_callback: Optional async embedding callback.

    Returns:
        Configured handler ready for testing.

    """
    return GraphRAGToolHandlers(
        graph_repository=graph_repo,
        search_repository=search_repo,
        indexing_repository=indexing_repo,
        source_storage=source_storage,
        embedding_callback=embedding_callback,
        settings=settings,
        database_name="default",
    )


# ===========================================================================
# Degradation Tests
# ===========================================================================


class TestDegradationPaths:
    """Tests for graceful degradation of the GraphRAG pipeline."""

    @pytest.mark.asyncio
    async def test_no_entities_falls_back_to_vector_search(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        source_storage: MagicMock,
        embedding_callback: AsyncMock,
        settings: EngineSettings,
    ) -> None:
        """Vector search returns only chunk: IDs, no node seeds — degrades to vector_only.

        When vector_search returns results with 'chunk:' prefixes only, the
        seed matching step finds no entities. Without seeds, PPR is skipped
        and the mode must be 'vector_only' (embedding succeeded but no graph
        traversal occurred).
        """
        # Vector search returns chunk results only — no node-ID results
        search_repo.vector_search.return_value = [
            ("chunk:abc123", 0.9),
            ("chunk:def456", 0.8),
        ]

        # hybrid_search also returns chunks for vector path
        chunk = make_chunk("abc123")
        indexing_repo.get_chunks_by_ids_batch.side_effect = batch_lookup(chunk)
        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:abc123", 0.9)])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            indexing_repo=indexing_repo,
            source_storage=source_storage,
            embedding_callback=embedding_callback,
        )

        result = await handler.graphrag_search("test query")

        assert result["retrieval_stats"]["mode"] == "vector_only"
        assert result["retrieval_stats"]["seed_entities_found"] == 0
        assert result["graph_context"] == {}
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_entities_no_citations_returns_graph_context_only(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        source_storage: MagicMock,
        embedding_callback: AsyncMock,
        settings: EngineSettings,
    ) -> None:
        """Seeds found, PPR runs, but citations are empty.

        Graph context should be populated, chunks should come from vector
        search only (no provenance chunks since citations are empty).
        Mode should be 'full_graphrag' because PPR ran successfully.
        """
        node_a = make_node("n1", "Alice")
        node_b = make_node("n2", "Bob")

        # Seed match: n1 is above threshold (default 0.5)
        search_repo.vector_search.return_value = [("n1", 0.9)]

        # Graph returns nodes and edges — configure both standard and minimal variants
        _configure_graph_repo(
            graph_repo,
            nodes=[node_a, node_b],
            edges=[make_edge("e1", "n1", "n2")],
        )

        # No citations → no provenance chunks
        source_storage.get_citations_batch.return_value = []

        # Vector search returns one chunk
        chunk = make_chunk("chunk_v1", content="Vector chunk content.")
        indexing_repo.get_chunks_by_ids_batch.side_effect = batch_lookup(chunk)
        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:chunk_v1", 0.85)])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            indexing_repo=indexing_repo,
            source_storage=source_storage,
            embedding_callback=embedding_callback,
        )

        result = await handler.graphrag_search("test query")

        assert result["retrieval_stats"]["mode"] == "full_graphrag"
        assert result["retrieval_stats"]["provenance_chunks"] == 0
        assert result["graph_context"] != {}
        assert "seed_entities" in result["graph_context"]
        # Chunks came from vector only
        assert result["retrieval_stats"]["vector_chunks"] >= 1

    @pytest.mark.asyncio
    async def test_full_pipeline_returns_both(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        source_storage: MagicMock,
        embedding_callback: AsyncMock,
        settings: EngineSettings,
    ) -> None:
        """All components return data — expects full_graphrag mode.

        When embedding succeeds, seeds are found, PPR runs, citations exist,
        and vector search also returns chunks, the pipeline should assemble
        all components and return 'full_graphrag' mode.
        """
        node_a = make_node("n1", "Alice", template_id="person")
        node_b = make_node("n2", "Bob", template_id="person")

        search_repo.vector_search.return_value = [("n1", 0.95)]
        _configure_graph_repo(
            graph_repo,
            nodes=[node_a, node_b],
            edges=[make_edge("e1", "n1", "n2")],
        )

        # Provenance chunk via citation
        prov_chunk = make_chunk("prov_chunk_1", content="Provenance content.", source_id="src1")
        source_storage.get_citations_batch.return_value = [
            {"chunk_id": "prov_chunk_1", "entity_uri": "n1"}
        ]

        # Vector chunk (different chunk)
        vec_chunk = make_chunk("vec_chunk_1", content="Vector content.", source_id="src1")

        indexing_repo.get_chunks_by_ids_batch.side_effect = batch_lookup(prov_chunk, vec_chunk)
        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:vec_chunk_1", 0.8)])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            indexing_repo=indexing_repo,
            source_storage=source_storage,
            embedding_callback=embedding_callback,
        )

        result = await handler.graphrag_search("test query")

        assert result["retrieval_stats"]["mode"] == "full_graphrag"
        assert result["graph_context"] != {}
        assert "seed_entities" in result["graph_context"]
        assert "relationships" in result["graph_context"]
        assert len(result["chunks"]) > 0
        assert result["success"] is True


# ===========================================================================
# Pipeline Mechanics Tests
# ===========================================================================


class TestPipelineMechanics:
    """Tests verifying internal pipeline behaviour and correct data flow."""

    @pytest.mark.asyncio
    async def test_seed_entities_filtered_to_nodes_only(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """chunk: prefixed results must be excluded from PPR seed candidates.

        Entries such as 'chunk:abc' returned by vector_search are document
        chunk references and must not be treated as graph-node seeds.
        """
        # Mix of chunk IDs and a real node ID — only the node should seed PPR
        search_repo.vector_search.return_value = [
            ("chunk:will_be_filtered", 0.95),
            ("chunk:also_filtered", 0.90),
            ("real_node_id", 0.85),
        ]

        node = make_node("real_node_id", "RealNode")
        _configure_graph_repo(graph_repo, nodes=[node], edges=[])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            embedding_callback=AsyncMock(return_value=SimpleNamespace(embedding=[0.1, 0.2, 0.3])),
        )

        result = await handler.graphrag_search("test query")

        # Only one real node seed should have been found
        assert result["retrieval_stats"]["seed_entities_found"] == 1
        assert result["retrieval_stats"]["mode"] == "full_graphrag"

    @pytest.mark.asyncio
    async def test_ppr_called_with_seed_similarities(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Personalization dict passed to PPR must match seed similarity scores.

        The score returned by vector_search for a seed node should be used
        directly as the personalization weight for that node in PPR.
        """
        # Two seeds with distinct scores
        search_repo.vector_search.return_value = [
            ("node_alpha", 0.9),
            ("node_beta", 0.6),
        ]

        node_alpha = make_node("node_alpha", "Alpha")
        node_beta = make_node("node_beta", "Beta")
        _configure_graph_repo(
            graph_repo,
            nodes=[node_alpha, node_beta],
            edges=[make_edge("e1", "node_alpha", "node_beta")],
        )

        captured_personalization: dict[str, Any] = {}

        original_calculate = (
            "chaoscypher_core.services.workflows.tools.engine.handlers"
            ".graphrag_handlers.calculate_pagerank"
        )

        def capture_pagerank(nodes: Any, edges: Any, **kwargs: Any) -> dict[str, Any]:
            captured_personalization.update(kwargs.get("personalization", {}))
            return {"pagerank_scores": {"node_alpha": 0.9, "node_beta": 0.1}}

        with patch(original_calculate, side_effect=capture_pagerank):
            handler = _make_handler(
                graph_repo,
                search_repo,
                settings,
                embedding_callback=AsyncMock(
                    return_value=SimpleNamespace(embedding=[0.1, 0.2, 0.3])
                ),
            )
            await handler.graphrag_search("test query")

        assert captured_personalization.get("node_alpha") == pytest.approx(0.9)
        assert captured_personalization.get("node_beta") == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_chunks_deduplicated_across_sources(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        source_storage: MagicMock,
        embedding_callback: AsyncMock,
        settings: EngineSettings,
    ) -> None:
        """A chunk appearing in both provenance and vector results has origin 'both'.

        When the same chunk_id appears in the provenance list (via citations)
        and the vector search list, the merged output must contain it only once
        and its ``retrieval_origin`` must be ``'both'``.
        """
        shared_chunk_id = "shared_chunk"
        shared_chunk = make_chunk(shared_chunk_id, content="Shared content.", source_id="src1")

        node_a = make_node("n1", "Alice")
        search_repo.vector_search.return_value = [("n1", 0.9)]
        _configure_graph_repo(graph_repo, nodes=[node_a], edges=[])

        # Citation points to the shared chunk
        source_storage.get_citations_batch.return_value = [
            {"chunk_id": shared_chunk_id, "entity_uri": "n1"}
        ]

        # Vector search also returns the same chunk
        indexing_repo.get_chunks_by_ids_batch.side_effect = batch_lookup(shared_chunk)
        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:" + shared_chunk_id, 0.85)])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            indexing_repo=indexing_repo,
            source_storage=source_storage,
            embedding_callback=embedding_callback,
        )

        result = await handler.graphrag_search("test query")

        chunks = result["chunks"]
        # Only one copy of the shared chunk should be present
        chunk_ids = [c["chunk_id"] for c in chunks]
        assert chunk_ids.count(shared_chunk_id) == 1

        # That single copy should carry retrieval_origin == "both"
        shared = next(c for c in chunks if c["chunk_id"] == shared_chunk_id)
        assert shared["retrieval_origin"] == "both"

    @pytest.mark.asyncio
    async def test_graph_context_summary_generated(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
        settings: EngineSettings,
    ) -> None:
        """Graph context summary must contain entity labels and relationship labels.

        The human-readable 'summary' field should surface at least the node
        labels and the edge label connecting them.
        """
        node_a = make_node("n1", "Alice", template_id="person")
        node_b = make_node("n2", "Bob", template_id="person")
        edge = make_edge("e1", "n1", "n2", label="knows")

        search_repo.vector_search.return_value = [("n1", 0.95)]
        _configure_graph_repo(graph_repo, nodes=[node_a, node_b], edges=[edge])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            embedding_callback=embedding_callback,
        )

        result = await handler.graphrag_search("test query")

        summary = result["graph_context"].get("summary", "")
        assert "Alice" in summary
        assert "Bob" in summary
        assert "knows" in summary

    @pytest.mark.asyncio
    async def test_source_ids_scope_applied(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        source_storage: MagicMock,
        embedding_callback: AsyncMock,
        settings: EngineSettings,
    ) -> None:
        """source_ids filter must be forwarded to all downstream retrieval calls.

        When ``source_ids`` is passed, citations, vector search results, and
        chunk hydration should all be scoped to those sources only.
        """
        node_a = make_node("n1", "Alice")
        # node_a has no source_id (passes scope filter for graph nodes)

        search_repo.vector_search.return_value = [("n1", 0.9)]
        _configure_graph_repo(graph_repo, nodes=[node_a], edges=[])

        # Citation chunk belongs to the scoped source
        source_storage.get_citations_batch.return_value = [
            {"chunk_id": "scoped_chunk", "entity_uri": "n1"}
        ]
        scoped_chunk = make_chunk("scoped_chunk", source_id="allowed_src")
        indexing_repo.get_chunks_by_ids_batch.side_effect = batch_lookup(scoped_chunk)
        search_repo.hybrid_search = AsyncMock(return_value=[])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            indexing_repo=indexing_repo,
            source_storage=source_storage,
            embedding_callback=embedding_callback,
        )

        await handler.graphrag_search("test query", source_ids=["allowed_src"])

        # Verify citations were fetched with the source filter
        source_storage.get_citations_batch.assert_called_once()
        call_args = source_storage.get_citations_batch.call_args
        # The call signature is (database_name, entity_uris=..., source_ids=...)
        passed_source_ids = call_args.kwargs.get("source_ids") or (
            call_args.args[2] if len(call_args.args) >= 3 else None
        )
        assert passed_source_ids == ["allowed_src"]

        # The PPR graph load must push the scope into SQL: a Python filter
        # after the SQL LIMIT hands scoped chats an arbitrary slice once the
        # graph exceeds max_graph_nodes (and loads the whole cap regardless).
        assert graph_repo.list_nodes_minimal.call_args.kwargs.get("source_ids") == ["allowed_src"]
        assert graph_repo.list_edges_minimal.call_args.kwargs.get("source_ids") == ["allowed_src"]


# ===========================================================================
# Edge Case Tests
# ===========================================================================


class TestEdgeCases:
    """Tests for failure modes and boundary conditions."""

    @pytest.mark.asyncio
    async def test_embedding_failure_degrades_to_keyword(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Embedding callback raising an exception causes keyword_only fallback.

        If the embedding step fails, the pipeline should not crash. Instead
        it should set mode to 'keyword_only' and still attempt vector/hybrid
        search (which may also fall back internally).
        """
        failing_callback = AsyncMock(side_effect=RuntimeError("embedding service unavailable"))

        # No hybrid results to simplify the test
        search_repo.hybrid_search = AsyncMock(return_value=[])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            embedding_callback=failing_callback,
        )

        result = await handler.graphrag_search("test query")

        assert result["retrieval_stats"]["mode"] == "keyword_only"
        assert result["retrieval_stats"]["seed_entities_found"] == 0
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_empty_graph_skips_ppr(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
        settings: EngineSettings,
    ) -> None:
        """When the graph has no nodes, PPR returns empty and the pipeline degrades.

        Even though a seed entity ID is found by vector_search, if
        ``list_nodes_minimal`` (the preferred variant) returns no nodes, PPR
        returns an empty dict and the mode falls back to 'vector_only'.
        """
        # Seed entity found by vector search
        search_repo.vector_search.return_value = [("n1", 0.9)]

        # Graph is empty — both variants return no nodes
        _configure_graph_repo(graph_repo, nodes=[], edges=[])
        # hybrid_search returns nothing for simplicity
        search_repo.hybrid_search = AsyncMock(return_value=[])

        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            embedding_callback=embedding_callback,
        )

        result = await handler.graphrag_search("test query")

        # PPR should not have produced graph context
        assert result["graph_context"] == {}
        # Mode: seeds were found but PPR produced no entities → vector_only
        assert result["retrieval_stats"]["mode"] == "vector_only"
        assert result["success"] is True


# ===========================================================================
# Type labels in the graph context
# ===========================================================================


def test_type_label_prefers_entity_type_over_generic_template(
    graph_repo: MagicMock, search_repo: MagicMock, settings: EngineSettings
) -> None:
    """Extraction files most nodes under one system template ("Item") and keeps
    the specific type on ``entity_type``; the summary must say "(Package)",
    not "(Item)". A node without an entity type still gets its template name.
    """
    handler = _make_handler(graph_repo, search_repo, settings)
    graph_repo.get_template.side_effect = lambda tid: SimpleNamespace(
        name={"tpl_item": "Item", "tpl_person": "Person"}[tid]
    )
    valkey = make_node("n1", "Valkey", template_id="tpl_item")
    valkey.entity_type = "Package"
    alice = make_node("n2", "Alice", template_id="tpl_person")
    alice.entity_type = None
    loose = make_node("n3", "Loose", template_id=None)
    loose.entity_type = None

    labels = handler._resolve_template_names([valkey, alice, loose])

    assert labels == {"n1": "Package", "n2": "Person", "n3": ""}


# ===========================================================================
# The hybrid reserve in the merge, and the public hybrid arm
# ===========================================================================


def _hydrated(chunk_id: str, origin: str, score: float = 1.0) -> dict[str, Any]:
    """A chunk as the retrieval steps hand it to the merge (header line included)."""
    return {
        "chunk_id": chunk_id,
        "chunk_alias": "C0",
        "content": f"[CHUNK C0 | book.txt]\n[S1] Text of {chunk_id}.",
        "original_content": f"Text of {chunk_id}.",
        "source_id": "src1",
        "score": score,
        "retrieval_origin": origin,
    }


class TestMergeReserve:
    """``graphrag.vector_reserve`` keeps the hybrid arm's best hits in the budget."""

    @staticmethod
    def _handler(reserve: int) -> GraphRAGToolHandlers:
        settings = EngineSettings()
        settings.graphrag.vector_reserve = reserve
        return _make_handler(MagicMock(), MagicMock(), settings)

    def test_reserve_swaps_the_lowest_provenance_chunks_for_the_next_hybrid_hits(self) -> None:
        """Twelve provenance and eight hybrid chunks, budget 10, reserve 6.

        Rank fusion keeps five hybrid hits; the sixth replaces the lowest
        provenance-only chunk, the kept order is untouched, nothing repeats,
        and the aliases run C0..C9 in the final order.
        """
        provenance = [_hydrated(f"p{i}", "provenance") for i in range(12)]
        vector = [_hydrated(f"v{i}", "vector", 0.9 - i / 100) for i in range(8)]

        merged = self._handler(6)._merge_and_rank([], provenance, vector, limit=10)

        ids = [c["chunk_id"] for c in merged]
        assert ids == ["p0", "v0", "p1", "v1", "p2", "v2", "p3", "v3", "v4", "v5"]
        assert len(set(ids)) == 10
        assert sum(c["retrieval_origin"] == "vector" for c in merged) == 6
        assert [c["chunk_alias"] for c in merged] == [f"C{i}" for i in range(10)]
        assert all(c["content"].startswith(f"[CHUNK {c['chunk_alias']} |") for c in merged)
        assert all("rrf_score" in c for c in merged)

    def test_zero_reserve_is_the_old_merge(self) -> None:
        """With no reserve the result is the fused top ten, hybrid hits or not."""
        provenance = [_hydrated(f"p{i}", "provenance") for i in range(12)]
        vector = [_hydrated(f"v{i}", "vector") for i in range(8)]

        merged = self._handler(0)._merge_and_rank([], provenance, vector, limit=10)

        assert [c["chunk_id"] for c in merged] == [
            "p0", "v0", "p1", "v1", "p2", "v2", "p3", "v3", "p4", "v4"
        ]  # fmt: skip

    def test_reserve_is_capped_by_the_hits_available(self) -> None:
        """One hybrid hit and a reserve of four: nothing is swapped for a hit that does not exist."""
        provenance = [_hydrated(f"p{i}", "provenance") for i in range(12)]
        vector = [_hydrated("v0", "vector")]

        merged = self._handler(4)._merge_and_rank([], provenance, vector, limit=10)

        assert [c["chunk_id"] for c in merged] == ["p0", "v0", *(f"p{i}" for i in range(1, 9))]

    def test_a_chunk_in_both_arms_counts_toward_the_reserve(self) -> None:
        """A provenance chunk the hybrid arm also found is a hybrid hit; no swap is forced."""
        provenance = [_hydrated(f"p{i}", "provenance") for i in range(10)]
        vector = [_hydrated("p0", "vector"), _hydrated("v0", "vector")]

        merged = self._handler(2)._merge_and_rank([], provenance, vector, limit=5)

        assert [c["chunk_id"] for c in merged] == ["p0", "p1", "v0", "p2", "p3"]
        assert merged[0]["retrieval_origin"] == "both"

    def test_reserve_is_by_hybrid_rank_not_by_count(self) -> None:
        """Reserve 8: the hybrid arm's fifth to eighth hits come in, replacing the lowest provenance.

        Fusion already keeps five hybrid-origin chunks, so a count-based
        reserve of eight would add three arbitrary hits; the guarantee is
        that v0..v7 are all present (the facts plain search found sat at
        those ranks on Book One, 2026-10-01).
        """
        provenance = [_hydrated(f"p{i}", "provenance") for i in range(12)]
        vector = [_hydrated(f"v{i}", "vector", 0.9 - i / 100) for i in range(8)]

        merged = self._handler(8)._merge_and_rank([], provenance, vector, limit=10)

        ids = [c["chunk_id"] for c in merged]
        assert ids == ["p0", "v0", "p1", "v1", "v2", "v3", "v4", "v5", "v6", "v7"]
        assert len(set(ids)) == 10

    def test_a_chunk_both_arms_found_is_never_dropped(self) -> None:
        """Only provenance-only chunks give way; a chunk the hybrid arm also found stays."""
        provenance = [_hydrated(f"p{i}", "provenance") for i in range(6)]
        vector = [_hydrated("p5", "vector"), *(_hydrated(f"v{i}", "vector") for i in range(4))]

        merged = self._handler(5)._merge_and_rank([], provenance, vector, limit=5)

        ids = [c["chunk_id"] for c in merged]
        assert "p5" in ids and set(ids) >= {"v0", "v1", "v2", "v3"}
        assert len(set(ids)) == 5

    def test_the_default_reserve_is_the_whole_hybrid_top_ten(self) -> None:
        """The shipped default: the hybrid arm's top ten of ten (Phase 2 panel and holdout, 2026-10-05; eight before)."""
        assert EngineSettings().graphrag.vector_reserve == 10  # the Phase 2 union shape, 2026-10-05


class TestEvidenceArm:
    """The evidence arm: the chunks the graph block cites enter the budget by right, never the reserve's victim."""

    @staticmethod
    def _handler(
        evidence: int, reserve: int = 0, indexing: MagicMock | None = None
    ) -> GraphRAGToolHandlers:
        settings = EngineSettings()
        settings.graphrag.evidence_chunks = evidence
        settings.graphrag.vector_reserve = reserve
        settings.graphrag.rank_chunks_by_seeds = False
        return _make_handler(MagicMock(), MagicMock(), settings, indexing_repo=indexing)

    @staticmethod
    def _indexing(*ids: str) -> MagicMock:
        indexing = MagicMock()
        indexing.get_chunks_by_ids_batch.side_effect = batch_lookup(
            *(make_chunk(i, content=f"Text of {i}.") for i in ids)
        )
        indexing.get_source_filenames_by_ids.side_effect = lambda ids, _db: dict.fromkeys(
            ids, "book.txt"
        )
        return indexing

    def test_an_evidence_chunk_enters_by_right_and_is_never_the_victim(self) -> None:
        """The missing reserved hybrid hit evicts the provenance chunk, not the evidence one."""
        kept = [
            _hydrated("p1", "provenance"),
            _hydrated("v1", "vector"),
            _hydrated("e1", "evidence"),
        ]
        rest = [_hydrated("v2", "vector")]
        out = GraphRAGToolHandlers._reserve_hybrid_hits(
            kept, rest, ["v1", "v2"], 2, evidence_ids=("e1",)
        )
        assert [c["chunk_id"] for c in out] == ["v1", "e1", "v2"]  # p1 evicted, e1 kept

    def test_with_no_victim_the_evidence_keeps_the_slot(self) -> None:
        """Every kept chunk protected: the reserved hybrid hit stays out (the pipeline counts it)."""
        kept = [_hydrated("e1", "evidence"), _hydrated("v1", "vector")]
        out = GraphRAGToolHandlers._reserve_hybrid_hits(
            kept, [_hydrated("v2", "vector")], ["v1", "v2"], 2, evidence_ids=("e1",)
        )
        assert [c["chunk_id"] for c in out] == ["e1", "v1"]

    def test_without_evidence_ids_the_evidence_chunk_would_be_the_victim(self) -> None:
        """The same case under the two-arm rule: the unprotected evidence chunk gives way."""
        kept = [_hydrated("e1", "evidence"), _hydrated("v1", "vector")]
        out = GraphRAGToolHandlers._reserve_hybrid_hits(
            kept, [_hydrated("v2", "vector")], ["v1", "v2"], 2
        )
        assert [c["chunk_id"] for c in out] == ["v1", "v2"]

    @pytest.mark.asyncio
    async def test_the_evidence_arm_is_empty_at_lever_zero_and_capped_at_the_lever(self) -> None:
        """Lever 0: nothing retrieved, storage not asked; lever 1: the first cited chunk only."""
        off = self._handler(0, indexing=self._indexing("c2", "c5"))
        assert await off._retrieve_evidence_chunks(["c2", "c5"], None) == []
        off.indexing.get_chunks_by_ids_batch.assert_not_called()  # type: ignore[union-attr]

        on = self._handler(1, indexing=self._indexing("c2", "c5"))
        [chunk] = await on._retrieve_evidence_chunks(["c2", "c5"], None)
        assert (chunk["chunk_id"], chunk["retrieval_origin"], chunk["score"]) == (
            "c2",
            "evidence",
            1.0,
        )
        assert "[CHUNK C0 | book.txt]\n[S1] Text of c2." in chunk["content"]
        on.indexing.get_chunks_by_ids_batch.assert_called_once_with(["c2"])  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_an_out_of_scope_evidence_chunk_is_left_out(self) -> None:
        """A cited chunk of another source does not enter a scoped retrieval."""
        indexing = MagicMock()
        indexing.get_chunks_by_ids_batch.side_effect = batch_lookup(
            make_chunk("c2", source_id="s1"), make_chunk("c5", source_id="s2")
        )
        indexing.get_source_filenames_by_ids.return_value = {}
        h = self._handler(2, indexing=indexing)
        out = await h._retrieve_evidence_chunks(["c2", "c5"], ["s1"])
        assert [c["chunk_id"] for c in out] == ["c2"]

    @pytest.mark.asyncio
    async def test_an_unreadable_evidence_arm_is_a_fault(self) -> None:
        """Storage failing on the cited chunks records ``evidence_chunks`` and the arm is empty."""
        from chaoscypher_core.utils.retrieval_faults import collect_retrieval_faults

        indexing = MagicMock()
        indexing.get_chunks_by_ids_batch.side_effect = RuntimeError("gone")
        h = self._handler(3, indexing=indexing)
        with collect_retrieval_faults() as faults:
            assert await h._retrieve_evidence_chunks(["c2"], None) == []
        assert list(faults) == ["evidence_chunks"]

    @pytest.mark.asyncio
    async def test_a_cited_chunk_in_the_hybrid_reserve_costs_no_slot_and_is_both(self) -> None:
        """Review focus 3: the hybrid arm's own copy joins the arm, unhydrated; the slot goes to the next.

        Lever 1, cited ``v0`` (the hybrid arm's best hit) then ``e1`` then
        ``e2``: ``v0`` is in the reserve, so it costs no slot and ``e1``
        takes the one slot; ``v0`` is not duplicated and is ``both``.
        """
        h = self._handler(1, reserve=2, indexing=self._indexing("e1", "e2"))
        vector = [_hydrated("v0", "vector", 0.9), _hydrated("v1", "vector", 0.8)]
        context: dict[str, Any] = {
            "aggregates": [{"groups": [{"rows": [{"chunk_id": "v0"}, {"chunk_id": "e1"}]}]}],
            "walks": [{"hops": [{"chunk_id": "e2"}]}],
        }

        arm = await h._evidence_arm(context, vector, limit=4, source_ids=None)

        assert [c["chunk_id"] for c in arm] == ["v0", "e1"]
        assert arm[0] is vector[0]  # the hybrid arm's chunk, not a second hydration
        h.indexing.get_chunks_by_ids_batch.assert_called_once_with(["e1"])  # type: ignore[union-attr]
        merged = h._merge_and_rank(arm, [], vector, limit=4)
        assert [c["chunk_id"] for c in merged] == ["v0", "e1", "v1"]
        assert [c["retrieval_origin"] for c in merged] == ["both", "evidence", "vector"]

    @pytest.mark.asyncio
    async def test_the_evidence_arm_reads_aggregate_rows_then_walk_hops(self) -> None:
        """Block order: the aggregate rows' chunks first, then the walk hops', each once."""
        h = self._handler(3, indexing=self._indexing("a1", "w1", "w2"))
        context = {
            "aggregates": [{"groups": [{"rows": [{"chunk_id": "a1"}, {"chunk_id": None}]}]}],
            "walks": [
                {"hops": [{"chunk_id": "w1"}, {"chunk_id": "a1"}]},
                {"hops": [{"chunk_id": "w2"}]},
            ],
        }
        arm = await h._evidence_arm(context, [], limit=10, source_ids=None)
        assert [c["chunk_id"] for c in arm] == ["a1", "w1", "w2"]

    def test_evidence_past_the_fused_cut_enters_by_right(self) -> None:
        """Four evidence chunks, budget 4: all of them, though fusion interleaves the provenance arm."""
        evidence = [_hydrated(f"e{i}", "evidence") for i in range(4)]
        provenance = [_hydrated(f"p{i}", "provenance") for i in range(6)]

        merged = self._handler(4)._merge_and_rank(evidence, provenance, [], limit=4)

        assert [c["chunk_id"] for c in merged] == ["e0", "e1", "e2", "e3"]
        assert [c["chunk_alias"] for c in merged] == ["C0", "C1", "C2", "C3"]

    def test_evidence_ranks_ahead_and_keeps_the_fused_order_otherwise(self) -> None:
        """One evidence chunk: first in the fused order (its arm comes first), the rest unchanged."""
        evidence = [_hydrated("e0", "evidence")]
        provenance = [_hydrated(f"p{i}", "provenance") for i in range(3)]
        vector = [_hydrated(f"v{i}", "vector") for i in range(3)]

        merged = self._handler(1)._merge_and_rank(evidence, provenance, vector, limit=4)

        assert [c["chunk_id"] for c in merged] == ["e0", "p0", "v0", "p1"]

    def test_an_evidence_chunk_another_arm_found_is_both_and_once(self) -> None:
        """An evidence chunk the provenance arm also cites appears once, ``both``, the evidence copy kept."""
        evidence = [_hydrated("p1", "evidence", 1.0)]
        provenance = [_hydrated("p0", "provenance"), _hydrated("p1", "provenance", 0.5)]

        merged = self._handler(1)._merge_and_rank(evidence, provenance, [], limit=3)

        assert [c["chunk_id"] for c in merged] == ["p1", "p0"]
        assert merged[0]["retrieval_origin"] == "both"
        assert merged[0]["score"] == 1.0  # first wins: the evidence arm's copy

    def test_the_reserve_evicts_around_the_evidence_and_counts_the_shortfall(self) -> None:
        """Budget 3, reserve 3, one evidence chunk: two hybrid hits and the evidence, one hit short.

        The provenance chunk is the only victim; the third reserved hit has
        none (both other kept chunks are protected) and stays out.
        """
        evidence = [_hydrated("e0", "evidence")]
        provenance = [_hydrated("p0", "provenance")]
        vector = [_hydrated(f"v{i}", "vector") for i in range(3)]

        merged = self._handler(1, reserve=3)._merge_and_rank(evidence, provenance, vector, limit=3)

        assert [c["chunk_id"] for c in merged] == ["e0", "v0", "v1"]
        assert GraphRAGToolHandlers._reserve_shortfall(merged, vector, 3) == 1


class TestHybridChunkSearch:
    """``hybrid_chunk_search``: the hybrid arm alone, as a public method."""

    @pytest.mark.asyncio
    async def test_returns_the_arms_top_chunks_with_aliases(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        settings: EngineSettings,
    ) -> None:
        """Entity hits are dropped, the list is cut to the limit, aliases run from C0."""
        chunks = [make_chunk(f"c{i}", content=f"Chunk {i}.") for i in range(3)]
        indexing_repo.get_chunks_by_ids_batch.side_effect = batch_lookup(*chunks)
        search_repo.hybrid_search = AsyncMock(
            return_value=[("n1", 0.95), ("chunk:c0", 0.9), ("chunk:c1", 0.8), ("chunk:c2", 0.7)]
        )
        handler = _make_handler(
            graph_repo,
            search_repo,
            settings,
            indexing_repo=indexing_repo,
            embedding_callback=embedding_callback,
        )

        result = await handler.hybrid_chunk_search("test query", limit=2)

        assert [c["chunk_id"] for c in result] == ["c0", "c1"]
        assert [c["chunk_alias"] for c in result] == ["C0", "C1"]
        assert [c["retrieval_origin"] for c in result] == ["vector", "vector"]
        assert [c["score"] for c in result] == [0.9, 0.8]
        assert "[CHUNK C1 |" in result[1]["content"]
        search_repo.hybrid_search.assert_awaited_once()
        assert search_repo.hybrid_search.await_args.kwargs["k"] == 4
