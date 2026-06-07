// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for templates.
 *
 * Introduced with the EdgeDetailPage migration off raw fetch+useState
 * (`useTemplate` as a dependent single-item query). Extended with the
 * TemplateDetailPage migration: `useUpdateTemplate` / `useDeleteTemplate` are
 * the detail-page mutations and invalidate the affected template plus the
 * templates list on success. `useDeleteTemplate` carries the optional `force`
 * flag the page uses for the "force delete" confirmation flow.
 *
 * Extended again with the GraphCanvas creation-modal migration: `useTemplates`
 * is the full (all-pages) list query backing the item/link creation dialogs,
 * the template-selection picker, and the canvas filters menu. It accepts an
 * optional `templateType` ('node' | 'edge') that maps straight onto
 * `templateApi.list(templateType?)` and keys the cache so node and edge lists
 * stay separate.
 *
 * `useTemplate` accepts a nullable id and stays disabled until one is present,
 * so it composes as a dependent query (e.g. an edge's template fetched after
 * the edge resolves).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { templateApi } from './templates';
import type { Template } from '../../types';

const TEMPLATES_QUERY_KEY = ['templates'] as const;

function templateQueryKey(templateId: string) {
  return ['template', templateId] as const;
}

function templatesListQueryKey(templateType?: 'node' | 'edge') {
  return templateType
    ? ([...TEMPLATES_QUERY_KEY, 'list', templateType] as const)
    : ([...TEMPLATES_QUERY_KEY, 'list'] as const);
}

interface UseTemplatesOptions {
  /** Defer the fetch until truthy (e.g. only when a modal is open). */
  enabled?: boolean;
}

/**
 * Full template list (all pages). Optionally filtered to a single
 * `templateType` server-side. Callers do their own client-side filtering
 * (system/lens/workflow exclusion) on top of the returned array. The
 * `enabled` option lets modal callers defer the fetch until they open.
 */
export function useTemplates(
  templateType?: 'node' | 'edge',
  options?: UseTemplatesOptions,
) {
  return useQuery<Template[]>({
    queryKey: templatesListQueryKey(templateType),
    queryFn: () => templateApi.list(templateType),
    enabled: options?.enabled ?? true,
  });
}

export function useTemplate(templateId: string | null | undefined) {
  return useQuery<Template>({
    queryKey: templateId ? templateQueryKey(templateId) : ['template', 'none'],
    queryFn: () => templateApi.get(templateId as string),
    enabled: templateId != null,
  });
}

export function useUpdateTemplate() {
  const qc = useQueryClient();
  return useMutation<Template, Error, { id: string; updates: Partial<Template> }>({
    mutationFn: ({ id, updates }) => templateApi.update(id, updates),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: templateQueryKey(data.id) });
      void qc.invalidateQueries({ queryKey: TEMPLATES_QUERY_KEY });
    },
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  return useMutation<void, Error, { id: string; force?: boolean }>({
    mutationFn: ({ id, force }) => templateApi.delete(id, force),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TEMPLATES_QUERY_KEY });
    },
  });
}
