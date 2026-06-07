// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import Graph from 'graphology';
import type { NodeAttributes, EdgeAttributes } from '../../types';
import { SOURCE_GROUP_PREFIX, SOURCE_PROVENANCE_PREFIX } from '../../types';
import { useGraphReducers } from '../useGraphReducers';

// ---------------------------------------------------------------------------
// Fake sigma: capture the reducer functions registered via setSetting.
// ---------------------------------------------------------------------------

type NodeReducer = (node: string, data: NodeAttributes) => NodeAttributes & {
  highlighted?: boolean;
  hidden?: boolean;
  zIndex?: number;
  borderColor?: string;
  borderSize?: number;
  targetColor?: string;
};

type EdgeReducer = (edge: string, data: EdgeAttributes) => EdgeAttributes & {
  hidden?: boolean;
  zIndex?: number;
  forceLabel?: boolean;
  targetColor?: string;
  type?: string;
  label?: string;
};

interface FakeSigma {
  setSetting: ReturnType<typeof vi.fn<(key: string, value: unknown) => void>>;
  getGraph: () => Graph<NodeAttributes, EdgeAttributes>;
}

// vi.hoisted so the mock factory (also hoisted) can close over a mutable holder
// that each test reassigns before rendering the hook.
const holder = vi.hoisted(() => ({ sigma: null as unknown }));

vi.mock('@react-sigma/core', () => ({
  useSigma: () => holder.sigma,
}));

function makeFakeSigma(graph: Graph<NodeAttributes, EdgeAttributes>): FakeSigma {
  return {
    setSetting: vi.fn<(key: string, value: unknown) => void>(),
    getGraph: () => graph,
  };
}

/** Pull the registered reducers out of the setSetting mock's calls. */
function captureReducers(sigma: FakeSigma): { nodeReducer: NodeReducer; edgeReducer: EdgeReducer } {
  let nodeReducer: NodeReducer | undefined;
  let edgeReducer: EdgeReducer | undefined;
  for (const [key, value] of sigma.setSetting.mock.calls) {
    if (key === 'nodeReducer') nodeReducer = value as NodeReducer;
    if (key === 'edgeReducer') edgeReducer = value as EdgeReducer;
  }
  if (!nodeReducer || !edgeReducer) {
    throw new Error('reducers were not registered');
  }
  return { nodeReducer, edgeReducer };
}

// ---------------------------------------------------------------------------
// Graph + attribute builders.
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
 * Build a graph with:
 *  - hub 'A' connected to B, C, D (degree 3 -> above CONNECTION_THRESHOLD)
 *  - 'B' connected only to A (degree 1 -> below threshold, gets desaturated)
 *  - 'C' connected to A and D
 *  - 'D' connected to A and C
 *  - a source-group node 'sg:src1'
 *  - a normal edge A->B, A->C, C->D
 *  - a provenance edge 'sp:1' from sg:src1 -> B
 */
function buildGraph(): Graph<NodeAttributes, EdgeAttributes> {
  const graph = new Graph<NodeAttributes, EdgeAttributes>();
  graph.addNode('A', nodeAttrs({ nodeId: 'A', color: '#FF0080' }));
  graph.addNode('B', nodeAttrs({ nodeId: 'B', color: '#00BFA5' }));
  graph.addNode('C', nodeAttrs({ nodeId: 'C', color: '#BF00FF' }));
  graph.addNode('D', nodeAttrs({ nodeId: 'D', color: '#1DE9B6' }));
  graph.addNode(`${SOURCE_GROUP_PREFIX}src1`, nodeAttrs({
    nodeId: `${SOURCE_GROUP_PREFIX}src1`,
    color: '#7C4DFF',
    isSourceGroup: true,
  }));

  graph.addEdgeWithKey('AB', 'A', 'B', edgeAttrs({ edgeId: 'AB' }));
  graph.addEdgeWithKey('AC', 'A', 'C', edgeAttrs({ edgeId: 'AC' }));
  graph.addEdgeWithKey('CD', 'C', 'D', edgeAttrs({ edgeId: 'CD' }));
  graph.addEdgeWithKey('AD', 'A', 'D', edgeAttrs({ edgeId: 'AD' }));
  // Provenance edge from the source-group node to B.
  graph.addEdgeWithKey(`${SOURCE_PROVENANCE_PREFIX}1`, `${SOURCE_GROUP_PREFIX}src1`, 'B', edgeAttrs({
    edgeId: `${SOURCE_PROVENANCE_PREFIX}1`,
    isProvenance: true,
  }));
  return graph;
}

interface RenderProps {
  selectedNodeId?: string | null;
  selectedEdgeId?: string | null;
  highlightedNodeIds?: Set<string>;
  hiddenNodeIds?: Set<string>;
  hasActiveSearch?: boolean;
  hoveredNode?: string | null;
  hoveredNeighbors?: Set<string>;
  iconVisibleBySize?: Map<number, boolean>;
  collapsedSourceIds?: Set<string>;
}

function renderReducers(graph: Graph<NodeAttributes, EdgeAttributes>, props: RenderProps = {}) {
  const sigma = makeFakeSigma(graph);
  holder.sigma = sigma;

  const hoveredNeighborsRef: React.RefObject<Set<string>> = {
    current: props.hoveredNeighbors ?? new Set<string>(),
  };
  const iconVisibleBySizeRef: React.RefObject<Map<number, boolean>> = {
    current: props.iconVisibleBySize ?? new Map<number, boolean>(),
  };

  const view = renderHook(
    (p: RenderProps) =>
      useGraphReducers({
        graph,
        selectedNodeId: p.selectedNodeId ?? null,
        selectedEdgeId: p.selectedEdgeId ?? null,
        highlightedNodeIds: p.highlightedNodeIds ?? new Set<string>(),
        hiddenNodeIds: p.hiddenNodeIds ?? new Set<string>(),
        hasActiveSearch: p.hasActiveSearch ?? false,
        hoveredNode: p.hoveredNode ?? null,
        hoveredNeighborsRef,
        iconVisibleBySizeRef,
        collapsedSourceIds: p.collapsedSourceIds,
      }),
    { initialProps: props },
  );

  const { nodeReducer, edgeReducer } = captureReducers(sigma);
  return { sigma, view, nodeReducer, edgeReducer };
}

const PROVENANCE_EDGE = `${SOURCE_PROVENANCE_PREFIX}1`;
const SOURCE_GROUP_NODE = `${SOURCE_GROUP_PREFIX}src1`;

// ---------------------------------------------------------------------------

describe('useGraphReducers', () => {
  beforeEach(() => {
    holder.sigma = null;
  });

  it('registers both nodeReducer and edgeReducer on the sigma instance', () => {
    const graph = buildGraph();
    const { sigma } = renderReducers(graph);
    const keys = sigma.setSetting.mock.calls.map(([k]) => k);
    expect(keys).toContain('nodeReducer');
    expect(keys).toContain('edgeReducer');
  });

  describe('nodeReducer', () => {
    it('applies a default glow border and keeps full color for well-connected nodes', () => {
      const graph = buildGraph();
      const { nodeReducer } = renderReducers(graph);
      const res = nodeReducer('A', graph.getNodeAttributes('A'));
      // Degree 3 (>= threshold) so color is unchanged, border matches color.
      expect(res.color).toBe('#FF0080');
      expect(res.borderColor).toBe('#FF0080');
      expect(res.borderSize).toBe(0.4);
      expect(res.hidden).toBeUndefined();
    });

    it('desaturates low-connection nodes (degree below threshold)', () => {
      // A standalone leaf with a single connection has degree 1 (< threshold).
      const graph = new Graph<NodeAttributes, EdgeAttributes>();
      graph.addNode('hub', nodeAttrs({ nodeId: 'hub', color: '#FF0080' }));
      graph.addNode('leaf', nodeAttrs({ nodeId: 'leaf', color: '#00BFA5' }));
      graph.addEdgeWithKey('hl', 'hub', 'leaf', edgeAttrs({ edgeId: 'hl' }));
      const { nodeReducer } = renderReducers(graph);
      const res = nodeReducer('leaf', graph.getNodeAttributes('leaf'));
      expect(res.color).not.toBe('#00BFA5');
      expect(res.borderColor).toBe(res.color);
    });

    it('gives source-group nodes a thin glow border and skips other filters', () => {
      const graph = buildGraph();
      const { nodeReducer } = renderReducers(graph, {
        hiddenNodeIds: new Set([SOURCE_GROUP_NODE]),
        hasActiveSearch: true,
      });
      const res = nodeReducer(SOURCE_GROUP_NODE, graph.getNodeAttributes(SOURCE_GROUP_NODE));
      expect(res.borderColor).toBe('#7C4DFF');
      expect(res.borderSize).toBe(0.5);
      // Source-group branch returns early -> not hidden despite being in hiddenNodeIds.
      expect(res.hidden).toBeUndefined();
      expect(res.label).toBe('Node');
    });

    it('hides nodes that are in the hidden set', () => {
      const graph = buildGraph();
      const { nodeReducer } = renderReducers(graph, { hiddenNodeIds: new Set(['B']) });
      const res = nodeReducer('B', graph.getNodeAttributes('B'));
      expect(res.hidden).toBe(true);
    });

    it('switches pictogram nodes to circle when their size class is hidden by zoom', () => {
      const graph = buildGraph();
      const iconVisibleBySize = new Map<number, boolean>([[5, false]]);
      const { nodeReducer } = renderReducers(graph, { iconVisibleBySize });
      // size 5 is mapped to hidden -> pictogram type downgraded to circle.
      const res = nodeReducer('A', { ...graph.getNodeAttributes('A'), type: 'pictogram', size: 5 });
      expect(res.type).toBe('circle');
    });

    it('keeps pictogram type when the size class is visible', () => {
      const graph = buildGraph();
      const iconVisibleBySize = new Map<number, boolean>([[5, true]]);
      const { nodeReducer } = renderReducers(graph, { iconVisibleBySize });
      const res = nodeReducer('A', { ...graph.getNodeAttributes('A'), type: 'pictogram', size: 5 });
      expect(res.type).toBe('pictogram');
    });

    describe('selection spotlight', () => {
      it('highlights the selected node with raised zIndex and thicker border', () => {
        const graph = buildGraph();
        const { nodeReducer } = renderReducers(graph, { selectedNodeId: 'A' });
        const res = nodeReducer('A', graph.getNodeAttributes('A'));
        expect(res.highlighted).toBe(true);
        expect(res.zIndex).toBe(2);
        expect(res.borderSize).toBe(0.8);
      });

      it('raises zIndex for neighbors of the selected node', () => {
        const graph = buildGraph();
        const { nodeReducer } = renderReducers(graph, { selectedNodeId: 'A' });
        // B is a neighbor of A.
        const res = nodeReducer('B', graph.getNodeAttributes('B'));
        expect(res.zIndex).toBe(1);
      });

      it('dims and unlabels non-connected nodes when a node is selected', () => {
        const graph = buildGraph();
        // Select B, whose only neighbor is A; C is not connected to B.
        const { nodeReducer } = renderReducers(graph, { selectedNodeId: 'B' });
        const res = nodeReducer('C', graph.getNodeAttributes('C'));
        expect(res.label).toBe('');
      });
    });

    describe('search highlight', () => {
      it('highlights matched nodes', () => {
        const graph = buildGraph();
        const { nodeReducer } = renderReducers(graph, {
          hasActiveSearch: true,
          highlightedNodeIds: new Set(['A']),
        });
        const res = nodeReducer('A', graph.getNodeAttributes('A'));
        expect(res.highlighted).toBe(true);
        expect(res.zIndex).toBe(1);
      });

      it('dims, hides label, and clears border on unmatched nodes', () => {
        const graph = buildGraph();
        const { nodeReducer } = renderReducers(graph, {
          hasActiveSearch: true,
          highlightedNodeIds: new Set(['A']),
        });
        const res = nodeReducer('C', graph.getNodeAttributes('C'));
        expect(res.label).toBe('');
        expect(res.borderColor).toBe('transparent');
      });
    });

    describe('hover spotlight', () => {
      it('highlights the hovered node', () => {
        const graph = buildGraph();
        const { nodeReducer } = renderReducers(graph, { hoveredNode: 'A' });
        const res = nodeReducer('A', graph.getNodeAttributes('A'));
        expect(res.highlighted).toBe(true);
        expect(res.zIndex).toBe(2);
        expect(res.borderSize).toBe(0.8);
      });

      it('raises zIndex for hovered neighbors', () => {
        const graph = buildGraph();
        const { nodeReducer } = renderReducers(graph, {
          hoveredNode: 'A',
          hoveredNeighbors: new Set(['B']),
        });
        const res = nodeReducer('B', graph.getNodeAttributes('B'));
        expect(res.zIndex).toBe(1);
      });

      it('dims and unlabels non-hovered, non-neighbor nodes', () => {
        const graph = buildGraph();
        const { nodeReducer } = renderReducers(graph, {
          hoveredNode: 'A',
          hoveredNeighbors: new Set(['B']),
        });
        const res = nodeReducer('C', graph.getNodeAttributes('C'));
        expect(res.label).toBe('');
      });
    });
  });

  describe('edgeReducer', () => {
    it('applies gradient coloring to default edges', () => {
      const graph = buildGraph();
      const { edgeReducer } = renderReducers(graph);
      const res = edgeReducer('AB', graph.getEdgeAttributes('AB'));
      expect(typeof res.color).toBe('string');
      expect(typeof res.targetColor).toBe('string');
      expect(res.hidden).toBeUndefined();
    });

    it('renders provenance edges as a subtle line gradient', () => {
      const graph = buildGraph();
      const { edgeReducer } = renderReducers(graph);
      const res = edgeReducer(PROVENANCE_EDGE, graph.getEdgeAttributes(PROVENANCE_EDGE));
      expect(res.type).toBe('line');
      expect(typeof res.color).toBe('string');
      expect(typeof res.targetColor).toBe('string');
      expect(res.hidden).toBeUndefined();
    });

    it('hides provenance edges when an endpoint node is hidden', () => {
      const graph = buildGraph();
      // B is an endpoint of the provenance edge.
      const { edgeReducer } = renderReducers(graph, { hiddenNodeIds: new Set(['B']) });
      const res = edgeReducer(PROVENANCE_EDGE, graph.getEdgeAttributes(PROVENANCE_EDGE));
      expect(res.hidden).toBe(true);
    });

    it('exercises the provenance non-leaf gradient branch', () => {
      // Build a graph where the provenance source has higher degree than target,
      // so sourceIsLeaf is false.
      const graph = new Graph<NodeAttributes, EdgeAttributes>();
      const sg = `${SOURCE_GROUP_PREFIX}hub`;
      graph.addNode(sg, nodeAttrs({ nodeId: sg, color: '#7C4DFF' }));
      graph.addNode('leaf', nodeAttrs({ nodeId: 'leaf' }));
      graph.addNode('x', nodeAttrs({ nodeId: 'x' }));
      graph.addNode('y', nodeAttrs({ nodeId: 'y' }));
      // Give sg high degree.
      graph.addEdgeWithKey('e1', sg, 'x', edgeAttrs({ edgeId: 'e1' }));
      graph.addEdgeWithKey('e2', sg, 'y', edgeAttrs({ edgeId: 'e2' }));
      const provKey = `${SOURCE_PROVENANCE_PREFIX}p`;
      graph.addEdgeWithKey(provKey, sg, 'leaf', edgeAttrs({ edgeId: provKey, isProvenance: true }));
      const { edgeReducer } = renderReducers(graph);
      const res = edgeReducer(provKey, graph.getEdgeAttributes(provKey));
      expect(res.type).toBe('line');
      // Non-leaf source -> color/targetColor still strings (different intensities).
      expect(res.color).not.toBe(res.targetColor);
    });

    it('hides normal edges connected to hidden nodes', () => {
      const graph = buildGraph();
      const { edgeReducer } = renderReducers(graph, { hiddenNodeIds: new Set(['A']) });
      const res = edgeReducer('AB', graph.getEdgeAttributes('AB'));
      expect(res.hidden).toBe(true);
    });

    describe('selection spotlight', () => {
      it('highlights edges touching the selected node (non-hub: thicker size)', () => {
        const graph = buildGraph();
        const { edgeReducer } = renderReducers(graph, { selectedNodeId: 'A' });
        // AB touches A.
        const res = edgeReducer('AB', graph.getEdgeAttributes('AB'));
        // A has degree 3 (<= 30) -> non-hub branch: size 2, zIndex 1.
        expect(res.size).toBe(2);
        expect(res.zIndex).toBe(1);
      });

      it('dims non-connected edges when a node is selected', () => {
        const graph = buildGraph();
        // Select A; edge CD does not touch A.
        const { edgeReducer } = renderReducers(graph, { selectedNodeId: 'A' });
        const res = edgeReducer('CD', graph.getEdgeAttributes('CD'));
        expect(res.label).toBe('');
        expect(res.zIndex).toBe(-1);
      });

      it('uses the hub branch (thin size, no label) for high-degree selected nodes', () => {
        const graph = new Graph<NodeAttributes, EdgeAttributes>();
        graph.addNode('hub', nodeAttrs({ nodeId: 'hub', color: '#FF0080' }));
        // Create 35 leaves so hub degree > 30.
        for (let i = 0; i < 35; i += 1) {
          const id = `leaf${i}`;
          graph.addNode(id, nodeAttrs({ nodeId: id }));
          graph.addEdgeWithKey(`h${i}`, 'hub', id, edgeAttrs({ edgeId: `h${i}` }));
        }
        const { edgeReducer } = renderReducers(graph, { selectedNodeId: 'hub' });
        const res = edgeReducer('h0', graph.getEdgeAttributes('h0'));
        expect(res.size).toBe(1);
        expect(res.label).toBe('');
        expect(res.zIndex).toBe(1);
      });
    });

    it('thickens the explicitly selected edge', () => {
      const graph = buildGraph();
      const { edgeReducer } = renderReducers(graph, { selectedEdgeId: 'AB' });
      const res = edgeReducer('AB', graph.getEdgeAttributes('AB'));
      expect(res.size).toBe(4);
      expect(res.zIndex).toBe(2);
    });

    describe('search', () => {
      it('fades edges not touching a matched node', () => {
        const graph = buildGraph();
        const { edgeReducer } = renderReducers(graph, {
          hasActiveSearch: true,
          highlightedNodeIds: new Set(['D']),
        });
        // AB touches neither D.
        const res = edgeReducer('AB', graph.getEdgeAttributes('AB'));
        expect(res.label).toBe('');
      });

      it('leaves edges touching a matched node un-faded (no label clear)', () => {
        const graph = buildGraph();
        const { edgeReducer } = renderReducers(graph, {
          hasActiveSearch: true,
          highlightedNodeIds: new Set(['A']),
        });
        // AB touches A (matched), so search branch does not clear its label.
        const res = edgeReducer('AB', graph.getEdgeAttributes('AB'));
        expect(res.label).toBe('rel');
      });
    });

    describe('hover spotlight', () => {
      it('highlights edges touching the hovered node (non-hub: forceLabel)', () => {
        const graph = buildGraph();
        const { edgeReducer } = renderReducers(graph, { hoveredNode: 'A' });
        const res = edgeReducer('AB', graph.getEdgeAttributes('AB'));
        expect(res.size).toBe(2);
        expect(res.forceLabel).toBe(true);
        expect(res.zIndex).toBe(2);
      });

      it('dims edges not touching the hovered node', () => {
        const graph = buildGraph();
        const { edgeReducer } = renderReducers(graph, { hoveredNode: 'A' });
        const res = edgeReducer('CD', graph.getEdgeAttributes('CD'));
        expect(res.label).toBe('');
        expect(res.zIndex).toBe(-1);
      });

      it('uses the hub branch for a high-degree hovered node', () => {
        const graph = new Graph<NodeAttributes, EdgeAttributes>();
        graph.addNode('hub', nodeAttrs({ nodeId: 'hub', color: '#FF0080' }));
        for (let i = 0; i < 120; i += 1) {
          const id = `leaf${i}`;
          graph.addNode(id, nodeAttrs({ nodeId: id }));
          graph.addEdgeWithKey(`h${i}`, 'hub', id, edgeAttrs({ edgeId: `h${i}` }));
        }
        const { edgeReducer } = renderReducers(graph, { hoveredNode: 'hub' });
        const res = edgeReducer('h0', graph.getEdgeAttributes('h0'));
        // degree > 100 -> hub branch with size 1 and cleared label.
        expect(res.size).toBe(1);
        expect(res.label).toBe('');
        expect(res.zIndex).toBe(2);
      });
    });
  });

  describe('re-render behaviour', () => {
    it('re-installs reducers (re-runs the effect) when a visual-state prop changes', () => {
      const graph = buildGraph();
      const { sigma, view } = renderReducers(graph, { selectedNodeId: null });

      const callsAfterFirst = sigma.setSetting.mock.calls.length;

      view.rerender({ selectedNodeId: 'A' });

      // The effect re-ran, registering nodeReducer + edgeReducer again.
      expect(sigma.setSetting.mock.calls.length).toBeGreaterThan(callsAfterFirst);

      // The newest nodeReducer now reflects the selection.
      const { nodeReducer } = captureReducers(sigma);
      const res = nodeReducer('A', graph.getNodeAttributes('A'));
      expect(res.highlighted).toBe(true);
    });
  });
});
