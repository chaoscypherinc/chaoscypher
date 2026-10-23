// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
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
import { SITES_QUERY_KEY } from '../../../services/api/sites';
import { useSites } from '../../../services/api/useSites';
import type { ExtractionDomain } from '../../../services/api/sources';
import type { Site, UnifiedSource, SourceQualityScore } from '../../../types';
import { getApiErrorMessage } from '../../../utils/errors';

interface UseSourcesDataOptions {
  pollInterval?: number;
  pauseWhenHidden?: boolean;
}

interface UseSourcesDataReturn {
  sources: UnifiedSource[];
  /** The imported sites, one row each (empty while the list is filtered). */
  sites: Site[];
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

/** How often the sources list (and an expanded site's pages) polls while work is in flight. */
export const SOURCES_POLL_INTERVAL_MS = 3000;

const EMPTY_SOURCES: UnifiedSource[] = [];
const EMPTY_SITES: Site[] = [];

/**
 * Whether the list is unfiltered, so each imported site shows as one row with
 * its pages beneath it. A filtered list (search, status, type, stage) searches
 * every source, a site's pages included, and sets the site rows aside.
 */
function groupsSites(filters: Filters): boolean {
  return filters.stage === 'all' && !filters.status && !filters.source_type && !filters.search;
}
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
 * the old manual `usePolling` + visibilitychange wiring. The imported sites
 * poll the same way while any of them is being imported or checked.
 */
export function useSourcesData(
  filters: Filters,
  options: UseSourcesDataOptions = {}
): UseSourcesDataReturn {
  const { pollInterval = SOURCES_POLL_INTERVAL_MS } = options;

  // Dismissible error: the page can clear the inline alert without forcing a
  // refetch. A successful refetch resets the query error and re-shows nothing.
  const [errorDismissed, setErrorDismissed] = useState(false);

  const queryClient = useQueryClient();
  const grouped = groupsSites(filters);
  const listFilters = useMemo(
    () => ({ ...filters, in_site: grouped ? false : undefined }),
    [filters, grouped],
  );
  const sourcesQuery = useSourcesList(listFilters, {
    pollInterval,
    shouldPoll: hasProcessingSources,
  });
  const sources = sourcesQuery.data ?? EMPTY_SOURCES;
  const sitesQuery = useSites({ pollInterval, enabled: grouped });
  const sites = grouped ? (sitesQuery.data ?? EMPTY_SITES) : EMPTY_SITES;

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
      await Promise.all([
        sourcesQuery.refetch(),
        queueStatsQuery.refetch(),
        queryClient.invalidateQueries({ queryKey: SITES_QUERY_KEY }),
      ]);
    },
    [sourcesQuery, queueStatsQuery, queryClient],
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
    sites,
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
