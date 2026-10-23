// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Imported docs sites: the `/sources/sites` routes.
 *
 * A site check (`preview`) finds a site's pages without importing anything;
 * `create` imports them, each page its own source; `refresh` checks the site
 * for updates (the pages the site leaves out stay out); `remove` deletes the site, its pages kept or deleted with it.
 */

import { apiClient } from './client';
import { mapSourceToUnified } from './sourceProcessing';
import type {
  PaginatedSourcesResponse,
  Site,
  SitePreview,
  UnifiedSource,
  UrlImportOptions,
} from '../../types';

/**
 * The query key every site query lives under (the site list, each site's
 * pages): under `['sources']`, so invalidating the sources refreshes the sites.
 */
export const SITES_QUERY_KEY = ['sources', 'sites'] as const;

interface PaginatedSites {
  data: Site[];
}

export const sitesApi = {
  /** Find the pages of the site at `url` (`POST /sources/sites/previews`); imports nothing. */
  preview: async (url: string): Promise<SitePreview> => {
    const response = await apiClient.post<SitePreview>('/sources/sites/previews', { url });
    return response.data;
  },

  /**
   * Import a site (`POST /sources/sites`, 202): every page the import's own
   * discovery finds but the `exclude`d ones (none when the list is empty).
   * 409 when the site is already imported (the body names it).
   */
  create: async (url: string, exclude: string[], options: UrlImportOptions): Promise<Site> => {
    const response = await apiClient.post<Site>('/sources/sites', {
      url,
      exclude: exclude.length > 0 ? exclude : null,
      options,
    });
    return response.data;
  },

  /** The imported sites, newest first, each with its progress. */
  list: async (pageSize: number): Promise<Site[]> => {
    const response = await apiClient.get<PaginatedSites>('/sources/sites', {
      params: { page_size: pageSize },
    });
    return response.data.data;
  },

  /** A site's pages, as the sources list shows them (gone ones carry `site_gone_at`). */
  listPages: async (siteId: string, pageSize: number): Promise<UnifiedSource[]> => {
    const response = await apiClient.get<PaginatedSourcesResponse>(
      `/sources/sites/${siteId}/pages`,
      { params: { page_size: pageSize } },
    );
    return response.data.data.map(mapSourceToUnified);
  },

  /**
   * Check a site for updates (`POST /sources/sites/{id}/refreshes`, 202; 409 while one runs).
   * With no `exclude` the pages the site leaves out stay out; a list replaces
   * them (`[]` brings every page back).
   */
  refresh: async (siteId: string, exclude?: string[]): Promise<Site> => {
    const url = `/sources/sites/${siteId}/refreshes`;
    const response =
      exclude === undefined
        ? await apiClient.post<Site>(url)
        : await apiClient.post<Site>(url, { exclude });
    return response.data;
  },

  /** Delete a site; `deletePages` deletes its pages too, else they stay as ordinary sources. */
  remove: async (siteId: string, deletePages: boolean): Promise<void> => {
    await apiClient.delete(`/sources/sites/${siteId}`, {
      params: { delete_pages: deletePages },
    });
  },
};
