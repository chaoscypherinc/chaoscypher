// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useSourceGraphPreview — the Overview Knowledge-map data hook.
 *
 * graphApi is mocked; the shared layout runs for real (pure, jsdom-safe).
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const fetchCanvasData = vi.fn();

vi.mock('../../../../../../services/api/graph', () => ({
  graphApi: {
    fetchCanvasData: (...args: unknown[]) => fetchCanvasData(...args),
  },
}));

import { useSourceGraphPreview } from '../useSourceGraphPreview';
import type { CanvasDataResponse } from '../../../../../../types/graph';

function makeCanvasData(overrides: Partial<CanvasDataResponse> = {}): CanvasDataResponse {
  return {
    nodes: [
      { id: 'a', template_id: 't1', label: 'A', source_id: 'src1' },
      { id: 'b', template_id: 't1', label: 'B', source_id: 'src1' },
      { id: 'c', template_id: 't2', label: 'C', source_id: 'src1' },
    ],
    edges: [
      { id: 'e1', source_node_id: 'a', target_node_id: 'b', template_id: 'r1', label: '' },
      { id: 'e2', source_node_id: 'b', target_node_id: 'c', template_id: 'r1', label: '' },
    ],
    templates: [],
    total_nodes: 3,
    total_edges: 2,
    truncated: false,
    ...overrides,
  };
}

function renderPreview(sourceId: string, enabled: boolean) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useSourceGraphPreview(sourceId, enabled), { wrapper });
}

beforeEach(() => {
  fetchCanvasData.mockReset();
  sessionStorage.clear();
});

describe('useSourceGraphPreview', () => {
  it('does not fetch when disabled', () => {
    const { result } = renderPreview('s1', false);
    expect(fetchCanvasData).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
    expect(result.current.nodes).toEqual([]);
  });

  it('fetches the source subgraph and returns positioned nodes + counts', async () => {
    fetchCanvasData.mockResolvedValue(makeCanvasData());
    const { result } = renderPreview('s1', true);

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchCanvasData).toHaveBeenCalledWith(['s1']);
    expect(result.current.nodes).toHaveLength(3);
    expect(result.current.entityCount).toBe(3);
    expect(result.current.relationshipCount).toBe(2);
    expect(result.current.isEmpty).toBe(false);
    for (const n of result.current.nodes) {
      expect(Number.isFinite(n.x)).toBe(true);
      expect(Number.isFinite(n.y)).toBe(true);
    }
  });

  it('keeps orphan entities (no relationships) in the rendered set', async () => {
    fetchCanvasData.mockResolvedValue(
      makeCanvasData({
        nodes: [
          { id: 'a', template_id: 't1', label: 'A', source_id: 'src1' },
          { id: 'b', template_id: 't1', label: 'B', source_id: 'src1' },
          { id: 'lonely', template_id: 't2', label: 'L', source_id: 'src1' },
        ],
        edges: [{ id: 'e1', source_node_id: 'a', target_node_id: 'b', template_id: 'r1', label: '' }],
        total_nodes: 3,
        total_edges: 1,
      }),
    );
    const { result } = renderPreview('s1', true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.nodes.map((n) => n.id)).toContain('lonely');
  });

  it('reports isEmpty when the source has no graph nodes', async () => {
    fetchCanvasData.mockResolvedValue(
      makeCanvasData({ nodes: [], edges: [], total_nodes: 0, total_edges: 0 }),
    );
    const { result } = renderPreview('s1', true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.isEmpty).toBe(true);
    expect(result.current.nodes).toEqual([]);
  });

  it('reports isEmpty (no throw) when the fetch fails', async () => {
    fetchCanvasData.mockRejectedValue(new Error('boom'));
    const { result } = renderPreview('s1', true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.isEmpty).toBe(true);
    expect(result.current.nodes).toEqual([]);
  });
});
