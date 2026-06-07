// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * PublicSettings context value, default fallback, and React context object.
 *
 * Split into its own module so PublicSettingsContext.tsx (provider component)
 * and the useAppConfig() hook can both consume the constants without tripping
 * the react-refresh `only-export-components` rule.
 */
import { createContext } from 'react';
import type { PublicSettings } from '../services/api/publicSettings';

/**
 * Defaults shipped with the SPA — used while the bootstrap fetch is in flight
 * AND as the ultimate fallback if `/api/v1/settings/public` is unreachable.
 *
 * These values must agree with the Pydantic `BaseModel` defaults in
 * `packages/core/src/chaoscypher_core/app_config/__init__.py` and
 * `packages/core/src/chaoscypher_core/settings.py`. Drift is caught by
 * `packages/cortex/tests/unit/features/settings_public/test_default_drift.py`
 * (added in PR3 Phase D).
 */
export const DEFAULT_PUBLIC_SETTINGS: PublicSettings = {
  // Pagination
  pagination_default_page_size: 50,
  pagination_max_page_size: 1000,
  pagination_workflow_executions_fetch_limit: 10_000,
  // Search
  search_default_result_limit: 10,
  search_min_similarity_threshold: 0.55,
  search_omnibar_entity_limit: 10,
  search_omnibar_source_limit: 5,
  search_debounce_ms: 300,
  // Upload / batch
  batch_max_upload_files: 20,
  batch_max_upload_bytes: 5 * 1024 * 1024 * 1024,
  batch_upload_timeout_ms: 120_000,
  batch_batch_upload_timeout_ms: 300_000,
  batch_bulk_operation_size: 50,
  batch_polling_max_attempts: 60,
  batch_polling_wait_ms: 1_000,
  batch_export_max_attempts: 120,
  batch_import_max_attempts: 180,
  batch_graph_source_page_size: 200,
  // Recovery
  recovery_warn_threshold: 5,
  recovery_max_attempts: 10,
  // Intervals
  intervals_log_poll_ms: 3_000,
  intervals_status_poll_ms: 10_000,
  intervals_log_initial_lines: 2_000,
  intervals_log_poll_lines: 200,
  intervals_chat_poll_ms: 5_000,
  intervals_sse_recent_event_window_ms: 10_000,
  intervals_mcp_stale_threshold_ms: 600_000,
  intervals_spotlight_hover_debounce_ms: 150,
  // Cache
  cache_default_stale_time_ms: 30_000,
  cache_default_gc_time_ms: 300_000,
  cache_graph_snapshot_stale_time_ms: 60_000,
  cache_graph_snapshot_null_refetch_ms: 3_000,
  cache_graph_snapshot_data_refetch_ms: 120_000,
  // HTTP
  http_default_timeout_ms: 30_000,
  // Validation
  chat_title_max_length: 500,
  chat_message_max_length: 500_000,
  pause_reason_max_chars: 500,
};

export const PublicSettingsContext = createContext<PublicSettings>(DEFAULT_PUBLIC_SETTINGS);
