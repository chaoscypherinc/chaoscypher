// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { type ReactNode } from 'react';
import { useDashboardData } from '../hooks/useDashboardData';
import { DashboardContext } from './dashboardContextValue';

/**
 * Wraps children with a single shared dashboard polling loop.
 *
 * Mount this once near the top of the app (above MiniSystemStatus,
 * Sources page, LLMQueueMonitor, etc.) so every consumer reads from
 * the same in-memory snapshot. The underlying hook polls
 * /api/v1/system/dashboard once per cycle.
 */
export function DashboardProvider({ children }: { children: ReactNode }) {
  const value = useDashboardData();
  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}
