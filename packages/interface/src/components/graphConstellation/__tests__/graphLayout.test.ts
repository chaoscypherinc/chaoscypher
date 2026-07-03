// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the shared constellation layout pipeline.
 *
 * d3-force and the palette run for real; ``Math.random`` is stubbed so the
 * sampler / initial positions are deterministic. Assertions cover structural
 * invariants (caps, orphan handling, edge integrity, deterministic colour)
 * and the sessionStorage cache path rather than exact coordinates.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  computeConstellationLayout,
  colorFromId,
  sampleConnected,
  type RawNode,
  type RawEdge,
} from '../graphLayout';
import { ChaosCypherPalette } from '../../../theme/palette';

const PALETTE = Object.values(ChaosCypherPalette) as string[];

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(Math, 'random').mockReturnValue(0.42);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('colorFromId', () => {
  it('is deterministic and drawn from the palette', () => {
    expect(colorFromId('abc')).toBe(colorFromId('abc'));
    expect(PALETTE).toContain(colorFromId('abc'));
  });

  it('maps different ids across the palette', () => {
    const colors = new Set(['t1', 't2', 't3', 't4', 't5', 't6'].map(colorFromId));
    expect(colors.size).toBeGreaterThan(1);
  });
});

describe('sampleConnected', () => {
  it('returns all nodes when under the limit', () => {
    const nodes: RawNode[] = [{ id: 'a' }, { id: 'b' }];
    expect(sampleConnected(nodes, [], 10)).toHaveLength(2);
  });

  it('caps the sample to the limit for a large connected chain', () => {
    const n = 50;
    const nodes: RawNode[] = Array.from({ length: n }, (_, i) => ({ id: `n${i}` }));
    const edges: RawEdge[] = Array.from({ length: n - 1 }, (_, i) => ({
      source_node_id: `n${i}`,
      target_node_id: `n${i + 1}`,
    }));
    expect(sampleConnected(nodes, edges, 10)).toHaveLength(10);
  });
});

describe('computeConstellationLayout', () => {
  const opts = { maxRenderNodes: 200, cacheKey: 'test_layout_v1' };

  it('returns empty for empty input', () => {
    expect(computeConstellationLayout([], [], opts)).toEqual({ nodes: [], edges: [] });
  });

  it('positions nodes with finite coords, palette colour, positive size', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't2', source_id: 's1' },
    ];
    const edges: RawEdge[] = [{ source_node_id: 'a', target_node_id: 'b' }];

    const { nodes: out } = computeConstellationLayout(nodes, edges, opts);

    expect(out).toHaveLength(2);
    for (const n of out) {
      expect(Number.isFinite(n.x)).toBe(true);
      expect(Number.isFinite(n.y)).toBe(true);
      expect(n.radius).toBeGreaterThan(0);
      expect(n.opacity).toBeGreaterThan(0);
      expect(PALETTE).toContain(n.color);
    }
  });

  it('keeps orphan nodes when dropOrphans is false', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't1', source_id: 's1' },
      { id: 'orphan', template_id: 't2', source_id: 's1' },
    ];
    const edges: RawEdge[] = [{ source_node_id: 'a', target_node_id: 'b' }];

    const { nodes: out } = computeConstellationLayout(nodes, edges, {
      ...opts,
      dropOrphans: false,
    });

    expect(out.map((n) => n.id)).toContain('orphan');
    expect(out).toHaveLength(3);
  });

  it('drops orphan nodes when dropOrphans is true', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't1', source_id: 's1' },
      { id: 'orphan', template_id: 't2', source_id: 's1' },
    ];
    const edges: RawEdge[] = [{ source_node_id: 'a', target_node_id: 'b' }];

    const { nodes: out } = computeConstellationLayout(nodes, edges, {
      ...opts,
      dropOrphans: true,
    });

    expect(out.map((n) => n.id)).not.toContain('orphan');
    expect(out).toHaveLength(2);
  });

  it('emits edges whose endpoints are all present in the node set', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't1', source_id: 's1' },
      { id: 'c', template_id: 't1', source_id: 's1' },
    ];
    const edges: RawEdge[] = [
      { source_node_id: 'a', target_node_id: 'b' },
      { source_node_id: 'b', target_node_id: 'c' },
    ];

    const { nodes: out, edges: outEdges } = computeConstellationLayout(nodes, edges, opts);
    const ids = new Set(out.map((n) => n.id));

    expect(outEdges.length).toBeGreaterThan(0);
    for (const e of outEdges) {
      expect(ids.has(e.source)).toBe(true);
      expect(ids.has(e.target)).toBe(true);
      expect(e.opacity).toBeGreaterThan(0);
    }
  });

  it('colours nodes deterministically by template (same template → same colour)', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 'shared', source_id: 's1' },
      { id: 'b', template_id: 'shared', source_id: 's1' },
      { id: 'c', template_id: 'other', source_id: 's1' },
    ];
    const edges: RawEdge[] = [
      { source_node_id: 'a', target_node_id: 'b' },
      { source_node_id: 'b', target_node_id: 'c' },
    ];

    const { nodes: out } = computeConstellationLayout(nodes, edges, opts);
    const byId = new Map(out.map((n) => [n.id, n]));
    expect(byId.get('a')!.color).toBe(byId.get('b')!.color);
  });

  it('caps rendered nodes to maxRenderNodes for oversized input', () => {
    const n = 60;
    const nodes: RawNode[] = Array.from({ length: n }, (_, i) => ({
      id: `n${i}`,
      template_id: `t${i % 3}`,
      source_id: 's1',
    }));
    const edges: RawEdge[] = Array.from({ length: n - 1 }, (_, i) => ({
      source_node_id: `n${i}`,
      target_node_id: `n${i + 1}`,
    }));

    const { nodes: out } = computeConstellationLayout(nodes, edges, {
      maxRenderNodes: 20,
      cacheKey: 'cap_test_v1',
    });
    expect(out.length).toBeGreaterThan(0);
    expect(out.length).toBeLessThanOrEqual(20);
  });

  it('reuses a cached layout for the same key + data (stable positions)', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't1', source_id: 's1' },
    ];
    const edges: RawEdge[] = [{ source_node_id: 'a', target_node_id: 'b' }];

    const first = computeConstellationLayout(nodes, edges, opts);
    expect(sessionStorage.getItem('test_layout_v1')).not.toBeNull();

    const second = computeConstellationLayout(nodes, edges, opts);
    const pos = (r: typeof first) => r.nodes.map((n) => ({ id: n.id, x: n.x, y: n.y }));
    expect(pos(second)).toEqual(pos(first));
  });
});

describe('resolveColor (template-palette opt-in)', () => {
  const opts = { maxRenderNodes: 200, cacheKey: 'resolve_base_v1' };

  it('colours nodes via resolveColor when provided', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't2', source_id: 's1' },
    ];
    const edges: RawEdge[] = [{ source_node_id: 'a', target_node_id: 'b' }];
    const resolveColor = (tid: string) => (tid === 't1' ? '#ff6f61' : '#123456');

    const { nodes: out } = computeConstellationLayout(nodes, edges, {
      ...opts,
      cacheKey: 'resolve_nodes_v1',
      resolveColor,
    });
    const byId = new Map(out.map((n) => [n.id, n]));
    expect(byId.get('a')!.color).toBe('#ff6f61');
    expect(byId.get('b')!.color).toBe('#123456');
  });

  it('falls back to the hash palette when resolveColor returns nullish', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't2', source_id: 's1' },
    ];
    const edges: RawEdge[] = [{ source_node_id: 'a', target_node_id: 'b' }];
    const resolveColor = (tid: string) => (tid === 't1' ? '#ff6f61' : null);

    const { nodes: out } = computeConstellationLayout(nodes, edges, {
      ...opts,
      cacheKey: 'resolve_fallback_v1',
      resolveColor,
    });
    const byId = new Map(out.map((n) => [n.id, n]));
    expect(byId.get('a')!.color).toBe('#ff6f61');
    expect(byId.get('b')!.color).toBe(colorFromId('t2'));
    expect(PALETTE).toContain(byId.get('b')!.color);
  });

  it('propagates resolved colours onto edges', () => {
    const nodes: RawNode[] = [
      { id: 'a', template_id: 't1', source_id: 's1' },
      { id: 'b', template_id: 't1', source_id: 's1' },
    ];
    const edges: RawEdge[] = [{ source_node_id: 'a', target_node_id: 'b' }];
    const resolveColor = () => '#abcdef';

    const { edges: out } = computeConstellationLayout(nodes, edges, {
      ...opts,
      cacheKey: 'resolve_edges_v1',
      resolveColor,
    });
    expect(out.length).toBeGreaterThan(0);
    expect(out.every((e) => e.color === '#abcdef')).toBe(true);
  });
});

describe('organicRelax / assignDepth (dashboard opt-ins)', () => {
  const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
    Math.hypot(a.x - b.x, a.y - b.y);
  const mean = (xs: number[]) => xs.reduce((s, v) => s + v, 0) / xs.length;

  /** Two sources × two templates, chained within each template + a couple of
   *  cross-template / cross-source links — enough cluster structure to test. */
  function buildClusteredGraph(): { nodes: RawNode[]; edges: RawEdge[] } {
    const nodes: RawNode[] = [];
    const edges: RawEdge[] = [];
    for (const s of ['s1', 's2']) {
      for (const t of ['ta', 'tb']) {
        const ids = Array.from({ length: 6 }, (_, i) => `${s}_${t}_${i}`);
        ids.forEach((id) => nodes.push({ id, template_id: `${s}_${t}`, source_id: s }));
        for (let i = 0; i < ids.length - 1; i++) {
          edges.push({ source_node_id: ids[i], target_node_id: ids[i + 1] });
        }
      }
      edges.push({ source_node_id: `${s}_ta_0`, target_node_id: `${s}_tb_0` });
    }
    edges.push({ source_node_id: 's1_ta_0', target_node_id: 's2_ta_0' });
    return { nodes, edges };
  }

  it('leaves z undefined by default (per-source map unaffected)', () => {
    const { nodes, edges } = buildClusteredGraph();
    const { nodes: out } = computeConstellationLayout(nodes, edges, {
      maxRenderNodes: 200,
      cacheKey: 'reg_default_v1',
    });
    expect(out.every((n) => n.z === undefined)).toBe(true);
  });

  it('assignDepth populates clustered z within [-0.5, 0.5]', () => {
    const { nodes, edges } = buildClusteredGraph();
    const { nodes: out } = computeConstellationLayout(nodes, edges, {
      maxRenderNodes: 200,
      cacheKey: 'reg_depth_v1',
      assignDepth: true,
    });

    expect(out.every((n) => typeof n.z === 'number')).toBe(true);
    for (const n of out) {
      expect(n.z!).toBeGreaterThanOrEqual(-0.5);
      expect(n.z!).toBeLessThanOrEqual(0.5);
    }

    // Distinct sources land in distinct depth bands.
    const s1z = mean(out.filter((n) => n.id.startsWith('s1_')).map((n) => n.z!));
    const s2z = mean(out.filter((n) => n.id.startsWith('s2_')).map((n) => n.z!));
    expect(Math.abs(s1z - s2z)).toBeGreaterThan(0.1);
  });

  it('organicRelax keeps edges shorter than the average node pair', () => {
    const { nodes, edges } = buildClusteredGraph();
    const { nodes: out, edges: outEdges } = computeConstellationLayout(nodes, edges, {
      maxRenderNodes: 200,
      cacheKey: 'reg_organic_v1',
      organicRelax: true,
    });

    const byId = new Map(out.map((n) => [n.id, n]));
    const meanEdge = mean(outEdges.map((e) => dist(byId.get(e.source)!, byId.get(e.target)!)));

    const pairs: number[] = [];
    for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) pairs.push(dist(out[i], out[j]));
    }
    // Connected nodes pull together, so edges are well below the typical pair.
    expect(meanEdge).toBeLessThan(mean(pairs));
  });
});
