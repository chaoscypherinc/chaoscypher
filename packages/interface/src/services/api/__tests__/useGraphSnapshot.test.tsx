// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the service module — must be declared before any imports that pull it
vi.mock('../graphSnapshot', () => ({
  getGraphSnapshot: vi.fn(),
  refreshGraphSnapshot: vi.fn(),
}));

// Import the mocked functions after the vi.mock() call
import * as graphSnapshotService from '../graphSnapshot';
import {
  useGraphSnapshot,
  useRefreshGraphSnapshot,
  GRAPH_SNAPSHOT_QUERY_KEY,
} from '../useGraphSnapshot';
import type { GraphBreakdown } from '../../../types/graphSnapshot';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build an isolated QueryClient per test to avoid cross-test cache pollution. */
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,       // don't retry on error in tests
        gcTime: Infinity,   // keep cache alive for the duration of the test
      },
    },
  });
}

function makeWrapper(qc: QueryClient): React.FC<{ children: ReactNode }> {
  return function Wrapper({ children }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

const sampleBreakdown: GraphBreakdown = {
  version: 2,
  generated_at: '2026-04-22T00:00:00Z',
  database_name: 'test-db',
  title: 'Test Graph',
  stats: { total_nodes: 10, total_edges: 5, total_sources: 2 },
  sources: [
    {
      id: 'src-1',
      name: 'Source One',
      source_type: 'pdf',
      total_entities: 8,
      total_internal_links: 4,
      templates: [{ id: 'tpl-1', name: 'Person', color: '#ff0000', count: 8 }],
    },
  ],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useGraphSnapshot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns null when the service resolves null (simulating 204)', async () => {
    (graphSnapshotService.getGraphSnapshot as ReturnType<typeof vi.fn>).mockResolvedValue(null);

    const qc = makeQueryClient();
    const { result } = renderHook(() => useGraphSnapshot(), { wrapper: makeWrapper(qc) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toBeNull();
  });

  it('returns the GraphBreakdown when the service resolves one', async () => {
    (graphSnapshotService.getGraphSnapshot as ReturnType<typeof vi.fn>).mockResolvedValue(
      sampleBreakdown,
    );

    const qc = makeQueryClient();
    const { result } = renderHook(() => useGraphSnapshot(), { wrapper: makeWrapper(qc) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(sampleBreakdown);
    expect(result.current.data?.stats.total_nodes).toBe(10);
  });
});

describe('useRefreshGraphSnapshot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls refreshGraphSnapshot and then invalidates the snapshot query', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    (graphSnapshotService.getGraphSnapshot as ReturnType<typeof vi.fn>).mockResolvedValue(null);
    (graphSnapshotService.refreshGraphSnapshot as ReturnType<typeof vi.fn>).mockResolvedValue({
      task_id: 'task-abc-123',
    });

    const qc = makeQueryClient();
    // Spy on invalidateQueries to verify it's called
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    const { result } = renderHook(() => useRefreshGraphSnapshot(), { wrapper: makeWrapper(qc) });

    // Fire the mutation and wait for it to settle
    result.current.mutate();

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // Before the 3-second delay the query should NOT yet be invalidated
    expect(invalidateSpy).not.toHaveBeenCalled();

    // Advance the timer past the 3 s delay inside onSuccess
    await act(async () => {
      vi.advanceTimersByTime(3100);
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: GRAPH_SNAPSHOT_QUERY_KEY });

    vi.useRealTimers();
  });
});
