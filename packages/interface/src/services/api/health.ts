// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Health API client for system status monitoring.
 */

import { apiClient } from './client';
import type { HealthCheckResponse } from '../../types/health';

export const healthApi = {
  getHealth: async (): Promise<HealthCheckResponse> => {
    const response = await apiClient.get<HealthCheckResponse>('/health');
    return response.data;
  },
};
