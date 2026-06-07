// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import {
  computeAvgRelations,
  computeDensity,
  computeEdgesPerSource,
} from '../stats';

describe('computeAvgRelations', () => {
  it('returns relations divided by entities, rounded to 1 decimal', () => {
    expect(computeAvgRelations(870, 6832)).toBe(7.9);
  });

  it('returns 0 when there are no entities', () => {
    expect(computeAvgRelations(0, 100)).toBe(0);
  });

  it('returns 0 when there are no relations', () => {
    expect(computeAvgRelations(50, 0)).toBe(0);
  });
});

describe('computeDensity', () => {
  it('returns directed-graph density as a percentage', () => {
    // 6832 / (870 * 869) * 100 = 0.9036... ≈ 0.9
    expect(computeDensity(870, 6832)).toBeCloseTo(0.9, 1);
  });

  it('caps tiny values to a single decimal place', () => {
    // 1 / (100 * 99) * 100 = 0.0101... → "0.0"
    expect(computeDensity(100, 1)).toBeCloseTo(0.0, 1);
  });

  it('returns 0 when fewer than 2 entities', () => {
    expect(computeDensity(0, 0)).toBe(0);
    expect(computeDensity(1, 0)).toBe(0);
  });

  it('returns 0 when there are no relations', () => {
    expect(computeDensity(100, 0)).toBe(0);
  });

  it('clamps at 100 if math somehow exceeds it', () => {
    // Shouldn't be reachable with sane inputs, but guard the boundary.
    expect(computeDensity(2, 100)).toBeLessThanOrEqual(100);
  });
});

describe('computeEdgesPerSource', () => {
  it('returns relations divided by sources, rounded to 1 decimal', () => {
    // 6832 / 11 = 621.09... → 621.1
    expect(computeEdgesPerSource(11, 6832)).toBe(621.1);
  });

  it('returns 0 when there are no sources', () => {
    expect(computeEdgesPerSource(0, 100)).toBe(0);
  });

  it('returns 0 when there are no relations', () => {
    expect(computeEdgesPerSource(11, 0)).toBe(0);
  });

  it('handles single source', () => {
    expect(computeEdgesPerSource(1, 50)).toBe(50);
  });
});
