// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import { NODE_FLAGS, hasNodeFlags, nodeFlags } from '../nodeFlags';

const keys = (properties: unknown) => nodeFlags(properties).map((flag) => flag.key);

describe('nodeFlags', () => {
  it('reads each known flag set to exactly true', () => {
    expect(keys({ orphan: true })).toEqual(['orphan']);
    expect(keys({ descriptor_reference: true })).toEqual(['descriptor_reference']);
    expect(keys({ low_plausibility: true, plausibility: 0.2 })).toEqual(['low_plausibility']);
    expect(keys({ unplaced_type: true, written_type: 'Quux' })).toEqual(['unplaced_type']);
    expect(keys({ unevidenced: true, evidence_reason: '"Mavra" is not named in S2' })).toEqual([
      'unevidenced',
    ]);
    expect(keys({ excluded_name: true, excluded_by: 'Chapter headings' })).toEqual([
      'excluded_name',
    ]);
  });

  it("never reads an extracted property of the text's as a flag (Sónya's shape)", () => {
    const sonya = {
      status: 'In love',
      family_role: 'Niece',
      orphan: 'yes',
      low_plausibility: 'no',
      unplaced_type: 'Niece',
      unevidenced: 'no',
      excluded_name: 'no',
    };
    expect(keys(sonya)).toEqual([]);
  });

  it('ignores values that are not exactly boolean true', () => {
    for (const value of ['true', 'True', 1, 'yes', {}, [], false, null, undefined, 0]) {
      const bag = Object.fromEntries(NODE_FLAGS.map((flag) => [flag.key, value]));
      expect(keys(bag)).toEqual([]);
    }
  });

  it('carries no flags for missing or non-object properties', () => {
    expect(nodeFlags(undefined)).toEqual([]);
    expect(nodeFlags(null)).toEqual([]);
    expect(nodeFlags('orphan')).toEqual([]);
    expect(nodeFlags(42)).toEqual([]);
    expect(nodeFlags({})).toEqual([]);
    expect(nodeFlags({ description: 'a count', other: true })).toEqual([]);
  });

  it('returns every flag a node carries, in table order whatever the key order', () => {
    const tableOrder = NODE_FLAGS.map((flag) => flag.key);
    const all = Object.fromEntries(tableOrder.map((key) => [key, true]));
    const reversed = Object.fromEntries([...tableOrder].reverse().map((key) => [key, true]));
    expect(keys(all)).toEqual(tableOrder);
    expect(keys(reversed)).toEqual(tableOrder);
  });

  it('exposes a label and description for every flag', () => {
    for (const flag of nodeFlags(Object.fromEntries(NODE_FLAGS.map((f) => [f.key, true])))) {
      expect(flag.label.length).toBeGreaterThan(0);
      expect(flag.description.length).toBeGreaterThan(0);
    }
    expect(nodeFlags({ orphan: true })[0].label).toBe('Unconnected');
    expect(nodeFlags({ descriptor_reference: true })[0].label).toBe('Reference');
    expect(nodeFlags({ low_plausibility: true })[0].label).toBe('Low plausibility');
    expect(nodeFlags({ unplaced_type: true })[0].label).toBe('Unplaced type');
  });

  it('hasNodeFlags mirrors nodeFlags', () => {
    expect(hasNodeFlags({ orphan: true })).toBe(true);
    expect(hasNodeFlags({ orphan: 'true' })).toBe(false);
    expect(hasNodeFlags(undefined)).toBe(false);
  });
});
