// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { DEFAULT_PUBLIC_SETTINGS } from '../../contexts/publicSettingsContextValue';
import { apiClient } from './client';
import type { SearchResult } from '../../types';

export interface IndexStatus {
  needs_rebuild: boolean;
  embedding_model: string;
  vector_dimensions: number;
  fulltext: { document_count: number };
  vector: { vector_count: number; dimensions: number };
}

export interface RebuildResult {
  success?: boolean;
  task_id?: string;
  status?: string;
  regenerated?: boolean;
  total_nodes?: number;
  nodes_with_embeddings?: number;
  chunks_indexed?: number;
  message?: string;
}

export const searchApi = {
  keyword: async (
    query: string,
    limit: number = DEFAULT_PUBLIC_SETTINGS.search_default_result_limit,
  ) => {
    const response = await apiClient.get<{ data: SearchResult[] }>('/search', {
      params: { q: query, search_type: 'keyword', limit },
    });
    return response.data.data;
  },
  semantic: async (
    query: string,
    limit: number = DEFAULT_PUBLIC_SETTINGS.search_default_result_limit,
  ) => {
    const response = await apiClient.get<{ data: SearchResult[] }>('/search', {
      params: { q: query, search_type: 'semantic', limit },
    });
    return response.data.data;
  },
  hybrid: async (
    query: string,
    limit: number = DEFAULT_PUBLIC_SETTINGS.search_default_result_limit,
  ) => {
    const response = await apiClient.get<{ data: SearchResult[] }>('/search', {
      params: { q: query, search_type: 'hybrid', limit },
    });
    return response.data.data;
  },
  getIndexStatus: async (): Promise<IndexStatus> => {
    const response = await apiClient.get<IndexStatus>('/search/indexes/status');
    return response.data;
  },
  rebuildIndexes: async (): Promise<RebuildResult> => {
    const response = await apiClient.post<RebuildResult>('/search/indexes');
    return response.data;
  },
};
