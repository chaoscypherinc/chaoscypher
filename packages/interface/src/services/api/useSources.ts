// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the Sources list view.
 *
 * Introduced with the Sources-list migration off raw fetch+useState+usePolling.
 * `useSourcesList` is the unified list query (server-state for the table) and
 * accepts the same filter params the page already had; the others back the
 * upload dialog's domain chips, the processing-queue completion estimates,
 * and the per-source quality scores. While any source is still processing the
 * page bumps the `refetchInterval` so the list/queue poll like the old
 * `usePolling` loop did, then settles to no polling once everything is active.
 *
 * Detail-page source queries (the `['source', id]` keys) live elsewhere — the
 * list uses the `['sources']` key so it never collides with them.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

import { sourcesApi } from './sources';
import type { ExtractionDomain } from './sources';
import type { ConfirmExtractionOptions } from './sourceProcessing';
import { settingsApi } from './settings';
import { qualityApi } from './quality';
import type { UnifiedSource, SourceQualityScore, SourceSummary } from '../../types';

const SOURCES_QUERY_KEY = ['sources'] as const;
const SOURCE_DOMAINS_QUERY_KEY = ['sources', 'domains'] as const;
const LLM_STATS_QUERY_KEY = ['llm', 'stats'] as const;
const EXTRACTION_CAPACITY_QUERY_KEY = ['sources', 'extraction-capacity'] as const;

function sourceSummariesQueryKey(pageSize: number) {
  return ['sources', 'summaries', { pageSize }] as const;
}

export interface SourcesListFilters {
  stage?: 'all' | 'queued' | 'processing' | 'active';
  status?: string;
  source_type?: string;
  search?: string;
}

export interface QueueStats {
  estimated_completion_times_human?: { llm: string; operations: string };
}

export interface ExtractionCapacitySettings {
  contextWindow: number;
  groupSize: number;
  inputPerChunk: number;
  outputPerChunk: number;
}

// Defaults match backend defaults (used while settings are loading).
const DEFAULT_EXTRACTION_CAPACITY: ExtractionCapacitySettings = {
  contextWindow: 8192,
  groupSize: 4,
  inputPerChunk: 150,
  outputPerChunk: 2000,
};

interface UseSourcesListOptions {
  /**
   * Poll interval in ms. While any source is still processing the caller
   * wants the list to keep refetching; once everything settles it should
   * stop. `refetchInterval` is evaluated from the latest query data so the
   * caller passes a predicate rather than re-subscribing.
   */
  pollInterval?: number;
  shouldPoll?: (sources: UnifiedSource[] | undefined) => boolean;
}

interface UseLlmQueueStatsOptions {
  refetchInterval?: number | false;
}

/**
 * Unified source list. `listUnified` maps the paginated source summaries to
 * `UnifiedSource` and applies the stage filter client-side, so the query key
 * carries the full filter set.
 */
export function useSourcesList(
  filters: SourcesListFilters,
  options: UseSourcesListOptions = {},
) {
  const { pollInterval, shouldPoll } = options;
  return useQuery<UnifiedSource[]>({
    queryKey: [...SOURCES_QUERY_KEY, filters],
    queryFn: () =>
      sourcesApi.listUnified({
        stage: filters.stage === 'all' ? undefined : filters.stage,
        status: filters.status || undefined,
        source_type: filters.source_type || undefined,
        search: filters.search || undefined,
      }),
    refetchInterval:
      pollInterval && shouldPoll
        ? (query) => (shouldPoll(query.state.data) ? pollInterval : false)
        : false,
  });
}

interface UseSourceSummariesOptions {
  /** Defer the fetch until truthy (e.g. only when a filter menu is open). */
  enabled?: boolean;
}

/**
 * Raw paginated source summaries (`SourceSummary[]`) at a caller-supplied page
 * size. Unlike `useSourcesList` this does no stage filtering or `UnifiedSource`
 * mapping — it backs the graph-canvas filter dropdown, which needs the
 * summary projection (title/filename) verbatim. `enabled` lets the popover
 * caller defer the fetch until it opens.
 */
export function useSourceSummaries(
  pageSize: number,
  options: UseSourceSummariesOptions = {},
) {
  return useQuery<SourceSummary[]>({
    queryKey: sourceSummariesQueryKey(pageSize),
    queryFn: async () => {
      const response = await sourcesApi.list({ page_size: pageSize });
      return response.data;
    },
    enabled: options.enabled ?? true,
  });
}

export function useExtractionDomains() {
  return useQuery<ExtractionDomain[]>({
    queryKey: SOURCE_DOMAINS_QUERY_KEY,
    queryFn: () => sourcesApi.listDomains(),
  });
}

export function useLlmQueueStats(options: UseLlmQueueStatsOptions = {}) {
  const { refetchInterval = false } = options;
  return useQuery<QueueStats>({
    queryKey: LLM_STATS_QUERY_KEY,
    queryFn: async () => {
      const res = await sourcesApi.getLlmStats();
      return res.data as QueueStats;
    },
    refetchInterval,
  });
}

/**
 * Extraction-capacity inputs for the upload dialog's domain chips, derived
 * from the settings endpoint with backend-default fallbacks.
 */
export function useExtractionCapacity() {
  return useQuery<ExtractionCapacitySettings>({
    queryKey: EXTRACTION_CAPACITY_QUERY_KEY,
    queryFn: async () => {
      const settings = await settingsApi.get();
      return {
        contextWindow:
          settings.llm?.ollama_num_ctx || settings.llm?.ai_context_window || DEFAULT_EXTRACTION_CAPACITY.contextWindow,
        groupSize: settings.chunking?.group_size || DEFAULT_EXTRACTION_CAPACITY.groupSize,
        inputPerChunk: Math.floor((settings.chunking?.small_chunk_size || 600) / 4),
        outputPerChunk: settings.chunking?.output_tokens_per_chunk || DEFAULT_EXTRACTION_CAPACITY.outputPerChunk,
      };
    },
  });
}

/** IDs of active sources that actually carry extraction data worth scoring. */
function activeSourceIdsForScoring(sources: UnifiedSource[]): string[] {
  return sources
    .filter(
      (s) =>
        s.stage === 'active' &&
        ((s.active?.entities_count ?? 0) > 0 || (s.active?.relationships_count ?? 0) > 0),
    )
    .map((s) => s.id);
}

/**
 * Quality scores for active sources, keyed by source id. Disabled (and resolves
 * to an empty map) when no active source carries extraction data, mirroring the
 * old hook's "skip the call entirely" behaviour.
 */
export function useSourceQualityScores(sources: UnifiedSource[]) {
  const ids = activeSourceIdsForScoring(sources);
  return useQuery<Map<string, SourceQualityScore>>({
    // Key on the id set so the scores refetch when the active sources change.
    queryKey: ['sources', 'quality', [...ids].sort()],
    queryFn: async () => {
      if (ids.length === 0) return new Map<string, SourceQualityScore>();
      const result = await qualityApi.analyze({ source_ids: ids, min_entities: 0 });
      const scoresMap = new Map<string, SourceQualityScore>();
      for (const score of result.sources) {
        scoresMap.set(score.source_id, score);
      }
      return scoresMap;
    },
  });
}

/**
 * Confirm the domain + extraction options for a parked source. On success
 * invalidates the whole sources list family so the row drops out of the
 * awaiting filter and the badge count refreshes. The list query key is
 * `[...SOURCES_QUERY_KEY, filters]` (see useSourcesList:88) — invalidating
 * the SOURCES_QUERY_KEY prefix matches every filter variant.
 */
export function useConfirmExtraction() {
  const qc = useQueryClient();
  return useMutation<
    { source_id: string; status: string },
    Error,
    { sourceId: string; options: ConfirmExtractionOptions }
  >({
    mutationFn: ({ sourceId, options }) =>
      sourcesApi.confirmExtraction(sourceId, options),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SOURCES_QUERY_KEY });
    },
  });
}

export { DEFAULT_EXTRACTION_CAPACITY };
