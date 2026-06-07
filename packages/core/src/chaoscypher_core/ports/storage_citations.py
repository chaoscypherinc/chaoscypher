# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CitationStorageProtocol for ChaosCypher storage.

Split from the original SourceStorageProtocol god-protocol (Phase 1 Task 12).
Covers SourceCitation and RelationshipCitation table operations, orphan
detection, and per-source citation statistics.
Binds to SourceCitationsMixin in the SQLite adapter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CitationStorageProtocol(Protocol):
    """Storage protocol for citation operations.

    Handles SourceCitation and RelationshipCitation records, orphan entity
    detection (citations that would become unreferenced after source deletion),
    and per-source citation/chunk statistics.

    ``get_source_stats(source_id)`` lives here because the implementation
    aggregates citation and chunk counts for a single source.  The
    database-level stats (counts by status across all sources) live on
    ``SourceStorageProtocol.get_stats``.
    """

    def create_citation(self, citation: dict[str, Any]) -> dict[str, Any]:
        """Create a new citation linking an entity to a source chunk.

        Args:
            citation: Citation dictionary with keys:
                - id: Citation UUID
                - database_name: Database name
                - entity_uri: Node ID (entity URI)
                - entity_label: Entity name
                - entity_type: Optional entity type
                - source_id: Source UUID
                - chunk_id: Chunk UUID
                - confidence: Confidence score (0.0-1.0)
                - extraction_method: Method used (e.g., 'ai_extraction')
                - context_snippet: Optional surrounding text
                - created_at: Creation timestamp
                - metadata: Optional metadata dict

        Returns:
            Created citation dictionary

        """
        ...

    def create_citations_batch(self, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create multiple citations in a single transaction.

        Args:
            citations: List of citation dictionaries, each with keys:
                - id: Citation UUID
                - database_name: Database name
                - entity_uri: Node ID (entity URI)
                - entity_label: Entity name
                - entity_type: Optional entity type
                - source_id: Source UUID
                - chunk_id: Chunk UUID
                - confidence: Confidence score (0.0-1.0)
                - extraction_method: Method used (e.g., 'ai_extraction')
                - context_snippet: Optional surrounding text
                - created_at: Creation timestamp
                - metadata: Optional metadata dict

        Returns:
            List of created citation dictionaries

        Performance:
            Single transaction for N citations vs N individual commits.
            Expected speedup: ~20-50x for 100+ citations.

        """
        ...

    def create_relationship_citations_batch(
        self, citations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Create multiple relationship citations in a single transaction.

        Args:
            citations: List of relationship citation dictionaries

        Returns:
            List of created citation dictionaries

        """
        ...

    def list_citations(
        self,
        database_name: str,
        entity_uri: str | None = None,
        source_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List citations with optional filters."""
        ...

    def get_citations_batch(
        self,
        database_name: str,
        entity_uris: list[str],
        source_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return SourceCitation records for multiple entity URIs.

        Uses batch lookup for efficient retrieval. entity_uris are node IDs
        (SourceCitation.entity_uri stores node IDs directly).

        Args:
            database_name: Database to query.
            entity_uris: List of node IDs to look up citations for.
            source_ids: Optional source scope filter.

        Returns:
            List of citation dicts.

        """
        ...

    def get_citations_by_entity(
        self, entity_uri: str, offset: int = 0, limit: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        """Get all citations for an entity (node).

        Returns where this entity was mentioned in source documents.

        Args:
            entity_uri: Node ID (entity URI in the knowledge graph)
            offset: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (citations list with source/chunk data, total count)

        Notes:
            - Each citation includes: citation, source, and chunk data
            - Citations are ordered by created_at descending

        """
        ...

    def get_citations_by_source(
        self, source_id: str, page: int = 1, page_size: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        """Get all citations from a source document.

        Args:
            source_id: Source UUID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (citations list, total count)

        """
        ...

    def delete_citations_by_source(self, source_id: str) -> dict[str, int]:
        """Delete all entity and relationship citations for a source.

        Used for idempotent commit: cleans up previously created citations
        before re-committing.

        Args:
            source_id: Source ID whose citations should be deleted.

        Returns:
            Dict with entity_citations_deleted and relationship_citations_deleted counts.

        """
        ...

    def get_orphaned_entity_uris(self, source_id: str) -> list[str]:
        """Get entity URIs that will be orphaned when source is deleted.

        Returns URIs where this source is the ONLY remaining citation.
        Entities with citations from other sources are excluded.

        Args:
            source_id: Source UUID to check

        Returns:
            List of entity URIs that would become orphans

        """
        ...

    def get_entity_uris_for_sources(self, source_ids: list[str]) -> list[str]:
        """Get all entity URIs cited by the given sources.

        Used for source-filtered export to determine which entities to include.

        Args:
            source_ids: List of source UUIDs

        Returns:
            List of unique entity URIs

        """
        ...

    def get_source_stats(self, source_id: str) -> dict[str, Any]:
        """Get statistics for a source.

        Args:
            source_id: Source UUID

        Returns:
            Dict with keys:
                - chunk_count: Number of chunks
                - citation_count: Number of citations
                - entity_count: Number of unique entities cited

        """
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 2).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def clear_all_citations(self) -> int:
        """Delete every SourceCitation row across the database.

        Returns:
            Number of rows deleted.
        """
        ...

    def delete_all_relationship_citations(self) -> int:
        """Delete every RelationshipCitation row across the database.

        Returns:
            Number of rows deleted.
        """
        ...
