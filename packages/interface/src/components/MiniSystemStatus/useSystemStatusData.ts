// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSystemStatusData: derives MiniSystemStatus display state from
 * the shared dashboard snapshot (DashboardContext).
 *
 * Previously this hook ran its own polling loop fetching /workflows/stats,
 * /llm/stats, /queue/stats, /counts every 2s in parallel — combined
 * with parallel polling from useSourcesData and LLMQueueMonitor, that
 * produced 3x duplicate /llm/stats and 2x duplicate /workflows/stats
 * calls per cycle plus an SQLite session for each. Now it reads from
 * the single shared dashboard poll (see useDashboardData and
 * DashboardProvider).
 */

import { useDashboard } from '../../contexts/useDashboard';
import { useSystemHealth } from '../../hooks/useSystemHealth';
import type { QueueStats, SemaphoreStats, WorkflowRuntimeStats } from '../../types/queue';

/** LLM queue statistics with optional semaphore detail. */
interface LLMQueueStats {
  total_queued: number;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  semaphore_stats?: SemaphoreStats;
}

/** Knowledge count data — same shape as /counts. */
export interface KnowledgeCounts {
  knowledge_nodes: number;
  links: number;
  templates: number;
  workflows: number;
}

/** Return value of the useSystemStatusData hook. */
interface UseSystemStatusDataResult {
  /** Workflow runtime stats, or null when unavailable. */
  workflowStats: WorkflowRuntimeStats | null;
  /** Aggregated LLM queue stats. */
  llmStats: LLMQueueStats | null;
  /** Per-queue stats array. */
  queueStats: QueueStats[];
  /** Knowledge entity/relationship/template/workflow counts. */
  counts: KnowledgeCounts | null;
  /** Whether the initial load is still in progress. */
  loading: boolean;
  /** System health payload from useSystemHealth. */
  health: ReturnType<typeof useSystemHealth>['health'];
  /** Whether health initial load is in progress. */
  healthLoading: boolean;
  /** Whether any health check reports an error. */
  hasErrors: boolean;
  /** Whether any health check reports a warning. */
  hasWarnings: boolean;
  /** Whether any processing activity is currently happening. */
  isActivityActive: boolean;
  /** Total knowledge item count (entities + links + templates + workflows). */
  totalKnowledge: number;
  /** Sources parked awaiting domain confirmation (0 when none/unavailable). */
  awaitingConfirmationCount: number;
  /** Computed status text for the indicator. */
  getStatusText: (isSystemPaused: boolean) => string;
}

/**
 * Hook that derives MiniSystemStatus display state from the shared
 * dashboard snapshot.
 *
 * The dashboard snapshot is provided by the top-level
 * DashboardProvider — this hook does not poll directly. The combined
 * system-health poll is kept separate (different cadence: 30s).
 */
export function useSystemStatusData(): UseSystemStatusDataResult {
  const { data, loading } = useDashboard();
  const { health, loading: healthLoading, hasErrors, hasWarnings } = useSystemHealth();

  const queueStats = data.queue;
  const counts: KnowledgeCounts | null = data.counts
    ? {
        knowledge_nodes: data.counts.knowledge_nodes,
        links: data.counts.links,
        templates: data.counts.templates,
        workflows: data.counts.workflows,
      }
    : null;

  // Derive LLM total_queued from per-queue counts to match the
  // previous behavior (the standalone /llm/stats endpoint returned
  // the LLM queue depth, but the MiniSystemStatus indicator wants
  // the combined queued+running across queues).
  const llmStats: LLMQueueStats | null = data.llm
    ? {
        ...data.llm,
        total_queued: queueStats.reduce(
          (sum, q) => sum + (q.queued || 0) + (q.running || 0),
          0,
        ),
      }
    : null;

  const workflowStats = data.workflows;

  const isActivityActive = (() => {
    const isSemaphoreActive =
      llmStats?.semaphore_stats?.active_count && llmStats.semaphore_stats.active_count > 0;
    const hasRunningTasks = queueStats.some(q => q.running > 0);
    const hasQueuedTasks = queueStats.some(q => q.queued > 0);
    const hasWorkflows = workflowStats && workflowStats.currently_running > 0;
    return !!(isSemaphoreActive || hasRunningTasks || hasQueuedTasks || hasWorkflows);
  })();

  const totalKnowledge = counts
    ? counts.knowledge_nodes + counts.links + counts.templates + counts.workflows
    : 0;

  const awaitingConfirmationCount = data.counts?.awaiting_confirmation ?? 0;

  const getStatusText = (isSystemPaused: boolean): string => {
    if (loading && healthLoading) return 'Checking...';

    if (isSystemPaused) return 'Paused';

    if (hasErrors && health?.checks) {
      const errorCount = Object.values(health.checks).filter(c => c.status === 'error').length;
      return `${errorCount} error${errorCount > 1 ? 's' : ''}`;
    }

    const isSemaphoreActive =
      llmStats?.semaphore_stats?.active_count && llmStats.semaphore_stats.active_count > 0;
    const llmQueue = queueStats.find(q => q.queue === 'llm');
    const opsQueue = queueStats.find(q => q.queue === 'operations');
    const hasWorkflows = workflowStats && workflowStats.currently_running > 0;
    const totalRunning = (llmQueue?.running || 0) + (opsQueue?.running || 0);
    const totalQueued = (llmQueue?.queued || 0) + (opsQueue?.queued || 0);

    if (isSemaphoreActive) {
      const activeCount = llmStats!.semaphore_stats!.active_count;
      const maxCount = llmStats!.semaphore_stats!.max_concurrent;
      return `LLM ${activeCount}/${maxCount}`;
    }
    if (totalRunning > 0) {
      const parts: string[] = [];
      if (llmQueue?.running) parts.push(`${llmQueue.running} LLM`);
      if (opsQueue?.running) parts.push(`${opsQueue.running} ops`);
      if (hasWorkflows) parts.push(`${workflowStats!.currently_running} wf`);
      return `${parts.join(' + ')} running`;
    }
    if (totalQueued > 0) return `${totalQueued} queued`;
    if (hasWorkflows)
      return `${workflowStats!.currently_running} workflow${workflowStats!.currently_running > 1 ? 's' : ''}`;

    if (hasWarnings && health?.checks) {
      const warningCount = Object.values(health.checks).filter(c => c.status === 'warning').length;
      return `${warningCount} warning${warningCount > 1 ? 's' : ''}`;
    }

    // Green state - show total knowledge count
    return totalKnowledge > 0 ? totalKnowledge.toLocaleString() : '0';
  };

  return {
    workflowStats,
    llmStats,
    queueStats,
    counts,
    loading,
    health,
    healthLoading,
    hasErrors,
    hasWarnings,
    isActivityActive,
    totalKnowledge,
    awaitingConfirmationCount,
    getStatusText,
  };
}
