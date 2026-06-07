// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';

export interface LogResponse {
  service: string | null;
  lines: string[];
  total_lines: number;
}

export interface ServiceStatus {
  name: string;
  state: string;
  pid: number | null;
  uptime_seconds: number | null;
  start_time: string | null;
  description: string;
}

export interface ServiceStatusResponse {
  available: boolean;
  services: ServiceStatus[];
}

export const logsApi = {
  getAll: async (lines: number = 2000): Promise<LogResponse> => {
    const response = await apiClient.get<LogResponse>('/logs', {
      params: { lines },
    });
    return response.data;
  },

  getService: async (service: string, lines: number = 2000): Promise<LogResponse> => {
    const response = await apiClient.get<LogResponse>(`/logs/${service}`, {
      params: { lines },
    });
    return response.data;
  },

  getStatus: async (): Promise<ServiceStatusResponse> => {
    const response = await apiClient.get<ServiceStatusResponse>('/logs/status');
    return response.data;
  },
};

interface LoggingLevelResponse {
  level: string;
  numeric_level: number;
  available_levels: string[];
}

interface SetLoggingLevelResponse {
  success: boolean;
  old_level: string;
  new_level: string;
  message: string;
}

export const loggingApi = {
  getLevel: async (): Promise<LoggingLevelResponse> => {
    const response = await apiClient.get<LoggingLevelResponse>('/settings/logging/level');
    return response.data;
  },

  setLevel: async (level: string): Promise<SetLoggingLevelResponse> => {
    const response = await apiClient.post<SetLoggingLevelResponse>('/settings/logging/level', {
      level,
    });
    return response.data;
  },
};

export const diagnosticsApi = {
  exportBundle: async (): Promise<void> => {
    const response = await apiClient.get('/diagnostics/export', {
      responseType: 'blob',
    });
    const url = window.URL.createObjectURL(response.data as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'chaoscypher-diagnostics.zip';
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  },
};
