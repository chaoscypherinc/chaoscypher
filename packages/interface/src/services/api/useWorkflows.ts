// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for workflows.
 *
 * Introduced in PR 2 (TriggersPage migration) with just the list query
 * so trigger rows can resolve workflow names. Extended in PR 4
 * (WorkflowsPage migration) with full CRUD + execute mutations and the
 * steps query.
 *
 * The `is_active` toggle is optimistic: `onMutate` snapshots + flips
 * via `setQueryData`, `onError` rolls back, `onSettled` invalidates.
 * Non-toggle mutations just invalidate the list on success.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  workflowsApi,
  type Workflow,
  type WorkflowUpdate,
  type WorkflowStep,
  type ExecuteWorkflowResponse,
} from './workflows';

const WORKFLOWS_QUERY_KEY = ['workflows'] as const;

function workflowQueryKey(workflowId: string) {
  return ['workflows', workflowId] as const;
}

function workflowStepsQueryKey(workflowId: string) {
  return ['workflows', workflowId, 'steps'] as const;
}

interface UseWorkflowsParams {
  category?: string;
  is_system?: boolean;
}

export function useWorkflows(params?: UseWorkflowsParams) {
  return useQuery<Workflow[]>({
    queryKey: params ? [...WORKFLOWS_QUERY_KEY, params] : WORKFLOWS_QUERY_KEY,
    queryFn: () => workflowsApi.list(params),
  });
}

export function useWorkflow(workflowId: string | null | undefined) {
  return useQuery<Workflow>({
    queryKey: workflowId ? workflowQueryKey(workflowId) : ['workflows', 'none'],
    queryFn: () => workflowsApi.get(workflowId as string),
    enabled: workflowId != null,
  });
}

export function useWorkflowSteps(workflowId: string | null) {
  return useQuery<WorkflowStep[]>({
    queryKey: workflowId ? workflowStepsQueryKey(workflowId) : ['workflows', 'steps', 'none'],
    queryFn: () => workflowsApi.listSteps(workflowId as string),
    enabled: workflowId != null,
  });
}

export function useDeleteWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) => workflowsApi.delete(workflowId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
    },
  });
}

export function useDuplicateWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) => workflowsApi.duplicate(workflowId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
    },
  });
}

export function useExecuteWorkflow() {
  const qc = useQueryClient();
  return useMutation<
    ExecuteWorkflowResponse,
    Error,
    { workflowId: string; inputs: Record<string, unknown> }
  >({
    mutationFn: ({ workflowId, inputs }) => workflowsApi.execute(workflowId, inputs),
    onSuccess: () => {
      // Refresh the list so `last_executed_at` updates.
      void qc.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
    },
  });
}

interface UpdateWorkflowVars {
  id: string;
  patch: WorkflowUpdate;
}

interface UpdateWorkflowContext {
  previous: Workflow[] | undefined;
}

/**
 * Optimistic update mutation. Designed for the `is_active` toggle and
 * other low-risk partial updates: the list flips immediately, rolls
 * back on PATCH failure, and re-syncs from the server on settle.
 */
export function useUpdateWorkflow() {
  const qc = useQueryClient();
  return useMutation<Workflow, Error, UpdateWorkflowVars, UpdateWorkflowContext>({
    mutationFn: ({ id, patch }) => workflowsApi.update(id, patch),
    onMutate: async ({ id, patch }) => {
      await qc.cancelQueries({ queryKey: WORKFLOWS_QUERY_KEY });
      const previous = qc.getQueryData<Workflow[]>(WORKFLOWS_QUERY_KEY);
      qc.setQueryData<Workflow[]>(WORKFLOWS_QUERY_KEY, (old) =>
        old?.map((w) => (w.id === id ? { ...w, ...patch } : w)) ?? old,
      );
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) {
        qc.setQueryData(WORKFLOWS_QUERY_KEY, ctx.previous);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
    },
  });
}
