# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Indexing protocol interface for chaoscypher-engine.

Defines Protocol for document chunk indexing operations.
Used by IndexingService for RAG embedding generation.
Main app implements this via an adapter that wraps its indexing repository.
"""

from typing import Any, Protocol


class IndexingProtocol(Protocol):
    """Interface for document chunk indexing operations.

    Handles storage and retrieval of document chunks with embeddings
    for RAG (Retrieval-Augmented Generation) indexing.

    Used by:
    - IndexingService: Generate and store embeddings for document chunks
    - SearchService: Retrieve chunks for search results
    """

    def get_chunks_by_source(
        self,
        source_id: str,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
        include_embeddings: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get all chunks for a source with pagination, ordered by chunk_index.

        Args:
            source_id: Source identifier
            page: Page number (1-indexed)
            page_size: Number of items per page
            status: Optional status filter
            include_embeddings: If True, include all columns (slower, for export)

        Returns:
            Tuple of (chunks list as dicts, total count).
            Chunk dicts contain keys:
                - id: Chunk UUID
                - source_id: Source ID
                - database_name: Database name
                - chunk_index: Sequential index
                - content: Text content
                - embedding: Base64-encoded embedding bytes (may be None)
                - embedding_model: Model name (may be None)
                - embedding_dimensions: Vector dimensions (may be None)
                - page_number: Optional page number
                - section: Optional section name
                - chunk_metadata: Optional metadata dict
                - status: 'staged' | 'indexed' | 'committed'
                - created_at: Creation datetime

        Notes:
            - Ordered by chunk_index for sequential processing
            - May include chunks without embeddings (status='staged')
            - Used by IndexingService to get chunks for embedding generation

        """
        ...

    def update_chunk_embedding(
        self,
        chunk_id: str,
        embedding: str,
        embedding_model: str,
        embedding_dimensions: int,
        status: str,
    ) -> None:
        """Update a chunk with its generated embedding.

        Args:
            chunk_id: Chunk UUID
            embedding: Base64-encoded embedding bytes
            embedding_model: Model name that generated the embedding
            embedding_dimensions: Vector dimensions (e.g., 1024)
            status: New status (typically 'indexed' = has embedding, not yet committed to vector search index)

        Notes:
            - Called by IndexingService after generating embeddings
            - Status progression: staged → indexed → committed
            - 'indexed' means has embedding but not yet in vector search index
            - 'committed' means indexed in sqlite-vec and searchable

        """
        ...

    def get_chunk_by_id(self, chunk_id: str) -> dict[str, Any] | None:
        """Get a single chunk by UUID with metadata.

        Args:
            chunk_id: Chunk UUID

        Returns:
            Chunk dictionary with keys, or None if not found:
                - id, source_id, database_name, chunk_index
                - content, embedding, embedding_model, embedding_dimensions
                - page_number, section, chunk_metadata, status, created_at

        Notes:
            - Used by SearchService to hydrate chunk results
            - Returns None if chunk not found

        """
        ...

    def update_chunk_status(self, source_id: str, status: str) -> int:
        """Update status for all chunks of a source.

        Args:
            source_id: Source identifier
            status: New status ('staged' | 'indexed' | 'committed' | 'rejected')

        Returns:
            Number of chunks updated

        Notes:
            - Used during commit process to mark chunks as committed
            - Status progression: staged → indexed → committed

        """
        ...

    def update_chunk_source(self, chunk_id: str, source_id: str) -> None:
        """Link a chunk to a source record (promote from staging).

        Args:
            chunk_id: Chunk UUID
            source_id: Source record ID

        Notes:
            - Called during commit to promote chunks to permanent storage

        """
        ...

    # ------------------------------------------------------------------
    # Quality-counter primitives. The structural ``_SupportsIncrement``
    # Protocol in ``services.quality.counters`` requires both methods;
    # IndexingService also reaches into the source row to record
    # silent-drop counters during embedding generation, so declaring them
    # here keeps the public contract aligned with the adapter surface.
    # ------------------------------------------------------------------

    def increment_source_counter(
        self, *, source_id: str, database_name: str, column: str, n: int
    ) -> None:
        """Atomically increment a numeric counter column on a source row.

        Best-effort: ``services.quality.counters`` swallows errors so the
        UPDATE may silently no-op for unknown sources.
        """
        ...

    def update_source_columns(
        self, *, source_id: str, database_name: str, updates: dict[str, Any]
    ) -> None:
        """Apply a partial column update to a source row.

        Used by quality-counter helpers (``mark_search_indexing_*``,
        ``set_loader_encoding``) without going through full ``update_source``.
        """
        ...
