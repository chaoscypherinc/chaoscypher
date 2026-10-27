// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { useGraphDataLoader } from '../useGraphDataLoader';

vi.mock('../../../../services/api/graph', () => ({
  graphApi: {
    fetchCanvasData: vi.fn(async () => ({
      truncated: false,
      nodes: [{ id: 'n1', template_id: 't', label: 'One', source_id: 's1' }],
      edges: [],
      templates: [],
    })),
  },
}));

describe('useGraphDataLoader', () => {
  it('calls onLoaded after every load, the first and each reload (a source-filter change)', async () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
    const seen: number[] = [];
    const onLoaded = vi.fn((g: Graph<NodeAttributes, EdgeAttributes>) => {
      seen.push(g.order);
    });
    const { result } = renderHook(() =>
      useGraphDataLoader({
        graph,
        applyLayout: async () => {},
        layoutType: 'manual',
        onLoaded,
      }),
    );

    await act(async () => {
      await result.current.loadGraphData();
    });
    // A reload clears and repopulates the graph; whatever hung off it (the
    // source roots) must be rebuilt, so onLoaded runs again.
    await act(async () => {
      await result.current.loadGraphData();
    });

    expect(onLoaded).toHaveBeenCalledTimes(2);
    expect(seen).toEqual([1, 1]);
  });
});
