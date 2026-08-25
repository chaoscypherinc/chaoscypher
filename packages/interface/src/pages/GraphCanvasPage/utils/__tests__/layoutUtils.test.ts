// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import Graph from 'graphology';
import {
  applyForceLayout,
  applyGridLayout,
  applyMindmapLayout,
  applyHierarchicalLayout,
  applyRadialLayout,
  applyPositionsToGraph,
  type LayoutNode,
  type LayoutEdge,
} from '../layoutUtils';

// ── Test helpers ─────────────────────────────────────────────────────

type PositionMap = Map<string, { x: number; y: number }>;

/**
 * Build a list of synthetic LayoutNodes with sequential ids n0..n{count-1}.
 */
function makeNodes(
  count: number,
  overrides: (i: number) => Partial<LayoutNode> = () => ({}),
): LayoutNode[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `n${i}`,
    x: 0,
    y: 0,
    ...overrides(i),
  }));
}

/**
 * Assert that every node id has a finite numeric x/y in the position map.
 */
function expectAllFinite(positions: PositionMap, nodes: LayoutNode[]): void {
  expect(positions.size).toBe(nodes.length);
  for (const node of nodes) {
    const pos = positions.get(node.id);
    expect(pos).toBeDefined();
    expect(Number.isFinite(pos!.x)).toBe(true);
    expect(Number.isFinite(pos!.y)).toBe(true);
  }
}

const dist = (a: { x: number; y: number }, b: { x: number; y: number }): number =>
  Math.hypot(a.x - b.x, a.y - b.y);

/** Mean position of the given node ids. */
function centroid(ids: string[], positions: PositionMap): { x: number; y: number } {
  const pts = ids.map(id => positions.get(id)!);
  return {
    x: pts.reduce((acc, p) => acc + p.x, 0) / pts.length,
    y: pts.reduce((acc, p) => acc + p.y, 0) / pts.length,
  };
}

/** Distance from the furthest member of `ids` to `center`. */
function spread(ids: string[], positions: PositionMap, center: { x: number; y: number }): number {
  return Math.max(...ids.map(id => dist(positions.get(id)!, center)));
}

/** Smallest distance between any two distinct positions. */
function minPairwiseDistance(positions: PositionMap): number {
  const pts = [...positions.values()];
  let min = Infinity;
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      min = Math.min(min, dist(pts[i], pts[j]));
    }
  }
  return min;
}

/**
 * Run `fn` with Math.random replaced by a seeded LCG.
 *
 * The force-directed layouts jitter their initial positions with
 * Math.random, so two runs of the same input are not comparable. Seeding
 * makes the geometric assertions below deterministic, and lets two runs
 * differ by exactly one input (e.g. node size).
 */
function withSeededRandom<T>(fn: () => T, seed = 42): T {
  const original = Math.random;
  let state = seed;
  Math.random = (): number => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
  try {
    return fn();
  } finally {
    Math.random = original;
  }
}

// ── applyGridLayout ──────────────────────────────────────────────────

describe('applyGridLayout', () => {
  it('returns an empty map for no nodes', () => {
    expect(applyGridLayout([]).size).toBe(0);
  });

  it('places a single node at the origin', () => {
    const nodes = makeNodes(1);
    const positions = applyGridLayout(nodes);
    expectAllFinite(positions, nodes);
    expect(positions.get('n0')).toEqual({ x: 0, y: 0 });
  });

  it('lays out nodes in a square grid with 30px cell spacing', () => {
    const nodes = makeNodes(9); // cols = ceil(sqrt(9)) = 3
    const positions = applyGridLayout(nodes);
    expectAllFinite(positions, nodes);

    const cols = 3;
    nodes.forEach((node, index) => {
      const pos = positions.get(node.id)!;
      expect(pos.x).toBe((index % cols) * 30);
      expect(pos.y).toBe(Math.floor(index / cols) * 30);
    });
    // Spot-check the wrap: index 3 starts the second row.
    expect(positions.get('n3')).toEqual({ x: 0, y: 30 });
    expect(positions.get('n8')).toEqual({ x: 60, y: 60 });
  });

  it('computes column count as ceil(sqrt(n)) for non-perfect-square counts', () => {
    const nodes = makeNodes(5); // cols = ceil(sqrt(5)) = 3
    const positions = applyGridLayout(nodes);
    const cols = 3;
    expect(positions.get('n0')).toEqual({ x: 0, y: 0 });
    expect(positions.get('n2')).toEqual({ x: 60, y: 0 });
    // index 3 wraps to next row
    expect(positions.get('n3')).toEqual({ x: 0, y: 30 });
    expect(positions.get('n4')).toEqual({ x: (4 % cols) * 30, y: Math.floor(4 / cols) * 30 });
  });
});

// ── applyRadialLayout ────────────────────────────────────────────────

describe('applyRadialLayout', () => {
  it('returns an empty map for no nodes', () => {
    expect(applyRadialLayout([], []).size).toBe(0);
  });

  it('places a single node at the center', () => {
    const nodes = makeNodes(1);
    const positions = applyRadialLayout(nodes, []);
    expectAllFinite(positions, nodes);
    // Single source -> center at origin; level 0 root sits at center.
    expect(positions.get('n0')).toEqual({ x: 0, y: 0 });
  });

  it('places ring nodes equidistant from the center hub', () => {
    // Star: n0 connected to n1..n4. n0 is most-connected -> center.
    const nodes = makeNodes(5);
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      { id: 'e2', source: 'n0', target: 'n2' },
      { id: 'e3', source: 'n0', target: 'n3' },
      { id: 'e4', source: 'n0', target: 'n4' },
    ];
    const positions = applyRadialLayout(nodes, edges);
    expectAllFinite(positions, nodes);

    const center = positions.get('n0')!;
    expect(center).toEqual({ x: 0, y: 0 });

    // All four ring nodes sit at level 1 -> identical radius from center.
    const radii = ['n1', 'n2', 'n3', 'n4'].map(id => dist(positions.get(id)!, center));
    const first = radii[0];
    expect(first).toBeGreaterThan(0);
    for (const r of radii) {
      expect(r).toBeCloseTo(first, 6);
    }
  });

  it('assigns disconnected nodes to an outer ring', () => {
    // n0-n1 connected; n2 disconnected -> pushed to maxLevel+1 ring.
    const nodes = makeNodes(3);
    const edges: LayoutEdge[] = [{ id: 'e1', source: 'n0', target: 'n1' }];
    const positions = applyRadialLayout(nodes, edges);
    expectAllFinite(positions, nodes);
    const center = positions.get('n0')!;
    // Disconnected node still gets a finite position beyond the center.
    expect(dist(positions.get('n2')!, center)).toBeGreaterThan(0);
  });
});

// ── applyHierarchicalLayout ──────────────────────────────────────────

describe('applyHierarchicalLayout', () => {
  it('returns an empty map for no nodes', () => {
    expect(applyHierarchicalLayout([], []).size).toBe(0);
  });

  it('places a single node with finite coordinates', () => {
    const nodes = makeNodes(1);
    const positions = applyHierarchicalLayout(nodes, []);
    expectAllFinite(positions, nodes);
  });

  it('stacks BFS levels top-to-bottom (deeper levels have larger y)', () => {
    // Chain: n0 - n1 - n2 - n3. n0/n3 endpoints; most-connected is interior.
    // Build a clear hub so the root is predictable: n0 connected to n1,n2;
    // n2 connected to n3.
    const nodes = makeNodes(4);
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      { id: 'e2', source: 'n0', target: 'n2' },
      { id: 'e3', source: 'n2', target: 'n3' },
    ];
    const positions = applyHierarchicalLayout(nodes, edges);
    expectAllFinite(positions, nodes);

    // n0 is most-connected (degree 2 vs n2's degree 2 -> ties keep first).
    // Root is level 0 (smallest y), its neighbours level 1, n3 level 2.
    const yRoot = positions.get('n0')!.y;
    const yLevel1 = Math.min(positions.get('n1')!.y, positions.get('n2')!.y);
    const yLevel2 = positions.get('n3')!.y;
    expect(yLevel1).toBeGreaterThan(yRoot);
    expect(yLevel2).toBeGreaterThan(yLevel1);
  });

  it('handles cyclic graphs without infinite recursion', () => {
    // Triangle cycle.
    const nodes = makeNodes(3);
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      { id: 'e2', source: 'n1', target: 'n2' },
      { id: 'e3', source: 'n2', target: 'n0' },
    ];
    const positions = applyHierarchicalLayout(nodes, edges);
    expectAllFinite(positions, nodes);
  });

  it('positions disconnected nodes on a trailing level', () => {
    const nodes = makeNodes(4);
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      // n2, n3 disconnected -> dumped on maxReached+1 level.
    ];
    const positions = applyHierarchicalLayout(nodes, edges);
    expectAllFinite(positions, nodes);

    const yConnected = ['n0', 'n1'].map(id => positions.get(id)!.y);
    const yDisconnected = ['n2', 'n3'].map(id => positions.get(id)!.y);

    // The BFS-unreachable pair lands on maxReached+1, strictly BELOW every
    // node the traversal did reach — that is what "trailing level" means.
    expect(Math.min(...yDisconnected)).toBeGreaterThan(Math.max(...yConnected));
    // Both share that one trailing level rather than being scattered.
    expect(yDisconnected[0]).toBe(yDisconnected[1]);
    // ...and the reachable pair is itself tiered: root above its child.
    expect(positions.get('n1')!.y).toBeGreaterThan(positions.get('n0')!.y);
  });
});

// ── applyMindmapLayout ───────────────────────────────────────────────

describe('applyMindmapLayout', () => {
  it('returns an empty map for no nodes', () => {
    expect(applyMindmapLayout([], []).size).toBe(0);
  });

  it('places a single node with finite coordinates', () => {
    const nodes = makeNodes(1);
    const positions = applyMindmapLayout(nodes, []);
    expectAllFinite(positions, nodes);
  });

  it('lays out template-grouped nodes with finite coordinates', () => {
    // Two template clusters within a single source.
    const nodes = makeNodes(6, i => ({ templateId: i < 3 ? 'tplA' : 'tplB' }));
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      { id: 'e2', source: 'n2', target: 'n3' }, // cross-template edge
      { id: 'e3', source: 'n4', target: 'n5' },
    ];
    const positions = withSeededRandom(() => applyMindmapLayout(nodes, edges));
    expectAllFinite(positions, nodes);

    // The two template clusters are disjoint groups — same predicate as
    // 'separates a multi-source graph into distinct regions'. Measured on
    // this seeded fixture: separation ~84.7 vs spreads ~9.3 / ~9.3.
    const tplA = ['n0', 'n1', 'n2'];
    const tplB = ['n3', 'n4', 'n5'];
    const centerA = centroid(tplA, positions);
    const centerB = centroid(tplB, positions);
    for (const id of tplA) {
      expect(dist(positions.get(id)!, centerA)).toBeLessThan(dist(positions.get(id)!, centerB));
    }
    for (const id of tplB) {
      expect(dist(positions.get(id)!, centerB)).toBeLessThan(dist(positions.get(id)!, centerA));
    }
    expect(dist(centerA, centerB)).toBeGreaterThan(
      spread(tplA, positions, centerA) + spread(tplB, positions, centerB),
    );
  });

  it('handles nodes lacking templateId (falls back to default cluster)', () => {
    const nodes = makeNodes(4); // no templateId
    const edges: LayoutEdge[] = [{ id: 'e1', source: 'n0', target: 'n1' }];
    const positions = applyMindmapLayout(nodes, edges);
    expectAllFinite(positions, nodes);

    // All four land in ONE 'default' cluster: a deterministic spiral
    // around the first member (spacing = gap * 0.7 = 7.7, angle = 2.4 * i,
    // r = spacing * sqrt(i)) — independent of Math.random, since only the
    // cluster centre is randomized. Splitting the nodes into per-node
    // clusters, or breaking the spiral, changes these offsets.
    const origin = positions.get('n0')!;
    const spacing = 7.7;
    for (let i = 1; i < 4; i++) {
      const pos = positions.get(`n${i}`)!;
      expect(pos.x - origin.x).toBeCloseTo(spacing * Math.sqrt(i) * Math.cos(2.4 * i), 5);
      expect(pos.y - origin.y).toBeCloseTo(spacing * Math.sqrt(i) * Math.sin(2.4 * i), 5);
    }
  });

  it('separates multiple sources and lays out their template clusters', () => {
    const nodes = makeNodes(8, i => ({
      sourceId: i < 4 ? 'srcA' : 'srcB',
      templateId: i % 2 === 0 ? 'tplA' : 'tplB',
    }));
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      { id: 'e2', source: 'n2', target: 'n3' },
      { id: 'e3', source: 'n0', target: 'n4' }, // cross-source edge
      { id: 'e4', source: 'n4', target: 'n5' },
      { id: 'e5', source: 'n6', target: 'n7' },
    ];
    const positions = withSeededRandom(() => applyMindmapLayout(nodes, edges));
    expectAllFinite(positions, nodes);

    const srcA = ['n0', 'n1', 'n2', 'n3'];
    const srcB = ['n4', 'n5', 'n6', 'n7'];
    const centerA = centroid(srcA, positions);
    const centerB = centroid(srcB, positions);

    // Each source occupies its own region: every node is nearer its own
    // source's centre than the other's. Collapsing the layout (or dropping
    // the source grouping) makes these ties/inversions.
    for (const id of srcA) {
      expect(dist(positions.get(id)!, centerA)).toBeLessThan(dist(positions.get(id)!, centerB));
    }
    for (const id of srcB) {
      expect(dist(positions.get(id)!, centerB)).toBeLessThan(dist(positions.get(id)!, centerA));
    }
    // The two regions are disjoint, not merely off-centre from each other.
    expect(dist(centerA, centerB)).toBeGreaterThan(
      spread(srcA, positions, centerA) + spread(srcB, positions, centerB),
    );

    // Level 2: within srcA the two template clusters are themselves disjoint
    // groups — same predicate as the source level above, not merely
    // "separated by something greater than zero" (which float noise passes).
    // Measured on this fixture: separation 84.7 vs spreads 3.9 / 3.9.
    const tplA = ['n0', 'n2'];
    const tplB = ['n1', 'n3'];
    const tplCenterA = centroid(tplA, positions);
    const tplCenterB = centroid(tplB, positions);
    expect(dist(tplCenterA, tplCenterB)).toBeGreaterThan(
      spread(tplA, positions, tplCenterA) + spread(tplB, positions, tplCenterB),
    );
  });
});

// ── applyForceLayout ─────────────────────────────────────────────────

describe('applyForceLayout', () => {
  it('returns an empty map for no nodes', () => {
    expect(applyForceLayout([], []).size).toBe(0);
  });

  it('places a single node with finite coordinates', () => {
    const nodes = makeNodes(1);
    const positions = applyForceLayout(nodes, []);
    expectAllFinite(positions, nodes);
  });

  it('assigns finite coordinates to all nodes in a connected graph', () => {
    const nodes = makeNodes(10);
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      { id: 'e2', source: 'n1', target: 'n2' },
      { id: 'e3', source: 'n2', target: 'n3' },
      { id: 'e4', source: 'n3', target: 'n4' },
      { id: 'e5', source: 'n0', target: 'n5' },
      { id: 'e6', source: 'n5', target: 'n6' },
      { id: 'e7', source: 'n6', target: 'n7' },
      { id: 'e8', source: 'n7', target: 'n8' },
      { id: 'e9', source: 'n8', target: 'n9' },
    ];
    const positions = applyForceLayout(nodes, edges);
    expectAllFinite(positions, nodes);
  });

  it('handles nodes with no edges', () => {
    const nodes = makeNodes(5);
    const positions = applyForceLayout(nodes, []);
    expectAllFinite(positions, nodes);
  });

  it('respects custom node size and filters dangling edges', () => {
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      // edge referencing a non-existent node is filtered out, not a crash.
      { id: 'e2', source: 'n0', target: 'missing' },
    ];
    // Same seed for both runs, so node size is the ONLY difference.
    const smallNodes = makeNodes(4, () => ({ size: 4 }));
    const largeNodes = makeNodes(4, () => ({ size: 40 }));
    const small = withSeededRandom(() => applyForceLayout(smallNodes, edges));
    const large = withSeededRandom(() => applyForceLayout(largeNodes, edges));

    expectAllFinite(small, smallNodes);
    expectAllFinite(large, largeNodes);

    // The dangling edge contributes no phantom node and no crash.
    expect(small.has('missing')).toBe(false);
    expect(small.size).toBe(4);

    // Collision radius is nodeSize + 2, so bigger nodes must end up
    // further apart. A layout that ignores `size` leaves these equal.
    expect(minPairwiseDistance(large)).toBeGreaterThan(minPairwiseDistance(small));
  });

  it('separates a multi-source graph into distinct regions', () => {
    const nodes = makeNodes(6, i => ({ sourceId: i < 3 ? 'srcA' : 'srcB' }));
    const edges: LayoutEdge[] = [
      { id: 'e1', source: 'n0', target: 'n1' },
      { id: 'e2', source: 'n1', target: 'n2' },
      { id: 'e3', source: 'n0', target: 'n3' }, // cross-source edge
      { id: 'e4', source: 'n3', target: 'n4' },
      { id: 'e5', source: 'n4', target: 'n5' },
    ];
    const positions = withSeededRandom(() => applyForceLayout(nodes, edges));
    expectAllFinite(positions, nodes);

    const srcA = ['n0', 'n1', 'n2'];
    const srcB = ['n3', 'n4', 'n5'];
    const centerA = centroid(srcA, positions);
    const centerB = centroid(srcB, positions);

    // Every node belongs to its own region...
    for (const id of srcA) {
      expect(dist(positions.get(id)!, centerA)).toBeLessThan(dist(positions.get(id)!, centerB));
    }
    for (const id of srcB) {
      expect(dist(positions.get(id)!, centerB)).toBeLessThan(dist(positions.get(id)!, centerA));
    }
    // ...and the two regions do not overlap. Collapsing every node to the
    // origin — which the old `expectAllFinite`-only body accepted — makes
    // both centres identical and fails here.
    expect(dist(centerA, centerB)).toBeGreaterThan(
      spread(srcA, positions, centerA) + spread(srcB, positions, centerB),
    );
  });

  it('handles a large graph via the isLarge fast path', () => {
    // >= 500 nodes triggers the large-graph parameter branch, which
    // tightens link distance (22 vs 40) and weakens charge — a *compact*
    // layout.
    const nodes = makeNodes(510);
    const edges: LayoutEdge[] = Array.from({ length: 509 }, (_, i) => ({
      id: `e${i}`,
      source: `n${i}`,
      target: `n${i + 1}`,
    }));
    const positions = withSeededRandom(() => applyForceLayout(nodes, edges));
    expectAllFinite(positions, nodes);

    // Mean edge length tracks the isLarge linkDist. Measured on this
    // seeded fixture: ~65 with the large-graph parameters, ~98 with the
    // small-graph parameters — removing the >=500 branch fails both
    // bounds below.
    const meanEdge =
      edges.reduce(
        (acc, e) => acc + dist(positions.get(e.source)!, positions.get(e.target)!),
        0,
      ) / edges.length;
    expect(meanEdge).toBeLessThan(80);

    // The compact tuning also bounds the overall extent: bbox diagonal is
    // ~994 seeded (vs ~2570 without the branch).
    const xs = [...positions.values()].map(p => p.x);
    const ys = [...positions.values()].map(p => p.y);
    expect(
      Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)),
    ).toBeLessThan(1700);
  });
});

// ── applyPositionsToGraph ────────────────────────────────────────────

describe('applyPositionsToGraph', () => {
  it('writes x/y attributes onto every matching graph node', () => {
    const graph = new Graph();
    graph.addNode('n0');
    graph.addNode('n1');

    const positions: PositionMap = new Map([
      ['n0', { x: 12, y: 34 }],
      ['n1', { x: -5, y: 7 }],
    ]);

    applyPositionsToGraph(graph, positions);

    expect(graph.getNodeAttribute('n0', 'x')).toBe(12);
    expect(graph.getNodeAttribute('n0', 'y')).toBe(34);
    expect(graph.getNodeAttribute('n1', 'x')).toBe(-5);
    expect(graph.getNodeAttribute('n1', 'y')).toBe(7);
  });

  it('skips positions for nodes that are absent from the graph', () => {
    const graph = new Graph();
    graph.addNode('present');

    const positions: PositionMap = new Map([
      ['present', { x: 1, y: 2 }],
      ['missing', { x: 9, y: 9 }],
    ]);

    // Must not throw on the missing-node branch.
    expect(() => applyPositionsToGraph(graph, positions)).not.toThrow();
    expect(graph.getNodeAttribute('present', 'x')).toBe(1);
    expect(graph.getNodeAttribute('present', 'y')).toBe(2);
    expect(graph.hasNode('missing')).toBe(false);
  });

  it('is a no-op for an empty position map', () => {
    const graph = new Graph();
    graph.addNode('n0');
    applyPositionsToGraph(graph, new Map());
    expect(graph.hasNodeAttribute('n0', 'x')).toBe(false);
  });

  it('integrates with a layout output end-to-end', () => {
    const nodes = makeNodes(4);
    const positions = applyGridLayout(nodes);

    const graph = new Graph();
    for (const n of nodes) graph.addNode(n.id);

    applyPositionsToGraph(graph, positions);

    for (const n of nodes) {
      const expected = positions.get(n.id)!;
      expect(graph.getNodeAttribute(n.id, 'x')).toBe(expected.x);
      expect(graph.getNodeAttribute(n.id, 'y')).toBe(expected.y);
    }
  });
});
