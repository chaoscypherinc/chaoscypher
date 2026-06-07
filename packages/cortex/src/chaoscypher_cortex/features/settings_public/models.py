# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Public settings DTO — operator-tunable subset of `Settings` exposed to the SPA.

This is the contract between the backend and the frontend for runtime-tunable
config values. Adding a field here means the frontend can read it via
useAppConfig(). NEVER include secrets.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PublicSettings(BaseModel):
    """Operator-tunable settings exposed to the SPA. NEVER include secrets."""

    # Pagination
    pagination_default_page_size: int = Field(description="Default page size for list endpoints.")
    pagination_max_page_size: int = Field(description="Cap on page_size query params.")
    pagination_workflow_executions_fetch_limit: int = Field(
        description="Max workflow execution rows per fetch."
    )

    # Search
    search_default_result_limit: int = Field(description="Default search result count.")
    search_min_similarity_threshold: float = Field(
        description="Default semantic-search similarity floor."
    )
    search_omnibar_entity_limit: int = Field(description="Omnibar: max entities returned.")
    search_omnibar_source_limit: int = Field(description="Omnibar: max sources returned.")
    search_debounce_ms: int = Field(description="Search input debounce (ms).")

    # Upload / batch
    batch_max_upload_files: int = Field(description="Max files per upload batch.")
    batch_max_upload_bytes: int = Field(description="Per-file upload size cap (bytes).")
    batch_upload_timeout_ms: int = Field(description="Frontend upload XHR timeout (ms).")
    batch_batch_upload_timeout_ms: int = Field(
        description="Frontend batch upload XHR timeout (ms)."
    )
    batch_bulk_operation_size: int = Field(description="Default bulk operation batch size.")
    batch_polling_max_attempts: int = Field(
        description="Max poll attempts for long-running operations."
    )
    batch_polling_wait_ms: int = Field(description="Poll wait between attempts (ms).")
    batch_export_max_attempts: int = Field(description="Max poll attempts for export jobs.")
    batch_import_max_attempts: int = Field(description="Max poll attempts for import jobs.")
    batch_graph_source_page_size: int = Field(description="Graph canvas source list page size.")

    # Recovery thresholds (closes the TODO in recoveryThresholds.ts)
    recovery_warn_threshold: int = Field(
        description="Recovery attempt count above which to warn the operator."
    )
    recovery_max_attempts: int = Field(
        description="Max recovery attempts before flipping source to 'error'."
    )

    # Polling / cache
    intervals_log_poll_ms: int = Field(description="Log viewer polling interval (ms).")
    intervals_status_poll_ms: int = Field(description="Service status polling interval (ms).")
    intervals_log_initial_lines: int = Field(description="Initial log lines to fetch.")
    intervals_log_poll_lines: int = Field(description="Log lines per poll.")
    intervals_chat_poll_ms: int = Field(description="Chat fallback polling interval (ms).")
    intervals_sse_recent_event_window_ms: int = Field(
        description="Skip-poll window after recent SSE event (ms)."
    )
    intervals_mcp_stale_threshold_ms: int = Field(
        description="MCP extraction staleness threshold (ms)."
    )
    intervals_spotlight_hover_debounce_ms: int = Field(
        description="Graph spotlight hover debounce (ms)."
    )

    # Cache (React Query)
    cache_default_stale_time_ms: int = Field(description="React Query default staleTime (ms).")
    cache_default_gc_time_ms: int = Field(description="React Query default gcTime (ms).")
    cache_graph_snapshot_stale_time_ms: int = Field(
        description="Graph snapshot query staleTime (ms)."
    )
    cache_graph_snapshot_null_refetch_ms: int = Field(
        description="Graph snapshot refetch when null (ms)."
    )
    cache_graph_snapshot_data_refetch_ms: int = Field(
        description="Graph snapshot refetch when data exists (ms)."
    )

    # HTTP
    http_default_timeout_ms: int = Field(description="Frontend HTTP client default timeout (ms).")

    # Validation lengths (mirrors operator-tunable settings)
    chat_title_max_length: int = Field(
        description="Max chat title length (mirrors backend validator)."
    )
    chat_message_max_length: int = Field(
        description="Max chat message length (mirrors backend validator)."
    )
    pause_reason_max_chars: int = Field(description="Max pause reason length.")
