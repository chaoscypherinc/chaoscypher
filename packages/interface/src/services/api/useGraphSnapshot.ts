// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAppConfig } from '../../contexts/useAppConfig';
import { getGraphSnapshot, refreshGraphSnapshot } from './graphSnapshot';

export const GRAPH_SNAPSHOT_QUERY_KEY = ['graph', 'snapshot'] as const;

/**
 * Fetch the current graph snapshot.
 *
 * Returns `null` when no snapshot has been built yet (server 204). The GET
 * endpoint enqueues a build on its own when it sees no snapshot, so we poll
 * aggressively while data is absent so the dashboard crossfades in as soon
 * as the worker finishes. Once a snapshot is populated, we drop to a slower
 * cadence that matches staleness-triggered rebuilds. All three intervals
 * (stale, null-refetch, data-refetch) are operator-tunable via
 * `cache_graph_snapshot_*` settings.
 */
export function useGraphSnapshot() {
  const config = useAppConfig();
  return useQuery({
    queryKey: GRAPH_SNAPSHOT_QUERY_KEY,
    queryFn: getGraphSnapshot,
    staleTime: config.cache_graph_snapshot_stale_time_ms,
    refetchInterval: (query) =>
      query.state.data == null
        ? config.cache_graph_snapshot_null_refetch_ms
        : config.cache_graph_snapshot_data_refetch_ms,
    refetchOnWindowFocus: true,
  });
}

/**
 * Trigger a background rebuild of the graph snapshot.
 *
 * On success, waits 3 seconds (rebuild is ~seconds) then invalidates the
 * snapshot query so the UI re-fetches the freshly-built snapshot.
 */
export function useRefreshGraphSnapshot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: refreshGraphSnapshot,
    onSuccess: () => {
      setTimeout(() => {
        void qc.invalidateQueries({ queryKey: GRAPH_SNAPSHOT_QUERY_KEY });
      }, 3000);
    },
  });
}
