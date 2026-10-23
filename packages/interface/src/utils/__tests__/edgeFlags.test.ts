// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import {
  EDGE_FLAGS,
  EDGE_FLAG_PROPERTY_KEYS,
  FLAGGED_EDGE_SIZE,
  edgeDrawing,
  edgeFlags,
  hasEdgeFlags,
} from '../edgeFlags';

const REASON =
  '"St. Petersburg" is named in S6 but "Anna Pávlovna Schérer" is not, and located_in needs both endpoints named';

const keys = (properties: unknown) => edgeFlags(properties).map((c) => c.flag.key);

describe('edgeFlags', () => {
  it('reads a capped edge set to exactly true', () => {
    expect(keys({ capped: true, cap_pass: 'degree', cap: 80 })).toEqual(['capped']);
    expect(hasEdgeFlags({ capped: true })).toBe(true);
    expect(edgeFlags({ capped: true })[0].reason).toBeUndefined();
  });

  it('reads the unevidenced flag set to exactly true, with its reason', () => {
    const carried = edgeFlags({ unevidenced: true, evidence_reason: REASON });
    expect(carried.map((c) => c.flag.key)).toEqual(['unevidenced']);
    expect(carried[0].reason).toBe(REASON);
    expect(carried[0].flag.label).toBe('Unevidenced');
  });

  it('reads every flag an edge carries, in table order', () => {
    expect(keys({ unevidenced: true, capped: true })).toEqual(['capped', 'unevidenced']);
    expect(keys({ type_mismatch: true, unmatched_label: true, capped: true })).toEqual([
      'capped',
      'unmatched_label',
      'type_mismatch',
    ]);
  });

  it("reads the type check's flags with the written label and the reason beside them", () => {
    const unmatched = edgeFlags({ unmatched_label: true, written_label: 'owes_money_to' });
    expect(unmatched.map((c) => [c.flag.key, c.reason])).toEqual([
      ['unmatched_label', 'owes_money_to'],
    ]);
    const mismatch = edgeFlags({ type_mismatch: true, type_mismatch_reason: 'parent_of takes a Person' });
    expect(mismatch.map((c) => [c.flag.key, c.reason])).toEqual([
      ['type_mismatch', 'parent_of takes a Person'],
    ]);
  });

  it('carries no reason when none is stored, and ignores a blank one', () => {
    expect(edgeFlags({ unevidenced: true })[0].reason).toBeUndefined();
    expect(edgeFlags({ unevidenced: true, evidence_reason: '  ' })[0].reason).toBeUndefined();
  });

  it("ignores values that are not exactly boolean true (the text's)", () => {
    for (const value of ['true', 'True', 'yes', 1, 0, {}, [], false, null, undefined]) {
      expect(
        keys({ capped: value, unevidenced: value, unmatched_label: value, type_mismatch: value }),
      ).toEqual([]);
    }
  });

  it('carries no flags for missing or non-object properties', () => {
    expect(edgeFlags(undefined)).toEqual([]);
    expect(edgeFlags(null)).toEqual([]);
    expect(edgeFlags('capped')).toEqual([]);
    expect(edgeFlags({})).toEqual([]);
    expect(hasEdgeFlags({ confidence: 0.9 })).toBe(false);
  });

  it('draws a capped edge thin and an unevidenced one dashed, each through its flag', () => {
    expect(edgeDrawing({ capped: true })).toEqual({ thin: true, dashed: false });
    expect(edgeDrawing({ unevidenced: true })).toEqual({ thin: false, dashed: true });
    expect(edgeDrawing({ capped: true, unevidenced: true })).toEqual({ thin: true, dashed: true });
    expect(edgeDrawing({ unmatched_label: true })).toEqual({ thin: true, dashed: false });
    expect(edgeDrawing({ type_mismatch: true })).toEqual({ thin: true, dashed: false });
    expect(edgeDrawing({ unevidenced: 'true' })).toEqual({ thin: false, dashed: false });
    expect(edgeDrawing(undefined)).toEqual({ thin: false, dashed: false });
    expect(FLAGGED_EDGE_SIZE).toBeLessThan(1);
  });

  it('labels every flag, says how it draws, and lists the keys it writes', () => {
    for (const flag of EDGE_FLAGS) {
      expect(flag.label.length).toBeGreaterThan(0);
      expect(flag.description.length).toBeGreaterThan(0);
      expect(['thin', 'dashed']).toContain(flag.draw);
      expect(EDGE_FLAG_PROPERTY_KEYS).toContain(flag.key);
    }
    expect(EDGE_FLAG_PROPERTY_KEYS).toEqual([
      'capped',
      'unevidenced',
      'evidence_reason',
      'unmatched_label',
      'written_label',
      'type_mismatch',
      'type_mismatch_reason',
    ]);
  });
});
