// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useDashboardData: single-source live-status polling for the UI.
 *
 * Replaces the previous fan-out where MiniSystemStatus, useSourcesData,
 * and LLMQueueMonitor each independently polled overlapping subsets of
 * /llm/stats, /workflows/stats, /queue/stats, /counts, and
 * /system/processing/status — at one point three components polled
 * /llm/stats every 2s in parallel, with each call opening its own
 * SQLite session.
 *
 * Now: one poll, one HTTP round-trip, one SQLite session, distributed
 * to consumers via DashboardContext.
 *
 * The per-feature endpoints (/llm/stats, /workflows/stats, etc.) are
 * NOT removed — they have legitimate non-polling consumers (the
 * LLMQueueMonitor task list, SourcePage stats tiles, settings UIs).
 * The dashboard endpoint exists alongside them as the optimized path
 * for live polling consumers.
 */

import { useCallback, useState } from 'react';
import { POLLING_INTERVALS } from '../constants/config';
import { apiClient } from '../services/api/client';
import { logger } from '../utils/logger';
import { usePolling } from './usePolling';
import type { QueueStats, SemaphoreStats, WorkflowRuntimeStats } from '../types/queue';

/** LLM queue + cost stats, mirrors the /llm/stats response. */
export interface DashboardLLMStats {
  total_queued: number;
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  semaphore_stats?: SemaphoreStats;
}

/** Knowledge counts, mirrors the /counts response. */
export interface DashboardCounts {
  knowledge_nodes: number;
  links: number;
  templates: number;
  workflows: number;
  lenses?: number;
  sources?: number;
  /** Count of sources parked awaiting domain confirmation. */
  awaiting_confirmation?: number;
}

/** System pause status, mirrors /system/processing/status. */
export interface DashboardProcessingStatus {
  paused: boolean;
  paused_at: string | null;
  reason: string | null;
}

/** Full /system/dashboard payload. */
export interface DashboardData {
  counts: DashboardCounts | null;
  llm: DashboardLLMStats | null;
  queue: QueueStats[];
  workflows: WorkflowRuntimeStats | null;
  processing: DashboardProcessingStatus;
}

const DEFAULT_DATA: DashboardData = {
  counts: null,
  llm: null,
  queue: [],
  workflows: null,
  processing: { paused: false, paused_at: null, reason: null },
};

interface DashboardResponseShape {
  counts: DashboardCounts;
  llm: { data: DashboardLLMStats } | DashboardLLMStats;
  queue: { queues: QueueStats[] };
  workflows: WorkflowRuntimeStats;
  processing: DashboardProcessingStatus;
}

export interface UseDashboardDataResult {
  data: DashboardData;
  loading: boolean;
  refresh: () => Promise<void>;
}

interface UseDashboardDataOptions {
  /** Poll interval in ms. Defaults to MINI_STATUS (2s) for live status feel. */
  interval?: number;
  /** Pause polling when the tab is hidden. Defaults to true. */
  pauseWhenHidden?: boolean;
}

/**
 * Polls the aggregated /system/dashboard endpoint and returns the latest snapshot.
 *
 * Use this once at the top of the component tree (via DashboardProvider) and
 * read the data via useDashboard() in descendants — running it multiple times
 * defeats the consolidation.
 */
export function useDashboardData(
  options: UseDashboardDataOptions = {},
): UseDashboardDataResult {
  const { interval = POLLING_INTERVALS.MINI_STATUS, pauseWhenHidden = true } = options;
  const [data, setData] = useState<DashboardData>(DEFAULT_DATA);
  const [loading, setLoading] = useState(true);

  const fetchDashboard = useCallback(async () => {
    try {
      const response = await apiClient.get<DashboardResponseShape>('/system/dashboard');
      const payload = response.data;
      // /llm/stats wraps its payload as { data: { ... } } — the
      // dashboard endpoint inlines that shape, but tolerate both so a
      // future backend change doesn't surprise the UI.
      const llm =
        payload.llm && 'data' in payload.llm
          ? (payload.llm as { data: DashboardLLMStats }).data
          : (payload.llm as DashboardLLMStats);
      setData({
        counts: payload.counts,
        llm,
        queue: payload.queue?.queues ?? [],
        workflows: payload.workflows,
        processing: payload.processing,
      });
      setLoading(false);
    } catch (error) {
      // Backend may be momentarily unavailable during restart — keep
      // the last good snapshot rather than blanking the UI.
      logger.error('Failed to load dashboard data:', error);
      setLoading(false);
    }
  }, []);

  usePolling({
    onPoll: fetchDashboard,
    interval,
    pauseWhenHidden,
    immediate: true,
  });

  return { data, loading, refresh: fetchDashboard };
}
