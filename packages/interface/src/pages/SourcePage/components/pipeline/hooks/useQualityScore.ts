// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Quality score fetch + recalculate logic.
 *
 * Migrated 2026-05-25 from raw `useEffect` + `useState` to TanStack Query.
 * The score is source-scoped, so it lives under the shared
 * ``['source', sourceId, …]`` key family. ``recalculateQuality`` flips a
 * force-recalculate flag and refetches, so the same query cache entry is
 * updated in place (the backend bypasses its cache when forced).
 *
 * Extracted from ``OverviewTab/hooks/useOverviewData.ts`` into the shared
 * ``pipeline/`` directory (2026-05-11 restructure). The Overview tab's
 * Quality tile reads its grade from this hook; clicking the tile opens
 * the QualityBreakdownDialog in place — no tab navigation involved.
 */

import { useCallback, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { qualityApi } from '../../../../../services/api/quality';
import type { SourceQualityScore } from '../../../../../types';

interface UseQualityScoreReturn {
  qualityScore: SourceQualityScore | null;
  qualityLoading: boolean;
  recalculateQuality: () => Promise<void>;
}

export function useQualityScore(sourceId: string, enabled: boolean): UseQualityScoreReturn {
  // Tracks whether the next fetch should bypass the backend's cached score.
  // A ref (not state) so toggling it never triggers an extra render — the
  // refetch is driven explicitly by recalculateQuality().
  const forceRecalculateRef = useRef(false);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['source', sourceId, 'quality'] as const,
    queryFn: async () => {
      const force = forceRecalculateRef.current;
      forceRecalculateRef.current = false;
      try {
        return await qualityApi.scoreSource(sourceId, force);
      } catch {
        // Quality score not available — surface as null rather than an error.
        return null;
      }
    },
    enabled,
  });

  const recalculateQuality = useCallback(async () => {
    forceRecalculateRef.current = true;
    await refetch();
  }, [refetch]);

  return {
    qualityScore: data ?? null,
    qualityLoading: isLoading,
    recalculateQuality,
  };
}
