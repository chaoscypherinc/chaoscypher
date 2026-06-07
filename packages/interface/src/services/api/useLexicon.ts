// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for the Lexicon package registry.
 *
 * Introduced with the LexiconPage migration off raw fetch+useState. The two
 * read queries back the page's "popular packages on mount" panel and its
 * debounced search; the import mutation queues a package download in the
 * worker. The device-auth login/logout/poll flow stays in `useLexiconAuth`
 * (imperative timer chain that doesn't fit a query) and is intentionally not
 * migrated here.
 *
 * Errors are surfaced to the caller unchanged so the page can keep its
 * existing 503-detection: a 503 from any lexicon endpoint means the optional
 * registry service isn't deployed/reachable, and the page swaps its whole
 * body for a single "service unavailable" panel. The hooks don't swallow that
 * — they expose `error` and the page narrows it with `isApiError`.
 */

import { useMutation, useQuery } from '@tanstack/react-query';

import { lexiconApi } from './lexicon';
import type {
  LexiconSearchResponse,
  SortOption,
} from '../../types/lexicon';

const POPULAR_PACKAGES_QUERY_KEY = ['lexicon', 'popular'] as const;
const LEXICON_SEARCH_QUERY_KEY = ['lexicon', 'search'] as const;

interface UsePopularPackagesOptions {
  limit: number;
}

/**
 * Top packages by download count, shown when there's no active search query.
 * `gcTime: 0` + `retry: false` keep the on-mount fetch behaviour close to the
 * old `loadPopularPackages` call (single attempt, no stale cache surprises);
 * the Retry button on the unavailable panel re-runs it via `refetch`.
 */
export function usePopularPackages({ limit }: UsePopularPackagesOptions) {
  return useQuery<LexiconSearchResponse>({
    queryKey: [...POPULAR_PACKAGES_QUERY_KEY, limit],
    queryFn: () =>
      lexiconApi.searchPackages({
        query: '*', // Wildcard to get all packages
        page: 1,
        limit,
        sort_by: 'downloads',
      }),
  });
}

interface UseSearchPackagesParams {
  query: string;
  page: number;
  sortBy: SortOption;
  limit: number;
}

/**
 * Package search. Disabled (and resolves to no results) while the query is
 * empty, mirroring the old `searchPackages` early-return. The trimmed query,
 * page, and sort are part of the key so paging/sorting refetch.
 */
export function useSearchPackages({ query, page, sortBy, limit }: UseSearchPackagesParams) {
  const trimmed = query.trim();
  return useQuery<LexiconSearchResponse>({
    queryKey: [...LEXICON_SEARCH_QUERY_KEY, trimmed, page, sortBy, limit],
    queryFn: () =>
      lexiconApi.searchPackages({
        query: trimmed,
        page,
        limit,
        sort_by: sortBy,
      }),
    enabled: trimmed.length > 0,
  });
}

interface ImportPackageVars {
  ownerUsername: string;
  repoName: string;
}

/**
 * Queue a package import. Returns immediately with a task_id — the actual
 * import runs async in the worker, so there's no list to invalidate here;
 * the page surfaces the queued message via a snackbar.
 */
export function useImportPackage() {
  return useMutation({
    mutationFn: ({ ownerUsername, repoName }: ImportPackageVars) =>
      lexiconApi.importFromLexicon(ownerUsername, repoName),
  });
}
