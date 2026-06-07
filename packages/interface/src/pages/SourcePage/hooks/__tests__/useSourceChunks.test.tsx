// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the ChunksTab read hooks after their migration to TanStack
 * Query.
 *
 *   - useSourceChunks — paginated chunk list (gated by `enabled`).
 *   - useChunkOutputFeeds — the once-per-tab entities/relationships/tasks
 *     combined query, with graceful empty fallback on failure.
 *   - useResolveHighlightChunkPage — resolves a deep-linked chunk id to its
 *     page; resolves immediately (page: null) when there's no highlight.
 *
 * Mocks at the `apiClient` layer so the real service modules run unchanged.
 */

import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { installApiClientMock } from '../../../../test/mocks/apiClient';
import {
  useSourceChunks,
  useChunkOutputFeeds,
  useResolveHighlightChunkPage,
} from '../useSourceChunks';

vi.mock('../../../../services/api/client', () => installApiClientMock());

import { apiClient } from '../../../../services/api/client';

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

function wrap({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useSourceChunks (TanStack Query)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads a page of chunks and exposes total', async () => {
    mockedApiClient.get.mockResolvedValue({
      data: {
        data: [
          { id: 'c1', chunk_index: 0, content: 'one', status: 'committed', created_at: 'x' },
          { id: 'c2', chunk_index: 1, content: 'two', status: 'committed', created_at: 'x' },
        ],
        pagination: { total: 2, page: 1, page_size: 50 },
      },
    });

    const { result } = renderHook(() => useSourceChunks('s1', 1, 50), { wrapper: wrap });

    await waitFor(() => expect(result.current.chunks.length).toBe(2));
    expect(result.current.total).toBe(2);
    expect(mockedApiClient.get).toHaveBeenCalledWith('/sources/s1/chunks', {
      params: { page: 1, page_size: 50 },
    });
  });

  it('does not fetch while disabled', async () => {
    mockedApiClient.get.mockResolvedValue({ data: { data: [], pagination: { total: 0 } } });

    const { result } = renderHook(() => useSourceChunks('s1', 1, 50, false), {
      wrapper: wrap,
    });

    await new Promise((r) => setTimeout(r, 0));
    expect(mockedApiClient.get).not.toHaveBeenCalled();
    expect(result.current.chunks).toEqual([]);
  });

  it('combines entities, relationships and tasks into one feed', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources/s1/entities') {
        return Promise.resolve({ data: { entities: [{ name: 'E1' }], pagination: {} } });
      }
      if (url === '/sources/s1/relationships') {
        return Promise.resolve({ data: { relationships: [{ id: 'r1' }], pagination: {} } });
      }
      if (url === '/sources/s1/extraction/tasks') {
        return Promise.resolve({ data: { tasks: [{ id: 't1', chunk_index: 0 }], total: 1, page: 1, page_size: 1000 } });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useChunkOutputFeeds('s1'), { wrapper: wrap });

    await waitFor(() => expect(result.current.data).toBeTruthy());
    expect(result.current.data?.entities.length).toBe(1);
    expect(result.current.data?.relationships.length).toBe(1);
    expect(result.current.data?.tasks.length).toBe(1);
  });

  it('resolves immediately (page null) when there is no highlight', () => {
    const { result } = renderHook(
      () => useResolveHighlightChunkPage('s1', null, 50),
      { wrapper: wrap },
    );
    expect(result.current.resolved).toBe(true);
    expect(result.current.page).toBeNull();
    expect(mockedApiClient.get).not.toHaveBeenCalled();
  });

  it('resolves a highlighted chunk to its 1-indexed page', async () => {
    // chunk_index 120, pageSize 50 → ceil(121/50) = 3
    mockedApiClient.get.mockResolvedValue({
      data: { id: 'c-hi', chunk_index: 120, content: 'x', status: 'committed', created_at: 'x' },
    });

    const { result } = renderHook(
      () => useResolveHighlightChunkPage('s1', 'c-hi', 50),
      { wrapper: wrap },
    );

    await waitFor(() => expect(result.current.resolved).toBe(true));
    expect(result.current.page).toBe(3);
    expect(mockedApiClient.get).toHaveBeenCalledWith('/sources/s1/chunks/c-hi');
  });
});
