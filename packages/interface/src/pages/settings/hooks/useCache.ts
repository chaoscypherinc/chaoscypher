// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hook for the Cache accordion (General Settings tab).
 *
 * Reads the cache overview; `update` (PATCH) writes the returned overview
 * straight into the query cache, `clear` (POST) refetches it so the entry
 * count and size drop to what the store now holds.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { cacheApi } from '../../../services/api/cache';
import type {
  CacheClearResult,
  CacheOverview,
  CacheUpdate,
} from '../../../services/api/cache';

const CACHE_QUERY_KEY = ['settings', 'cache'] as const;

export function useCache() {
  const qc = useQueryClient();
  const query = useQuery<CacheOverview>({
    queryKey: CACHE_QUERY_KEY,
    queryFn: () => cacheApi.getOverview(),
  });

  const updateMutation = useMutation<CacheOverview, Error, CacheUpdate>({
    mutationFn: (patch) => cacheApi.update(patch),
    onSuccess: (overview) => {
      qc.setQueryData(CACHE_QUERY_KEY, overview);
    },
  });

  const clearMutation = useMutation<CacheClearResult, Error, string>({
    mutationFn: (name) => cacheApi.clear(name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: CACHE_QUERY_KEY });
    },
  });

  return {
    overview: query.data ?? null,
    loading: query.isLoading,
    error: query.error ?? updateMutation.error ?? clearMutation.error ?? null,
    pending: updateMutation.isPending || clearMutation.isPending,
    refresh: () => query.refetch(),
    update: (patch: CacheUpdate) => updateMutation.mutateAsync(patch),
    clear: (name: string) => clearMutation.mutateAsync(name),
  };
}
