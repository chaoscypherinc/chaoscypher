import { describe, it, expect } from 'vitest';
import { buildHeroGraph, type HeroNode, type HeroEdge } from '../dataCrystal.data';

describe('buildHeroGraph', () => {
  it('is deterministic across calls', () => {
    expect(buildHeroGraph()).toEqual(buildHeroGraph());
  });

  it('produces a full-but-bounded node set', () => {
    const { nodes } = buildHeroGraph();
    expect(nodes.length).toBeGreaterThanOrEqual(120);
    expect(nodes.length).toBeLessThanOrEqual(220);
  });

  it('never connects two different sources (no cross-source edges)', () => {
    const { nodes, edges } = buildHeroGraph();
    const sourceOf = new Map(nodes.map((n) => [n.id, n.id.split('_')[0]]));
    for (const e of edges) {
      expect(sourceOf.get(e.source)).toBe(sourceOf.get(e.target));
    }
  });

  it('gives every edge endpoint a real node', () => {
    const { nodes, edges } = buildHeroGraph();
    const ids = new Set(nodes.map((n) => n.id));
    for (const e of edges) {
      expect(ids.has(e.source)).toBe(true);
      expect(ids.has(e.target)).toBe(true);
    }
  });

  it('marks some groups as deeper "satellite" background groups', () => {
    const { nodes } = buildHeroGraph();
    expect(nodes.some((n) => n.z > 0.5)).toBe(true);  // back groups sit deeper
  });
});
