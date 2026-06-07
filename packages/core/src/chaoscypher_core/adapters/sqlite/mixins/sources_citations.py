# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Citations Mixin for SqliteAdapter.

Handles entity citation and relationship citation operations,
source statistics, orphan detection, and bulk clear.
Part of the unified SourceStorageProtocol implementation.
"""

from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import (
    DocumentChunk,
    RelationshipCitation,
    SourceCitation,
    SourceRow,
    SourceTag,
    SourceTagAssignment,
)
from chaoscypher_core.ports.storage_citations import CitationStorageProtocol
from chaoscypher_core.utils.id import generate_id


logger = structlog.get_logger(__name__)


class SourceCitationsMixin(SqliteMixinBase, CitationStorageProtocol):
    """Mixin providing citation operations for SQLite storage.

    Implements operations for:
    - Entity citation CRUD (create, batch create, list, filter)
    - Relationship citation CRUD (batch create, list)
    - Source statistics aggregation
    - Orphan detection for entity cleanup
    - Bulk clear operations

    Note: This mixin contributes to the unified SourceStorageProtocol.
    """

    def create_citation(self, citation_data: dict[str, Any]) -> dict[str, Any]:
        """Create source citation."""
        self._ensure_connected()
        citation = SourceCitation(**citation_data)
        self.session.add(citation)
        self._maybe_commit()
        # No session.refresh(): all fields (id, created_at, etc.) are set
        # Python-side before insert — no server_default columns exist on
        # SourceCitation, so refresh would only issue a needless SELECT.
        return self._entity_to_dict(citation)

    def create_citations_batch(self, citations_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create multiple citations in a single transaction.

        Args:
            citations_data: List of citation dictionaries

        Returns:
            List of citation dictionaries (includes both newly-created
            and pre-existing rows when duplicate IDs are passed in).

        Performance:
            Single commit for all citations instead of N individual commits.
            Expected speedup: ~20-50x for 100+ citations.

        Idempotency:
            Callers may pass citation IDs that already exist in the
            database — the commit path uses content-addressed
            stable IDs, so a re-dispatched commit passes the same
            ``id`` values it did the first time. This method filters
            incoming rows against the existing-IDs set and only
            inserts the genuinely new ones, preventing PK conflicts
            on replay.
        """
        self._ensure_connected()

        if not citations_data:
            return []

        # Pre-fetch any existing rows that match the incoming IDs so
        # a re-dispatched commit doesn't re-insert them.
        incoming_ids = [d["id"] for d in citations_data if d.get("id")]
        existing_rows: dict[str, SourceCitation] = {}
        if incoming_ids:
            existing_stmt = select(SourceCitation).where(SourceCitation.id.in_(incoming_ids))
            for row in self.session.scalars(existing_stmt).all():
                existing_rows[row.id] = row

        new_citations: list[SourceCitation] = []
        result_rows: list[SourceCitation] = []
        batch_seen: dict[str, SourceCitation] = {}
        for data in citations_data:
            row_id = data.get("id")
            if row_id and row_id in existing_rows:
                result_rows.append(existing_rows[row_id])
                continue
            if row_id and row_id in batch_seen:
                result_rows.append(batch_seen[row_id])
                continue
            citation = SourceCitation(**data)
            new_citations.append(citation)
            result_rows.append(citation)
            self.session.add(citation)
            if row_id:
                batch_seen[row_id] = citation

        if new_citations:
            self._maybe_commit()
            # No per-row session.refresh(): all fields (id, created_at, etc.)
            # are set Python-side before insert.  SourceCitation has no
            # server_default columns, so the refresh loop was issuing N
            # needless SELECTs — one per inserted row.

        return self._entities_to_dicts(result_rows)

    def list_citations(
        self,
        database_name: str,
        entity_uri: str | None = None,
        source_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List citations with optional filters."""
        self._ensure_connected()
        stmt = (
            select(SourceCitation)
            .options(
                load_only(
                    SourceCitation.id,
                    SourceCitation.database_name,
                    SourceCitation.entity_uri,
                    SourceCitation.entity_label,
                    SourceCitation.entity_type,
                    SourceCitation.source_id,
                    SourceCitation.chunk_id,
                    SourceCitation.confidence,
                    SourceCitation.extraction_method,
                    SourceCitation.created_at,
                )
            )
            .where(SourceCitation.database_name == database_name)
        )

        if entity_uri is not None:
            stmt = stmt.where(SourceCitation.entity_uri == entity_uri)
        if source_id is not None:
            stmt = stmt.where(SourceCitation.source_id == source_id)

        stmt = stmt.limit(limit)

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def get_citations_batch(
        self,
        database_name: str,
        entity_uris: list[str],
        source_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return SourceCitation records for multiple entity URIs.

        Args:
            database_name: Database to query.
            entity_uris: List of node IDs to look up citations for.
            source_ids: Optional source scope filter.

        Returns:
            List of citation dicts.

        """
        self._ensure_connected()

        if not entity_uris:
            return []

        stmt = (
            select(SourceCitation)
            .options(
                load_only(
                    SourceCitation.id,
                    SourceCitation.database_name,
                    SourceCitation.entity_uri,
                    SourceCitation.entity_label,
                    SourceCitation.entity_type,
                    SourceCitation.source_id,
                    SourceCitation.chunk_id,
                    SourceCitation.confidence,
                    SourceCitation.extraction_method,
                    SourceCitation.created_at,
                )
            )
            .where(
                SourceCitation.database_name == database_name,
                SourceCitation.entity_uri.in_(entity_uris),
            )
        )
        if source_ids:
            stmt = stmt.where(SourceCitation.source_id.in_(source_ids))

        results = self.session.exec(stmt).all()
        return self._entities_to_dicts(results)

    def get_citations_by_entity(
        self, entity_uri: str, offset: int = 0, limit: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        """Get all citations for an entity (node).

        Args:
            entity_uri: Node ID (entity URI in the knowledge graph)
            offset: Pagination offset
            limit: Maximum results

        Returns:
            Tuple of (citations list with source/chunk data, total count)

        """
        self._ensure_connected()

        # Count total
        count_stmt = (
            select(func.count())
            .select_from(SourceCitation)
            .where(SourceCitation.entity_uri == entity_uri)
        )
        total = self.session.exec(count_stmt).one()

        # Fetch page with load_only
        stmt = (
            select(SourceCitation)
            .where(SourceCitation.entity_uri == entity_uri)
            .options(
                load_only(
                    SourceCitation.id,
                    SourceCitation.source_id,
                    SourceCitation.chunk_id,
                    SourceCitation.database_name,
                    SourceCitation.entity_uri,
                    SourceCitation.entity_label,
                    SourceCitation.entity_type,
                    SourceCitation.confidence,
                    SourceCitation.extraction_method,
                    SourceCitation.created_at,
                )
            )
            .order_by(SourceCitation.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all()), total

    def get_citations_by_source(
        self, source_id: str, page: int = 1, page_size: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        """Get all citations for a source with pagination.

        Uses load_only() to exclude large JSON/TEXT columns for performance.
        For full citation data, use a dedicated get method.

        Args:
            source_id: Source ID to filter citations
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (citations list, total count)

        """
        self._ensure_connected()

        # Count total
        count_stmt = (
            select(func.count())
            .select_from(SourceCitation)
            .where(SourceCitation.source_id == source_id)
        )
        total = self.session.exec(count_stmt).one()

        # Fetch page with load_only
        stmt = (
            select(SourceCitation)
            .where(SourceCitation.source_id == source_id)
            .options(
                load_only(
                    SourceCitation.id,
                    SourceCitation.source_id,
                    SourceCitation.chunk_id,
                    SourceCitation.database_name,
                    SourceCitation.entity_uri,
                    SourceCitation.entity_label,
                    SourceCitation.entity_type,
                    SourceCitation.confidence,
                    SourceCitation.extraction_method,
                    SourceCitation.created_at,
                )
            )
            .order_by(SourceCitation.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all()), total

    def get_source_stats(
        self, source_id: str, top_cited_entities_limit: int | None = None
    ) -> dict[str, Any]:
        """Get statistics for a source.

        Args:
            source_id: Source UUID
            top_cited_entities_limit: Max entities returned in ``top_entities``.
                ``None`` (default) resolves to
                ``QualitySettings().top_cited_entities_limit`` (the class
                default) — the SQLite adapter holds no settings object, so a
                caller that wants the operator override must pass it explicitly.

        Returns:
            Dict with chunk_count, citation_count, entity_count, chunk status breakdown, content length

        """
        self._ensure_connected()

        if top_cited_entities_limit is None:
            from chaoscypher_core.settings import QualitySettings

            top_cited_entities_limit = QualitySettings().top_cited_entities_limit

        # Get source record for total_content_length only - exclude large JSON columns
        statement = (
            select(SourceRow)
            .where(SourceRow.id == source_id)
            .options(load_only(SourceRow.id, SourceRow.total_content_length))
        )
        source = self.session.exec(statement).first()
        if not source:
            return {
                "total_chunks": 0,
                "total_content_length": 0,
                "committed_chunks": 0,
                "staged_chunks": 0,
                "rejected_chunks": 0,
                "total_citations": 0,
                "entity_count": 0,
            }

        # Chunk stats: single GROUP BY status query (replaces 4 separate count queries)
        chunk_status_query = (
            select(DocumentChunk.status, func.count())
            .where(DocumentChunk.source_id == source_id)
            .group_by(DocumentChunk.status)
        )
        chunk_counts: dict[str, int] = {}
        for status_val, count in self.session.exec(chunk_status_query).all():
            chunk_counts[status_val or "unknown"] = count
        total_chunks = sum(chunk_counts.values())
        committed_count = chunk_counts.get("committed", 0)
        staged_count = chunk_counts.get("staged", 0)
        rejected_count = chunk_counts.get("rejected", 0)

        # Citation stats: single query for count, unique entities, avg confidence
        citation_stats_query = (
            select(
                func.count(),
                func.count(func.distinct(SourceCitation.entity_uri)),
                func.avg(SourceCitation.confidence),
            )
            .select_from(SourceCitation)
            .where(SourceCitation.source_id == source_id)
        )
        citation_row = self.session.exec(citation_stats_query).one()
        citation_count = citation_row[0]
        entity_count = citation_row[1]
        avg_confidence = citation_row[2] or 0.0

        # Entity type distribution - GROUP BY entity_type
        type_dist_query = (
            select(SourceCitation.entity_type, func.count())
            .where(SourceCitation.source_id == source_id)
            .group_by(SourceCitation.entity_type)
        )
        type_distribution = {
            row[0] or "Unknown": row[1] for row in self.session.exec(type_dist_query).all()
        }

        # Top cited entities - GROUP BY entity_label, ORDER BY count DESC,
        # capped at the configured top-cited-entities limit.
        top_entities_query = (
            select(
                SourceCitation.entity_label,
                SourceCitation.entity_type,
                func.count().label("count"),
            )
            .where(SourceCitation.source_id == source_id)
            .group_by(SourceCitation.entity_label, SourceCitation.entity_type)
            .order_by(func.count().desc())
            .limit(top_cited_entities_limit)
        )
        top_entities = [
            {"label": row[0], "type": row[1], "count": row[2]}
            for row in self.session.exec(top_entities_query).all()
        ]

        # Relationship citation stats: count + type distribution in one pass
        rel_type_dist_query = (
            select(RelationshipCitation.edge_label, func.count())
            .where(RelationshipCitation.source_id == source_id)
            .group_by(RelationshipCitation.edge_label)
        )
        relationship_type_distribution: dict[str, int] = {}
        relationship_count = 0
        for label, count in self.session.exec(rel_type_dist_query).all():
            relationship_type_distribution[label or "Unknown"] = count
            relationship_count += count

        return {
            "total_chunks": total_chunks,
            "total_content_length": source.total_content_length,
            "committed_chunks": committed_count,
            "staged_chunks": staged_count,
            "rejected_chunks": rejected_count,
            "total_citations": citation_count,
            "entity_count": entity_count,
            "relationship_count": relationship_count,
            "entity_type_distribution": type_distribution,
            "relationship_type_distribution": relationship_type_distribution,
            "top_entities": top_entities,
            "avg_confidence": round(avg_confidence, 2),
        }

    # ================================
    # Relationship Citations
    # ================================

    def create_relationship_citations_batch(
        self, citations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Create multiple relationship citations in batch.

        Args:
            citations: List of citation dicts with edge_id, source_id, chunk_id, etc.

        Returns:
            List of citation dicts (both newly-created and pre-existing
            rows when duplicate IDs are passed in).

        Idempotency:
            Commit callers pass stable content-addressed IDs so a
            re-dispatched commit lands the same ``id`` values twice.
            This method filters incoming rows against the existing-IDs
            set and only inserts the genuinely new ones. Rows without
            an explicit ``id`` key still get a UUID (legacy callers).
        """
        self._ensure_connected()

        if not citations:
            return []

        # Fill in missing IDs for legacy callers first, then filter
        # against existing rows.
        for citation_data in citations:
            if "id" not in citation_data:
                citation_data["id"] = generate_id()

        incoming_ids = [d["id"] for d in citations]
        existing_rows: dict[str, RelationshipCitation] = {}
        if incoming_ids:
            existing_stmt = select(RelationshipCitation).where(
                RelationshipCitation.id.in_(incoming_ids)
            )
            for row in self.session.scalars(existing_stmt).all():
                existing_rows[row.id] = row

        new_created: list[dict[str, Any]] = []
        result: list[dict[str, Any]] = []
        batch_seen: dict[str, dict[str, Any]] = {}
        for citation_data in citations:
            row_id = citation_data["id"]
            if row_id in existing_rows:
                result.append(self._entity_to_dict(existing_rows[row_id]))
                continue
            if row_id in batch_seen:
                result.append(batch_seen[row_id])
                continue
            citation = RelationshipCitation(**citation_data)
            self.session.add(citation)
            dict_row = self._entity_to_dict(citation)
            # _entity_to_dict only returns None when entity is None; we just
            # constructed citation above, so the cast is safe.
            assert dict_row is not None
            new_created.append(dict_row)
            result.append(dict_row)
            batch_seen[row_id] = dict_row

        if new_created:
            self._maybe_commit()

        logger.info(
            "relationship_citations_batch_upserted",
            total=len(citations),
            new=len(new_created),
            reused=len(citations) - len(new_created),
        )
        return result

    # ================================
    # Bulk Clear
    # ================================

    def clear_all(self, database_name: str) -> dict[str, int]:
        """Clear all sources, chunks, citations, tags, and tag assignments for database.

        WARNING: This operation cannot be undone!

        Args:
            database_name: Database to clear sources from

        Returns:
            Dictionary with counts of deleted entities

        """
        self._ensure_connected()

        # Count before deleting
        sources_query = (
            select(func.count())
            .select_from(SourceRow)
            .where(SourceRow.database_name == database_name)
        )
        sources_count = self.session.exec(sources_query).one()

        chunks_query = (
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.database_name == database_name)
        )
        chunks_count = self.session.exec(chunks_query).one()

        citations_query = (
            select(func.count())
            .select_from(SourceCitation)
            .where(SourceCitation.database_name == database_name)
        )
        citations_count = self.session.exec(citations_query).one()

        relationship_citations_query = (
            select(func.count())
            .select_from(RelationshipCitation)
            .where(RelationshipCitation.database_name == database_name)
        )
        relationship_citations_count = self.session.exec(relationship_citations_query).one()

        tags_query = (
            select(func.count())
            .select_from(SourceTag)
            .where(SourceTag.database_name == database_name)
        )
        tags_count = self.session.exec(tags_query).one()

        tag_assignments_query = (
            select(func.count())
            .select_from(SourceTagAssignment)
            .where(SourceTagAssignment.database_name == database_name)
        )
        tag_assignments_count = self.session.exec(tag_assignments_query).one()

        # Delete all data (order matters: delete children first, then parents)

        # Delete tag assignments
        self.session.exec(
            delete(SourceTagAssignment).where(SourceTagAssignment.database_name == database_name)
        )

        # Delete citations
        self.session.exec(
            delete(SourceCitation).where(SourceCitation.database_name == database_name)
        )

        # Delete relationship citations
        self.session.exec(
            delete(RelationshipCitation).where(RelationshipCitation.database_name == database_name)
        )

        # Delete chunks
        self.session.exec(delete(DocumentChunk).where(DocumentChunk.database_name == database_name))

        # Delete tags
        self.session.exec(delete(SourceTag).where(SourceTag.database_name == database_name))

        # Delete sources
        self.session.exec(delete(SourceRow).where(SourceRow.database_name == database_name))

        self._maybe_commit()

        return {
            "sources_deleted": sources_count,
            "chunks_deleted": chunks_count,
            "citations_deleted": citations_count,
            "relationship_citations_deleted": relationship_citations_count,
            "tags_deleted": tags_count,
            "tag_assignments_deleted": tag_assignments_count,
        }

    def delete_citations_by_source(self, source_id: str) -> dict[str, int]:
        """Delete all entity and relationship citations for a source.

        Used for idempotent commit: cleans up previously created citations
        before re-committing.

        Args:
            source_id: Source ID whose citations should be deleted.

        Returns:
            Dict with entity_citations_deleted and relationship_citations_deleted counts.

        """
        self._ensure_connected()

        entity_result = self.session.exec(
            delete(SourceCitation).where(SourceCitation.source_id == source_id)
        )
        entity_count = entity_result.rowcount  # type: ignore[union-attr]

        rel_result = self.session.exec(
            delete(RelationshipCitation).where(RelationshipCitation.source_id == source_id)
        )
        rel_count = rel_result.rowcount  # type: ignore[union-attr]

        self._maybe_commit()

        logger.info(
            "citations_deleted_by_source",
            source_id=source_id,
            entity_citations_deleted=entity_count,
            relationship_citations_deleted=rel_count,
        )

        return {
            "entity_citations_deleted": entity_count,
            "relationship_citations_deleted": rel_count,
        }

    def delete_citations_for_source(self, source_id: str) -> None:
        """Delete entity + relationship citation rows owned by this source."""
        self._ensure_connected()
        self.session.exec(delete(SourceCitation).where(SourceCitation.source_id == source_id))
        self.session.exec(
            delete(RelationshipCitation).where(RelationshipCitation.source_id == source_id)
        )
        self._maybe_commit()

    # ================================
    # Orphan Detection (for entity cleanup)
    # ================================

    def get_orphaned_entity_uris(self, source_id: str) -> list[str]:
        """Get entity URIs that will be orphaned when source is deleted.

        Returns URIs where this source is the ONLY remaining citation.
        Entities with citations from other sources are excluded.

        Args:
            source_id: Source UUID to check

        Returns:
            List of entity URIs that would become orphans

        """
        self._ensure_connected()

        # Step 1: Get all entity URIs cited by this source
        cited_by_source_stmt = (
            select(SourceCitation.entity_uri)
            .where(SourceCitation.source_id == source_id)
            .distinct()
        )
        cited_by_source = set(self.session.exec(cited_by_source_stmt).all())

        if not cited_by_source:
            return []

        # Step 2: Get entity URIs that have citations from OTHER sources
        cited_by_others_stmt = (
            select(SourceCitation.entity_uri)
            .where(
                SourceCitation.source_id != source_id,
                SourceCitation.entity_uri.in_(cited_by_source),
            )
            .distinct()
        )
        cited_by_others = set(self.session.exec(cited_by_others_stmt).all())

        # Step 3: Return the difference (orphans = cited by source - cited by others)
        orphaned = cited_by_source - cited_by_others
        return list(orphaned)

    def get_entity_uris_for_sources(self, source_ids: list[str]) -> list[str]:
        """Get all entity URIs cited by the given sources.

        Used for source-filtered export to determine which entities to include.

        Args:
            source_ids: List of source UUIDs

        Returns:
            List of unique entity URIs

        """
        self._ensure_connected()

        if not source_ids:
            return []

        stmt = (
            select(SourceCitation.entity_uri)
            .where(SourceCitation.source_id.in_(source_ids))
            .distinct()
        )
        results = self.session.exec(stmt).all()
        return list(results)

    def get_entity_uris_grouped_by_source(
        self, database_name: str, source_ids: list[str]
    ) -> dict[str, list[str]]:
        """Get entity URIs grouped by source ID.

        Returns a dict mapping each source_id to its unique entity URIs.
        Uses a single query with DISTINCT for efficiency.

        Args:
            database_name: Current database name.
            source_ids: Source IDs to query.

        Returns:
            Dict mapping source_id to list of unique entity URIs.

        """
        if not source_ids:
            return {}

        self._ensure_connected()

        stmt = (
            select(SourceCitation.source_id, SourceCitation.entity_uri)
            .where(
                SourceCitation.database_name == database_name,
                SourceCitation.source_id.in_(source_ids),
            )
            .distinct()
        )
        results = self.session.exec(stmt).all()

        grouped: dict[str, list[str]] = {}
        for source_id, entity_uri in results:
            grouped.setdefault(source_id, []).append(entity_uri)
        return grouped

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 2).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def clear_all_citations(self) -> int:
        """Delete every SourceCitation row across the database."""
        self._ensure_connected()
        result = self.session.exec(delete(SourceCitation))
        self._maybe_commit()
        return int(result.rowcount or 0)

    def delete_all_relationship_citations(self) -> int:
        """Delete every RelationshipCitation row across the database."""
        self._ensure_connected()
        result = self.session.exec(delete(RelationshipCitation))
        self._maybe_commit()
        return int(result.rowcount or 0)
