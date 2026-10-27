# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Service for chaoscypher-engine.

Business logic for source/citation tracking operations.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.models import SourceDeletion
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import SearchRepository
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.retry import RetryPolicyPort
    from chaoscypher_core.ports.storage_chunks import ChunkStorageProtocol
    from chaoscypher_core.ports.storage_citations import CitationStorageProtocol
    from chaoscypher_core.ports.storage_source_tags import SourceTagStorageProtocol
    from chaoscypher_core.ports.storage_sources import SourceStorageProtocol
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


def source_chunk_ids(repository: Any, source_id: str, settings: EngineSettings | None) -> list[str]:
    """The ids of a source's chunks (up to ``batching.chunk_fetch_limit``), read before they go.

    The source delete and a site page's re-import read them first, then drop
    the chunks' vectors from the search index with :func:`remove_chunk_vectors`.
    """
    from chaoscypher_core.settings import BatchingSettings

    batching = settings.batching if settings is not None else BatchingSettings()
    chunks, _total = repository.get_chunks_by_source(
        source_id, page=1, page_size=batching.chunk_fetch_limit
    )
    return [c["id"] for c in chunks]


def remove_chunk_vectors(search_repo: Any, source_id: str, chunk_ids: list[str]) -> None:
    """Drop a source's old chunk vectors from the search index; best-effort, logged.

    Left behind, a dead chunk id matches nothing but still takes a slot in a
    nearest-neighbour search's top k.
    """
    if not chunk_ids:
        return
    try:
        removed = search_repo.remove_embeddings_batch(
            [f"chunk:{cid}" for cid in chunk_ids], "chunk"
        )
        logger.info("chunk_embeddings_cleaned", source_id=source_id, chunks_removed=removed)
    except Exception:
        logger.warning(
            "chunk_embeddings_cleanup_failed", source_id=source_id, chunk_count=len(chunk_ids)
        )


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

    @property
    def settings(self) -> EngineSettings | None:
        """The engine settings this service was built with (the graph's naming conventions)."""
        return self._settings

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
        site_id: str | None = None,
        in_site: bool | None = None,
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
            site_id: Only the pages of this imported site
            in_site: True: only pages of imported sites; False: only sources
                outside any site

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
            site_id=site_id,
            in_site=in_site,
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
        search_repo: SearchRepository | None = None,
        *,
        leave_out_of_site: bool = True,
        commit_with: Callable[[GraphRepositoryProtocol], None] | None = None,
    ) -> SourceDeletion:
        """Public delete_source entry point with retry-on-db-lock.

        Delete a source atomically across SQL + graph, with best-effort
        search + file cleanup.

        The graph release (the source's memberships, and the nodes and edges
        no other source attests, #795) and the SQL cascade run in a single
        transaction on the adapter's session: if any step fails, both are
        rolled back and the source is preserved for retry.

        Chunk index cleanup and file deletion happen after the transaction
        commits. The graph's search rows and embeddings are the caller's:
        it passes the returned deletion's ``released`` to
        ``release.refresh_after_release`` with its embedding provider (a
        guard in ``test_ci_config_guards.py`` holds every caller to it).
        Failures there are logged but do not affect the outcome —
        search orphans are harmless for query correctness (dead IDs don't
        match anything) and file orphans can be swept by a future cleanup job.

        The actual delete logic lives in ``_delete_source_impl``; this wrapper
        retries the whole idempotent operation if ``SQLITE_BUSY`` fires inside
        the ``adapter.transaction()`` block. ``SafeSession`` retries the final
        commit call, but busy errors can also occur earlier in the
        transactional write sequence.

        Args:
            source_id: ID of the source to delete
            search_repo: Optional SearchRepository for search index cleanup
            leave_out_of_site: A page of a docs site joins the pages the site
                leaves out, so a refresh does not import it again; False when
                the site itself is being deleted with its pages.
            commit_with: Work that commits with the delete or not at all:
                called inside its transaction, after the source row is gone,
                with the transaction's graph repository (a forget's audit row
                and the facts it reopened). Run again on a retry.

        Returns:
            The deletion: truthy when the source was deleted, falsy when not
            found, and what the graph release took and rebuilt.

        """
        return self._retry_policy.run_sync(
            self._delete_source_impl,
            source_id=source_id,
            search_repo=search_repo,
            leave_out_of_site=leave_out_of_site,
            commit_with=commit_with,
            operation_name="source_delete",
        )

    def _delete_source_impl(
        self,
        source_id: str,
        search_repo: SearchRepository | None = None,
        *,
        leave_out_of_site: bool = True,
        commit_with: Callable[[GraphRepositoryProtocol], None] | None = None,
    ) -> SourceDeletion:
        """Inner delete body. Must be idempotent — may be retried on lock.

        Args:
            source_id: ID of the source to delete
            search_repo: Optional SearchRepository for chunk index cleanup
            leave_out_of_site: See :meth:`delete_source`.
            commit_with: See :meth:`delete_source`.

        Returns:
            The deletion (see :meth:`delete_source`).

        """
        # === PREP (reads only) ===
        source_info = self.repository.get_source(source_id, self.database_name)
        if not source_info:
            # before the release: never release another database's source
            return SourceDeletion(deleted=False)
        filepath = source_info.get("filepath")

        chunk_ids = (
            source_chunk_ids(self.repository, source_id, self._settings) if search_repo else []
        )

        logger.info("delete_source_started", source_id=source_id, chunk_count=len(chunk_ids))

        # === ATOMIC PHASE (graph release + SQL cascade in one transaction) ===
        # At all production call sites, self.repository IS the SqliteAdapter:
        # the release must run on its session to join its transaction. mypy
        # cannot infer that from the SourcesProtocol annotation, hence cast.
        from chaoscypher_core.operations.sources.site_runs import leave_out_deleted_page
        from chaoscypher_core.repo_factories import get_transaction_graph_repository
        from chaoscypher_core.services.graph.management.release import release_source_graph

        adapter = cast("SqliteAdapter", self.repository)
        try:
            with adapter.transaction():
                # Release before the source row goes: graph_*.source_id is
                # ON DELETE SET NULL, so the row delete takes no graph rows.
                # The survivors are rebuilt from the sources they still have.
                graph = get_transaction_graph_repository(adapter, self.database_name)
                released = release_source_graph(
                    graph,
                    source_id,
                    sources=self.repository,
                    database_name=self.database_name,
                    settings=self._settings,
                )

                # SQL cascade (no file deletion)
                deleted = self.repository.delete_source_db(
                    source_id, database_name=self.database_name
                )
                if not deleted:
                    # Source not found — transaction exits cleanly, nothing to roll back
                    return SourceDeletion(deleted=False)
                # A deleted page of a site stays out of the site's refreshes.
                if leave_out_of_site:
                    leave_out_deleted_page(adapter, source_info, self.database_name)
                if commit_with is not None:
                    commit_with(graph)
            # Transaction committed atomically
        except Exception:
            logger.exception(
                "delete_source_atomic_phase_failed",
                source_id=source_id,
            )
            raise

        # === POST-TRANSACTION (best-effort) ===
        # The graph's search rows and embeddings are the caller's to refresh
        # (``release.refresh_after_release``, with its embedding provider):
        # the returned deletion carries what the release took and rebuilt.
        if search_repo:
            remove_chunk_vectors(search_repo, source_id, chunk_ids)

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
        return SourceDeletion(deleted=True, released=released)

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
