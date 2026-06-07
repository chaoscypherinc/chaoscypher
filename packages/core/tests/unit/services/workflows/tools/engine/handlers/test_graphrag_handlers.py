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


def make_chunk(
    chunk_id: str,
    content: str = "Test content.",
    source_id: str = "src1",
) -> dict[str, Any]:
    """Create a minimal chunk dict matching the storage protocol output.

    Args:
        chunk_id: Chunk identifier.
        content: Text content of the chunk.
        source_id: Owning source identifier.

    Returns:
        A dict matching the shape returned by indexing storage protocols.

    """
    return {
        "id": chunk_id,
        "content": content,
        "source_id": source_id,
        "database_name": "default",
        "chunk_index": 0,
        "page_number": 1,
        "section": None,
        "chunk_metadata": None,
    }


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
    repo.get_chunk_by_id.return_value = None
    repo.get_source.return_value = None
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
        indexing_repo.get_chunk_by_id.return_value = chunk
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
        indexing_repo.get_chunk_by_id.return_value = chunk
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

        def get_chunk_side_effect(chunk_id: str) -> dict[str, Any] | None:
            if chunk_id == "prov_chunk_1":
                return prov_chunk
            if chunk_id == "vec_chunk_1":
                return vec_chunk
            return None

        indexing_repo.get_chunk_by_id.side_effect = get_chunk_side_effect
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
        indexing_repo.get_chunk_by_id.return_value = shared_chunk
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
        indexing_repo.get_chunk_by_id.return_value = scoped_chunk
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
