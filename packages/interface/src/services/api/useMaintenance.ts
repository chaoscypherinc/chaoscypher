// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the maintenance / database-upgrade flow.
 *
 * Introduced with the MaintenancePage migration off raw fetch+useState.
 * `usePendingUpgrades` polls the pending-migration state (the page bounces
 * back to the app once it goes ready, and polls while migrations are
 * outstanding); `useApplyUpgrades` / `useRollbackUpgrade` are the
 * apply/roll-back mutations and invalidate the pending query on success.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  applyUpgrades,
  fetchPendingUpgrades,
  rollbackUpgrade,
  type PendingUpgradesResponse,
} from './upgrade';

const PENDING_UPGRADES_QUERY_KEY = ['upgrade', 'pending'] as const;

interface UsePendingUpgradesOptions {
  /**
   * Poll interval in ms while migrations are still pending. The page also
   * stops the page-level polling once `ready` flips true (it navigates away),
   * so `enabled` here is the gate the caller controls.
   */
  refetchInterval?: number | false;
  enabled?: boolean;
}

export function usePendingUpgrades(options: UsePendingUpgradesOptions = {}) {
  const { refetchInterval = false, enabled = true } = options;
  return useQuery<PendingUpgradesResponse>({
    queryKey: PENDING_UPGRADES_QUERY_KEY,
    queryFn: () => fetchPendingUpgrades(),
    refetchInterval,
    enabled,
  });
}

export function useApplyUpgrades() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => applyUpgrades(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PENDING_UPGRADES_QUERY_KEY });
    },
  });
}

export function useRollbackUpgrade() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => rollbackUpgrade(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: PENDING_UPGRADES_QUERY_KEY });
    },
  });
}
