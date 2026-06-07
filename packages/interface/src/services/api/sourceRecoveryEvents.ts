// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useQuery } from '@tanstack/react-query';
import { DEFAULT_PUBLIC_SETTINGS } from '../../contexts/publicSettingsContextValue';
import { useAppConfig } from '../../contexts/useAppConfig';
import { apiClient } from './client';

interface RecoveryEvent {
  id: string;
  source_id: string;
  database_name: string;
  attempt_at: string; // ISO-8601
  from_status: string;
  action_taken: string;
  reason: string;
  enqueued_count: number;
}

interface RecoveryEventListResponse {
  events: RecoveryEvent[];
}

const SOURCE_RECOVERY_EVENTS_QUERY_KEY = (sourceId: string) =>
  ['sources', sourceId, 'recovery_events'] as const;

/**
 * Fetch the recovery audit trail for a source.
 *
 * Backs the source detail page's recovery panel so operators can see
 * exactly which recoveries fired and why — instead of grepping logs.
 */
async function getSourceRecoveryEvents(
  sourceId: string,
  limit = DEFAULT_PUBLIC_SETTINGS.pagination_default_page_size,
): Promise<RecoveryEventListResponse> {
  const response = await apiClient.get<RecoveryEventListResponse>(
    `/sources/${sourceId}/recovery_events`,
    { params: { limit } },
  );
  return response.data;
}

/**
 * TanStack Query hook for the recovery events list.
 *
 * Cached for 30 seconds; refetched on window focus so the panel stays
 * fresh while operators investigate. `enabled` lets the caller defer
 * the fetch until the panel is expanded (avoids one network round-trip
 * on every source-page mount).
 */
export function useSourceRecoveryEvents(sourceId: string, enabled = true) {
  const config = useAppConfig();
  return useQuery({
    queryKey: SOURCE_RECOVERY_EVENTS_QUERY_KEY(sourceId),
    queryFn: () => getSourceRecoveryEvents(sourceId),
    enabled,
    staleTime: config.cache_default_stale_time_ms,
    refetchOnWindowFocus: true,
  });
}
