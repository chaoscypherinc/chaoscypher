// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the per-chunk rerun + attempts endpoints.
 *
 *   POST /api/v1/sources/{source_id}/chunks/{chunk_index}/rerun
 *   GET  /api/v1/sources/{source_id}/chunks/{chunk_index}/attempts
 *   GET  /api/v1/sources/{source_id}/chunks/{chunk_index}/attempts/{attempt_id}
 *
 * Mutation: useRerunChunk — on success invalidates the source detail +
 * source extraction-tasks queries + the per-chunk attempts list.
 * Query: useChunkAttempts — drives the Attempts section inside the
 * expanded chunk row on SourcePage.
 *
 * Mirrors useVisionPages.ts.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { UseMutationResult } from '@tanstack/react-query';

import { apiClient } from './client';
import type {
  ChunkRerunResponse,
  ChunkAttemptDetail,
} from './sourceProcessing';

// ========================================
// Query keys
// ========================================

const CHUNK_ATTEMPTS_QUERY_KEY = (sourceId: string, chunkIndex: number) =>
  ['source', sourceId, 'chunk-attempts', chunkIndex] as const;

// ========================================
// Mutation: rerun one chunk
// ========================================

export function useRerunChunk(
  sourceId: string,
): UseMutationResult<ChunkRerunResponse, Error, { chunkIndex: number }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ chunkIndex }) => {
      const response = await apiClient.post<ChunkRerunResponse>(
        `/sources/${encodeURIComponent(sourceId)}/chunks/${chunkIndex}/rerun`,
      );
      return response.data;
    },
    onSuccess: (_data, { chunkIndex }) => {
      void qc.invalidateQueries({ queryKey: ['source', sourceId] });
      void qc.invalidateQueries({
        queryKey: CHUNK_ATTEMPTS_QUERY_KEY(sourceId, chunkIndex),
      });
    },
  });
}

// ========================================
// One-shot fetch: full attempt body (not a hook — called from the
// AttemptsList expand-on-click handler)
// ========================================

export async function fetchChunkAttempt(
  sourceId: string,
  chunkIndex: number,
  attemptId: string,
): Promise<ChunkAttemptDetail> {
  const response = await apiClient.get<ChunkAttemptDetail>(
    `/sources/${encodeURIComponent(sourceId)}/chunks/${chunkIndex}/attempts/${encodeURIComponent(attemptId)}`,
  );
  return response.data;
}
