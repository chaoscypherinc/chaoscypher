// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import { parseSentRef, renderChunkWithHighlights } from '../chunkHighlight';

describe('parseSentRef', () => {
  it('parses a single sentence reference to 0-based index', () => {
    expect(parseSentRef('S1')).toEqual([0]);
    expect(parseSentRef('S2')).toEqual([1]);
    expect(parseSentRef('S5')).toEqual([4]);
  });

  it('parses comma-separated references', () => {
    expect(parseSentRef('S1,S3,S5')).toEqual([0, 2, 4]);
  });

  it('parses range references', () => {
    expect(parseSentRef('S1-S3')).toEqual([0, 1, 2]);
    expect(parseSentRef('S2-S5')).toEqual([1, 2, 3, 4]);
  });

  it('parses ranges without second S prefix', () => {
    expect(parseSentRef('S1-3')).toEqual([0, 1, 2]);
  });

  it('parses mixed comma and range references', () => {
    expect(parseSentRef('S1,S3-S4,S6')).toEqual([0, 2, 3, 5]);
  });

  it('returns empty array for invalid input', () => {
    expect(parseSentRef('')).toEqual([]);
    expect(parseSentRef('garbage')).toEqual([]);
  });

  it('is case-insensitive', () => {
    expect(parseSentRef('s1')).toEqual([0]);
  });
});

describe('renderChunkWithHighlights', () => {
  it('returns raw content when sentRef is undefined', () => {
    expect(renderChunkWithHighlights('hello world')).toBe('hello world');
  });

  it('returns raw content when chunkMetadata has no offsets', () => {
    expect(renderChunkWithHighlights('hello world', 'S1', {})).toBe('hello world');
  });

  it('returns raw content when sentRef has no valid references', () => {
    expect(
      renderChunkWithHighlights('hello world', 'garbage', {
        sentence_offsets: [{ start: 0, end: 5 }],
      }),
    ).toBe('hello world');
  });

  it('returns raw content when highlight ranges are out of bounds', () => {
    expect(
      renderChunkWithHighlights('hello world', 'S99', {
        sentence_offsets: [{ start: 0, end: 5 }],
      }),
    ).toBe('hello world');
  });
});
