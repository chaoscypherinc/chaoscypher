# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Deletion Orchestrator Mixin for SqliteAdapter.

Coordinates the cross-table cascade that must run when a source is
deleted. Each owning mixin exposes a ``delete_*_for_source(source_id)``
method that deletes only the rows it owns; this orchestrator fans out
to them in FK-safe order and finally removes the ``SourceRow`` itself.

All sibling deletes run through ``_maybe_commit()`` so a surrounding
``adapter.transaction()`` keeps the whole cascade atomic — individual
siblings flush but never commit mid-cascade.
"""

import structlog

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import SourceRow


logger = structlog.get_logger(__name__)


class SourceDeletionMixin(SqliteMixinBase):
    """Orchestrator for the source deletion cascade.

    Calls each owning mixin's ``delete_*_for_source`` method in FK-safe
    order, then deletes the ``SourceRow`` itself. Runs inside a single
    ``_maybe_commit()`` boundary so the cascade is atomic when wrapped
    in ``adapter.transaction()``.
    """

    def delete_source_db(self, source_id: str, database_name: str = "") -> bool:
        """SQL cascade delete of source and all related rows.

        Does NOT delete disk files — caller is responsible for files
        so they can be deleted outside the transaction boundary.

        Uses ``_maybe_commit()`` so it participates in enclosing
        ``adapter.transaction()`` contexts.

        Cascade order (respects FK constraints):
        1. SourceCitation + RelationshipCitation (owned by SourceCitationsMixin)
        2. DocumentChunk (owned by SourceChunksMixin)
        3. SourceTagAssignment (owned by SourceTagsMixin)
        4. SourceEntityEmbedding (owned by SourceIndexingMixin)
        5. ChunkExtractionTask (owned by SourceChunkTasksMixin — child of jobs)
        6. ChunkExtractionJob (owned by SourceExtractionJobsMixin)
        7. SourceRow itself (owned by SourcesMixin)

        Args:
            source_id: ID of the source to delete
            database_name: Database name for the source

        Returns:
            True if source was deleted, False if not found or wrong database

        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source or source.database_name != database_name:
            return False

        logger.info("deleting_source_db_cascade", source_id=source_id)

        # Fan out to owning mixins — each flushes but defers commit when
        # inside an adapter.transaction() boundary.
        self.delete_citations_for_source(source_id)  # type: ignore[attr-defined]
        self.delete_chunks_for_source(source_id)  # type: ignore[attr-defined]
        self.delete_tags_for_source(source_id)  # type: ignore[attr-defined]
        self.delete_entity_embeddings_for_source(source_id)  # type: ignore[attr-defined]
        # Tasks are children of jobs, so delete tasks first then jobs.
        self.delete_tasks_for_source(source_id)  # type: ignore[attr-defined]
        self.delete_extraction_jobs_for_source(source_id)  # type: ignore[attr-defined]

        # Flush before deleting the source row (FK constraints).
        self.session.flush()

        self.session.delete(source)
        self._maybe_commit()

        logger.info("source_db_deleted", source_id=source_id)
        return True
