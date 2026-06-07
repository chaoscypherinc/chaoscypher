// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useState } from 'react';
import {
  useSourcesList,
  useExtractionDomains,
  useLlmQueueStats,
  useExtractionCapacity,
  useSourceQualityScores,
  DEFAULT_EXTRACTION_CAPACITY,
  type ExtractionCapacitySettings,
  type QueueStats,
} from '../../../services/api/useSources';
import type { ExtractionDomain } from '../../../services/api/sources';
import type { UnifiedSource, SourceQualityScore } from '../../../types';
import { getApiErrorMessage } from '../../../utils/errors';

interface UseSourcesDataOptions {
  pollInterval?: number;
  pauseWhenHidden?: boolean;
}

interface UseSourcesDataReturn {
  sources: UnifiedSource[];
  loading: boolean;
  error: string | null;
  domains: ExtractionDomain[];
  queueStats: QueueStats | null;
  extractionCapacity: ExtractionCapacitySettings;
  qualityScores: Map<string, SourceQualityScore>;
  refresh: (silent?: boolean) => Promise<void>;
  clearError: () => void;
}

interface Filters {
  stage: 'all' | 'queued' | 'processing' | 'active';
  status: string;
  source_type: string;
  search: string;
}

const EMPTY_SOURCES: UnifiedSource[] = [];
const EMPTY_DOMAINS: ExtractionDomain[] = [];
const EMPTY_QUALITY_SCORES = new Map<string, SourceQualityScore>();

// Any non-active, non-errored source means work is still in flight, so the
// list + queue queries should keep polling. `awaiting_confirmation` is
// poll-terminal: it rests indefinitely until a human confirms, so polling it
// would spin forever for no state change.
export function hasProcessingSources(sources: UnifiedSource[] | undefined): boolean {
  return !!sources?.some(
    (s) => s.stage !== 'active' && s.status !== 'error' && s.status !== 'awaiting_confirmation',
  );
}

/**
 * Sources-list data hook, backed by TanStack Query.
 *
 * Server state (the list, domains, queue stats, settings-derived extraction
 * capacity, quality scores) is owned by the query hooks in
 * `services/api/useSources.ts`. This hook composes them and adapts the result
 * to the shape the Sources page already consumed: a flat `sources` array,
 * a `loading` boolean, a string `error`, and a `refresh()` that re-runs the
 * list + queue queries.
 *
 * Polling: while any source is still processing (any non-active source that
 * isn't errored) the list and queue queries poll on `pollInterval`; once
 * everything settles, polling stops. TanStack already pauses background
 * refetches for a hidden tab and refetches on window focus, which replaces
 * the old manual `usePolling` + visibilitychange wiring.
 */
export function useSourcesData(
  filters: Filters,
  options: UseSourcesDataOptions = {}
): UseSourcesDataReturn {
  const { pollInterval = 3000 } = options;

  // Dismissible error: the page can clear the inline alert without forcing a
  // refetch. A successful refetch resets the query error and re-shows nothing.
  const [errorDismissed, setErrorDismissed] = useState(false);

  const sourcesQuery = useSourcesList(filters, {
    pollInterval,
    shouldPoll: hasProcessingSources,
  });
  const sources = sourcesQuery.data ?? EMPTY_SOURCES;

  const queueRefetchInterval = hasProcessingSources(sourcesQuery.data)
    ? pollInterval
    : false;

  const domainsQuery = useExtractionDomains();
  const queueStatsQuery = useLlmQueueStats({ refetchInterval: queueRefetchInterval });
  const capacityQuery = useExtractionCapacity();
  const qualityQuery = useSourceQualityScores(sources);

  const refresh = useCallback(
    async (_silent = false) => {
      setErrorDismissed(false);
      await Promise.all([sourcesQuery.refetch(), queueStatsQuery.refetch()]);
    },
    [sourcesQuery, queueStatsQuery],
  );

  const clearError = useCallback(() => {
    setErrorDismissed(true);
  }, []);

  const loadError = sourcesQuery.error;
  const error =
    loadError && !errorDismissed
      ? 'Failed to load sources: ' + getApiErrorMessage(loadError)
      : null;

  return {
    sources,
    loading: sourcesQuery.isLoading,
    error,
    domains: domainsQuery.data ?? EMPTY_DOMAINS,
    queueStats: queueStatsQuery.data ?? null,
    extractionCapacity: capacityQuery.data ?? DEFAULT_EXTRACTION_CAPACITY,
    qualityScores: qualityQuery.data ?? EMPTY_QUALITY_SCORES,
    refresh,
    clearError,
  };
}
