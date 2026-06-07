// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback } from 'react';
import { apiClient } from '../services/api/client';
import { useDashboard } from '../contexts/useDashboard';

export interface SystemPauseStatus {
  paused: boolean;
  paused_at: string | null;
  reason: string | null;
}

/**
 * Read the system-wide pause status (from the shared dashboard poll)
 * and expose pause/resume action wrappers.
 *
 * Previously polled /system/processing/status every 2s on its own
 * timer; now reads the same data from the consolidated
 * /system/dashboard poll via DashboardContext. The standalone
 * /system/processing/status GET endpoint is preserved for the
 * useDashboardData fetch path and any non-polling consumer that
 * needs an ad-hoc snapshot.
 */
export function useSystemPauseStatus() {
  const { data, refresh } = useDashboard();
  const status: SystemPauseStatus = data.processing;

  const pauseSystem = useCallback(
    async (reason?: string) => {
      await apiClient.post('/system/processing/pause', { reason: reason || null });
      await refresh();
    },
    [refresh],
  );

  const resumeSystem = useCallback(async () => {
    await apiClient.post('/system/processing/resume');
    await refresh();
  }, [refresh]);

  return { status, pauseSystem, resumeSystem, refresh };
}
