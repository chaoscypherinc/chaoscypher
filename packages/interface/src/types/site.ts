// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Imported docs sites (`/api/v1/sources/sites`), re-exported from the
 * OpenAPI-generated schema. A site is one row in the sources list; each of
 * its pages is an ordinary source carrying the site's `site_id`.
 */

import type { components } from './generated/api';

/** One imported site with its progress (`GET /sources/sites/{id}`). */
export type Site = components['schemas']['SiteResponse'];

/** What a site check found at an address (`POST /sources/sites/previews`). */
export type SitePreview = components['schemas']['SitePreviewResponse'];

/** How a site's pages were found: `llms_txt`, `sitemap`, `crawl`, or `none`. */
export type SiteDiscoveryMethod = components['schemas']['DiscoveryMethod'];

/** The upload options a URL import (and every page of a site import) applies. */
export type UrlImportOptions = components['schemas']['UrlImportOptions'];

/** `POST /sources/url`: the queued fetch, not a source (the worker creates it). */
export type UrlImportResponse = components['schemas']['UrlImportResponse'];
