// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Application-wide configuration constants.
 *
 * Operator-tunable values (search debounce, batch sizes, polling
 * waits, export/import attempt caps) are sourced from
 * `DEFAULT_PUBLIC_SETTINGS` so the frontend default matches the
 * backend Pydantic default. UI-only timing values (mini-status,
 * health-check, layout counts, etc.) stay as local literals — they
 * govern client-side UX cadence and aren't operator-meaningful.
 *
 * TODO(PR3-followup): The four `BATCH_CONFIG` constants and
 * `POLLING_INTERVALS.SEARCH_DEBOUNCE` are still consumed at module
 * scope (services and a non-hook code path). Switch their consumers
 * to `useAppConfig()` so live operator changes propagate without
 * page reload. Tracked in the eliminate-hardcoded-values plan.
 */
import { DEFAULT_PUBLIC_SETTINGS } from '../contexts/publicSettingsContextValue';

export const POLLING_INTERVALS = {
  LAYOUT_COUNTS: 60_000,
  MINI_STATUS: 2_000,
  QUEUE_STATS: 10_000,
  SEARCH_DEBOUNCE: DEFAULT_PUBLIC_SETTINGS.search_debounce_ms,
  HEALTH_CHECK: 30_000,
  EVENT_TOASTS: 10_000,
  QUEUE_MONITOR: 2_000,
  EXECUTION_HISTORY: 5_000,
  TEST_EXECUTION: 1_000,
  ACTIVITY_LOG_ACTIVE: 5_000,
  ACTIVITY_LOG_IDLE: 30_000,
} as const;

export const BATCH_CONFIG = {
  BULK_OPERATION_SIZE: DEFAULT_PUBLIC_SETTINGS.batch_bulk_operation_size,
  POLLING_MAX_ATTEMPTS: DEFAULT_PUBLIC_SETTINGS.batch_polling_max_attempts,
  POLLING_WAIT_MS: DEFAULT_PUBLIC_SETTINGS.batch_polling_wait_ms,
  EXPORT_MAX_ATTEMPTS: DEFAULT_PUBLIC_SETTINGS.batch_export_max_attempts,
  IMPORT_MAX_ATTEMPTS: DEFAULT_PUBLIC_SETTINGS.batch_import_max_attempts,
} as const;
