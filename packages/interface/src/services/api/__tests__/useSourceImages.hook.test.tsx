// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the ``useSourceImages`` TanStack Query hook.
 *
 * Backend returns urls as bare paths (``/sources/<id>/images/<file>``);
 * the hook normalizes them to ``${API_BASE}${url}`` so consumers can use
 * the returned ``url`` directly as ``<img src>`` without each one
 * remembering to prepend the API base.
 */

import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import * as client from '../client';
import { useSourceImages } from '../useSourceImages';

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
}

function makeWrapper(qc: QueryClient): React.FC<{ children: ReactNode }> {
  return function Wrapper({ children }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe('useSourceImages', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('prepends API_BASE to each returned image url so <img src> works directly', async () => {
    vi.spyOn(client.apiClient, 'get').mockResolvedValueOnce({
      data: [
        { filename: 'page_1.png', url: '/sources/src-1/images/page_1.png' },
        { filename: 'page_2.png', url: '/sources/src-1/images/page_2.png' },
      ],
      status: 200,
      headers: new Headers(),
    });

    const qc = makeQueryClient();
    const { result } = renderHook(() => useSourceImages('src-1'), {
      wrapper: makeWrapper(qc),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual([
      { filename: 'page_1.png', url: `${client.API_BASE}/sources/src-1/images/page_1.png` },
      { filename: 'page_2.png', url: `${client.API_BASE}/sources/src-1/images/page_2.png` },
    ]);
  });
});
