// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';
import type { components } from '../../types/generated/api';

export type ChunkDetail = components['schemas']['ChunkResponse'];

export function useChunkDetail(sourceId: string, chunkId: string | null) {
  return useQuery<ChunkDetail>({
    queryKey: ['chunk-detail', sourceId, chunkId],
    queryFn: async () => {
      const response = await apiClient.get<ChunkDetail>(
        `/sources/${sourceId}/chunks/${chunkId}`,
      );
      return response.data;
    },
    enabled: !!chunkId,
    staleTime: 60_000,
  });
}
