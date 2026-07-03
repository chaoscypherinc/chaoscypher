# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral unit tests for services/search/engine/search.py.

Covers the hydration helpers (``_hydrate_nodes`` / ``_hydrate_chunks``), the
result-assembly path (``_build_search_results``), the three public search
entrypoints (keyword/semantic/hybrid) including embedding-callback fallback,
stats / rebuild orchestration, the chunk vector re-index decode path, and the
full regeneration loop.

All repositories are MagicMock/AsyncMock; settings use a real ``EngineSettings()``.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from chaoscypher_core import EngineSettings
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.search.engine.search import SearchService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_search_service(**kwargs: Any) -> SearchService:
    """Return a SearchService with minimal fake dependencies.

    Copied from test_search_engine_exceptions.py (sibling-import prohibited),
    extended to default-inject a real EngineSettings and a sources_repository.
    """
    search_repo = MagicMock()
    graph_repo = MagicMock()
    indexing_repo = MagicMock()
    source_repo = MagicMock()
    kwargs.setdefault("settings", EngineSettings())
    kwargs.setdefault("sources_repository", source_repo)
    return SearchService(
        search_repository=search_repo,
        graph_repository=graph_repo,
        indexing_repository=indexing_repo,
        source_repository=source_repo,
        **kwargs,
    )


def _node(
    node_id: str,
    *,
    source_id: str | None = None,
    source_document_id: str | None = None,
) -> SimpleNamespace:
    """Build a lightweight node stand-in matching the attributes search.py reads."""
    props: dict[str, Any] = {}
    if source_document_id is not None:
        props["source_document_id"] = source_document_id
    return SimpleNamespace(
        id=node_id,
        template_id="tmpl",
        label=f"label-{node_id}",
        properties=props,
        source_id=source_id,
        position=None,
        embedding=None,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


def _chunk(
    chunk_id: str, *, source_id: str | None = "src-1", database_name: str | None = "default"
):
    """Build a chunk data dict matching get_chunk_by_id's return contract."""
    return {
        "id": chunk_id,
        "chunk_index": 0,
        "content": f"content-{chunk_id}",
        "page_number": 1,
        "section": "intro",
        "source_id": source_id,
        "database_name": database_name,
    }


def _enabled_sources(*ids: str) -> list[dict[str, str]]:
    return [{"id": i} for i in ids]


# ---------------------------------------------------------------------------
# _get_enabled_source_ids
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEnabledSourceIds:
    def test_returns_empty_when_no_sources_repository(self) -> None:
        svc = _make_search_service(sources_repository=None)
        assert svc._get_enabled_source_ids() == set()

    def test_returns_enabled_ids(self) -> None:
        svc = _make_search_service()
        svc.sources_repository.list_sources.return_value = (
            _enabled_sources("a", "b"),
            2,
        )
        assert svc._get_enabled_source_ids() == {"a", "b"}


# ---------------------------------------------------------------------------
# _hydrate_nodes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHydrateNodes:
    def test_empty_short_circuit(self) -> None:
        svc = _make_search_service()
        assert svc._hydrate_nodes([], None) == {}
        svc.graph_repository.get_nodes_batch.assert_not_called()

    def test_no_filter_returns_all(self) -> None:
        svc = _make_search_service()
        svc.graph_repository.get_nodes_batch.return_value = [
            _node("n1"),
            _node("n2"),
        ]
        result = svc._hydrate_nodes(["n1", "n2"], None)
        assert set(result.keys()) == {"n1", "n2"}

    def test_filters_disabled_source(self) -> None:
        svc = _make_search_service()
        svc.graph_repository.get_nodes_batch.return_value = [
            _node("n1", source_document_id="enabled"),
            _node("n2", source_document_id="disabled"),
        ]
        result = svc._hydrate_nodes(["n1", "n2"], {"enabled"})
        assert set(result.keys()) == {"n1"}

    def test_node_without_source_passes_filter(self) -> None:
        svc = _make_search_service()
        # source_document_id None → not filtered out
        svc.graph_repository.get_nodes_batch.return_value = [_node("n1")]
        result = svc._hydrate_nodes(["n1"], {"enabled"})
        assert "n1" in result

    def test_imported_node_filters_by_source_id_column_not_property(self) -> None:
        """Imported nodes filter by the canonical source_id column, not the property.

        An imported node's ``properties.source_document_id`` holds the ORIGINAL
        export-machine source id (stale); its ``source_id`` column is re-pointed
        to the local imported source. The filter must use the column, or every
        imported node is wrongly dropped from search even when its source is on.
        """
        svc = _make_search_service()
        svc.graph_repository.get_nodes_batch.return_value = [
            # column → enabled local source; property → stale original id.
            _node("n1", source_id="local-enabled", source_document_id="stale-original"),
            # column → a NOT-enabled local source: still correctly filtered.
            _node("n2", source_id="local-disabled", source_document_id="stale-original"),
        ]
        result = svc._hydrate_nodes(["n1", "n2"], {"local-enabled"})
        assert set(result.keys()) == {"n1"}


# ---------------------------------------------------------------------------
# _hydrate_chunks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHydrateChunks:
    def test_empty_short_circuit(self) -> None:
        svc = _make_search_service()
        assert svc._hydrate_chunks([], None) == {}

    def test_enriches_filename_from_source(self) -> None:
        svc = _make_search_service()
        svc.indexing_repository.get_chunk_by_id.return_value = _chunk("c1")
        svc.source_repository.get_source.return_value = {"filename": "doc.pdf"}
        result = svc._hydrate_chunks(["c1"], None)
        assert result["c1"]["filename"] == "doc.pdf"

    def test_missing_chunk_skipped(self) -> None:
        svc = _make_search_service()
        svc.indexing_repository.get_chunk_by_id.return_value = None
        assert svc._hydrate_chunks(["c1"], None) == {}

    def test_missing_source_reference_uses_unknown(self) -> None:
        svc = _make_search_service()
        # No source_id / database_name → warning branch, filename "Unknown"
        svc.indexing_repository.get_chunk_by_id.return_value = _chunk(
            "c1", source_id=None, database_name=None
        )
        result = svc._hydrate_chunks(["c1"], None)
        assert result["c1"]["filename"] == "Unknown"
        svc.source_repository.get_source.assert_not_called()

    def test_filters_disabled_source(self) -> None:
        svc = _make_search_service()
        svc.indexing_repository.get_chunk_by_id.return_value = _chunk("c1", source_id="disabled")
        svc.source_repository.get_source.return_value = {"filename": "doc.pdf"}
        result = svc._hydrate_chunks(["c1"], {"enabled"})
        assert result == {}


# ---------------------------------------------------------------------------
# _build_search_results
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSearchResults:
    def test_mixed_nodes_and_chunks_preserve_score_order(self) -> None:
        svc = _make_search_service()
        # Disable source filtering for simplicity
        svc.graph_repository.get_nodes_batch.return_value = [_node("n1")]
        svc.graph_repository.count_edges_per_node.return_value = {"n1": 3}
        svc.indexing_repository.get_chunk_by_id.return_value = _chunk("cuid")
        svc.source_repository.get_source.return_value = {"filename": "f.pdf"}

        results = [
            ("chunk:cuid", 0.9),
            ("n1", 0.8),
        ]
        out = svc._build_search_results(results, "keyword", include_disabled_sources=True)
        assert out["type"] == "keyword"
        assert [r["result_type"] for r in out["data"]] == ["chunk", "node"]
        assert out["data"][0]["chunk"]["filename"] == "f.pdf"
        assert out["data"][1]["node"]["edge_count"] == 3

    def test_filters_disabled_sources_when_flag_false(self) -> None:
        svc = _make_search_service()
        svc.sources_repository.list_sources.return_value = (_enabled_sources("enabled"), 1)
        svc.graph_repository.get_nodes_batch.return_value = [
            _node("n1", source_document_id="disabled"),
        ]
        svc.graph_repository.count_edges_per_node.return_value = {}
        out = svc._build_search_results([("n1", 0.5)], "keyword", include_disabled_sources=False)
        # n1 filtered out → no data
        assert out["data"] == []

    def test_node_not_hydrated_is_dropped(self) -> None:
        svc = _make_search_service()
        svc.graph_repository.get_nodes_batch.return_value = []  # nothing hydrated
        svc.graph_repository.count_edges_per_node.return_value = {}
        out = svc._build_search_results(
            [("missing", 0.5)], "keyword", include_disabled_sources=True
        )
        assert out["data"] == []


# ---------------------------------------------------------------------------
# keyword / semantic / hybrid search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchEntrypoints:
    def test_keyword_search(self) -> None:
        svc = _make_search_service()
        svc.search_repository.keyword_search.return_value = [("n1", 0.7)]
        svc.graph_repository.get_nodes_batch.return_value = [_node("n1")]
        svc.graph_repository.count_edges_per_node.return_value = {"n1": 0}
        out = svc.keyword_search("hello", limit=5, include_disabled_sources=True)
        svc.search_repository.keyword_search.assert_called_once_with("hello", limit=5)
        assert out["type"] == "keyword"
        assert len(out["data"]) == 1

    @pytest.mark.asyncio
    async def test_semantic_search_uses_explicit_callback(self) -> None:
        svc = _make_search_service()
        svc.search_repository.semantic_search = AsyncMock(return_value=[("n1", 0.6)])
        svc.graph_repository.get_nodes_batch.return_value = [_node("n1")]
        svc.graph_repository.count_edges_per_node.return_value = {}
        cb = object()
        out = await svc.semantic_search(
            "q", limit=3, embedding_provider_callback=cb, include_disabled_sources=True
        )
        _, kwargs = svc.search_repository.semantic_search.call_args
        assert kwargs["embedding_provider_callback"] is cb
        assert kwargs["k"] == 3
        assert out["type"] == "semantic"

    @pytest.mark.asyncio
    async def test_semantic_search_falls_back_to_default_callback(self) -> None:
        default_cb = object()
        svc = _make_search_service(default_embedding_callback=default_cb)
        svc.search_repository.semantic_search = AsyncMock(return_value=[])
        svc.graph_repository.count_edges_per_node.return_value = {}
        await svc.semantic_search("q", include_disabled_sources=True)
        _, kwargs = svc.search_repository.semantic_search.call_args
        assert kwargs["embedding_provider_callback"] is default_cb

    @pytest.mark.asyncio
    async def test_hybrid_search_passes_min_similarity_and_fallback(self) -> None:
        default_cb = object()
        svc = _make_search_service(default_embedding_callback=default_cb)
        svc.search_repository.hybrid_search = AsyncMock(return_value=[])
        svc.graph_repository.count_edges_per_node.return_value = {}
        out = await svc.hybrid_search(
            "q", limit=4, min_similarity=0.42, include_disabled_sources=True
        )
        _, kwargs = svc.search_repository.hybrid_search.call_args
        assert kwargs["embedding_provider_callback"] is default_cb
        assert kwargs["min_similarity"] == 0.42
        assert kwargs["k"] == 4
        assert out["type"] == "hybrid"


# ---------------------------------------------------------------------------
# get_stats / rebuild_indexes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStatsAndRebuild:
    def test_get_stats_passthrough(self) -> None:
        svc = _make_search_service()
        svc.search_repository.get_index_stats.return_value = {"nodes_indexed": 5}
        assert svc.get_stats() == {"nodes_indexed": 5}

    def test_rebuild_indexes_counts_embeddings(self) -> None:
        svc = _make_search_service()
        nodes = [_node("n1"), _node("n2")]
        nodes[0].embedding = [0.1, 0.2]  # has embedding
        nodes[1].embedding = None
        svc.graph_repository.list_nodes.return_value = nodes
        svc.search_repository.reindex_all_nodes.return_value = None
        # No committed sources → chunk phase returns 0
        svc.sources_repository.list_sources.return_value = ([], 0)

        out = svc.rebuild_indexes()
        assert out["success"] is True
        assert out["total_nodes"] == 2
        assert out["nodes_with_embeddings"] == 1
        assert out["chunks_indexed"] == 0
        svc.search_repository.reindex_all_nodes.assert_called_once_with(nodes)


# ---------------------------------------------------------------------------
# _rebuild_chunk_vector_index
# ---------------------------------------------------------------------------


def _b64_embedding(vec: list[float]) -> str:
    """Encode a float32 vector the way chunk embeddings are stored."""
    raw = np.array(vec, dtype=np.float32).tobytes()
    return base64.b64encode(raw).decode("ascii")


@pytest.mark.unit
class TestRebuildChunkVectorIndex:
    def test_skipped_when_no_sources_repository(self) -> None:
        svc = _make_search_service(sources_repository=None)
        assert svc._rebuild_chunk_vector_index() == 0

    def test_no_committed_sources_returns_zero(self) -> None:
        svc = _make_search_service()
        svc.sources_repository.list_sources.return_value = ([], 0)
        assert svc._rebuild_chunk_vector_index() == 0

    def test_decodes_and_indexes_embeddings(self) -> None:
        svc = _make_search_service()
        svc.sources_repository.list_sources.return_value = (
            [{"id": "src-1", "filename": "f.pdf"}],
            1,
        )
        chunks = [
            {"id": "c1", "content": "text one", "embedding": _b64_embedding([0.1, 0.2, 0.3])},
        ]
        svc.indexing_repository.get_chunks_by_source.return_value = (chunks, 1)
        svc.search_repository.index_embeddings_batch.return_value = 1

        total = svc._rebuild_chunk_vector_index()
        assert total == 1
        args, kwargs = svc.search_repository.index_embeddings_batch.call_args
        # first positional arg = embeddings_to_index list of (chunk_id, vector)
        embeddings = args[0]
        assert embeddings[0][0] == "chunk:c1"
        assert kwargs["item_type"] == "chunk"
        assert kwargs["text_lookup"]["chunk:c1"] == "text one"

    def test_skips_chunk_with_empty_embedding(self) -> None:
        svc = _make_search_service()
        svc.sources_repository.list_sources.return_value = (
            [{"id": "src-1"}],
            1,
        )
        chunks = [{"id": "c1", "content": "t", "embedding": None}]
        svc.indexing_repository.get_chunks_by_source.return_value = (chunks, 1)
        total = svc._rebuild_chunk_vector_index()
        assert total == 0
        svc.search_repository.index_embeddings_batch.assert_not_called()

    def test_decode_failure_is_skipped(self) -> None:
        svc = _make_search_service()
        svc.sources_repository.list_sources.return_value = (
            [{"id": "src-1"}],
            1,
        )
        # invalid base64 → b64decode raises → counted as skipped
        chunks = [{"id": "c1", "content": "t", "embedding": "!!!not-base64!!!"}]
        svc.indexing_repository.get_chunks_by_source.return_value = (chunks, 1)
        total = svc._rebuild_chunk_vector_index()
        assert total == 0
        svc.search_repository.index_embeddings_batch.assert_not_called()


# ---------------------------------------------------------------------------
# rebuild_with_regeneration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRebuildWithRegeneration:
    def _wire_clean_rebuild(self, svc: SearchService) -> None:
        """Make rebuild_indexes() return cleanly with no nodes/chunks."""
        svc.graph_repository.list_nodes.return_value = []
        svc.search_repository.reindex_all_nodes.return_value = None
        svc.sources_repository.list_sources.return_value = ([], 0)

    @pytest.mark.asyncio
    async def test_no_indexing_service_just_rebuilds(self) -> None:
        svc = _make_search_service()
        self._wire_clean_rebuild(svc)
        out = await svc.rebuild_with_regeneration(indexing_service=None)
        assert out["regenerated"] is True
        assert out["sources_regenerated"] == 0
        assert out["regeneration_errors"] == 0

    @pytest.mark.asyncio
    async def test_source_reembed_loop_counts_success_and_errors(self) -> None:
        svc = _make_search_service()

        # list_sources is called twice with different signatures:
        #  - regeneration phase: status=COMMITTED → returns 2 sources
        #  - rebuild_chunk_vector_index: status="committed" → returns []
        def list_sources_side_effect(*args, **kwargs):
            status = kwargs.get("status")
            # Regeneration phase passes the SourceStatus.COMMITTED enum member;
            # the chunk-rebuild phase passes the plain literal "committed".
            # Both compare equal (StrEnum), so distinguish by exact identity.
            if status is SourceStatus.COMMITTED:
                return ([{"id": "s1", "filename": "a"}, {"id": "s2", "filename": "b"}], 2)
            return ([], 0)

        svc.sources_repository.list_sources.side_effect = list_sources_side_effect
        svc.graph_repository.list_nodes.return_value = []
        svc.search_repository.reindex_all_nodes.return_value = None

        indexing_service = MagicMock()
        # create_index succeeds for s1, raises for s2
        indexing_service.create_index = AsyncMock(side_effect=[None, RuntimeError("boom")])
        # no embedding_service → node re-embed loop skipped
        indexing_service.embedding_service = None

        out = await svc.rebuild_with_regeneration(indexing_service=indexing_service)
        assert out["sources_regenerated"] == 1
        assert out["regeneration_errors"] == 1

    @pytest.mark.asyncio
    async def test_node_reembed_loop_runs_with_embedding_service(self) -> None:
        svc = _make_search_service()

        # Regeneration source phase returns no committed sources; node loop drives the test.
        svc.sources_repository.list_sources.return_value = ([], 0)

        node = _node("n1", source_document_id=None)
        node.properties = {"name": "Alice", "age": 30}  # only str values joined
        svc.graph_repository.list_nodes.return_value = [node]
        svc.search_repository.reindex_all_nodes.return_value = None
        svc.graph_repository.update_node.return_value = None

        indexing_service = MagicMock()
        indexing_service.create_index = AsyncMock()
        embedding_service = MagicMock()
        embedding_service.embed = AsyncMock(return_value=SimpleNamespace(embedding=[0.1, 0.2]))
        indexing_service.embedding_service = embedding_service

        out = await svc.rebuild_with_regeneration(indexing_service=indexing_service)
        # node re-embed happened → update_node called once
        svc.graph_repository.update_node.assert_called_once()
        embedding_service.embed.assert_awaited_once()
        assert out["regenerated"] is True

    @pytest.mark.asyncio
    async def test_node_reembed_error_is_counted_and_swallowed(self) -> None:
        svc = _make_search_service()
        svc.sources_repository.list_sources.return_value = ([], 0)

        node = _node("n1")
        svc.graph_repository.list_nodes.return_value = [node]
        svc.search_repository.reindex_all_nodes.return_value = None

        indexing_service = MagicMock()
        indexing_service.create_index = AsyncMock()
        embedding_service = MagicMock()
        embedding_service.embed = AsyncMock(side_effect=RuntimeError("embed fail"))
        indexing_service.embedding_service = embedding_service

        # Should not raise despite embed failure
        out = await svc.rebuild_with_regeneration(indexing_service=indexing_service)
        svc.graph_repository.update_node.assert_not_called()
        assert out["regenerated"] is True
