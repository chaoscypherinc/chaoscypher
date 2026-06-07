// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { DEFAULT_PUBLIC_SETTINGS } from '../../contexts/publicSettingsContextValue';
import { createCrudApi, fetchAllPages } from '../crudApiFactory';
import type { PaginationMetadata } from '../crudApiFactory';
import { apiClient } from './client';
import type { Edge, EdgeCreateRequest } from '../../types';

const edgeCrudApi = createCrudApi<Edge, EdgeCreateRequest, Partial<Edge>>('/edges', apiClient);

interface ListOptions {
  nodeId?: string;
  /** Filter by source document IDs */
  sourceIds?: string[];
  /** If true, only loads essential fields (id, source_node_id, target_node_id, label, template_id) for better performance */
  minimal?: boolean;
}

interface PaginatedEdgesResponse {
  data: Edge[];
  pagination: PaginationMetadata;
}

export const edgeApi = {
  /**
   * List edges with options. Returns all edges by default (fetches all pages).
   * WARNING: Can be slow with large datasets - prefer listPaginated for UI.
   */
  list: async (options?: ListOptions | string) => {
    // Support both old (nodeId string) and new (options object) signatures
    const opts: ListOptions = typeof options === 'string' ? { nodeId: options } : (options || {});
    const filters: Record<string, unknown> = {};
    if (opts.nodeId) filters.node_id = opts.nodeId;
    if (opts.sourceIds) filters.source_ids = opts.sourceIds;
    if (opts.minimal) filters.minimal = true;

    return fetchAllPages<Edge>((page, size) => {
      return edgeCrudApi.list({ ...filters, page, page_size: size });
    }, 1000);
  },

  /**
   * List edges with pagination (single page).
   */
  listPaginated: async (
    page: number = 1,
    pageSize: number = DEFAULT_PUBLIC_SETTINGS.pagination_default_page_size,
    options?: ListOptions
  ): Promise<PaginatedEdgesResponse> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (options?.nodeId) params.node_id = options.nodeId;
    if (options?.sourceIds) params.source_ids = options.sourceIds;
    if (options?.minimal) params.minimal = true;

    const response = await apiClient.get<PaginatedEdgesResponse>('/edges', { params });
    return response.data;
  },

  get: edgeCrudApi.get,
  create: edgeCrudApi.create,
  update: edgeCrudApi.update,
  delete: edgeCrudApi.delete,
};
