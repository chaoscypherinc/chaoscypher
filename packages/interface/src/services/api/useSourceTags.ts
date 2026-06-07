// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the source-scoped tag editor (InlineTagEditor).
 *
 * Introduced with the SourcePage tags migration off raw
 * fetch+useState+useEffect.
 *
 *   - `useSourceAssignedTags` — the tags assigned to one source
 *     (`['source', sourceId, 'tags']`).
 *   - `useAllTags` — the full tag catalog used to populate the
 *     autocomplete (`['tags']`).
 *   - `useAssignTag` / `useUnassignTag` / `useCreateAndAssignTag` — the
 *     add/remove/create mutations. Each invalidates the source-scoped
 *     `['source', sourceId, 'tags']` key (and the global `['tags']`
 *     catalog when it changes) so the chips re-fetch after a mutation,
 *     matching the source-key convention used elsewhere on SourcePage.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';

import { sourcesApi, tagsApi } from './sources';
import type { SourceTag } from '../../types';

// ========================================
// Query keys (module-local — exporting trips knip)
// ========================================

const sourceTagsQueryKey = (sourceId: string) =>
  ['source', sourceId, 'tags'] as const;

const ALL_TAGS_QUERY_KEY = ['tags'] as const;

// ========================================
// Queries
// ========================================

/** Tags currently assigned to one source. */
export function useSourceAssignedTags(
  sourceId: string,
): UseQueryResult<SourceTag[], Error> {
  return useQuery<SourceTag[], Error>({
    queryKey: sourceTagsQueryKey(sourceId),
    queryFn: () => sourcesApi.getTags(sourceId),
  });
}

/** Full tag catalog (for the add-tag autocomplete + create-on-the-fly). */
export function useAllTags(): UseQueryResult<SourceTag[], Error> {
  return useQuery<SourceTag[], Error>({
    queryKey: ALL_TAGS_QUERY_KEY,
    queryFn: () => tagsApi.list(),
  });
}

// ========================================
// Mutations
// ========================================

/** Assign an existing tag to the source. */
export function useAssignTag(sourceId: string) {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (tagId) => sourcesApi.assignTag(sourceId, tagId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceTagsQueryKey(sourceId) });
    },
  });
}

/** Remove a tag from the source. */
export function useUnassignTag(sourceId: string) {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (tagId) => sourcesApi.unassignTag(sourceId, tagId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceTagsQueryKey(sourceId) });
    },
  });
}

/**
 * Create a brand-new tag and immediately assign it to the source.
 *
 * Invalidates both the source's assigned tags and the global catalog so
 * the new tag shows up everywhere it's listed.
 */
export function useCreateAndAssignTag(sourceId: string) {
  const qc = useQueryClient();
  return useMutation<void, Error, { name: string; color: string }>({
    mutationFn: async ({ name, color }) => {
      const newTag = await tagsApi.create({ name, color });
      await sourcesApi.assignTag(sourceId, newTag.id);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceTagsQueryKey(sourceId) });
      void qc.invalidateQueries({ queryKey: ALL_TAGS_QUERY_KEY });
    },
  });
}

/**
 * Imperatively refresh both tag queries — used by the TagManager dialog's
 * `onTagsChanged` callback, which mutates tags outside this component.
 */
export function useRefreshTags(sourceId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: sourceTagsQueryKey(sourceId) });
    void qc.invalidateQueries({ queryKey: ALL_TAGS_QUERY_KEY });
  };
}
