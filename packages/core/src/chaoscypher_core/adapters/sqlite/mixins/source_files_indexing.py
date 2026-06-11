# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Indexing and Lifecycle Mixin for SqliteAdapter.

Handles source status transitions (indexing, extraction, commit stages),
progress tracking, entity embeddings, and extraction queue gating.
Part of the unified SourceStorageProtocol implementation.
"""

import base64
from datetime import UTC, datetime
from typing import Any

import numpy as np
import structlog
from sqlalchemy import func, update
from sqlalchemy.orm import load_only
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import (
    LLMStageProgress,
    SourceEntity,
    SourceEntityEmbedding,
    SourceRelationship,
    SourceRow,
)
from chaoscypher_core.models import SourceErrorStage, SourceStatus
from chaoscypher_core.ports.storage_embeddings import EntityEmbeddingStorageProtocol
from chaoscypher_core.ports.storage_extraction_queue import ExtractionQueueStorageProtocol
from chaoscypher_core.utils.id import generate_id


logger = structlog.get_logger(__name__)


# Phase 3 note: SourceIndexingMixin is a god-mixin satisfying 3 protocols
# (ExtractionQueueStorageProtocol, EntityEmbeddingStorageProtocol, and the
# lifecycle half of SourceStorageProtocol). Splitting it into 3 focused
# mixins is deferred to Phase 3.
class SourceIndexingMixin(
    SqliteMixinBase,
    ExtractionQueueStorageProtocol,
    EntityEmbeddingStorageProtocol,
):
    """Mixin providing source lifecycle status operations for SQLite storage.

    Implements operations for:
    - Source status management (indexing, extraction, commit stages)
    - Progress tracking for UI
    - Entity embeddings storage and retrieval
    - Extraction queue gating (single-extraction-at-a-time)

    Note: This mixin contributes to the unified SourceStorageProtocol.
    """

    # Source lifecycle status tracking methods
    def start_indexing(self, source_id: str) -> None:
        """Mark file as starting indexing stage."""
        self._ensure_connected()
        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return

        source.status = SourceStatus.INDEXING
        source.indexing_started_at = datetime.now(UTC)
        source.error_message = None
        source.error_stage = None

        self.session.add(source)
        self._maybe_commit()

    def complete_indexing(
        self, source_id: str, chunks_count: int, embedding_model: str, embedding_dimensions: int
    ) -> None:
        """Mark indexing stage as complete."""
        self._ensure_connected()

        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return

        source.status = SourceStatus.INDEXED
        source.indexing_complete = True
        source.indexing_completed_at = datetime.now(UTC)
        source.chunk_count = chunks_count
        source.embedding_model = embedding_model
        source.embedding_dimensions = embedding_dimensions
        source.current_step = 0
        source.total_steps = 0
        source.step_description = ""

        self.session.add(source)
        self._maybe_commit()

    def _apply_failure(
        self,
        source: SourceRow,
        *,
        stage: SourceErrorStage,
        error: str,
    ) -> None:
        """Shared failure-write: ERROR status + cleared progress + cleared job ref.

        Centralizes the lifecycle write so fail_indexing / fail_extraction /
        fail_commit cannot drift apart on what they clear. UI relies on the
        progress fields being zeroed so an errored source does not keep
        rendering "Indexing 1/2" forever.

        ``stage`` must be a ``SourceErrorStage`` member so the persisted
        string is always locked to the enum definition.
        """
        source.status = SourceStatus.ERROR
        source.error_stage = stage.value
        source.error_message = error
        source.current_step = 0
        source.total_steps = 0
        source.step_description = ""
        source.current_extraction_job_id = None
        self.session.add(source)
        self._maybe_commit()

    def fail_indexing(self, source_id: str, error: str) -> None:
        """Mark indexing stage as failed."""
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source:
            return
        self._apply_failure(source, stage=SourceErrorStage.INDEXING, error=error)

    def start_extraction(self, source_id: str, depth: str = "full") -> None:
        """Mark file as starting extraction stage."""
        self._ensure_connected()
        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return

        source.status = SourceStatus.EXTRACTING
        source.extraction_started_at = datetime.now(UTC)
        source.extraction_depth = depth
        source.error_message = None
        source.error_stage = None

        self.session.add(source)
        self._maybe_commit()

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

        Replaces every existing ``source_entities`` / ``source_relationships``
        row for this source with the supplied lists, in a single
        transaction. Relationship dicts use integer indices into the
        ``entities`` list (the legacy chunk-extractor shape); this method
        resolves them to entity FKs at write time.

        Raises InvalidStateError if called on a source that already has
        ``commit_complete=True``. The committed source is authoritative;
        applying new extraction results would silently discard work, so we
        raise instead (audit fix #H5). Callers that intend to re-extract a
        committed source must use ``force_re_extract`` to reset first.

        Args:
            source_id: Unique source identifier.
            entities: Deduplicated entity dicts (legacy shape — name,
                type, confidence, plus arbitrary attribute keys).
            relationships: Relationship dicts. ``source`` / ``target``
                must be integer indices into ``entities``; out-of-range
                indices are dropped with a warning log.
            detected_domain: Domain inferred by the extraction pipeline.
            forced_domain: Operator-specified domain override.
            domain_version: Plugin version this source extracted under.
            domain_content_hash: sha256 of the plugin content at extraction time.
            cross_chunk_filtering_log: Cross-chunk filtering diagnostics
                surfaced by the "Filtering" UI tab. Optional.

        Raises:
            InvalidStateError: When the source is already committed
                (``commit_complete=True``).
        """
        from chaoscypher_core.exceptions import InvalidStateError

        self._ensure_connected()

        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return

        if source.commit_complete:
            msg = (
                f"Source {source_id!r} is already committed; cannot apply "
                f"new extraction results without resetting first. Use "
                f"force_re_extract if a re-extract is intended."
            )
            logger.error(
                "complete_extraction_rejected_already_committed",
                source_id=source_id,
                status=source.status,
            )
            raise InvalidStateError(msg)

        database_name = source.database_name

        self.replace_source_extraction(
            source_id=source_id,
            database_name=database_name,
            entities=entities,
            relationships=relationships,
        )

        source.status = SourceStatus.EXTRACTED
        source.extraction_complete = True
        source.extraction_completed_at = datetime.now(UTC)
        source.extraction_entities_count = len(entities)
        source.extraction_relationships_count = len(relationships)
        source.cross_chunk_filtering_log = cross_chunk_filtering_log

        # Set domain info: use forced_domain if set, otherwise detected_domain
        source.extraction_domain = forced_domain or detected_domain
        source.extraction_domain_auto = forced_domain is None
        source.domain_version = domain_version
        source.domain_content_hash = domain_content_hash

        # Clear stale job reference now that extraction is complete
        source.current_extraction_job_id = None
        source.current_step = 0
        source.total_steps = 0
        source.step_description = ""

        self.session.add(source)
        self._maybe_commit()

    def replace_source_extraction(
        self,
        *,
        source_id: str,
        database_name: str,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
    ) -> None:
        """Wipe and re-populate ``source_entities`` + ``source_relationships`` for a source.

        Uses ``session.bulk_insert_mappings`` for both tables — a 30k-row
        extraction would otherwise stall on per-row ORM machinery. The
        deletes + inserts run in the session's current transaction so the
        whole replacement is atomic with the caller's enclosing
        ``adapter.transaction()`` block when present.

        Relationship dicts come from the chunk extractor with integer
        ``source`` / ``target`` indices into the entities list. We resolve
        them to the generated entity IDs and drop relationships pointing
        at out-of-range indices with a warning log (mirrors
        ``aggregate_chunk_results``'s drop-on-bad-index behaviour upstream).

        Args:
            source_id: Source whose extraction rows are being replaced.
            database_name: Database scope (mirrored on every row for
                tenant filtering).
            entities: Entity dicts (legacy shape).
            relationships: Relationship dicts referencing entity indices.
        """
        self._ensure_connected()

        session = self.session
        assert session is not None
        session.exec(
            delete(SourceRelationship).where(
                SourceRelationship.source_id == source_id,
                SourceRelationship.database_name == database_name,
            )
        )
        session.exec(
            delete(SourceEntity).where(
                SourceEntity.source_id == source_id,
                SourceEntity.database_name == database_name,
            )
        )

        now = datetime.now(UTC)

        entity_ids: list[str] = [generate_id("ent") for _ in entities]
        if entities:
            entity_payload: list[dict[str, Any]] = []
            for ordinal, entity in enumerate(entities):
                if not isinstance(entity, dict):
                    continue
                ent_id = entity_ids[ordinal]
                attributes = {
                    k: v for k, v in entity.items() if k not in {"id", "name", "type", "confidence"}
                }
                entity_payload.append(
                    {
                        "id": ent_id,
                        "source_id": source_id,
                        "database_name": database_name,
                        "ordinal": ordinal,
                        "name": str(entity.get("name", "")),
                        "type": entity.get("type"),
                        "confidence": entity.get("confidence"),
                        "attributes": attributes or None,
                        "created_at": now,
                    }
                )
            if entity_payload:
                session.bulk_insert_mappings(SourceEntity, entity_payload)

        if relationships:
            rel_payload: list[dict[str, Any]] = []
            dropped = 0
            for ordinal, rel in enumerate(relationships):
                if not isinstance(rel, dict):
                    dropped += 1
                    continue
                src_idx = rel.get("source")
                tgt_idx = rel.get("target")
                if (
                    not isinstance(src_idx, int)
                    or not isinstance(tgt_idx, int)
                    or isinstance(src_idx, bool)
                    or isinstance(tgt_idx, bool)
                    or src_idx < 0
                    or tgt_idx < 0
                    or src_idx >= len(entity_ids)
                    or tgt_idx >= len(entity_ids)
                ):
                    dropped += 1
                    continue
                attributes = {
                    k: v
                    for k, v in rel.items()
                    if k
                    not in {
                        "id",
                        "source",
                        "target",
                        "predicate",
                        "type",
                        "confidence",
                    }
                }
                # Honor either ``predicate`` or the legacy ``type`` key —
                # both flow through the chunk extractor.
                predicate = rel.get("predicate") or rel.get("type")
                rel_payload.append(
                    {
                        "id": generate_id("rel"),
                        "source_id": source_id,
                        "database_name": database_name,
                        "ordinal": ordinal,
                        "source_entity_id": entity_ids[src_idx],
                        "target_entity_id": entity_ids[tgt_idx],
                        "predicate": predicate,
                        "confidence": rel.get("confidence"),
                        "attributes": attributes or None,
                        "created_at": now,
                    }
                )
            if dropped:
                logger.warning(
                    "replace_source_extraction_dropped_invalid_relationships",
                    source_id=source_id,
                    dropped=dropped,
                )
            if rel_payload:
                session.bulk_insert_mappings(SourceRelationship, rel_payload)

        self._maybe_commit()

    def assert_extractable(self, source_id: str, database_name: str) -> None:
        """Raise InvalidStateError if the source cannot accept new extraction.

        Called at the top of the extraction handler before any LLM work so
        we do not waste tokens on a source that is already committed. Audit
        fix #H5.

        Args:
            source_id: Unique source identifier.
            database_name: Database the source belongs to.

        Raises:
            InvalidStateError: When the source is already committed
                (``commit_complete=True``).
        """
        from chaoscypher_core.exceptions import InvalidStateError

        self._ensure_connected()
        statement = select(SourceRow).where(
            SourceRow.id == source_id,
            SourceRow.database_name == database_name,
        )
        source = self.session.exec(statement).first()
        if not source:
            return  # let downstream raise NotFoundError

        if source.commit_complete:
            msg = (
                f"Source {source_id!r} is already committed; "
                f"call reset_for_re_extraction before re-extracting."
            )
            raise InvalidStateError(msg)

    def fail_extraction(self, source_id: str, error: str) -> None:
        """Mark extraction stage as failed."""
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source:
            return
        self._apply_failure(source, stage=SourceErrorStage.EXTRACTION, error=error)

    def start_commit(self, source_id: str) -> None:
        """Mark file as starting commit stage."""
        self._ensure_connected()
        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return

        source.status = SourceStatus.COMMITTING
        source.commit_started_at = datetime.now(UTC)
        source.error_message = None
        source.error_stage = None

        self.session.add(source)
        self._maybe_commit()

    def complete_commit(
        self,
        source_id: str,
        nodes_created: int,
        edges_created: int,
        templates_created: int,
        source_document_node_id: str | None = None,
    ) -> None:
        """Mark commit stage as complete (unified schema)."""
        self._ensure_connected()

        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return

        # Update commit status
        source.status = SourceStatus.COMMITTED
        source.commit_complete = True
        source.commit_completed_at = datetime.now(UTC)
        source.commit_nodes_created = nodes_created
        source.commit_edges_created = edges_created
        source.commit_templates_created = templates_created
        source.source_document_node_id = source_document_node_id

        # Clear stale job reference now that commit is complete
        source.current_extraction_job_id = None
        source.current_step = 0
        source.total_steps = 0
        source.step_description = ""

        self.session.add(source)
        self._maybe_commit()

    def fail_commit(self, source_id: str, error: str) -> None:
        """Mark commit stage as failed."""
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source:
            return
        self._apply_failure(source, stage=SourceErrorStage.COMMIT, error=error)

    def cancel_extraction(self, source_id: str) -> None:
        """Revert an in-flight extraction back to INDEXED.

        Used when the user cancels an extraction job: the source stays
        valid (chunks + embeddings were already created during indexing),
        so the status returns to INDEXED rather than ERROR. Step progress,
        the stale job reference, and extraction_complete are all cleared
        so the UI stops showing a half-finished pipeline (audit fix #6
        defensive).

        Args:
            source_id: Source whose extraction was cancelled.
        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)

        if not source:
            return

        source.status = SourceStatus.INDEXED
        source.extraction_complete = False
        source.current_step = 0
        source.total_steps = 0
        source.step_description = ""
        source.current_extraction_job_id = None

        self.session.add(source)
        self._maybe_commit()

    def reset_to_indexed_for_re_extract(self, source_id: str) -> None:
        """Reset a non-ERROR, non-COMMITTED source back to INDEXED for re-extraction.

        State-machine method handling the
        ``INDEXED / EXTRACTED / EXTRACTING / MCP_EXTRACTING / COMMITTING``
        re-extract paths that previously routed through a bare
        ``update_file({"status": SourceStatus.INDEXED, ...})`` dict write
        in cortex.features.sources.service.re_extract — bypassing the
        adapter's state-machine discipline (now enforced by a status-writer
        lint test in the cortex test suite).

        ``reset_for_retry`` cannot be used for these statuses because its
        ``WHERE status = 'error'`` guard rejects everything except ERROR.

        Clears:
          - extraction_complete + commit_complete (re-extract redoes both)
          - current_extraction_job_id (any running handler will discover
            its slot has been reassigned and exit on its next checkpoint)
          - step progress fields
          - any prior error_message / error_stage

        The caller is responsible for clearing ``commit_payload``
        (via ``clear_source_commit_payload``) and quality counters
        (via ``reset_quality_counters``) — those live in separate mixins
        and have their own transaction lifecycles, matching the prior
        inline behaviour.

        Args:
            source_id: Source to reset back to INDEXED.
        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)

        if not source:
            return

        source.status = SourceStatus.INDEXED
        source.extraction_complete = False
        source.commit_complete = False
        source.current_extraction_job_id = None
        source.current_step = 0
        source.total_steps = 0
        source.step_description = ""
        source.error_message = None
        source.error_stage = None

        self.session.add(source)
        self._maybe_commit()

    def abort_processing(
        self,
        source_id: str,
        error_stage: str,
        error_message: str | None = None,
    ) -> None:
        """Abort any in-flight processing stage and flag the source as ERROR.

        Unlike ``fail_*`` which describes a handler reporting its own
        failure, ``abort_processing`` is driven from outside — the UI
        cancels queued/running tasks and then flips the source to
        ``ERROR`` so the state can be retried from a known-stable
        starting point. Step progress and the stale extraction-job
        reference are cleared.

        Args:
            source_id: Source whose pipeline was aborted.
            error_stage: Stage that was in flight at abort time (e.g.
                ``"indexing"``, ``"extraction"``, ``"commit"``).
            error_message: Optional human-readable reason to persist.
        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)

        if not source:
            return

        source.status = SourceStatus.ERROR
        source.error_stage = error_stage
        if error_message is not None:
            source.error_message = error_message
        source.current_step = 0
        source.total_steps = 0
        source.step_description = ""
        source.current_extraction_job_id = None

        self.session.add(source)
        self._maybe_commit()

    def update_step_progress(
        self,
        source_id: str,
        current_step: int,
        total_steps: int,
        step_description: str = "",
    ) -> None:
        """Update file processing progress."""
        self._ensure_connected()
        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return

        source.current_step = current_step
        source.total_steps = total_steps
        source.step_description = step_description

        self.session.add(source)
        self._maybe_commit()

    def count_sources_by_statuses(
        self,
        *,
        statuses: list[str],
        database_name: str,
    ) -> int:
        """Return a COUNT of sources whose status is in the given set.

        Issues a single ``SELECT COUNT(*) … WHERE status IN (…) AND
        database_name = ?`` — no row materialization. Used by the
        dashboard-badge path (``SourceRecovery.count_awaiting_confirmation``)
        where loading every parked row into Python would be wasteful at the
        "thousands of parked sources" scale the badge exists to surface.

        Args:
            statuses: Status values to include.
            database_name: Active database (multi-DB isolation).

        Returns:
            Integer count of matching rows.
        """
        self._ensure_connected()
        statement = (
            select(func.count())
            .select_from(SourceRow)
            .where(
                SourceRow.database_name == database_name,
                SourceRow.status.in_(statuses),
            )
        )
        result = self.session.exec(statement)
        return int(result.one())

    def list_sources_by_statuses(
        self,
        *,
        statuses: list[str],
        database_name: str,
    ) -> list[dict[str, Any]]:
        """List sources whose status is in the given set.

        Used by the SourceRecovery reconciler to find
        non-terminal sources that need their missing queue work re-
        dispatched after a worker crash. Uses ``load_only()`` column
        projection — recovery only needs id, status, timing,
        and activity columns, not the heavy extraction_results JSON.

        Args:
            statuses: Status values to include
                (e.g. ``["pending", "indexing", "extracting"]``).
            database_name: Active database (multi-DB isolation).

        Returns:
            List of source dicts ordered by ``last_activity_at``
            ascending so the most stalled sources come first.
        """
        self._ensure_connected()
        statement = (
            select(SourceRow)
            .options(
                load_only(
                    SourceRow.id,
                    SourceRow.database_name,
                    SourceRow.status,
                    SourceRow.last_activity_at,
                    SourceRow.recovery_attempts,
                    SourceRow.current_step,
                    SourceRow.step_description,
                    SourceRow.error_message,
                    SourceRow.error_stage,
                    SourceRow.is_paused,
                    SourceRow.auto_analyze,
                    SourceRow.extraction_depth,
                    SourceRow.filename,
                    SourceRow.filepath,
                    SourceRow.file_type,
                    SourceRow.indexing_complete,
                    SourceRow.extraction_complete,
                    SourceRow.commit_complete,
                    SourceRow.detection_proposal,
                    SourceRow.confirmation_required,
                )
            )
            .where(
                SourceRow.database_name == database_name,
                SourceRow.status.in_(statuses),
            )
            .order_by(SourceRow.last_activity_at.asc())
        )
        rows = self.session.scalars(statement).all()
        return [d for d in (self._entity_to_dict(r) for r in rows) if d is not None]

    def increment_source_recovery_attempts(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> None:
        """Atomically bump ``SourceRow.recovery_attempts`` by 1.

        Called by the SourceRecovery reconciler after each successful
        dispatch so the UI can surface "this source has been recovered
        N times" and so runaway recovery loops can be detected and
        debounced. Uses a raw UPDATE so the increment is atomic even
        under concurrent recovery runs.

        Args:
            source_id: Source being recovered.
            database_name: Active database (scopes the update).
        """
        self._ensure_connected()
        statement = (
            update(SourceRow)
            .where(
                SourceRow.id == source_id,
                SourceRow.database_name == database_name,
            )
            .values(recovery_attempts=SourceRow.recovery_attempts + 1)
        )
        self.session.execute(statement)
        self._maybe_commit()

    def reset_source_recovery_attempts(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> None:
        """Atomically zero ``SourceRow.recovery_attempts``.

        Called by stage-entry handlers (``finalize_extraction_handler``,
        commit) so a successful stage transition discards accumulated
        false-positive recoveries from the prior stage. Without this,
        the counter monotonically climbs across the source's lifetime
        and a healthy source that progresses through every stage can hit
        the 10-attempt exhaustion cap from compounded prior-stage noise.

        Idempotent: a UPDATE setting recovery_attempts=0 on an already-zero
        row is a no-op write but makes no semantic difference. Scoped to
        ``database_name`` to mirror ``update_source_last_activity``.

        Args:
            source_id: Source whose counter to reset.
            database_name: Active database (multi-DB isolation).
        """
        self._ensure_connected()
        statement = (
            update(SourceRow)
            .where(
                SourceRow.id == source_id,
                SourceRow.database_name == database_name,
            )
            .values(recovery_attempts=0)
        )
        self.session.execute(statement)
        self._maybe_commit()

    def update_source_last_activity(
        self,
        *,
        source_id: str,
        database_name: str,
        at_time: datetime,
    ) -> None:
        """Touch the source's ``last_activity_at`` timestamp.

        Called by resumable handlers (indexing, analysis, extraction,
        commit) at every checkpoint so the source-reconciler can tell
        which in-flight sources have actually made forward progress
        versus which have stalled and need re-dispatch.

        Uses a plain UPDATE (no row-load round-trip) because this is a
        tight-loop checkpoint on the hot path.

        Args:
            source_id: Source to touch.
            database_name: Active database (scopes the update to prevent
                cross-database collisions when multiple workers share an
                adapter cache).
            at_time: Timestamp to write.
        """
        self._ensure_connected()
        statement = (
            update(SourceRow)
            .where(
                SourceRow.id == source_id,
                SourceRow.database_name == database_name,
            )
            .values(last_activity_at=at_time)
        )
        self.session.execute(statement)
        self._maybe_commit()

    def store_entity_embeddings(
        self,
        source_id: str,
        embeddings_data: list[dict[str, Any]],
        embedding_model: str,
        embedding_dimensions: int,
        batch_size: int = 100,
    ) -> None:
        """Store entity embeddings for file.

        Args:
            source_id: The source processing file ID
            embeddings_data: List of embedding dictionaries
            embedding_model: Name of the embedding model used
            embedding_dimensions: Dimensions of the embeddings
            batch_size: Number of embeddings to commit per batch (default: 100).
                Batching prevents long-held database locks during large imports.

        """
        self._ensure_connected()

        # Store embeddings in batches to avoid holding database lock too long
        # With 100 embeddings per batch, each commit holds lock for ~0.5s instead of 10-30s
        for i, data in enumerate(embeddings_data):
            embedding_id = f"{source_id}_{data['entity_index']}"

            # Convert numpy array to base64-encoded bytes
            embedding_array = np.array(data["embedding"], dtype=np.float32)
            embedding_bytes = base64.b64encode(embedding_array.tobytes())

            entity_embedding = SourceEntityEmbedding(
                id=embedding_id,
                source_id=source_id,
                entity_index=data["entity_index"],
                entity_id=data.get("entity_id"),
                embedding=embedding_bytes,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
            )

            self.session.add(entity_embedding)

            # Commit every batch_size rows to release database lock
            if (i + 1) % batch_size == 0:
                self._maybe_commit()
                logger.debug(
                    "embedding_batch_committed",
                    source_id=source_id,
                    batch_number=(i + 1) // batch_size,
                    total_so_far=i + 1,
                )

        # Commit any remaining embeddings
        self._maybe_commit()

        # Update file metadata
        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if source:
            source.embeddings_generated = True
            source.embeddings_count = len(embeddings_data)
            source.embeddings_model = embedding_model
            source.embeddings_generated_at = datetime.now(UTC)
            self.session.add(source)

        self._maybe_commit()

    def delete_entity_embeddings_for_source(self, source_id: str) -> None:
        """Delete SourceEntityEmbedding rows owned by this source."""
        self._ensure_connected()
        stmt = delete(SourceEntityEmbedding).where(SourceEntityEmbedding.source_id == source_id)
        self.session.execute(stmt)
        self._maybe_commit()

    def get_entity_embeddings(self, source_id: str) -> list[dict[str, Any]]:
        """Get entity embeddings for file."""
        self._ensure_connected()
        statement = (
            select(SourceEntityEmbedding)
            .where(SourceEntityEmbedding.source_id == source_id)
            .order_by(SourceEntityEmbedding.entity_index)
        )

        embeddings = list(self.session.exec(statement).all())

        # Decode embeddings. The column is typed `bytes` and writes go
        # through base64.b64encode(...) which returns bytes — SQLAlchemy
        # round-trips a BLOB column as bytes, so the runtime type is
        # always bytes. The previous str-coercion branch was dead code
        # (mypy correctly flagged its siblings as unreachable).
        result = []
        for embedding_entity in embeddings:
            embedding_bytes = base64.b64decode(embedding_entity.embedding)
            embedding_array = np.frombuffer(embedding_bytes, dtype=np.float32)

            result.append(
                {
                    "source_id": embedding_entity.source_id,
                    "entity_index": embedding_entity.entity_index,
                    "entity_id": embedding_entity.entity_id,
                    "embedding": embedding_array.tolist(),
                }
            )

        return result

    def get_stats(self, database_name: str) -> dict[str, Any]:
        """Get source processing stats for database using SQL aggregation."""
        self._ensure_connected()

        statement = (
            select(
                SourceRow.status,
                func.count().label("count"),
                func.coalesce(func.sum(SourceRow.file_size), 0).label("total_size"),
            )
            .where(SourceRow.database_name == database_name)
            .group_by(SourceRow.status)
        )
        rows = self.session.exec(statement).all()

        by_status: dict[str, int] = {}
        total_files = 0
        total_size_bytes = 0
        for status, count, size in rows:
            by_status[str(status)] = int(count)
            total_files += int(count)
            total_size_bytes += int(size)

        return {
            "total_files": total_files,
            "by_status": by_status,
            "total_size_bytes": total_size_bytes,
        }

    # ================================
    # Extraction Queue Gating Methods
    # ================================

    def get_extracting_source_count(self, database_name: str) -> int:
        """Get count of sources currently in 'extracting' or 'mcp_extracting' status.

        Used to gate new extractions - only one source should extract at a time.

        Args:
            database_name: Database context

        Returns:
            Count of sources with status='extracting' or 'mcp_extracting'
        """
        self._ensure_connected()
        statement = (
            select(func.count())
            .select_from(SourceRow)
            .where(SourceRow.database_name == database_name)
            .where(SourceRow.status.in_([SourceStatus.EXTRACTING, SourceStatus.MCP_EXTRACTING]))
        )
        result = self.session.exec(statement)
        return result.one()

    def try_claim_extraction(self, source_id: str, database_name: str, depth: str = "full") -> bool:
        """Atomically claim extraction slot if no other source is extracting.

        Uses a single UPDATE ... WHERE query to avoid TOCTOU race conditions
        when multiple workers check-then-set concurrently.

        Args:
            source_id: Source to claim extraction for
            database_name: Database context
            depth: Extraction depth level

        Returns:
            True if this source claimed the extraction slot, False if another
            source is already extracting.
        """
        self._ensure_connected()

        # Subquery: count of currently-extracting sources (excluding this one)
        extracting_count = (
            select(func.count())
            .select_from(SourceRow)
            .where(SourceRow.database_name == database_name)
            .where(SourceRow.status.in_([SourceStatus.EXTRACTING, SourceStatus.MCP_EXTRACTING]))
            .where(SourceRow.id != source_id)
            .scalar_subquery()
        )

        # Atomic UPDATE: only update if no other source is extracting
        stmt = (
            update(SourceRow)
            .where(SourceRow.id == source_id)
            .where(extracting_count == 0)
            .values(
                status=SourceStatus.EXTRACTING,
                extraction_started_at=datetime.now(UTC),
                extraction_depth=depth,
            )
        )
        result = self.session.execute(stmt)
        self._maybe_commit()

        # rowcount == 1 means we claimed the slot; 0 means another source is extracting
        return result.rowcount == 1

    def mark_extraction_waiting(self, source_id: str, file_info: dict[str, Any]) -> None:
        """Mark a source as waiting for extraction.

        Sets extraction_queued_at timestamp to track queue order.
        Stores file_info for later use when extraction starts.

        Atomicity invariant
        -------------------
        Both ``extraction_queued_at`` and ``extraction_pending_file_info`` are
        written to the same ``SourceRow`` instance and persisted in a single
        UPDATE inside ``_maybe_commit()``. SQLite commits the row atomically:
        if the process crashes mid-commit the WAL transaction rolls back and
        readers see neither column changed; if the commit succeeds both columns
        flip together.

        This means the reconciler in
        ``chaoscypher_core.services.sources.recovery`` always observes one of
        two states for a source — both fields ``NULL`` (not yet queued) or both
        fields populated (queued and waiting). It will never see a half-written
        row with ``extraction_queued_at`` set but ``extraction_pending_file_info``
        empty (or vice versa). Tests that assert pairwise behaviour can rely
        on this guarantee.

        Args:
            source_id: Source ID to mark as waiting
            file_info: File info dict to store for later extraction
        """
        self._ensure_connected()
        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            logger.warning("source_not_found_for_waiting", source_id=source_id)
            return

        source.extraction_queued_at = datetime.now(UTC)
        # Store file_info in a JSON field for later retrieval
        source.extraction_pending_file_info = file_info

        self.session.add(source)
        self._maybe_commit()

        logger.info(
            "source_marked_waiting_extraction",
            source_id=source_id,
            extraction_queued_at=source.extraction_queued_at.isoformat(),
        )

    def get_oldest_waiting_extraction(self, database_name: str) -> dict[str, Any] | None:
        """Get the oldest source waiting for extraction.

        Finds sources that:
        - Have status='indexed' (ready for extraction)
        - Have extraction_queued_at set (waiting in queue)

        Args:
            database_name: Database context

        Returns:
            Source dict with file_info or None if no waiting sources
        """
        self._ensure_connected()
        statement = (
            select(SourceRow)
            .where(SourceRow.database_name == database_name)
            .where(SourceRow.status == SourceStatus.INDEXED)
            .where(SourceRow.extraction_queued_at.isnot(None))
            .order_by(SourceRow.extraction_queued_at.asc())
            .limit(1)
        )
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return None

        return self._entity_to_dict(source)

    def clear_extraction_waiting(self, source_id: str) -> None:
        """Clear the extraction waiting flag after extraction starts.

        Args:
            source_id: Source ID to clear waiting flag
        """
        self._ensure_connected()
        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()

        if not source:
            return

        source.extraction_queued_at = None
        source.extraction_pending_file_info = None

        self.session.add(source)
        self._maybe_commit()

    def mark_source_exhausted(
        self,
        source_id: str,
        database_name: str,
        error_message: str,
    ) -> None:
        """Transition source to status=error with stage=recovery_exhausted.

        Used by SourceRecovery when max_recovery_attempts is hit. Leaves
        recovery_attempts intact so operators can see the number of
        attempts that led to exhaustion.

        Args:
            source_id: Source to mark exhausted.
            database_name: Active database (scopes the update).
            error_message: Human-readable message summarising the failure.
        """
        self._ensure_connected()

        # Read the current error_stage before overwriting it so we can preserve
        # where the source actually failed (idempotent: skip if already
        # RECOVERY_EXHAUSTED so re-exhausting keeps the original prior stage).
        existing = self.session.get(SourceRow, source_id)
        prior_stage: str | None = None
        if existing is not None:
            current_stage = existing.error_stage
            if (
                current_stage is not None
                and current_stage != SourceErrorStage.RECOVERY_EXHAUSTED.value
            ):
                prior_stage = current_stage
            else:
                # Already exhausted — preserve whatever was stored previously.
                prior_stage = existing.last_failed_stage

        update_values: dict[str, object] = {
            "status": SourceStatus.ERROR,
            "error_stage": SourceErrorStage.RECOVERY_EXHAUSTED.value,
            "error_message": error_message,
            "current_extraction_job_id": None,
            "current_step": 0,
            "total_steps": 0,
            "step_description": "",
        }
        if prior_stage is not None:
            update_values["last_failed_stage"] = prior_stage

        stmt = (
            update(SourceRow)
            .where(
                SourceRow.id == source_id,
                SourceRow.database_name == database_name,
            )
            .values(**update_values)
        )
        self.session.execute(stmt)
        self._maybe_commit()

    def reset_for_retry(
        self,
        source_id: str,
        database_name: str,
        new_status: str,
        *,
        clear_commit_payload: bool = False,
    ) -> None:
        """Reset an errored source for manual retry.

        Atomically (within SQLAlchemy UoW):
        - Sets status = new_status
        - Clears error_message and error_stage
        - Resets recovery_attempts to 0

        Used by the manual-retry API endpoint. The caller is responsible
        for dispatching the appropriate queue task after this returns.

        Args:
            source_id: Source to reset.
            database_name: Database context.
            new_status: Target status (e.g. 'pending', 'indexed', 'extracted').
            clear_commit_payload: When True, also NULL out the
                ``commit_payload`` column. Callers MUST pass True whenever
                the retry transitions the source back through extraction
                (PENDING / INDEXED) — otherwise a stale payload from the
                previous extraction attempt could later land in the graph
                ahead of the freshly-extracted data. The "retry commit
                only" path (target = EXTRACTED) leaves the payload intact
                because that *is* the data the caller wants to retry with.
                Audit fix #F44.
        """
        self._ensure_connected()
        # Wrap the whole reset in an adapter.transaction() so the source-row
        # mutation and the orphan-job cascade commit (or roll back) as a
        # single unit. Without this, ``cancel_extraction_job_cascade``'s
        # mid-method ``_maybe_commit()`` would commit the cancel in its
        # own transaction and the trailing ``_maybe_commit()`` below would
        # commit the source reset in another — two windows where a crash
        # could leave the row and the orphan job out of sync. Audit fix #F53.
        with self.transaction():  # type: ignore[attr-defined]  # resolved at runtime via SqliteAdapter mixin composition
            statement = select(SourceRow).where(
                SourceRow.id == source_id,
                SourceRow.database_name == database_name,
                SourceRow.status == "error",  # guard: only reset errored sources
            )
            source = self.session.exec(statement).first()
            if source is None:
                # Source not found, is in a different database, or has already
                # been reset (e.g. by a concurrent retry request).  Treat as
                # idempotent success — the caller need not distinguish these cases.
                return

            # Capture the orphan job pointer BEFORE clearing it so we can cancel
            # the job + its non-terminal tasks atomically with the source reset
            # (audit fix #F53). Otherwise the recovery reconciler — which filters
            # by job status, not by source pointer — will re-dispatch the chunks
            # of an "abandoned" job and cause duplicate extraction.
            orphan_job_id = source.current_extraction_job_id

            source.status = new_status
            source.error_message = None
            source.error_stage = None
            source.recovery_attempts = 0
            # Clear forward-looking completion flags so SourceRecovery._classify
            # short-circuits don't skip re-dispatch on a status the user just
            # asked to redo. Audit fix #H2.
            if new_status == SourceStatus.PENDING:
                source.indexing_complete = False
                source.extraction_complete = False
                source.commit_complete = False
            elif new_status == SourceStatus.INDEXED:
                source.extraction_complete = False
                source.commit_complete = False
            elif new_status == SourceStatus.EXTRACTED:
                source.commit_complete = False
            source.current_extraction_job_id = None
            source.current_step = 0
            source.total_steps = 0
            source.step_description = ""
            if clear_commit_payload:
                source.commit_payload = None
            self.session.add(source)

            # Cancel the orphan job (if any) inside the SAME unit-of-work.
            # ``cancel_extraction_job_cascade`` is a no-op when the job is
            # already terminal, so this stays idempotent under concurrent
            # retry requests.
            if orphan_job_id is not None:
                self.cancel_extraction_job_cascade(orphan_job_id)  # type: ignore[attr-defined]  # resolved at runtime via SqliteAdapter mixin composition

        # Phase 2 (2026-05-08): match reset_for_re_extraction's pattern.
        # ERROR-state retry must drain quality counters so a re-run starts
        # from a clean slate, same as INDEXED/EXTRACTED/etc. paths fixed
        # in Phase 1.  Called OUTSIDE the transaction() block because
        # reset_quality_counters issues its own UPDATE via update_source_columns
        # which calls _maybe_commit(); nesting it inside the transaction()
        # context manager would conflict.  The counter reset is best-effort
        # (logs and continues on failure) so a mid-reset failure must not
        # orphan the source-row changes committed above.
        # Function-local import avoids a top-level circular import: same
        # rationale as the matching block in reset_for_re_extraction.
        from chaoscypher_core.services.quality.counters import reset_quality_counters

        reset_quality_counters(self, source_id, database_name)

    def reset_for_re_extraction(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> None:
        """Reset a committed source so it can be re-extracted from scratch.

        Used by trigger_extraction(force=True) on COMMITTED sources. Clears
        extraction + commit completion flags, counts, timestamps, the
        cached per-source entity / relationship rows, and routes the
        source back to INDEXED so the analysis handler can claim the
        extraction slot.

        Does NOT touch chunks, embeddings, or the source file itself —
        those remain valid (indexing is unchanged). The graph artifacts
        (nodes, edges, templates created by the prior commit) are deleted
        by the caller via ``graph_repository.delete_source_artifacts``
        BEFORE this method is called.

        Atomicity: callers MUST wrap this method together with
        graph_repository.delete_source_artifacts in a single
        adapter.transaction() block. The
        services.sources.management.re_extraction.force_re_extract
        helper does this correctly — prefer it over invoking the two
        methods directly.

        Idempotent: running on a non-committed source is a no-op write.
        """
        self._ensure_connected()
        source = self.session.get(SourceRow, source_id)
        if not source or source.database_name != database_name:
            return

        # Drop the per-source entity/relationship rows. FK CASCADE would
        # collect them when the source is deleted, but a re-extract keeps
        # the source row — so we must wipe explicitly here. Deleting
        # entities first lets the relationships' FK CASCADE drop them in
        # the same statement set without an out-of-order constraint
        # violation. Explicit deletes on both tables stay
        # implementation-defensive.
        self.session.exec(
            delete(SourceRelationship).where(
                SourceRelationship.source_id == source_id,
                SourceRelationship.database_name == database_name,
            )
        )
        self.session.exec(
            delete(SourceEntity).where(
                SourceEntity.source_id == source_id,
                SourceEntity.database_name == database_name,
            )
        )

        # Clear stale stage-timing rows so the Data Quality tab's
        # stage display doesn't carry EMA-smoothed avg_ms from the
        # discarded run into the next extraction (see TODO: force_re_extract
        # leaves orphaned llm_stage_progress rows).
        self.session.exec(delete(LLMStageProgress).where(LLMStageProgress.source_id == source_id))

        source.status = SourceStatus.INDEXED
        source.extraction_complete = False
        source.commit_complete = False
        source.cross_chunk_filtering_log = None
        # Defensive: clear any pending commit_payload. On a successful commit
        # this is already None (commit handler clears it atomically with the
        # write), but a partial-failure mid-commit can leave a stale payload
        # behind that must not survive the re-extraction. Audit fix #F44.
        source.commit_payload = None
        source.extraction_entities_count = 0
        source.extraction_relationships_count = 0
        source.extraction_started_at = None
        source.extraction_completed_at = None
        source.commit_started_at = None
        source.commit_completed_at = None
        source.commit_nodes_created = 0
        source.commit_edges_created = 0
        source.commit_templates_created = 0
        source.source_document_node_id = None
        source.current_extraction_job_id = None
        source.current_step = 0
        source.total_steps = 0
        source.step_description = ""
        source.error_message = None
        source.error_stage = None
        source.recovery_attempts = 0

        self.session.add(source)
        self._maybe_commit()

        # Workstream 2 (2026-05-07): clear every per-stage drop / merge
        # counter and the vector-search status so the re-extracted graph
        # starts from a clean slate.  ``reset_quality_counters`` is
        # best-effort (logs and continues on failure); counter visibility
        # is observability, not control flow, so a mid-reset failure must
        # not orphan the source halfway between INDEXED and the prior
        # COMMITTED graph.  Function-local import avoids a top-level
        # circular import: ``counters.py`` types its adapter parameter
        # via a Protocol, but the adapter mixins live alongside this
        # file, so importing the service at module load would round-trip
        # back through the adapter package.
        from chaoscypher_core.services.quality.counters import reset_quality_counters

        reset_quality_counters(self, source_id, database_name)

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 5).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_embeddings(self) -> int:
        """Count all SourceEntityEmbedding rows."""
        self._ensure_connected()
        stmt = select(func.count()).select_from(SourceEntityEmbedding)
        return int(self.session.exec(stmt).one())

    def clear_all_embeddings(self) -> int:
        """Delete every SourceEntityEmbedding row."""
        self._ensure_connected()
        result = self.session.exec(delete(SourceEntityEmbedding))
        self._maybe_commit()
        return int(result.rowcount or 0)
