// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Pure-logic tests for the shared merged-chip helper.
 *
 * The render tests in SourceStatusCell.test.tsx already cover the
 * end-to-end behaviour through the list-row component, but the helper
 * itself is the contract — and now used in two places (list row +
 * detail header). Pinning the matrix here keeps either call site
 * honest if the other one's tests are skipped.
 */

import { describe, it, expect } from 'vitest';
import { deriveMergedChipState } from '../mergedChipState';

describe('deriveMergedChipState', () => {
  it('returns Disabled regardless of vector status when isEnabled is false', () => {
    for (const status of [undefined, 'pending', 'indexed', 'degraded', 'failed']) {
      const chip = deriveMergedChipState(false, status, null);
      expect(chip.label).toBe('Disabled');
      expect(chip.color).toBe('default');
    }
  });

  it("returns warning 'Search retrying' for degraded vector status", () => {
    const chip = deriveMergedChipState(true, 'degraded', null);
    expect(chip.label).toBe('Search retrying');
    expect(chip.color).toBe('warning');
    expect(chip.tooltip).toMatch(/retry/i);
  });

  it("returns error 'Search failed' for failed vector status", () => {
    const chip = deriveMergedChipState(true, 'failed', null);
    expect(chip.label).toBe('Search failed');
    expect(chip.color).toBe('error');
    expect(chip.tooltip).toMatch(/re-extract/i);
  });

  it("returns success 'Active' for indexed vector status and embeds the indexed timestamp in the tooltip", () => {
    const chip = deriveMergedChipState(true, 'indexed', '2026-05-11T12:00:00Z');
    expect(chip.label).toBe('Active');
    expect(chip.color).toBe('success');
    expect(chip.tooltip).toMatch(/Vector index is current/);
    expect(chip.tooltip).toMatch(/last indexed/);
  });

  it("returns success 'Active' for indexed without a timestamp (no 'last indexed' line)", () => {
    const chip = deriveMergedChipState(true, 'indexed', null);
    expect(chip.label).toBe('Active');
    expect(chip.tooltip).toMatch(/Vector index is current/);
    expect(chip.tooltip).not.toMatch(/last indexed/);
  });

  it("returns success 'Active' for pending vector status with a 'building' tooltip line", () => {
    const chip = deriveMergedChipState(true, 'pending', null);
    expect(chip.label).toBe('Active');
    expect(chip.color).toBe('success');
    expect(chip.tooltip).toMatch(/being built/);
  });

  it("returns success 'Active' for missing vector status (legacy source)", () => {
    const chip = deriveMergedChipState(true, undefined, null);
    expect(chip.label).toBe('Active');
    expect(chip.color).toBe('success');
    expect(chip.tooltip).toBe('Visible in knowledge graph and search.');
  });

  it("returns success 'Active' for unknown future vector status (defensive)", () => {
    const chip = deriveMergedChipState(true, 'quantum-flux', null);
    expect(chip.label).toBe('Active');
    expect(chip.color).toBe('success');
  });
});
