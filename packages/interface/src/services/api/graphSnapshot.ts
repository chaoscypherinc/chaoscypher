// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import type { GraphBreakdown } from '../../types/graphSnapshot';

/**
 * GET /api/v1/graph/snapshot
 *
 * Returns `null` when the server responds 204 (no snapshot has been built yet).
 */
export async function getGraphSnapshot(): Promise<GraphBreakdown | null> {
  const res = await apiClient.get<GraphBreakdown | null>('/graph/snapshot');
  if (res.status === 204) {
    return null;
  }
  return res.data;
}

/**
 * POST /api/v1/graph/snapshot/refresh → 202 Accepted
 *
 * Enqueues a background rebuild of the graph snapshot.
 * Returns `{ task_id }` from the 202 BulkResponse envelope.
 */
export async function refreshGraphSnapshot(): Promise<{ task_id: string }> {
  const res = await apiClient.post<{ task_id: string }>('/graph/snapshot/refresh');
  return res.data;
}
