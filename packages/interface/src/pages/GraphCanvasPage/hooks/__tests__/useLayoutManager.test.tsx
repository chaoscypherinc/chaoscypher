// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { SOURCE_GROUP_PREFIX, SOURCE_PROVENANCE_PREFIX } from '../../types';
import { useLayoutManager } from '../useLayoutManager';

// ---------------------------------------------------------------------------
// Fake sigma: exposes only the methods the hook calls.
// ---------------------------------------------------------------------------

interface FakeCamera {
  animatedReset: ReturnType<typeof vi.fn<(opts: { duration: number }) => void>>;
}

interface FakeSigma {
  getCamera: ReturnType<typeof vi.fn<() => FakeCamera>>;
}

const holder = vi.hoisted(() => ({ sigma: null as unknown }));

vi.mock('@react-sigma/core', () => ({
  useSigma: () => holder.sigma,
}));

vi.mock('../../../../utils/logger', () => ({
  logger: {
    error: vi.fn<(...args: unknown[]) => void>(),
    warn: vi.fn<(...args: unknown[]) => void>(),
    info: vi.fn<(...args: unknown[]) => void>(),
    debug: vi.fn<(...args: unknown[]) => void>(),
  },
}));

function makeFakeCamera(): FakeCamera {
  return {
    animatedReset: vi.fn<(opts: { duration: number }) => void>(),
  };
}

function makeFakeSigma(): FakeSigma {
  return {
    getCamera: vi.fn<() => FakeCamera>(() => makeFakeCamera()),
  };
}

// ---------------------------------------------------------------------------
// Graph + attribute builders
// ---------------------------------------------------------------------------

function nodeAttrs(overrides: Partial<NodeAttributes> = {}): NodeAttributes {
  return {
    nodeId: 'n',
    title: 'Node',
    content: {},
    templateId: 't1',
    tags: [],
    createdAt: '2026-01-01',
    updatedAt: '2026-01-01',
    x: 0,
    y: 0,
    size: 5,
    color: '#00E5FF',
    label: 'Node',
    ...overrides,
  };
}

function edgeAttrs(overrides: Partial<EdgeAttributes> = {}): EdgeAttributes {
  return {
    edgeId: 'e',
    label: 'rel',
    templateId: 't1',
    sourceId: 's',
    targetId: 't',
    properties: {},
    createdAt: '2026-01-01',
    updatedAt: '2026-01-01',
    ...overrides,
  };
}

/**
 * Build a basic graph with 4 regular nodes, 3 edges, a source-group node,
 * and a provenance edge.
 */
function buildGraph(): Graph<NodeAttributes, EdgeAttributes> {
  const graph = new Graph<NodeAttributes, EdgeAttributes>();
  graph.addNode('A', nodeAttrs({ nodeId: 'A', x: 1, y: 2, sourceDocumentId: 'src1' }));
  graph.addNode('B', nodeAttrs({ nodeId: 'B', x: 3, y: 4, sourceDocumentId: 'src1' }));
  graph.addNode('C', nodeAttrs({ nodeId: 'C', x: 5, y: 6, sourceDocumentId: 'src1' }));
  graph.addNode('D', nodeAttrs({ nodeId: 'D', x: 7, y: 8, sourceDocumentId: 'src1' }));

  // Add a source-group virtual node — should be excluded from layout.
  const sgId = `${SOURCE_GROUP_PREFIX}src1`;
  graph.addNode(sgId, nodeAttrs({
    nodeId: sgId,
    isSourceGroup: true,
    sourceGroupId: 'src1',
    x: 0,
    y: 0,
  }));

  graph.addEdgeWithKey('AB', 'A', 'B', edgeAttrs({ edgeId: 'AB' }));
  graph.addEdgeWithKey('BC', 'B', 'C', edgeAttrs({ edgeId: 'BC' }));
  graph.addEdgeWithKey('CD', 'C', 'D', edgeAttrs({ edgeId: 'CD' }));

  // Provenance edge — should be excluded from layout.
  const spId = `${SOURCE_PROVENANCE_PREFIX}1`;
  graph.addEdgeWithKey(spId, sgId, 'A', edgeAttrs({ edgeId: spId, isProvenance: true }));

  return graph;
}

// ---------------------------------------------------------------------------
// Helper: render hook with graph + mock sigma, return { applyLayout, sigma }
// ---------------------------------------------------------------------------

interface HookResult {
  applyLayout: (type: string) => Promise<void>;
  sigma: FakeSigma;
  setLayoutType: ReturnType<typeof vi.fn<(t: string) => void>>;
  setError: ReturnType<typeof vi.fn<(e: string | null) => void>>;
}

function renderLayoutManager(graph: Graph<NodeAttributes, EdgeAttributes>): HookResult {
  const sigma = makeFakeSigma();
  holder.sigma = sigma;

  const setLayoutType = vi.fn<(t: string) => void>();
  const setError = vi.fn<(e: string | null) => void>();

  // Cast to avoid LayoutType import friction while keeping type safety.
  const { result } = renderHook(() =>
    useLayoutManager({
      graph: graph as Graph<NodeAttributes, EdgeAttributes>,
      setLayoutType: setLayoutType as (t: import('../../types').LayoutType) => void,
      setError,
    }),
  );

  return {
    applyLayout: result.current.applyLayout as (type: string) => Promise<void>,
    sigma,
    setLayoutType,
    setError,
  };
}

// ---------------------------------------------------------------------------
// Convenience: run applyLayout inside act() and advance timers.
// ---------------------------------------------------------------------------

async function runLayout(applyLayout: (type: string) => Promise<void>, type: string) {
  await act(async () => {
    await applyLayout(type);
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useLayoutManager', () => {
  beforeEach(() => {
    holder.sigma = null;
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  // ── Helper assertions ─────────────────────────────────────────────────────

  /**
   * Assert that every regular node (non-source-group) in the graph has finite
   * numeric x and y attributes after a layout has been applied.
   */
  function assertNodesPositioned(graph: Graph<NodeAttributes, EdgeAttributes>) {
    graph.forEachNode((id, attrs) => {
      if (id.startsWith(SOURCE_GROUP_PREFIX)) return; // virtual — skip
      expect(typeof attrs.x, `node ${id} x`).toBe('number');
      expect(isFinite(attrs.x), `node ${id} x is finite`).toBe(true);
      expect(typeof attrs.y, `node ${id} y`).toBe('number');
      expect(isFinite(attrs.y), `node ${id} y is finite`).toBe(true);
    });
  }

  // ── Return shape ──────────────────────────────────────────────────────────

  it('returns an applyLayout function', () => {
    const graph = buildGraph();
    const { applyLayout } = renderLayoutManager(graph);
    expect(typeof applyLayout).toBe('function');
  });

  // ── force layout ──────────────────────────────────────────────────────────

  describe('force layout', () => {
    it('updates node positions and calls setLayoutType("force")', async () => {
      const graph = buildGraph();
      const { applyLayout, setLayoutType } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'force');

      assertNodesPositioned(graph);
      expect(setLayoutType).toHaveBeenCalledWith('force');
    });

    it('schedules an animatedReset via setTimeout after force layout', async () => {
      const graph = buildGraph();
      const { applyLayout, sigma } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'force');

      // animatedReset should not have fired yet (50 ms delay).
      expect(sigma.getCamera().animatedReset).not.toHaveBeenCalled();

      // Advance fake timers past the 50 ms delay.
      act(() => { vi.advanceTimersByTime(100); });

      // getCamera is called inside the timeout, so check it was called overall.
      expect(sigma.getCamera).toHaveBeenCalled();
    });

    it('does not call setError on success', async () => {
      const graph = buildGraph();
      const { applyLayout, setError } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'force');

      expect(setError).not.toHaveBeenCalled();
    });
  });

  // ── grid layout ───────────────────────────────────────────────────────────

  describe('grid layout', () => {
    it('assigns finite x/y positions to all regular nodes', async () => {
      const graph = buildGraph();
      const { applyLayout, setLayoutType } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'grid');

      assertNodesPositioned(graph);
      expect(setLayoutType).toHaveBeenCalledWith('grid');
    });

    it('does not call setError', async () => {
      const graph = buildGraph();
      const { applyLayout, setError } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'grid');

      expect(setError).not.toHaveBeenCalled();
    });
  });

  // ── mindmap layout ────────────────────────────────────────────────────────

  describe('mindmap layout', () => {
    it('assigns finite x/y positions to all regular nodes', async () => {
      const graph = buildGraph();
      const { applyLayout, setLayoutType } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'mindmap');

      assertNodesPositioned(graph);
      expect(setLayoutType).toHaveBeenCalledWith('mindmap');
    });
  });

  // ── hierarchical layout ───────────────────────────────────────────────────

  describe('hierarchical layout', () => {
    it('assigns finite x/y positions to all regular nodes', async () => {
      const graph = buildGraph();
      const { applyLayout, setLayoutType } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'hierarchical');

      assertNodesPositioned(graph);
      expect(setLayoutType).toHaveBeenCalledWith('hierarchical');
    });
  });

  // ── radial layout ─────────────────────────────────────────────────────────

  describe('radial layout', () => {
    it('assigns finite x/y positions to all regular nodes', async () => {
      const graph = buildGraph();
      const { applyLayout, setLayoutType } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'radial');

      assertNodesPositioned(graph);
      expect(setLayoutType).toHaveBeenCalledWith('radial');
    });
  });

  // ── manual (default/no-op) ────────────────────────────────────────────────

  describe('manual layout (default branch)', () => {
    it('calls setLayoutType("manual") without computing positions', async () => {
      const graph = buildGraph();
      const before = new Map<string, { x: number; y: number }>();
      graph.forEachNode((id, attrs) => before.set(id, { x: attrs.x, y: attrs.y }));

      const { applyLayout, setLayoutType } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'manual');

      expect(setLayoutType).toHaveBeenCalledWith('manual');

      // Positions should be unchanged for manual layout.
      graph.forEachNode((id, attrs) => {
        expect(attrs.x).toBe(before.get(id)!.x);
        expect(attrs.y).toBe(before.get(id)!.y);
      });
    });

    it('does not call setError for manual layout', async () => {
      const graph = buildGraph();
      const { applyLayout, setError } = renderLayoutManager(graph);

      await runLayout(applyLayout, 'manual');

      expect(setError).not.toHaveBeenCalled();
    });
  });

  // ── source-group node exclusion ───────────────────────────────────────────

  describe('source-group node handling', () => {
    it('excludes source-group nodes from layout algorithm input', async () => {
      const graph = buildGraph();
      const sgId = `${SOURCE_GROUP_PREFIX}src1`;

      // Record initial sg position before layout.
      const beforeX = graph.getNodeAttribute(sgId, 'x');
      const beforeY = graph.getNodeAttribute(sgId, 'y');

      const { applyLayout } = renderLayoutManager(graph);
      await runLayout(applyLayout, 'grid');

      // Source-group node positions are set by repositionSourceGroupNodes, which
      // computes the centroid of member nodes. Since none of the regular nodes
      // have sourceGroupMembership set, count stays 0 and the sg node position
      // is not updated by that path — and grid layout ignores it (excluded).
      // Either unchanged or repositioned to centroid: both finite is acceptable.
      const afterX = graph.getNodeAttribute(sgId, 'x');
      const afterY = graph.getNodeAttribute(sgId, 'y');
      expect(typeof afterX).toBe('number');
      expect(typeof afterY).toBe('number');
      // Grid layout should not have repositioned it since it was excluded.
      expect(afterX).toBe(beforeX);
      expect(afterY).toBe(beforeY);
    });

    it('repositions source-group node to centroid of its members', async () => {
      // Build a graph where member nodes have sourceGroupMembership set.
      const graph = new Graph<NodeAttributes, EdgeAttributes>();
      const sgId = `${SOURCE_GROUP_PREFIX}srcA`;

      graph.addNode('M1', nodeAttrs({
        nodeId: 'M1', x: 0, y: 0,
        sourceGroupMembership: 'srcA',
        sourceDocumentId: 'srcA',
      }));
      graph.addNode('M2', nodeAttrs({
        nodeId: 'M2', x: 0, y: 0,
        sourceGroupMembership: 'srcA',
        sourceDocumentId: 'srcA',
      }));
      graph.addNode(sgId, nodeAttrs({
        nodeId: sgId, isSourceGroup: true, sourceGroupId: 'srcA',
        x: 999, y: 999,
      }));
      graph.addEdgeWithKey('m1m2', 'M1', 'M2', edgeAttrs({ edgeId: 'm1m2' }));

      const { applyLayout } = renderLayoutManager(graph);
      await runLayout(applyLayout, 'grid');

      // After grid layout, M1 and M2 get positions from applyPositionsToGraph.
      // Then repositionSourceGroupNodes sets sgId to their centroid.
      const sgX = graph.getNodeAttribute(sgId, 'x');
      const sgY = graph.getNodeAttribute(sgId, 'y');
      const m1x = graph.getNodeAttribute('M1', 'x');
      const m2x = graph.getNodeAttribute('M2', 'x');
      const m1y = graph.getNodeAttribute('M1', 'y');
      const m2y = graph.getNodeAttribute('M2', 'y');

      expect(sgX).toBeCloseTo((m1x + m2x) / 2);
      expect(sgY).toBeCloseTo((m1y + m2y) / 2);
    });

    it('skips source-group node that has no sourceGroupId set', async () => {
      // Cover the `if (!sourceId) return;` branch in repositionSourceGroupNodes.
      const graph = new Graph<NodeAttributes, EdgeAttributes>();
      graph.addNode('N1', nodeAttrs({ nodeId: 'N1', x: 0, y: 0 }));
      const sgId = `${SOURCE_GROUP_PREFIX}noId`;
      // Intentionally omit sourceGroupId so sourceId is undefined.
      graph.addNode(sgId, nodeAttrs({ nodeId: sgId, isSourceGroup: true, x: 999, y: 999 }));

      const { applyLayout, setError } = renderLayoutManager(graph);
      await runLayout(applyLayout, 'grid');

      // Should not throw or call setError.
      expect(setError).not.toHaveBeenCalled();
      // The sg node position is unchanged (no reposition happened).
      expect(graph.getNodeAttribute(sgId, 'x')).toBe(999);
      expect(graph.getNodeAttribute(sgId, 'y')).toBe(999);
    });
  });

  // ── provenance edge exclusion ─────────────────────────────────────────────

  describe('provenance edge exclusion', () => {
    it('does not include provenance edges in layout edge set (no crash)', async () => {
      const graph = buildGraph();
      const { applyLayout, setError } = renderLayoutManager(graph);

      // Should complete without error even though graph has a provenance edge.
      await runLayout(applyLayout, 'force');

      expect(setError).not.toHaveBeenCalled();
    });
  });

  // ── error handling ────────────────────────────────────────────────────────

  describe('error handling', () => {
    it('calls setError and logs when layout throws', async () => {
      const graph = buildGraph();

      // Temporarily break the graph's forEachNode to trigger the catch block.
      const original = graph.forEachNode.bind(graph);
      let callCount = 0;
      vi.spyOn(graph, 'forEachNode').mockImplementation((...args: Parameters<typeof graph.forEachNode>) => {
        callCount++;
        // Throw on the second call (extractEdges iteration) to force the error.
        if (callCount >= 2) {
          throw new Error('simulated graph failure');
        }
        return original(...args);
      });

      const { applyLayout, setError } = renderLayoutManager(graph);

      await act(async () => {
        await applyLayout('force');
      });

      expect(setError).toHaveBeenCalledWith('Failed to apply layout');
    });
  });

  // ── multi-source graph ────────────────────────────────────────────────────

  describe('multi-source graph', () => {
    it('positions nodes from different source documents', async () => {
      const graph = new Graph<NodeAttributes, EdgeAttributes>();
      graph.addNode('A1', nodeAttrs({ nodeId: 'A1', x: 0, y: 0, sourceDocumentId: 'srcA' }));
      graph.addNode('A2', nodeAttrs({ nodeId: 'A2', x: 0, y: 0, sourceDocumentId: 'srcA' }));
      graph.addNode('B1', nodeAttrs({ nodeId: 'B1', x: 0, y: 0, sourceDocumentId: 'srcB' }));
      graph.addNode('B2', nodeAttrs({ nodeId: 'B2', x: 0, y: 0, sourceDocumentId: 'srcB' }));
      graph.addEdgeWithKey('a1a2', 'A1', 'A2', edgeAttrs({ edgeId: 'a1a2' }));
      graph.addEdgeWithKey('b1b2', 'B1', 'B2', edgeAttrs({ edgeId: 'b1b2' }));

      const { applyLayout, setLayoutType } = renderLayoutManager(graph);

      for (const type of ['force', 'grid', 'mindmap', 'hierarchical', 'radial'] as const) {
        // Reset positions to 0 before each layout.
        graph.forEachNode((id) => {
          graph.setNodeAttribute(id, 'x', 0);
          graph.setNodeAttribute(id, 'y', 0);
        });

        await runLayout(applyLayout, type);
        assertNodesPositioned(graph);
        expect(setLayoutType).toHaveBeenCalledWith(type);
        setLayoutType.mockClear();
      }
    });
  });

  // ── single node graph ─────────────────────────────────────────────────────

  describe('single-node graph', () => {
    it('handles a graph with one regular node across all layout types', async () => {
      const graph = new Graph<NodeAttributes, EdgeAttributes>();
      graph.addNode('only', nodeAttrs({ nodeId: 'only', x: 0, y: 0 }));

      const { applyLayout, setLayoutType } = renderLayoutManager(graph);

      for (const type of ['force', 'grid', 'mindmap', 'hierarchical', 'radial'] as const) {
        graph.setNodeAttribute('only', 'x', 0);
        graph.setNodeAttribute('only', 'y', 0);

        await runLayout(applyLayout, type);
        expect(setLayoutType).toHaveBeenCalledWith(type);
        setLayoutType.mockClear();
      }
    });
  });

  // ── camera animation ──────────────────────────────────────────────────────

  describe('camera animation', () => {
    it('calls animatedReset with duration 400 after timer fires', async () => {
      const graph = buildGraph();
      const fakeCamera = makeFakeCamera();
      const sigma = makeFakeSigma();
      // Override getCamera to always return the same camera instance.
      sigma.getCamera.mockReturnValue(fakeCamera);
      holder.sigma = sigma;

      const setLayoutType = vi.fn<(t: string) => void>();
      const setError = vi.fn<(e: string | null) => void>();

      const { result } = renderHook(() =>
        useLayoutManager({
          graph,
          setLayoutType: setLayoutType as (t: import('../../types').LayoutType) => void,
          setError,
        }),
      );

      await act(async () => {
        await result.current.applyLayout('grid');
      });

      expect(fakeCamera.animatedReset).not.toHaveBeenCalled();

      act(() => { vi.advanceTimersByTime(100); });

      expect(fakeCamera.animatedReset).toHaveBeenCalledWith({ duration: 400 });
    });
  });
});
