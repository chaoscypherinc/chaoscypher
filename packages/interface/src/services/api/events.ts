// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Events API client for system event log.
 */

import { apiClient } from './client';

export interface SystemEvent {
  id: number;
  timestamp: string;
  type: string; // "pause" | "resume" | "health_change" | "task_failed" | "recovery"
  action: string;
  source: string | null;
  reason: string | null;
  details: string | null; // JSON string
  database_name: string | null;
}

export const eventsApi = {
  listEvents: async (params?: { type?: string; limit?: number }): Promise<SystemEvent[]> => {
    const { type, ...rest } = params ?? {};
    const query = { ...rest, ...(type ? { event_type: type } : {}) };
    const response = await apiClient.get<SystemEvent[]>('/system/processing/events', { params: query });
    return response.data;
  },

  clearEvents: async (): Promise<{ deleted: number }> => {
    const response = await apiClient.delete<{ deleted: number }>('/system/processing/events');
    return response.data;
  },
};
