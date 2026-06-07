// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import { pageNumberFromFilename } from '../useSourceImages';

describe('pageNumberFromFilename', () => {
  it('extracts a 1-indexed page number from the canonical filename', () => {
    expect(pageNumberFromFilename('page_1.png')).toBe(1);
    expect(pageNumberFromFilename('page_42.png')).toBe(42);
    expect(pageNumberFromFilename('page_999.png')).toBe(999);
  });

  it('is case-insensitive on the extension (some FSes uppercase)', () => {
    expect(pageNumberFromFilename('page_3.PNG')).toBe(3);
  });

  it('returns null for filenames that do not match the convention', () => {
    expect(pageNumberFromFilename('slide_1.png')).toBeNull();
    expect(pageNumberFromFilename('thumbnail.png')).toBeNull();
    expect(pageNumberFromFilename('page_one.png')).toBeNull();
    expect(pageNumberFromFilename('page_1.jpg')).toBeNull();
    expect(pageNumberFromFilename('')).toBeNull();
  });
});
