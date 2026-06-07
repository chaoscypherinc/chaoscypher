// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Workflows API Service
 *
 * API client for workflow CRUD, steps, and execution management.
 */

import { apiClient } from './client';
import type { PaginationMetadata } from '../crudApiFactory';
import type { components } from '../../types/generated/api';

export type WorkflowExecutionStatus = components['schemas']['WorkflowExecutionStatus'];

// ============================================================================
// Types
// ============================================================================

export interface Workflow {
  id: string;
  database_name: string;
  name: string;
  description?: string;
  category?: string;
  is_system: boolean;
  is_active: boolean;
  expose_as_ai_tool: boolean;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  tags?: string[];
  icon?: string;
  timeout_seconds?: number;
  max_retries?: number;
  created_at: string;
  updated_at: string;
  last_executed_at?: string;
}

interface WorkflowCreate {
  name: string;
  description?: string;
  category?: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  expose_as_ai_tool?: boolean;
  timeout_seconds?: number;
  max_retries?: number;
  tags?: string[];
  icon?: string;
}

interface WorkflowUpdate {
  name?: string;
  description?: string;
  category?: string;
  input_schema?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  expose_as_ai_tool?: boolean;
  is_active?: boolean;
  timeout_seconds?: number;
  max_retries?: number;
  tags?: string[];
  icon?: string;
}

export interface WorkflowStep {
  id: string;
  workflow_id: string;
  step_number: number;
  name: string;
  description?: string;
  tool_type: 'system_tool' | 'user_tool' | 'workflow';
  tool_id: string;
  configuration: Record<string, unknown>;
  condition?: Record<string, unknown>;
  depends_on: string[];
  retry_on_failure: boolean;
  timeout_seconds?: number;
  continue_on_error: boolean;
  thinking_mode?: string;
}

export interface WorkflowStepCreate {
  step_number: number;
  name: string;
  description?: string;
  tool_type: 'system_tool' | 'user_tool' | 'workflow';
  tool_id: string;
  configuration?: Record<string, unknown>;
  condition?: Record<string, unknown>;
  depends_on?: string[];
  retry_on_failure?: boolean;
  timeout_seconds?: number;
  continue_on_error?: boolean;
  thinking_mode?: string;
}

interface WorkflowStepUpdate {
  step_number?: number;
  name?: string;
  description?: string;
  tool_type?: 'system_tool' | 'user_tool' | 'workflow';
  tool_id?: string;
  configuration?: Record<string, unknown>;
  condition?: Record<string, unknown>;
  depends_on?: string[];
  retry_on_failure?: boolean;
  timeout_seconds?: number;
  continue_on_error?: boolean;
  thinking_mode?: string;
}

export interface WorkflowExecution {
  id: string;
  workflow_id: string;
  triggered_by: string;
  trigger_id?: string;
  parent_execution_id?: string;
  inputs: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  status: WorkflowExecutionStatus;
  current_step_id?: string;
  failed_step_id?: string;
  error_message?: string;
  duration_ms?: number;
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

export interface WorkflowStepExecution {
  id: string;
  execution_id: string;
  step_id: string;
  inputs: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  error_message?: string;
  retry_count: number;
  duration_ms?: number;
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

export interface WorkflowExecutionDetail extends WorkflowExecution {
  step_executions: WorkflowStepExecution[];
}

export interface WorkflowStats {
  workflow_id: string;
  total_executions: number;
  successful_executions: number;
  failed_executions: number;
  cancelled_executions: number;
  avg_duration_ms?: number;
  min_duration_ms?: number;
  max_duration_ms?: number;
  last_execution_at?: string;
  last_success_at?: string;
  last_failure_at?: string;
}

interface WorkflowListParams {
  category?: string;
  is_system?: boolean;
  is_active?: boolean;
  expose_as_ai_tool?: boolean;
}

interface ExecutionListParams {
  page?: number;
  page_size?: number;
  status?: string;
}

interface PaginatedWorkflowExecutionsResponse {
  data: WorkflowExecution[];
  pagination: {
    page: number;
    page_size: number;
    total_items: number;
    total_pages: number;
  };
}

export interface ExecuteWorkflowResponse {
  execution_id: string;
  status: string;
  message: string;
}

export type { WorkflowUpdate, WorkflowCreate };

export interface WorkflowTrigger {
  id: string;
  name: string;
  event_source: string;
  workflow_id: string;
  filters: Record<string, unknown>;
  workflow_inputs: Record<string, unknown> | null;
  enabled: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
}

// ============================================================================
// API Client
// ============================================================================

export const workflowsApi = {
  // Workflow CRUD
  list: (params?: WorkflowListParams & { page?: number; page_size?: number }) =>
    apiClient
      .get<{ data: Workflow[]; pagination: PaginationMetadata }>(
        '/workflows',
        { params }
      )
      .then((r) => r.data.data),

  get: (workflowId: string) =>
    apiClient.get<Workflow>(`/workflows/${workflowId}`).then((r) => r.data),

  create: (data: WorkflowCreate) =>
    apiClient.post<Workflow>('/workflows', data).then((r) => r.data),

  update: (workflowId: string, data: WorkflowUpdate) =>
    apiClient.patch<Workflow>(`/workflows/${workflowId}`, data).then((r) => r.data),

  delete: (workflowId: string) =>
    apiClient.delete(`/workflows/${workflowId}`),

  duplicate: (workflowId: string) =>
    apiClient.post<Workflow>(`/workflows/${workflowId}/duplicate`).then((r) => r.data),

  // Steps CRUD
  listSteps: (workflowId: string) =>
    apiClient.get<WorkflowStep[]>(`/workflows/${workflowId}/steps`).then((r) => r.data),

  getStep: (workflowId: string, stepId: string) =>
    apiClient.get<WorkflowStep>(`/workflows/${workflowId}/steps/${stepId}`).then((r) => r.data),

  createStep: (workflowId: string, data: WorkflowStepCreate) =>
    apiClient.post<WorkflowStep>(`/workflows/${workflowId}/steps`, data).then((r) => r.data),

  updateStep: (workflowId: string, stepId: string, data: WorkflowStepUpdate) =>
    apiClient.patch<WorkflowStep>(`/workflows/${workflowId}/steps/${stepId}`, data).then((r) => r.data),

  deleteStep: (workflowId: string, stepId: string) =>
    apiClient.delete(`/workflows/${workflowId}/steps/${stepId}`),

  reorderSteps: (workflowId: string, stepOrder: string[]) =>
    apiClient.put(`/workflows/${workflowId}/steps/reorder`, { step_order: stepOrder }),

  // Triggers
  listTriggers: (workflowId: string) =>
    apiClient.get<WorkflowTrigger[]>(`/workflows/${workflowId}/triggers`).then((r) => r.data),

  // Execution
  execute: (workflowId: string, inputs: Record<string, unknown>) =>
    apiClient.post<ExecuteWorkflowResponse>(`/workflows/${workflowId}/executions`, { inputs }).then((r) => r.data),

  getExecution: (workflowId: string, executionId: string) =>
    apiClient.get<WorkflowExecutionDetail>(`/workflows/${workflowId}/executions/${executionId}`).then((r) => r.data),

  listExecutions: (workflowId: string, params?: ExecutionListParams) =>
    apiClient
      .get<PaginatedWorkflowExecutionsResponse>(`/workflows/${workflowId}/executions`, { params })
      .then((r) => r.data.data),

  cancelExecution: (workflowId: string, executionId: string) =>
    apiClient.post(`/workflows/${workflowId}/executions/${executionId}/cancel`),

  // Statistics
  getStats: (workflowId: string) =>
    apiClient.get<WorkflowStats>(`/workflows/${workflowId}/stats`).then((r) => r.data),
};
