# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunk Extraction Tasks Analytics Mixin.

Provides metrics, statistics, and chart data for extraction task analytics,
including per-source task listing, aggregate statistics, and detail views.
"""

from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import (
    ChunkExtractionJob,
    ChunkExtractionTask,
    DocumentChunk,
)


logger = structlog.get_logger(__name__)


class ChunkTasksAnalyticsMixin(SqliteMixinBase):
    """Mixin providing analytics and metrics operations for chunk extraction tasks.

    Handles paginated task listing, chart data retrieval, single-task detail
    views, and aggregate statistics computation via SQL aggregates.
    """

    def get_extraction_tasks_for_source(
        self,
        source_id: str,
        database_name: str,
        page: int = 1,
        per_page: int = 20,
        include_text_content: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get all extraction tasks for a source with pagination.

        Args:
            source_id: The source ID
            database_name: Database context
            page: Page number (1-indexed)
            per_page: Items per page
            include_text_content: If True, includes input_text and llm_response_json
                fields. If False, only includes length fields for performance.

        Returns:
            Tuple of (list of task dicts, total count)
        """
        self._ensure_connected()
        self.session.expire_all()

        # Get the most recent job for this source
        job_stmt = (
            select(ChunkExtractionJob)
            .where(ChunkExtractionJob.source_id == source_id)
            .where(ChunkExtractionJob.database_name == database_name)
            .order_by(ChunkExtractionJob.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        job = self.session.exec(job_stmt).first()

        if not job:
            return [], 0

        # Count total tasks
        count_stmt = (
            select(func.count())
            .select_from(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job.id)
        )
        total = self.session.exec(count_stmt).one()

        # Build query with optional column projection
        task_stmt = select(ChunkExtractionTask).where(ChunkExtractionTask.job_id == job.id)

        if not include_text_content:
            # Exclude large TEXT columns for list views
            task_stmt = task_stmt.options(
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
                    # Include length fields but not the TEXT content
                    ChunkExtractionTask.input_text_length,
                    ChunkExtractionTask.llm_response_length,
                    ChunkExtractionTask.llm_duration_ms,
                    # Token tracking fields
                    ChunkExtractionTask.input_tokens,
                    ChunkExtractionTask.output_tokens,
                    ChunkExtractionTask.context_window_available,
                )
            )

        # Pagination
        offset = (page - 1) * per_page
        task_stmt = (
            task_stmt.order_by(ChunkExtractionTask.chunk_index).offset(offset).limit(per_page)
        )

        tasks = list(self.session.exec(task_stmt).all())
        return self._entities_to_dicts(tasks), total

    def get_extraction_tasks_for_charts(
        self,
        source_id: str,
        database_name: str,
    ) -> list[dict[str, Any]]:
        """Get all extraction tasks with minimal fields for chart rendering.

        Returns only the fields needed for charts (no pagination, no content).
        This is optimized for rendering Processing Time, Entity Density,
        and Retry Analysis charts with data from ALL tasks.

        Args:
            source_id: The source ID
            database_name: Database context

        Returns:
            List of task dicts with minimal chart fields
        """
        self._ensure_connected()
        self.session.expire_all()

        # Get the most recent job for this source
        job_stmt = (
            select(ChunkExtractionJob)
            .where(ChunkExtractionJob.source_id == source_id)
            .where(ChunkExtractionJob.database_name == database_name)
            .order_by(ChunkExtractionJob.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        job = self.session.exec(job_stmt).first()

        if not job:
            return []

        # Fetch all tasks with only chart-required fields
        task_stmt = (
            select(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job.id)
            .options(
                load_only(
                    ChunkExtractionTask.id,
                    ChunkExtractionTask.chunk_index,
                    ChunkExtractionTask.status,
                    ChunkExtractionTask.retry_count,
                    ChunkExtractionTask.entity_count,
                    ChunkExtractionTask.relationship_count,
                    ChunkExtractionTask.invalid_relationship_count,
                    ChunkExtractionTask.input_text_length,
                    ChunkExtractionTask.llm_duration_ms,
                )
            )
            .order_by(ChunkExtractionTask.chunk_index)
        )

        tasks = list(self.session.exec(task_stmt).all())
        return self._entities_to_dicts(tasks)

    def get_extraction_task_detail(self, task_id: str) -> dict[str, Any] | None:
        """Get a single extraction task with full details including text content.

        Args:
            task_id: Task identifier

        Returns:
            Task as dictionary with all fields, or None if not found.
            Includes small_chunk_numbers (1-indexed) for UI display.
        """
        self._ensure_connected()
        self.session.expire_all()

        statement = select(ChunkExtractionTask).where(ChunkExtractionTask.id == task_id)
        result = self.session.exec(statement)
        task = result.first()

        if not task:
            return None

        task_dict = self._entity_to_dict(task)
        if not task_dict:
            return None

        # Map small_chunk_ids (UUIDs) to chunk numbers for UI display
        small_chunk_ids = task_dict.get("small_chunk_ids")
        if small_chunk_ids:
            # Look up chunk indices for the UUIDs
            # Note: No load_only needed - select() already specifies columns
            chunk_stmt = select(DocumentChunk.id, DocumentChunk.chunk_index).where(
                DocumentChunk.id.in_(small_chunk_ids)  # type: ignore[attr-defined]
            )
            chunk_results = self.session.exec(chunk_stmt).all()

            # Create a mapping from UUID to chunk_index
            id_to_index = dict(chunk_results)

            # Build chunk numbers list (1-indexed, sorted by chunk index)
            chunk_numbers = [id_to_index[cid] + 1 for cid in small_chunk_ids if cid in id_to_index]
            # Sort to ensure consistent ordering
            chunk_numbers.sort()
            task_dict["small_chunk_numbers"] = chunk_numbers

        return task_dict

    def get_extraction_task_stats(
        self,
        source_id: str,
        database_name: str,
    ) -> dict[str, Any] | None:
        """Get aggregate statistics for extraction tasks.

        Computes min/avg/max for tokens, duration, and other metrics using
        SQL aggregates for efficiency. This allows charts to show accurate
        statistics for ALL tasks without loading every row.

        Args:
            source_id: The source ID
            database_name: Database context

        Returns:
            Dictionary with aggregate statistics, or None if no data
        """
        self._ensure_connected()
        self.session.expire_all()

        # Get the most recent job for this source
        job_stmt = (
            select(ChunkExtractionJob)
            .where(ChunkExtractionJob.source_id == source_id)
            .where(ChunkExtractionJob.database_name == database_name)
            .order_by(ChunkExtractionJob.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        job = self.session.exec(job_stmt).first()

        if not job:
            return None

        # Compute aggregate statistics in a single query
        stats_stmt = (
            select(  # type: ignore[call-overload]
                func.count().label("total_tasks"),
                # Input tokens
                func.min(ChunkExtractionTask.input_tokens).label("min_input_tokens"),
                func.max(ChunkExtractionTask.input_tokens).label("max_input_tokens"),
                func.avg(ChunkExtractionTask.input_tokens).label("avg_input_tokens"),
                # Output tokens
                func.min(ChunkExtractionTask.output_tokens).label("min_output_tokens"),
                func.max(ChunkExtractionTask.output_tokens).label("max_output_tokens"),
                func.avg(ChunkExtractionTask.output_tokens).label("avg_output_tokens"),
                # Total tokens (input + output per chunk)
                func.min(
                    ChunkExtractionTask.input_tokens + ChunkExtractionTask.output_tokens
                ).label("min_total_tokens"),
                func.max(
                    ChunkExtractionTask.input_tokens + ChunkExtractionTask.output_tokens
                ).label("max_total_tokens"),
                func.avg(
                    ChunkExtractionTask.input_tokens + ChunkExtractionTask.output_tokens
                ).label("avg_total_tokens"),
                # Duration
                func.min(ChunkExtractionTask.llm_duration_ms).label("min_duration_ms"),
                func.max(ChunkExtractionTask.llm_duration_ms).label("max_duration_ms"),
                func.avg(ChunkExtractionTask.llm_duration_ms).label("avg_duration_ms"),
                # Entity counts
                func.sum(ChunkExtractionTask.entity_count).label("total_entities"),
                func.avg(ChunkExtractionTask.entity_count).label("avg_entities_per_task"),
                # Relationship counts
                func.sum(ChunkExtractionTask.relationship_count).label("total_relationships"),
                func.avg(ChunkExtractionTask.relationship_count).label(
                    "avg_relationships_per_task"
                ),
                # Retry stats
                func.sum(ChunkExtractionTask.retry_count).label("total_retries"),
                func.max(ChunkExtractionTask.retry_count).label("max_retries"),
                # Invalid relationship stats
                func.sum(ChunkExtractionTask.invalid_relationship_count).label(
                    "total_invalid_relationships"
                ),
                func.avg(ChunkExtractionTask.invalid_relationship_count).label(
                    "avg_invalid_per_task"
                ),
            )
            .where(ChunkExtractionTask.job_id == job.id)
            .where(ChunkExtractionTask.status == "completed")
        )

        result = self.session.exec(stats_stmt).first()

        if not result or result.total_tasks == 0:
            return None

        # Get context window from any task (should be same for all)
        context_stmt = (
            select(ChunkExtractionTask.context_window_available)
            .where(ChunkExtractionTask.job_id == job.id)
            .where(ChunkExtractionTask.context_window_available.isnot(None))
            .limit(1)
        )
        context_window = self.session.exec(context_stmt).first()

        # Utilization: single-pass extraction uses one LLM call per chunk
        min_total = result.min_total_tokens
        max_total = result.max_total_tokens
        avg_total = result.avg_total_tokens

        return {
            "total_tasks": result.total_tasks,
            "context_window": context_window,
            # Input tokens
            "min_input_tokens": result.min_input_tokens,
            "max_input_tokens": result.max_input_tokens,
            "avg_input_tokens": round(result.avg_input_tokens) if result.avg_input_tokens else None,
            # Output tokens
            "min_output_tokens": result.min_output_tokens,
            "max_output_tokens": result.max_output_tokens,
            "avg_output_tokens": round(result.avg_output_tokens)
            if result.avg_output_tokens
            else None,
            # Total tokens
            "min_total_tokens": min_total,
            "max_total_tokens": max_total,
            "avg_total_tokens": round(avg_total) if avg_total else None,
            # Utilization percentages
            "min_utilization": round((min_total / context_window) * 100, 1)
            if context_window and min_total
            else None,
            "max_utilization": round((max_total / context_window) * 100, 1)
            if context_window and max_total
            else None,
            "avg_utilization": round((avg_total / context_window) * 100, 1)
            if context_window and avg_total
            else None,
            # Duration
            "min_duration_ms": result.min_duration_ms,
            "max_duration_ms": result.max_duration_ms,
            "avg_duration_ms": round(result.avg_duration_ms) if result.avg_duration_ms else None,
            # Entity counts
            "total_entities": result.total_entities or 0,
            "avg_entities_per_task": round(result.avg_entities_per_task, 1)
            if result.avg_entities_per_task
            else 0,
            # Relationship counts
            "total_relationships": result.total_relationships or 0,
            "avg_relationships_per_task": round(result.avg_relationships_per_task, 1)
            if result.avg_relationships_per_task
            else 0,
            # Retry stats
            "total_retries": result.total_retries or 0,
            "max_retries_single_task": result.max_retries or 0,
            # Invalid relationship stats
            "total_invalid_relationships": result.total_invalid_relationships or 0,
            "avg_invalid_per_task": round(result.avg_invalid_per_task, 1)
            if result.avg_invalid_per_task
            else 0,
            # Shared LLM prompts (from job, same for all chunks)
            "system_prompt": job.system_prompt,
            "user_instructions": job.user_instructions,
            "relationship_instructions": job.relationship_instructions,
            # Separate parts for distinct UI display
            "user_instructions_template": job.user_instructions_template,
            "extraction_rules_template": job.extraction_rules_template,
            "entity_templates": job.entity_templates,
            "relationship_templates": job.relationship_templates,
            "domain_guidance": job.domain_guidance,
            "domain_examples": job.domain_examples,
        }

    def get_extraction_tasks_filtering_logs(
        self,
        source_id: str,
        database_name: str,
    ) -> list[dict[str, Any]]:
        """Get filtering_log column from all completed tasks for a source.

        Returns only the filtering_log field (via ``load_only``) for aggregate
        stats computation. Skips tasks without a filtering log.

        Args:
            source_id: The source ID.
            database_name: Database context.

        Returns:
            List of dicts with ``filtering_log`` key.

        """
        self._ensure_connected()

        job_stmt = (
            select(ChunkExtractionJob)
            .where(ChunkExtractionJob.source_id == source_id)
            .where(ChunkExtractionJob.database_name == database_name)
            .order_by(ChunkExtractionJob.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        job = self.session.exec(job_stmt).first()
        if not job:
            return []

        task_stmt = (
            select(ChunkExtractionTask)
            .where(ChunkExtractionTask.job_id == job.id)
            .where(ChunkExtractionTask.status == "completed")
            .where(ChunkExtractionTask.filtering_log.isnot(None))  # type: ignore[union-attr]
            .options(
                load_only(
                    ChunkExtractionTask.id,
                    ChunkExtractionTask.filtering_log,
                )
            )
        )
        tasks = list(self.session.exec(task_stmt).all())
        return self._entities_to_dicts(tasks)
