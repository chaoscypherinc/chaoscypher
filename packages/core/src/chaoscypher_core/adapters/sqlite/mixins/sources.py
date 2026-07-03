# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Storage Protocol Mixin for SqliteAdapter.

Provides core Source CRUD operations (get, create, update, delete, list, count).
Implements the SourceStorageProtocol for framework-agnostic source persistence.

Related mixins (split for maintainability):
- sources_tags.py: Tag CRUD and tag-to-source assignments
- sources_chunks.py: Document chunk CRUD, batch operations, hierarchical grouping
- sources_citations.py: Entity/relationship citations, stats, orphan detection, bulk clear
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import InstrumentedAttribute, aliased, load_only
from sqlmodel import delete, func, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import (
    SourceEntity,
    SourceRelationship,
    SourceRow,
    SourceTagAssignment,
)
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.ports.storage_sources import SourceStorageProtocol


logger = structlog.get_logger(__name__)


# Columns excluded from get_source() because they can be large
# (commit_payload can be MBs of pending entity/relationship data, even
# though per-source entity/relationship rows now live in dedicated
# tables since migration 0042). Callers needing the commit payload use
# the dedicated accessor ``get_source_commit_payload``.
_HEAVY_SOURCE_COLUMNS: frozenset[str] = frozenset({"commit_payload"})


def _light_source_columns() -> list[InstrumentedAttribute[Any]]:
    """Return every SourceRow column attribute except the heavy ones.

    Computed dynamically from the SQLAlchemy mapper so newly added
    columns are picked up automatically — no hand-maintained list.
    """
    return [
        getattr(SourceRow, attr.key)
        for attr in sa_inspect(SourceRow).column_attrs
        if attr.key not in _HEAVY_SOURCE_COLUMNS
    ]


class SourcesMixin(SqliteMixinBase, SourceStorageProtocol):
    """Mixin implementing core Source CRUD for SQLite storage.

    Implements the SourceStorageProtocol with operations for:
    - Source get/create/update/delete
    - Source listing with filtering and pagination
    - Source counting

    Related mixins provide additional operations:
    - SourceTagsMixin: Tag CRUD and assignments
    - SourceChunksMixin: Document chunk operations
    - SourceCitationsMixin: Citation operations, stats, orphan detection
    """

    def get_source(self, source_id: str, database_name: str = "") -> dict[str, Any] | None:
        """Get source by ID and database.

        Uses ``load_only()`` to exclude the large ``commit_payload`` text
        column. Per-source entity/relationship rows live in the dedicated
        ``source_entities`` / ``source_relationships`` tables (migration
        0042) — fetch them through ``get_source_entities_page`` /
        ``get_source_relationships_page``.

        Populates ``stage_progress`` from ``llm_stage_progress`` (empty
        dict when no stages are active or completed).
        """
        self._ensure_connected()
        statement = (
            select(SourceRow)
            .where(SourceRow.id == source_id, SourceRow.database_name == database_name)
            .options(load_only(*_light_source_columns()))
        )
        row = self.session.exec(statement).first()
        if row is None:
            return None
        result = self._entity_to_dict(row)
        if result is None:
            # _entity_to_dict's signature is dict | None, but a non-None row
            # always converts to a non-None dict — narrow the type for mypy.
            return None
        result["stage_progress"] = self._fetch_stage_progress(source_id)
        return result

    def get_source_extraction_metadata(
        self, source_id: str, database_name: str
    ) -> dict[str, Any] | None:
        """Return source-level extraction metadata without entity/relationship rows.

        Narrow projection (id, extraction_domain, chunk_count,
        cross_chunk_filtering_log) used by the cortex quality and entity-
        list services to obtain the domain context they need before
        calling the per-source entity/relationship readers.

        Returns ``None`` when the source does not exist.
        """
        self._ensure_connected()
        statement = (
            select(SourceRow)
            .where(SourceRow.id == source_id, SourceRow.database_name == database_name)
            .options(
                load_only(
                    SourceRow.id,
                    SourceRow.extraction_domain,
                    SourceRow.chunk_count,
                    SourceRow.cross_chunk_filtering_log,
                )
            )
        )
        source = self.session.exec(statement).first()
        if not source:
            return None
        return {
            "extraction_domain": source.extraction_domain,
            "chunk_count": source.chunk_count,
            "cross_chunk_filtering_log": source.cross_chunk_filtering_log,
        }

    @staticmethod
    def _entity_row_to_dict(row: SourceEntity) -> dict[str, Any]:
        """Project a ``SourceEntity`` row into the legacy entity-dict shape.

        Returns the same keys the old in-blob entities carried so cortex
        consumers (quality scorer, response envelope) do not need to
        change. Attribute keys override the canonical scalars only when
        not already present.
        """
        attributes = row.attributes or {}
        result: dict[str, Any] = {
            "id": row.id,
            "name": row.name,
            "type": row.type,
            "confidence": row.confidence,
        }
        for key, value in attributes.items():
            if key not in result or result[key] is None:
                result[key] = value
        return result

    @staticmethod
    def _relationship_row_to_dict(
        rel: SourceRelationship,
        *,
        source_name: str | None,
        target_name: str | None,
    ) -> dict[str, Any]:
        """Project a ``SourceRelationship`` row + joined entity names into a dict.

        The ``from`` / ``to`` keys carry the resolved entity names so the
        cortex relationship-list endpoint does not need a second pass to
        enrich them.
        """
        attributes = rel.attributes or {}
        result: dict[str, Any] = {
            "id": rel.id,
            "source": rel.source_entity_id,
            "target": rel.target_entity_id,
            "predicate": rel.predicate,
            "type": rel.predicate,
            "confidence": rel.confidence,
            "from": source_name,
            "to": target_name,
        }
        for key, value in attributes.items():
            if key not in result or result[key] is None:
                result[key] = value
        return result

    def get_source_entities_page(
        self,
        source_id: str,
        database_name: str,
        *,
        page: int = 1,
        per_page: int = 50,
        sort_by: str = "default",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Return one page of extracted entities for a source.

        ``sort_by`` values:

        * ``"default"`` — order by ``ordinal`` (preserves the original
          extraction-order shown in legacy responses).
        * ``"confidence"`` — order by ``confidence``.
        * ``"name"`` — order by ``LOWER(name)``.
        * ``"type"`` — order by ``LOWER(COALESCE(type, ''))``.
        * ``"quality"`` — sort-mode handled by the caller because the
          score is computed from multiple entity fields plus
          source-level context; the adapter just returns the full
          entity set unpaginated for that case (callers detect via the
          ``total`` count and ``returned == total``).

        Returns ``{"entities": [...], "total": int}``. ``entities``
        already match the legacy dict shape callers expect.
        """
        self._ensure_connected()
        base_stmt = select(SourceEntity).where(
            SourceEntity.source_id == source_id,
            SourceEntity.database_name == database_name,
        )
        total = int(self.session.exec(select(func.count()).select_from(base_stmt.subquery())).one())
        if total == 0:
            return {"entities": [], "total": 0}

        descending = sort_order != "asc"
        order_col: Any
        if sort_by == "confidence":
            order_col = SourceEntity.confidence
        elif sort_by == "name":
            order_col = func.lower(SourceEntity.name)
        elif sort_by == "type":
            order_col = func.lower(func.coalesce(SourceEntity.type, ""))
        else:
            order_col = SourceEntity.ordinal

        order_clause = order_col.desc() if descending else order_col.asc()
        # Tie-break on ordinal asc for deterministic pagination.
        stmt = base_stmt.order_by(order_clause, SourceEntity.ordinal.asc())

        per_page = max(per_page, 1)
        page = max(page, 1)
        offset = (page - 1) * per_page
        stmt = stmt.offset(offset).limit(per_page)
        rows = list(self.session.exec(stmt).all())
        entities = [self._entity_row_to_dict(row) for row in rows]
        return {"entities": entities, "total": total}

    def list_source_entities(
        self,
        source_id: str,
        database_name: str,
    ) -> list[dict[str, Any]]:
        """Return every entity for a source, in extraction order.

        Used by the quality-sort path (which needs the full set to compute
        scores before paginating) and by the commit-recovery path (which
        rebuilds the commit payload).
        """
        self._ensure_connected()
        stmt = (
            select(SourceEntity)
            .where(
                SourceEntity.source_id == source_id,
                SourceEntity.database_name == database_name,
            )
            .order_by(SourceEntity.ordinal.asc())
        )
        rows = list(self.session.exec(stmt).all())
        return [self._entity_row_to_dict(row) for row in rows]

    def get_source_relationships_page(
        self,
        source_id: str,
        database_name: str,
        *,
        page: int = 1,
        per_page: int = 50,
    ) -> dict[str, Any]:
        """Return one page of extracted relationships joined to entity names.

        Joins ``source_entities`` on both ``source_entity_id`` and
        ``target_entity_id`` so the ``from`` / ``to`` names are filled in
        at SQL time. Order is ``ordinal`` ascending so paginated reads
        match the legacy extraction-order.
        """
        self._ensure_connected()
        base_stmt = select(SourceRelationship).where(
            SourceRelationship.source_id == source_id,
            SourceRelationship.database_name == database_name,
        )
        total = int(self.session.exec(select(func.count()).select_from(base_stmt.subquery())).one())
        if total == 0:
            return {"relationships": [], "total": 0}

        per_page = max(per_page, 1)
        page = max(page, 1)
        offset = (page - 1) * per_page

        source_alias = aliased(SourceEntity)
        target_alias = aliased(SourceEntity)
        stmt = (
            select(SourceRelationship, source_alias.name, target_alias.name)
            .where(
                SourceRelationship.source_id == source_id,
                SourceRelationship.database_name == database_name,
            )
            .join(
                source_alias,
                source_alias.id == SourceRelationship.source_entity_id,
                isouter=True,
            )
            .join(
                target_alias,
                target_alias.id == SourceRelationship.target_entity_id,
                isouter=True,
            )
            .order_by(SourceRelationship.ordinal.asc())
            .offset(offset)
            .limit(per_page)
        )

        rows = self.session.exec(stmt).all()
        relationships: list[dict[str, Any]] = []
        for rel, from_name, to_name in rows:
            relationships.append(
                self._relationship_row_to_dict(
                    rel,
                    source_name=from_name,
                    target_name=to_name,
                )
            )
        return {"relationships": relationships, "total": total}

    def list_source_relationships(
        self,
        source_id: str,
        database_name: str,
    ) -> list[dict[str, Any]]:
        """Return every relationship for a source, in extraction order."""
        self._ensure_connected()
        source_alias = aliased(SourceEntity)
        target_alias = aliased(SourceEntity)
        stmt = (
            select(SourceRelationship, source_alias.name, target_alias.name)
            .where(
                SourceRelationship.source_id == source_id,
                SourceRelationship.database_name == database_name,
            )
            .join(
                source_alias,
                source_alias.id == SourceRelationship.source_entity_id,
                isouter=True,
            )
            .join(
                target_alias,
                target_alias.id == SourceRelationship.target_entity_id,
                isouter=True,
            )
            .order_by(SourceRelationship.ordinal.asc())
        )
        rows = self.session.exec(stmt).all()
        return [
            self._relationship_row_to_dict(rel, source_name=from_name, target_name=to_name)
            for rel, from_name, to_name in rows
        ]

    def set_source_commit_payload(
        self,
        source_id: str,
        payload: dict[str, Any],
        database_name: str,
    ) -> None:
        """Persist a pending commit payload on a source row.

        Used by the extraction finalizer (and the manual re-commit retry path)
        to stash the large ``commit_data`` dict in the database before
        enqueueing ``OP_IMPORT_COMMIT`` — the queue payload then only needs
        to carry the source id. Keeps Valkey memory flat regardless of
        document size.

        The payload is stored as a JSON string in the ``commit_payload``
        TEXT column. Writes participate in any enclosing
        ``adapter.transaction()`` via ``_maybe_commit()``.

        Args:
            source_id: Target source ID.
            payload: Commit-data dict (entities, relationships, templates,
                etc.). Must be JSON-serializable.
            database_name: Database scope for the source.

        Raises:
            NotFoundError: If the source does not exist in the given
                database.
        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source or source.database_name != database_name:
            msg = "Source"
            raise NotFoundError(msg, source_id)
        source.commit_payload = json.dumps(payload)
        source.updated_at = datetime.now(UTC)
        self.session.add(source)
        self._maybe_commit()

    def get_source_commit_payload(
        self,
        source_id: str,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Read the pending commit payload for a source.

        Loads only the ``commit_payload`` column (narrow projection)
        without touching any other row data. The commit handler calls
        this at the top of its dispatch path to hydrate the payload
        that the extraction finalizer stashed earlier.

        Args:
            source_id: Source ID to read.
            database_name: Database scope for the source.

        Returns:
            The stashed commit-data dict, or ``None`` if the source does
            not exist or has no pending payload.
        """
        self._ensure_connected()
        statement = (
            select(SourceRow)
            .where(SourceRow.id == source_id, SourceRow.database_name == database_name)
            .options(load_only(SourceRow.id, SourceRow.commit_payload))
        )
        source = self.session.exec(statement).first()
        if not source or not source.commit_payload:
            return None
        try:
            decoded = json.loads(source.commit_payload)
        except json.JSONDecodeError:
            logger.warning(
                "commit_payload_decode_failed",
                source_id=source_id,
                database_name=database_name,
            )
            return None
        return decoded if isinstance(decoded, dict) else None

    def clear_source_commit_payload(
        self,
        source_id: str,
        database_name: str,
    ) -> None:
        """Clear the pending commit payload for a source.

        Called by the commit handler after a successful commit, inside
        the same transaction that performs the graph write — if the
        commit fails the payload stays for the next retry; if it
        succeeds the payload is discarded atomically with the source
        status transition.

        Args:
            source_id: Source ID to clear.
            database_name: Database scope for the source.
        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source or source.database_name != database_name:
            return
        if source.commit_payload is None:
            return
        source.commit_payload = None
        source.updated_at = datetime.now(UTC)
        self.session.add(source)
        self._maybe_commit()

    def create_source(self, source_data: dict[str, Any]) -> dict[str, Any]:
        """Create source."""
        self._ensure_connected()
        source = SourceRow(**source_data)
        self.session.add(source)
        self._maybe_commit()
        self.session.refresh(source)
        if not source:
            raise ValueError("Source creation failed")
        return self._entity_to_dict(source)

    def get_source_by_ccx_iri(self, ccx_iri: str, database_name: str) -> dict[str, Any] | None:
        """Look up a source by its stable CCX IRI.

        Scoped to ``(database_name, ccx_iri)``; returns the source row dict
        (including ``ccx_iri`` and ``full_text``) or ``None`` when no row in
        ``database_name`` carries that IRI.

        Args:
            ccx_iri: The CCX 3.0 stable IRI to match.
            database_name: Database that owns the source.
        """
        self._ensure_connected()
        statement = select(SourceRow).where(
            SourceRow.ccx_iri == ccx_iri,
            SourceRow.database_name == database_name,
        )
        row = self.session.exec(statement).first()
        if row is None:
            return None
        return self._entity_to_dict(row)

    def upsert_source_by_ccx_iri(
        self,
        ccx_iri: str,
        source_dict: dict[str, Any],
        database_name: str,
    ) -> dict[str, Any]:
        """Idempotently create or update a source keyed by CCX IRI.

        SELECT by ``(database_name, ccx_iri)``; if a row exists, UPDATE its
        mutable columns from ``source_dict`` (incoming-wins) and return it (no
        duplicate); otherwise CREATE a new ``SourceRow`` carrying the given
        ``ccx_iri``.

        ``source_dict`` is a plain column dict (e.g. ``id``, ``database_name``,
        ``filename``, ``title``, ``full_text``). On create, ``ccx_iri`` and
        ``database_name`` are forced to the supplied values regardless of what
        the dict carries; on update, immutable identity columns (``id``,
        ``database_name``, ``ccx_iri``, ``created_at``) are never overwritten.

        Args:
            ccx_iri: Stable CCX IRI used as the merge key.
            source_dict: Source column values.
            database_name: Database to scope the upsert to.

        Returns:
            The created or updated source row dict (including ``ccx_iri``).
        """
        self._ensure_connected()
        statement = select(SourceRow).where(
            SourceRow.ccx_iri == ccx_iri,
            SourceRow.database_name == database_name,
        )
        row = self.session.exec(statement).first()

        immutable = {"id", "database_name", "ccx_iri", "created_at"}

        if row is not None:
            for key, value in source_dict.items():
                if key in immutable:
                    continue
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = datetime.now(UTC)
            self.session.add(row)
            self._maybe_commit()
            self.session.refresh(row)
            updated = self._entity_to_dict(row)
            assert updated is not None  # a non-None row always converts
            return updated

        row_data = {key: value for key, value in source_dict.items() if key not in {"ccx_iri"}}
        row_data["database_name"] = database_name
        row_data["ccx_iri"] = ccx_iri
        row = SourceRow(**row_data)
        self.session.add(row)
        self._maybe_commit()
        self.session.refresh(row)
        created = self._entity_to_dict(row)
        assert created is not None  # a non-None row always converts
        return created

    def update_source(self, source_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update source fields.

        Args:
            source_id: Source identifier
            updates: Dictionary of fields to update

        Returns:
            Updated source dictionary

        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source:
            msg = "Source"
            raise NotFoundError(msg, source_id)

        # Update only provided fields (skip id, internal fields, and datetime fields)
        # Datetime fields must be skipped because get_source() returns them as ISO strings,
        # but SQLite expects datetime objects. We only want to update the specific fields
        # that were explicitly passed in the update request.
        skip_fields = {
            "id",
            "database_name",
            "created_at",
            "updated_at",  # Set separately below
            # Datetime fields returned as ISO strings from get_source()
            "indexing_started_at",
            "indexing_completed_at",
            "extraction_started_at",
            "extraction_completed_at",
            "extraction_queued_at",
            "embeddings_generated_at",
            "commit_started_at",
            "commit_completed_at",
            "cached_scores_at",
        }
        for key, value in updates.items():
            if key not in skip_fields and hasattr(source, key):
                setattr(source, key, value)

        source.updated_at = datetime.now(UTC)
        self.session.add(source)
        self._maybe_commit()
        self.session.refresh(source)
        assert source is not None
        result = self._entity_to_dict(source)
        assert result is not None
        return result

    def delete_source_files(self, filepath: str | None) -> None:
        """Delete the source's on-disk files.

        Separate from ``delete_source_db`` so callers can orchestrate
        file deletion AFTER a transaction commits (files cannot be
        rolled back if the transaction fails).

        Best-effort: logs a warning and returns on failure. Does not raise.

        Args:
            filepath: Absolute path to the source's staged file. The entire
                parent directory is removed. No-op if ``filepath`` is None,
                empty, NOT absolute, or the directory does not exist.

        """
        if not filepath:
            return
        path = Path(filepath)
        # Defence-in-depth: a real staged source file is ALWAYS an absolute path
        # under the data dir, so its parent is that source's own staged
        # directory. A relative / bare ``filepath`` — e.g. an imported source
        # whose "filepath" is a display name, not an on-disk file — makes
        # ``Path(filepath).parent`` resolve to the process working directory, so
        # ``rmtree`` wipes an unrelated directory (this once deleted the served
        # frontend at /app/static). Never delete for a non-absolute path.
        if not path.is_absolute():
            logger.warning("source_files_delete_skipped_relative", filepath=filepath)
            return
        file_dir = path.parent
        if not file_dir.exists():
            return
        try:
            shutil.rmtree(file_dir, ignore_errors=True)
            logger.debug("deleted_source_files", path=str(file_dir))
        except Exception:
            logger.warning("source_files_delete_failed", path=str(file_dir), exc_info=True)

    def delete_source(self, source_id: str, database_name: str = "") -> bool:
        """Delete source and its on-disk files (backward-compat wrapper).

        Prefer ``delete_source_db`` + ``delete_source_files`` separately
        when orchestrating inside a transaction. This method commits the
        SQL cascade and then deletes files, leaving no transaction boundary
        for callers to wrap.

        Cascade order (respects FK constraints):
        1. SourceCitations (references source_id and chunk_id)
        2. RelationshipCitations (references source_id and chunk_id)
        3. DocumentChunks (references source_id)
        4. SourceTagAssignments (references source_id)
        5. Entity embeddings (references source_id)
        6. Extraction jobs and tasks (references source_id)
        7. Source record itself
        8. Physical file on disk

        Args:
            source_id: ID of the source to delete
            database_name: Database name for the source

        Returns:
            True if source was deleted, False if not found

        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source or source.database_name != database_name:
            return False

        filepath = source.filepath
        deleted = self.delete_source_db(source_id, database_name=database_name)
        if deleted:
            self.delete_source_files(filepath)
        return deleted

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

        Uses load_only() to exclude user_metadata JSON column for faster queries.
        For full source data, use get_source().

        Args:
            page: Page number (1-indexed)
            page_size: Items per page
            source_type: Filter by type (document/url/note/etc)
            status: Filter by processing_status (ready/indexing/extracting/error)
            enabled: Filter by enabled status ('enabled' or 'disabled')
            search: Search in title/origin_url
            tag_id: Filter by tag assignment

        Returns:
            Tuple of (sources list, total count)

        """
        self._ensure_connected()
        # Exclude user_metadata (JSON) - rarely needed in list views
        stmt = (
            select(SourceRow)
            .options(
                load_only(
                    SourceRow.id,
                    SourceRow.database_name,
                    # File metadata
                    SourceRow.filename,
                    SourceRow.filepath,
                    SourceRow.file_type,
                    SourceRow.file_size,
                    SourceRow.version,
                    SourceRow.parent_id,
                    SourceRow.source_type,
                    SourceRow.title,
                    SourceRow.origin_url,
                    SourceRow.chunk_count,
                    SourceRow.total_content_length,
                    SourceRow.embedding_model,
                    SourceRow.embedding_dimensions,
                    SourceRow.status,
                    SourceRow.enabled,
                    SourceRow.error_message,
                    SourceRow.error_stage,
                    # Import stats (for tooltip display)
                    SourceRow.extraction_entities_count,
                    SourceRow.extraction_relationships_count,
                    SourceRow.commit_nodes_created,
                    SourceRow.commit_edges_created,
                    SourceRow.commit_templates_created,
                    # Processing timestamps (durations computed in mapper)
                    SourceRow.indexing_started_at,
                    SourceRow.indexing_completed_at,
                    SourceRow.extraction_started_at,
                    SourceRow.extraction_completed_at,
                    SourceRow.commit_started_at,
                    SourceRow.commit_completed_at,
                    SourceRow.created_at,
                    SourceRow.updated_at,
                    # LLM Metrics for success indicator and tooltip
                    SourceRow.llm_total_calls,
                    SourceRow.llm_first_try_successes,
                    SourceRow.llm_retry_successes,
                    SourceRow.llm_permanent_failures,
                    SourceRow.llm_total_input_tokens,
                    SourceRow.llm_total_output_tokens,
                    SourceRow.llm_model,
                    # Progress tracking (UI progress indicators)
                    SourceRow.current_step,
                    SourceRow.total_steps,
                    SourceRow.step_description,
                    # Domain
                    SourceRow.extraction_domain,
                    SourceRow.extraction_domain_auto,
                    SourceRow.extraction_depth,
                    SourceRow.forced_domain,
                    # MCP extraction progress
                    SourceRow.extraction_mode,
                    # Resumability observability
                    SourceRow.last_activity_at,
                    SourceRow.recovery_attempts,
                    # Per-source pause state
                    SourceRow.is_paused,
                    SourceRow.paused_at,
                    SourceRow.paused_reason,
                    # Vector-search visibility (Workstream 10) — surfaced
                    # flat on SourceSummaryResponse so the list view can
                    # render the SearchStatusBadge without loading the
                    # full QualityMetrics object.
                    SourceRow.vector_indexed_at,
                    SourceRow.vector_indexing_status,
                    # Domain confirmation gate (migration 0049) — surfaced so
                    # the awaiting-confirmation list view can render the
                    # proposal + actionable chip without a per-row get_source.
                    SourceRow.confirmation_required,
                    SourceRow.extraction_confirmed_at,
                    SourceRow.detection_proposal,
                    # EXCLUDE: user_metadata (JSON, rarely needed in list)
                )
            )
            .where(SourceRow.database_name == self.database_name)
        )

        if status is not None:
            stmt = stmt.where(SourceRow.status == status)
        if enabled is not None:
            # Filter by enabled status: 'enabled' or 'disabled'
            enabled_bool = enabled.lower() == "enabled"
            stmt = stmt.where(SourceRow.enabled == enabled_bool)
        if source_type is not None:
            stmt = stmt.where(SourceRow.source_type == source_type)
        if search is not None:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                (SourceRow.title.ilike(search_pattern))
                | (SourceRow.origin_url.ilike(search_pattern))
            )
        if tag_id is not None:
            # Join with tag assignments to filter by tag
            stmt = stmt.join(SourceTagAssignment).where(SourceTagAssignment.tag_id == tag_id)

        # Get total count before pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.session.exec(count_stmt).one()

        # Apply pagination and ordering
        offset = (page - 1) * page_size
        stmt = stmt.order_by(SourceRow.created_at.desc()).offset(offset).limit(page_size)

        results = self.session.exec(stmt)
        sources = self._entities_to_dicts(results.all())

        # Bulk-fetch stage_progress in one round trip for the whole page
        # (avoids N+1 queries per source row).  Empty page short-circuits
        # because ``_fetch_stage_progress_bulk([])`` would no-op anyway.
        if sources:
            source_ids = [s["id"] for s in sources]
            progress_by_source = self._fetch_stage_progress_bulk(source_ids)
            for s in sources:
                s["stage_progress"] = progress_by_source.get(s["id"], {})

        return sources, total

    def transition_source_status(
        self,
        source_id: str,
        from_status: str,
        to_status: str,
        *,
        database_name: str,
    ) -> bool:
        """Atomic compare-and-swap status transition, scoped to a single database.

        Only updates if both the current status matches ``from_status`` and the
        row belongs to ``database_name``.  Without the database scope a
        ``source_id`` collision across two databases (unlikely with UUID4 but
        possible after a manual import or migration bug) could silently update
        the wrong row.  Mirrors the scoping already applied to
        ``reset_for_retry`` and ``mark_source_exhausted``.

        Args:
            source_id: Source identifier.
            from_status: Expected current status.
            to_status: New status to set.
            database_name: Database that owns the source.  The WHERE clause
                filters on ``(id, status, database_name)`` so only the
                intended row can be updated.

        Returns:
            True if transition succeeded, False if status or database didn't
            match (or the row does not exist).

        """
        from sqlalchemy import update

        self._ensure_connected()
        result = self.session.execute(
            update(SourceRow)
            .where(
                SourceRow.id == source_id,
                SourceRow.status == from_status,
                SourceRow.database_name == database_name,
            )
            .values(status=to_status)
        )
        self._maybe_commit()
        return result.rowcount > 0

    def get_source_titles_by_ids(
        self,
        source_ids: list[str],
        database_name: str,
    ) -> dict[str, str]:
        """Fetch display titles for multiple sources in a single query.

        Returns a map from source id to the best available display label:
        ``title`` if set, else ``filename``. Sources not found in the
        database are absent from the returned map; callers decide how
        to render them (for example fall back to the raw id).

        Uses a narrow ``load_only`` projection (id, title, filename) so
        the heavy ``user_metadata`` JSON column is not loaded.

        Args:
            source_ids: Source IDs to fetch titles for.
            database_name: Database name for filtering.

        Returns:
            Dict mapping source_id to its display title.

        """
        if not source_ids:
            return {}

        self._ensure_connected()
        stmt = (
            select(SourceRow)
            .where(
                SourceRow.id.in_(source_ids),  # type: ignore[union-attr]
                SourceRow.database_name == database_name,
            )
            .options(
                load_only(
                    SourceRow.id,
                    SourceRow.title,
                    SourceRow.filename,
                )
            )
        )
        rows = self.session.exec(stmt).all()
        return {row.id: (row.title or row.filename) for row in rows}

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 4).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_sources(self, *, database_name: str) -> int:
        """Count SourceRow rows in one database."""
        self._ensure_connected()
        stmt = (
            select(func.count())
            .select_from(SourceRow)
            .where(SourceRow.database_name == database_name)
        )
        return int(self.session.exec(stmt).one())

    def delete_all_sources(self, *, database_name: str) -> int:
        """Delete every SourceRow in one database."""
        self._ensure_connected()
        stmt = delete(SourceRow).where(SourceRow.database_name == database_name)
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)
