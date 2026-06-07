// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useChunkDetail } from '../useChunkDetail';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(async (_path: string) => ({
      data: {
        id: 'c1',
        chunk_index: 1,
        content: 'cleaned',
        raw_content: 'raw original',
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: {},
    })),
  },
  API_BASE: '/api/v1',
}));

function wrap({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useChunkDetail', () => {
  beforeEach(() => vi.clearAllMocks());

  it('does not fetch when chunkId is null', () => {
    const { result } = renderHook(() => useChunkDetail('s1', null), { wrapper: wrap });
    expect(result.current.data).toBeUndefined();
    expect(result.current.isLoading).toBe(false);
  });

  it('fetches and returns chunk with raw_content when chunkId set', async () => {
    const { result } = renderHook(() => useChunkDetail('s1', 'c1'), { wrapper: wrap });
    await waitFor(() => expect(result.current.data?.raw_content).toBe('raw original'));
  });
});
