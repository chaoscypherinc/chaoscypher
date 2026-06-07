// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for workflow executions and stats.
 *
 * Introduced with the WorkflowExecutionHistoryPage / ExecutionHistoryPanel
 * migration off raw fetch+useState. `useWorkflowExecutions` lists a
 * workflow's executions; `useWorkflowExecution` fetches a single execution's
 * detail (steps + inputs/outputs); `useWorkflowStats` fetches aggregate stats.
 *
 * The list query supports an optional `pollInterval` so callers that
 * previously polled with `setInterval` while an execution was running can
 * pass `POLLING_INTERVALS.EXECUTION_HISTORY`; the query then refetches on that
 * cadence only while at least one execution is `running`/`pending`, and
 * TanStack tears the timer down on unmount. Pass `0` (or omit) to disable
 * polling entirely.
 */

import { useQuery } from '@tanstack/react-query';

import {
  workflowsApi,
  type WorkflowExecution,
  type WorkflowExecutionDetail,
  type WorkflowStats,
} from './workflows';

function executionsQueryKey(workflowId: string, maxItems: number) {
  return ['workflows', workflowId, 'executions', { maxItems }] as const;
}

function executionDetailQueryKey(workflowId: string, executionId: string) {
  return ['workflows', workflowId, 'executions', executionId] as const;
}

function workflowStatsQueryKey(workflowId: string) {
  return ['workflows', workflowId, 'stats'] as const;
}

interface UseWorkflowExecutionsOptions {
  /** Page size requested from the API (defaults to 100). */
  maxItems?: number;
  /**
   * Poll interval in ms. When > 0 the list refetches on that cadence while at
   * least one execution is `running` or `pending`; pass 0 (the default) to
   * disable polling.
   */
  pollInterval?: number;
}

function hasRunningExecution(executions: WorkflowExecution[] | undefined): boolean {
  return !!executions?.some((e) => e.status === 'running' || e.status === 'pending');
}

function isExecutionRunning(execution: WorkflowExecutionDetail | undefined): boolean {
  return execution?.status === 'running' || execution?.status === 'pending';
}

interface UseWorkflowExecutionOptions {
  /**
   * Poll interval in ms. When > 0 the detail refetches on that cadence while
   * the execution is `running` or `pending`; pass 0 (the default) to disable
   * polling. Mirrors `useWorkflowExecutions` so the builder's test-run modal
   * can poll a single execution to completion then settle.
   */
  pollInterval?: number;
}

export function useWorkflowExecutions(
  workflowId: string | null | undefined,
  { maxItems = 100, pollInterval = 0 }: UseWorkflowExecutionsOptions = {},
) {
  return useQuery<WorkflowExecution[]>({
    queryKey: workflowId
      ? executionsQueryKey(workflowId, maxItems)
      : ['workflows', 'none', 'executions', { maxItems }],
    queryFn: () => workflowsApi.listExecutions(workflowId as string, { page_size: maxItems }),
    enabled: workflowId != null,
    refetchInterval:
      pollInterval > 0
        ? (query) => (hasRunningExecution(query.state.data) ? pollInterval : false)
        : false,
  });
}

export function useWorkflowExecution(
  workflowId: string | null | undefined,
  executionId: string | null | undefined,
  { pollInterval = 0 }: UseWorkflowExecutionOptions = {},
) {
  return useQuery<WorkflowExecutionDetail>({
    queryKey:
      workflowId && executionId
        ? executionDetailQueryKey(workflowId, executionId)
        : ['workflows', 'none', 'executions', 'none'],
    queryFn: () => workflowsApi.getExecution(workflowId as string, executionId as string),
    enabled: workflowId != null && executionId != null,
    refetchInterval:
      pollInterval > 0
        ? (query) => (isExecutionRunning(query.state.data) ? pollInterval : false)
        : false,
  });
}

export function useWorkflowStats(workflowId: string | null | undefined) {
  return useQuery<WorkflowStats>({
    queryKey: workflowId ? workflowStatsQueryKey(workflowId) : ['workflows', 'none', 'stats'],
    queryFn: () => workflowsApi.getStats(workflowId as string),
    enabled: workflowId != null,
  });
}
