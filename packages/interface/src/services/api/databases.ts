// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { apiClient } from './client';
import type { DatabaseCreate, DatabaseInfo } from '../../types';

export const databaseApi = {
  list: async () => {
    const response = await apiClient.get<{ databases: DatabaseInfo[] }>('/databases');
    return response.data.databases;
  },
  getCurrent: async () => {
    const response = await apiClient.get<{ current: string; info: DatabaseInfo }>('/databases/current');
    return response.data;
  },
  get: async (name: string) => {
    const response = await apiClient.get<DatabaseInfo>(`/databases/${name}`);
    return response.data;
  },
  create: async (database: DatabaseCreate) => {
    const response = await apiClient.post<DatabaseInfo>('/databases', database);
    return response.data;
  },
  switch: async (name: string) => {
    const response = await apiClient.patch<{ success: boolean; message: string; database: string }>(
      '/databases/current',
      { name }
    );
    return response.data;
  },
  delete: async (name: string) => {
    await apiClient.delete(`/databases/${name}`);
    return { success: true };
  },
};
