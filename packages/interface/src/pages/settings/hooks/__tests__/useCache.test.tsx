// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { apiClient } from '../../../../services/api/client';
import type { CacheOverview } from '../../../../services/api/cache';
import { useCache } from '../useCache';

vi.mock('../../../../services/api/client', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
}));

const client = vi.mocked(apiClient);

function overview(enabled = true): CacheOverview {
  const store = (name: string) => ({
    name,
    enabled,
    require_hit: false,
    entries: 3,
    bytes: 100,
    max_bytes: 1000,
    hits: 1,
    misses: 2,
    bypasses: 0,
    hit_rate: 1 / 3,
    latency_saved_ms: 500,
    by_call_site: {},
  });
  return { dir: '/data/cache/llm', caches: [store('completions'), store('embeddings')] };
}

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

const ok = <T,>(data: T) => ({ data, status: 200, headers: new Headers() });

describe('useCache', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockResolvedValue(ok(overview()));
  });

  it('reads the overview from GET /cache', async () => {
    const { result } = renderHook(() => useCache(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.overview).not.toBeNull());
    expect(client.get).toHaveBeenCalledWith('/cache');
    expect(result.current.overview?.caches.map((c) => c.name)).toEqual(['completions', 'embeddings']);
    expect(result.current.error).toBeNull();
  });

  it('update PATCHes /cache and takes the returned overview', async () => {
    client.patch.mockResolvedValue(ok(overview(false)));
    const { result } = renderHook(() => useCache(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.overview).not.toBeNull());

    await act(async () => {
      await result.current.update({ completions: { enabled: false } });
    });

    expect(client.patch).toHaveBeenCalledWith('/cache', { completions: { enabled: false } });
    await waitFor(() => expect(result.current.overview?.caches[0].enabled).toBe(false));
  });

  it('clear POSTs /cache/{name}/clear and refetches the overview', async () => {
    client.post.mockResolvedValue(ok({ name: 'embeddings', entries_removed: 3 }));
    const { result } = renderHook(() => useCache(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.overview).not.toBeNull());

    await act(async () => {
      await result.current.clear('embeddings');
    });

    expect(client.post).toHaveBeenCalledWith('/cache/embeddings/clear');
    await waitFor(() => expect(client.get).toHaveBeenCalledTimes(2));
  });

  it('surfaces a failed read as error', async () => {
    client.get.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useCache(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.overview).toBeNull();
  });
});
