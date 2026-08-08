// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the queue feature.
 *
 * Consumed by:
 *   - `DashboardPage`'s activity-log (one-shot snapshot + adaptive
 *     poll); reads tasks and stats but no mutations.
 *   - `QueueMonitorPage` (paginated table + 5s auto-refresh, plus
 *     cancel and clear-history mutations).
 *
 * Both pages share cache when query keys line up (same params = same
 * key). Polling is opt-in via `refetchInterval` so callers decide
 * cadence per use case.
 *
 * Mutations:
 *   - `useCancelTask` — DELETE /queue/tasks/{id}
 *   - `useCancelAllTasks` — DELETE /queue/tasks (server-side cancel-all)
 *   - `useClearTaskHistory` — DELETE /queue/tasks/history
 * All invalidate the tasks + stats keys on success so the UI re-syncs.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';

const QUEUE_TASKS_QUERY_KEY = 'queue-tasks';
const QUEUE_STATS_QUERY_KEY = ['queue', 'stats'] as const;

export interface QueueTask {
  task_id: string;
  queue: string;
  operation: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'retried';
  priority: number;
  attempts: number;
  /** ISO-8601 UTC timestamps — the backend stores and returns strings. */
  created_at: string;
  started_at?: string;
  completed_at?: string;
  error?: string;
  error_type?: string;
  data?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface QueueStatsEntry {
  queue: string;
  queued: number;
  running: number;
  completed_recent: number;
  failed_recent: number;
  workers: number;
}

interface QueueTasksResponse {
  data: QueueTask[];
  pagination?: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
    has_next: boolean;
    has_prev: boolean;
  };
  total_in_queue?: number;
  queues?: string[] | null;
}

interface QueueStatsResponse {
  queues: QueueStatsEntry[];
  note?: string | null;
}

interface QueueTasksParams {
  page: number;
  page_size: number;
  queues?: string;
}

export function useQueueTasks(
  params: QueueTasksParams,
  opts: { refetchInterval?: number | false } = {},
) {
  return useQuery({
    queryKey: [QUEUE_TASKS_QUERY_KEY, params],
    queryFn: async (): Promise<QueueTasksResponse> => {
      const response = await apiClient.get<QueueTasksResponse>('/queue/tasks', { params });
      return response.data;
    },
    refetchInterval: opts.refetchInterval ?? false,
  });
}

export function useQueueStats(opts: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: QUEUE_STATS_QUERY_KEY,
    queryFn: async (): Promise<QueueStatsResponse> => {
      const response = await apiClient.get<QueueStatsResponse>('/queue/stats');
      return response.data;
    },
    refetchInterval: opts.refetchInterval ?? false,
  });
}

/**
 * Invalidate every cached `/queue/tasks` entry plus the stats key. The
 * tasks query key is `[QUEUE_TASKS_QUERY_KEY, params]` so we match by
 * the constant prefix.
 */
function invalidateQueue(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: [QUEUE_TASKS_QUERY_KEY] });
  void qc.invalidateQueries({ queryKey: QUEUE_STATS_QUERY_KEY });
}

export function useCancelTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => apiClient.delete(`/queue/tasks/${taskId}`),
    onSuccess: () => invalidateQueue(qc),
  });
}

interface CancelAllTasksResponse {
  cancelled: number;
  queue: string | null;
}

/**
 * Cancel ALL active tasks server-side via `DELETE /queue/tasks` —
 * not limited to the task IDs the caller happens to have on the
 * current page (the bug the old per-ID batch call had).
 */
export function useCancelAllTasks() {
  const qc = useQueryClient();
  return useMutation<CancelAllTasksResponse, Error, void>({
    mutationFn: async () => {
      const response = await apiClient.delete<CancelAllTasksResponse>('/queue/tasks');
      return response.data;
    },
    onSuccess: () => invalidateQueue(qc),
  });
}

interface ClearHistoryResponse {
  cleared: number;
}

export function useClearTaskHistory() {
  const qc = useQueryClient();
  return useMutation<ClearHistoryResponse, Error, void>({
    mutationFn: async () => {
      const response = await apiClient.delete<ClearHistoryResponse>('/queue/tasks/history');
      return response.data;
    },
    onSuccess: () => invalidateQueue(qc),
  });
}
