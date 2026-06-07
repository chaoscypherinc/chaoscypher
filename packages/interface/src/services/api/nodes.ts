// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { DEFAULT_PUBLIC_SETTINGS } from '../../contexts/publicSettingsContextValue';
import { createCrudApi, fetchAllPages } from '../crudApiFactory';
import type { PaginationMetadata } from '../crudApiFactory';
import { apiClient } from './client';
import type { Node, NodeCreateRequest, CitationListResponse, ConnectionsResponse } from '../../types';

const nodeCrudApi = createCrudApi<Node, NodeCreateRequest, Partial<Node>>('/nodes', apiClient);

interface ListOptions {
  templateId?: string;
  /** Filter by source document IDs */
  sourceIds?: string[];
  /** If true, only loads essential fields (id, label, template_id, position) for better performance */
  minimal?: boolean;
  /** If true, includes edge/citation stats for each node */
  includeStats?: boolean;
}

interface PaginatedNodesResponse {
  data: Node[];
  pagination: PaginationMetadata;
}

export const nodeApi = {
  /**
   * List nodes with options. Returns all nodes by default (fetches all pages).
   */
  list: async (options?: ListOptions | string) => {
    // Support both old (templateId string) and new (options object) signatures
    const opts: ListOptions = typeof options === 'string' ? { templateId: options } : (options || {});
    const filters: Record<string, unknown> = {};
    if (opts.templateId) filters.template_id = opts.templateId;
    if (opts.sourceIds) filters.source_ids = opts.sourceIds;
    if (opts.minimal) filters.minimal = true;
    if (opts.includeStats) filters.include_stats = true;

    return fetchAllPages<Node>((page, size) => {
      return nodeCrudApi.list({ ...filters, page, page_size: size });
    }, 1000);
  },

  /**
   * List nodes with pagination (single page).
   */
  listPaginated: async (
    page: number = 1,
    pageSize: number = DEFAULT_PUBLIC_SETTINGS.pagination_default_page_size,
    options?: ListOptions
  ): Promise<PaginatedNodesResponse> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (options?.templateId) params.template_id = options.templateId;
    if (options?.sourceIds) params.source_ids = options.sourceIds;
    if (options?.minimal) params.minimal = true;
    if (options?.includeStats) params.include_stats = true;

    const response = await apiClient.get<PaginatedNodesResponse>('/nodes', { params });
    return response.data;
  },

  get: nodeCrudApi.get,
  create: nodeCrudApi.create,
  update: nodeCrudApi.update,
  delete: nodeCrudApi.delete,

  getFull: async (id: string) => {
    const response = await apiClient.get(`/nodes/${id}`);
    return response.data;
  },

  getCitations: async (
    id: string,
    page: number = 1,
    pageSize: number = DEFAULT_PUBLIC_SETTINGS.pagination_default_page_size,
  ): Promise<CitationListResponse> => {
    const response = await apiClient.get<CitationListResponse>(`/nodes/${id}/citations`, {
      params: { page, page_size: pageSize }
    });
    return response.data;
  },

  getConnections: async (
    id: string,
    sortBy: string = 'edge_count',
    page: number = 1,
    pageSize: number = DEFAULT_PUBLIC_SETTINGS.pagination_default_page_size,
  ): Promise<ConnectionsResponse> => {
    const response = await apiClient.get<ConnectionsResponse>(`/nodes/${id}/connections`, {
      params: { sort_by: sortBy, page, page_size: pageSize }
    });
    return response.data;
  },
};
