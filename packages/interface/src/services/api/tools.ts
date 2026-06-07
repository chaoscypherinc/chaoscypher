// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tools API Service
 *
 * API client for system tools and user tools management.
 */

import { apiClient } from './client';
import type { PaginationMetadata } from '../crudApiFactory';

interface SystemTool {
  id: string;
  category: string;
  icon: string | null;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  version: string;
  is_active: boolean;
}

// The list endpoint returns SystemToolSummary (no schemas) but legacy
// callers (WorkflowBuilderPage's tool palette) read input_schema /
// output_schema off list-endpoint results. Keep the listSystem type
// as `SystemTool[]` until that pre-existing mismatch is cleaned up.
type SystemToolSummary = SystemTool;

interface UserTool {
  id: string;
  database_name: string;
  system_tool_id: string;
  name: string;
  description?: string;
  configuration: Record<string, unknown>;
  tags?: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface UserToolCreate {
  system_tool_id: string;
  name: string;
  description?: string;
  configuration: Record<string, unknown>;
  tags?: string[];
}

interface UserToolUpdate {
  name?: string;
  description?: string;
  configuration?: Record<string, unknown>;
  tags?: string[];
  is_active?: boolean;
}

export const toolsApi = {
  // System tools
  listSystem: (params?: { category?: string; is_active?: boolean }) =>
    apiClient.get<SystemTool[]>('/tools/system', { params }).then((r) => r.data),

  getSystem: (toolId: string) =>
    apiClient.get<SystemTool>(`/tools/system/${toolId}`).then((r) => r.data),

  // User tools
  list: (params?: { system_tool_id?: string; is_active?: boolean; page?: number; page_size?: number }) =>
    apiClient
      .get<{ data: UserTool[]; pagination: PaginationMetadata }>(
        '/tools',
        { params }
      )
      .then((r) => r.data.data),

  get: (toolId: string) =>
    apiClient.get<UserTool>(`/tools/${toolId}`).then((r) => r.data),

  create: (data: UserToolCreate) =>
    apiClient.post<UserTool>('/tools', data).then((r) => r.data),

  update: (toolId: string, data: UserToolUpdate) =>
    apiClient.patch<UserTool>(`/tools/${toolId}`, data).then((r) => r.data),

  delete: (toolId: string) =>
    apiClient.delete(`/tools/${toolId}`),

  /**
   * Duplicate a user tool. Note: as of 2026-05-17 the backend route
   * does not exist — this call returns 404 in production. Preserved
   * here as-is until the backend gains the endpoint.
   */
  duplicate: (toolId: string) =>
    apiClient.post<UserTool>(`/tools/${toolId}/duplicate`).then((r) => r.data),
};

export type { SystemTool, SystemToolSummary, UserTool, UserToolCreate, UserToolUpdate };
