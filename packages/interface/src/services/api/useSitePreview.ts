// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The Add Source dialog's site check, apart from the other site hooks
 * (`useSites.ts`): the dialog loads with the app shell, the site list only
 * with the Sources page. Its key is outside `['sources']`, so a sources
 * refresh never re-runs a discovery.
 */

import { useQuery } from '@tanstack/react-query';

import { sitesApi } from './sites';
import type { SitePreview } from '../../types';

/**
 * Check an address for a site (`null` checks nothing). A refused address
 * answers 400 at once, so a failed check is not retried.
 */
export function useSitePreview(url: string | null) {
  return useQuery<SitePreview>({
    queryKey: ['site-preview', url],
    queryFn: () => sitesApi.preview(url ?? ''),
    enabled: !!url,
    retry: false,
    staleTime: Infinity,
  });
}
