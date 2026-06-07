// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import type {
  SourceTag,
  SourceTagCreate,
  SourceTagUpdate,
} from '../../types';

// ========================================
// Source-scoped Tag Assignment
// ========================================

export const sourceTagsApi = {
  getTags: async (sourceId: string): Promise<SourceTag[]> => {
    const response = await apiClient.get<SourceTag[]>(`/sources/${sourceId}/tags`);
    return response.data;
  },

  assignTag: async (sourceId: string, tagId: string): Promise<void> => {
    await apiClient.post(`/sources/${sourceId}/tags/${tagId}`);
  },

  unassignTag: async (sourceId: string, tagId: string): Promise<void> => {
    await apiClient.delete(`/sources/${sourceId}/tags/${tagId}`);
  },
};

// ========================================
// Tag CRUD (standalone)
// ========================================

export const tagsApi = {
  list: async (): Promise<SourceTag[]> => {
    const response = await apiClient.get<SourceTag[]>('/sources/tags');
    return response.data;
  },

  get: async (tagId: string): Promise<SourceTag> => {
    const response = await apiClient.get<SourceTag>(`/sources/tags/${tagId}`);
    return response.data;
  },

  create: async (data: SourceTagCreate): Promise<SourceTag> => {
    const response = await apiClient.post<SourceTag>('/sources/tags', data);
    return response.data;
  },

  update: async (tagId: string, data: SourceTagUpdate): Promise<SourceTag> => {
    const response = await apiClient.patch<SourceTag>(`/sources/tags/${tagId}`, data);
    return response.data;
  },

  delete: async (tagId: string): Promise<void> => {
    await apiClient.delete(`/sources/tags/${tagId}`);
  },
};
