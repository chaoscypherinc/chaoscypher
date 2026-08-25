# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Service.

Backend wrapper for engine SearchService (VSA compliance).

Delegates all search logic to engine, converts responses to Pydantic models.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.search.engine.search import (
    SearchService as EngineSearchService,
)
from chaoscypher_cortex.features.search.models import (
    ChunkResult,
    GenerateEmbeddingsResponse,
    RebuildIndexResponse,
    SearchNodeHit,
    SearchResponse,
    SearchResult,
    SearchStatistics,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.app_config import Settings

logger = structlog.get_logger(__name__)

# Nodes per embedding wave when no Settings are wired (mirrors the default of
# ``BatchingSettings.embedding_batch_size``, which drives it when they are).
_DEFAULT_EMBED_WAVE_SIZE = 512


class SearchService:
    """Backend wrapper for engine SearchService.

    Provides VSA-compliant service layer that:
    - Delegates all search operations to chaoscypher service
    - Converts dict responses to Pydantic models for API
    - Adds backend-specific features (generate_embeddings)
    """

    def __init__(
        self,
        search_repository: SearchRepository,
        graph_repository: GraphRepository,
        indexing_repository: Any,  # SqliteAdapter (implements storage protocols)
        source_repository: Any,  # SqliteAdapter (implements SourceStorageProtocol)
        sources_repository: Any,  # SqliteAdapter (implements SourcesProtocol for enable/disable)
        settings: Settings | None = None,
    ) -> None:
        """Initialize search service.

        Args:
            search_repository: SearchRepository instance
            graph_repository: GraphRepository instance
            indexing_repository: SqliteAdapter instance (for chunk hydration)
            source_repository: SqliteAdapter instance (for source metadata)
            sources_repository: SqliteAdapter instance (for enable/disable filtering)
            settings: Application settings for LLM provider and engine configuration

        """
        self.search_repository = search_repository
        self.graph_repository = graph_repository
        self.indexing_repository = indexing_repository
        self.source_repository = source_repository
        self.sources_repository = sources_repository
        self.settings = settings

        # Build engine settings from backend settings for core service
        from chaoscypher_core.app_config.engine_factory import (
            build_engine_settings,
        )

        engine_settings = build_engine_settings(settings) if settings else None

        # Create chaoscypher service for business logic
        # Note: SqliteAdapter implements all storage protocols needed here
        self.engine_service = EngineSearchService(
            search_repository=search_repository,
            graph_repository=graph_repository,
            indexing_repository=indexing_repository,
            source_repository=source_repository,
            sources_repository=sources_repository,
            settings=engine_settings,
        )

    def _convert_to_pydantic(self, engine_response: dict) -> SearchResponse:
        """Convert engine dict response to Pydantic models.

        Args:
            engine_response: Dict with 'data' (list of results) and 'type' (search type)

        Returns:
            SearchResponse with Pydantic models

        """
        search_results = []
        for result_dict in engine_response["data"]:
            if result_dict["result_type"] == "chunk":
                chunk_data = result_dict["chunk"]
                search_results.append(
                    SearchResult(
                        chunk=ChunkResult(
                            chunk_id=chunk_data["chunk_id"],
                            source_id=chunk_data["source_id"],
                            chunk_index=chunk_data["chunk_index"],
                            content=chunk_data["content"],
                            page_number=chunk_data.get("page_number"),
                            section=chunk_data.get("section"),
                            filename=chunk_data["filename"],
                        ),
                        score=result_dict["score"],
                        result_type="chunk",
                    )
                )
            else:  # node
                node_data = result_dict["node"]
                search_results.append(
                    SearchResult(
                        node=SearchNodeHit(
                            id=node_data["id"],
                            label=node_data["label"],
                            template_id=node_data.get("template_id"),
                            edge_count=node_data.get("edge_count", 0),
                        ),
                        score=result_dict["score"],
                        result_type="node",
                    )
                )

        return SearchResponse(data=search_results, type=engine_response["type"])

    async def search(
        self,
        query: str,
        limit: int | None = None,
        search_type: str = "keyword",
    ) -> SearchResponse:
        """Dispatch across keyword / semantic / hybrid search modes.

        Handles building the embedding callback for semantic/hybrid modes
        so the API handler doesn't need to know about the embedding
        service — the route becomes a single service call.

        Args:
            query: Search query string.
            limit: Maximum results; service default applies when ``None``.
            search_type: One of ``"keyword"`` / ``"semantic"`` / ``"hybrid"``.

        Returns:
            ``SearchResponse`` with results.

        Raises:
            ValueError: If ``search_type`` is not a supported value.
        """
        if search_type == "keyword":
            return self.keyword_search(query, limit=limit)

        if search_type in ("semantic", "hybrid"):
            from chaoscypher_core.repo_factories import get_embedding_service

            embedding_service = get_embedding_service()

            async def embedding_callback(text: str) -> list[float]:
                """Embed the query string and return the raw vector."""
                result = await embedding_service.embed(text)
                return result.embedding

            if search_type == "semantic":
                return await self.semantic_search(
                    query, limit=limit, embedding_provider_callback=embedding_callback
                )

            if self.settings is None:
                msg = "SearchService.settings must be set before calling search methods"
                raise RuntimeError(msg)
            min_similarity = self.settings.search.min_similarity_threshold
            return await self.hybrid_search(
                query,
                limit=limit,
                embedding_provider_callback=embedding_callback,
                min_similarity=min_similarity,
            )

        msg = f"Invalid search type: {search_type}. Must be 'keyword', 'semantic', or 'hybrid'"
        raise ValueError(msg)

    def keyword_search(self, query: str, limit: int | None = None) -> SearchResponse:
        """Perform keyword search.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            SearchResponse with results

        """
        effective_limit = (
            limit
            if limit is not None
            else (self.settings.pagination.default_search_results if self.settings else 10)
        )
        # Delegate to chaoscypher service
        engine_response = self.engine_service.keyword_search(query, limit=effective_limit)
        # Convert dict to Pydantic models
        return self._convert_to_pydantic(engine_response)

    async def semantic_search(
        self, query: str, limit: int | None = None, embedding_provider_callback: Any = None
    ) -> SearchResponse:
        """Perform semantic/vector search.

        Args:
            query: Search query
            limit: Maximum results
            embedding_provider_callback: Optional callback for generating query embedding

        Returns:
            SearchResponse with results

        """
        effective_limit = (
            limit
            if limit is not None
            else (self.settings.pagination.default_search_results if self.settings else 10)
        )
        # Delegate to chaoscypher service
        engine_response = await self.engine_service.semantic_search(
            query, limit=effective_limit, embedding_provider_callback=embedding_provider_callback
        )
        # Convert dict to Pydantic models
        return self._convert_to_pydantic(engine_response)

    async def hybrid_search(
        self,
        query: str,
        limit: int | None = None,
        embedding_provider_callback: Any = None,
        min_similarity: float | None = None,
    ) -> SearchResponse:
        """Perform hybrid search (semantic with keyword fallback).

        Args:
            query: Search query
            limit: Maximum results
            embedding_provider_callback: Optional callback for generating query embedding
            min_similarity: Minimum similarity score to consider a result relevant.
                If ``None`` (default), resolves from
                ``settings.search.min_similarity_threshold``. Pass an explicit
                float to override per-call.

        Returns:
            SearchResponse with results

        """
        if min_similarity is None:
            from chaoscypher_core.app_config import get_settings

            min_similarity = get_settings().search.min_similarity_threshold
        effective_limit = (
            limit
            if limit is not None
            else (self.settings.pagination.default_search_results if self.settings else 10)
        )
        # Delegate to chaoscypher service
        engine_response = await self.engine_service.hybrid_search(
            query,
            limit=effective_limit,
            embedding_provider_callback=embedding_provider_callback,
            min_similarity=min_similarity,
        )
        # Convert dict to Pydantic models
        return self._convert_to_pydantic(engine_response)

    def get_stats(self) -> SearchStatistics:
        """Get search index statistics.

        Returns:
            SearchStatistics with index info

        """
        # Delegate to chaoscypher service
        stats_dict = self.engine_service.get_stats()

        # Flatten nested dict to match Pydantic model
        # Engine returns: {"fulltext": {...}, "vector": {...}}
        # Model expects: {fulltext_doc_count, vector_index_size, vector_dimension}
        fulltext_stats = stats_dict.get("fulltext", {})
        vector_stats = stats_dict.get("vector", {})

        return SearchStatistics(
            fulltext_doc_count=fulltext_stats.get("document_count", 0),
            vector_index_size=vector_stats.get("vector_count", 0),
            vector_dimension=vector_stats.get("dimensions", 0),
        )

    def rebuild_indexes(self) -> RebuildIndexResponse:
        """Rebuild both keyword and vector search indexes from all nodes.

        Returns:
            RebuildIndexResponse with rebuild stats

        """
        # Delegate to chaoscypher service
        rebuild_result = self.engine_service.rebuild_indexes()
        # Convert dict to Pydantic model
        return RebuildIndexResponse(**rebuild_result)

    async def generate_embeddings(self, trigger_service: Any = None) -> GenerateEmbeddingsResponse:
        """Generate embeddings for all nodes that don't have them.

        Args:
            trigger_service: Optional trigger service for event publishing (deprecated)

        Returns:
            GenerateEmbeddingsResponse with processing stats

        Note:
            Generates embeddings directly using the embedding provider, without
            requiring the trigger service. Updates nodes and search index in place.

        """
        logger.info("search_embedding_generation_started")

        # Query only nodes missing embeddings (SQL-filtered, skips loading
        # existing embedding vectors which can be large).
        total_nodes = self.graph_repository.count_nodes()
        nodes_without_embeddings = self.graph_repository.list_nodes_without_embeddings()
        logger.info(
            "search_embedding_nodes_filtered",
            nodes_needing_embeddings=len(nodes_without_embeddings),
            total_nodes=total_nodes,
        )

        if not nodes_without_embeddings:
            return GenerateEmbeddingsResponse(
                success=True,
                total_nodes=total_nodes,
                processed_count=0,
                message="All nodes already have embeddings",
            )

        # Generate embeddings directly using EmbeddingService
        from chaoscypher_core.repo_factories import get_embedding_service

        embedding_service = get_embedding_service()

        # Batch the run instead of one provider round trip + one write per node,
        # but slice it into waves so a failure is survivable: `batch_embed` fails
        # as a unit, so a single wave carrying every node would discard all the
        # vectors already computed. One wave = one batch_embed + one txn + one
        # index write, and completed waves stay committed (see
        # _embed_and_persist_wave).
        wave_size = (
            self.settings.batching.embedding_batch_size
            if self.settings
            else _DEFAULT_EMBED_WAVE_SIZE
        )

        processed_count = 0
        failed_count = 0
        for start in range(0, len(nodes_without_embeddings), wave_size):
            wave = nodes_without_embeddings[start : start + wave_size]
            wave_processed, wave_failed = await self._embed_and_persist_wave(
                wave, embedding_service
            )
            processed_count += wave_processed
            failed_count += wave_failed

        logger.info(
            "search_embedding_generation_completed",
            processed_count=processed_count,
            failed_count=failed_count,
            total_needing_embeddings=len(nodes_without_embeddings),
        )

        message = f"Generated embeddings for {processed_count} nodes"
        if failed_count > 0:
            message += f" ({failed_count} failed)"

        return GenerateEmbeddingsResponse(
            success=True,
            total_nodes=total_nodes,
            processed_count=processed_count,
            message=message,
        )

    async def _embed_and_persist_wave(
        self, nodes: list[Any], embedding_service: Any
    ) -> tuple[int, int]:
        """Embed one wave of nodes and persist it; returns (processed, failed).

        One ``batch_embed`` (the provider chunks it into API calls internally),
        one ``update_node_embeddings_batch`` transaction, one
        ``index_nodes_batch`` — the shape the import path uses in
        ``_reembed_nodes``.

        Failure granularity is the wave, not the node: the embedding port is
        all-or-nothing (``batch_embed`` raises on the first bad text rather
        than returning an empty vector for it — every shipped provider,
        ollama/openai/gemini/local, works this way), so the port's documented
        "empty list for failures" shape in ``embeddings`` is defensive only.
        Losing one wave is why the caller waves at all: earlier waves are
        already committed, so a transient provider error costs this slice and
        a repeated call resumes from it.
        """
        texts = [self._node_to_embedding_text(node) for node in nodes]
        try:
            batch_result = await embedding_service.batch_embed(texts)
            embeddings = batch_result.embeddings
        except Exception as e:
            # Reported, not raised, so the caller still gets the counts.
            logger.exception(
                "search_embedding_batch_failed",
                node_count=len(texts),
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return 0, len(nodes)

        if len(embeddings) != len(nodes):
            # A well-behaved provider returns one vector per text; a short list
            # leaves the tail nodes vectorless, so they are counted as failures.
            logger.warning(
                "search_embedding_batch_count_mismatch",
                requested=len(nodes),
                returned=len(embeddings),
            )

        updates: dict[str, list[float]] = {}
        embedded_nodes: list[Any] = []
        for node, embedding in zip(nodes, embeddings, strict=False):
            if not embedding:
                logger.warning(
                    "search_embedding_empty_result",
                    node_id=node.id,
                )
                continue
            # Keep the in-memory node fresh so index_nodes_batch sees the vector.
            node.embedding = embedding
            updates[node.id] = embedding
            embedded_nodes.append(node)

        if not updates:
            return 0, len(nodes)

        try:
            # One txn, not N SELECT+UPDATE+COMMIT round trips. The return is the
            # number of rows actually found, so a node deleted mid-request stays
            # counted as a failure (what update_node -> None did before).
            changed = self.graph_repository.update_node_embeddings_batch(updates)
        except Exception as e:
            logger.exception(
                "search_embedding_batch_write_failed",
                node_count=len(updates),
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return 0, len(nodes)

        confirmed = embedded_nodes
        if changed != len(updates):
            logger.warning(
                "search_embedding_node_update_failed",
                requested=len(updates),
                updated=changed,
            )
            # The batch write reports only a count, so re-read to learn which
            # rows survived: a node deleted mid-request must not be pushed into
            # the search index (what the per-node update_node -> None check did).
            found = (
                {n.id for n in self.graph_repository.get_nodes_batch(list(updates))}
                if changed
                else set()
            )
            confirmed = [node for node in embedded_nodes if node.id in found]

        if confirmed:
            # One batched index write, not one vec0 upsert per node. Best-effort
            # by contract (it logs rather than raises), and the vectors are
            # already durably persisted above.
            self.search_repository.index_nodes_batch(confirmed)

        return changed, len(nodes) - changed

    def _node_to_embedding_text(self, node: Any) -> str:
        """Convert node to text suitable for embeddings.

        Args:
            node: Node object with label and properties

        Returns:
            Text representation of the node

        """
        parts = [f"Label: {node.label}"]

        # Add properties
        for key, value in (node.properties or {}).items():
            if key not in ["embedding", "id", "created_at", "updated_at"] and value is not None:
                parts.append(f"{key}: {value}")

        return " | ".join(parts)
