// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { useUnresolvedRegion } from '../useUnresolvedRegion';
import { UNRESOLVED_REGION_STORAGE_KEY } from '../../utils/unresolvedRegion';

function attrs(content: Record<string, unknown>): NodeAttributes {
  return {
    nodeId: 'n', title: 'n', content, templateId: 't', tags: [], createdAt: '', updatedAt: '',
    x: 0, y: 0, size: 5, color: '#00E5FF', label: 'n',
  };
}

function makeGraph() {
  const graph = new Graph<NodeAttributes, EdgeAttributes>();
  graph.addNode('a', attrs({ orphan: true }));
  graph.addNode('b', attrs({}));
  graph.addEdge('a', 'b', {
    edgeId: 'e', label: '', templateId: 't', sourceId: 'a', targetId: 'b',
    properties: {}, createdAt: '', updatedAt: '',
  });
  return graph;
}

describe('useUnresolvedRegion', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('starts expanded with no stored state, and persists a toggle', () => {
    const graph = makeGraph();
    const { result } = renderHook(() => useUnresolvedRegion(graph));
    expect(result.current.region.expanded).toBe(true);
    expect(result.current.region.memberIds).toEqual(['a']);

    act(() => result.current.toggle());
    expect(result.current.region.expanded).toBe(false);
    expect(result.current.region.hiddenIds).toEqual(new Set(['a']));
    expect(result.current.region.summaryLabel).toBe('Unresolved (1)');
    expect(localStorage.getItem(UNRESOLVED_REGION_STORAGE_KEY)).toBe('collapsed');
  });

  it('restores the stored collapsed state', () => {
    localStorage.setItem(UNRESOLVED_REGION_STORAGE_KEY, 'collapsed');
    const { result } = renderHook(() => useUnresolvedRegion(makeGraph()));
    expect(result.current.region.expanded).toBe(false);
  });

  it('works for the session when storage throws on every access', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const { result } = renderHook(() => useUnresolvedRegion(makeGraph()));
    expect(result.current.region.expanded).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.region.expanded).toBe(false);
    act(() => result.current.toggle());
    expect(result.current.region.expanded).toBe(true);
  });

  it('tracks membership as nodes are added, re-flagged or dropped, never mutating the graph', () => {
    const graph = makeGraph();
    const { result } = renderHook(() => useUnresolvedRegion(graph));

    act(() => {
      graph.addNode('c', attrs({ descriptor_reference: true }));
    });
    expect(result.current.region.memberIds).toEqual(['a', 'c']);

    act(() => {
      graph.setNodeAttribute('b', 'content', { orphan: true });
    });
    expect(result.current.region.memberIds).toEqual(['a', 'b', 'c']);

    act(() => {
      graph.dropNode('a');
    });
    expect(result.current.region.memberIds).toEqual(['b', 'c']);

    // Collapsing is presentation: the graph keeps every node and edge.
    const before = { order: graph.order, size: graph.size };
    act(() => result.current.toggle());
    expect({ order: graph.order, size: graph.size }).toEqual(before);
  });
});
