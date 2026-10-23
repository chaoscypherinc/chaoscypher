// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hook for the GraphRAG accordion (General Settings tab).
 *
 * Reads the `graphrag` section of the settings; `update` sends a partial
 * `PATCH /settings` (persisted to settings.yaml, applied to the next chat
 * without the page-wide Save) and writes the returned settings into the
 * query cache — the Cache accordion's pattern (`useCache`).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { settingsApi } from '../../../services/api/settings';
import type { Settings } from '../../../types';

export type GraphRagSettings = NonNullable<Settings['graphrag']>;

const GRAPHRAG_QUERY_KEY = ['settings', 'graphrag'] as const;

export function useGraphRagSettings() {
  const qc = useQueryClient();
  const query = useQuery<GraphRagSettings>({
    queryKey: GRAPHRAG_QUERY_KEY,
    queryFn: async () => (await settingsApi.get()).graphrag ?? {},
  });

  const updateMutation = useMutation<GraphRagSettings, Error, Partial<GraphRagSettings>>({
    mutationFn: async (patch) =>
      (await settingsApi.update({ graphrag: patch })).settings.graphrag ?? {},
    onSuccess: (graphrag) => {
      qc.setQueryData(GRAPHRAG_QUERY_KEY, graphrag);
    },
  });

  return {
    graphrag: query.data ?? null,
    loading: query.isLoading,
    error: query.error ?? updateMutation.error ?? null,
    pending: updateMutation.isPending,
    update: (patch: Partial<GraphRagSettings>) => updateMutation.mutateAsync(patch),
  };
}
