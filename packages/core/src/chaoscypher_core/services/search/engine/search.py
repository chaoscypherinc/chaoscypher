# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Service for chaoscypher-engine.

Business logic for search operations with node and chunk hydration.
"""

import base64
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import structlog

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.models import SourceStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.index import IndexingProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.ports.storage_sources import SourceStorageProtocol
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


class SearchService:
    """Service for search business logic with node and chunk hydration.

    Handles keyword, semantic, and hybrid search across both:
    - Graph nodes (entities in the knowledge graph)
    - Document chunks (RAG indexed documents)
    """

    def __init__(
        self,
        search_repository: SearchRepositoryProtocol,
        graph_repository: GraphRepositoryProtocol,
        indexing_repository: IndexingProtocol,
        source_repository: SourceStorageProtocol,
        sources_repository: SourceStorageProtocol | None = None,
        settings: EngineSettings | None = None,
        default_embedding_callback: Any = None,
    ):
        """Initialize search service.

        Args:
            search_repository: SearchRepository implementation
            graph_repository: GraphRepository implementation (for node hydration)
            indexing_repository: IndexingProtocol implementation (for chunk hydration)
            source_repository: SourceStorageProtocol implementation (for source metadata)
            sources_repository: SourceStorageProtocol for checking source enabled status
            settings: Engine settings for database name and batching configuration
            default_embedding_callback: Default callback for generating query
                embeddings in semantic/hybrid search. When provided, callers
                don't need to pass ``embedding_provider_callback`` explicitly.

        """
        self.search_repository = search_repository
        self.graph_repository = graph_repository
        self.indexing_repository = indexing_repository
        self.source_repository = source_repository
        self.sources_repository = sources_repository
        self.settings = settings
        self.database_name = settings.current_database if settings else "default"
        self._default_embedding_callback = default_embedding_callback

    @classmethod
    def from_engine(cls, engine: Any) -> SearchService:
        """Create a SearchService wired from an Engine instance.

        Args:
            engine: Engine instance with search_repository, graph_repository,
                storage_adapter, and settings.

        Returns:
            SearchService with all dependencies injected.

        """
        # Use Engine's embedding callback if available
        callback = getattr(engine, "_default_embed_callback", None)
        return cls(
            search_repository=engine.search_repository,
            graph_repository=engine.graph_repository,
            indexing_repository=engine.storage_adapter,
            source_repository=engine.storage_adapter,
            sources_repository=engine.storage_adapter,
            settings=engine.settings,
            default_embedding_callback=callback,
        )

    @classmethod
    def from_adapter(
        cls,
        adapter: SqliteAdapter,
        settings: EngineSettings,
        *,
        search_repository: SearchRepositoryProtocol,
        graph_repository: GraphRepositoryProtocol | None = None,
        default_embedding_callback: Any = None,
    ) -> SearchService:
        """Create a SearchService from a storage adapter.

        Wires the adapter into protocol slots (IndexingProtocol,
        SourceStorageProtocol) that it already implements.  Graph repository
        is created from the adapter session if not provided.

        Args:
            adapter: SqliteAdapter (or compatible) implementing
                IndexingProtocol and SourceStorageProtocol.
            settings: Engine settings.
            search_repository: SearchRepository instance (required — needs
                SQLAlchemy engine that the adapter doesn't expose directly).
            graph_repository: Optional GraphRepository override.
            default_embedding_callback: Optional embedding callback for
                semantic search queries.

        Returns:
            Configured SearchService.

        Raises:
            OperationError: If ``graph_repository`` is not provided and
                ``adapter.session`` is None (i.e. ``adapter.connect()`` has
                not been called).

        Example:
            from chaoscypher_core import SearchService, SqliteAdapter, EngineSettings
            from chaoscypher_core.adapters.sqlite.repos import SearchRepository

            adapter = SqliteAdapter("app.db", "default")
            search_repo = SearchRepository(db_engine, 1024, "model-name")
            service = SearchService.from_adapter(
                adapter, EngineSettings(), search_repository=search_repo,
            )

        """
        if graph_repository is None:
            from chaoscypher_core.adapters.sqlite.repos import GraphRepository

            if adapter.session is None:
                msg = "SqliteAdapter.session is None — call adapter.connect() first."
                raise OperationError(msg, operation="connect")
            graph_repository = GraphRepository(adapter.session, settings.current_database)
        return cls(
            search_repository=search_repository,
            graph_repository=graph_repository,
            indexing_repository=adapter,
            # SqliteAdapter satisfies SourceStorageProtocol structurally via
            # its sources mixin; mypy can't see that the adapter mixin's
            # get_source matches the protocol exactly.
            source_repository=cast("SourceStorageProtocol", adapter),
            sources_repository=adapter,
            settings=settings,
            default_embedding_callback=default_embedding_callback,
        )

    def _get_enabled_source_ids(self) -> set[str]:
        """Get IDs of all enabled sources.

        Returns:
            Set of source IDs that are enabled. Empty set if sources_repository not available.

        """
        if not self.sources_repository:
            return set()  # No filtering if repository not available

        sources_list, _ = self.sources_repository.list_sources(
            enabled="enabled",
            page=1,
            page_size=self.settings.batching.chunk_fetch_limit if self.settings else 10000,
        )
        return {s["id"] for s in sources_list}

    def _hydrate_nodes(
        self, node_ids: list[str], enabled_source_ids: set[str] | None
    ) -> dict[str, Any]:
        """Fetch nodes in batch and filter by enabled sources.

        Args:
            node_ids: List of node IDs to fetch.
            enabled_source_ids: Set of enabled source IDs for filtering, or None to skip.

        Returns:
            Dict mapping node ID to node object (filtered).

        """
        if not node_ids:
            return {}

        nodes_dict: dict[str, Any] = {}
        nodes = self.graph_repository.get_nodes_batch(node_ids)
        for node in nodes:
            if enabled_source_ids is not None:
                source_id = node.properties.get("source_document_id") if node.properties else None
                if source_id is not None and source_id not in enabled_source_ids:
                    logger.debug(
                        "node_filtered_disabled_source",
                        node_id=node.id,
                        source_id=source_id,
                    )
                    continue
            nodes_dict[node.id] = node
        return nodes_dict

    def _hydrate_chunks(
        self, chunk_ids: list[str], enabled_source_ids: set[str] | None
    ) -> dict[str, dict[str, Any]]:
        """Fetch chunks, enrich with source metadata, and filter by enabled sources.

        Args:
            chunk_ids: List of chunk UUIDs to fetch.
            enabled_source_ids: Set of enabled source IDs for filtering, or None to skip.

        Returns:
            Dict mapping chunk UUID to enriched chunk data (filtered).

        """
        if not chunk_ids:
            return {}

        chunks_dict: dict[str, dict[str, Any]] = {}
        for chunk_id in chunk_ids:
            chunk_data = self.indexing_repository.get_chunk_by_id(chunk_id)
            if not chunk_data:
                continue

            source_id = chunk_data.get("source_id")
            database_name = chunk_data.get("database_name")

            source: dict[str, Any] | None = None
            if source_id and database_name:
                source = self.source_repository.get_source(source_id, database_name)
            else:
                logger.warning(
                    "chunk_missing_source_reference",
                    chunk_id=chunk_id,
                    source_id=source_id,
                    database_name=database_name,
                    hint="Chunk may need re-import to link to source",
                )

            if (
                enabled_source_ids is not None
                and source_id is not None
                and source_id not in enabled_source_ids
            ):
                logger.debug(
                    "chunk_filtered_disabled_source",
                    chunk_id=chunk_id,
                    source_id=source_id,
                )
                continue

            chunks_dict[chunk_id] = {
                **chunk_data,
                "filename": source["filename"] if source else "Unknown",
                "source_id": source_id,
            }
        return chunks_dict

    def _build_search_results(
        self,
        results: list[tuple[str, float]],
        search_type: str,
        include_disabled_sources: bool = False,
    ) -> dict[str, Any]:
        """Build standardized search results from IDs and scores.

        Handles both graph nodes and document chunks (distinguished by "chunk:" prefix).

        Args:
            results: List of (id, score) tuples (can be node_id or "chunk:uuid")
            search_type: Type of search performed (keyword, semantic, hybrid)
            include_disabled_sources: If False, filters out results from disabled sources

        Returns:
            Dictionary with keys:
                - data: List of search result dicts (with 'node' or 'chunk', 'score', 'result_type')
                - type: Search type

        """
        # Get enabled source IDs for filtering
        enabled_source_ids: set[str] | None = None
        if not include_disabled_sources and self.sources_repository:
            enabled_source_ids = self._get_enabled_source_ids()

        # Separate node IDs from chunk IDs
        node_ids = []
        chunk_ids = []
        for result_id, _ in results:
            if result_id.startswith("chunk:"):
                chunk_ids.append(result_id[6:])
            else:
                node_ids.append(result_id)

        logger.info(
            "search_results_built",
            total_results=len(results),
            node_count=len(node_ids),
            chunk_count=len(chunk_ids),
            search_type=search_type,
        )

        # Hydrate nodes and chunks
        nodes_dict = self._hydrate_nodes(node_ids, enabled_source_ids)
        chunks_dict = self._hydrate_chunks(chunk_ids, enabled_source_ids)

        # Per-node edge counts so the omnibar / search results can show real
        # connection numbers instead of a hardcoded 0. Computed for the
        # filtered set so disabled-source nodes drop out cleanly.
        node_edge_counts: dict[str, int] = (
            self.graph_repository.count_edges_per_node(list(nodes_dict.keys()))
            if nodes_dict
            else {}
        )

        # Build mixed results list preserving original score order
        search_results = []
        for result_id, score in results:
            if result_id.startswith("chunk:"):
                chunk_uuid = result_id[6:]
                if chunk_uuid in chunks_dict:
                    chunk_data = chunks_dict[chunk_uuid]
                    search_results.append(
                        {
                            "chunk": {
                                "chunk_id": chunk_data["id"],
                                "source_id": chunk_data.get("source_id"),
                                "chunk_index": chunk_data["chunk_index"],
                                "content": chunk_data["content"],
                                "page_number": chunk_data.get("page_number"),
                                "section": chunk_data.get("section"),
                                "filename": chunk_data["filename"],
                            },
                            "score": score,
                            "result_type": "chunk",
                        }
                    )
            elif result_id in nodes_dict:
                node = nodes_dict[result_id]
                search_results.append(
                    {
                        "node": {
                            "id": node.id,
                            "template_id": node.template_id,
                            "label": node.label,
                            "properties": node.properties,
                            "position": node.position,
                            "embedding": node.embedding,
                            "created_at": node.created_at,
                            "updated_at": node.updated_at,
                            "edge_count": node_edge_counts.get(node.id, 0),
                        },
                        "score": score,
                        "result_type": "node",
                    }
                )

        return {"data": search_results, "type": search_type}

    def keyword_search(
        self, query: str, limit: int = 10, include_disabled_sources: bool = False
    ) -> dict[str, Any]:
        """Perform keyword search.

        Args:
            query: Search query
            limit: Maximum results
            include_disabled_sources: If True, include results from disabled sources

        Returns:
            Dict with 'data' (list of results) and 'type' (search type)

        """
        results = self.search_repository.keyword_search(query, limit=limit)
        return self._build_search_results(results, "keyword", include_disabled_sources)

    async def semantic_search(
        self,
        query: str,
        limit: int = 10,
        embedding_provider_callback: Any = None,
        include_disabled_sources: bool = False,
    ) -> dict[str, Any]:
        """Perform semantic/vector search.

        Args:
            query: Search query
            limit: Maximum results
            embedding_provider_callback: Optional callback for generating query embedding.
                Falls back to the default callback injected at construction time.
            include_disabled_sources: If True, include results from disabled sources

        Returns:
            Dict with 'data' (list of results) and 'type' (search type)

        """
        callback = embedding_provider_callback or self._default_embedding_callback
        results = await self.search_repository.semantic_search(
            query, k=limit, embedding_provider_callback=callback
        )
        return self._build_search_results(results, "semantic", include_disabled_sources)

    async def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        embedding_provider_callback: Any = None,
        min_similarity: float = 0.55,
        include_disabled_sources: bool = False,
    ) -> dict[str, Any]:
        """Perform hybrid search (semantic with keyword fallback).

        Args:
            query: Search query
            limit: Maximum results
            embedding_provider_callback: Optional callback for generating query embedding.
                Falls back to the default callback injected at construction time.
            min_similarity: Minimum similarity score to consider a result relevant
            include_disabled_sources: If True, include results from disabled sources

        Returns:
            Dict with 'data' (list of results) and 'type' (search type)

        """
        callback = embedding_provider_callback or self._default_embedding_callback
        results = await self.search_repository.hybrid_search(
            query,
            k=limit,
            embedding_provider_callback=callback,
            min_similarity=min_similarity,
        )
        return self._build_search_results(results, "hybrid", include_disabled_sources)

    def get_stats(self) -> dict[str, Any]:
        """Get search index statistics.

        Returns:
            Dict with index stats (nodes_indexed, chunks_indexed, etc.)

        """
        stats: dict[str, Any] = self.search_repository.get_index_stats()
        return stats

    def rebuild_indexes(self) -> dict[str, Any]:
        """Rebuild keyword, vector, and chunk search indexes.

        Rebuilds graph node indexes (FTS + vector) and re-indexes all
        committed document chunk embeddings into the vector search index.

        Returns:
            Dict with rebuild stats for nodes and chunks.

        """
        logger.info("search_rebuild_started")

        # --- Phase 1: Rebuild graph node indexes (including disabled sources) ---
        all_nodes = self.graph_repository.list_nodes(
            limit=self.settings.batching.chunk_fetch_limit if self.settings else 100000,
            include_disabled_sources=True,
        )
        logger.info("search_rebuild_nodes_found", node_count=len(all_nodes))

        nodes_with_embeddings = sum(1 for node in all_nodes if node.embedding)
        self.search_repository.reindex_all_nodes(all_nodes)

        logger.info(
            "search_rebuild_nodes_completed",
            total_nodes=len(all_nodes),
            nodes_with_embeddings=nodes_with_embeddings,
        )

        # --- Phase 2: Rebuild chunk vector index ---
        chunks_indexed = self._rebuild_chunk_vector_index()

        message = (
            f"Rebuilt indexes: {len(all_nodes)} nodes "
            f"({nodes_with_embeddings} with embeddings), "
            f"{chunks_indexed} chunk embeddings"
        )
        logger.info("search_rebuild_completed", message=message)

        return {
            "success": True,
            "total_nodes": len(all_nodes),
            "nodes_with_embeddings": nodes_with_embeddings,
            "chunks_indexed": chunks_indexed,
            "message": message,
        }

    async def rebuild_with_regeneration(
        self,
        indexing_service: Any = None,
    ) -> dict[str, Any]:
        """Regenerate all embeddings and rebuild search indexes.

        When the embedding model or dimensions have changed, stored
        embeddings are stale. This method re-runs the indexing pipeline
        for each committed source to generate fresh embeddings with
        the current model, then rebuilds the vector search index.

        Args:
            indexing_service: IndexingService for regenerating chunk
                embeddings. Required for regeneration.

        Returns:
            Dict with success, sources_regenerated, total_nodes,
            nodes_with_embeddings, chunks_indexed, message.

        """
        sources_regenerated = 0
        regeneration_errors = 0

        # Phase 1: Regenerate chunk embeddings if indexing_service is available
        if indexing_service and self.sources_repository:
            sources_list, _ = self.sources_repository.list_sources(
                status=SourceStatus.COMMITTED,
                page=1,
                page_size=(self.settings.batching.chunk_fetch_limit if self.settings else 10000),
            )

            for source in sources_list:
                source_id = source["id"]
                try:
                    await indexing_service.create_index(source_id)
                    sources_regenerated += 1
                    logger.info(
                        "rebuild_source_reembedded",
                        source_id=source_id,
                        filename=source.get("filename"),
                    )
                except Exception as e:
                    regeneration_errors += 1
                    logger.warning(
                        "rebuild_source_reembed_failed",
                        source_id=source_id,
                        error=str(e),
                    )

        # Phase 1.5: Regenerate node embeddings if an embedding callback is available
        nodes_reembedded = 0
        if indexing_service and self.graph_repository:
            embedding_callback = getattr(indexing_service, "embedding_service", None)
            if embedding_callback:
                all_nodes = self.graph_repository.list_nodes(
                    limit=self.settings.batching.chunk_fetch_limit if self.settings else 100000,
                    include_disabled_sources=True,
                    # Re-embedding regenerates the vector from label + properties,
                    # so the existing embedding is never read — skip loading it.
                    include_embedding=False,
                )
                for node in all_nodes:
                    try:
                        # Build text from node label + properties
                        text_parts = [node.label]
                        text_parts.extend(
                            v for v in (node.properties or {}).values() if isinstance(v, str)
                        )
                        text = " ".join(text_parts)

                        result = await embedding_callback.embed(text)
                        from chaoscypher_core.models import NodeUpdate

                        self.graph_repository.update_node(
                            node.id, NodeUpdate(embedding=result.embedding)
                        )
                        nodes_reembedded += 1
                    except Exception as e:
                        failed_node_id = node.id
                        logger.warning(
                            "rebuild_node_reembed_failed",
                            node_id=failed_node_id,
                            error=str(e),
                        )
                if nodes_reembedded:
                    logger.info(
                        "rebuild_nodes_reembedded",
                        nodes_reembedded=nodes_reembedded,
                        total_nodes=len(all_nodes),
                    )

        # Phase 2: Rebuild per-type vec0 indexes from freshly generated embeddings
        rebuild_result = self.rebuild_indexes()

        # Phase 3: Clear the reindex flag (in-memory + persisted)
        if hasattr(self.search_repository, "clear_reindex_flag"):
            self.search_repository.clear_reindex_flag()

        return {
            "success": rebuild_result.get("success", True),
            "regenerated": True,
            "sources_regenerated": sources_regenerated,
            "regeneration_errors": regeneration_errors,
            "total_nodes": rebuild_result.get("total_nodes", 0),
            "nodes_with_embeddings": rebuild_result.get("nodes_with_embeddings", 0),
            "chunks_indexed": rebuild_result.get("chunks_indexed", 0),
            "message": (
                f"Regenerated embeddings for {sources_regenerated} sources, "
                f"indexed {rebuild_result.get('chunks_indexed', 0)} chunks"
            ),
        }

    def _rebuild_chunk_vector_index(self) -> int:
        """Re-index all committed chunk embeddings into the vector search index.

        Loads all committed sources, fetches their chunks with embeddings,
        and batch-indexes them into the vector store.

        Returns:
            Total number of chunk embeddings indexed.

        """
        if not self.sources_repository:
            logger.warning("rebuild_chunks_skipped_no_sources_repository")
            return 0

        # Get all committed sources
        sources_list, _ = self.sources_repository.list_sources(
            status="committed",
            page=1,
            page_size=self.settings.batching.chunk_fetch_limit if self.settings else 10000,
        )
        if not sources_list:
            logger.info("rebuild_chunks_no_committed_sources")
            return 0

        total_indexed = 0
        total_skipped = 0

        for source in sources_list:
            source_id = source["id"]
            result = self.indexing_repository.get_chunks_by_source(
                source_id,
                page=1,
                page_size=self.settings.batching.chunk_fetch_limit if self.settings else 100000,
                include_embeddings=True,
            )
            chunks = result[0] if isinstance(result, tuple) else result

            embeddings_to_index: list[tuple[str, list[float]]] = []
            text_lookup: dict[str, str] = {}
            for chunk in chunks:
                raw_embedding = chunk.get("embedding")
                if not raw_embedding:
                    total_skipped += 1
                    continue

                try:
                    embedding_bytes = base64.b64decode(raw_embedding)
                    embedding_array = np.frombuffer(embedding_bytes, dtype=np.float32)
                    chunk_id = f"chunk:{chunk['id']}"
                    embeddings_to_index.append((chunk_id, embedding_array.tolist()))

                    # Store text for re-embedding on dimension mismatch
                    chunk_text = chunk.get("content", "")
                    if chunk_text:
                        text_lookup[chunk_id] = chunk_text
                except Exception:
                    logger.warning(
                        "rebuild_chunk_embedding_decode_failed",
                        chunk_id=chunk.get("id"),
                    )
                    total_skipped += 1

            if embeddings_to_index:
                count = self.search_repository.index_embeddings_batch(
                    embeddings_to_index,
                    item_type="chunk",
                    text_lookup=text_lookup,
                )
                total_indexed += count

            logger.info(
                "rebuild_chunks_source_indexed",
                source_id=source_id,
                filename=source.get("filename"),
                chunks_indexed=len(embeddings_to_index),
                chunks_skipped=total_skipped,
            )

        logger.info(
            "rebuild_chunks_completed",
            total_indexed=total_indexed,
            total_skipped=total_skipped,
        )
        return total_indexed
