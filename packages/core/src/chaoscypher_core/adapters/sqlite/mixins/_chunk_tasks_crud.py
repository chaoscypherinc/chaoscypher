# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunk Extraction Tasks CRUD Mixin.

Provides create, read, update, delete, and list operations
for chunk extraction tasks in the SQLite storage adapter.
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete
from sqlalchemy.orm import load_only
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionJob,
    ChunkExtractionTask,
)


logger = structlog.get_logger(__name__)


class ChunkTasksCRUDMixin(SqliteMixinBase):
    """Mixin providing CRUD operations for chunk extraction tasks.

    Handles creation (single and batch), retrieval, update, listing,
    and deletion of chunk extraction tasks and their parent jobs.
    """

    def create_chunk_task(
        self,
        task_id: str,
        job_id: str,
        database_name: str,
        chunk_index: int,
        hierarchical_group_id: str | None = None,
        small_chunk_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a chunk extraction task.

        Args:
            task_id: Unique task identifier
            job_id: Parent job identifier
            database_name: Database context
            chunk_index: Index of this chunk in the document
            hierarchical_group_id: Reference to hierarchical chunk group
            small_chunk_ids: IDs of small chunks in this group

        Returns:
            Created task as dictionary
        """
        self._ensure_connected()

        task = ChunkExtractionTask(
            id=task_id,
            job_id=job_id,
            database_name=database_name,
            chunk_index=chunk_index,
            hierarchical_group_id=hierarchical_group_id,
            small_chunk_ids=small_chunk_ids,
            status="pending",
            created_at=datetime.now(UTC),
        )

        self.session.add(task)
        self._maybe_commit()
        self.session.refresh(task)
        assert task is not None  # mypy hint - task exists after creation
        result_dict = self._entity_to_dict(task)
        assert result_dict is not None
        return result_dict

    def create_chunk_tasks_batch(self, tasks_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create multiple chunk tasks in a single transaction.

        Args:
            tasks_data: List of task data dictionaries with keys:
                - task_id, job_id, database_name, chunk_index
                - optional: hierarchical_group_id, small_chunk_ids

        Returns:
            List of created tasks as dictionaries
        """
        self._ensure_connected()
        now = datetime.now(UTC)

        tasks = []
        for data in tasks_data:
            task = ChunkExtractionTask(
                id=data["task_id"],
                job_id=data["job_id"],
                database_name=data["database_name"],
                chunk_index=data["chunk_index"],
                hierarchical_group_id=data.get("hierarchical_group_id"),
                small_chunk_ids=data.get("small_chunk_ids"),
                status="pending",
                created_at=now,
            )
            tasks.append(task)
            self.session.add(task)

        self._maybe_commit()

        # Convert all tasks, filtering out any None (shouldn't happen)
        result_dicts = [self._entity_to_dict(t) for t in tasks]
        return [d for d in result_dicts if d is not None]

    def get_chunk_task(self, task_id: str) -> dict[str, Any] | None:
        """Get chunk task by ID.

        Args:
            task_id: Task identifier

        Returns:
            Task as dictionary or None if not found
        """
        self._ensure_connected()
        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        result = self.session.exec(statement)
        task = result.first()
        return self._entity_to_dict(task) if task else None

    def get_chunk_task_entity(self, task_id: str) -> ChunkExtractionTask | None:
        """Get chunk task entity by ID.

        Args:
            task_id: Task identifier

        Returns:
            Task entity or None if not found
        """
        self._ensure_connected()

        # Expire session cache to see changes from other processes
        self.session.expire_all()

        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        result = self.session.exec(statement)
        return result.first()

    def update_chunk_task(self, task_id: str, updates: dict[str, Any]) -> None:
        """Update chunk task fields.

        Args:
            task_id: Task identifier
            updates: Dictionary of fields to update
        """
        self._ensure_connected()

        # Expire session cache to see changes from other processes
        self.session.expire_all()

        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        result = self.session.exec(statement)
        task = result.first()

        if not task:
            logger.warning("chunk_task_not_found", task_id=task_id)
            return

        for field, value in updates.items():
            if hasattr(task, field):
                setattr(task, field, value)
            else:
                logger.warning("unknown_field_in_task_update", field=field, task_id=task_id)

        self.session.add(task)
        self._maybe_commit()
        self.session.refresh(task)

    def list_chunk_tasks(
        self,
        job_id: str,
        status: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List chunk tasks for a job.

        Args:
            job_id: Parent job identifier
            status: Optional status filter
            limit: Maximum tasks to return

        Returns:
            List of tasks as dictionaries
        """
        self._ensure_connected()
        statement = (
            select(ChunkExtractionTask)
            .options(
                load_only(
                    ChunkExtractionTask.id,
                    ChunkExtractionTask.job_id,
                    ChunkExtractionTask.database_name,
                    ChunkExtractionTask.chunk_index,
                    ChunkExtractionTask.hierarchical_group_id,
                    ChunkExtractionTask.small_chunk_ids,
                    ChunkExtractionTask.queue_task_id,
                    ChunkExtractionTask.status,
                    ChunkExtractionTask.retry_count,
                    ChunkExtractionTask.max_retries,
                    ChunkExtractionTask.created_at,
                    ChunkExtractionTask.queued_at,
                    ChunkExtractionTask.started_at,
                    ChunkExtractionTask.completed_at,
                    ChunkExtractionTask.entity_count,
                    ChunkExtractionTask.relationship_count,
                    ChunkExtractionTask.invalid_relationship_count,
                    ChunkExtractionTask.error_message,
                    ChunkExtractionTask.error_type,
                    ChunkExtractionTask.input_text_length,
                    ChunkExtractionTask.llm_response_length,
                    ChunkExtractionTask.llm_duration_ms,
                    ChunkExtractionTask.input_tokens,
                    ChunkExtractionTask.output_tokens,
                    ChunkExtractionTask.context_window_available,
                    # EXCLUDE: raw_entities, raw_relationships, input_text, llm_response_json
                )
            )
            .where(ChunkExtractionTask.job_id == job_id)
        )

        if status:
            statement = statement.where(ChunkExtractionTask.status == status)

        statement = statement.order_by(ChunkExtractionTask.chunk_index).limit(limit)

        results = self.session.exec(statement)
        return self._entities_to_dicts(results.all())

    def list_extraction_tasks_for_job(
        self, *, job_id: str, database_name: str
    ) -> list[dict[str, Any]]:
        """Return every ChunkExtractionTask belonging to a job.

        Used by ``_upsert_extraction_tasks`` to decide which chunk
        indices already have task rows so it can skip them on a
        handler restart. Does NOT apply a limit — we want the full
        set for an accurate idempotency check, and a given job
        rarely exceeds a few thousand tasks.

        Uses ``load_only`` per the CLAUDE.md SQLAlchemy performance
        rules: the idempotency check only needs ``id``, ``chunk_index``,
        and ``status`` so the large per-task JSON columns are
        deliberately excluded.

        Args:
            job_id: Parent job.
            database_name: Active database (scopes the query when the
                same adapter is shared across workers).

        Returns:
            List of task dicts ordered by chunk_index.
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
                    ChunkExtractionTask.hierarchical_group_id,
                    ChunkExtractionTask.status,
                    ChunkExtractionTask.retry_count,
                )
            )
            .where(
                ChunkExtractionTask.job_id == job_id,
                ChunkExtractionTask.database_name == database_name,
            )
            .order_by(ChunkExtractionTask.chunk_index)
        )
        results = self.session.scalars(statement).all()
        return self._entities_to_dicts(results)

    def list_extraction_tasks_by_status(
        self,
        *,
        job_id: str,
        statuses: list[str],
        database_name: str,
    ) -> list[dict[str, Any]]:
        """List tasks for a job whose status is in the given set.

        Used by the analysis handler's re-dispatch path to pick only
        pending/failed tasks for enqueue, so completed work is never
        retried. Keeping this a dedicated method (rather than a list
        comprehension over the full list) makes it trivial to add a
        covering index later if the failure matrix grows.

        Args:
            job_id: Parent job.
            statuses: Status values to include (e.g., ``["pending",
                "failed"]``).
            database_name: Active database.

        Returns:
            Matching task dicts ordered by chunk_index.
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
                    ChunkExtractionTask.hierarchical_group_id,
                    ChunkExtractionTask.small_chunk_ids,
                    ChunkExtractionTask.status,
                    ChunkExtractionTask.retry_count,
                    # queued_at is required by SourceRecovery's
                    # queued-orphan vs in-flight age filter.
                    ChunkExtractionTask.queued_at,
                    # started_at is required by SourceRecovery's
                    # running-zombie age filter (worker died mid-claim;
                    # task stays in 'running' forever without this fallback).
                    ChunkExtractionTask.started_at,
                )
            )
            .where(
                ChunkExtractionTask.job_id == job_id,
                ChunkExtractionTask.database_name == database_name,
                ChunkExtractionTask.status.in_(statuses),
            )
            .order_by(ChunkExtractionTask.chunk_index)
        )
        results = self.session.scalars(statement).all()
        return self._entities_to_dicts(results)

    def delete_tasks_for_source(self, source_id: str) -> None:
        """Delete ChunkExtractionTask rows for all jobs of this source."""
        self._ensure_connected()
        job_ids = list(
            self.session.scalars(
                select(ChunkExtractionJob.id).where(ChunkExtractionJob.source_id == source_id)
            ).all()
        )
        if job_ids:
            stmt = delete(ChunkExtractionTask).where(ChunkExtractionTask.job_id.in_(job_ids))
            self.session.execute(stmt)
        self._maybe_commit()

    def delete_pending_chunk_tasks_for_source(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> int:
        """Delete pending/queued chunk_extraction_tasks rows for a source.

        Used when extraction is aborted early (e.g., ``assert_extractable``
        raised because the source is already committed) so we do not keep DB
        state we will never use. Completed and failed rows are kept as audit
        history. Audit fix #H5.

        Args:
            source_id: Source whose pending tasks should be removed.
            database_name: Database the source belongs to.

        Returns:
            Number of rows deleted.
        """
        self._ensure_connected()
        job_ids = list(
            self.session.scalars(
                select(ChunkExtractionJob.id).where(
                    ChunkExtractionJob.source_id == source_id,
                    ChunkExtractionJob.database_name == database_name,
                )
            ).all()
        )
        if not job_ids:
            return 0

        stmt = (
            delete(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id.in_(job_ids))
            .where(ChunkExtractionTask.status.in_(["pending", "queued"]))
        )
        result = self.session.execute(stmt)
        self._maybe_commit()
        return result.rowcount
