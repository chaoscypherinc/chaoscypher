// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from 'vitest';
import { safeNextPath } from '../safeNextPath';

describe('safeNextPath', () => {
  it.each([
    ['/sources', '/sources'],
    ['/ok?a=b#c', '/ok?a=b#c'],
    ['/', '/'],
  ])('keeps the in-app path %s', (raw, expected) => {
    expect(safeNextPath(raw)).toBe(expected);
  });

  it.each([
    [null],
    [''],
    ['//evil.com'],
    ['/\\evil.com'],
    ['/\\/evil.com'],
    ['/path\\..\\x'],
    ['https://evil.com'],
    ['javascript:alert(1)'],
    ['evil.com'],
  ])('falls back to / for %s', (raw) => {
    expect(safeNextPath(raw)).toBe('/');
  });

  it('never yields a target that leaves the origin', () => {
    const origin = 'http://app.local';
    for (const raw of ['//evil.com', '/\\evil.com', '/\\/evil.com', '/sources']) {
      expect(new URL(safeNextPath(raw), origin).origin).toBe(origin);
    }
  });
});
