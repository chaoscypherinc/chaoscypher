// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { SOURCE_GROUP_PREFIX, unresolvedBucketId } from '../../types';
import { searchMatches, useSearchHighlight } from '../useSearchHighlight';
import { syncUnresolvedBuckets } from '../../utils/unresolvedBucket';

/** The hub for unresolved nodes with no known source. */
const UNRESOLVED_BUCKET_ID = unresolvedBucketId(null);

function attrs(id: string, title: string, content: Record<string, unknown> = {}): NodeAttributes {
  return {
    nodeId: id, title, content, templateId: 'person', tags: [], createdAt: '', updatedAt: '',
    x: 0, y: 0, size: 5, color: '#00E5FF', label: title,
  };
}

/** A real "Unresolved Matters" node, an unresolved node, a source root and the hub. */
function makeGraph() {
  const graph = new Graph<NodeAttributes, EdgeAttributes>({ type: 'directed' });
  graph.addNode('real', attrs('real', 'Unresolved Matters'));
  graph.addNode('orphan', attrs('orphan', 'Lone Figure', { orphan: true }));
  graph.addNode(`${SOURCE_GROUP_PREFIX}s1`, attrs(`${SOURCE_GROUP_PREFIX}s1`, 'Unresolved Sources'));
  syncUnresolvedBuckets(graph);
  return graph;
}

describe('useSearchHighlight', () => {
  it('matches real nodes only, never a synthetic hub', () => {
    const graph = makeGraph();
    expect(graph.hasNode(UNRESOLVED_BUCKET_ID)).toBe(true);
    const { result } = renderHook(() => useSearchHighlight({ graph, templateFilters: [] }));

    act(() => result.current.handleSearch('unresolved'));
    expect(result.current.highlightedNodeIds).toEqual(new Set(['real']));
  });

  it('still finds an unresolved node by name', () => {
    const graph = makeGraph();
    const { result } = renderHook(() => useSearchHighlight({ graph, templateFilters: [] }));

    act(() => result.current.handleSearch('lone'));
    expect(result.current.highlightedNodeIds).toEqual(new Set(['orphan']));
  });

  it('filters real nodes by template, leaving synthetic hubs out of the hidden set', () => {
    const graph = makeGraph();
    const { result } = renderHook(() =>
      useSearchHighlight({ graph, templateFilters: ['place'] }),
    );
    expect(result.current.hiddenNodeIds).toEqual(new Set(['real', 'orphan']));
  });
  it('lists the matches a search selects from, real nodes only, in graph order', () => {
    const graph = makeGraph();
    expect(searchMatches(graph, 'unresolved')).toEqual(['real']);
    expect(searchMatches(graph, '  LONE ')).toEqual(['orphan']);
    expect(searchMatches(graph, '   ')).toEqual([]);
  });
});
