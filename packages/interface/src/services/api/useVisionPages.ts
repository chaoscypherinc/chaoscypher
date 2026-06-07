// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * API client + TanStack Query hooks for the vision-pages endpoints.
 *
 *   GET    /api/v1/sources/{source_id}/vision_pages                       — list
 *   POST   /api/v1/sources/{source_id}/vision_pages/{page_number}/retry   — retry one
 *   POST   /api/v1/sources/{source_id}/vision_pages/retry_failed          — retry all failed
 *
 * Backs the per-page vision panel on the Source detail view: the list query
 * is the source of truth for page rows (status, error, description), the
 * single-page retry primes one row and re-enqueues it, and the batch retry
 * resets every ``failed`` row in one call. Both mutations invalidate the
 * list query so the panel re-fetches the freshly-reset rows.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query';

import { useNotification } from '../../contexts/useNotification';
import { apiClient } from './client';

// ========================================
// Wire types — kept hand-rolled until the generated OpenAPI types in
// `types/generated/api.ts` are regenerated alongside the new endpoints
// (PR3 Task 11). Field names mirror the Pydantic DTOs in
// `chaoscypher_cortex.features.sources.models`.
// ========================================

export type VisionPageStatus = 'pending' | 'succeeded' | 'failed' | 'truncated';
export type VisionPageKind = 'pdf_page' | 'standalone_image';

export interface VisionPage {
  id: string;
  source_id: string;
  job_id: string;
  page_number: number;
  region_index: number;
  kind: VisionPageKind;
  status: VisionPageStatus;
  image_path: string;
  description: string | null;
  finish_reason: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface VisionJobSummary {
  id: string;
  total_pages: number;
  completed: number;
  failed: number;
  is_terminal: boolean;
  created_at: string;
  updated_at: string;
}

export interface VisionPagesListResponse {
  source_id: string;
  job: VisionJobSummary | null;
  pages: VisionPage[];
}

export interface VisionPageRetryResponse {
  source_id: string;
  page_number: number;
  region_index: number;
  page_id: string;
  status: VisionPageStatus;
  reset: boolean;
}

export interface VisionPagesBatchRetryResponse {
  source_id: string;
  retried_count: number;
  skipped_count: number;
  page_ids: string[];
}

// ========================================
// Query keys
// ========================================

/**
 * Canonical query key for the per-source vision-pages list.
 *
 * Tuple shape mirrors the ``['source', <id>, '<subresource>']`` convention
 * used by ``SOURCE_IMAGES_QUERY_KEY`` and the rest of the source-scoped
 * queries.
 */
export const VISION_PAGES_QUERY_KEY = (sourceId: string) =>
  ['source', sourceId, 'vision_pages'] as const;

// ========================================
// Hooks
// ========================================

/**
 * Fetch the vision-pages list for a source.
 *
 * Pass ``refetchInterval`` to enable polling while the job is in flight
 * (the panel does this only when the source is in ``vision_pending`` so
 * we don't hammer the endpoint after the terminal state is reached).
 */
export function useVisionPages(
  sourceId: string,
  opts?: { refetchInterval?: number | false; enabled?: boolean },
): UseQueryResult<VisionPagesListResponse, Error> {
  return useQuery({
    queryKey: VISION_PAGES_QUERY_KEY(sourceId),
    queryFn: async (): Promise<VisionPagesListResponse> => {
      const response = await apiClient.get<VisionPagesListResponse>(
        `/sources/${encodeURIComponent(sourceId)}/vision_pages`,
      );
      return response.data;
    },
    refetchInterval: opts?.refetchInterval ?? false,
    enabled: opts?.enabled ?? true,
    refetchOnWindowFocus: false,
  });
}

/**
 * Retry a single vision page (POST /vision_pages/{page}/retry).
 *
 * ``regionIndex`` defaults to 0 (the canonical single-region case); only
 * future multi-region renderers would pass a different value. The query
 * string is omitted when ``regionIndex === 0`` so the URL stays clean for
 * the common case.
 *
 * On success the list query is invalidated so the panel reflects the new
 * ``pending`` status without waiting for the next poll tick.
 */
export function useRetryVisionPage(
  sourceId: string,
): UseMutationResult<
  VisionPageRetryResponse,
  Error,
  { pageNumber: number; regionIndex?: number }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ pageNumber, regionIndex = 0 }) => {
      const config =
        regionIndex !== 0
          ? { params: { region_index: regionIndex } }
          : undefined;
      const response = await apiClient.post<VisionPageRetryResponse>(
        `/sources/${encodeURIComponent(sourceId)}/vision_pages/${pageNumber}/retry`,
        undefined,
        config,
      );
      return response.data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: VISION_PAGES_QUERY_KEY(sourceId),
      });
    },
  });
}

/**
 * Retry every ``failed`` vision page for a source in one call
 * (POST /vision_pages/retry_failed).
 *
 * On success the list query is invalidated so the panel re-fetches the
 * reset rows. ``retried_count`` and ``skipped_count`` in the response let
 * the caller surface a "Retried N pages, skipped M" toast.
 */
export function useRetryFailedVisionPages(
  sourceId: string,
): UseMutationResult<VisionPagesBatchRetryResponse, Error, void> {
  const qc = useQueryClient();
  const { notify } = useNotification();
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<VisionPagesBatchRetryResponse>(
        `/sources/${encodeURIComponent(sourceId)}/vision_pages/retry_failed`,
      );
      return response.data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: VISION_PAGES_QUERY_KEY(sourceId),
      });
    },
    onError: (err) => {
      notify(`Failed to retry vision pages: ${err.message}`, 'error');
    },
  });
}
