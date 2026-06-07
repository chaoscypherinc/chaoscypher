// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useQuery } from '@tanstack/react-query';
import { eventsApi, type SystemEvent } from '../../services/api/events';
import { useQueueStats, useQueueTasks } from '../../services/api/useQueue';
import { useLLMStats } from '../../services/api/useDashboard';
import { POLLING_INTERVALS } from '../../constants/config';
import type { ActivityEntry } from './types';

const ACTIVITY_EVENTS_QUERY_KEY = ['dashboard', 'activity-events'] as const;

/** Friendly operation name for progress summaries. */
function friendlyOperation(op: string): string {
  const map: Record<string, string> = {
    extract_chunk: 'Extracting',
    import_analysis: 'Analyzing',
    index_document: 'Indexing',
    chat_completion: 'Thinking',
    generate_embeddings: 'Embedding',
    commit_extraction: 'Committing',
    rebuild_search_indexes: 'Rebuilding search indexes',
  };
  return map[op] || op.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase()) + 'ing';
}

/** Progress summary for when queues are active. */
export interface ProgressSummary {
  /** e.g., "Extracting 3 chunks · 5 queued" */
  text: string;
  /** Number currently running */
  running: number;
  /** Number waiting in queue */
  queued: number;
}

function formatEventTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * Compose dashboard activity-feed reads.
 *
 * - Queue stats + queue tasks: real-time processing status. Cadence
 *   is derived from `activeCount` — 5s while jobs are running, 30s
 *   when idle. The interval naturally flips on the next refetch.
 * - System events: persistent event log; polled every 30s.
 * - LLM stats: total cost USD; same cadence as the queue tasks.
 *
 * The first render reads `activeCount = 0` (no queue stats yet) so
 * the initial cadence is "idle". As soon as the first stats payload
 * lands, the cadence flips if jobs are active.
 */
export function useActivityLog(limit: number = 5): {
  entries: ActivityEntry[];
  isIdle: boolean;
  activeCount: number;
  progress: ProgressSummary | null;
  totalCostUsd: number;
} {
  const statsQuery = useQueueStats();
  const stats = statsQuery.data;

  const queueEntries = stats?.queues ?? [];
  const runningCount = queueEntries.reduce(
    (sum, q) => sum + (q.running ?? 0),
    0,
  );
  const queuedCount = queueEntries.reduce(
    (sum, q) => sum + (q.queued ?? 0),
    0,
  );
  const activeCount = runningCount + queuedCount;

  const pollInterval =
    activeCount > 0
      ? POLLING_INTERVALS.ACTIVITY_LOG_ACTIVE
      : POLLING_INTERVALS.ACTIVITY_LOG_IDLE;

  const tasksQuery = useQueueTasks(
    { page: 1, page_size: 50 },
    { refetchInterval: pollInterval },
  );
  const llmStatsQuery = useLLMStats({ refetchInterval: pollInterval });

  const eventsQuery = useQuery({
    queryKey: [...ACTIVITY_EVENTS_QUERY_KEY, limit],
    queryFn: () => eventsApi.listEvents({ limit }),
    refetchInterval: POLLING_INTERVALS.HEALTH_CHECK,
    refetchIntervalInBackground: false,
  });

  const tasks = tasksQuery.data?.data ?? [];

  let progress: ProgressSummary | null = null;
  if (activeCount > 0) {
    const runningTasks = tasks.filter((t) => t.status === 'running');
    const queuedTasks = tasks.filter((t) => t.status === 'queued');

    const opCounts = new Map<string, number>();
    for (const t of runningTasks) {
      const op = t.operation;
      opCounts.set(op, (opCounts.get(op) ?? 0) + 1);
    }

    const parts: string[] = [];
    for (const [op, count] of opCounts) {
      parts.push(`${friendlyOperation(op)} ${count}`);
    }

    let text = parts.join(' · ');
    if (queuedTasks.length > 0) {
      text += text ? ` · ${queuedTasks.length} queued` : `${queuedTasks.length} queued`;
    }

    progress = {
      text: text || `${activeCount} tasks active`,
      running: runningCount,
      queued: queuedCount,
    };
  }

  const rawEvents: SystemEvent[] = Array.isArray(eventsQuery.data) ? eventsQuery.data : [];
  const entries: ActivityEntry[] = rawEvents.map((e) => ({
    id: String(e.id),
    time: formatEventTime(e.timestamp),
    message: e.action,
  }));

  const totalCostUsd = llmStatsQuery.data?.data?.total_cost_usd ?? 0;

  return {
    entries,
    isIdle: activeCount === 0,
    activeCount,
    progress,
    totalCostUsd,
  };
}
