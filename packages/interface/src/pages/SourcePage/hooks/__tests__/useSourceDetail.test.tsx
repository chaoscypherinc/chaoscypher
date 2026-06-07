// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for `useSourceDetail` after its migration to TanStack Query.
 *
 * Covers:
 *   - the loaded happy path (source + stats once committed),
 *   - the data-driven `refetchInterval`: it polls while the source is in a
 *     processing status and STOPS once the source reaches a terminal
 *     (committed) status,
 *   - a mutation (toggleEnabled → PATCH) writing back into the source.
 *
 * Mocks at the `apiClient` layer so the real service modules + query hooks
 * run unchanged.
 */

import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { useSourceDetail } from '../useSourceDetail';

vi.mock('../../../../services/api/client', () => installApiClientMock());

import { apiClient } from '../../../../services/api/client';

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const navigate = vi.fn();

function makeSource(overrides: Record<string, unknown> = {}) {
  return {
    id: 's1',
    filename: 'doc.pdf',
    title: 'Doc',
    status: 'committed',
    enabled: true,
    chunk_count: 3,
    extraction_entities_count: 0,
    extraction_relationships_count: 0,
    commit_templates_created: 0,
    ...overrides,
  };
}

function wrap({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function countGetCalls(url: string): number {
  return mockedApiClient.get.mock.calls.filter((c) => c[0] === url).length;
}

describe('useSourceDetail (TanStack Query)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads the source and, once committed, its stats', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources/s1') return Promise.resolve({ data: makeSource() });
      if (url === '/sources/s1/stats') {
        return Promise.resolve({ data: { total_nodes: 5 } });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useSourceDetail('s1', navigate), {
      wrapper: wrap,
    });

    await waitFor(() => expect(result.current.source).not.toBeNull());
    expect(result.current.source?.id).toBe('s1');
    // Stats query is enabled only once the source is committed.
    await waitFor(() =>
      expect(result.current.stats).toEqual({ total_nodes: 5 }),
    );
    expect(result.current.loadError).toBeNull();
  });

  it('surfaces a load error when the source fetch fails', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources/s1') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useSourceDetail('s1', navigate), {
      wrapper: wrap,
    });

    await waitFor(() =>
      expect(result.current.loadError).toContain('Failed to load source'),
    );
    expect(result.current.source).toBeNull();
  });

  it('does NOT fetch source stats while the source is still processing', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources/s1') {
        return Promise.resolve({ data: makeSource({ status: 'indexing' }) });
      }
      // Stats / extraction would 404 server-side — assert we never call them.
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useSourceDetail('s1', navigate), {
      wrapper: wrap,
    });

    await waitFor(() => expect(result.current.source?.status).toBe('indexing'));
    expect(countGetCalls('/sources/s1/stats')).toBe(0);
  });

  it('polls while processing and stops once the source reaches a terminal status', async () => {
    vi.useFakeTimers();

    // First fetch → extracting (in-progress). Subsequent fetches → committed
    // (terminal), at which point the refetchInterval predicate returns false.
    let calls = 0;
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources/s1') {
        calls += 1;
        const status = calls === 1 ? 'extracting' : 'committed';
        return Promise.resolve({ data: makeSource({ status }) });
      }
      // extraction-progress endpoint while extracting
      if (url === '/sources/s1/extraction') {
        return Promise.resolve({ data: { has_extraction_job: false } });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useSourceDetail('s1', navigate), {
      wrapper: wrap,
    });

    // Initial fetch resolves to "extracting".
    await vi.waitFor(() => expect(result.current.source?.status).toBe('extracting'));
    const afterFirst = countGetCalls('/sources/s1');

    // Advance past one poll interval (3s) — the predicate saw "extracting" so
    // it should poll again, this time landing on "committed".
    await vi.advanceTimersByTimeAsync(3100);
    await vi.waitFor(() => expect(result.current.source?.status).toBe('committed'));
    const afterPoll = countGetCalls('/sources/s1');
    expect(afterPoll).toBeGreaterThan(afterFirst);

    // Now terminal — further time advances must NOT trigger more source
    // fetches (refetchInterval returned false).
    await vi.advanceTimersByTimeAsync(10000);
    expect(countGetCalls('/sources/s1')).toBe(afterPoll);
  });

  it('toggleEnabled PATCHes the source and updates the cached value', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources/s1') {
        return Promise.resolve({ data: makeSource({ enabled: true }) });
      }
      return Promise.resolve({ data: {} });
    });
    mockedApiClient.patch.mockResolvedValue({
      data: makeSource({ enabled: false }),
    });

    const { result } = renderHook(() => useSourceDetail('s1', navigate), {
      wrapper: wrap,
    });

    await waitFor(() => expect(result.current.source?.enabled).toBe(true));

    await result.current.toggleEnabled();

    expect(mockedApiClient.patch).toHaveBeenCalledWith(
      '/sources/s1',
      { enabled: false },
    );
    await waitFor(() => expect(result.current.source?.enabled).toBe(false));
  });
});
