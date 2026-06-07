// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';

export interface BackupInfo {
  database: string;
  filename: string;
  size: number;
  created_at: string;
}

interface RestoreResult {
  database: string;
  restored_from: string;
}

export const backupApi = {
  create: async (): Promise<BackupInfo> => {
    const response = await apiClient.post<BackupInfo>('/backup');
    return response.data;
  },

  list: async (): Promise<BackupInfo[]> => {
    const response = await apiClient.get<{ backups: BackupInfo[] }>('/backup');
    return response.data.backups;
  },

  restore: async (filename: string): Promise<RestoreResult> => {
    const response = await apiClient.post<RestoreResult>(`/backup/${filename}/restore`);
    return response.data;
  },

  download: async (filename: string): Promise<void> => {
    const response = await apiClient.get(`/backup/${filename}/download`, {
      responseType: 'blob',
    });
    const url = window.URL.createObjectURL(response.data as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  },

  delete: async (filename: string): Promise<void> => {
    await apiClient.delete(`/backup/${filename}`);
  },
};
