// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback } from 'react';
import { apiClient } from '../services/api/client';

/**
 * Per-source and bulk pause/resume actions.
 *
 * @param onChange Optional callback fired after each action so the
 *   caller can refetch the source list.
 */
export function useSourcePause(onChange?: () => void) {
  const pauseSource = useCallback(async (sourceId: string, reason?: string) => {
    await apiClient.post(`/sources/${sourceId}/pause`, { reason: reason || null });
    onChange?.();
  }, [onChange]);

  const resumeSource = useCallback(async (sourceId: string) => {
    await apiClient.post(`/sources/${sourceId}/resume`);
    onChange?.();
  }, [onChange]);

  const pauseSources = useCallback(async (sourceIds: string[], reason?: string) => {
    await apiClient.post('/sources/pause', { source_ids: sourceIds, reason: reason || null });
    onChange?.();
  }, [onChange]);

  const resumeSources = useCallback(async (sourceIds: string[]) => {
    await apiClient.post('/sources/resume', { source_ids: sourceIds });
    onChange?.();
  }, [onChange]);

  return { pauseSource, resumeSource, pauseSources, resumeSources };
}
