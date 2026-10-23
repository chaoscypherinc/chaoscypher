// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LLM response and embedding caches (`/api/v1/cache`): their stats, their
 * settings, and clearing one of them.
 */

import { apiClient } from './client';
import type { components } from '../../types/generated/api';

export type CacheOverview = components['schemas']['CacheOverviewResponse'];
export type CacheStats = components['schemas']['CacheStatsResponse'];
export type CacheUpdate = components['schemas']['CacheUpdateRequest'];
export type CacheClearResult = components['schemas']['CacheClearResponse'];

export const cacheApi = {
  /** GET /cache — both caches: settings, size, history by call site. */
  getOverview: async (): Promise<CacheOverview> => {
    const response = await apiClient.get<CacheOverview>('/cache');
    return response.data;
  },

  /** PATCH /cache — change enabled flags / caps; returns the overview after the change. */
  update: async (patch: CacheUpdate): Promise<CacheOverview> => {
    const response = await apiClient.patch<CacheOverview>('/cache', patch);
    return response.data;
  },

  /** POST /cache/{name}/clear — delete one cache's entries (its history is kept). */
  clear: async (name: string): Promise<CacheClearResult> => {
    const response = await apiClient.post<CacheClearResult>(
      `/cache/${encodeURIComponent(name)}/clear`,
    );
    return response.data;
  },
};
