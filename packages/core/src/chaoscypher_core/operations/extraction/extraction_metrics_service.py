# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction Metrics Service - LLM metrics persistence for chunk extraction.

Provides a standalone function for syncing LLM retry counts and persisting
per-call metrics to the storage adapter after each chunk extraction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

logger = structlog.get_logger(__name__)


def persist_chunk_metrics(
    adapter: SqliteAdapter,
    metrics_collector: Any,
    chunk_task_id: str,
    chunk_index: int,
    chunk_content: str,
    chunk_entities: list[dict[str, Any]],
    chunk_relationships: list[dict[str, Any]],
) -> None:
    """Sync LLM retry count and persist per-call metrics to storage.

    Args:
        adapter: Storage adapter.
        metrics_collector: LLM metrics collector with attempts.
        chunk_task_id: Chunk task ID.
        chunk_index: Chunk index.
        chunk_content: Full chunk text.
        chunk_entities: Extracted entities.
        chunk_relationships: Extracted relationships.
    """
    if not metrics_collector.attempts:
        return

    # Sync retry count
    llm_retry_count = sum(1 for a in metrics_collector.attempts if a.get("was_retry"))
    if llm_retry_count > 0:
        current_task = adapter.get_chunk_task(chunk_task_id)
        existing_retries = current_task.get("retry_count", 0) if current_task else 0
        adapter.update_chunk_task(
            chunk_task_id, {"retry_count": existing_retries + llm_retry_count}
        )

    # Enrich attempts with chunk context (setdefault preserves per-pass values)
    for attempt in metrics_collector.attempts:
        attempt["chunk_index"] = chunk_index
        attempt["chunk_size_chars"] = len(chunk_content)
        attempt.setdefault("entities_extracted", len(chunk_entities))
        attempt.setdefault("relationships_extracted", len(chunk_relationships))

    try:
        adapter.create_llm_call_metrics_batch(metrics_collector.get_all_attempts())
        logger.info(
            "llm_metrics_persisted",
            chunk_task_id=chunk_task_id,
            metrics_count=len(metrics_collector.attempts),
        )
    except Exception as metrics_err:
        logger.warning(
            "llm_metrics_persistence_failed",
            chunk_task_id=chunk_task_id,
            error_type=type(metrics_err).__name__,
            error_message=str(metrics_err),
            metrics_count=len(metrics_collector.attempts),
        )
