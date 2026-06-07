// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import type {
  Source,
  SourceUpdate,
  PaginatedSourcesResponse,
} from '../../types';
import { sourceProcessingApi } from './sourceProcessing';
import { sourceTagsApi } from './sourceTags';

// Re-export sub-modules for direct imports
export { tagsApi } from './sourceTags';
export type { ExtractionDomain } from './sourceProcessing';

// ========================================
// File type recommendations for content normalization
// ========================================

/**
 * File types where normalization should be DISABLED.
 * Normalization is enabled by default for all other types because TextCleaner
 * (encoding fixes, whitespace normalization, BOM removal) is universally safe.
 */
const NORMALIZATION_DISABLED: Set<string> = new Set([
  // Structured data — exact content preservation required
  'csv', 'json', 'jsonl', 'ttl', 'rdf', 'nt',
  // Code — whitespace is semantically meaningful
  'py', 'js', 'ts', 'jsx', 'tsx',
]);

/**
 * Get recommended normalization setting based on file extension.
 * Returns true (enabled) by default — only disabled for code and structured data.
 */
export const getRecommendedNormalization = (filename: string): boolean => {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return !NORMALIZATION_DISABLED.has(ext);
};

// ========================================
// Source CRUD Operations
// ========================================

const sourcesCrudApi = {
  list: async (params?: {
    page?: number;
    page_size?: number;
    source_type?: string;
    status?: string;  // processing_status filter
    enabled?: string;  // 'enabled' | 'disabled' filter
    search?: string;
    tag_id?: string;
  }): Promise<PaginatedSourcesResponse> => {
    const response = await apiClient.get<PaginatedSourcesResponse>('/sources', { params });
    return response.data;
  },

  get: async (sourceId: string): Promise<Source> => {
    const response = await apiClient.get<Source>(`/sources/${sourceId}`);
    return response.data;
  },

  update: async (sourceId: string, data: SourceUpdate): Promise<Source> => {
    const response = await apiClient.patch<Source>(`/sources/${sourceId}`, data);
    return response.data;
  },

  delete: async (sourceId: string): Promise<void> => {
    await apiClient.delete(`/sources/${sourceId}`);
  },
};

// ========================================
// Composed sourcesApi — preserves the single-object interface
// for all consumers (sourcesApi.list, sourcesApi.upload, etc.)
// ========================================

export const sourcesApi = {
  // CRUD
  ...sourcesCrudApi,
  // Processing (upload, extract, commit, chunks, citations, stats, etc.)
  ...sourceProcessingApi,
  // Tag assignment (source-scoped)
  ...sourceTagsApi,
};
