# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Extraction Jobs Mixin for SqliteAdapter.

Handles chunk extraction job CRUD operations (create, get, update, status transitions).
Part of the unified SourceStorageProtocol implementation.
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy import update as sqla_update
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.mixins._extraction_job_query_base import (
    ExtractionJobQueryBase,
)
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionJob,
    ChunkExtractionTask,
    SourceRow,
)


logger = structlog.get_logger(__name__)


class SourceExtractionJobsMixin(ExtractionJobQueryBase, SqliteMixinBase):
    """Mixin providing chunk extraction job operations for SQLite storage.

    Implements operations for:
    - Extraction job CRUD (create, get, update)
    - Job status transitions (start, complete, fail)
    - Job progress tracking

    Note: This mixin contributes to the unified SourceStorageProtocol.
    """

    def create_extraction_job(
        self,
        job_id: str,
        source_id: str,
        database_name: str,
        extraction_depth: str = "full",
        generate_embeddings: bool = True,
        parent_task_id: str | None = None,
        forced_domain: str | None = None,
        detected_domain: str | None = None,
        domain_guidance: str | None = None,
        extraction_config: str | None = None,
    ) -> dict[str, Any]:
        """Create a new chunk extraction job.

        Args:
            job_id: Unique job identifier
            source_id: ID of the source processing file being processed
            database_name: Database context
            extraction_depth: Extraction depth ('quick', 'full')
            generate_embeddings: Whether to generate entity embeddings
            parent_task_id: Queue task ID that created this job
            forced_domain: User-selected domain (e.g., 'technical')
            detected_domain: Auto-detected domain name
            domain_guidance: Domain-specific LLM guidance text
            extraction_config: JSON-encoded per-job extraction config (templates, guidance,
                examples) stored once instead of duplicated per-chunk

        Returns:
            Created job as dictionary
        """
        self._ensure_connected()

        job = ChunkExtractionJob(
            id=job_id,
            source_id=source_id,
            database_name=database_name,
            extraction_depth=extraction_depth,
            generate_embeddings=generate_embeddings,
            parent_task_id=parent_task_id,
            forced_domain=forced_domain,
            detected_domain=detected_domain,
            domain_guidance=domain_guidance,
            extraction_config=extraction_config,
            status="pending",
            created_at=datetime.now(UTC),
        )

        self.session.add(job)
        self._maybe_commit()
        self.session.refresh(job)
        assert job is not None  # mypy hint - job exists after creation

        # Update source processing file with current job reference
        statement = select(SourceRow).where(SourceRow.id == source_id)
        result = self.session.exec(statement)
        source = result.first()
        if source:
            source.current_extraction_job_id = job_id
            self.session.add(source)
            self._maybe_commit()

        result_dict = self._entity_to_dict(job)
        assert result_dict is not None
        return result_dict

    def get_active_extraction_job(
        self, *, source_id: str, database_name: str
    ) -> dict[str, Any] | None:
        """Return the most recent non-terminal job for a source, if any.

        "Active" means status in (``pending``, ``running``) — completed
        and failed jobs are terminal and never returned. Used by the
        analysis handler to decide whether to reuse an existing job or
        create a fresh one during a restart.

        Args:
            source_id: Source to query.
            database_name: Active database.

        Returns:
            Most recently created active job as a dict, or None.
        """
        self._ensure_connected()

        # Expire session cache so we see concurrent writes from other workers
        self.session.expire_all()

        statement = (
            select(ChunkExtractionJob)
            .where(
                ChunkExtractionJob.source_id == source_id,
                ChunkExtractionJob.database_name == database_name,
                ChunkExtractionJob.status.in_(["pending", "running"]),
            )
            .order_by(ChunkExtractionJob.created_at.desc())
            .limit(1)
        )
        job = self.session.scalars(statement).first()
        return self._entity_to_dict(job) if job else None

    def update_extraction_job_total(
        self, *, job_id: str, total_chunks: int, database_name: str
    ) -> None:
        """Update total_chunks on a ChunkExtractionJob.

        Thin wrapper over update_extraction_job for the analysis
        handler's idempotent refresh of the authoritative group count.

        Args:
            job_id: Job to update.
            total_chunks: New total (post-filtering, post-depth strategy).
            database_name: Active database (accepted for API symmetry
                with the other task-level helpers; the job_id is
                already unique).
        """
        self.update_extraction_job(job_id, {"total_chunks": total_chunks})

    # ``get_extraction_job`` is provided by ``ExtractionJobQueryBase`` —
    # shared with the chunk-task mixins so neither side reaches across
    # sibling MRO for the lookup.

    def get_extraction_job_entity(self, job_id: str) -> ChunkExtractionJob | None:
        """Get extraction job entity by ID.

        Args:
            job_id: Job identifier

        Returns:
            Job entity or None if not found
        """
        self._ensure_connected()

        # Expire session cache to see changes from other processes
        self.session.expire_all()

        statement = select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
        result = self.session.exec(statement)
        return result.first()

    def update_extraction_job(self, job_id: str, updates: dict[str, Any]) -> None:
        """Update extraction job fields.

        Args:
            job_id: Job identifier
            updates: Dictionary of fields to update
        """
        self._ensure_connected()

        # Expire session cache to see changes from other processes
        self.session.expire_all()

        statement = select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
        result = self.session.exec(statement)
        job = result.first()

        if not job:
            logger.warning("extraction_job_not_found", job_id=job_id)
            return

        for field, value in updates.items():
            if hasattr(job, field):
                setattr(job, field, value)
            else:
                logger.warning("unknown_field_in_job_update", field=field, job_id=job_id)

        self.session.add(job)
        self._maybe_commit()
        self.session.refresh(job)

    def start_extraction_job(self, job_id: str) -> None:
        """Mark extraction job as running.

        Args:
            job_id: Job identifier
        """
        self.update_extraction_job(
            job_id,
            {
                "status": "running",
                "started_at": datetime.now(UTC),
            },
        )

    def complete_extraction_job(self, job_id: str) -> None:
        """Mark extraction job as completed and cascade non-terminal tasks to orphaned.

        Both the job update and the task cascade run in the same transaction
        so the DB is never left with the job completed but tasks still in flight.
        Terminal tasks (completed, failed, cancelled, orphaned) are unchanged.

        Args:
            job_id: Job identifier
        """
        self._ensure_connected()

        self.session.expire_all()

        statement = select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
        job = self.session.exec(statement).first()

        if job is None:
            logger.warning("extraction_job_not_found_on_complete", job_id=job_id)
            return

        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        self.session.add(job)

        orphan_stmt = (
            sqla_update(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job_id)
            .where(ChunkExtractionTask.status.in_(("pending", "queued", "running")))
            .values(
                status="orphaned",
                error_message="parent job completed",
            )
        )
        result = self.session.execute(orphan_stmt)
        orphaned_count = result.rowcount

        self._maybe_commit()
        self.session.refresh(job)

        if orphaned_count:
            logger.info(
                "extraction_job_completed_orphaned_tasks",
                job_id=job_id,
                orphaned=orphaned_count,
            )

    def fail_extraction_job(self, job_id: str, error_message: str) -> None:
        """Mark extraction job as failed and cascade non-terminal tasks to orphaned.

        Both the job update and the task cascade run in the same transaction
        so the DB is never left with the job failed but tasks still in flight.
        Terminal tasks (completed, failed, cancelled, orphaned) are unchanged.

        Args:
            job_id: Job identifier
            error_message: Error description
        """
        self._ensure_connected()

        self.session.expire_all()

        statement = select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
        job = self.session.exec(statement).first()

        if job is None:
            logger.warning("extraction_job_not_found_on_fail", job_id=job_id)
            return

        job.status = "failed"
        job.error_message = error_message
        job.completed_at = datetime.now(UTC)
        self.session.add(job)

        orphan_stmt = (
            sqla_update(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job_id)
            .where(ChunkExtractionTask.status.in_(("pending", "queued", "running")))
            .values(
                status="orphaned",
                error_message="parent job failed",
            )
        )
        result = self.session.execute(orphan_stmt)
        orphaned_count = result.rowcount

        self._maybe_commit()
        self.session.refresh(job)

        if orphaned_count:
            logger.info(
                "job_failed_orphaned_tasks",
                job_id=job_id,
                orphaned=orphaned_count,
            )

    def cancel_extraction_job_cascade(self, job_id: str) -> int:
        """Mark an extraction job as cancelled and cascade non-terminal tasks.

        Used by ``reset_for_retry`` (audit fix #F53): when the source's
        ``current_extraction_job_id`` pointer is cleared, the orphaned
        job row is left behind in ``running``/``pending`` status. The
        recovery reconciler filters by job status (not by source
        pointer), so a stale job can be picked up and its chunks
        re-dispatched, causing duplicate extraction. Cancelling the job
        + its in-flight tasks keeps the reconciler's terminal-state
        invariants intact.

        Both updates run inside the current SQLAlchemy unit-of-work,
        so the caller's enclosing ``adapter.transaction()`` block
        (used by ``reset_for_retry``) makes the cascade atomic with
        the source reset.

        No-op when the job_id does not resolve, when the job is already
        in a terminal status (``completed``/``failed``/``cancelled``),
        or when there are no non-terminal tasks. Safe to call repeatedly.

        Args:
            job_id: Extraction job to cancel.

        Returns:
            Number of task rows transitioned to ``cancelled`` (0 when
            the job was already terminal or had no in-flight tasks).
        """
        self._ensure_connected()

        statement = select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
        job = self.session.exec(statement).first()

        if job is None:
            return 0

        if job.status in ("completed", "failed", "cancelled"):
            return 0

        now = datetime.now(UTC)
        job.status = "cancelled"
        job.completed_at = now
        self.session.add(job)

        cancel_stmt = (
            sqla_update(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job_id)
            .where(ChunkExtractionTask.status.in_(("pending", "queued", "running")))
            .values(
                status="cancelled",
                cancelled_at=now,
                error_message="parent job cancelled by source reset",
            )
        )
        result = self.session.execute(cancel_stmt)
        cancelled_count = int(result.rowcount or 0)

        self._maybe_commit()

        if cancelled_count:
            logger.info(
                "extraction_job_cancelled_cascade",
                job_id=job_id,
                cancelled_tasks=cancelled_count,
            )

        return cancelled_count

    def delete_extraction_jobs_for_source(self, source_id: str) -> None:
        """Delete ChunkExtractionJob rows owned by this source."""
        self._ensure_connected()
        stmt = delete(ChunkExtractionJob).where(ChunkExtractionJob.source_id == source_id)
        self.session.execute(stmt)
        self._maybe_commit()

    def increment_job_completed_and_check(
        self,
        *,
        job_id: str,
        database_name: str,
        outcome: str,
    ) -> dict[str, Any]:
        """Atomically increment a counter and read back progress.

        The underlying operation is a single UPDATE with an arithmetic
        expression (``completed_chunks = completed_chunks + 1``) which
        SQLite serializes at the database level — atomic UPDATE, no
        read-modify-write window. This is the primitive the resumability
        story relies on: if
        two workers both successfully process a chunk, exactly one
        of them observes the terminal state and exactly one
        finalization job is enqueued.

        After the increment commits, a second atomic UPDATE *claims* the
        terminal transition: it sets ``finalize_claimed = 1`` only
        ``WHERE finalize_claimed = 0 AND completed + failed >= total``.
        Because that claim is itself a single serialized UPDATE, exactly
        one caller's claim affects a row (``rowcount == 1``) — so
        ``is_terminal`` is True for exactly one caller even when two
        concurrent last-chunk handlers both observe the terminal counts.
        (The previous "re-read after commit and compare" approach could
        return ``is_terminal=True`` to BOTH, double-enqueuing finalize.)

        Args:
            job_id: Extraction job to update.
            database_name: Active database (unused at present — the
                job_id is globally unique — but accepted for symmetry
                with the rest of the adapter surface).
            outcome: Either ``"completed"`` or ``"failed"``.

        Returns:
            ``{"completed": int, "failed": int, "total": int,
            "is_terminal": bool}`` where ``is_terminal`` means "this caller
            atomically claimed the terminal transition" (enqueue finalize).

        Raises:
            ValueError: If outcome is not "completed" or "failed".
        """
        self._ensure_connected()

        if outcome not in ("completed", "failed"):
            msg = f"outcome must be 'completed' or 'failed', got {outcome!r}"
            raise ValueError(msg)

        if outcome == "completed":
            stmt = (
                sqla_update(ChunkExtractionJob)
                .where(ChunkExtractionJob.id == job_id)
                .values(completed_chunks=ChunkExtractionJob.completed_chunks + 1)
            )
        else:  # failed
            stmt = (
                sqla_update(ChunkExtractionJob)
                .where(ChunkExtractionJob.id == job_id)
                .values(failed_chunks=ChunkExtractionJob.failed_chunks + 1)
            )

        self.session.execute(stmt)
        self._maybe_commit()

        # Atomically claim the terminal transition. Only the caller whose
        # increment made (completed+failed) reach total AND finds the claim
        # flag still unset wins (rowcount == 1); all others get rowcount 0.
        claim_stmt = (
            sqla_update(ChunkExtractionJob)
            .where(
                ChunkExtractionJob.id == job_id,
                ChunkExtractionJob.finalize_claimed == False,  # noqa: E712 - SQL boolean column predicate
                ChunkExtractionJob.completed_chunks + ChunkExtractionJob.failed_chunks
                >= ChunkExtractionJob.total_chunks,
            )
            .values(finalize_claimed=True)
        )
        claim_result = self.session.execute(claim_stmt)
        self._maybe_commit()
        claimed_terminal = claim_result.rowcount == 1

        # Re-read fresh counts for the returned progress payload.
        self.session.expire_all()
        job = self.session.scalars(
            select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
        ).first()

        if job is None:
            return {
                "completed": 0,
                "failed": 0,
                "total": 0,
                "is_terminal": False,
            }

        return {
            "completed": job.completed_chunks or 0,
            "failed": job.failed_chunks or 0,
            "total": job.total_chunks or 0,
            "is_terminal": claimed_terminal,
        }

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 6).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_extraction_jobs(self, *, database_name: str) -> int:
        """Count ChunkExtractionJob rows in one database."""
        self._ensure_connected()
        stmt = (
            select(func.count())
            .select_from(ChunkExtractionJob)
            .where(ChunkExtractionJob.database_name == database_name)
        )
        return int(self.session.exec(stmt).one())

    def delete_extraction_jobs(self, *, database_name: str) -> int:
        """Delete every ChunkExtractionJob in one database."""
        self._ensure_connected()
        stmt = delete(ChunkExtractionJob).where(ChunkExtractionJob.database_name == database_name)
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)

    def count_extraction_tasks(self, *, database_name: str) -> int:
        """Count ChunkExtractionTask rows in one database."""
        self._ensure_connected()
        stmt = (
            select(func.count())
            .select_from(ChunkExtractionTask)
            .where(ChunkExtractionTask.database_name == database_name)
        )
        return int(self.session.exec(stmt).one())

    def delete_extraction_tasks(self, *, database_name: str) -> int:
        """Delete every ChunkExtractionTask in one database."""
        self._ensure_connected()
        stmt = delete(ChunkExtractionTask).where(ChunkExtractionTask.database_name == database_name)
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)

    def clear_all_extraction_jobs(self) -> int:
        """Delete every ChunkExtractionJob across databases."""
        self._ensure_connected()
        result = self.session.exec(delete(ChunkExtractionJob))
        self._maybe_commit()
        return int(result.rowcount or 0)

    def clear_all_extraction_tasks(self) -> int:
        """Delete every ChunkExtractionTask across databases."""
        self._ensure_connected()
        result = self.session.exec(delete(ChunkExtractionTask))
        self._maybe_commit()
        return int(result.rowcount or 0)
