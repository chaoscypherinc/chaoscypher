# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Service for chaoscypher-engine.

Business logic for source/citation tracking operations.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from typing import Protocol

    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.ports.retry import RetryPolicyPort
    from chaoscypher_core.ports.storage_chunks import ChunkStorageProtocol
    from chaoscypher_core.ports.storage_citations import CitationStorageProtocol
    from chaoscypher_core.ports.storage_source_tags import SourceTagStorageProtocol
    from chaoscypher_core.ports.storage_sources import SourceStorageProtocol
    from chaoscypher_core.ports.transactional import TransactionalAdapterProtocol
    from chaoscypher_core.settings import EngineSettings

    class SourcesProtocol(
        SourceStorageProtocol,
        SourceTagStorageProtocol,
        ChunkStorageProtocol,
        CitationStorageProtocol,
        Protocol,
    ):
        """Combined protocol for SourceService — covers CRUD, tags, chunks, citations."""


logger = structlog.get_logger(__name__)


class SourceService:
    """Service for source and citation management.

    Handles:
    - Source CRUD (documents, URLs, notes)
    - Tag management for source organization
    - Citation tracking (entity → chunk attributions)
    """

    def __init__(
        self,
        repository: SourcesProtocol,
        database_name: str,
        settings: EngineSettings | None = None,
        retry_policy: RetryPolicyPort | None = None,
    ):
        """Initialize source service.

        Args:
            repository: SourcesProtocol implementation
            database_name: Database name for source operations
            settings: Engine settings; falls back to global settings when omitted.
            retry_policy: Retry policy for SQLite-lock-sensitive operations;
                defaults to :class:`DbLockRetryPolicy` when omitted.

        """
        from chaoscypher_core.utils.retry import DbLockRetryPolicy

        self.repository = repository
        self.database_name = database_name
        self._settings = settings
        self._retry_policy: RetryPolicyPort = retry_policy or DbLockRetryPolicy()

    # ================================
    # Source Operations
    # ================================

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        """Get a source by ID."""
        return self.repository.get_source(source_id, self.database_name)

    def list_sources(
        self,
        page: int = 1,
        page_size: int = 50,
        source_type: str | None = None,
        status: str | None = None,
        enabled: str | None = None,
        search: str | None = None,
        tag_id: str | None = None,
    ) -> dict[str, Any]:
        """List sources with filtering and pagination.

        Args:
            page: Page number (1-indexed)
            page_size: Items per page
            source_type: Filter by type
            status: Filter by processing_status
            enabled: Filter by enabled status ('enabled' or 'disabled')
            search: Search query
            tag_id: Filter by tag

        Returns:
            Dict with keys:
                - sources: List of source dicts
                - total: Total count
                - page: Current page
                - page_size: Items per page

        """
        sources, total = self.repository.list_sources(
            page=page,
            page_size=page_size,
            source_type=source_type,
            status=status,
            enabled=enabled,
            search=search,
            tag_id=tag_id,
        )

        return {"sources": sources, "total": total, "page": page, "page_size": page_size}

    def create_source(
        self,
        source_type: str,
        title: str,
        origin_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        version: int = 1,
        parent_id: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
        database_name: str = "",
    ) -> dict[str, Any]:
        """Create a new source.

        Args:
            source_type: Type (document/url/note/etc)
            title: Source title
            origin_url: Optional URL
            metadata: Optional metadata dict
            version: Version number
            parent_id: Optional parent source
            embedding_model: Optional embedding model name
            embedding_dimensions: Optional embedding dimensions
            database_name: Database name

        Returns:
            Created source dict

        """
        now = datetime.now(UTC)
        source = {
            "id": generate_id(),
            "database_name": database_name,
            "version": version,
            "parent_id": parent_id,
            "source_type": source_type,
            "title": title,
            "origin_url": origin_url,
            "chunk_count": 0,
            "total_content_length": 0,
            "embedding_model": embedding_model,
            "embedding_dimensions": embedding_dimensions,
            "processing_status": "ready",
            "created_at": now,
            "updated_at": now,
            "user_metadata": metadata,
        }

        return self.repository.create_source(source)

    def update_source(
        self,
        source_id: str,
        title: str | None = None,
        processing_status: str | None = None,
        enabled: bool | None = None,
        user_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a source.

        Args:
            source_id: Source UUID
            title: Optional new title
            processing_status: Optional new processing status
            enabled: Optional enabled state (controls visibility in graph/search)
            user_metadata: Optional new user metadata

        Returns:
            Updated source dict, None if not found

        """
        # Confirm existence first so a missing source maps to None (404)
        # rather than the storage layer's NotFoundError.
        source = self.repository.get_source(source_id, self.database_name)
        if not source:
            return None

        # Forward ONLY the fields the caller asked to change. Echoing the full
        # read-back dict back into the storage layer is unsafe: get_source()
        # serializes datetime columns (e.g. last_activity_at, vector_indexed_at)
        # to ISO strings, which SQLite's DateTime binding rejects with a
        # TypeError. ``processing_status`` maps to the model field ``status``
        # (DB column ``processing_status``); the storage layer keys writes by
        # field name, so the wrong key would be silently dropped.
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if processing_status is not None:
            updates["status"] = processing_status
        if enabled is not None:
            updates["enabled"] = enabled
        if user_metadata is not None:
            updates["user_metadata"] = user_metadata

        if not updates:
            return source

        # update_source() refreshes ``updated_at`` itself.
        return self.repository.update_source(source_id, updates)

    def delete_source(
        self,
        source_id: str,
        graph_repo: GraphRepository | None = None,
        search_repo: SearchRepository | None = None,
    ) -> bool:
        """Public delete_source entry point with retry-on-db-lock.

        Delete a source atomically across SQL + graph, with best-effort
        search + file cleanup.

        The SQL cascade (via storage adapter) and graph entity deletes
        participate in a single transaction: if any step fails, both are
        rolled back and the source is preserved for retry.

        Search index cleanup and file deletion happen after the transaction
        commits. Failures there are logged but do not affect the outcome —
        search orphans are harmless for query correctness (dead IDs don't
        match anything) and file orphans can be swept by a future cleanup job.

        The actual delete logic lives in ``_delete_source_impl``; this wrapper
        retries the whole idempotent operation if ``SQLITE_BUSY`` fires inside
        the ``adapter.transaction()`` block. ``SafeSession`` retries the final
        commit call, but busy errors can also occur earlier in the
        transactional write sequence.

        Args:
            source_id: ID of the source to delete
            graph_repo: Optional GraphRepository for knowledge graph cleanup
            search_repo: Optional SearchRepository for search index cleanup

        Returns:
            True if source was deleted, False if not found

        """
        return self._retry_policy.run_sync(
            self._delete_source_impl,
            source_id=source_id,
            graph_repo=graph_repo,
            search_repo=search_repo,
            operation_name="source_delete",
        )

    def _delete_source_impl(
        self,
        source_id: str,
        graph_repo: GraphRepository | None = None,
        search_repo: SearchRepository | None = None,
    ) -> bool:
        """Inner delete body. Must be idempotent — may be retried on lock.

        Args:
            source_id: ID of the source to delete
            graph_repo: Optional GraphRepository for knowledge graph cleanup
            search_repo: Optional SearchRepository for search index cleanup

        Returns:
            True if source was deleted, False if not found

        """
        # === PREP (reads only) ===
        orphaned_uris = self.repository.get_orphaned_entity_uris(source_id)

        chunk_ids: list[str] = []
        if search_repo:
            from chaoscypher_core.settings import BatchingSettings

            batching = self._settings.batching if self._settings is not None else BatchingSettings()
            chunks, _total = self.repository.get_chunks_by_source(
                source_id, page=1, page_size=batching.chunk_fetch_limit
            )
            chunk_ids = [c["id"] for c in chunks]

        source_info = self.repository.get_source(source_id, self.database_name)
        filepath = source_info.get("filepath") if source_info else None

        logger.info(
            "delete_source_started",
            source_id=source_id,
            orphaned_entities=len(orphaned_uris),
            chunk_count=len(chunk_ids),
        )

        # === ATOMIC PHASE (SQL + graph in one transaction) ===
        # At all production call sites, self.repository IS the SqliteAdapter
        # which implements both SourcesProtocol and TransactionalAdapterProtocol.
        # mypy cannot infer that from the SourcesProtocol annotation, hence cast.
        adapter = cast("TransactionalAdapterProtocol", self.repository)
        try:
            with adapter.transaction():
                # Graph cleanup first — executed on shared session
                if graph_repo and orphaned_uris:
                    node_ids = [uri.split("/")[-1] if "/" in uri else uri for uri in orphaned_uris]
                    # Absent nodes are silently skipped; only a raised exception
                    # (e.g. DB error) triggers transaction rollback.
                    graph_repo.delete_nodes_batch(node_ids=node_ids)

                # SQL cascade (no file deletion)
                deleted = self.repository.delete_source_db(
                    source_id, database_name=self.database_name
                )
                if not deleted:
                    # Source not found — transaction exits cleanly, nothing to roll back
                    return False
            # Transaction committed atomically
        except Exception:
            logger.exception(
                "delete_source_atomic_phase_failed",
                source_id=source_id,
            )
            raise

        # === POST-TRANSACTION (best-effort) ===
        if search_repo:
            for uri in orphaned_uris:
                node_id = uri.split("/")[-1] if "/" in uri else uri
                try:
                    search_repo.delete_node(node_id)
                except Exception:
                    logger.warning(
                        "search_orphan_delete_failed",
                        node_id=node_id,
                        source_id=source_id,
                    )

            if chunk_ids:
                try:
                    prefixed_ids = [f"chunk:{cid}" for cid in chunk_ids]
                    removed = search_repo.remove_embeddings_batch(prefixed_ids, "chunk")
                    logger.info(
                        "chunk_embeddings_cleaned",
                        source_id=source_id,
                        chunks_removed=removed,
                    )
                except Exception:
                    logger.warning(
                        "chunk_embeddings_cleanup_failed",
                        source_id=source_id,
                        chunk_count=len(chunk_ids),
                    )

        if filepath:
            try:
                self.repository.delete_source_files(filepath)
            except Exception:
                logger.warning(
                    "source_files_cleanup_failed",
                    source_id=source_id,
                    filepath=filepath,
                )

        # Best-effort cleanup of any rendered vision PNGs. The directory
        # lives outside the source's staged-file parent (it sits under
        # ``{data_dir}/databases/<db>/images/<source_id>/``), so
        # ``delete_source_files`` does not touch it. Audit fix F32.
        try:
            from chaoscypher_core.operations.importing.indexing_handler import (
                cleanup_vision_images,
            )
            from chaoscypher_core.settings import PathSettings

            data_dir = (
                self._settings.paths.data_dir
                if self._settings is not None
                else PathSettings().data_dir
            )
            cleanup_vision_images(
                data_dir=data_dir,
                database_name=self.database_name,
                source_id=source_id,
            )
        except Exception:
            logger.warning(
                "vision_images_cleanup_failed",
                source_id=source_id,
            )

        logger.info("delete_source_completed", source_id=source_id)
        return True

    # ================================
    # Tag Operations
    # ================================

    def get_tag(self, tag_id: str) -> dict[str, Any] | None:
        """Get a tag by ID."""
        return self.repository.get_tag(tag_id, self.database_name)

    def list_tags(self) -> list[dict[str, Any]]:
        """List all tags."""
        return self.repository.list_tags(self.database_name)

    def create_tag(
        self,
        name: str,
        color: str | None = None,
        description: str | None = None,
        database_name: str = "",
    ) -> dict[str, Any]:
        """Create a new tag.

        Args:
            name: Tag name
            color: Optional color (hex)
            description: Optional human-readable description
            database_name: Database name (optional, uses repository's database if not provided)

        Returns:
            Created tag dict

        """
        # Use repository's database_name if not explicitly provided
        if not database_name:
            database_name = self.database_name

        # Return existing tag if name already exists (case-insensitive)
        existing_tags = self.repository.list_tags(database_name)
        for existing in existing_tags:
            if existing["name"].lower() == name.lower():
                return existing

        tag = {
            "id": generate_id(),
            "database_name": database_name,
            "name": name,
            "color": color,
            "description": description,
            "created_at": datetime.now(UTC),
        }

        return self.repository.create_tag(tag)

    def update_tag(
        self,
        tag_id: str,
        name: str | None = None,
        color: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Update a tag.

        Args:
            tag_id: Tag UUID
            name: Optional new name
            color: Optional new color
            description: Optional new description

        Returns:
            Updated tag dict, None if not found

        """
        tag = self.repository.get_tag(tag_id, self.database_name)
        if not tag:
            return None

        if name is not None:
            tag["name"] = name
        if color is not None:
            tag["color"] = color
        if description is not None:
            tag["description"] = description

        return self.repository.update_tag(tag)

    def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag."""
        return self.repository.delete_tag(tag_id)

    def get_source_tags(self, source_id: str) -> list[dict[str, Any]]:
        """Get all tags assigned to a source."""
        return self.repository.get_source_tags(source_id)

    def get_source_tags_batch(self, source_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Get tags for multiple sources in a single query."""
        return self.repository.get_source_tags_batch(source_ids)

    def assign_tag(self, source_id: str, tag_id: str) -> dict[str, Any]:
        """Assign a tag to a source."""
        return self.repository.assign_tag(source_id, tag_id, self.database_name)

    def unassign_tag(self, source_id: str, tag_id: str) -> bool:
        """Remove a tag from a source."""
        return self.repository.unassign_tag(source_id, tag_id)

    # ================================
    # Chunk Operations
    # ================================

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        """Get a document chunk by ID."""
        return self.repository.get_chunk(chunk_id, self.database_name)

    def get_chunks_by_source(
        self, source_id: str, page: int = 1, page_size: int = 50, status: str | None = None
    ) -> dict[str, Any]:
        """Get all chunks for a source with pagination.

        Args:
            source_id: Source UUID
            page: Page number (1-indexed)
            page_size: Items per page
            status: Filter by status

        Returns:
            Dict with keys:
                - chunks: List of chunk dicts
                - total: Total count
                - page: Current page
                - page_size: Items per page

        """
        chunks, total = self.repository.get_chunks_by_source(
            source_id=source_id, page=page, page_size=page_size, status=status
        )

        return {"chunks": chunks, "total": total, "page": page, "page_size": page_size}

    # ================================
    # Citation Operations
    # ================================

    def get_citations_by_entity(
        self, entity_uri: str, page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        """Get all citations for an entity (node).

        Args:
            entity_uri: Node ID (entity URI in the knowledge graph)
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Dict with keys:
                - citations: List of citation dicts (with source/chunk data)
                - total: Total count
                - page: Current page
                - page_size: Items per page

        """
        offset = (page - 1) * page_size
        citations, total = self.repository.get_citations_by_entity(
            entity_uri=entity_uri, offset=offset, limit=page_size
        )

        return {"citations": citations, "total": total, "page": page, "page_size": page_size}

    def get_citations_by_source(
        self, source_id: str, page: int = 1, page_size: int = 50
    ) -> dict[str, Any]:
        """Get all citations from a source document.

        Args:
            source_id: Source UUID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Dict with keys:
                - citations: List of citation dicts
                - total: Total count
                - page: Current page
                - page_size: Items per page

        """
        citations, total = self.repository.get_citations_by_source(
            source_id=source_id, page=page, page_size=page_size
        )

        return {"citations": citations, "total": total, "page": page, "page_size": page_size}

    # ================================
    # Statistics
    # ================================

    def get_source_stats(self, source_id: str) -> dict[str, Any]:
        """Get statistics for a source.

        Args:
            source_id: Source UUID

        Returns:
            Dict with chunk_count, citation_count, entity_count

        """
        return self.repository.get_source_stats(source_id)
