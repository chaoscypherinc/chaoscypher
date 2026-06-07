// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for dashboard-scoped reads.
 *
 * `/counts` and `/quality/summary` are one-shot reads — refreshed at the
 * normal `cache_default_stale_time_ms` cadence, not polled. `/llm/stats`
 * is polled at the activity-log cadence so total-cost-USD updates while
 * jobs run. Polling here is opt-in via `refetchInterval` because the
 * three values surface in different parts of the dashboard with
 * different freshness expectations.
 */

import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';
import { qualityApi } from './quality';
import type { components } from '../../types/generated/api';

const COUNTS_QUERY_KEY = ['dashboard', 'counts'] as const;
const QUALITY_SUMMARY_QUERY_KEY = ['quality', 'summary'] as const;
const LLM_STATS_QUERY_KEY = ['llm', 'stats'] as const;

type CountsResponse = components['schemas']['CountsResponse'];
type QualitySummary = components['schemas']['QualitySummaryResponse'];

export function useCounts() {
  return useQuery({
    queryKey: COUNTS_QUERY_KEY,
    queryFn: async (): Promise<CountsResponse> => {
      const response = await apiClient.get<CountsResponse>('/counts');
      return response.data;
    },
  });
}

export function useQualitySummary() {
  return useQuery({
    queryKey: QUALITY_SUMMARY_QUERY_KEY,
    queryFn: (): Promise<QualitySummary> => qualityApi.getSummary(),
  });
}

interface LLMStatsPayload {
  data: { total_cost_usd?: number };
}

export function useLLMStats(opts: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: LLM_STATS_QUERY_KEY,
    queryFn: async (): Promise<LLMStatsPayload> => {
      const response = await apiClient.get<LLMStatsPayload>('/llm/stats');
      return response.data;
    },
    refetchInterval: opts.refetchInterval ?? false,
  });
}
