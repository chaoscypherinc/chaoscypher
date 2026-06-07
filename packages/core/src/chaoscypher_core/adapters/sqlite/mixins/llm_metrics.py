# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM Metrics Storage Protocol Mixin for SqliteAdapter."""

from typing import Any

import structlog
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import load_only
from sqlmodel import func, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import LLMCallMetric, LLMDailySpend
from chaoscypher_core.analytics import compute_metrics_summary
from chaoscypher_core.ports.storage_llm_metrics import LLMMetricsStorageProtocol


logger = structlog.get_logger(__name__)


class LLMMetricsMixin(SqliteMixinBase, LLMMetricsStorageProtocol):
    """Mixin implementing LLMMetricsStorageProtocol for SQLite storage.

    Implements operations for:
    - LLM call metrics (per-call detail records)
    - Aggregation and summary computation
    - Source LLM summary updates
    """

    def create_llm_call_metric(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create an LLM call metric record.

        Args:
            data: Metric data from LLMMetricsCollector.record_attempt()

        Returns:
            Created metric as dict

        """
        self._ensure_connected()
        metric = LLMCallMetric(**data)
        self.session.add(metric)
        self._maybe_commit()
        self.session.refresh(metric)
        return self._entity_to_dict(metric)

    def create_llm_call_metrics_batch(self, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create multiple LLM call metric records in a batch.

        More efficient than creating one at a time.
        Handles FK constraint errors gracefully with rollback.

        Args:
            metrics: List of metric data dicts

        Returns:
            List of created metrics as dicts

        Raises:
            Exception: Re-raises after rollback if persistence fails

        """
        self._ensure_connected()
        if not metrics:
            return []

        try:
            entities = [LLMCallMetric(**m) for m in metrics]
            self.session.add_all(entities)
            self._maybe_commit()

            # Refresh all to get generated values
            for entity in entities:
                self.session.refresh(entity)

            return [self._entity_to_dict(e) for e in entities]
        except Exception:
            # Rollback to prevent PendingRollbackError on subsequent operations
            self.session.rollback()
            raise

    def list_llm_call_metrics(
        self,
        database_name: str,
        source_id: str | None = None,
        chunk_task_id: str | None = None,
        operation_type: str | None = None,
        success: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List LLM call metrics with optional filtering.

        Args:
            database_name: Database to query
            source_id: Filter by source file
            chunk_task_id: Filter by chunk task
            operation_type: Filter by operation type
            success: Filter by success/failure
            limit: Maximum records to return
            offset: Records to skip

        Returns:
            List of metrics as dicts, ordered by started_at desc

        """
        self._ensure_connected()

        statement = (
            select(LLMCallMetric)
            .options(
                load_only(
                    LLMCallMetric.id,
                    LLMCallMetric.database_name,
                    LLMCallMetric.source_id,
                    LLMCallMetric.chunk_task_id,
                    LLMCallMetric.operation_type,
                    LLMCallMetric.call_sequence,
                    LLMCallMetric.provider,
                    LLMCallMetric.model,
                    LLMCallMetric.input_tokens,
                    LLMCallMetric.output_tokens,
                    LLMCallMetric.duration_ms,
                    LLMCallMetric.started_at,
                    LLMCallMetric.completed_at,
                    LLMCallMetric.success,
                    LLMCallMetric.error_type,
                    LLMCallMetric.was_retry,
                    LLMCallMetric.retry_reason,
                    LLMCallMetric.chunk_index,
                    LLMCallMetric.chunk_size_chars,
                    LLMCallMetric.entities_extracted,
                    LLMCallMetric.relationships_extracted,
                    LLMCallMetric.created_at,
                )
            )
            .where(LLMCallMetric.database_name == database_name)
        )

        if source_id:
            statement = statement.where(LLMCallMetric.source_id == source_id)
        if chunk_task_id:
            statement = statement.where(LLMCallMetric.chunk_task_id == chunk_task_id)
        if operation_type:
            statement = statement.where(LLMCallMetric.operation_type == operation_type)
        if success is not None:
            statement = statement.where(LLMCallMetric.success == success)

        statement = statement.order_by(LLMCallMetric.started_at.desc())
        statement = statement.offset(offset).limit(limit)

        metrics = list(self.session.exec(statement))
        return [self._entity_to_dict(m) for m in metrics]

    def count_llm_call_metrics(
        self,
        database_name: str,
        source_id: str | None = None,
        success: bool | None = None,
    ) -> int:
        """Count LLM call metrics with optional filtering.

        Args:
            database_name: Database to query
            source_id: Filter by source file
            success: Filter by success/failure

        Returns:
            Count of matching metrics

        """
        self._ensure_connected()

        statement = select(func.count(LLMCallMetric.id)).where(
            LLMCallMetric.database_name == database_name
        )

        if source_id:
            statement = statement.where(LLMCallMetric.source_id == source_id)
        if success is not None:
            statement = statement.where(LLMCallMetric.success == success)

        result = self.session.exec(statement).one()
        return result or 0

    def compute_llm_summary(
        self,
        source_id: str,
        database_name: str,
        custom_input_cost: float = 0.0,
        custom_output_cost: float = 0.0,
    ) -> dict[str, Any]:
        """Compute aggregated LLM metrics summary for a source file.

        Uses shared compute_metrics_summary() for consistent calculations
        across CLI and Cortex paths.

        Args:
            source_id: Source file to summarize
            database_name: Database name
            custom_input_cost: Custom cost per million input tokens (for Ollama/self-hosted)
            custom_output_cost: Custom cost per million output tokens (for Ollama/self-hosted)

        Returns:
            Summary dict ready for storage on Source

        """
        self._ensure_connected()

        # Get all metrics for this file
        statement = select(LLMCallMetric).where(
            LLMCallMetric.source_id == source_id,
            LLMCallMetric.database_name == database_name,
        )
        metrics = list(self.session.exec(statement))

        if not metrics:
            return {
                "llm_total_calls": 0,
                "llm_successful_calls": 0,
                "llm_failed_calls": 0,
                "llm_retry_calls": 0,
                "llm_first_try_successes": 0,
                "llm_retry_successes": 0,
                "llm_permanent_failures": 0,
                "llm_total_input_tokens": 0,
                "llm_total_output_tokens": 0,
                "llm_wasted_tokens": 0,
                "llm_avg_call_duration_ms": None,
                "llm_total_duration_ms": 0,
                "llm_estimated_cost_usd": None,
                "llm_error_counts": {},
                "llm_model": None,
            }

        # Get primary provider and model (most common)
        provider_counts: dict[str, int] = {}
        model_counts: dict[str, int] = {}
        for m in metrics:
            if m.provider:
                provider_counts[m.provider] = provider_counts.get(m.provider, 0) + 1
            if m.model:
                model_counts[m.model] = model_counts.get(m.model, 0) + 1

        primary_provider = max(provider_counts, key=provider_counts.get) if provider_counts else ""
        primary_model = max(model_counts, key=model_counts.get) if model_counts else ""

        # Convert metrics to attempt dicts for shared helper
        attempts = [
            {
                "success": m.success,
                "was_retry": m.was_retry,
                "input_tokens": m.input_tokens,
                "output_tokens": m.output_tokens,
                "duration_ms": m.duration_ms,
                "error_type": m.error_type,
            }
            for m in metrics
        ]

        # Use shared helper for consistent calculations
        summary = compute_metrics_summary(
            attempts=attempts,
            provider=primary_provider,
            model=primary_model,
            custom_input_cost=custom_input_cost,
            custom_output_cost=custom_output_cost,
        )

        # Log outliers if detected
        if summary.get("outlier_count", 0) > 0:
            logger.info(
                "llm_metrics_outliers_detected",
                source_id=source_id,
                outlier_count=summary["outlier_count"],
                total_calls=summary["total_calls"],
            )

        # Return with llm_ prefix for Source model fields
        return {
            "llm_total_calls": summary["total_calls"],
            "llm_successful_calls": summary["successful_calls"],
            "llm_failed_calls": summary["failed_calls"],
            "llm_retry_calls": summary["retry_calls"],
            "llm_first_try_successes": summary["first_try_successes"],
            "llm_retry_successes": summary["retry_successes"],
            "llm_permanent_failures": summary["permanent_failures"],
            "llm_total_input_tokens": summary["total_input_tokens"],
            "llm_total_output_tokens": summary["total_output_tokens"],
            "llm_wasted_tokens": summary["wasted_tokens"],
            "llm_avg_call_duration_ms": summary["avg_call_duration_ms"],
            "llm_total_duration_ms": summary["total_duration_ms"],
            "llm_estimated_cost_usd": summary["estimated_cost_usd"],
            "llm_error_counts": summary["error_breakdown"],
            "llm_model": summary["model"],
            "extraction_mode": "internal",
        }

    # ------------------------------------------------------------------
    # Daily spend-cap persistence (llm_daily_spend)
    # ------------------------------------------------------------------
    # Restart-safe backing store for the daily LLM token-spend cap. The
    # in-memory LLMSpendTracker daily counter zeroes on worker restart, so a
    # crash-loop re-armed a set daily budget every restart. Persisting the
    # running total here (per database, per UTC date) closes that. Read before
    # every LLM call; incremented after. See services/llm/spend.py.

    def get_daily_token_spend(self, *, database_name: str, spend_date: str) -> int:
        """Return tokens consumed by ``database_name`` on the given UTC date.

        Args:
            database_name: Active database name.
            spend_date: UTC date as an ISO ``YYYY-MM-DD`` string.

        Returns:
            The persisted token total, or 0 when no row exists for that date
            (so the spend-cap comparison is always against an int).
        """
        self._ensure_connected()
        statement = select(LLMDailySpend.total_tokens).where(
            LLMDailySpend.database_name == database_name,
            LLMDailySpend.spend_date == spend_date,
        )
        result = self.session.exec(statement).first()
        return int(result) if result is not None else 0

    def add_daily_token_spend(self, *, database_name: str, spend_date: str, tokens: int) -> None:
        """Add ``tokens`` to the running daily total for ``(database_name, date)``.

        Atomic SQLite UPSERT (``INSERT ... ON CONFLICT DO UPDATE SET
        total_tokens = total_tokens + :delta``), inserting the row on first
        use. No-op for non-positive ``tokens`` (providers occasionally report
        0/None on streaming failures). The increment happens entirely inside
        the database so it cannot lose an update under concurrency — the Cortex
        streaming-chat handler records spend on the FastAPI process while the
        Neuron worker records on its own process, both writing this same
        per-database ``app.db`` row, so a read-modify-write window would
        interleave and drop increments.

        Args:
            database_name: Active database name.
            spend_date: UTC date as an ISO ``YYYY-MM-DD`` string.
            tokens: Token delta to add (input + output of the LLM call).
        """
        if tokens <= 0:
            return
        self._ensure_connected()
        statement = (
            sqlite_insert(LLMDailySpend)
            .values(database_name=database_name, spend_date=spend_date, total_tokens=tokens)
            .on_conflict_do_update(
                index_elements=["database_name", "spend_date"],
                set_={"total_tokens": LLMDailySpend.total_tokens + tokens},
            )
        )
        self.session.execute(statement)
        self._maybe_commit()
