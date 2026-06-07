// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hook for the rendered page images of a source.
 *
 * The backend writes ``page_{N}.png`` to ``{data_dir}/databases/<db>/images/<source_id>/``
 * during PDF indexing (vision-enabled sources only). The list endpoint
 * (`GET /sources/{id}/images`) is a thin directory scan that returns
 * ``{filename, url}[]`` — no separate page-number field, because the
 * page number is encoded into the filename. Consumers parse it out via
 * ``pageNumberFromFilename`` below.
 *
 * Used by:
 *   - ``ChunksTab`` to render a thumbnail next to each chunk and a
 *     "Page render failed" placeholder when a chunk's expected page
 *     image is missing.
 *   - ``OverviewTab`` (historically — now superseded by the in-chunks
 *     thumbnail strip, but the hook still exists for any consumer that
 *     wants the raw list).
 */

import { useQuery } from '@tanstack/react-query';
import { API_BASE, apiClient } from './client';

interface SourceImage {
  filename: string;
  url: string;
}

const SOURCE_IMAGES_QUERY_KEY = (sourceId: string) =>
  ['source', sourceId, 'images'] as const;

/**
 * Parse the 1-indexed page number out of a ``page_{N}.png`` filename.
 *
 * Returns ``null`` for filenames that don't match the convention — keep
 * the caller defensive against future filename schemes (e.g. paginated
 * non-PDF sources that might write ``slide_{N}.png``).
 */
export function pageNumberFromFilename(filename: string): number | null {
  const match = filename.match(/^page_(\d+)\.png$/i);
  if (!match) return null;
  const n = Number.parseInt(match[1], 10);
  return Number.isFinite(n) ? n : null;
}

/**
 * Fetch the list of rendered page images for a source.
 *
 * Returns an empty array if the source has no images (the endpoint
 * 404s on missing source — the apiClient surfaces the error and React
 * Query exposes it via ``query.error``; callers should treat both an
 * empty array and an error as "no images available").
 */
export function useSourceImages(sourceId: string, enabled: boolean = true) {
  return useQuery({
    queryKey: SOURCE_IMAGES_QUERY_KEY(sourceId),
    queryFn: async (): Promise<SourceImage[]> => {
      const response = await apiClient.get<SourceImage[]>(
        `/sources/${sourceId}/images`,
      );
      // Backend returns urls as bare paths (``/sources/<id>/images/<file>``);
      // prepend API_BASE so consumers can use the returned ``url`` directly
      // as ``<img src>`` without each one remembering the api prefix.
      return response.data.map((img) => ({ ...img, url: `${API_BASE}${img.url}` }));
    },
    enabled,
    // Page images are written once during indexing and don't churn —
    // a long stale time avoids hammering the directory scan endpoint
    // every time the ChunksTab paginates.
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}
