# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunk Extraction Tasks Lifecycle Mixin.

Provides status transitions, progress tracking, LLM I/O tracking,
and timing statistics for chunk extraction tasks.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import delete as sqla_delete
from sqlalchemy import func
from sqlalchemy import update as sqla_update
from sqlalchemy.orm import load_only
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.mixins._extraction_job_query_base import (
    ExtractionJobQueryBase,
)
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionAttempt,
    ChunkExtractionJob,
    ChunkExtractionTask,
    SourceRow,
)
from chaoscypher_core.exceptions import ConflictError, NotFoundError
from chaoscypher_core.utils.id import generate_id


logger = structlog.get_logger(__name__)


class ChunkTasksLifecycleMixin(ExtractionJobQueryBase):
    """Mixin providing lifecycle and status transition operations for chunk tasks.

    Handles queuing, starting, completing, failing tasks, as well as
    progress summaries, timing statistics, and LLM I/O tracking.

    ``get_extraction_job`` is inherited from ``ExtractionJobQueryBase``.
    ``update_chunk_task`` still comes from the sibling ``ChunkTasksCRUDMixin``
    via the composed adapter class; declared here for type-checking only.
    """

    # -- Declared for type-checking; implemented by sibling mixin --
    def update_chunk_task(self, task_id: str, updates: dict[str, Any]) -> None:
        """Update chunk task fields (provided by ChunkTasksCRUDMixin)."""

    # -- Status transition methods --

    def mark_chunk_task_queued(self, task_id: str, queue_task_id: str) -> None:
        """Mark chunk task as queued with queue task ID.

        Args:
            task_id: Task identifier
            queue_task_id: Queue task ID
        """
        self.update_chunk_task(
            task_id,
            {
                "status": "queued",
                "queue_task_id": queue_task_id,
                "queued_at": datetime.now(UTC),
            },
        )

    def mark_chunk_tasks_queued_batch(self, task_queue_pairs: list[tuple[str, str]]) -> None:
        """Mark multiple chunk tasks as queued in a single transaction.

        Args:
            task_queue_pairs: List of (task_id, queue_task_id) tuples.
        """
        if not task_queue_pairs:
            return

        self._ensure_connected()
        now = datetime.now(UTC)
        task_ids = [pair[0] for pair in task_queue_pairs]
        queue_map = dict(task_queue_pairs)

        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id.in_(task_ids))
        tasks = list(self.session.exec(statement).all())

        for task in tasks:
            task.status = "queued"
            task.queue_task_id = queue_map[task.id]
            task.queued_at = now
            self.session.add(task)

        self._maybe_commit()

    def start_chunk_task(self, task_id: str) -> None:
        """Mark chunk task as running.

        Args:
            task_id: Task identifier
        """
        self.update_chunk_task(
            task_id,
            {
                "status": "running",
                "started_at": datetime.now(UTC),
            },
        )

    def complete_chunk_task(
        self,
        task_id: str,
        raw_entities: list[dict] | None = None,
        raw_relationships: list[dict] | None = None,
        invalid_relationship_count: int = 0,
    ) -> None:
        """Mark chunk task as completed with results.

        Args:
            task_id: Task identifier
            raw_entities: Extracted entities from this chunk
            raw_relationships: Extracted relationships from this chunk
            invalid_relationship_count: Number of relationships skipped due to invalid indices
        """
        entities = raw_entities or []
        relationships = raw_relationships or []

        self.update_chunk_task(
            task_id,
            {
                "status": "completed",
                "completed_at": datetime.now(UTC),
                "raw_entities": entities,
                "raw_relationships": relationships,
                "entity_count": len(entities),
                "relationship_count": len(relationships),
                "invalid_relationship_count": invalid_relationship_count,
            },
        )

    def fail_chunk_task(
        self, task_id: str, error_message: str, error_type: str = "unknown"
    ) -> None:
        """Mark chunk task as failed.

        Args:
            task_id: Task identifier
            error_message: Error description
            error_type: Error category
        """
        self._ensure_connected()
        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        result = self.session.exec(statement)
        task = result.first()

        if not task:
            logger.warning("chunk_task_not_found", task_id=task_id)
            return

        task.status = "failed"
        task.completed_at = datetime.now(UTC)
        task.error_message = error_message
        task.error_type = error_type
        task.retry_count += 1

        self.session.add(task)
        self._maybe_commit()
        self.session.refresh(task)

    # -- Progress and summary methods --

    def get_chunk_tasks_summary(self, job_id: str) -> dict[str, Any]:
        """Get aggregated summary of chunk tasks for a job.

        Args:
            job_id: Parent job identifier

        Returns:
            Summary with counts by status, total entities/relationships
        """
        self._ensure_connected()
        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.job_id == job_id)
        tasks = list(self.session.exec(statement).all())

        summary = {
            "total": len(tasks),
            "by_status": {
                "pending": 0,
                "queued": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
            },
            "total_entities": 0,
            "total_relationships": 0,
        }

        for task in tasks:
            status = task.status
            by_status = cast("dict[str, int]", summary["by_status"])
            by_status[status] = by_status.get(status, 0) + 1
            summary["total_entities"] += task.entity_count
            summary["total_relationships"] += task.relationship_count

        return summary

    def get_failed_chunk_tasks(self, job_id: str) -> list[dict[str, Any]]:
        """Get failed chunk tasks for retry.

        Args:
            job_id: Parent job identifier

        Returns:
            List of failed tasks that can be retried
        """
        self._ensure_connected()
        statement = (
            select(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job_id)
            .where(ChunkExtractionTask.status == "failed")
        )

        results = self.session.exec(statement)
        tasks = results.all()

        # Filter to tasks that haven't exceeded max retries
        retryable = [t for t in tasks if t.retry_count < t.max_retries]

        return self._entities_to_dicts(retryable)

    def get_running_chunk_task(self, job_id: str) -> dict[str, Any] | None:
        """Get the currently running chunk task for a job.

        Args:
            job_id: Parent job identifier

        Returns:
            Dict with running chunk info including retry_count and elapsed time,
            or None if no chunk is currently running.
        """
        self._ensure_connected()
        statement = (
            select(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job_id)
            .where(ChunkExtractionTask.status == "running")
            .order_by(ChunkExtractionTask.started_at.desc())
            .limit(1)
        )

        result = self.session.exec(statement).first()
        if not result:
            return None

        # Calculate elapsed time for this chunk
        elapsed_seconds = None
        if result.started_at:
            now = datetime.now(UTC)
            started = result.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            elapsed_seconds = (now - started).total_seconds()

        return {
            "chunk_index": result.chunk_index,
            "retry_count": result.retry_count,
            "max_retries": result.max_retries,
            "started_at": result.started_at.isoformat() if result.started_at else None,
            "elapsed_seconds": elapsed_seconds,
        }

    def get_completed_chunk_results(self, job_id: str) -> list[dict[str, Any]]:
        """Get all completed chunk results for aggregation.

        Args:
            job_id: Parent job identifier

        Returns:
            List of completed tasks with their extraction results
        """
        self._ensure_connected()
        statement = (
            select(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job_id)
            .where(ChunkExtractionTask.status == "completed")
            .order_by(ChunkExtractionTask.chunk_index)
        )

        results = self.session.exec(statement)
        return self._entities_to_dicts(results.all())

    def get_chunk_tasks_by_job(self, job_id: str) -> list[dict[str, Any]]:
        """Return all chunk tasks for a job, regardless of status.

        Unlike ``get_completed_chunk_results`` (which filters to completed
        only), this returns every task row so callers can inspect in-flight
        state. Used by the finalizer pre-aggregation guard to detect task
        rows that are still mid-flight (pending/queued/running) before
        aggregating results.

        Uses ``load_only`` to exclude large JSON columns (raw_entities,
        raw_relationships, input_text, llm_response_json) since callers
        only need status and id for the guard check.

        Args:
            job_id: Parent job identifier.

        Returns:
            List of all task dicts for the job, ordered by chunk_index.
        """
        self._ensure_connected()
        self.session.expire_all()
        statement = (
            select(ChunkExtractionTask)
            .options(
                load_only(
                    ChunkExtractionTask.id,
                    ChunkExtractionTask.job_id,
                    ChunkExtractionTask.database_name,
                    ChunkExtractionTask.chunk_index,
                    ChunkExtractionTask.status,
                    ChunkExtractionTask.retry_count,
                    ChunkExtractionTask.error_message,
                    # EXCLUDE: raw_entities, raw_relationships, input_text, llm_response_json
                )
            )
            .where(ChunkExtractionTask.job_id == job_id)
            .order_by(ChunkExtractionTask.chunk_index)
        )
        results = self.session.exec(statement)
        return self._entities_to_dicts(results.all())

    # -- Combined lifecycle methods (reduce per-chunk DB writes) --

    def start_chunk_task_with_input(self, task_id: str, input_text: str) -> None:
        """Mark chunk task as running and store input text in a single transaction.

        Combines ``start_chunk_task`` + ``update_chunk_task_input`` into one
        select-update-commit cycle, halving the DB writes at task start.

        Args:
            task_id: Task identifier.
            input_text: The combined content sent to LLM for extraction.
        """
        self._ensure_connected()

        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        result = self.session.exec(statement)
        task = result.first()

        if not task:
            logger.warning("chunk_task_not_found_for_start_input", task_id=task_id)
            return

        task.status = "running"
        task.started_at = datetime.now(UTC)
        task.input_text = input_text
        task.input_text_length = len(input_text)

        self.session.add(task)
        self._maybe_commit()

    def complete_chunk_task_with_output(
        self,
        task_id: str,
        llm_response_json: str,
        llm_duration_ms: int,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        context_window_available: int | None = None,
        raw_entities: list[dict] | None = None,
        raw_entity_embeddings: list[list[float]] | None = None,
        raw_relationships: list[dict] | None = None,
        invalid_relationship_count: int = 0,
        chunk_sentences: list[str] | None = None,
        filtering_log: dict | None = None,
        finish_reason: str | None = None,
        aborted_by_loop: bool | None = None,
    ) -> None:
        """Store LLM output and mark chunk task as completed in a single transaction.

        Combines ``update_chunk_task_output`` + ``complete_chunk_task`` into one
        select-update-commit cycle, halving the DB writes at task completion.

        Args:
            task_id: Task identifier.
            llm_response_json: Raw JSON response from LLM.
            llm_duration_ms: Duration of the LLM call in milliseconds.
            input_tokens: Actual input token count from LLM API.
            output_tokens: Actual output token count from LLM API.
            context_window_available: Context window available at extraction time.
            raw_entities: Extracted entities from this chunk.
            raw_relationships: Extracted relationships from this chunk.
            invalid_relationship_count: Number of relationships skipped.
            raw_entity_embeddings: Cached embeddings (parallel to raw_entities,
                one inner list per entity). Persisted in the same UPDATE as
                raw_entities so a finalize-handler crash doesn't trigger
                re-embedding the aggregated set on retry. Pass ``None`` when
                the embedding service is unavailable; finalize will backfill.
            chunk_sentences: Pre-split sentences from this chunk (reused in finalization).
            filtering_log: Pipeline filtering diagnostics (per-chunk stages).
            finish_reason: Normalized provider finish reason.
                One of ``stop`` / ``length`` / ``content_filter`` /
                ``tool_calls`` / ``error`` / ``unknown``. Persisted on
                the chunk task for per-chunk truncation visibility.
            aborted_by_loop: True when the streaming loop detector cut
                the LLM stream short on a degenerate pattern.
                Persisted for per-chunk degenerate-stream visibility.
        """
        self._ensure_connected()
        entities = raw_entities or []
        relationships = raw_relationships or []

        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        result = self.session.exec(statement)
        task = result.first()

        if not task:
            logger.warning("chunk_task_not_found_for_complete_output", task_id=task_id)
            return

        # Output fields
        task.llm_response_json = llm_response_json
        task.llm_response_length = len(llm_response_json)
        task.llm_duration_ms = llm_duration_ms
        task.input_tokens = input_tokens
        task.output_tokens = output_tokens
        task.context_window_available = context_window_available

        # Completion fields
        task.status = "completed"
        task.completed_at = datetime.now(UTC)
        task.raw_entities = entities  # type: ignore[assignment]
        task.raw_entity_embeddings = raw_entity_embeddings  # type: ignore[assignment]
        task.raw_relationships = relationships  # type: ignore[assignment]
        task.entity_count = len(entities)
        task.relationship_count = len(relationships)
        task.invalid_relationship_count = invalid_relationship_count
        if chunk_sentences is not None:
            task.chunk_sentences = chunk_sentences  # type: ignore[assignment]
        if filtering_log is not None:
            task.filtering_log = filtering_log  # type: ignore[assignment]
        # Persist Workstream 8 observability fields. Both tolerate None
        # so legacy callers (and tests) that don't pass them in keep
        # working.
        if finish_reason is not None:
            task.finish_reason = finish_reason
        if aborted_by_loop is not None:
            task.aborted_by_loop = aborted_by_loop

        self.session.add(task)
        self._maybe_commit()

    def set_chunk_task_embeddings(
        self,
        task_id: str,
        embeddings: list[list[float]] | None,
    ) -> None:
        """Backfill raw_entity_embeddings on a previously-completed chunk task.

        Used by the finalize-time backfill loop to fill rows that pre-date
        the schema change (raw_entities present, raw_entity_embeddings NULL).
        Single-column UPDATE; commits immediately so a mid-loop crash
        persists every row written so far.
        """
        self._ensure_connected()
        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        task = self.session.exec(statement).first()
        if not task:
            logger.warning("chunk_task_not_found_for_embedding_backfill", task_id=task_id)
            return
        task.raw_entity_embeddings = embeddings  # type: ignore[assignment]
        self.session.add(task)
        self._maybe_commit()

    def orphan_chunk_tasks_outside_range(
        self,
        *,
        job_id: str,
        database_name: str,
        max_chunk_index: int,
    ) -> int:
        """Orphan stale non-terminal chunk task rows at chunk_index >= max_chunk_index.

        Bulk UPDATE that transitions ``status`` to ``orphaned`` for any task in
        the job whose ``chunk_index`` lies beyond the current group set. Used by
        ``_upsert_extraction_tasks`` to clean up after a re-analysis pass that
        produced fewer hierarchical groups than a previous pass on the same job.

        Without this, leftover rows at indices ``>= len(groups)`` stay in
        ``pending``/``queued`` and the SourceRecovery reconciler thrashes them
        every 60 s — eventually flipping the source to
        ``error: recovery_exhausted``. This was the root cause of the loop on
        source fa992140-….

        Terminal rows (``completed``, ``failed``, ``cancelled``, ``orphaned``)
        are deliberately preserved so prior work and prior failure context are
        never trampled.

        Args:
            job_id: Parent extraction job.
            database_name: Active database (defensive scoping; ``job_id`` is
                already unique, but the same predicate appears on sibling
                bulk-update helpers and keeps the SQL shape consistent).
            max_chunk_index: Threshold from the current analysis pass — usually
                ``len(hierarchical_groups)``. Rows with
                ``chunk_index >= max_chunk_index`` are eligible for orphaning.

        Returns:
            Number of rows transitioned to ``orphaned``. Zero is the common case.
        """
        self._ensure_connected()

        stmt = (
            sqla_update(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job_id)
            .where(ChunkExtractionTask.database_name == database_name)
            .where(ChunkExtractionTask.chunk_index >= max_chunk_index)
            .where(ChunkExtractionTask.status.in_(("pending", "queued", "running")))
            .values(
                status="orphaned",
                error_message=(
                    "Stale task: chunk_index beyond current analysis groups "
                    f"(threshold={max_chunk_index})."
                ),
            )
        )
        result = self.session.execute(stmt)
        self._maybe_commit()
        rowcount: int = result.rowcount

        if rowcount:
            logger.info(
                "chunk_tasks_orphaned_outside_range",
                job_id=job_id,
                database_name=database_name,
                max_chunk_index=max_chunk_index,
                orphaned=rowcount,
            )
        return rowcount

    def cleanup_orphaned_chunk_tasks(self, *, older_than_seconds: int) -> int:
        """Delete chunk extraction tasks in 'orphaned' state older than the cutoff.

        Orphaned tasks are created by BE-7's cascade update when an
        ExtractionJob fails with non-terminal tasks still pending/running.
        Without cleanup they accumulate forever.

        The cutoff is applied against ``created_at``. Since orphaned tasks
        are always at least as old as the job failure that produced them,
        using ``created_at`` as the age signal is conservative and correct:
        a task created 7+ days ago that is still orphaned is definitively
        stale. The ``ChunkExtractionTask`` model has no ``updated_at`` column.

        Args:
            older_than_seconds: Delete rows whose created_at is older than
                this many seconds ago. The neuron worker passes
                ``retention_days * 86400``.

        Returns:
            Number of rows deleted.
        """
        self._ensure_connected()

        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        stmt = (
            sqla_delete(ChunkExtractionTask)
            .where(ChunkExtractionTask.status == "orphaned")
            .where(ChunkExtractionTask.created_at < cutoff)
        )
        result = self.session.execute(stmt)
        self._maybe_commit()

        deleted_count = result.rowcount
        if deleted_count:
            logger.info(
                "orphan_tasks_cleaned",
                deleted_count=deleted_count,
                older_than_seconds=older_than_seconds,
            )
        return deleted_count

    # ------------------------------------------------------------------
    # Rerun support (per-chunk rerun feature, 2026-05-15)
    # ------------------------------------------------------------------

    def reset_chunk_task_for_rerun(self, *, task_id: str, source_id: str) -> int:
        """Snapshot the chunk task into history, wipe it, walk source back.

        Atomic: snapshot + wipe + source-status walk-back happen in one
        transaction. Returns the new attempt_number (1-indexed).

        The commit handler keys idempotency off ``commit_complete`` (not
        status), so this also clears ``commit_complete`` and
        ``commit_completed_at`` on the source.

        Raises:
            NotFoundError: chunk task does not exist.
            ConflictError: chunk task is in pending/queued/running, or
                the atomic UPDATE lost a race.

        Args:
            task_id: Chunk extraction task identifier.
            source_id: Parent source identifier (for source.status walk-back).
        """
        self._ensure_connected()

        # 1. Load current row + status guard
        task = self.session.exec(
            select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        ).first()
        if task is None:
            raise NotFoundError("chunk_task", task_id)
        if task.status in ("pending", "queued", "running"):
            msg = f"chunk {task.chunk_index} is already being processed"
            raise ConflictError(msg)

        # 2. Determine next attempt_number
        max_attempt_row = self.session.exec(
            select(func.coalesce(func.max(ChunkExtractionAttempt.attempt_number), 0)).where(
                ChunkExtractionAttempt.chunk_task_id == task_id
            )
        ).first()
        max_attempt = int(max_attempt_row or 0)
        attempt_number = max_attempt + 1

        # 3. Insert snapshot
        snapshot = ChunkExtractionAttempt(
            id=generate_id("att"),
            chunk_task_id=task_id,
            attempt_number=attempt_number,
            snapshotted_at=datetime.now(UTC),
            started_at=task.started_at,
            completed_at=task.completed_at,
            input_text=task.input_text,
            input_text_length=task.input_text_length,
            input_tokens=task.input_tokens,
            output_tokens=task.output_tokens,
            context_window_available=task.context_window_available,
            llm_response_json=task.llm_response_json,
            llm_response_length=task.llm_response_length,
            llm_duration_ms=task.llm_duration_ms,
            raw_entities=task.raw_entities,
            raw_relationships=task.raw_relationships,
            entity_count=task.entity_count,
            relationship_count=task.relationship_count,
            invalid_relationship_count=task.invalid_relationship_count,
            filtering_log=task.filtering_log,
            finish_reason=task.finish_reason,
            aborted_by_loop=task.aborted_by_loop,
            chunk_sentences=task.chunk_sentences,
            error_message=task.error_message,
            error_type=task.error_type,
        )
        self.session.add(snapshot)
        self.session.flush()

        # 4. Atomic wipe + status guard (rowcount=0 → lost the race)
        result = self.session.execute(
            sqla_update(ChunkExtractionTask)
            .where(ChunkExtractionTask.id == task_id)
            .where(ChunkExtractionTask.status.notin_(("pending", "queued", "running")))
            .values(
                status="pending",
                started_at=None,
                completed_at=None,
                queued_at=None,
                input_text=None,
                input_text_length=None,
                input_tokens=None,
                output_tokens=None,
                context_window_available=None,
                llm_response_json=None,
                llm_response_length=None,
                llm_duration_ms=None,
                raw_entities=None,
                raw_relationships=None,
                raw_entity_embeddings=None,
                entity_count=0,
                relationship_count=0,
                invalid_relationship_count=0,
                filtering_log=None,
                finish_reason=None,
                aborted_by_loop=None,
                chunk_sentences=None,
                error_message=None,
                error_type=None,
                retry_count=ChunkExtractionTask.retry_count + 1,
            )
        )
        if result.rowcount == 0:
            msg = f"chunk {task.chunk_index} rerun lost race to concurrent caller"
            raise ConflictError(msg)

        # 5. Walk source.status back. Commit handler keys idempotency off
        # commit_complete (not status), so we MUST clear it for the next
        # commit cycle to run.
        now = datetime.now(UTC)
        self.session.execute(
            sqla_update(SourceRow)
            .where(SourceRow.id == source_id)
            .where(SourceRow.status.in_(("committed", "extracted")))
            .values(
                status="extracting",
                commit_complete=False,
                commit_completed_at=None,
                last_activity_at=now,
            )
        )
        # If source is already 'extracting' (mid-rerun-batch), still bump
        # last_activity_at so the reconciler stays happy.
        self.session.execute(
            sqla_update(SourceRow)
            .where(SourceRow.id == source_id)
            .where(SourceRow.status == "extracting")
            .values(last_activity_at=now)
        )

        # 6. Walk the extraction_job status back. The chunk extraction
        # handler short-circuits when ``job.status in (completed, failed,
        # cancelled)`` — after a finalize cycle the job is ``completed``,
        # so the re-enqueued OP_EXTRACT_CHUNK would skip without doing
        # anything. Reset to ``in_progress`` so the rerun chunk actually
        # runs. Surfaced by the in-process pipeline integration test
        # ``test_journey_chunk_rerun_preserves_graph``.
        self.session.execute(
            sqla_update(ChunkExtractionJob)
            .where(ChunkExtractionJob.id == task.job_id)
            .where(ChunkExtractionJob.status.in_(("completed", "failed")))
            .values(status="in_progress")
        )

        self._maybe_commit()
        return attempt_number

    def list_chunk_attempts(
        self,
        *,
        chunk_task_id: str,
        include_body: bool = False,
    ) -> list[dict[str, Any]]:
        """Return prior extraction attempts for a chunk in attempt-number order.

        By default uses ``load_only`` to project summary columns only,
        keeping the listing query cheap on chunks with many attempts
        (CC003). Pass ``include_body=True`` to also load the heavy JSON
        / text fields (raw_entities, filtering_log, input_text,
        llm_response_json, chunk_sentences).

        Args:
            chunk_task_id: Chunk extraction task identifier.
            include_body: If ``True``, also load the heavy fields.

        Returns:
            Dicts ordered by ``attempt_number ASC``. Empty list if the
            chunk has never been rerun.
        """
        self._ensure_connected()
        summary_cols = (
            ChunkExtractionAttempt.id,
            ChunkExtractionAttempt.chunk_task_id,
            ChunkExtractionAttempt.attempt_number,
            ChunkExtractionAttempt.snapshotted_at,
            ChunkExtractionAttempt.started_at,
            ChunkExtractionAttempt.completed_at,
            ChunkExtractionAttempt.entity_count,
            ChunkExtractionAttempt.relationship_count,
            ChunkExtractionAttempt.invalid_relationship_count,
            ChunkExtractionAttempt.finish_reason,
            ChunkExtractionAttempt.aborted_by_loop,
            ChunkExtractionAttempt.llm_duration_ms,
            ChunkExtractionAttempt.input_tokens,
            ChunkExtractionAttempt.output_tokens,
            ChunkExtractionAttempt.input_text_length,
            ChunkExtractionAttempt.llm_response_length,
            ChunkExtractionAttempt.error_message,
            ChunkExtractionAttempt.error_type,
        )
        statement = (
            select(ChunkExtractionAttempt)
            .where(ChunkExtractionAttempt.chunk_task_id == chunk_task_id)
            .order_by(ChunkExtractionAttempt.attempt_number)
        )
        if not include_body:
            statement = statement.options(load_only(*summary_cols))
        rows = list(self.session.exec(statement).all())
        return self._entities_to_dicts(rows)

    def get_chunk_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        """Return the full snapshot for one attempt, or None if missing."""
        self._ensure_connected()
        row = self.session.exec(
            select(ChunkExtractionAttempt).where(ChunkExtractionAttempt.id == attempt_id)
        ).first()
        if row is None:
            return None
        return self._entities_to_dicts([row])[0]

    def get_chunk_task_by_job_and_index(
        self,
        *,
        job_id: str,
        chunk_index: int,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Fetch one chunk task by (job_id, chunk_index) within a database."""
        self._ensure_connected()
        row = self.session.exec(
            select(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job_id)
            .where(ChunkExtractionTask.chunk_index == chunk_index)
            .where(ChunkExtractionTask.database_name == database_name)
        ).first()
        if row is None:
            return None
        return self._entities_to_dicts([row])[0]

    def get_chunk_task_by_source_and_index(
        self,
        *,
        source_id: str,
        chunk_index: int,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Fetch the most recent chunk task for (source_id, chunk_index).

        Joins ``chunk_extraction_tasks`` → ``chunk_extraction_jobs`` so the
        per-chunk-rerun path can locate the task without needing the
        ``current_extraction_job_id`` pointer on ``sources`` — that pointer
        is cleared at extraction-complete time, which used to make rerun
        on a committed source impossible (404). Most-recent-job ordering
        handles the multi-job case where ``force_re_extract`` has run.
        """
        self._ensure_connected()
        row = self.session.exec(
            select(ChunkExtractionTask)
            .join(ChunkExtractionJob, ChunkExtractionTask.job_id == ChunkExtractionJob.id)
            .where(ChunkExtractionJob.source_id == source_id)
            .where(ChunkExtractionTask.chunk_index == chunk_index)
            .where(ChunkExtractionTask.database_name == database_name)
            .order_by(ChunkExtractionJob.created_at.desc())
        ).first()
        if row is None:
            return None
        return self._entities_to_dicts([row])[0]
