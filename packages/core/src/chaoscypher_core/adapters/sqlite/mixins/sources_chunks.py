# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Chunks Mixin for SqliteAdapter.

Handles document chunk CRUD, batch operations, hierarchical grouping,
and embedding management.
Part of the unified SourceStorageProtocol implementation.
"""

import json
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import delete, select, update

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import DocumentChunk
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.ports.storage_chunks import ChunkStorageProtocol


logger = structlog.get_logger(__name__)


class SourceChunksMixin(SqliteMixinBase, ChunkStorageProtocol):
    """Mixin providing document chunk operations for SQLite storage.

    Implements operations for:
    - Chunk CRUD (create, get, update, delete)
    - Chunk listing with filters and pagination
    - Embedding updates
    - Source linking (promote from staging)
    - Batch operations
    - Hierarchical chunk grouping

    Note: This mixin contributes to the unified SourceStorageProtocol.
    """

    def create_chunk(self, chunk_data: dict[str, Any]) -> dict[str, Any]:
        """Create document chunk."""
        self._ensure_connected()
        chunk = DocumentChunk(**chunk_data)
        self.session.add(chunk)
        self._maybe_commit()
        self.session.refresh(chunk)
        return self._entity_to_dict(chunk)

    def get_chunk(self, chunk_id: str, database_name: str) -> dict[str, Any] | None:
        """Get chunk by ID."""
        self._ensure_connected()
        statement = select(DocumentChunk).where(
            DocumentChunk.id == chunk_id,
            DocumentChunk.database_name == database_name,
        )
        chunk = self.session.exec(statement).first()
        if chunk:
            return self._entity_to_dict(chunk)
        return None

    def get_chunk_by_id(self, chunk_id: str) -> dict[str, Any] | None:
        """Get a single chunk by UUID (database-agnostic).

        Used by SearchService to hydrate chunk results during search.
        Unlike get_chunk(), this doesn't require database_name since chunk IDs
        are globally unique UUIDs.

        Args:
            chunk_id: Chunk UUID

        Returns:
            Chunk dictionary with all metadata, or None if not found

        """
        self._ensure_connected()
        statement = select(DocumentChunk).where(DocumentChunk.id == chunk_id)
        chunk = self.session.exec(statement).first()
        if chunk:
            return self._entity_to_dict(chunk)
        return None

    def list_chunks(
        self,
        database_name: str,
        source_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        include_content: bool = False,
    ) -> list[dict[str, Any]]:
        """List chunks with optional filters.

        Uses load_only() to exclude large columns (embedding ~5KB per chunk).
        For full chunk data including content/embedding, use get_chunk()
        or pass include_content=True.

        Args:
            database_name: Database name filter.
            source_id: Optional source ID filter.
            status: Optional status filter.
            limit: Maximum number of chunks to return.
            include_content: Default False. When False, content and
                chunk_metadata columns are excluded for lightweight
                list views. Set True when the caller specifically needs
                the chunk text (chunk-detail views, re-extraction).
        """
        self._ensure_connected()
        # Build column projection based on include_content flag
        columns = [
            DocumentChunk.id,
            DocumentChunk.database_name,
            DocumentChunk.source_id,
            DocumentChunk.chunk_index,
            DocumentChunk.embedding_model,
            DocumentChunk.embedding_dimensions,
            DocumentChunk.page_number,
            DocumentChunk.section,
            DocumentChunk.group_index,
            DocumentChunk.status,
            DocumentChunk.created_at,
            # EXCLUDE always: embedding (5KB blob, never needed in list)
        ]
        if include_content:
            columns.append(DocumentChunk.content)
            columns.append(DocumentChunk.chunk_metadata)

        stmt = (
            select(DocumentChunk)
            .options(load_only(*columns))
            .where(DocumentChunk.database_name == database_name)
        )

        if source_id is not None:
            stmt = stmt.where(DocumentChunk.source_id == source_id)
        if status is not None:
            stmt = stmt.where(DocumentChunk.status == status)

        # Note: ORDER BY is expensive on large tables (~500ms for 3000+ rows in SQLite)
        # For filtered queries (by source_id), chunks are naturally ordered.
        # Remove global ordering for list performance; apply ordering in specific use cases.
        stmt = stmt.limit(limit)

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def get_chunks_by_source(
        self,
        source_id: str,
        page: int = 1,
        page_size: int = 50,
        status: str | None = None,
        include_embeddings: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get all chunks for a source with pagination.

        Uses load_only() to exclude large BLOB/JSON columns for performance.
        For full chunk data including embeddings, set include_embeddings=True.

        Args:
            source_id: Source ID to filter chunks
            page: Page number (1-indexed)
            page_size: Number of items per page
            status: Optional status filter
            include_embeddings: If True, include all columns (slower, for export)

        Returns:
            Tuple of (chunks list as dicts, total count)

        """
        self._ensure_connected()

        # Build query - optionally exclude large columns for performance
        query = select(DocumentChunk).where(
            DocumentChunk.source_id == source_id,
            DocumentChunk.database_name == self.database_name,
        )

        if not include_embeddings:
            # Use load_only() to exclude large columns not needed in list view
            # EXCLUDE: embedding (5KB BLOB!), chunk_metadata (JSON), embedding_model,
            # embedding_dimensions, database_name, source_id
            query = query.options(
                load_only(
                    DocumentChunk.id,
                    DocumentChunk.source_id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.content,
                    DocumentChunk.page_number,
                    DocumentChunk.section,
                    DocumentChunk.group_index,
                    DocumentChunk.status,
                    DocumentChunk.created_at,
                )
            )

        if status:
            query = query.where(DocumentChunk.status == status)

        # Get total count (from base query without load_only)
        base_query = select(DocumentChunk).where(
            DocumentChunk.source_id == source_id,
            DocumentChunk.database_name == self.database_name,
        )
        if status:
            base_query = base_query.where(DocumentChunk.status == status)
        count_query = select(func.count()).select_from(base_query.subquery())
        total = self.session.exec(count_query).one()

        # Apply pagination and ordering
        query = query.order_by(DocumentChunk.chunk_index)
        query = query.offset((page - 1) * page_size).limit(page_size)

        chunks = self.session.exec(query).all()
        return self._entities_to_dicts(chunks), total

    _CHUNK_SKIP_FIELDS = frozenset({"id", "database_name", "created_at", "source_id"})

    def update_chunk(self, chunk_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update chunk."""
        self._ensure_connected()
        statement = select(DocumentChunk).where(DocumentChunk.id == chunk_id)
        chunk = self.session.exec(statement).first()
        if not chunk:
            msg = "DocumentChunk"
            raise NotFoundError(msg, chunk_id)

        for key, value in updates.items():
            if key in self._CHUNK_SKIP_FIELDS:
                continue
            setattr(chunk, key, value)

        self.session.add(chunk)
        self._maybe_commit()
        self.session.refresh(chunk)
        return self._entity_to_dict(chunk)

    def update_chunk_embedding(
        self,
        chunk_id: str,
        embedding: str,
        embedding_model: str,
        embedding_dimensions: int,
        status: str,
    ) -> None:
        """Update chunk with embedding data.

        Args:
            chunk_id: Chunk ID to update
            embedding: Base64-encoded embedding string
            embedding_model: Name of the embedding model used
            embedding_dimensions: Dimension count of the embedding
            status: New status (typically 'indexed')

        """
        self._ensure_connected()
        # Use explicit query instead of session.get() to avoid identity map
        # staleness when concurrent async operations share the same session
        statement = select(DocumentChunk).where(DocumentChunk.id == chunk_id)
        chunk = self.session.exec(statement).first()
        if not chunk:
            msg = "DocumentChunk"
            raise NotFoundError(msg, chunk_id)

        # Convert base64 string to bytes for BLOB storage
        # The embedding field is bytes, but indexing passes a base64 string
        embedding_bytes = embedding.encode("utf-8") if isinstance(embedding, str) else embedding

        # Update embedding fields
        chunk.embedding = embedding_bytes
        chunk.embedding_model = embedding_model
        chunk.embedding_dimensions = embedding_dimensions
        chunk.status = status

        self.session.add(chunk)
        self._maybe_commit()

    def list_unembedded_chunks(
        self,
        *,
        source_id: str,
        database_name: str,
        after_chunk_index: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List chunks for a source whose embedding has not been persisted.

        Used by the embedding sub-stage of indexing to resume after a
        crashed worker: returns only chunks with ``embedded_at IS NULL``,
        ordered by chunk_index so embedding proceeds in the natural order.

        Supports keyset pagination so the embedding handler can process a
        multi-GB document in bounded waves instead of materializing every
        unembedded chunk (and its ``content``) in memory at once:

        - ``limit`` caps the wave size.
        - ``after_chunk_index`` is the keyset cursor — only rows with
          ``chunk_index > after_chunk_index`` are returned. Pass the last
          chunk_index of the previous wave to advance. ``chunk_index`` is
          unique per source, so the cursor guarantees forward progress and
          termination regardless of whether the just-embedded rows have been
          marked yet.

        With both arguments omitted the behaviour is unchanged (all
        unembedded rows), keeping existing callers working.

        Uses load_only() to project the minimal column set per CLAUDE.md
        SQLAlchemy performance rules.

        Args:
            source_id: The parent source ID.
            database_name: Active database name.
            after_chunk_index: Keyset cursor — return only rows past this
                chunk_index. ``None`` (default) starts from the beginning.
            limit: Maximum rows to return. ``None`` (default) returns all.

        Returns:
            List of chunk dicts (TypedDict access per the data-type
            boundary rule). Empty list means "all embedded" or "no chunks".
        """
        self._ensure_connected()
        statement = (
            select(DocumentChunk)
            .options(
                load_only(
                    DocumentChunk.id,
                    DocumentChunk.source_id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.content,
                    DocumentChunk.embedded_at,
                )
            )
            .where(
                DocumentChunk.source_id == source_id,
                DocumentChunk.database_name == database_name,
                DocumentChunk.embedded_at.is_(None),
            )
        )
        if after_chunk_index is not None:
            statement = statement.where(DocumentChunk.chunk_index > after_chunk_index)
        statement = statement.order_by(DocumentChunk.chunk_index)
        if limit is not None:
            statement = statement.limit(limit)
        rows = self.session.scalars(statement).all()
        return [
            {
                "id": c.id,
                "source_id": c.source_id,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "embedded_at": c.embedded_at,
            }
            for c in rows
        ]

    def count_unembedded_chunks(self, *, source_id: str, database_name: str) -> int:
        """Count chunks for a source whose embedding has not been persisted.

        Cheap ``COUNT(*)`` companion to :meth:`list_unembedded_chunks` used by
        the embedding handler to set the StageProgress total once up front
        before streaming chunks in bounded waves.

        Args:
            source_id: The parent source ID.
            database_name: Active database name.

        Returns:
            Number of chunks with ``embedded_at IS NULL`` for the source.
        """
        self._ensure_connected()
        statement = (
            select(func.count())
            .select_from(DocumentChunk)
            .where(
                DocumentChunk.source_id == source_id,
                DocumentChunk.database_name == database_name,
                DocumentChunk.embedded_at.is_(None),
            )
        )
        return int(self.session.scalar(statement) or 0)

    def mark_chunks_embedded(
        self,
        *,
        chunk_ids: list[str],
        embedded_at: Any,
        database_name: str,
    ) -> int:
        """Mark chunks as embedded with the given timestamp.

        Called by the embedding handler after a batch of embeddings is
        successfully persisted to the vector index. Writing embedded_at
        AFTER vector-index persistence means a crash between the two
        operations leaves chunks in the "re-embed me" state — wasted
        work, not correctness loss.

        Args:
            chunk_ids: List of chunk IDs to mark embedded.
            embedded_at: Timestamp to set.
            database_name: Active database name.

        Returns:
            Number of rows updated.
        """
        if not chunk_ids:
            return 0
        self._ensure_connected()
        statement = (
            update(DocumentChunk)
            .where(
                DocumentChunk.id.in_(chunk_ids),
                DocumentChunk.database_name == database_name,
            )
            .values(embedded_at=embedded_at)
        )
        result = self.session.execute(statement)
        self._maybe_commit()
        return result.rowcount or 0

    def update_chunk_source(self, chunk_id: str, source_id: str) -> None:
        """Link a chunk to a source record (promote from staging).

        Args:
            chunk_id: Chunk UUID
            source_id: Source record ID

        Notes:
            - Called during commit to promote chunks to permanent storage
            - Chunks start with source_id only, then get source_id

        """
        self._ensure_connected()
        statement = select(DocumentChunk).where(DocumentChunk.id == chunk_id)
        chunk = self.session.exec(statement).first()
        if not chunk:
            msg = "DocumentChunk"
            raise NotFoundError(msg, chunk_id)

        # Link chunk to source
        chunk.source_id = source_id

        self.session.add(chunk)
        self._maybe_commit()

    def update_chunk_status(self, source_id: str, status: str) -> int:
        """Update status for all chunks of an source processing file.

        Uses a single bulk UPDATE statement for optimal performance with large
        chunk counts (500+ chunks per file).

        Args:
            source_id: Source processing file identifier
            status: New status ('staged' | 'indexed' | 'committed' | 'rejected')

        Returns:
            Number of chunks updated

        Notes:
            - Used during commit process to mark chunks as committed
            - Status progression: staged -> indexed -> committed

        """
        self._ensure_connected()
        stmt = (
            update(DocumentChunk).where(DocumentChunk.source_id == source_id).values(status=status)
        )
        result = self.session.exec(stmt)
        self._maybe_commit()
        return result.rowcount

    def delete_chunks_for_source(self, source_id: str) -> None:
        """Delete DocumentChunk rows owned by this source."""
        self._ensure_connected()
        stmt = delete(DocumentChunk).where(DocumentChunk.source_id == source_id)
        self.session.execute(stmt)
        self._maybe_commit()

    def get_small_chunks(self, source_id: str) -> list[dict[str, Any]]:
        """Get all small chunks for a source (for RAG indexing).

        Returns chunks where chunk_metadata contains chunk_type='small',
        ordered by chunk_index.

        Args:
            source_id: Source identifier

        Returns:
            List of chunk dictionaries

        """
        self._ensure_connected()
        stmt = (
            select(DocumentChunk)
            .options(
                load_only(
                    DocumentChunk.id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.content,
                    DocumentChunk.embedding,
                    DocumentChunk.embedding_model,
                    DocumentChunk.embedding_dimensions,
                    DocumentChunk.status,
                )
            )
            .where(
                DocumentChunk.source_id == source_id,
                DocumentChunk.database_name == self.database_name,
            )
            .order_by(DocumentChunk.chunk_index)
        )
        chunks = list(self.session.exec(stmt).all())
        return self._entities_to_dicts(chunks)

    def store_chunks_and_groups(
        self,
        small_chunks: list[dict[str, Any]],
        hierarchical_groups: list[dict[str, Any]],
        batch_size: int = 500,
    ) -> None:
        """Store small chunks and hierarchical group metadata.

        Args:
            small_chunks: List of chunk dictionaries with keys:
                - id, source_id, database_name, chunk_index
                - content, embedding, char_start, char_end
                - chunk_metadata, status, created_at
            hierarchical_groups: List of group dictionaries with keys:
                - id, group_index, small_chunk_ids
                - combined_content, char_start, char_end, token_count
            batch_size: Number of chunks to commit per batch (default: 500).
                Batching prevents long-held database locks during large imports.

        Notes:
            - Groups are stored in chunk_metadata['hierarchical_group']
            - Chunks are stored in 'staged' status (not searchable yet)
            - No embeddings are generated (done at index time)

        """
        self._ensure_connected()

        # Delete any existing chunks for this source to handle retries idempotently.
        # Uses the same session to avoid cross-session synchronization issues.
        if small_chunks:
            source_id = small_chunks[0].get("source_id")
            if source_id:
                del_stmt = delete(DocumentChunk).where(DocumentChunk.source_id == source_id)
                del_result = self.session.exec(del_stmt)
                if del_result.rowcount > 0:
                    logger.info(
                        "store_chunks_deleted_existing",
                        source_id=source_id,
                        deleted_count=del_result.rowcount,
                    )
                self._maybe_commit()

        # Create a mapping of chunk_id -> group data for chunks that are part of groups
        chunk_to_group = {}
        for group in hierarchical_groups:
            for chunk_id in group["small_chunk_ids"]:
                chunk_to_group[chunk_id] = group

        total_chunks = len(small_chunks)

        # Store all small chunks with group metadata in batches
        for i, chunk_dict in enumerate(small_chunks):
            # Add hierarchical group metadata if this chunk is part of a group
            chunk_metadata = chunk_dict.get("chunk_metadata", {})
            if chunk_dict["id"] in chunk_to_group:
                group = chunk_to_group[chunk_dict["id"]]
                chunk_metadata["hierarchical_group"] = {
                    "id": group["id"],
                    "group_index": group["group_index"],
                    "small_chunk_ids": group["small_chunk_ids"],
                    "combined_content": group["combined_content"],
                    "char_start": group["char_start"],
                    "char_end": group["char_end"],
                }
                chunk_dict["group_index"] = group["group_index"]

            # Update chunk_metadata in the dict (ensure it's not empty)
            chunk_dict["chunk_metadata"] = chunk_metadata if chunk_metadata else None

            # Create DocumentChunk entity
            chunk = DocumentChunk(**chunk_dict)
            self.session.add(chunk)

            # Commit every batch_size rows to release database lock
            # Uses retry logic to handle concurrent access from other processes
            if (i + 1) % batch_size == 0:
                self._maybe_commit()
                logger.debug(
                    "chunk_batch_committed",
                    batch_number=(i + 1) // batch_size,
                    total_so_far=i + 1,
                    total_chunks=total_chunks,
                )

        # Commit any remaining chunks
        if total_chunks % batch_size != 0:
            self._maybe_commit()

    def get_hierarchical_groups(
        self, source_id: str, database_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Get hierarchical groups from chunks for entity extraction.

        Args:
            source_id: Source processing file ID to get groups for
            database_name: Database name filter (defaults to adapter's database_name)

        Returns:
            List of hierarchical group dictionaries

        """
        self._ensure_connected()
        if database_name is None:
            database_name = self.database_name

        logger.debug(
            "get_hierarchical_groups_query",
            source_id=source_id,
            database_name=database_name,
        )

        stmt = (
            select(DocumentChunk)
            .options(
                load_only(
                    DocumentChunk.id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.chunk_metadata,
                )
            )
            .where(
                DocumentChunk.source_id == source_id,
                DocumentChunk.database_name == database_name,
            )
            .order_by(DocumentChunk.chunk_index)
        )

        chunks_result = self.session.exec(stmt)
        chunks = list(chunks_result.all())

        logger.debug(
            "get_hierarchical_groups_chunks_found",
            chunk_count=len(chunks),
            source_id=source_id,
        )

        # Extract groups from metadata and deduplicate by group_id
        groups_by_id = {}
        for chunk in chunks:
            if not chunk.chunk_metadata:
                continue

            # Parse JSON string if needed (SQLModel sometimes returns JSON fields as strings)
            metadata = chunk.chunk_metadata
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    continue

            if not isinstance(metadata, dict):
                continue

            if "hierarchical_group" in metadata:
                group_data = metadata["hierarchical_group"]
                group_id = group_data["id"]

                # Only add each unique group once
                if group_id not in groups_by_id:
                    groups_by_id[group_id] = {
                        "id": group_data["id"],
                        "group_index": group_data["group_index"],
                        "small_chunk_ids": group_data["small_chunk_ids"],
                        "combined_content": group_data["combined_content"],
                        "char_start": group_data["char_start"],
                        "char_end": group_data["char_end"],
                    }

        # Return groups sorted by group_index
        groups = list(groups_by_id.values())
        groups.sort(key=lambda g: g["group_index"])
        return groups

    def create_dynamic_hierarchical_groups(
        self,
        source_id: str,
        database_name: str,
        group_size: int,
        group_overlap: int = 1,
    ) -> list[dict[str, Any]]:
        """Create hierarchical groups dynamically from small chunks.

        This method creates groups on-the-fly using current settings,
        ignoring any stored hierarchical_group metadata. This ensures
        extraction uses the optimal group size for current context window.

        Args:
            source_id: Source ID to get chunks for
            database_name: Database name filter
            group_size: Number of small chunks per group
            group_overlap: Overlap between consecutive groups

        Returns:
            List of dynamically created hierarchical groups

        """
        from chaoscypher_core.utils.id import generate_id

        self._ensure_connected()

        logger.info(
            "create_dynamic_groups_start",
            source_id=source_id,
            group_size=group_size,
            group_overlap=group_overlap,
        )

        # Fetch all small chunks with content
        stmt = (
            select(DocumentChunk)
            .options(
                load_only(
                    DocumentChunk.id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.content,
                )
            )
            .where(
                DocumentChunk.source_id == source_id,
                DocumentChunk.database_name == database_name,
            )
            .order_by(DocumentChunk.chunk_index)
        )

        chunks_result = self.session.exec(stmt)
        chunks = list(chunks_result.all())

        if not chunks:
            logger.warning("create_dynamic_groups_no_chunks", source_id=source_id)
            return []

        # Create groups using sliding window
        groups = []
        step = max(1, group_size - group_overlap)

        for group_idx, start_idx in enumerate(range(0, len(chunks), step)):
            group_chunks = chunks[start_idx : start_idx + group_size]

            if not group_chunks:
                break

            # Create group with combined content
            group_id = generate_id()
            small_chunk_ids = [chunk.id for chunk in group_chunks]
            combined_content = "\n\n".join(
                [chunk.content for chunk in group_chunks if chunk.content]
            )

            groups.append(
                {
                    "id": group_id,
                    "group_index": group_idx,
                    "small_chunk_ids": small_chunk_ids,
                    "combined_content": combined_content,
                    "char_start": 0,  # Not tracking char positions for dynamic groups
                    "char_end": len(combined_content),
                }
            )

        logger.info(
            "create_dynamic_groups_complete",
            source_id=source_id,
            total_chunks=len(chunks),
            groups_created=len(groups),
            group_size=group_size,
            avg_chunks_per_group=len(chunks) / len(groups) if groups else 0,
        )

        return groups

    def get_chunks_by_ids(
        self,
        chunk_ids: list[str],
        database_name: str,
    ) -> list[dict[str, Any]]:
        """Fetch multiple chunks by ID in a single query.

        Used by the chunk-extraction handler to rehydrate the chunk
        content for a hierarchical group without carrying the text in
        the queue payload (Phase 5 Task D — OP_EXTRACT_CHUNK shrink).

        Uses ``load_only()`` to project ``id``, ``chunk_index``, and
        ``content`` only — the 5 KB ``embedding`` BLOB and
        ``chunk_metadata`` JSON are excluded because the extraction
        handler does not need them to rebuild the group text.

        Ordering guarantee: results are returned ordered by
        ``chunk_index`` ascending, which matches both
        ``get_hierarchical_groups`` and
        ``create_dynamic_hierarchical_groups``. Callers that need to
        preserve the input ``chunk_ids`` ordering exactly should sort
        the result themselves using a lookup map.

        Args:
            chunk_ids: List of chunk UUIDs to fetch.
            database_name: Database scope.

        Returns:
            List of chunk dicts with keys ``id``, ``chunk_index``,
            ``content``. Chunks missing from the DB are silently
            absent from the result — callers that care about
            completeness should compare ``len(result)`` against
            ``len(chunk_ids)``.
        """
        if not chunk_ids:
            return []
        self._ensure_connected()
        statement = (
            select(DocumentChunk)
            .options(
                load_only(
                    DocumentChunk.id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.content,
                )
            )
            .where(
                DocumentChunk.id.in_(chunk_ids),  # type: ignore[union-attr]
                DocumentChunk.database_name == database_name,
            )
            .order_by(DocumentChunk.chunk_index)
        )
        results = self.session.scalars(statement).all()
        return [
            {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content or "",
            }
            for chunk in results
        ]

    def get_chunks_for_extraction(
        self,
        source_id: str,
        database_name: str,
    ) -> list[dict[str, Any]]:
        """Fetch chunks for extraction grouping.

        Returns lightweight chunk data (id, chunk_index, content) for
        content filtering and dynamic group building. Does not load
        embeddings or metadata.

        Args:
            source_id: Source ID to get chunks for.
            database_name: Database name filter.

        Returns:
            Ordered list of chunk dicts with keys: id, chunk_index, content.
        """
        self._ensure_connected()

        stmt = (
            select(DocumentChunk)
            .options(
                load_only(
                    DocumentChunk.id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.content,
                )
            )
            .where(
                DocumentChunk.source_id == source_id,
                DocumentChunk.database_name == database_name,
            )
            .order_by(DocumentChunk.chunk_index)
        )

        results = self.session.exec(stmt)
        return [
            {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content or "",
            }
            for chunk in results.all()
        ]

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 1).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_chunks(self) -> int:
        """Count every DocumentChunk row across all databases."""
        self._ensure_connected()
        stmt = select(func.count()).select_from(DocumentChunk)
        return int(self.session.exec(stmt).one())

    def count_staged_chunks(self, *, database_name: str) -> int:
        """Count chunks with source_id IS NULL in one database."""
        self._ensure_connected()
        stmt = (
            select(func.count())
            .select_from(DocumentChunk)
            .where(
                DocumentChunk.database_name == database_name,
                DocumentChunk.source_id == None,  # noqa: E711 - SQL IS NULL
            )
        )
        return int(self.session.exec(stmt).one())

    def clear_all_chunks(self) -> int:
        """Delete every DocumentChunk across all databases."""
        self._ensure_connected()
        result = self.session.exec(delete(DocumentChunk))
        self._maybe_commit()
        return int(result.rowcount or 0)

    def delete_staged_chunks(self, *, database_name: str) -> int:
        """Delete staged (source_id IS NULL) chunks in one database."""
        self._ensure_connected()
        stmt = delete(DocumentChunk).where(
            DocumentChunk.database_name == database_name,
            DocumentChunk.source_id == None,  # noqa: E711 - SQL IS NULL
        )
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)
