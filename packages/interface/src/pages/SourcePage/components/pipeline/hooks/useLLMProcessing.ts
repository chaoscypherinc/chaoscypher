// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Manages the async state for per-chunk extraction tasks, stats, and
 * chart data consumed by the Overview tab's Pipeline Flow section and
 * the Chunks tab's Chunk Overview band.
 *
 * Migrated 2026-05-18 from raw `useReducer` + `useEffect` to TanStack
 * Query so the per-chunk rerun mutation's `invalidateQueries` on
 * `['source', sourceId]` (see `useChunkRerun`) actually refreshes the
 * chart cells in the UI. Query keys live under `['source', sourceId, …]`
 * so any source-scoped mutation that invalidates the parent key cascades
 * to all of them.
 */

import { useCallback, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { sourcesApi } from '../../../../../services/api/sources';
import type {
  ExtractionTask,
  ExtractionTaskStats,
  ExtractionChartTask,
} from '../../../../../types';

export interface LLMProcessingState {
  chartTasks: ExtractionChartTask[];
  stats: ExtractionTaskStats | null;
  loading: boolean;
  selectedChunkId: string | null;
  selectedTask: ExtractionTask | null;
  selectedTaskLoading: boolean;
}

interface UseLLMProcessingReturn {
  state: LLMProcessingState;
  selectChunk: (id: string | null) => void;
}

export function useLLMProcessing(sourceId: string, enabled: boolean = true): UseLLMProcessingReturn {
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);

  const statsQuery = useQuery({
    queryKey: ['source', sourceId, 'extraction-task-stats'] as const,
    queryFn: () => sourcesApi.getExtractionTaskStats(sourceId),
    enabled,
    refetchOnWindowFocus: false,
  });

  const chartTasksQuery = useQuery({
    queryKey: ['source', sourceId, 'extraction-chart-tasks'] as const,
    queryFn: () => sourcesApi.getExtractionTasksForCharts(sourceId),
    enabled,
    refetchOnWindowFocus: false,
  });

  const selectedTaskQuery = useQuery({
    queryKey: ['source', sourceId, 'extraction-task', selectedChunkId] as const,
    queryFn: () => sourcesApi.getExtractionTask(sourceId, selectedChunkId as string),
    enabled: enabled && selectedChunkId !== null,
    refetchOnWindowFocus: false,
  });

  const selectChunk = useCallback((id: string | null) => {
    setSelectedChunkId(id);
  }, []);

  return {
    state: {
      chartTasks: chartTasksQuery.data ?? [],
      stats: statsQuery.data ?? null,
      loading: statsQuery.isLoading || chartTasksQuery.isLoading,
      selectedChunkId,
      selectedTask: selectedChunkId === null ? null : selectedTaskQuery.data ?? null,
      selectedTaskLoading: selectedChunkId !== null && selectedTaskQuery.isLoading,
    },
    selectChunk,
  };
}
