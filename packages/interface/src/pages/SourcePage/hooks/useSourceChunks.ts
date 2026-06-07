// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * TanStack Query hooks backing the ChunksTab on the Source detail page.
 *
 * Migrated from raw fetch+useState+useEffect. Three reads:
 *
 *   - `useSourceChunks(sourceId, page, pageSize)` — the paginated chunk
 *     list (`['source', sourceId, 'chunks', page, pageSize]`). Each page
 *     is its own cache entry so paginating back and forth is instant.
 *   - `useChunkOutputFeeds(sourceId)` — the once-per-tab entities +
 *     relationships + extraction-task feeds that the per-chunk OUTPUT
 *     view filters by `chunk_index`. One combined query so the three
 *     calls fire together (mirrors the old `Promise.all`).
 *   - `useResolveHighlightChunkPage(sourceId, highlightChunkId, pageSize)`
 *     — resolves a deep-linked chunk id to the page it lives on so the
 *     list opens on the right page. Returns `{ page, resolved }`.
 *
 * Keys live under `['source', sourceId, …]` so any source-scoped
 * mutation that invalidates the parent key cascades to them.
 */

import { useQuery } from '@tanstack/react-query';

import { sourcesApi } from '../../../services/api/sources';
import type {
  SourceChunk,
  SourceChunkListResponse,
  ExtractedEntity,
  InferredRelationship,
  ExtractionTask,
} from '../../../types';

// ========================================
// Query keys (module-local — exporting trips knip)
// ========================================

const sourceChunksQueryKey = (sourceId: string, page: number, pageSize: number) =>
  ['source', sourceId, 'chunks', page, pageSize] as const;

const chunkOutputFeedsQueryKey = (sourceId: string) =>
  ['source', sourceId, 'chunk-output-feeds'] as const;

const highlightChunkPageQueryKey = (sourceId: string, chunkId: string) =>
  ['source', sourceId, 'highlight-chunk-page', chunkId] as const;

// ========================================
// Chunk list
// ========================================

export interface UseSourceChunksResult {
  chunks: SourceChunk[];
  total: number;
  isLoading: boolean;
  error: unknown;
}

/**
 * Paginated chunk list for a source. `enabled` lets the caller defer the
 * fetch until the initial highlight page has been resolved (otherwise it
 * would fetch page 1 and then immediately re-fetch the resolved page).
 */
export function useSourceChunks(
  sourceId: string,
  page: number,
  pageSize: number,
  enabled: boolean = true,
): UseSourceChunksResult {
  const query = useQuery<SourceChunkListResponse>({
    queryKey: sourceChunksQueryKey(sourceId, page, pageSize),
    queryFn: () => sourcesApi.getChunks(sourceId, { page, page_size: pageSize }),
    enabled,
  });

  return {
    chunks: query.data?.data ?? [],
    total: query.data?.pagination.total ?? 0,
    isLoading: query.isLoading,
    error: query.error,
  };
}

// ========================================
// OUTPUT-view feeds (entities + relationships + extraction tasks)
// ========================================

export interface ChunkOutputFeeds {
  entities: ExtractedEntity[];
  relationships: InferredRelationship[];
  tasks: ExtractionTask[];
}

/**
 * Tab-level OUTPUT feeds loaded once and filtered per-chunk by
 * `chunk_index` in the chunk row body. A single combined query keeps the
 * three calls firing together like the old `Promise.all` did and yields
 * empty arrays on failure so the rest of the tab keeps working.
 */
export function useChunkOutputFeeds(sourceId: string) {
  return useQuery<ChunkOutputFeeds>({
    queryKey: chunkOutputFeedsQueryKey(sourceId),
    queryFn: async () => {
      const [entitiesResponse, relsResponse, tasksResponse] = await Promise.all([
        sourcesApi.getEntities(sourceId, 1, 1000),
        sourcesApi.getRelationships(sourceId, 1, 1000),
        sourcesApi.getExtractionTasks(sourceId, {
          page: 1,
          page_size: 1000,
          include_content: false,
        }),
      ]);
      return {
        entities: entitiesResponse.entities,
        relationships: relsResponse.relationships,
        tasks: tasksResponse.tasks,
      };
    },
  });
}

// ========================================
// Highlight deep-link page resolution
// ========================================

export interface ResolvedHighlightPage {
  /** Page the highlighted chunk lives on (1-indexed), or null while unknown. */
  page: number | null;
  /**
   * True once the resolution has settled — either we found the page, the
   * lookup failed, or there was no highlight to resolve. Gates the chunk
   * list query so it fetches the right page first time.
   */
  resolved: boolean;
}

/**
 * Resolve a deep-linked chunk id to the 1-indexed page it sits on so the
 * chunk list can open directly on that page. With no highlight, resolves
 * immediately (`page: null`, `resolved: true`).
 */
export function useResolveHighlightChunkPage(
  sourceId: string,
  highlightChunkId: string | null | undefined,
  pageSize: number,
): ResolvedHighlightPage {
  const query = useQuery<number>({
    queryKey: highlightChunkPageQueryKey(sourceId, highlightChunkId ?? ''),
    queryFn: async () => {
      const chunk = await sourcesApi.getChunk(sourceId, highlightChunkId as string);
      return Math.ceil((chunk.chunk_index + 1) / pageSize);
    },
    enabled: !!highlightChunkId,
    staleTime: 60_000,
  });

  if (!highlightChunkId) {
    return { page: null, resolved: true };
  }
  // `isFetched` flips true on success OR error — either way the list should
  // stop waiting and load (falling back to page 1 on a failed lookup).
  return { page: query.data ?? null, resolved: query.isFetched };
}
