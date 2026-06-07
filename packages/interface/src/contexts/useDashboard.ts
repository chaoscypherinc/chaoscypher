// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useContext } from 'react';
import { DashboardContext } from './dashboardContextValue';
import type { UseDashboardDataResult } from '../hooks/useDashboardData';

/**
 * Read the live dashboard snapshot provided by DashboardProvider.
 *
 * Throws if used outside the provider — that's intentional: a
 * component reading dashboard data without the provider would either
 * (a) silently get stale defaults forever, or (b) start its own
 * polling loop and defeat the consolidation. Both are bugs we want
 * to surface immediately.
 */
export function useDashboard(): UseDashboardDataResult {
  const ctx = useContext(DashboardContext);
  if (ctx === null) {
    throw new Error(
      'useDashboard must be used within a <DashboardProvider>. ' +
        'Wrap the app (or the relevant subtree) in <DashboardProvider> in main.tsx.',
    );
  }
  return ctx;
}
