// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import type { SourceQualityScore, QualityAnalysisResponse } from '../../types';
import type { components } from '../../types/generated/api';

type QualitySummaryResponse = components['schemas']['QualitySummaryResponse'];

// ============================================================================
// Quality API Client
// ============================================================================

interface RecalculateResponse {
  recalculated_count: number;
  errors: Array<{ source_id: string; error: string }>;
}

interface OutdatedSourcesResponse {
  outdated_count: number;
  sources: Array<{
    id: string;
    title: string | null;
    cached_scores_version: number | null;
    current_version: number;
  }>;
}

export const qualityApi = {
  /**
   * Score a single source's extraction quality
   * @param sourceId - The source ID to score
   * @param forceRecalculate - If true, bypass cache and recalculate fresh scores
   */
  scoreSource: async (
    sourceId: string,
    forceRecalculate: boolean = false
  ): Promise<SourceQualityScore> => {
    const params = forceRecalculate ? { force_recalculate: true } : {};
    const response = await apiClient.get(`/quality/sources/${sourceId}`, { params });
    return response.data;
  },

  /**
   * Analyze quality across multiple sources
   */
  analyze: async (options?: {
    source_ids?: string[];
    domain?: string;
    min_entities?: number;
  }): Promise<QualityAnalysisResponse> => {
    const response = await apiClient.post('/quality/analyze', options || {});
    return response.data;
  },

  /**
   * Recalculate and cache quality scores for all sources
   * @param domain - Optional domain filter
   */
  recalculateAll: async (domain?: string): Promise<RecalculateResponse> => {
    const response = await apiClient.post('/quality/recalculate', { domain });
    return response.data;
  },

  /**
   * Get sources with outdated or missing cached quality scores
   */
  getOutdatedSources: async (): Promise<OutdatedSourcesResponse> => {
    const response = await apiClient.get('/quality/outdated');
    return response.data;
  },

  /**
   * Overall quality summary (avg grade, top/bottom sources, totals).
   */
  getSummary: async (): Promise<QualitySummaryResponse> => {
    const response = await apiClient.get<QualitySummaryResponse>('/quality/summary');
    return response.data;
  },
};
