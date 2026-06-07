// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { createContext } from 'react';
import type { UseDashboardDataResult } from '../hooks/useDashboardData';

/**
 * Shared dashboard data — the result of useDashboardData() hoisted to
 * the top of the component tree. Components consume via useDashboard()
 * instead of starting their own polling loops, so the live status
 * data is fetched once per cycle (not 3-7 times in parallel).
 */
export const DashboardContext = createContext<UseDashboardDataResult | null>(null);
