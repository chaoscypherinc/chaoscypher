# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SourceStorageProtocol — storage contract for source CRUD and lifecycle.

Covers Source CRUD, lifecycle stage transitions, and database-level stats.
Implemented by ``SourcesMixin`` + ``SourceLifecycleMixin`` + ``SourceIndexingMixin``
in the SQLite adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from pathlib import Path


@runtime_checkable
class SourceStorageProtocol(Protocol):
    """Slim storage protocol for source CRUD and lifecycle operations.

    Covers all operations on the SourceRow model itself — CRUD plus state
    machine transitions. Every method reads or writes the source record.

    Cascade note: ``delete_source`` and ``delete_source_db`` own the cascade
    deletion of all associated chunks, citations, tags, embeddings, and
    extraction data. Per-protocol delete methods (``delete_chunks_for_source``,
    ``delete_citations_by_source``) exist for targeted cleanup only.
    """

    # ========== Source CRUD ==========

    def upload_source(
        self,
        source_id: str,
        database_name: str,
        filename: str,
        file_content: bytes | None = None,
        staging_dir: str = "",
        extraction_depth: str = "full",
        forced_domain: str | None = None,
        origin_url: str | None = None,
        source_type_override: str | None = None,
        title_override: str | None = None,
        content_hash: str | None = None,
        staged_file_path: Path | None = None,
        file_size: int | None = None,
        auto_analyze: bool = True,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        filtering_mode: str = "balanced",
        enable_direction_correction: bool | None = None,
        protect_orphans: bool | None = None,
        enable_inverse_relationships: bool | None = None,
        max_entity_degree_override: int | None = None,
        confirmation_required: bool = False,
    ) -> dict[str, Any]:
        """Upload file and create source record.

        Creates source with status='pending'. Returns created Source as dict.
        """
        ...

    def get_source(self, source_id: str, database_name: str = "") -> dict[str, Any] | None:
        """Get a source by ID.

        Args:
            source_id: Source UUID
            database_name: Database name (optional, uses default if not provided)

        Returns:
            Source dictionary with keys:
                - id, database_name, version, parent_id
                - source_type, title, origin_url
                - chunk_count, total_content_length
                - embedding_model, embedding_dimensions
                - status, created_at, updated_at
                - metadata (optional dict)
                - stage_progress: ``dict[str, StageProgressDict]`` of per-stage
                  LLM progress rows (vision, embedding, mcp_extraction).
                  Empty dict when the source has no in-flight or completed
                  stages.

            None if not found

        """
        ...

    def create_source(self, source: dict[str, Any]) -> dict[str, Any]:
        """Create a new source.

        Args:
            source: Source dictionary with all fields

        Returns:
            Created source dictionary

        """
        ...

    def update_source(self, source_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update an existing source.

        Args:
            source_id: Source identifier
            updates: Dictionary of fields to update

        Returns:
            Updated source dictionary

        """
        ...

    def delete_source_db(
        self,
        source_id: str,
        database_name: str = "",
    ) -> bool:
        """SQL cascade delete of source and all related rows (no file deletion).

        Participates in enclosing ``adapter.transaction()`` contexts via
        ``_maybe_commit()``. Callers should call ``delete_source_files``
        AFTER the transaction commits (files cannot be rolled back).

        Args:
            source_id: Source UUID
            database_name: Database name

        Returns:
            True if deleted, False if not found

        """
        ...

    def delete_source_files(self, filepath: str | None) -> None:
        """Delete the source's on-disk files (best-effort, no raise).

        Separate from ``delete_source_db`` so callers can delete files
        outside the transaction boundary.

        Args:
            filepath: Path to the source file; parent directory is removed.
                No-op if None or if the directory does not exist.

        """
        ...

    def delete_source(
        self,
        source_id: str,
        database_name: str = "",
    ) -> bool:
        """Delete a source and all associated SQLite data (backward-compat wrapper).

        Calls ``delete_source_db`` then ``delete_source_files`` in sequence.
        Prefer using the two methods separately when orchestrating inside a
        transaction (so files are deleted only after the transaction commits).

        Args:
            source_id: Source UUID
            database_name: Database name

        Returns:
            True if deleted, False if not found

        """
        ...

    def list_sources(
        self,
        page: int = 1,
        page_size: int = 50,
        source_type: str | None = None,
        status: str | None = None,
        enabled: str | None = None,
        search: str | None = None,
        tag_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List sources with filtering and pagination.

        Args:
            page: Page number (1-indexed)
            page_size: Items per page
            source_type: Filter by type (document/url/note/etc)
            status: Filter by status (active/archived)
            enabled: Filter by enabled status ('enabled' or 'disabled')
            search: Search in title/origin_url
            tag_id: Filter by tag assignment

        Returns:
            Tuple of (sources list, total count).  Each source dict
            includes ``stage_progress`` (same shape as ``get_source``).
            The implementation bulk-fetches stage_progress in one extra
            round trip per page to avoid N+1 queries.

        """
        ...

    def transition_source_status(
        self,
        source_id: str,
        from_status: str,
        to_status: str,
        *,
        database_name: str,
    ) -> bool:
        """Atomic compare-and-swap status transition, scoped to a single database.

        Args:
            source_id: Source identifier.
            from_status: Expected current status.
            to_status: New status to set.
            database_name: Database that owns the source.

        Returns:
            True if transition succeeded, False if status or database didn't
            match (or the row does not exist).

        """
        ...

    def get_entity_uris_grouped_by_source(
        self, database_name: str, source_ids: list[str]
    ) -> dict[str, list[str]]:
        """Get entity URIs grouped by source ID.

        Args:
            database_name: Current database name.
            source_ids: Source IDs to query.

        Returns:
            Dict mapping source_id to list of unique entity URIs.

        """
        ...

    # ========== Lifecycle Stage Tracking ==========

    def start_indexing(self, source_id: str) -> None:
        """Mark source as starting indexing stage."""
        ...

    def complete_indexing(
        self, source_id: str, chunks_count: int, embedding_model: str, embedding_dimensions: int
    ) -> None:
        """Mark indexing stage as complete."""
        ...

    def fail_indexing(self, source_id: str, error: str) -> None:
        """Mark indexing stage as failed."""
        ...

    def start_extraction(self, source_id: str, depth: str = "full") -> None:
        """Mark source as starting extraction stage."""
        ...

    def complete_extraction(
        self,
        source_id: str,
        *,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        detected_domain: str | None = None,
        forced_domain: str | None = None,
        domain_version: str | None = None,
        domain_content_hash: str | None = None,
        cross_chunk_filtering_log: dict[str, Any] | None = None,
    ) -> None:
        """Mark extraction stage as complete and persist the entity/relationship rows.

        Args:
            source_id: The source ID.
            entities: Deduplicated entity dicts.
            relationships: Relationship dicts with integer ``source`` /
                ``target`` indices into ``entities``.
            detected_domain: Auto-detected domain name (if not forced).
            forced_domain: User-selected domain name (if specified).
            domain_version: Plugin version this source extracted under.
            domain_content_hash: sha256 of the plugin content at extraction time.
            cross_chunk_filtering_log: Cross-chunk filtering log dict
                surfaced by the "Filtering" UI tab.
        """
        ...

    def fail_extraction(self, source_id: str, error: str) -> None:
        """Mark extraction stage as failed."""
        ...

    def start_commit(self, source_id: str) -> None:
        """Mark source as starting commit stage."""
        ...

    def complete_commit(
        self,
        source_id: str,
        nodes_created: int,
        edges_created: int,
        templates_created: int,
        source_document_node_id: str | None = None,
    ) -> None:
        """Mark commit stage as complete."""
        ...

    def fail_commit(self, source_id: str, error: str) -> None:
        """Mark commit stage as failed."""
        ...

    def clear_source_commit_payload(self, source_id: str, database_name: str) -> None:
        """Clear the pending commit payload for a source.

        Called by the commit handler as the LAST write inside the same
        transaction that performs the graph write — if commit fails the
        payload stays for the next retry; if it succeeds the payload is
        discarded atomically with the source status transition.

        Folding the clear into the inner commit transaction is what lets
        ``ImportOperationsService._run_commit`` drop its outer
        ``adapter.transaction()`` wrapper (2026-05-20 writer-lock-
        contention root fix): the outer transaction was holding the
        SQLite writer lock across the LLM embedding HTTP call inside
        the commit service's post-inner-txn phase.
        """
        ...

    def update_step_progress(
        self,
        source_id: str,
        current_step: int,
        total_steps: int,
        step_description: str = "",
    ) -> None:
        """Update source processing progress for UI."""
        ...

    # ========== Statistics ==========

    def get_stats(self, database_name: str) -> dict[str, Any]:
        """Get source statistics for database (counts by status).

        Corresponds to the adapter-level get_stats() method on
        SourceIndexingMixin.  The investigation doc proposed renaming this
        to get_database_source_stats to avoid confusion with the per-source
        CitationStorageProtocol.get_source_stats(source_id), but Task 12
        uses the adapter's existing method name so that isinstance() checks
        pass without touching the mixin.  Task 13 can add the alias.

        Args:
            database_name: Database to query.

        Returns:
            Dict with counts by status.

        """
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 4).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_sources(self, *, database_name: str) -> int:
        """Count SourceRow rows in one database.

        Args:
            database_name: Database to scope to.

        Returns:
            Non-negative integer count.
        """
        ...

    def delete_all_sources(self, *, database_name: str) -> int:
        """Delete every SourceRow in one database.

        Args:
            database_name: Database to scope to.

        Returns:
            Number of rows deleted.
        """
        ...

    # ------------------------------------------------------------------
    # Quality-counter primitives (used by ``services.quality.counters``).
    # The structural ``_SupportsIncrement`` Protocol expects both methods;
    # declaring them here keeps the public contract aligned with the
    # adapter surface every commit / index / search call site relies on.
    # ------------------------------------------------------------------

    def increment_source_counter(
        self, *, source_id: str, database_name: str, column: str, n: int
    ) -> None:
        """Atomically increment a numeric counter column on a source row.

        Best-effort: the helper in ``services.quality.counters`` logs and
        swallows failures, so the underlying UPDATE may no-op for unknown
        sources.

        Args:
            source_id: Source UUID.
            database_name: Database to scope to.
            column: Name of the counter column to increment.
            n: Increment value (typically 1; may be larger for batched drops).
        """
        ...

    def update_source_columns(
        self, *, source_id: str, database_name: str, updates: dict[str, Any]
    ) -> None:
        """Apply a partial column update to a source row.

        Used by quality-counter helpers (``mark_search_indexing_*``,
        ``set_loader_encoding``) to stamp status / timestamp / encoding
        fields without going through the heavier ``update_source`` path.

        Args:
            source_id: Source UUID.
            database_name: Database to scope to.
            updates: Column-name -> value mapping to write.
        """
        ...
