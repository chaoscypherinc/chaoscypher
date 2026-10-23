// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks for imported docs sites.
 *
 * The site list and each site's pages live under the `['sources', 'sites']`
 * key, so anything that invalidates `['sources']` refreshes them too; the
 * site check (preview) lives under its own key, so a list refresh never
 * re-runs a discovery (`useSitePreview`, its own module: the Add Source
 * dialog loads with the app shell). While a site is being imported or checked, its
 * queries poll, the way the sources list does while a source is processing.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { SITES_QUERY_KEY, sitesApi } from './sites';
import { useAppConfig } from '../../contexts/useAppConfig';
import type { Site, UnifiedSource } from '../../types';

function sitePagesQueryKey(siteId: string) {
  return [...SITES_QUERY_KEY, siteId, 'pages'] as const;
}

/** Whether a site is being imported or checked (its live run, or any of its pages, still moving). */
export function siteIsBusy(site: Site): boolean {
  return siteRunInProgress(site) || site.progress.in_progress > 0;
}

/**
 * Whether a site's own run (import or check for updates) is live: the backend's
 * `run_in_progress`, which a run left behind by a killed worker no longer
 * holds once it goes stale (its status alone would lock the site for good).
 */
export function siteRunInProgress(site: Site): boolean {
  return site.run_in_progress;
}

interface UseSitesOptions {
  /** Poll interval (ms) while a site is busy. */
  pollInterval?: number;
  /** Fetch at all (the sources list sets the site rows aside while it is filtered). */
  enabled?: boolean;
}

/** The imported sites, polled while any of them is busy. */
export function useSites(options: UseSitesOptions = {}) {
  const { pollInterval, enabled = true } = options;
  const { batch_graph_source_page_size: pageSize } = useAppConfig();
  return useQuery<Site[]>({
    queryKey: SITES_QUERY_KEY,
    queryFn: () => sitesApi.list(pageSize),
    enabled,
    refetchInterval: pollInterval
      ? (query) => (query.state.data?.some(siteIsBusy) ? pollInterval : false)
      : false,
  });
}

interface SitePagesOptions {
  /** Fetch only while the site row is expanded. */
  enabled: boolean;
  /** Poll interval (ms), or false to not poll. */
  refetchInterval: number | false;
}

/** One site's pages (every one, up to `web.site_max_pages`), as source rows. */
export function useSitePages(siteId: string, options: SitePagesOptions) {
  const { web_site_max_pages: maxPages, pagination_max_page_size: maxPageSize } = useAppConfig();
  return useQuery<UnifiedSource[]>({
    queryKey: sitePagesQueryKey(siteId),
    queryFn: () => sitesApi.listPages(siteId, Math.min(maxPages, maxPageSize)),
    enabled: options.enabled,
    refetchInterval: options.refetchInterval,
  });
}

interface RefreshSiteArgs {
  siteId: string;
  /** A new list of pages to leave out; omitted, the site's own list stays. */
  exclude?: string[];
}

/** Check a site for updates; the site list and its pages refresh afterwards. */
export function useRefreshSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ siteId, exclude }: RefreshSiteArgs) => sitesApi.refresh(siteId, exclude),
    onSettled: () => queryClient.invalidateQueries({ queryKey: SITES_QUERY_KEY }),
  });
}

interface DeleteSiteArgs {
  siteId: string;
  deletePages: boolean;
}

/** Delete a site (its pages kept or deleted); every sources query refreshes afterwards. */
export function useDeleteSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ siteId, deletePages }: DeleteSiteArgs) => sitesApi.remove(siteId, deletePages),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['sources'] }),
  });
}
