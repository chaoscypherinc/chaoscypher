// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { SOURCE_GROUP_PREFIX } from '../../types';
import {
  UNRESOLVED_REGION_STORAGE_KEY,
  boundsOf,
  groupUnresolvedNodes,
  layoutUnresolvedRegion,
  readUnresolvedRegionExpanded,
  unresolvedMemberIds,
  unresolvedSummaryLabel,
  writeUnresolvedRegionExpanded,
} from '../unresolvedRegion';

const NODES = [
  { id: 'plain', properties: { description: 'named and connected' } },
  { id: 'orphan', properties: { orphan: true } },
  { id: 'stringy', properties: { orphan: 'true' } },
  { id: 'both', properties: { descriptor_reference: true, orphan: true } },
  { id: 'none', properties: undefined },
  { id: 'ref', properties: { descriptor_reference: true } },
];

describe('groupUnresolvedNodes', () => {
  it('puts every node carrying a pipeline flag in the region, in input order', () => {
    const region = groupUnresolvedNodes(NODES, true);
    expect(region.memberIds).toEqual(['orphan', 'both', 'ref']);
    expect(region.count).toBe(3);
    expect(region.flagsById.get('both')?.map((f) => f.label)).toEqual(['Unconnected', 'Reference']);
    expect(region.flagsById.has('stringy')).toBe(false);
  });

  it('expanded: hides nothing and has no summary node', () => {
    const region = groupUnresolvedNodes(NODES, true);
    expect(region.expanded).toBe(true);
    expect(region.hiddenIds.size).toBe(0);
    expect(region.summaryLabel).toBeNull();
  });

  it('collapsed: hides every member behind one "Unresolved (N)" summary', () => {
    const region = groupUnresolvedNodes(NODES, false);
    expect(region.expanded).toBe(false);
    expect([...region.hiddenIds]).toEqual(['orphan', 'both', 'ref']);
    expect(region.summaryLabel).toBe('Unresolved (3)');
  });

  it('an empty region has no summary even when collapsed', () => {
    const region = groupUnresolvedNodes([{ id: 'a', properties: {} }], false);
    expect(region.count).toBe(0);
    expect(region.hiddenIds.size).toBe(0);
    expect(region.summaryLabel).toBeNull();
  });

  it('labels the summary with the member count', () => {
    expect(unresolvedSummaryLabel(12)).toBe('Unresolved (12)');
  });
});

function attrs(content: Record<string, unknown>): NodeAttributes {
  return {
    nodeId: 'n', title: 'n', content, templateId: 't', tags: [], createdAt: '', updatedAt: '',
    x: 0, y: 0, size: 5, color: '#00E5FF', label: 'n',
  };
}

describe('unresolvedMemberIds', () => {
  it('reads flags from the canvas node content and skips virtual source-group nodes', () => {
    const graph = new Graph<NodeAttributes, EdgeAttributes>();
    graph.addNode('a', attrs({ orphan: true }));
    graph.addNode('b', attrs({}));
    graph.addNode(`${SOURCE_GROUP_PREFIX}s1`, attrs({ orphan: true }));
    expect(unresolvedMemberIds(graph)).toEqual(['a']);
  });
});

describe('layoutUnresolvedRegion', () => {
  it('places members in a square grid to the right of the rest of the graph', () => {
    const others = [{ x: 0, y: 0 }, { x: 100, y: 200 }];
    const positions = layoutUnresolvedRegion(['a', 'b', 'c', 'd', 'e'], others, 10);
    expect(positions.size).toBe(5);
    const pts = [...positions.values()];
    // Clear of the main graph's right edge.
    for (const p of pts) expect(p.x).toBeGreaterThan(100);
    // 5 members -> 3 columns, 2 rows.
    expect(new Set(pts.map((p) => p.x)).size).toBe(3);
    expect(new Set(pts.map((p) => p.y)).size).toBe(2);
    // Vertically centred on the main graph (centre y = 100).
    const ys = pts.map((p) => p.y);
    expect((Math.min(...ys) + Math.max(...ys)) / 2).toBeCloseTo(100);
  });

  it('centres the grid on the origin when nothing else is on the canvas', () => {
    const positions = layoutUnresolvedRegion(['a', 'b', 'c', 'd'], [], 10);
    const b = boundsOf(positions.values());
    expect(b).toEqual({ minX: -5, minY: -5, maxX: 5, maxY: 5 });
  });

  it('returns nothing for an empty region', () => {
    expect(layoutUnresolvedRegion([], [{ x: 1, y: 1 }]).size).toBe(0);
  });
});

describe('boundsOf', () => {
  it('grows the box by each point radius plus padding', () => {
    expect(boundsOf([{ x: 0, y: 0, r: 2 }, { x: 10, y: 5 }], 1)).toEqual({
      minX: -3, minY: -3, maxX: 11, maxY: 6,
    });
  });

  it('is null for no points', () => {
    expect(boundsOf([])).toBeNull();
  });
});

describe('region state persistence', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('defaults to expanded when nothing is stored or the value is unknown', () => {
    expect(readUnresolvedRegionExpanded()).toBe(true);
    localStorage.setItem(UNRESOLVED_REGION_STORAGE_KEY, 'garbage');
    expect(readUnresolvedRegionExpanded()).toBe(true);
  });

  it('round-trips the collapsed and expanded states', () => {
    writeUnresolvedRegionExpanded(false);
    expect(localStorage.getItem(UNRESOLVED_REGION_STORAGE_KEY)).toBe('collapsed');
    expect(readUnresolvedRegionExpanded()).toBe(false);
    writeUnresolvedRegionExpanded(true);
    expect(readUnresolvedRegionExpanded()).toBe(true);
  });

  it('falls back to expanded and never throws when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readUnresolvedRegionExpanded()).toBe(true);
    expect(() => writeUnresolvedRegionExpanded(false)).not.toThrow();
  });
});
