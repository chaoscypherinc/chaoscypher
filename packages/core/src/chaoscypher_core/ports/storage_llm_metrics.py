# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLMMetricsStorageProtocol — storage contract for per-call LLM metrics.

Split from the legacy ``ports/storage.py`` god file on 2026-04-23.
Implemented by ``chaoscypher_core.adapters.sqlite.mixins.llm_metrics.LLMMetricsMixin``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMMetricsStorageProtocol(Protocol):
    """Storage protocol for LLM call metrics.

    Handles CRUD and aggregation for:
    - Individual LLM call metrics (per-call detail)
    - Summary aggregation for source files
    """

    def create_llm_call_metric(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create an LLM call metric record."""
        ...

    def create_llm_call_metrics_batch(self, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create multiple LLM call metrics in batch."""
        ...

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
        """List LLM call metrics with optional filtering."""
        ...

    def count_llm_call_metrics(
        self,
        database_name: str,
        source_id: str | None = None,
        success: bool | None = None,
    ) -> int:
        """Count LLM call metrics with optional filtering."""
        ...

    def compute_llm_summary(
        self,
        source_id: str,
        database_name: str,
        custom_input_cost: float = 0.0,
        custom_output_cost: float = 0.0,
    ) -> dict[str, Any]:
        """Compute aggregated LLM metrics summary for a source.

        Args:
            source_id: Source ID
            database_name: Database name
            custom_input_cost: Custom cost per million input tokens (for Ollama/self-hosted)
            custom_output_cost: Custom cost per million output tokens (for Ollama/self-hosted)

        Returns:
            Summary dictionary with aggregated metrics

        """
        ...
