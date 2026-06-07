// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for edges (relationships).
 *
 * Introduced with the EdgeDetailPage migration off raw fetch+useState.
 * `useEdge` fetches a single edge by id; `useUpdateEdge` / `useDeleteEdge`
 * are the detail-page mutations and invalidate the affected edge plus the
 * edges list on success.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { edgeApi } from './edges';
import type { Edge } from '../../types';

const EDGES_QUERY_KEY = ['edges'] as const;

function edgeQueryKey(edgeId: string) {
  return ['edge', edgeId] as const;
}

export function useEdge(edgeId: string | null | undefined) {
  return useQuery<Edge>({
    queryKey: edgeId ? edgeQueryKey(edgeId) : ['edge', 'none'],
    queryFn: () => edgeApi.get(edgeId as string),
    enabled: edgeId != null,
  });
}

export function useUpdateEdge() {
  const qc = useQueryClient();
  return useMutation<Edge, Error, { id: string; updates: Partial<Edge> }>({
    mutationFn: ({ id, updates }) => edgeApi.update(id, updates),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: edgeQueryKey(data.id) });
      void qc.invalidateQueries({ queryKey: EDGES_QUERY_KEY });
    },
  });
}

export function useDeleteEdge() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => edgeApi.delete(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: EDGES_QUERY_KEY });
    },
  });
}
