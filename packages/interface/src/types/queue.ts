// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared queue and system status types.
 *
 * Consolidates duplicated interfaces from LLMQueueMonitor components
 * and MiniSystemStatus into a single source of truth.
 */

export interface SemaphoreStats {
  max_concurrent: number;
  reserved_high_priority: number;
  active_count: number;
  active_high_priority: number;
  active_low_priority: number;
  waiting_high_priority: number;
  waiting_low_priority: number;
  total_high_priority: number;
  total_low_priority: number;
  avg_wait_time_high: number;
  avg_wait_time_low: number;
}

export interface RunningExecution {
  workflow_id: string;
  workflow_name: string;
  started_at: string;
}

/** Real-time workflow runtime status (from /api/v1/workflows/stats). */
export interface WorkflowRuntimeStats {
  currently_running: number;
  total_executed: number;
  completed: number;
  failed: number;
  avg_execution_time: number;
  success_rate: number;
  running_executions: RunningExecution[];
}

/** Simple queue stats (from /api/v1/queue/stats). */
export interface QueueStats {
  queue: string;
  queued: number;
  max_depth: number;
  running: number;
  completed_recent: number;
  failed_recent: number;
  workers: number;
}
