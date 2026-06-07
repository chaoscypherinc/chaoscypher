# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ChunkStorageProtocol for ChaosCypher storage.

Split from the original SourceStorageProtocol god-protocol (Phase 1 Task 12).
Covers DocumentChunk table operations.
Binds to SourceChunksMixin in the SQLite adapter.

Note: IndexingProtocol (ports/index.py) is a narrow read/update view of the
same mixin used by the indexing pipeline.  ChunkStorageProtocol is the full
CRUD surface.  Both coexist without conflict — IndexingProtocol methods are
a strict subset of SourceChunksMixin's implementation.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ChunkStorageProtocol(Protocol):
    """Storage protocol for document chunk operations.

    Handles CRUD for DocumentChunk records.  DocumentChunk is an independent
    table keyed by (source_id, chunk_index) and can be read/written without
    touching the SourceRow or citation tables.
    """

    def create_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        """Create document chunk."""
        ...

    def get_chunk(self, chunk_id: str, database_name: str) -> dict[str, Any] | None:
        """Get chunk by ID."""
        ...

    def list_chunks(
        self,
        database_name: str,
        source_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        include_content: bool = False,
    ) -> list[dict[str, Any]]:
        """List chunks with optional filters.

        Default False: content and chunk_metadata columns excluded for
        lightweight list views. Pass include_content=True when the caller
        needs the chunk text (chunk-detail views, re-extraction internals).
        """
        ...

    def update_chunk(self, chunk_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update chunk."""
        ...

    def delete_chunks_for_source(self, source_id: str) -> None:
        """Delete all chunks owned by a source. Called by the cascade orchestrator."""
        ...

    def get_chunks_by_source(
        self,
        source_id: str,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
        include_embeddings: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get all chunks for a source with pagination.

        Args:
            source_id: Source UUID
            page: Page number (1-indexed)
            page_size: Items per page
            status: Filter by status (staged/indexed/committed)
            include_embeddings: If True, include embedding data (slower, for export)

        Returns:
            Tuple of (chunks list, total count)

        """
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations — PR2a Task 1.
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_chunks(self) -> int:
        """Count every DocumentChunk row across all databases.

        Returns:
            Non-negative integer count.
        """
        ...

    def count_staged_chunks(self, *, database_name: str) -> int:
        """Count staged (``source_id IS NULL``) chunks in one database.

        Staged chunks are those not yet linked to a persisted ``SourceRow``.
        Matches the legacy reset-code definition of "staged".

        Args:
            database_name: Database to scope the count to.

        Returns:
            Non-negative integer count.
        """
        ...

    def clear_all_chunks(self) -> int:
        """Delete every DocumentChunk row across all databases.

        Returns:
            Number of rows deleted.
        """
        ...

    def delete_staged_chunks(self, *, database_name: str) -> int:
        """Delete staged (``source_id IS NULL``) chunks in one database.

        Args:
            database_name: Database to scope the delete to.

        Returns:
            Number of rows deleted.
        """
        ...
