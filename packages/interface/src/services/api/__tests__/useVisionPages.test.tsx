// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the vision-pages TanStack Query hooks.
 *
 * Mocks are wired through ``client.apiClient`` (matching the pattern in
 * ``upgrade.test.ts``) rather than at the ``fetch`` boundary so we exercise
 * the same code path consumers use and don't have to reconstruct the
 * ``ApiResponse<T>`` envelope from raw fetch mocks.
 */

import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import * as client from '../client';
import { NotificationProvider } from '../../../contexts/NotificationContext';
import {
  VISION_PAGES_QUERY_KEY,
  useRetryFailedVisionPages,
  useRetryVisionPage,
  useVisionPages,
} from '../useVisionPages';
import type {
  VisionPage,
  VisionPagesBatchRetryResponse,
  VisionPagesListResponse,
  VisionPageRetryResponse,
} from '../useVisionPages';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

function makeWrapper(qc: QueryClient): React.FC<{ children: ReactNode }> {
  return function Wrapper({ children }) {
    return (
      <QueryClientProvider client={qc}>
        <NotificationProvider>{children}</NotificationProvider>
      </QueryClientProvider>
    );
  };
}

function makeListResponse(): VisionPagesListResponse {
  const page: VisionPage = {
    id: 'page-1',
    source_id: 'src-1',
    job_id: 'job-1',
    page_number: 1,
    region_index: 0,
    kind: 'pdf_page',
    status: 'succeeded',
    image_path: '/data/images/src-1/page_1.png',
    description: 'A diagram.',
    finish_reason: 'stop',
    error_message: null,
    created_at: '2026-05-14T00:00:00Z',
    updated_at: '2026-05-14T00:00:01Z',
  };
  return {
    source_id: 'src-1',
    job: {
      id: 'job-1',
      total_pages: 1,
      completed: 1,
      failed: 0,
      is_terminal: true,
      created_at: '2026-05-14T00:00:00Z',
      updated_at: '2026-05-14T00:00:01Z',
    },
    pages: [page],
  };
}

// ---------------------------------------------------------------------------
// Query key
// ---------------------------------------------------------------------------

describe('VISION_PAGES_QUERY_KEY', () => {
  it('returns the canonical ["source", <id>, "vision_pages"] tuple', () => {
    expect(VISION_PAGES_QUERY_KEY('src-1')).toEqual([
      'source',
      'src-1',
      'vision_pages',
    ]);
  });

  it('uses snake_case "vision_pages" (matches the API path; CC006 forbids hyphens)', () => {
    const key = VISION_PAGES_QUERY_KEY('src-1');
    expect(key[2]).toBe('vision_pages');
    expect(key[2]).not.toContain('-');
  });
});

// ---------------------------------------------------------------------------
// useVisionPages — GET list
// ---------------------------------------------------------------------------

describe('useVisionPages', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('calls GET /sources/<id>/vision_pages (snake_case path, URL-encoded id)', async () => {
    const getSpy = vi
      .spyOn(client.apiClient, 'get')
      .mockResolvedValueOnce({
        data: makeListResponse(),
        status: 200,
        headers: new Headers(),
      });

    const qc = makeQueryClient();
    const { result } = renderHook(() => useVisionPages('src-1'), {
      wrapper: makeWrapper(qc),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getSpy).toHaveBeenCalledWith('/sources/src-1/vision_pages');
    expect(result.current.data?.pages).toHaveLength(1);
    expect(result.current.data?.job?.is_terminal).toBe(true);
  });

  it('URL-encodes the source id so a slash-containing id does not break path routing', async () => {
    const getSpy = vi
      .spyOn(client.apiClient, 'get')
      .mockResolvedValueOnce({
        data: makeListResponse(),
        status: 200,
        headers: new Headers(),
      });

    const qc = makeQueryClient();
    renderHook(() => useVisionPages('src/with/slash'), {
      wrapper: makeWrapper(qc),
    });

    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(1));

    expect(getSpy).toHaveBeenCalledWith(
      '/sources/src%2Fwith%2Fslash/vision_pages',
    );
  });

  it('respects opts.enabled=false (does not fire the request)', async () => {
    const getSpy = vi.spyOn(client.apiClient, 'get');

    const qc = makeQueryClient();
    const { result } = renderHook(
      () => useVisionPages('src-1', { enabled: false }),
      { wrapper: makeWrapper(qc) },
    );

    // A disabled query never transitions to loading.
    expect(result.current.fetchStatus).toBe('idle');
    expect(getSpy).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// useRetryVisionPage — POST single retry
// ---------------------------------------------------------------------------

describe('useRetryVisionPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function retryResponse(pageNumber: number, regionIndex = 0): VisionPageRetryResponse {
    return {
      source_id: 'src-1',
      page_number: pageNumber,
      region_index: regionIndex,
      page_id: `page-${pageNumber}-${regionIndex}`,
      status: 'pending',
      reset: true,
    };
  }

  it('POSTs to /sources/<id>/vision_pages/<page>/retry with NO query string when region_index is 0', async () => {
    const postSpy = vi
      .spyOn(client.apiClient, 'post')
      .mockResolvedValueOnce({
        data: retryResponse(3, 0),
        status: 200,
        headers: new Headers(),
      });

    const qc = makeQueryClient();
    const { result } = renderHook(() => useRetryVisionPage('src-1'), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate({ pageNumber: 3 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // url=<bare path>, body=undefined, config=undefined → no params.
    expect(postSpy).toHaveBeenCalledWith(
      '/sources/src-1/vision_pages/3/retry',
      undefined,
      undefined,
    );
  });

  it('POSTs with ?region_index=N when region_index is non-zero', async () => {
    const postSpy = vi
      .spyOn(client.apiClient, 'post')
      .mockResolvedValueOnce({
        data: retryResponse(5, 2),
        status: 200,
        headers: new Headers(),
      });

    const qc = makeQueryClient();
    const { result } = renderHook(() => useRetryVisionPage('src-1'), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate({ pageNumber: 5, regionIndex: 2 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(postSpy).toHaveBeenCalledWith(
      '/sources/src-1/vision_pages/5/retry',
      undefined,
      { params: { region_index: 2 } },
    );
  });

  it('invalidates the list query on success', async () => {
    vi.spyOn(client.apiClient, 'post').mockResolvedValueOnce({
      data: retryResponse(1, 0),
      status: 200,
      headers: new Headers(),
    });

    const qc = makeQueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    const { result } = renderHook(() => useRetryVisionPage('src-1'), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate({ pageNumber: 1 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: VISION_PAGES_QUERY_KEY('src-1'),
    });
  });
});

// ---------------------------------------------------------------------------
// useRetryFailedVisionPages — POST batch retry
// ---------------------------------------------------------------------------

describe('useRetryFailedVisionPages', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const batchResponse: VisionPagesBatchRetryResponse = {
    source_id: 'src-1',
    retried_count: 3,
    skipped_count: 0,
    page_ids: ['p-1', 'p-2', 'p-3'],
  };

  it('POSTs to /sources/<id>/vision_pages/retry_failed (no body, no params)', async () => {
    const postSpy = vi.spyOn(client.apiClient, 'post').mockResolvedValueOnce({
      data: batchResponse,
      status: 200,
      headers: new Headers(),
    });

    const qc = makeQueryClient();
    const { result } = renderHook(() => useRetryFailedVisionPages('src-1'), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate();

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(postSpy).toHaveBeenCalledWith(
      '/sources/src-1/vision_pages/retry_failed',
    );
    expect(result.current.data?.retried_count).toBe(3);
  });

  it('invalidates the list query on success', async () => {
    vi.spyOn(client.apiClient, 'post').mockResolvedValueOnce({
      data: batchResponse,
      status: 200,
      headers: new Headers(),
    });

    const qc = makeQueryClient();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    const { result } = renderHook(() => useRetryFailedVisionPages('src-1'), {
      wrapper: makeWrapper(qc),
    });

    result.current.mutate();

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: VISION_PAGES_QUERY_KEY('src-1'),
    });
  });
});

// ---------------------------------------------------------------------------
// Mutation-hardening tests (2026-05-19, added alongside `make mutate-interface`).
// Stryker spotted two surviving BooleanLiteral mutants in useVisionPages:
//   line 118: refetchInterval ?? false   -> ?? true
//   line 120: refetchOnWindowFocus: false -> true
// Both would cause the Source detail page to poll the vision endpoint
// unexpectedly. The tests below assert the query-options contract so
// any future regression trips the suite.
// ---------------------------------------------------------------------------

// We assert the *options object* passed to TanStack Query's useQuery rather
// than relying on its runtime focus/polling behavior. The runtime behavior
// of `refetchInterval: true` vs `false` is internal to TanStack Query and
// not directly observable in jsdom focus-event tests, but the options
// object the hook constructs IS directly observable via a vi.mock on
// '@tanstack/react-query' that captures the call arguments.
vi.mock('@tanstack/react-query', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-query')>(
    '@tanstack/react-query',
  );
  return {
    ...actual,
    useQuery: vi.fn(actual.useQuery),
  };
});

describe('useVisionPages query options (mutation hardening)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('pins refetchOnWindowFocus to false (would survive a true-flip mutation)', async () => {
    vi.spyOn(client.apiClient, 'get').mockResolvedValue({
      data: makeListResponse(),
      status: 200,
      headers: new Headers(),
    });

    const { useQuery: useQueryMock } = await import('@tanstack/react-query');
    const qc = makeQueryClient();
    const { result } = renderHook(() => useVisionPages('src-1'), {
      wrapper: makeWrapper(qc),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // useQuery was called at least once; the first call's options carry
    // the contract we care about.
    const firstCallArgs = (useQueryMock as unknown as { mock: { calls: unknown[][] } }).mock.calls[0];
    const options = firstCallArgs[0] as { refetchOnWindowFocus?: boolean };
    expect(options.refetchOnWindowFocus).toBe(false);
  });

  it('defaults refetchInterval to false when not supplied (would survive a true-flip mutation)', async () => {
    vi.spyOn(client.apiClient, 'get').mockResolvedValue({
      data: makeListResponse(),
      status: 200,
      headers: new Headers(),
    });

    const { useQuery: useQueryMock } = await import('@tanstack/react-query');
    const qc = makeQueryClient();
    const { result } = renderHook(() => useVisionPages('src-1'), {
      wrapper: makeWrapper(qc),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const firstCallArgs = (useQueryMock as unknown as { mock: { calls: unknown[][] } }).mock.calls[0];
    const options = firstCallArgs[0] as { refetchInterval?: number | false };
    expect(options.refetchInterval).toBe(false);
  });

  it('defaults enabled to true when not supplied (would survive an && / true-flip mutation)', async () => {
    vi.spyOn(client.apiClient, 'get').mockResolvedValue({
      data: makeListResponse(),
      status: 200,
      headers: new Headers(),
    });

    const { useQuery: useQueryMock } = await import('@tanstack/react-query');
    const qc = makeQueryClient();
    const { result } = renderHook(() => useVisionPages('src-1'), {
      wrapper: makeWrapper(qc),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const firstCallArgs = (useQueryMock as unknown as { mock: { calls: unknown[][] } }).mock.calls[0];
    const options = firstCallArgs[0] as { enabled?: boolean };
    expect(options.enabled).toBe(true);
  });
});
