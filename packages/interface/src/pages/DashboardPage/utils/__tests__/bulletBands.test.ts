// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import {
  AVG_REL_BANDS,
  DENSITY_BANDS,
  QUALITY_BANDS,
  classify,
  fillPct,
  tickPositions,
} from '../bulletBands';

describe('classify', () => {
  it('returns the first band when value falls in its range', () => {
    expect(classify(30, QUALITY_BANDS).label).toBe('Poor');
    expect(classify(0, QUALITY_BANDS).label).toBe('Poor');
  });

  it('returns the middle band when value is in its range', () => {
    expect(classify(60, QUALITY_BANDS).label).toBe('OK');
    expect(classify(74.9, QUALITY_BANDS).label).toBe('OK');
  });

  it('returns the last band when value matches or exceeds the top', () => {
    expect(classify(80, QUALITY_BANDS).label).toBe('Good');
    expect(classify(100, QUALITY_BANDS).label).toBe('Good');
    expect(classify(150, QUALITY_BANDS).label).toBe('Good');
  });

  it('classifies typical KG densities correctly', () => {
    expect(classify(0.05, DENSITY_BANDS).label).toBe('Sparse');
    expect(classify(0.5, DENSITY_BANDS).label).toBe('Typical');
    expect(classify(3.8, DENSITY_BANDS).label).toBe('Dense');
  });

  it('classifies typical avg-relationship counts correctly', () => {
    expect(classify(2, AVG_REL_BANDS).label).toBe('Sparse');
    expect(classify(6.1, AVG_REL_BANDS).label).toBe('Moderate');
    expect(classify(20, AVG_REL_BANDS).label).toBe('Heavy');
  });
});

describe('fillPct', () => {
  it('returns the percentage of the scale max', () => {
    expect(fillPct(50, QUALITY_BANDS)).toBe(50);
    expect(fillPct(68, QUALITY_BANDS)).toBe(68);
  });

  it('clamps to 100 when value exceeds scale max', () => {
    expect(fillPct(120, QUALITY_BANDS)).toBe(100);
  });

  it('clamps to 0 when value is negative', () => {
    expect(fillPct(-10, QUALITY_BANDS)).toBe(0);
  });

  it('returns 0 when scaleMax is zero (defensive)', () => {
    expect(fillPct(5, { bands: [], scaleMax: 0 })).toBe(0);
  });

  it('handles fractional values correctly for density', () => {
    // 3.8 / 5 = 76
    expect(fillPct(3.8, DENSITY_BANDS)).toBe(76);
  });
});

describe('classify state', () => {
  it('maps the three Quality bands to poor / ok / good', () => {
    expect(classify(30, QUALITY_BANDS).state).toBe('poor');
    expect(classify(60, QUALITY_BANDS).state).toBe('ok');
    expect(classify(90, QUALITY_BANDS).state).toBe('good');
  });

  it('maps the three Density bands to poor / ok / good', () => {
    expect(classify(0.05, DENSITY_BANDS).state).toBe('poor');
    expect(classify(0.5, DENSITY_BANDS).state).toBe('ok');
    expect(classify(3.8, DENSITY_BANDS).state).toBe('good');
  });

  it('maps the three Avg-Rel bands to poor / ok / good', () => {
    expect(classify(2, AVG_REL_BANDS).state).toBe('poor');
    expect(classify(6.1, AVG_REL_BANDS).state).toBe('ok');
    expect(classify(20, AVG_REL_BANDS).state).toBe('good');
  });
});

describe('tickPositions', () => {
  it('returns one tick per internal band boundary, expressed as percent', () => {
    // Quality: bands end at 50, 75, 100 → internal boundaries are 50% and 75%.
    expect(tickPositions(QUALITY_BANDS)).toEqual([50, 75]);
  });

  it('skips the final boundary which sits at scaleMax', () => {
    expect(tickPositions(QUALITY_BANDS)).toHaveLength(QUALITY_BANDS.bands.length - 1);
  });

  it('handles non-100 scaleMax by expressing positions in percent', () => {
    // Density: bands end at 0.1, 1, 5 with scaleMax 5 → 2% and 20%.
    expect(tickPositions(DENSITY_BANDS)).toEqual([2, 20]);
  });

  it('returns an empty list when scaleMax is zero (defensive)', () => {
    expect(tickPositions({ bands: [], scaleMax: 0 })).toEqual([]);
  });
});

describe('band configs are well-formed', () => {
  it('quality bands cover 0–100 with ascending upper bounds', () => {
    const bounds = QUALITY_BANDS.bands.map((b) => b.upperBound);
    expect(bounds).toEqual([...bounds].sort((a, b) => a - b));
    expect(QUALITY_BANDS.bands[QUALITY_BANDS.bands.length - 1].upperBound).toBe(
      QUALITY_BANDS.scaleMax,
    );
  });

  it('density bands have ascending upper bounds', () => {
    const bounds = DENSITY_BANDS.bands.map((b) => b.upperBound);
    expect(bounds).toEqual([...bounds].sort((a, b) => a - b));
  });

  it('avg-rel bands have ascending upper bounds', () => {
    const bounds = AVG_REL_BANDS.bands.map((b) => b.upperBound);
    expect(bounds).toEqual([...bounds].sort((a, b) => a - b));
  });
});
