// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for tools (system + user).
 *
 * All non-toggle mutations invalidate USER_TOOLS_QUERY_KEY on success so
 * the list re-syncs from the server.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  toolsApi,
  type SystemTool,
  type SystemToolSummary,
  type UserTool,
  type UserToolCreate,
  type UserToolUpdate,
} from './tools';

const SYSTEM_TOOLS_QUERY_KEY = ['tools', 'system'] as const;
const USER_TOOLS_QUERY_KEY = ['tools', 'user'] as const;

function systemToolQueryKey(toolId: string) {
  return ['tools', 'system', toolId] as const;
}

export function useSystemTools() {
  return useQuery<SystemToolSummary[]>({
    queryKey: SYSTEM_TOOLS_QUERY_KEY,
    queryFn: () => toolsApi.listSystem(),
  });
}

export function useUserTools() {
  return useQuery<UserTool[]>({
    queryKey: USER_TOOLS_QUERY_KEY,
    queryFn: () => toolsApi.list(),
  });
}

export function useSystemTool(toolId: string | null) {
  return useQuery<SystemTool>({
    queryKey: toolId ? systemToolQueryKey(toolId) : ['tools', 'system', 'none'],
    queryFn: () => toolsApi.getSystem(toolId as string),
    enabled: toolId != null,
  });
}

export function useCreateUserTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: UserToolCreate) => toolsApi.create(input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: USER_TOOLS_QUERY_KEY });
    },
  });
}

export function useUpdateUserTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: UserToolUpdate }) =>
      toolsApi.update(id, patch),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: USER_TOOLS_QUERY_KEY });
    },
  });
}

export function useDeleteUserTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (toolId: string) => toolsApi.delete(toolId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: USER_TOOLS_QUERY_KEY });
    },
  });
}

export function useDuplicateUserTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (toolId: string) => toolsApi.duplicate(toolId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: USER_TOOLS_QUERY_KEY });
    },
  });
}
