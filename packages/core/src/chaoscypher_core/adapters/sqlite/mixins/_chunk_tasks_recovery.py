# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunk Extraction Tasks Recovery Mixin.

Provides failure and retry recovery queries for chunk extraction tasks,
including orphaned task detection and stuck source identification.
"""

from typing import Any

import structlog
from sqlalchemy.orm import load_only
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.mixins._extraction_job_query_base import (
    ExtractionJobQueryBase,
)
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionTask,
    SourceRow,
)
from chaoscypher_core.models import SourceStatus


logger = structlog.get_logger(__name__)


class ChunkTasksRecoveryMixin(ExtractionJobQueryBase):
    """Mixin providing recovery query operations for chunk extraction tasks.

    Handles detection of orphaned tasks (queued/running with no active
    queue entry) and stuck sources (extracting status with no active job).

    ``get_extraction_job`` is inherited from ``ExtractionJobQueryBase``
    so the cross-mixin call is explicit in the inheritance graph.
    """

    def list_orphaned_chunk_tasks(self, database_name: str) -> list[dict[str, Any]]:
        """Get all chunk tasks that may be orphaned (queued or running with queue_task_id).

        Used by worker startup recovery to find orphaned tasks that were
        queued or running when the worker died. These tasks have status='queued'
        or 'running' but their corresponding queue tasks may no longer exist in queue.

        Args:
            database_name: Database context

        Returns:
            List of potentially orphaned tasks as dictionaries with all task fields
        """
        self._ensure_connected()
        # Expire cache to see changes from other processes
        self.session.expire_all()

        statement = (
            select(ChunkExtractionTask)
            .options(
                load_only(
                    ChunkExtractionTask.id,
                    ChunkExtractionTask.job_id,
                    ChunkExtractionTask.database_name,
                    ChunkExtractionTask.chunk_index,
                    ChunkExtractionTask.queue_task_id,
                    ChunkExtractionTask.status,
                    ChunkExtractionTask.retry_count,
                    ChunkExtractionTask.max_retries,
                    ChunkExtractionTask.created_at,
                    ChunkExtractionTask.queued_at,
                    ChunkExtractionTask.started_at,
                    ChunkExtractionTask.error_message,
                    ChunkExtractionTask.error_type,
                    # EXCLUDE: raw_entities, raw_relationships, input_text, llm_response_json
                )
            )
            .where(ChunkExtractionTask.database_name == database_name)
            .where(ChunkExtractionTask.status.in_(["queued", "running"]))  # type: ignore[attr-defined]
            .where(ChunkExtractionTask.queue_task_id.isnot(None))
        )

        results = self.session.exec(statement)
        return self._entities_to_dicts(results.all())

    def get_stuck_extracting_sources(self, database_name: str) -> list[dict[str, Any]]:
        """Get sources stuck in 'extracting' status with no active extraction job.

        A source is considered "stuck" if:
        - status = 'extracting'
        - AND either:
          - No current_extraction_job_id set
          - OR the referenced job has status 'failed' or 'cancelled'

        Used by worker startup recovery (Layer 2) to handle sources that
        got stuck due to worker crash mid-extraction.

        Args:
            database_name: Database context

        Returns:
            List of stuck source dicts with their extraction job status (if any)
        """
        self._ensure_connected()
        # Expire cache to see changes from other processes
        self.session.expire_all()

        # Find sources in 'extracting' status — only load fields needed for
        # stuck-source detection. Per-source entity/relationship rows live
        # in dedicated tables since migration 0042, so the source row is
        # already cheap; we still narrow the projection to avoid hauling
        # user_metadata / cross_chunk_filtering_log JSON columns.
        statement = (
            select(SourceRow)
            .options(
                load_only(
                    SourceRow.id,
                    SourceRow.database_name,
                    SourceRow.filename,
                    SourceRow.status,
                    SourceRow.current_extraction_job_id,
                    SourceRow.error_message,
                    SourceRow.error_stage,
                    # EXCLUDE: user_metadata, cross_chunk_filtering_log,
                    # extraction_pending_file_info, llm_error_counts, etc.
                )
            )
            .where(SourceRow.database_name == database_name)
            .where(SourceRow.status == SourceStatus.EXTRACTING)
        )

        sources = list(self.session.exec(statement).all())
        stuck_sources = []

        for source in sources:
            job_id = source.current_extraction_job_id
            job_status = None

            if job_id:
                # Check job status
                job = self.get_extraction_job(job_id)
                if job:
                    job_status_val = job.get("status")
                    # If job is still running or pending, source is not stuck
                    if job_status_val in ("running", "pending"):
                        continue
                    job_status = job_status_val

            # Source is stuck: no job, or job is failed/cancelled/completed
            source_dict = self._entity_to_dict(source)
            if source_dict:
                if job_status:
                    source_dict["extraction_job_status"] = job_status
                stuck_sources.append(source_dict)

        return stuck_sources
