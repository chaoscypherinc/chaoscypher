// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings-adjacent endpoints that previously lived on `authApi` but aren't
 * actually authentication: TLS toggles and VRAM presets. Both are served
 * under `/api/v1/settings/*` by the backend.
 */

import { apiClient } from './client';

interface TLSStatusResponse {
  enabled: boolean;
}

interface TLSActionResponse {
  status: string;
  mode?: string;
}

export const tlsApi = {
  /** GET /settings/tls/status — current TLS configuration. */
  getStatus: async (): Promise<TLSStatusResponse> => {
    const response = await apiClient.get<TLSStatusResponse>('/settings/tls/status');
    return response.data;
  },

  /** POST /settings/tls/selfsigned — enable self-signed TLS with optional hostname. */
  enableSelfSigned: async (hostname?: string): Promise<TLSActionResponse> => {
    const params = hostname ? { hostname } : {};
    const response = await apiClient.post<TLSActionResponse>(
      '/settings/tls/selfsigned',
      null,
      { params },
    );
    return response.data;
  },

  /** DELETE /settings/tls — disable TLS. */
  disable: async (): Promise<TLSActionResponse> => {
    const response = await apiClient.delete<TLSActionResponse>('/settings/tls');
    return response.data;
  },
};
