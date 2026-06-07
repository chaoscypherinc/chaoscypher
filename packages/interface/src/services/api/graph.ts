// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import type { CanvasDataResponse, SourceGroupListResponse } from '../../types/graph';

/** Graph visualization API. */
export const graphApi = {
  /** Bulk fetch all graph data for canvas rendering in a single request. */
  async fetchCanvasData(sourceIds?: string[]): Promise<CanvasDataResponse> {
    const params: Record<string, unknown> = {};
    if (sourceIds && sourceIds.length > 0) {
      params.source_ids = sourceIds;
    }
    const response = await apiClient.get<CanvasDataResponse>(
      '/graph/canvas',
      { params },
    );
    return response.data;
  },

  /** Fetch source groups for graph canvas visualization. */
  async fetchSourceGroups(): Promise<SourceGroupListResponse> {
    const response = await apiClient.get<SourceGroupListResponse>(
      '/graph/source_groups',
    );
    return response.data;
  },
};
