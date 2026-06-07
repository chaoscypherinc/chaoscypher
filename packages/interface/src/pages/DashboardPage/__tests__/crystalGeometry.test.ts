// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import {
  createIcosahedron,
  computeOrbitHomes,
  SHELL_RADIUS,
  SHELL_DEPTH_SPREAD,
  type Vec3,
} from '../crystalGeometry';

function len(v: Vec3): number {
  return Math.hypot(v[0], v[1], v[2]);
}
function dist(a: Vec3, b: Vec3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

describe('createIcosahedron', () => {
  it('has 12 vertices', () => {
    expect(createIcosahedron().vertices).toHaveLength(12);
  });

  it('has 30 edges', () => {
    expect(createIcosahedron().edges).toHaveLength(30);
  });

  it('places every vertex on the unit sphere (normalized circumradius)', () => {
    for (const v of createIcosahedron().vertices) {
      expect(len(v)).toBeCloseTo(1, 9);
    }
  });

  it('references valid, distinct vertex indices with no duplicate edges', () => {
    const { vertices, edges } = createIcosahedron();
    const seen = new Set<string>();
    for (const [a, b] of edges) {
      expect(a).not.toBe(b);
      expect(a).toBeGreaterThanOrEqual(0);
      expect(b).toBeLessThan(vertices.length);
      const key = a < b ? `${a}-${b}` : `${b}-${a}`;
      expect(seen.has(key)).toBe(false);
      seen.add(key);
    }
  });
});

describe('computeOrbitHomes', () => {
  it('returns one shell position per node', () => {
    const homes = computeOrbitHomes([
      { x: 10, y: 20 },
      { x: -30, y: 5, z: 0.2 },
      { x: 0, y: -15 },
    ]);
    expect(homes).toHaveLength(3);
  });

  it('returns an empty array for no nodes', () => {
    expect(computeOrbitHomes([])).toEqual([]);
  });

  it('places every node on the shell, layered radially by z', () => {
    const homes = computeOrbitHomes([
      { x: 100, y: 50, z: 0.5 },
      { x: -100, y: -50, z: -0.5 },
      { x: 0, y: 0, z: 0 },
    ]);
    const maxR = SHELL_RADIUS + SHELL_DEPTH_SPREAD * 0.5 + 1e-9;
    const minR = SHELL_RADIUS - SHELL_DEPTH_SPREAD * 0.5 - 1e-9;
    for (const h of homes) {
      expect(len(h)).toBeGreaterThanOrEqual(minR);
      expect(len(h)).toBeLessThanOrEqual(maxR);
    }
  });

  it('keeps layout-clustered nodes clumped (near layout → near shell)', () => {
    // A and B are close in layout; C is far. Their shell homes must preserve that.
    const [a, b, c] = computeOrbitHomes([
      { x: 100, y: 50 },
      { x: 110, y: 55 },
      { x: -120, y: -60 },
    ]);
    expect(dist(a, b)).toBeLessThan(dist(a, c));
  });

  it('is deterministic for the same input', () => {
    const input = [{ x: 12, y: -8, z: 0.1 }, { x: -4, y: 30 }];
    expect(computeOrbitHomes(input)).toEqual(computeOrbitHomes(input));
  });

  it('handles a single node (degenerate extent) without NaN', () => {
    const [h] = computeOrbitHomes([{ x: 5, y: 5 }]);
    expect(Number.isFinite(h[0])).toBe(true);
    expect(len(h)).toBeCloseTo(SHELL_RADIUS, 9);
  });
});
