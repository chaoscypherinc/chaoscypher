# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Build a PublicSettings DTO from the current Settings instance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_cortex.features.settings_public.models import PublicSettings


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings


def build_public_settings(settings: Settings) -> PublicSettings:
    """Project the operator-tunable subset of Settings into the public DTO.

    Never includes secret-bearing fields. The PublicSettings shape is enforced
    to omit any field whose name contains 'password', 'secret', 'token', 'api_key'.
    """
    return PublicSettings(
        # Pagination
        pagination_default_page_size=settings.pagination.default_page_size,
        pagination_max_page_size=settings.pagination.max_page_size,
        pagination_workflow_executions_fetch_limit=settings.pagination.workflow_executions_fetch_limit,
        # Search
        search_default_result_limit=settings.search.default_result_limit,
        search_min_similarity_threshold=settings.search.min_similarity_threshold,
        search_omnibar_entity_limit=settings.search.omnibar_entity_limit,
        search_omnibar_source_limit=settings.search.omnibar_source_limit,
        search_debounce_ms=settings.search.debounce_ms,
        # Upload / batch
        batch_max_upload_files=settings.batching.max_upload_files,
        batch_max_upload_bytes=settings.batching.max_upload_bytes,
        batch_upload_timeout_ms=settings.batching.frontend_upload_timeout_ms,
        batch_batch_upload_timeout_ms=settings.batching.frontend_batch_upload_timeout_ms,
        batch_bulk_operation_size=settings.batching.bulk_operation_size,
        batch_polling_max_attempts=settings.batching.polling_max_attempts,
        batch_polling_wait_ms=settings.batching.polling_wait_ms,
        batch_export_max_attempts=settings.batching.export_max_attempts,
        batch_import_max_attempts=settings.batching.import_max_attempts,
        batch_graph_source_page_size=settings.batching.graph_source_page_size,
        # Recovery
        recovery_warn_threshold=settings.source_recovery.recovery_warn_threshold,
        recovery_max_attempts=settings.source_recovery.max_recovery_attempts,
        # Intervals
        intervals_log_poll_ms=settings.intervals.frontend_log_poll_ms,
        intervals_status_poll_ms=settings.intervals.frontend_status_poll_ms,
        intervals_log_initial_lines=settings.intervals.frontend_log_initial_lines,
        intervals_log_poll_lines=settings.intervals.frontend_log_poll_lines,
        intervals_chat_poll_ms=settings.intervals.frontend_chat_poll_ms,
        intervals_sse_recent_event_window_ms=settings.intervals.frontend_sse_recent_event_window_ms,
        intervals_mcp_stale_threshold_ms=settings.intervals.frontend_mcp_stale_threshold_ms,
        intervals_spotlight_hover_debounce_ms=settings.intervals.frontend_spotlight_hover_debounce_ms,
        # Cache
        cache_default_stale_time_ms=settings.intervals.frontend_cache_default_stale_time_ms,
        cache_default_gc_time_ms=settings.intervals.frontend_cache_default_gc_time_ms,
        cache_graph_snapshot_stale_time_ms=settings.intervals.frontend_cache_graph_snapshot_stale_time_ms,
        cache_graph_snapshot_null_refetch_ms=settings.intervals.frontend_cache_graph_snapshot_null_refetch_ms,
        cache_graph_snapshot_data_refetch_ms=settings.intervals.frontend_cache_graph_snapshot_data_refetch_ms,
        # HTTP
        http_default_timeout_ms=settings.timeouts.frontend_http_default_timeout_ms,
        # Validation
        chat_title_max_length=settings.chat_context.chat_title_max_length,
        chat_message_max_length=settings.chat_context.chat_message_max_length,
        pause_reason_max_chars=settings.pause.reason_max_chars,
    )
