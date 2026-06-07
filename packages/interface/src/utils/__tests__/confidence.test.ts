// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import {
  normalizeConfidence,
  formatConfidencePct,
  confidenceChipColor,
} from '../confidence';
import { ChaosCypherPalette } from '../../theme/palette';

describe('normalizeConfidence', () => {
  it('returns a 0-1 fraction unchanged', () => {
    expect(normalizeConfidence(0.87)).toBeCloseTo(0.87);
  });

  it('scales a 0-100 percentage down to a fraction', () => {
    expect(normalizeConfidence(87)).toBeCloseTo(0.87);
  });

  it('clamps out-of-range values into [0, 1]', () => {
    expect(normalizeConfidence(140)).toBe(1);
    expect(normalizeConfidence(-0.5)).toBe(0);
  });

  it('returns null for missing or non-numeric input', () => {
    expect(normalizeConfidence(undefined)).toBeNull();
    expect(normalizeConfidence('high')).toBeNull();
    expect(normalizeConfidence(NaN)).toBeNull();
  });
});

describe('formatConfidencePct', () => {
  it('formats a fraction as a whole percent', () => {
    expect(formatConfidencePct(0.873)).toBe('87%');
  });

  it('returns null when there is no usable value', () => {
    expect(formatConfidencePct(undefined)).toBeNull();
  });
});

describe('confidenceChipColor', () => {
  it('grades by threshold: mint / gold / red', () => {
    expect(confidenceChipColor(0.9)).toBe(ChaosCypherPalette.success);
    expect(confidenceChipColor(0.6)).toBe(ChaosCypherPalette.warning);
    expect(confidenceChipColor(0.2)).toBe(ChaosCypherPalette.error);
  });

  it('falls back to the neutral accent when there is no value', () => {
    expect(confidenceChipColor(undefined)).toBe(ChaosCypherPalette.primary);
  });
});
