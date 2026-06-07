// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the search-index health + rebuild (SearchTab).
 *
 * `useIndexStatus` reads the current index status (model + dimensions the
 * index was built with); `useRebuildIndexes` queues / runs a rebuild and
 * invalidates the status on success so the mismatch chip clears.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { searchApi } from '../../../services/api/search';
import type { IndexStatus, RebuildResult } from '../../../services/api/search';

const INDEX_STATUS_QUERY_KEY = ['settings', 'search', 'index-status'] as const;

export function useIndexStatus() {
  return useQuery<IndexStatus>({
    queryKey: INDEX_STATUS_QUERY_KEY,
    queryFn: () => searchApi.getIndexStatus(),
  });
}

export function useRebuildIndexes() {
  const qc = useQueryClient();
  return useMutation<RebuildResult, Error, void>({
    mutationFn: () => searchApi.rebuildIndexes(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: INDEX_STATUS_QUERY_KEY });
    },
  });
}
