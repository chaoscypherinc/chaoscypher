// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import type {
  LexiconAuthStatus,
  LexiconDeviceCodeRequest,
  LexiconDeviceCodeResponse,
  LexiconPollRequest,
  LexiconAuthResponse,
  LexiconSearchParams,
  LexiconSearchResponse,
} from '../../types/lexicon';

// ============================================================================
// Lexicon API Client
// ============================================================================

export const lexiconApi = {
  // ==========================================================================
  // Auth Endpoints
  // ==========================================================================

  /**
   * Get current authentication status
   */
  getAuthStatus: async (): Promise<LexiconAuthStatus> => {
    const response = await apiClient.get('/lexicon/auth/status');
    return response.data;
  },

  /**
   * Initiate device authorization flow
   */
  requestDeviceCode: async (
    request?: LexiconDeviceCodeRequest
  ): Promise<LexiconDeviceCodeResponse> => {
    const response = await apiClient.post('/lexicon/auth/device', request || {});
    return response.data;
  },

  /**
   * Poll for device token (single poll)
   */
  pollDeviceToken: async (
    request: LexiconPollRequest
  ): Promise<LexiconAuthResponse> => {
    const response = await apiClient.post('/lexicon/auth/poll', request);
    return response.data;
  },

  /**
   * Logout and clear credentials
   */
  logout: async (): Promise<LexiconAuthResponse> => {
    const response = await apiClient.post('/lexicon/auth/logout');
    return response.data;
  },

  // ==========================================================================
  // Package Endpoints
  // ==========================================================================

  /**
   * Search packages on the lexicon
   */
  searchPackages: async (
    params: LexiconSearchParams
  ): Promise<LexiconSearchResponse> => {
    const searchParams = new URLSearchParams();
    if (params.query !== undefined) searchParams.append('query', params.query);
    if (params.page) searchParams.append('page', params.page.toString());
    if (params.limit) searchParams.append('limit', params.limit.toString());
    if (params.sort_by) searchParams.append('sort_by', params.sort_by);
    if (params.is_public !== undefined) searchParams.append('is_public', params.is_public.toString());
    if (params.owner_id) searchParams.append('owner_id', params.owner_id);
    if (params.package_type) searchParams.append('package_type', params.package_type);

    const response = await apiClient.get(
      `/lexicon/search?${searchParams.toString()}`
    );
    return response.data;
  },

  /**
   * Download package and import to current database.
   * Returns immediately with a task_id - import runs async in the worker.
   */
  importFromLexicon: async (
    ownerUsername: string,
    repoName: string,
    version: string = 'latest'
  ): Promise<{
    message: string;
    task_id: string;
    status: string;
    owner_username: string;
    repo_name: string;
    version: string;
  }> => {
    const response = await apiClient.post('/lexicon/import', {
      owner_username: ownerUsername,
      repo_name: repoName,
      version,
    });
    return response.data;
  },
};
