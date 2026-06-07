// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Triggers API Service
 *
 * API client for trigger CRUD operations.
 */

import { apiClient } from './client';
import type { PaginationMetadata } from '../crudApiFactory';

// ============================================================================
// Types
// ============================================================================

interface Trigger {
  id: string;
  name: string;
  event_source: string;
  workflow_id: string;
  filters: Record<string, unknown>;
  workflow_inputs: Record<string, unknown> | null;
  enabled: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
}

interface TriggerCreate {
  name: string;
  event_source: string;
  workflow_id: string;
  filters?: Record<string, unknown>;
  workflow_inputs?: Record<string, unknown>;
  enabled?: boolean;
  priority?: number;
}

interface TriggerUpdate {
  name?: string;
  event_source?: string;
  workflow_id?: string;
  filters?: Record<string, unknown>;
  workflow_inputs?: Record<string, unknown>;
  enabled?: boolean;
  priority?: number;
}

export interface TriggerStats {
  total_executions: number;
  successful_executions: number;
  failed_executions: number;
  success_rate: number;
  average_duration_ms: number;
}

export type { Trigger, TriggerCreate, TriggerUpdate };

// ============================================================================
// API Client
// ============================================================================

export const triggersApi = {
  // Trigger CRUD
  list: (params?: { event_source?: string; enabled?: boolean; page?: number; page_size?: number }) =>
    apiClient
      .get<{ data: Trigger[]; pagination: PaginationMetadata }>(
        '/triggers',
        { params }
      )
      .then((r) => r.data.data),

  get: (triggerId: string) =>
    apiClient.get<Trigger>(`/triggers/${triggerId}`).then((r) => r.data),

  create: (data: TriggerCreate) =>
    apiClient.post<Trigger>('/triggers', data).then((r) => r.data),

  update: (triggerId: string, data: TriggerUpdate) =>
    apiClient.patch<Trigger>(`/triggers/${triggerId}`, data).then((r) => r.data),

  delete: (triggerId: string) =>
    apiClient.delete(`/triggers/${triggerId}`),

  getStats: (triggerId: string) =>
    apiClient.get<TriggerStats>(`/triggers/${triggerId}/stats`).then((r) => r.data),
};
