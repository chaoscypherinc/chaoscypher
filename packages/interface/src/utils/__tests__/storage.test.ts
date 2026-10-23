// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  readStoredJson,
  readStoredString,
  removeStoredItem,
  writeStoredJson,
  writeStoredString,
} from '../storage';

function makeStorageThrow() {
  const fail = () => {
    throw new Error('storage unavailable');
  };
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(fail);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(fail);
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(fail);
}

describe('storage helpers', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('round-trips strings and JSON', () => {
    expect(writeStoredString('k', 'v')).toBe(true);
    expect(readStoredString('k')).toBe('v');
    expect(writeStoredJson('j', { a: [1, 2] })).toBe(true);
    expect(readStoredJson('j', null)).toEqual({ a: [1, 2] });
    expect(removeStoredItem('k')).toBe(true);
    expect(readStoredString('k')).toBeNull();
  });

  it('returns the fallback for absent, garbled or rejected JSON', () => {
    expect(readStoredJson('missing', 'fallback')).toBe('fallback');
    localStorage.setItem('bad', '{not json');
    expect(readStoredJson('bad', [])).toEqual([]);
    localStorage.setItem('num', '5');
    const isArray = (v: unknown): v is unknown[] => Array.isArray(v);
    expect(readStoredJson('num', [], isArray)).toEqual([]);
  });

  it('never throws when every storage accessor throws', () => {
    makeStorageThrow();
    expect(readStoredString('k')).toBeNull();
    expect(writeStoredString('k', 'v')).toBe(false);
    expect(removeStoredItem('k')).toBe(false);
    expect(readStoredJson('k', 'fallback')).toBe('fallback');
    expect(writeStoredJson('k', { a: 1 })).toBe(false);
  });

  it('reports a failed write for a value JSON cannot serialise', () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(writeStoredJson('k', cyclic)).toBe(false);
  });
});
